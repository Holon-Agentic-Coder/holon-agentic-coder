#!/usr/bin/env python3
"""One-off, repeatable migration of ``plans/`` to the ``plans/{intent}/`` layout (intent ``I-1794976491-shortref``).

Every plan file directly in ``plans/`` moves to the path its branch had (``sandbox_executor.plan_paths``): the intent
comes from the file's ``plans.jsonl`` row (``intent_branch``), and the calibration report moves with it. A file with
no ledger row is matched to a supplied pull request head branch by its plan id (the legacy hex disambiguator is
ignored when comparing, because older plan files were named without it), and otherwise moves to ``plans/legacy/``.

The maintainer granted a one-off exemption from the append-only ledger rule for this migration only, limited to two
stored paths: ``plan_file`` in ``plans.jsonl`` and ``calibration_report_path`` in ``executions.jsonl``. Every other
byte of every row stays as it was; no row is added, removed or reordered. The rewrite is verified row by row before
anything on disk changes, and the run aborts on any ambiguity (two files for one target, an occupied target, a row
that would need any other change).

The script is a no-op on a migrated tree. It prints every file moved, every path rewritten, and every file whose new
path matches no pull request head branch in the supplied list.

Usage:
    uv run python scripts/migrate_plan_layout.py [--repo DIR] [--pr-heads FILE] [--dry-run]

``--pr-heads`` names a file with one pull request head branch name per line (for example the output of
``gh pr list --state all --limit 1000 --json headRefName -q '.[].headRefName'``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from sandbox_executor.naming import GENERATION_LEGACY_HEX, parse_stage_id
from sandbox_executor.plan_paths import (
    CALIBRATION_REPORT_SUFFIX,
    LEGACY_DIR,
    PLAN_FILE_SUFFIX,
    PLANS_DIR,
    bare_branch,
    calibration_report_for_branch,
    calibration_report_rel,
    is_plan_segment,
    legacy_calibration_report_rel,
    legacy_plan_file_rel,
    plan_branch_from_path,
    plan_branch_of,
    plan_file_for_branch,
    plan_file_rel,
)

PLANS_LEDGER = "holon-knowledge/ledger/plans.jsonl"
EXECUTIONS_LEDGER = "holon-knowledge/ledger/executions.jsonl"
# The only two fields the exemption allows to change, one per ledger.
LEDGER_PATH_FIELDS = {PLANS_LEDGER: "plan_file", EXECUTIONS_LEDGER: "calibration_report_path"}


class MigrationError(RuntimeError):
    """The migration cannot proceed without breaking its guarantees; nothing has been changed."""


@dataclass(frozen=True)
class Rewrite:
    """One stored path rewritten in one ledger row."""

    ledger: str
    line_no: int
    field: str
    old: str
    new: str


@dataclass
class MigrationPlan:
    """Everything the migration would do, computed and verified before any change is made."""

    moves: dict[str, str] = field(default_factory=dict)
    rewrites: list[Rewrite] = field(default_factory=list)
    ledger_text: dict[str, str] = field(default_factory=dict)
    unmatched: list[str] = field(default_factory=list)

    @property
    def is_noop(self) -> bool:
        return not self.moves and not self.rewrites


def _read_lines(repo: Path, rel: str) -> list[str]:
    path = repo / rel
    return path.read_text(encoding="utf-8").splitlines(keepends=True) if path.exists() else []


def _parse(line: str) -> dict | None:
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    return row if isinstance(row, dict) else None


def _flat_files(repo: Path) -> list[str]:
    """Markdown files directly in ``plans/`` (the only files the migration moves)."""
    plans = repo / PLANS_DIR
    if not plans.is_dir():
        return []
    names = sorted(p.name for p in plans.iterdir() if p.is_file() and p.name.endswith(PLAN_FILE_SUFFIX))
    return [f"{PLANS_DIR}/{name}" for name in names]


def _add_move(moves: dict[str, str], source: str, target: str) -> None:
    if moves.get(source, target) != target:
        raise MigrationError(f"{source} would move to both {moves[source]} and {target}")
    moves[source] = target


def _row_sources(row: dict) -> tuple[list[str], list[str]]:
    """Flat legacy locations of a plan row's plan file and calibration report (the row's own path first)."""
    plan_id, recorded = str(row["plan_id"]), str(row.get("plan_file") or "")
    plan_sources = [recorded, legacy_plan_file_rel(plan_id)] if recorded.count("/") == 1 else []
    plan_sources = plan_sources or [legacy_plan_file_rel(plan_id)]
    stems = [p.removesuffix(PLAN_FILE_SUFFIX) for p in plan_sources]
    report_sources = [legacy_calibration_report_rel(plan_id), *(f"{stem}{CALIBRATION_REPORT_SUFFIX}" for stem in stems)]
    return list(dict.fromkeys(plan_sources)), list(dict.fromkeys(report_sources))


def _row_moves(flat: set[str], plan_rows: Iterable[dict]) -> dict[str, str]:
    """Moves of every flat plan file and calibration report that a ``plans.jsonl`` row owns."""
    moves: dict[str, str] = {}
    for row in plan_rows:
        if not row.get("plan_id") or not row.get("intent_branch"):
            continue
        parent, plan_id = bare_branch(str(row["intent_branch"])), str(row["plan_id"])
        plan_sources, report_sources = _row_sources(row)
        for source in (s for s in plan_sources if s in flat):
            _add_move(moves, source, plan_file_rel(parent, plan_id))
        for source in (s for s in report_sources if s in flat):
            _add_move(moves, source, calibration_report_rel(parent, plan_id))
    return moves


def _same_plan(segment: str, stem: str) -> bool:
    """True when a branch segment and a file stem name the same plan, ignoring a legacy hex disambiguator."""
    if segment == stem:
        return True
    ours, theirs = parse_stage_id(segment), parse_stage_id(stem)
    if ours is None or theirs is None or ours.labels is None:
        return False
    same_name = (ours.kind, ours.epoch, ours.labels) == (theirs.kind, theirs.epoch, theirs.labels)
    return same_name and GENERATION_LEGACY_HEX in (ours.generation, theirs.generation)


def _matching_plan_branch(stem: str, pr_heads: Iterable[str]) -> str:
    """The one plan branch among the pull request heads whose plan segment names ``stem``; ``""`` if none or many."""
    found = set()
    for head in pr_heads:
        segments = bare_branch(head).split("/")
        for index, segment in enumerate(segments):
            if is_plan_segment(segment) and _same_plan(segment, stem):
                found.add("/".join(segments[: index + 1]))
    return next(iter(found)) if len(found) == 1 else ""


def _rowless_target(source: str, pr_heads: list[str]) -> str:
    name = source.removeprefix(f"{PLANS_DIR}/")
    is_report = name.endswith(CALIBRATION_REPORT_SUFFIX)
    stem = name.removesuffix(CALIBRATION_REPORT_SUFFIX if is_report else PLAN_FILE_SUFFIX)
    plan_branch = _matching_plan_branch(stem, pr_heads)
    if not plan_branch:
        return f"{LEGACY_DIR}/{name}"
    return calibration_report_for_branch(plan_branch) if is_report else plan_file_for_branch(plan_branch)


def _check_targets(repo: Path, moves: dict[str, str]) -> None:
    """Abort when two files would share a target or a target is already occupied."""
    seen: dict[str, str] = {}
    for source, target in moves.items():
        if target in seen:
            raise MigrationError(f"{seen[target]} and {source} would both move to {target}")
        seen[target] = source
        if (repo / target).exists():
            raise MigrationError(f"{source} cannot move to {target}: the target already exists")


def plan_moves(repo: Path, plan_rows: list[dict], pr_heads: list[str]) -> dict[str, str]:
    """Every move of a flat ``plans/`` file: ledger-backed files to their branch path, the rest by PR head or legacy."""
    flat = _flat_files(repo)
    moves = _row_moves(set(flat), plan_rows)
    for source in (s for s in flat if s not in moves):
        moves[source] = _rowless_target(source, pr_heads)
    _check_targets(repo, moves)
    return moves


def _rewrite_line(line: str, row: dict, path_field: str, new: str) -> str:
    """Replace only the stored path in a row's raw text, keeping every other byte, and verify the result."""
    old = row[path_field]
    candidates = [f'"{path_field}": {json.dumps(old)}', f'"{path_field}":{json.dumps(old)}']
    needle = next((c for c in candidates if line.count(c) == 1), None)
    if needle is None:
        raise MigrationError(f"cannot locate exactly one '{path_field}' value in the row: {line.strip()[:120]}")
    rewritten = line.replace(needle, needle.replace(json.dumps(old), json.dumps(new)))
    if _parse(rewritten) != {**row, path_field: new}:
        raise MigrationError(f"rewriting '{path_field}' would change more than that field: {line.strip()[:120]}")
    return rewritten


