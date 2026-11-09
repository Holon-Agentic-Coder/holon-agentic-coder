"""The decomposition rule: whether a plan is executed, decomposed into its sub-intent list, or over budget.

This module is the single home of the rule and of the one serialisation of its result. The planner container records
the decision on the plan row it appends (:meth:`DecompositionDecision.to_plan_fields`); the flow engine and the executor
container read it back (:meth:`DecompositionDecision.from_plan_row`). It imports nothing from
``sandbox_executor.entrypoint``, so the host flow engine can use it without depending on a container entrypoint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sandbox_executor.budgets import DEFAULT_ENTROPY_BUDGET, resolve_entropy_budget

# Decision values returned by should_decompose().
DECISION_EXECUTE = "execute"
DECISION_DECOMPOSED = "decomposed"
DECISION_OVER_BUDGET = "over_budget"

# Normalised heading texts (lower-cased, hyphens, underscores and runs of spaces collapsed to one space) that open an
# explicit sub-intent list. The legacy capitalised plural heading normalises to "sub intents".
_SUB_INTENT_HEADINGS = frozenset({"sub intent list", "sub intents"})
# An ATX heading (matched on a stripped line): 1-6 "#" followed by whitespace and the text, or by nothing at all, so
# a bare "#" or "##" is an empty heading, as in CommonMark; group 2 is None for an empty heading.
_HEADING_RE = re.compile(r"^(#{1,6})(?:\s+(.*?)\s*#*\s*)?$")
_LIST_ITEM_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+(.*)$")
# An item is a placeholder ("no child work") when, once normalised by _normalise_placeholder_item, its first whole
# word is one of these, whatever follows: "None (plan fits the budget)", "N/A (single step)" and "No sub-intents
# needed" are placeholders, while "Nonetheless ...", "Normalise ...", "Notify ..." and "Note ..." name real work. The
# planner template requires every real item to start with an imperative verb, so a real item never opens with one.
_PLACEHOLDER_RE = re.compile(r"^(?:none|n/a|na|nil|no|not)\b")
# Wrapping characters stripped from both ends of an item before the placeholder test: brackets, parentheses, straight
# and curly quotes, and markdown emphasis or code markers.
_PLACEHOLDER_WRAPPERS = "()[]{}<>\"'`*_~\u2018\u2019\u201c\u201d"
# Code fences, matched against a stripped line so a fence nested in a list item at any indentation is still a fence: an
# opener is 3+ backticks or tildes (a backtick fence's info string may not contain a backtick); it closes only on a line
# of the same character, at least as long, with nothing but whitespace after it.
_FENCE_OPEN_RE = re.compile(r"^(`{3,}(?=[^`]*$)|~{3,})")
_FENCE_CLOSE_RE = re.compile(r"^(`{3,}|~{3,})$")
# A thematic break: 3+ of the same "*", "-" or "_", optionally separated by spaces or tabs (matched on a stripped line).
_THEMATIC_BREAK_RE = re.compile(r"^(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")
# A setext heading underline (matched on a stripped line): only "=" (level 1) or only "-" (level 2), no inner spaces.
_SETEXT_UNDERLINE_RE = re.compile(r"^(=+|-+)$")

# Every decision a plan row may record, and the keys of each recorded sub-intent item.
_DECISIONS = frozenset({DECISION_EXECUTE, DECISION_DECOMPOSED, DECISION_OVER_BUDGET})
_SUB_INTENT_KEYS = ("slug", "description", "goal")


class InvalidPlanDecisionError(ValueError):
    """A plan row records a decomposition decision that is not one :func:`should_decompose` can return."""


def _as_float(value: Any, default: float = 0.0) -> float:
    """Return ``value`` as a float, or ``default`` when it is ``None`` or not numeric."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _recorded_sub_intents(decision: str, row: dict[str, Any]) -> list[dict[str, Any]]:
    """Return copies of the ``sub_intents`` a plan row records for ``decision``, validating their shape.

    Raises:
        InvalidPlanDecisionError: A ``decomposed`` row has no non-empty list of ``{slug, description, goal}`` string
            dicts, or another decision carries ``sub_intents``.
    """
    if decision != DECISION_DECOMPOSED:
        if "sub_intents" in row:
            raise InvalidPlanDecisionError(f"a plan row recording {decision!r} must not carry sub_intents")
        return []
    raw = row.get("sub_intents")
    if not isinstance(raw, list) or not raw:
        raise InvalidPlanDecisionError("a decomposed plan row must record a non-empty sub_intents list")
    for item in raw:
        if not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in _SUB_INTENT_KEYS):
            raise InvalidPlanDecisionError("every recorded sub-intent needs string slug, description and goal")
    return [{key: item[key] for key in _SUB_INTENT_KEYS} for item in raw]


