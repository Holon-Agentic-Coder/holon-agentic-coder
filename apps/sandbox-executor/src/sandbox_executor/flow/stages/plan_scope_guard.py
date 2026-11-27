from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from sandbox_executor.git import is_metadata_path
from sandbox_executor.intent_payload_rules import scope_path
from sandbox_executor.touch_set import (
    TouchSet,
    TouchSetEntry,
    entry_matches,
    normalise_touch_path,
    parse_touch_set_section,
)

PLAN_SCOPE_VIOLATION = "plan_touch_set_out_of_scope"
_PROVENANCE_PREFIXES = ("plans/", "executions/", "holon-knowledge/", "intents/")
_EXPANSION_KEYS = ("scope_expansion", "scope_expansions")
_EXPANSION_HEADING_RE = re.compile(r"^##\s+scope\s+expansions?\s*$", re.IGNORECASE)
_NEXT_SECTION_RE = re.compile(r"^#{1,2}\s")
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_ITEM_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+(?P<body>.*)$")
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_PLACEHOLDER_WORDS = frozenset({"none", "n/a", "na", "nil", "no", "not"})
_PLACEHOLDER_WRAPPERS = "()[]{}<>\"'`*_~\u2018\u2019\u201c\u201d"
_PLACEHOLDER_PUNCTUATION = ".,;:"


def _is_provenance_path(path: str) -> bool:
    clean = normalise_touch_path(path)
    if not clean:
        return False
    if is_metadata_path(clean):
        return True
    return any(clean == prefix.rstrip("/") or clean.startswith(prefix) for prefix in _PROVENANCE_PREFIXES)


def _is_placeholder(text: str) -> bool:
    stripped_words = text.strip(_PLACEHOLDER_WRAPPERS).split()
    if not stripped_words:
        return False
    first_word = stripped_words[0].strip(_PLACEHOLDER_WRAPPERS + _PLACEHOLDER_PUNCTUATION).lower()
    return first_word in _PLACEHOLDER_WORDS


def _iter_item_paths(item: Any) -> Iterable[str]:
    if isinstance(item, str):
        yield item
        return
    if not isinstance(item, Mapping):
        return
    if isinstance(item.get("path"), str):
        yield item["path"]
    paths = item.get("paths")
    if isinstance(paths, list):
        for candidate in paths:
            if isinstance(candidate, str):
                yield candidate


def _normalised_entries(raw_items: Iterable[Any]) -> tuple[TouchSetEntry, ...]:
    entries: list[TouchSetEntry] = []
    seen: set[str] = set()
    for item in raw_items:
        for raw in _iter_item_paths(item):
            if _is_placeholder(raw):
                continue
            clean = normalise_touch_path(scope_path(raw))
            if not clean or clean in seen:
                continue
            seen.add(clean)
            entries.append(TouchSetEntry(clean))
    return tuple(entries)


def _coerce_raw_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return list(value)
    return [value]


def _fenced_line_indices(lines: list[str]) -> set[int]:
    fenced: set[int] = set()
    fence: str | None = None
    for index, line in enumerate(lines):
        match = _FENCE_RE.match(line)
        if fence is None and match:
            fence = match.group(1)
        elif fence is not None and match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
            fenced.add(index)
            fence = None
            continue
        if fence is not None:
            fenced.add(index)
    return fenced


def _expansion_path_from_item(body: str) -> str:
    if _is_placeholder(body):
        return ""
    backticked = _BACKTICK_RE.search(body)
    if backticked:
        return backticked.group(1)
    cleaned = scope_path(body)
    return cleaned.partition(" ")[0]


def _markdown_scope_expansions(plan_content: str) -> list[str]:
    lines = plan_content.splitlines()
    fenced = _fenced_line_indices(lines)
    start = next(
        (i for i, line in enumerate(lines) if i not in fenced and _EXPANSION_HEADING_RE.match(line.strip())),
        None,
    )
    if start is None:
        return []
    raw_paths: list[str] = []
    for index, line in enumerate(lines[start + 1 :], start + 1):
        if index in fenced:
            continue
        if _NEXT_SECTION_RE.match(line):
            break
        match = _ITEM_RE.match(line)
        if not match:
            continue
        candidate = _expansion_path_from_item(match.group("body").strip())
        if candidate:
            raw_paths.append(candidate)
    return raw_paths


def _scope_expansion_entries(
    intent_data: Mapping[str, Any],
    plan_content: str,
    plan_row: Mapping[str, Any],
) -> tuple[TouchSetEntry, ...]:
    raw_items: list[Any] = []
    for source in (intent_data, plan_row):
        for key in _EXPANSION_KEYS:
            raw_items.extend(_coerce_raw_items(source.get(key)))
    raw_items.extend(_markdown_scope_expansions(plan_content))
    return _normalised_entries(raw_items)


def _plan_touch_set_paths(plan_content: str, plan_row: Mapping[str, Any]) -> list[str]:
    candidates: list[TouchSetEntry] = []
    parsed = parse_touch_set_section(plan_content)
    if parsed is not None:
        candidates.extend(parsed.entries)
    from_row = TouchSet.from_row(plan_row.get("touch_set"))
    if from_row is not None:
        candidates.extend(from_row.entries)
    paths: list[str] = []
    seen: set[str] = set()
    for entry in candidates:
        clean = normalise_touch_path(entry.path)
        if not clean or clean in seen or _is_provenance_path(clean):
            continue
        seen.add(clean)
        paths.append(clean)
    return paths


def _out_of_scope_touch_set_paths(
    intent_data: Mapping[str, Any] | None,
    plan_content: str,
    plan_row: Mapping[str, Any] | None = None,
) -> list[str]:
    intent = intent_data or {}
    raw_scope = intent.get("scope")
    if not isinstance(raw_scope, list):
        return []
    scope_entries = _normalised_entries(raw_scope)
    if not scope_entries:
        return []
    expansion_entries = _scope_expansion_entries(intent, plan_content, plan_row or {})
    allowed = (*scope_entries, *expansion_entries)
    return [
        path
        for path in _plan_touch_set_paths(plan_content, plan_row or {})
        if not any(entry_matches(entry, path) for entry in allowed)
    ]


def _plan_scope_failure_message(plan_branch: str, out_of_scope: list[str]) -> str:
    joined = ", ".join(out_of_scope)
    return (
        f"Plan stage failed: plan {plan_branch} lists ## Touch Set path(s) outside intent.scope without a recorded "
        f"scope expansion: {joined}. Remove them from ## Touch Set or record an explicit scope expansion on the plan "
        "or intent."
    )
