from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from sandbox_executor.harness_reference_patterns import find_references

# why: a local copy of the new-file marker rule keeps Stage 1 from importing the git-running scope graph.
_NEW_PREFIX_RE = re.compile(r"^new:\s*", re.IGNORECASE)
_NEW_SUFFIX_RE = re.compile(r"\s+\(new\)$", re.IGNORECASE)
_GLOB_CHARACTERS = frozenset("*?[]{}")
_TEXT_FIELDS = ("goal", "description", "rationale")
_LIST_FIELDS = ("scope", "acceptance_criteria")
_SCOPE_FIX = (
    "list each file on its own; a directory or glob widens the touch set "
    "and blocks every other flow at Stage 3 admission"
)


@dataclass(frozen=True)
class PayloadFindings:
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def refused(self) -> bool:
        return bool(self.violations)


def scope_path(raw: str) -> str:
    stripped = _NEW_SUFFIX_RE.sub("", _NEW_PREFIX_RE.sub("", raw.strip())).strip()
    return stripped.removeprefix("./")


def _entry_name(path: str, number: int) -> str:
    return f"scope entry {number}" if find_references(path) else f"scope entry '{path}'"


def _shape_problem(path: str, worktree: str | None) -> str | None:
    if path.endswith("/"):
        return "ends in a slash"
    if _GLOB_CHARACTERS & set(path):
        return "contains a glob character"
    if worktree is not None and not PurePosixPath(path).is_absolute() and Path(worktree, path).is_dir():
        return "is an existing directory"
    return None


def scope_shape_violations(entries: list[str], worktree: str | None = None) -> list[str]:
    violations: list[str] = []
    for number, raw in enumerate(entries, start=1):
        path = scope_path(raw)
        problem = _shape_problem(path, worktree)
        if problem:
            violations.append(f"{_entry_name(path, number)} {problem}: {_SCOPE_FIX}")
    return violations


def _hub_reason(path: str) -> str | None:
    pure = PurePosixPath(path)
    if path.endswith("flow/lazy_exports.py"):
        return "re-exports the flow package"
    if pure.name == "conftest.py":
        return "shares fixtures with every test below it"
    if pure.suffix == ".md" and pure.parent.name == "docs":
        return "is a shared documentation page"
    if path.endswith("scripts/pre_commit_check.py"):
        return "is the shared pre-commit gate"
    return None


def hub_file_warnings(entries: list[str]) -> list[str]:
    warnings: list[str] = []
    for raw in entries:
        path = scope_path(raw)
        reason = _hub_reason(path)
        if reason and not find_references(path):
            warnings.append(f"{path}: hub file that {reason}; expect overlap with other flows at Stage 3 admission")
    return warnings


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def intent_texts(payload: dict[str, Any]) -> Iterator[tuple[str, str]]:
    for name in _TEXT_FIELDS:
        value = payload.get(name)
        if isinstance(value, str):
            yield name, value
    for name in _LIST_FIELDS:
        for item in _as_list(payload.get(name)):
            yield name, item


def harness_reference_violations(payload: dict[str, Any]) -> list[str]:
    labels_by_field: dict[str, list[str]] = {}
    for name, text in intent_texts(payload):
        for label in find_references(text):
            labels = labels_by_field.setdefault(name, [])
            if label not in labels:
                labels.append(label)
    return [
        f"intent field '{name}' names a control-plane {', '.join(labels)}; "
        "the intent text is copied into the append-only intents ledger, so remove it"
        for name, labels in labels_by_field.items()
    ]


def check_intent_payload(payload: dict[str, Any], worktree: str | None = None) -> PayloadFindings:
    entries = [entry for entry in _as_list(payload.get("scope")) if entry.strip()]
    return PayloadFindings(
        violations=[*scope_shape_violations(entries, worktree), *harness_reference_violations(payload)],
        warnings=hub_file_warnings(entries),
    )