@dataclass(frozen=True)
class DecompositionDecision:
    """Outcome of :func:`should_decompose`.

    Attributes:
        decision: ``"execute"``, ``"decomposed"`` (the plan lists an explicit sub-intent list) or ``"over_budget"``
            (the plan entropy exceeds the budget and the plan lists no sub-intent list).
        sub_intents: The parsed sub-intent list, one ``{slug, description, goal}`` dict per item; empty unless
            ``decision`` is ``"decomposed"``.
        entropy: The plan entropy compared against the budget.
        entropy_budget: The resolved entropy budget (see :func:`sandbox_executor.budgets.resolve_entropy_budget`).
    """

    decision: str
    sub_intents: list[dict[str, Any]] = field(default_factory=list)
    entropy: float = 0.0
    entropy_budget: float = DEFAULT_ENTROPY_BUDGET

    def to_plan_fields(self) -> dict[str, Any]:
        """Return the plan-row fields that record this decision.

        The fields are ``decomposition_decision``, ``entropy``, ``entropy_budget`` and, only for ``decomposed``,
        ``sub_intents`` (one ``{slug, description, goal}`` copy per item).
        """
        fields: dict[str, Any] = {
            "decomposition_decision": self.decision,
            "entropy": self.entropy,
            "entropy_budget": self.entropy_budget,
        }
        if self.decision == DECISION_DECOMPOSED:
            fields["sub_intents"] = [{key: item[key] for key in _SUB_INTENT_KEYS} for item in self.sub_intents]
        return fields

    @classmethod
    def from_plan_row(cls, row: dict[str, Any]) -> DecompositionDecision | None:
        """Rebuild the decision a plan row records, the inverse of :meth:`to_plan_fields`.

        Args:
            row: A ``plans.jsonl`` row.

        Returns:
            The recorded decision, or ``None`` when the row has no ``decomposition_decision`` (an older planner image).

        Raises:
            InvalidPlanDecisionError: The recorded decision is unknown or its ``sub_intents`` do not match it.
        """
        if "decomposition_decision" not in row:
            return None
        decision = row["decomposition_decision"]
        if not isinstance(decision, str) or decision not in _DECISIONS:
            raise InvalidPlanDecisionError(f"unknown decomposition decision {decision!r} on the plan row")
        sub_intents = _recorded_sub_intents(decision, row)
        return cls(decision, sub_intents, _as_float(row.get("entropy"), 0.0), resolve_entropy_budget(row, None))


_MAX_LIST_INDENT = 3
"""Deepest indentation, in columns, at which CommonMark still reads a list item as top-level."""


def _normalise_heading(text: str) -> str:
    """Lower-case a heading and collapse hyphens, underscores, non-breaking hyphens and whitespace to single spaces."""
    return re.sub(r"[\s\-_\u2010\u2011]+", " ", text.strip().lower()).strip()


def _normalise_placeholder_item(text: str) -> str:
    """Lower-case an item, strip wrapping brackets, quotes and emphasis markers, and drop trailing punctuation."""
    normalised = re.sub(r"\s+", " ", text).strip()
    previous = None
    while normalised != previous:
        previous = normalised
        normalised = normalised.strip(_PLACEHOLDER_WRAPPERS + " ").rstrip(".!?;:,")
    return normalised.lower()


def is_placeholder_item(text: str) -> bool:
    """True when a sub-intent list item says there is no child work rather than naming any (see _PLACEHOLDER_RE)."""
    return bool(_PLACEHOLDER_RE.match(_normalise_placeholder_item(text)))


