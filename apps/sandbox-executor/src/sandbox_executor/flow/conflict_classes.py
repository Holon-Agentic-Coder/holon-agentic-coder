from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from sandbox_executor.flow.conflict_classes_lists import appended_list_union
from sandbox_executor.flow.conflict_classes_tokens import python_lines
from sandbox_executor.git import run_git

TIMESTAMP_KEYS = frozenset({"created_at", "updated_at", "created_ts", "updated_ts", "last_seen", "timestamp"})
KNOWLEDGE_JSON_ROOTS = ("holon-knowledge/kb/", "holon-knowledge/wisdom/")
_IMPORT_LINE = re.compile(r"^(?:import|from)\s")


class ConflictClass(Enum):
    LEDGER = "ledger"
    KB_TIMESTAMP_ONLY = "kb_timestamp_only"
    DELETE_VS_TOUCH = "delete_vs_touch"
    APPENDED_LIST = "appended_list"
    CODE = "code"


@dataclass(frozen=True)
class ConflictSides:
    path: str
    base: str | None
    ours: str | None
    theirs: str | None


def _is_ledger_path(path: str) -> bool:
    return "holon-knowledge/ledger" in path and path.endswith(".jsonl")


def unmerged_stages(worktree: str, path: str) -> set[int]:
    res = run_git(["ls-files", "-u", "-z", "--", path], cwd=worktree, check=False)
    out = res.stdout if res.returncode == 0 and isinstance(res.stdout, str) else ""
    return {int(entry.split("\t", 1)[0].split()[2]) for entry in out.split("\0") if "\t" in entry}


def _stage_text(worktree: str, stage: int, path: str) -> str | None:
    res = run_git(["show", f":{stage}:{path}"], cwd=worktree, check=False)
    return res.stdout if res.returncode == 0 and isinstance(res.stdout, str) else None


def read_sides(worktree: str, path: str) -> ConflictSides:
    stages = unmerged_stages(worktree, path)
    texts = {stage: _stage_text(worktree, stage, path) if stage in stages else None for stage in (1, 2, 3)}
    return ConflictSides(path, texts[1], texts[2], texts[3])


def _without_timestamps(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_timestamps(item) for key, item in value.items() if key not in TIMESTAMP_KEYS}
    if isinstance(value, list):
        return [_without_timestamps(item) for item in value]
    return value


def _is_timestamp_only(sides: ConflictSides) -> bool:
    if not (sides.path.startswith(KNOWLEDGE_JSON_ROOTS) and sides.path.endswith(".json")):
        return False
    if sides.ours is None or sides.theirs is None:
        return False
    try:
        ours, theirs = json.loads(sides.ours), json.loads(sides.theirs)
    except ValueError:
        return False
    return _without_timestamps(ours) == _without_timestamps(theirs)


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _normalised_lines(text: str, python: bool) -> list[str] | None:
    if not python:
        # why: indentation is semantic in Python, YAML and Makefiles, so a re-indent is never mechanical.
        return [_indent(line) + " ".join(line.split()) for line in text.splitlines() if line.strip()]
    # why: Python is compared token by token so whitespace inside a string literal stays significant.
    lines = python_lines(text)
    if lines is None:
        return None
    result: list[str] = []
    block: list[str] = []
    for line in [*lines, ""]:
        if _IMPORT_LINE.match(line):
            block.append(line)
            continue
        result.extend(sorted(block))
        block = []
        result.append(line)
    return result[:-1]


def _is_delete_vs_touch(sides: ConflictSides) -> bool:
    if sides.theirs is not None or sides.base is None or sides.ours is None:
        return False
    python = sides.path.endswith(".py")
    base = _normalised_lines(sides.base, python)
    return base is not None and base == _normalised_lines(sides.ours, python)


def _is_appended_list(sides: ConflictSides) -> bool:
    if not sides.path.endswith(".py") or None in (sides.base, sides.ours, sides.theirs):
        return False
    return appended_list_union(sides.base or "", sides.ours or "", sides.theirs or "") is not None


def classify_sides(sides: ConflictSides) -> ConflictClass:
    if _is_ledger_path(sides.path.replace("\\", "/")):
        return ConflictClass.LEDGER
    if _is_timestamp_only(sides):
        return ConflictClass.KB_TIMESTAMP_ONLY
    if _is_delete_vs_touch(sides):
        return ConflictClass.DELETE_VS_TOUCH
    if _is_appended_list(sides):
        return ConflictClass.APPENDED_LIST
    # why: a mechanical class needs positive evidence; anything doubtful is left to the code resolver.
    return ConflictClass.CODE


def classify_path(worktree: str, path: str) -> ConflictClass:
    try:
        return classify_sides(read_sides(worktree, path))
    except UnicodeDecodeError:
        return ConflictClass.CODE


def classify_merge_conflicts(worktree: str, paths: list[str]) -> list[tuple[str, ConflictClass]]:
    return [(path, classify_path(worktree, path)) for path in paths]
