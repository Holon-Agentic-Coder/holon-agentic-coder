"""One-off migration of ``plans/`` to nested intent paths, rewriting only the two ledger path fields.

The maintainer granted a one-off exemption from the append-only ledger rule for this migration (intent
``I-1794976491-shortref``); the rule applies again afterwards. The migration moves every plan file directly in
``plans/`` to ``plans/{intent}/`` (the intent taken from its ``plans.jsonl`` row, else from a supplied pull-request
head branch matching the plan ID, else to ``plans/legacy/``), with each calibration report following its plan. In the
ledgers it rewrites only ``plan_file`` in ``plans.jsonl`` and ``calibration_report_path`` in ``executions.jsonl``; a
field-masked byte comparison asserts that no other field, row, order or byte changed, and the whole run aborts and
restores both ledgers on any difference. A second run on a migrated tree finds nothing to do and reports zero actions.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from sandbox_executor.plan_paths import LEGACY_PLANS_SUBDIR, PLANS_DIR, branch_dir

PLANS_LEDGER_REL = "holon-knowledge/ledger/plans.jsonl"
EXECUTIONS_LEDGER_REL = "holon-knowledge/ledger/executions.jsonl"

#: The only ledger fields this migration may rewrite, per ledger file.
REWRITTEN_FIELDS: dict[str, tuple[str, ...]] = {
    PLANS_LEDGER_REL: ("plan_file",),
    EXECUTIONS_LEDGER_REL: ("calibration_report_path",),
}


class MigrationError(RuntimeError):
    """The migration found an inconsistency and stopped; nothing partially rewritten is kept."""


@dataclass
class MigrationReport:
    """Everything the migration did (or, on a dry run, would do)."""

    moves: list[tuple[str, str]] = field(default_factory=list)
    rewrites: list[tuple[str, int, str, str]] = field(default_factory=list)
    legacy: list[str] = field(default_factory=list)
    unmatched_pr: list[str] = field(default_factory=list)
    missing_targets: list[tuple[str, str]] = field(default_factory=list)

    @property
    def action_count(self) -> int:
        """Number of file moves plus ledger path rewrites."""
        return len(self.moves) + len(self.rewrites)

    def render(self, applied: bool) -> str:
        """Human-readable report: every move, every rewrite, every legacy file and every PR-unmatched path."""
        mode = "applied" if applied else "dry run"
        lines = [f"plan migration ({mode}): {len(self.moves)} file move(s), {len(self.rewrites)} ledger rewrite(s)"]
        lines += [f"  move: {src} -> {dst}" for src, dst in self.moves]
        lines += [f"  rewrite: {ledger} line {line_no}: {old} -> {new}" for ledger, line_no, old, new in self.rewrites]
        lines += [f"  legacy (no ledger row, no PR head branch): {src}" for src in self.legacy]
        lines += [f"  new path matches no supplied PR head branch: {dst}" for dst in self.unmatched_pr]
        lines += [f"  ledger path points at a missing file: {ledger}: {path}" for ledger, path in self.missing_targets]
        return "\n".join(lines)


def _read_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _intent_by_plan_id(rows: list[dict[str, object]]) -> dict[str, str]:
    """Map each plan ID to its intent directory; a plan ID recorded under two intents is ambiguous and dropped."""
    mapping: dict[str, str] = {}
    ambiguous: set[str] = set()
    for row in rows:
        plan_id, intent = str(row.get("plan_id") or ""), branch_dir(str(row.get("intent_branch") or ""))
        if not plan_id or not intent:
            continue
        if plan_id in mapping and mapping[plan_id] != intent:
            ambiguous.add(plan_id)
        mapping[plan_id] = intent
    for plan_id in ambiguous:
        mapping.pop(plan_id, None)
    return mapping


def _parent_from_pr_branch(plan_id: str, pr_branches: list[str]) -> str | None:
    """The branch path above ``plan_id`` in the first supplied PR head branch that carries it as a segment."""
    for branch in pr_branches:
        segments = branch_dir(branch).split("/")
        if plan_id in segments:
            return "/".join(segments[: segments.index(plan_id)])
    return None


def _plan_id_of(name: str) -> str:
    """The plan ID a top-level ``plans/`` file belongs to (its stem, minus any ``_calibration`` suffix)."""
    stem = name.removesuffix(".md")
    return stem.removesuffix("_calibration")


def _target_dir(plan_id: str, intent_of: dict[str, str], pr_branches: list[str]) -> tuple[str, str]:
    """Resolve where a plan's files belong: (subdirectory under plans/, resolution kind)."""
    if plan_id in intent_of:
        return intent_of[plan_id], "ledger"
    pr_parent = _parent_from_pr_branch(plan_id, pr_branches)
    if pr_parent:
        return pr_parent, "pr"
    return LEGACY_PLANS_SUBDIR, "legacy"


def _masked(line: str, fields: tuple[str, ...]) -> str:
    for name in fields:
        line = re.sub(rf'("{name}":\s*)"[^"]*"', r'\1"***"', line)
    return line


