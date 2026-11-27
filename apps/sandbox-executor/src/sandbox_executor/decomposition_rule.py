from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sandbox_executor.budgets import DEFAULT_ENTROPY_BUDGET, resolve_entropy_budget
from sandbox_executor.ref_names import MAX_SLUG_LENGTH, truncate_derived_slug

DECISION_EXECUTE = "execute"
DECISION_DECOMPOSED = "decomposed"
DECISION_OVER_BUDGET = "over_budget"

_SUB_INTENT_HEADINGS = frozenset({"sub intent list", "sub intents"})
_SUB_INTENT_HEADING_LEVELS = (2, 3)
_HEADING_RE = re.compile(r"^(#{1,6})(?:\s+(.*?)\s*#*\s*)?$")
_HEADING_SEPARATOR_RE = re.compile(r"[\s\-_\u2010\u2011]+")
_LIST_ITEM_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+(.*)$")
# why: planner template requires real items to start with an imperative verb, so leading placeholders never collide
_PLACEHOLDER_RE = re.compile(r"^(?:none|n/a|na|nil|no|not)\b")
_PLACEHOLDER_WRAPPERS = "()[]{}<>\"'`*_~\u2018\u2019\u201c\u201d"
_TRAILING_PLACEHOLDER_PUNCTUATION = ".!?;:,"
_FENCE_OPEN_RE = re.compile(r"^(`{3,}(?=[^`]*$)|~{3,})")
_FENCE_CLOSE_RE = re.compile(r"^(`{3,}|~{3,})$")
_THEMATIC_BREAK_RE = re.compile(r"^(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")
_SETEXT_UNDERLINE_RE = re.compile(r"^(=+|-+)$")
_NON_ALNUM_SLUG_RE = re.compile(r"[^a-z0-9]+")

_DECISIONS = frozenset({DECISION_EXECUTE, DECISION_DECOMPOSED, DECISION_OVER_BUDGET})
_SUB_INTENT_KEYS = ("slug", "description", "goal")
_DEFAULT_SUB_INTENT_SLUG = "sub-intent"
_TAB_WIDTH = 4
_MAX_LIST_INDENT = 3
_SETEXT_LEVEL_1 = 1
_SETEXT_LEVEL_2 = 2
_PARAGRAPH_KIND_TEXT = "text"
_PARAGRAPH_KIND_ITEM = "item"


class InvalidPlanDecisionError(ValueError):
    pass


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _is_valid_sub_intent_item(item: Any) -> bool:
    return isinstance(item, dict) and all(isinstance(item.get(key), str) for key in _SUB_INTENT_KEYS)


def _copy_sub_intent_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: item[key] for key in _SUB_INTENT_KEYS} for item in items]


def _recorded_sub_intents(decision: str, row: dict[str, Any]) -> list[dict[str, Any]]:
    if decision != DECISION_DECOMPOSED:
        if "sub_intents" in row:
            raise InvalidPlanDecisionError(f"a plan row recording {decision!r} must not carry sub_intents")
        return []
    raw = row.get("sub_intents")
    if not isinstance(raw, list) or not raw:
        raise InvalidPlanDecisionError("a decomposed plan row must record a non-empty sub_intents list")
    for item in raw:
        if not _is_valid_sub_intent_item(item):
            raise InvalidPlanDecisionError("every recorded sub-intent needs string slug, description and goal")
    return _copy_sub_intent_items(raw)


@dataclass(frozen=True)
class DecompositionDecision:
    decision: str
    sub_intents: list[dict[str, Any]] = field(default_factory=list)
    entropy: float = 0.0
    entropy_budget: float = DEFAULT_ENTROPY_BUDGET

    def to_plan_fields(self) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "decomposition_decision": self.decision,
            "entropy": self.entropy,
            "entropy_budget": self.entropy_budget,
        }
        if self.decision == DECISION_DECOMPOSED:
            fields["sub_intents"] = _copy_sub_intent_items(self.sub_intents)
        return fields

    @classmethod
    def from_plan_row(cls, row: dict[str, Any]) -> DecompositionDecision | None:
        if "decomposition_decision" not in row:
            return None
        decision = row["decomposition_decision"]
        if not isinstance(decision, str) or decision not in _DECISIONS:
            raise InvalidPlanDecisionError(f"unknown decomposition decision {decision!r} on the plan row")
        sub_intents = _recorded_sub_intents(decision, row)
        return cls(decision, sub_intents, _as_float(row.get("entropy"), 0.0), resolve_entropy_budget(row, None))