def _paragraph_state(
    stripped: str, indent: int, kind: str | None, list_col: int | None
) -> tuple[str | None, int | None]:
    """Return the ``(paragraph kind, list content column)`` after a line outside fences that is not an ATX heading.

    The kind is ``"text"`` for a plain paragraph, ``"item"`` for a list item and its content (including lazy
    continuation lines, which CommonMark folds into the item's paragraph even at column 0) and ``None`` after a blank
    line, a thematic break or an indented code line. ``list_col`` is the content column of the open top-level item, so
    a line after a blank line still belongs to the item when indented at least that far.
    """
    if not stripped:
        return None, list_col
    nested = list_col is not None and indent >= list_col
    if indent <= _MAX_LIST_INDENT and _THEMATIC_BREAK_RE.match(stripped):
        return None, list_col if nested else None
    item = _LIST_ITEM_RE.match(stripped)
    if item and (indent <= _MAX_LIST_INDENT or nested):
        return "item", list_col if nested else indent + len(stripped) - len(item.group(1))
    if kind is not None:
        # A continuation line, lazy or not, stays in the paragraph it continues.
        return kind, list_col
    if nested:
        return "item", list_col
    return ("text" if indent <= _MAX_LIST_INDENT else None), None


def _setext_level(lines: list[str], index: int) -> int | None:
    """Return the setext level (1 for ``=``, 2 for ``-``) when the line after a plain paragraph line underlines it.

    The caller only asks for a line of a plain paragraph (``"text"`` from :func:`_paragraph_state`), never for a list
    item, its content or its lazy continuation, so a ``---`` after any of them stays a thematic break and a ``===``
    stays continuation text. The underline is only ``=`` or only ``-`` at 0-3 columns.
    """
    if index + 1 >= len(lines):
        return None
    following = lines[index + 1].expandtabs(4)
    underline = _SETEXT_UNDERLINE_RE.match(following.strip())
    if not underline or len(following) - len(following.lstrip()) > _MAX_LIST_INDENT:
        return None
    return 1 if underline.group(1)[0] == "=" else 2


@dataclass
class _SubIntentScan:
    """Line-by-line state of :func:`parse_sub_intent_list`, whose docstring states the rules it applies."""

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
        """Return every top-level item of the sub-intent sections, each with its nested detail lines."""
        for index, line in enumerate(self.lines):
            if self.skip_underline:
                # The underline of the setext heading on the line above.
                self.skip_underline = False
                continue
            expanded = line.expandtabs(4)
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
        """Inside a fenced block nothing is a heading or an item; only a matching closing fence ends it."""
        fence = self.fence or ""
        closing = _FENCE_CLOSE_RE.match(stripped)
        if closing and closing.group(1)[0] == fence[0] and len(closing.group(1)) >= len(fence):
            self.fence = None

    def _open_fence(self, stripped: str, indent: int) -> bool:
        """Open a fence at any indentation; an item stays open across it only when the fence is nested in it."""
        opening = _FENCE_OPEN_RE.match(stripped)
        if not opening:
            return False
        self.fence = opening.group(1)
        self.paragraph = None
        self.list_col = self.list_col if self.list_col is not None and indent >= self.list_col else None
        return True

    def _heading(self, index: int, stripped: str, indent: int) -> tuple[int, str, bool] | None:
        """Return ``(level, text, is_setext)`` for a heading line; otherwise advance the paragraph state.

        A setext heading's text is its whole paragraph, so "Sub-intent" then "list" over "---" opens the list.
        """
        heading = _HEADING_RE.match(stripped) if indent <= _MAX_LIST_INDENT else None
        if heading:
            return len(heading.group(1)), heading.group(2) or "", False
        previous = self.paragraph
        self.paragraph, self.list_col = _paragraph_state(stripped, indent, self.paragraph, self.list_col)
        if self.paragraph != "text":
            return None
        self.paragraph_lines = [*self.paragraph_lines, stripped] if previous == "text" else [stripped]
        setext = _setext_level(self.lines, index)
        return None if setext is None else (setext, " ".join(self.paragraph_lines), True)

    def _enter_heading(self, level: int, text: str, setext: bool) -> None:
        """Open or close the sub-intent section at a heading, and skip a setext heading's underline."""
        self.in_section = level in (2, 3) and _normalise_heading(text) in _SUB_INTENT_HEADINGS
        self.base_indent = None
        self.paragraph, self.list_col = None, None
        self.skip_underline = setext

    def _collect(self, stripped: str, indent: int) -> None:
        """Collect a non-blank line inside the section as a new item or as detail of the item above."""
        if indent <= _MAX_LIST_INDENT and _THEMATIC_BREAK_RE.match(stripped):
            # "* * *" or "- - -" is a separator, never an item or an item's detail.
            return
        if self.base_indent is not None and indent > self.base_indent:
            # Nested bullets and continuation lines belong to the item above them, never to a child of their own.
            self.items[-1][1].append(stripped)
            return
        item = _LIST_ITEM_RE.match(stripped)
        if item and item.group(1).strip() and indent <= _MAX_LIST_INDENT:
            # CommonMark allows a top-level list to be indented 0-3 spaces; the first item fixes the section's level.
            if self.base_indent is None:
                self.base_indent = indent
            self.items.append((item.group(1).strip(), []))