def plan_ledger_rewrites(repo: Path, ledger: str, moves: dict[str, str]) -> tuple[str, list[Rewrite]]:
    """The ledger text with its one exempted path field rewritten to the moved paths, and the rewrites made."""
    path_field = LEDGER_PATH_FIELDS[ledger]
    lines, rewrites = _read_lines(repo, ledger), []
    out: list[str] = []
    for line_no, line in enumerate(lines, start=1):
        row = _parse(line)
        old = row.get(path_field) if row else None
        if not isinstance(old, str) or old not in moves:
            out.append(line)
            continue
        out.append(_rewrite_line(line, row, path_field, moves[old]))
        rewrites.append(Rewrite(ledger, line_no, path_field, old, moves[old]))
    verify_ledger_rewrite(lines, out, path_field)
    return "".join(out), rewrites


def _without(row: dict, key: str) -> dict:
    return {k: v for k, v in row.items() if k != key}


def verify_ledger_rewrite(before: list[str], after: list[str], path_field: str) -> None:
    """Every row is byte-identical, or differs only in ``path_field``; no row is added, removed or reordered."""
    if len(before) != len(after):
        raise MigrationError(f"row count would change from {len(before)} to {len(after)}")
    for line_no, (old_line, new_line) in enumerate(zip(before, after, strict=True), start=1):
        if old_line == new_line:
            continue
        old_row, new_row = _parse(old_line), _parse(new_line)
        if old_row is None or new_row is None or old_row.keys() != new_row.keys():
            raise MigrationError(f"line {line_no} would change shape")
        if _without(old_row, path_field) != _without(new_row, path_field):
            raise MigrationError(f"line {line_no} would change a field other than '{path_field}'")