def _normalise_heading(text: str) -> str:
    return _HEADING_SEPARATOR_RE.sub(" ", text.strip().lower()).strip()


def _normalise_placeholder_item(text: str) -> str:
    normalised = re.sub(r"\s+", " ", text).strip()
    previous = None
    while normalised != previous:
        previous = normalised
        normalised = normalised.strip(_PLACEHOLDER_WRAPPERS + " ").rstrip(_TRAILING_PLACEHOLDER_PUNCTUATION)
    return normalised.lower()


def is_placeholder_item(text: str) -> bool:
    return bool(_PLACEHOLDER_RE.match(_normalise_placeholder_item(text)))


def _is_within_top_level_indent(indent: int) -> bool:
    return indent <= _MAX_LIST_INDENT


def _is_thematic_break(stripped: str, indent: int) -> bool:
    return _is_within_top_level_indent(indent) and bool(_THEMATIC_BREAK_RE.match(stripped))


def _is_nested_in_list(indent: int, list_col: int | None) -> bool:
    return list_col is not None and indent >= list_col


def _list_item_content_column(indent: int, stripped: str, item_text: str) -> int:
    return indent + len(stripped) - len(item_text)


def _is_matching_closing_fence(stripped: str, active_fence: str) -> bool:
    closing = _FENCE_CLOSE_RE.match(stripped)
    return bool(closing and closing.group(1)[0] == active_fence[0] and len(closing.group(1)) >= len(active_fence))


def _paragraph_state(
    stripped: str, indent: int, kind: str | None, list_col: int | None
) -> tuple[str | None, int | None]:
    if not stripped:
        return None, list_col
    nested = _is_nested_in_list(indent, list_col)
    if _is_thematic_break(stripped, indent):
        return None, list_col if nested else None
    item = _LIST_ITEM_RE.match(stripped)
    if item and (_is_within_top_level_indent(indent) or nested):
        content_col = list_col if nested else _list_item_content_column(indent, stripped, item.group(1))
        return _PARAGRAPH_KIND_ITEM, content_col
    if kind is not None:
        return kind, list_col
    if nested:
        return _PARAGRAPH_KIND_ITEM, list_col
    return (_PARAGRAPH_KIND_TEXT if _is_within_top_level_indent(indent) else None), None


def _setext_level(lines: list[str], index: int) -> int | None:
    if index + 1 >= len(lines):
        return None
    following = lines[index + 1].expandtabs(_TAB_WIDTH)
    underline = _SETEXT_UNDERLINE_RE.match(following.strip())
    following_indent = len(following) - len(following.lstrip())
    if not underline or not _is_within_top_level_indent(following_indent):
        return None
    return _SETEXT_LEVEL_1 if underline.group(1)[0] == "=" else _SETEXT_LEVEL_2


