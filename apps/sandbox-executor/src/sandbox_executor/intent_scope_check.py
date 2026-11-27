from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from sandbox_executor.intent_payload_rules import check_intent_payload
from sandbox_executor.intent_scope_graph import (
    build_module_index,
    co_changed,
    imported_modules,
    imported_sources,
    importing_tests,
    is_test_path,
    module_name,
    python_files,
    reexporting_init,
)

_NEW_PREFIX_RE = re.compile(r"^new:\s*", re.IGNORECASE)
_NEW_SUFFIX_RE = re.compile(r"\s+\(new\)$", re.IGNORECASE)


@dataclass(frozen=True)
class ScopeEntry:
    path: str
    marked_new: bool

    @classmethod
    def parse(cls, raw: str) -> ScopeEntry:
        text = raw.strip()
        stripped = _NEW_SUFFIX_RE.sub("", _NEW_PREFIX_RE.sub("", text)).strip()
        return cls(path=stripped.removeprefix("./"), marked_new=stripped != text)


@dataclass(frozen=True)
class DraftIntent:
    scope: list[ScopeEntry]
    acceptance_criteria: list[str]
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Proposal:
    path: str
    reason: str
    source: str


@dataclass(frozen=True)
class IntentScopeReport:
    missing: list[str]
    proposed: list[Proposal]
    warnings: list[str]
    violations: list[str] = field(default_factory=list)

    @property
    def has_proposals(self) -> bool:
        return bool(self.proposed)


def load_intent(path: str) -> DraftIntent:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read intent file {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("scope"), list):
        raise ValueError(f"intent file {path} has no 'scope' list")
    criteria = payload.get("acceptance_criteria") or []
    if not isinstance(criteria, list):
        raise ValueError(f"intent file {path} has an 'acceptance_criteria' value that is not a list")
    scope = [ScopeEntry.parse(str(entry)) for entry in payload["scope"] if str(entry).strip()]
    return DraftIntent(scope=scope, acceptance_criteria=[str(item) for item in criteria], payload=payload)


def _exists(worktree: str, path: str) -> bool:
    return not PurePosixPath(path).is_absolute() and Path(worktree, path).exists()


def _test_proposals(worktree: str, test_path: str, index: dict[str, str]) -> list[Proposal]:
    sources = imported_sources(index, imported_modules(worktree, test_path))
    return [Proposal(source, "listed test imports this module", test_path) for source in sources]


def _source_proposals(worktree: str, path: str, test_imports: dict[str, set[str]]) -> list[Proposal]:
    module = module_name(path)
    if module is None:
        return []
    proposals = [Proposal(test, "test imports listed module", path) for test in importing_tests(test_imports, module)]
    init = reexporting_init(worktree, path)
    if init:
        proposals.append(Proposal(init, "package __init__.py re-exports listed module", path))
    return proposals


def _import_proposals(worktree: str, existing: list[str]) -> list[Proposal]:
    listed_python = [path for path in existing if path.endswith(".py")]
    if not listed_python:
        return []
    files = python_files(worktree)
    index = build_module_index(files)
    test_imports = {path: imported_modules(worktree, path) for path in files if is_test_path(path)}
    proposals: list[Proposal] = []
    for path in listed_python:
        if is_test_path(path):
            proposals += _test_proposals(worktree, path, index)
        else:
            proposals += _source_proposals(worktree, path, test_imports)
    return proposals


def _co_change_proposals(worktree: str, listed: list[str]) -> list[Proposal]:
    partners = co_changed(worktree, listed)
    return [Proposal(path, f"co-changed {count}x with {source}", source) for path, (count, source) in partners.items()]


def _merge(proposals: Iterable[Proposal], in_scope: set[str]) -> list[Proposal]:
    unique: dict[str, Proposal] = {}
    for proposal in proposals:
        if proposal.path not in in_scope:
            unique.setdefault(proposal.path, proposal)
    return [unique[path] for path in sorted(unique)]


def _mentions(text: str, path: str) -> bool:
    pure = PurePosixPath(path)
    names = {path, pure.name, pure.stem, module_name(path) or ""} - {""}
    return any(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text) for name in names)


def _criteria_warnings(intent: DraftIntent) -> list[str]:
    text = "\n".join(intent.acceptance_criteria)
    return [
        f"{entry.path}: no acceptance criterion names it by path, file name or module name"
        for entry in intent.scope
        if not _mentions(text, entry.path)
    ]


def check_intent_scope(intent: DraftIntent, worktree: str) -> IntentScopeReport:
    listed = [entry.path for entry in intent.scope]
    existing = [path for path in listed if _exists(worktree, path)]
    missing = sorted({entry.path for entry in intent.scope if not entry.marked_new and entry.path not in existing})
    candidates = [*_import_proposals(worktree, existing), *_co_change_proposals(worktree, listed)]
    payload = intent.payload or {"scope": listed, "acceptance_criteria": intent.acceptance_criteria}
    findings = check_intent_payload(payload, worktree)
    return IntentScopeReport(
        missing=missing,
        proposed=_merge(candidates, set(listed)),
        warnings=[*_criteria_warnings(intent), *findings.warnings],
        violations=findings.violations,
    )


def _section(title: str, lines: list[str]) -> list[str]:
    return [f"{title}:", *(f"  - {line}" for line in lines or ["none"])]


def render_text(report: IntentScopeReport) -> str:
    proposed = [f"{item.path} ({item.reason}; from {item.source})" for item in report.proposed]
    lines = [
        *(_section("Violations", report.violations) if report.violations else []),
        *_section("Missing", report.missing),
        *_section("Proposed companions", proposed),
        *_section("Warnings", report.warnings),
        f"{len(report.proposed)} proposal(s).",
    ]
    return "\n".join(lines)


def to_json(report: IntentScopeReport) -> dict[str, Any]:
    data: dict[str, Any] = {
        "missing": list(report.missing),
        "proposed": [{"path": item.path, "reason": item.reason, "source": item.source} for item in report.proposed],
        "warnings": list(report.warnings),
    }
    if report.violations:
        data["violations"] = list(report.violations)
    return data
