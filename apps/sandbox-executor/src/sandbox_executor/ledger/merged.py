from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from sandbox_executor.git import run_git
from sandbox_executor.ledger.fragments import LEDGER_DIR_REL, iter_fragment_files, ledger_name_of, pending_rel_dir
from sandbox_executor.ledger.order import merge_by_instant

Row = dict[str, Any]
BadLineHandler = Callable[[int, Exception], None]
GitRunner = Callable[..., Any]

_IDENTITY_KEYS: dict[str, tuple[str, ...]] = {
    "plans.jsonl": ("intent_branch", "plan_id", "ledger_revision"),
    "intents.jsonl": ("branch", "ledger_revision", "entry_type"),
    "executions.jsonl": ("plan_branch", "execution_id", "entry_type", "ledger_revision", "created_at"),
}
_ANCHOR_KEYS: dict[str, tuple[str, ...]] = {
    "plans.jsonl": ("plan_id",),
    "intents.jsonl": ("branch", "intent_id"),
    "executions.jsonl": ("execution_id",),
}


def parse_jsonl_rows(text: str, on_bad_line: BadLineHandler | None = None) -> list[Row]:
    rows: list[Row] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            if on_bad_line is not None:
                on_bad_line(line_no, exc)
            continue
        if isinstance(row, dict):
            rows.append(row)
        elif on_bad_line is not None:
            on_bad_line(line_no, TypeError(f"not a JSON object: {type(row).__name__}"))
    return rows


def row_identity(ledger_name: str, row: Row) -> str:
    if ledger_name == "executions.jsonl" and row.get("action_id"):
        return json.dumps(["action", row["action_id"]], default=str)
    if any(row.get(key) for key in _ANCHOR_KEYS.get(ledger_name, ())):
        keyed = {key: row.get(key) for key in _IDENTITY_KEYS[ledger_name]}
        if ledger_name == "intents.jsonl":
            keyed["branch"] = row.get("branch") or row.get("intent_id")
        return json.dumps(["keyed", keyed], sort_keys=True, default=str)
    return json.dumps(["row", row], sort_keys=True, default=str)


def merge_rows(ledger_name: str, canonical_rows: list[Row], fragment_row_lists: Sequence[list[Row]]) -> list[Row]:
    seen = {row_identity(ledger_name, row) for row in canonical_rows}
    fresh: list[Row] = []
    for rows in fragment_row_lists:
        for row in rows:
            identity = row_identity(ledger_name, row)
            if identity not in seen:
                seen.add(identity)
                fresh.append(row)
    return merge_by_instant(canonical_rows, fresh) if fresh else list(canonical_rows)


def merged_jsonl_text(rows: list[Row]) -> str:
    return "".join(json.dumps(row) + "\n" for row in rows)


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _fragment_texts(canonical_path: str) -> list[str]:
    return [text for path in iter_fragment_files(canonical_path) if (text := _read_text(path)) is not None]


def read_merged_rows(canonical_path: str, on_bad_line: BadLineHandler | None = None) -> list[Row]:
    canonical_rows = parse_jsonl_rows(_read_text(canonical_path) or "", on_bad_line)
    fragments = [parse_jsonl_rows(text, on_bad_line) for text in _fragment_texts(canonical_path)]
    if not fragments:
        return canonical_rows
    return merge_rows(ledger_name_of(canonical_path) or "", canonical_rows, fragments)


def fragment_sync_plan(canonical_path: str) -> list[tuple[str, Callable[[Row], bool]]]:
    ledger_name = ledger_name_of(canonical_path) or ""
    seen = {row_identity(ledger_name, row) for row in parse_jsonl_rows(_read_text(canonical_path) or "")}
    plan: list[tuple[str, Callable[[Row], bool]]] = []
    for path in iter_fragment_files(canonical_path):
        known = frozenset(seen)
        plan.append((path, lambda row, known=known: row_identity(ledger_name, row) in known))
        seen.update(row_identity(ledger_name, row) for row in parse_jsonl_rows(_read_text(path) or ""))
    return plan


def read_merged_text(canonical_path: str) -> str | None:
    canonical_text = _read_text(canonical_path)
    fragments = _fragment_texts(canonical_path)
    if not fragments:
        return canonical_text
    return _merged_text(ledger_name_of(canonical_path) or "", canonical_text, fragments)


def _merged_text(ledger_name: str, canonical_text: str | None, fragment_texts: list[str]) -> str:
    fragments = [parse_jsonl_rows(text) for text in fragment_texts]
    return merged_jsonl_text(merge_rows(ledger_name, parse_jsonl_rows(canonical_text or ""), fragments))


def _git_text(git: GitRunner, repo_dir: str, args: list[str]) -> str | None:
    res = git(args, cwd=repo_dir, check=False)
    if res.returncode != 0 or not isinstance(res.stdout, str):
        return None
    return res.stdout


def _ref_fragment_texts(repo_dir: str, ref: str, ledger_name: str, git: GitRunner) -> list[str]:
    listing = _git_text(git, repo_dir, ["ls-tree", "-r", "--name-only", ref, "--", f"{pending_rel_dir(ledger_name)}/"])
    paths = sorted(path for path in (listing or "").splitlines() if path.endswith(".jsonl"))
    return [text for path in paths if (text := _git_text(git, repo_dir, ["show", f"{ref}:{path}"])) is not None]


def with_ref_fragments(
    canonical_text: str | None, repo_dir: str, ref: str, ledger_name: str, *, git: GitRunner = run_git
) -> str | None:
    fragments = _ref_fragment_texts(repo_dir, ref, ledger_name, git)
    if not fragments:
        return canonical_text
    return _merged_text(ledger_name, canonical_text, fragments)


def read_merged_rows_at_ref(repo_dir: str, ref: str, ledger_name: str, *, git: GitRunner = run_git) -> list[Row]:
    canonical_text = _git_text(git, repo_dir, ["show", f"{ref}:{LEDGER_DIR_REL}/{ledger_name}"])
    canonical_rows = parse_jsonl_rows(canonical_text or "")
    fragments = [parse_jsonl_rows(text) for text in _ref_fragment_texts(repo_dir, ref, ledger_name, git)]
    if not fragments:
        return canonical_rows
    return merge_rows(ledger_name, canonical_rows, fragments)