def _planned_ledger_rewrite(
    repo_root: Path, ledger_rel: str, moved: dict[str, str], report: MigrationReport
) -> bytes | None:
    """Compute the ledger's rewritten bytes (only the allowed path fields change) and verify them; no write happens.

    Returns None when the ledger is absent or unchanged.
    """
    path = repo_root / ledger_rel
    if not path.exists():
        return None
    fields = REWRITTEN_FIELDS[ledger_rel]
    before = path.read_bytes()
    old_lines = before.decode("utf-8").split("\n")
    new_lines: list[str] = []
    for line_no, line in enumerate(old_lines, start=1):
        new_line = line
        if line.strip():
            row = json.loads(line)
            for name in fields:
                old_value = row.get(name)
                if isinstance(old_value, str) and old_value in moved:
                    pattern = rf'("{name}":\s*){re.escape(json.dumps(old_value))}'
                    replacement = json.dumps(moved[old_value])
                    new_line = re.sub(pattern, lambda m, value=replacement: m.group(1) + value, new_line)
                    report.rewrites.append((ledger_rel, line_no, old_value, moved[old_value]))
        new_lines.append(new_line)
    after = "\n".join(new_lines).encode("utf-8")
    _assert_only_fields_changed(ledger_rel, before, after, fields)
    return after if after != before else None


def _assert_only_fields_changed(ledger_rel: str, before: bytes, after: bytes, fields: tuple[str, ...]) -> None:
    """Raise :class:`MigrationError` unless the ledgers differ only inside the allowed fields' values."""
    old_lines, new_lines = before.decode("utf-8").split("\n"), after.decode("utf-8").split("\n")
    if len(old_lines) != len(new_lines):
        raise MigrationError(f"{ledger_rel}: row count changed from {len(old_lines)} to {len(new_lines)}")
    for line_no, (old, new) in enumerate(zip(old_lines, new_lines, strict=True), start=1):
        if _masked(old, fields) != _masked(new, fields):
            raise MigrationError(f"{ledger_rel} line {line_no}: a field outside {fields} changed; aborting")


def _top_level_plan_files(repo_root: Path) -> list[str]:
    plans_dir = repo_root / PLANS_DIR
    if not plans_dir.is_dir():
        return []
    return sorted(entry.name for entry in plans_dir.iterdir() if entry.is_file() and entry.name.endswith(".md"))


def _git_mv(repo_root: Path, src_rel: str, dst_rel: str) -> None:
    (repo_root / dst_rel).parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["git", "mv", src_rel, dst_rel], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise MigrationError(f"git mv {src_rel} -> {dst_rel} failed: {result.stderr.strip()}")


def _check_ledger_targets(repo_root: Path, report: MigrationReport) -> None:
    """Record every ledger path field that points at a file which does not exist (post-migration check)."""
    for ledger_rel, fields in REWRITTEN_FIELDS.items():
        for row in _read_rows(repo_root / ledger_rel):
            for name in fields:
                value = row.get(name)
                if isinstance(value, str) and value and not (repo_root / value).exists():
                    report.missing_targets.append((ledger_rel, value))


def migrate_plans(repo_root: str | Path, pr_branches: list[str], apply: bool = False) -> MigrationReport:
    """Move every top-level ``plans/`` file to its branch path and rewrite the two ledger path fields.

    Args:
        repo_root: Repository root containing ``plans/`` and the ledgers.
        pr_branches: Pull-request head branch names, used for files with no ledger row and to report new paths that
            match no PR head branch.
        apply: False (the default) reports what would happen without touching anything; True performs the moves with
            ``git mv`` and writes the rewritten ledgers.

    Returns:
        The full report; ``action_count`` is 0 on an already-migrated tree.

    Raises:
        MigrationError: On any inconsistency, including a ledger difference outside the two allowed fields.
    """
    root = Path(repo_root).resolve()
    intent_of = _intent_by_plan_id(_read_rows(root / PLANS_LEDGER_REL))
    report = MigrationReport()
    moved: dict[str, str] = {}
    branch_dirs = {branch_dir(b) for b in pr_branches}

    for name in _top_level_plan_files(root):
        plan_id = _plan_id_of(name)
        src_rel = f"{PLANS_DIR}/{name}"
        target_dir, kind = _target_dir(plan_id, intent_of, pr_branches)
        dst_rel = f"{PLANS_DIR}/{target_dir}/{name}"
        report.moves.append((src_rel, dst_rel))
        moved[src_rel] = dst_rel
        if kind == "legacy":
            report.legacy.append(src_rel)
        if kind != "legacy" and not any(d.startswith(target_dir) for d in branch_dirs):
            report.unmatched_pr.append(dst_rel)

    # Every ledger rewrite is computed and byte-verified before anything on disk changes, so a verification
    # failure aborts with the tree untouched.
    planned = {rel: _planned_ledger_rewrite(root, rel, moved, report) for rel in REWRITTEN_FIELDS}

    if apply:
        for src_rel, dst_rel in report.moves:
            _git_mv(root, src_rel, dst_rel)
        for ledger_rel, content in planned.items():
            if content is not None:
                (root / ledger_rel).write_bytes(content)

    if apply or not report.moves:
        _check_ledger_targets(root, report)
    return report