def _sub_intent_dicts(items: list[tuple[str, list[str]]]) -> list[dict[str, Any]]:
    """Turn scanned items into ``{slug, description, goal}`` dicts, skipping placeholders and suffixing duplicates."""
    sub_intents: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for text, details in items:
        if is_placeholder_item(text) or not any(c.isalnum() for c in text):
            # Placeholders and punctuation-only items name no child work.
            continue
        base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48].strip("-") or "sub-intent"
        count = seen.get(base, 0) + 1
        seen[base] = count
        slug = base if count == 1 else f"{base}-{count}"
        description = "\n".join([text, *details])
        sub_intents.append({"slug": slug, "description": description, "goal": text})
    return sub_intents


def parse_sub_intent_list(plan_content: str) -> list[dict[str, Any]]:
    """Parse the explicit sub-intent list from a plan's markdown.

    Only a level-2 or level-3 heading whose normalised text is ``sub intent list`` or ``sub intents`` opens the list;
    top-level bullet and numbered items are collected until the next heading. The first item fixes the top-level
    indentation (0-3 columns, as CommonMark allows); lines indented deeper than it, such as nested detail bullets,
    never become items of their own: they are appended to the preceding item's description. A fence opens at any
    indentation (so a code block nested in an item counts) and every line inside it is ignored, headings included; it
    closes only on a run of the same character at least as long as its opener, and an unclosed fence runs to the end of
    the plan. Outside fences, only a heading indented 0-3 columns ends the section, whether ATX (``## Next``) or setext
    (a plain paragraph underlined with ``===`` or ``---``, see :func:`_setext_level`; a level-2 setext heading opens
    the list like its ATX form), and thematic breaks (``* * *``, or ``---`` not under a plain paragraph, such as one
    under an item or its lazy continuation line) at 0-3 columns are skipped.
    Per-step sub-intent recommendation bullets are never under such a heading, so they are ignored. Each item becomes
    ``{slug, description, goal}``; the slug and goal come from the item text only. Empty items, items with no
    alphanumeric character and placeholder items are skipped (an item that, stripped of wrapping brackets, quotes and
    emphasis and lower-cased, has ``none``, ``n/a``, ``na``, ``nil``, ``no`` or ``not`` as its first whole word; see
    :func:`is_placeholder_item`), and duplicate slugs get a numeric suffix.

    Args:
        plan_content: The plan markdown.

    Returns:
        The parsed items, in plan order; empty when the plan has no such section.
    """
    return _sub_intent_dicts(_SubIntentScan(plan_content.splitlines()).scan())


def should_decompose(
    plan_data: dict[str, Any], plan_content: str, intent_data: dict[str, Any] | None = None
) -> DecompositionDecision:
    """Decide whether a plan is executed, decomposed into its own sub-intent list, or rejected as over budget.

    The budget is the one the planner showed the agent and persisted on the plan row, resolved by
    :func:`sandbox_executor.budgets.resolve_entropy_budget` (plan row, then intent row, then the shared default). A
    non-empty explicit sub-intent list gives ``decomposed`` whatever the entropy; a plan above the budget without a
    list gives ``over_budget``; anything else gives ``execute``. No child work is ever invented. The flow, not this
    function, enforces ``MAX_SUB_INTENT_DEPTH``: it fails Stage 3 on a ``decomposed`` plan nested beyond that limit.

    Args:
        plan_data: The plan's ``plans.jsonl`` row.
        plan_content: The plan markdown.
        intent_data: The parent intent's ``intents.jsonl`` row, when known.

    Returns:
        A :class:`DecompositionDecision`.
    """
    entropy = _as_float(plan_data.get("entropy"), 0.0)
    budget = resolve_entropy_budget(plan_data, intent_data)
    sub_intents = parse_sub_intent_list(plan_content)
    if sub_intents:
        return DecompositionDecision(DECISION_DECOMPOSED, sub_intents, entropy, budget)
    if entropy > budget:
        return DecompositionDecision(DECISION_OVER_BUDGET, [], entropy, budget)
    return DecompositionDecision(DECISION_EXECUTE, [], entropy, budget)