@dataclass
class _SubIntentScan:
    lines: list[str]
    items: list[tuple[str, list[str]]] = field(default_factory=list)
    in_section: bool = False
    fence: str | None = None
    base_indent: int | None = None
    skip_underline: bool = False
    paragraph: str | None = None
    paragraph_lines: list[str] = field(default_factory=list)
    list_col: int | None = None

    def scan(self) -> list[tuple[str, list[str]]]:
        for index, line in enumerate(self.lines):
            if self.skip_underline:
                self.skip_underline = False
                continue
            expanded = line.expandtabs(_TAB_WIDTH)
            stripped = expanded.strip()
            if self.fence is not None:
                self._close_fence(stripped)
                continue
            indent = len(expanded) - len(expanded.lstrip())
            if self._open_fence(stripped, indent):
                continue
            heading = self._heading(index, stripped, indent)
            if heading is not None:
                self._enter_heading(*heading)
            elif self.in_section and stripped:
                self._collect(stripped, indent)
        return self.items

    def _close_fence(self, stripped: str) -> None:
        if self.fence and _is_matching_closing_fence(stripped, self.fence):
            self.fence = None

    def _open_fence(self, stripped: str, indent: int) -> bool:
        opening = _FENCE_OPEN_RE.match(stripped)
        if not opening:
            return False
        self.fence = opening.group(1)
        self.paragraph = None
        self.list_col = self.list_col if _is_nested_in_list(indent, self.list_col) else None
        return True

    def _heading(self, index: int, stripped: str, indent: int) -> tuple[int, str, bool] | None:
        heading = _HEADING_RE.match(stripped) if _is_within_top_level_indent(indent) else None
        if heading:
            return len(heading.group(1)), heading.group(2) or "", False
        previous = self.paragraph
        self.paragraph, self.list_col = _paragraph_state(stripped, indent, self.paragraph, self.list_col)
        if self.paragraph != _PARAGRAPH_KIND_TEXT:
            return None
        self.paragraph_lines = [*self.paragraph_lines, stripped] if previous == _PARAGRAPH_KIND_TEXT else [stripped]
        setext = _setext_level(self.lines, index)
        return None if setext is None else (setext, " ".join(self.paragraph_lines), True)

    def _enter_heading(self, level: int, text: str, setext: bool) -> None:
        self.in_section = level in _SUB_INTENT_HEADING_LEVELS and _normalise_heading(text) in _SUB_INTENT_HEADINGS
        self.base_indent = None
        self.paragraph, self.list_col = None, None
        self.skip_underline = setext

    def _collect(self, stripped: str, indent: int) -> None:
        # why: thematic breaks like "* * *" or "- - -" also match _LIST_ITEM_RE and are separators, never items
        if _is_thematic_break(stripped, indent):
            return
        # why: nested bullets and continuation lines belong to the parent item, never as separate child sub-intents
        if self.base_indent is not None and indent > self.base_indent:
            self.items[-1][1].append(stripped)
            return
        item = _LIST_ITEM_RE.match(stripped)
        if item and item.group(1).strip() and _is_within_top_level_indent(indent):
            if self.base_indent is None:
                self.base_indent = indent
            self.items.append((item.group(1).strip(), []))


def _is_skippable_item_text(text: str) -> bool:
    return is_placeholder_item(text) or not any(char.isalnum() for char in text)


def _next_unique_child_slug(text: str, seen_slug_counts: dict[str, int]) -> str:
    base = truncate_derived_slug(_NON_ALNUM_SLUG_RE.sub("-", text.lower()).strip("-")) or _DEFAULT_SUB_INTENT_SLUG
    count = seen_slug_counts.get(base, 0) + 1
    seen_slug_counts[base] = count
    suffix = "" if count == 1 else f"-{count}"
    return truncate_derived_slug(base, MAX_SLUG_LENGTH - len(suffix)) + suffix


def _sub_intent_dicts(items: list[tuple[str, list[str]]]) -> list[dict[str, Any]]:
    sub_intents: list[dict[str, Any]] = []
    seen_slug_counts: dict[str, int] = {}
    for text, details in items:
        if _is_skippable_item_text(text):
            continue
        slug = _next_unique_child_slug(text, seen_slug_counts)
        description = "\n".join([text, *details])
        sub_intents.append({"slug": slug, "description": description, "goal": text})
    return sub_intents


def parse_sub_intent_list(plan_content: str) -> list[dict[str, Any]]:
    return _sub_intent_dicts(_SubIntentScan(plan_content.splitlines()).scan())


def should_decompose(
    plan_data: dict[str, Any], plan_content: str, intent_data: dict[str, Any] | None = None
) -> DecompositionDecision:
    entropy = _as_float(plan_data.get("entropy"), 0.0)
    budget = resolve_entropy_budget(plan_data, intent_data)
    sub_intents = parse_sub_intent_list(plan_content)
    if sub_intents:
        return DecompositionDecision(DECISION_DECOMPOSED, sub_intents, entropy, budget)
    if entropy > budget:
        return DecompositionDecision(DECISION_OVER_BUDGET, [], entropy, budget)
    return DecompositionDecision(DECISION_EXECUTE, [], entropy, budget)