def _matches_a_pr_head(new_path: str, pr_heads: list[str]) -> bool:
    plan_branch = plan_branch_from_path(new_path)
    if not plan_branch or new_path.startswith(f"{LEGACY_DIR}/"):
        return False
    return any(plan_branch_of(head) == plan_branch for head in pr_heads)


def build_plan(repo: Path, pr_heads: list[str]) -> MigrationPlan:
    """Compute and verify the whole migration without touching the tree."""
    plan_rows = [row for row in map(_parse, _read_lines(repo, PLANS_LEDGER)) if row]
    plan = MigrationPlan(moves=plan_moves(repo, plan_rows, pr_heads))
    for ledger in LEDGER_PATH_FIELDS:
        text, rewrites = plan_ledger_rewrites(repo, ledger, plan.moves)
        if rewrites:
            plan.ledger_text[ledger] = text
            plan.rewrites.extend(rewrites)
    plan.unmatched = sorted(t for t in plan.moves.values() if not _matches_a_pr_head(t, pr_heads))
    return plan


def missing_ledger_paths(repo: Path) -> list[str]:
    """Stored plan and calibration paths (in both ledgers) that point at no file."""
    missing = []
    for ledger, path_field in LEDGER_PATH_FIELDS.items():
        for line_no, row in enumerate(map(_parse, _read_lines(repo, ledger)), start=1):
            value = row.get(path_field) if row else None
            if isinstance(value, str) and value and not (repo / value).exists():
                missing.append(f"{ledger}:{line_no} {path_field}: {value}")
    return missing


def apply_plan(repo: Path, plan: MigrationPlan) -> None:
    """Move the files, then write the verified ledgers."""
    for source, target in plan.moves.items():
        (repo / target).parent.mkdir(parents=True, exist_ok=True)
        os.rename(repo / source, repo / target)
    for ledger, text in plan.ledger_text.items():
        (repo / ledger).write_text(text, encoding="utf-8")


def format_report(plan: MigrationPlan, dry_run: bool) -> str:
    """The full report: every move, every rewritten path and every new path matching no pull request head."""
    if plan.is_noop:
        return "Nothing to migrate: plans/ and the ledgers already use the plans/{intent}/ layout."
    verb = "Would move" if dry_run else "Moved"
    lines = [f"{verb} {len(plan.moves)} file(s):"]
    lines += [f"  {source} -> {target}" for source, target in sorted(plan.moves.items())]
    lines.append(f"{'Would rewrite' if dry_run else 'Rewrote'} {len(plan.rewrites)} ledger path(s):")
    lines += [f"  {r.ledger}:{r.line_no} {r.field}: {r.old} -> {r.new}" for r in plan.rewrites]
    lines.append(f"{len(plan.unmatched)} new path(s) match no supplied pull request head branch:")
    lines += [f"  {path}" for path in plan.unmatched]
    return "\n".join(lines)


def _read_pr_heads(path: str | None) -> list[str]:
    if not path:
        return []
    return [line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=".", help="repository root (default: the current directory)")
    parser.add_argument("--pr-heads", help="file listing pull request head branch names, one per line")
    parser.add_argument("--dry-run", action="store_true", help="report what would change without changing anything")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    try:
        plan = build_plan(repo, _read_pr_heads(args.pr_heads))
    except MigrationError as exc:
        print(f"Migration aborted, nothing changed: {exc}", file=sys.stderr)
        return 1
    if not args.dry_run:
        apply_plan(repo, plan)
    print(format_report(plan, args.dry_run))
    missing = [] if args.dry_run else missing_ledger_paths(repo)
    for entry in missing:
        print(f"Stored path points at no file: {entry}", file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
