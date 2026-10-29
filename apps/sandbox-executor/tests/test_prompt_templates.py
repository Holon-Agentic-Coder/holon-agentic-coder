"""Template hygiene: no shipped prompt template may carry an unmatched placeholder brace."""

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
TEMPLATES = sorted((REPO_ROOT / "holon-config" / "prompts").glob("*.template.md"))
COMPLETE = re.compile(r"\{([a-z][a-z0-9_]*)\}")
# Bare names the planner renders through render_placeholders.
PLANNER_KEYS = {
    "timestamp",
    "agent",
    "agent_version",
    "model",
    "intent_id",
    "parent_intent_id",
    "description",
    "plan_id",
    "date",
    "intent_json",
    "safe_model",
    "budget_from_intent",
}


def known_names(texts: list[str]) -> set[str]:
    """Return every name used as a complete ``{name}`` placeholder in ``texts``, plus the planner's keys."""
    return PLANNER_KEYS | {name for text in texts for name in COMPLETE.findall(text)}


def unmatched_placeholders(text: str, names: set[str]) -> list[tuple[int, str]]:
    """Return ``(line_number, name)`` for each known ``{name`` whose brace is never closed on its line.

    Brace groups closed on the same line, such as ``{standard | elevated}`` or ``{bullets with rule IDs}``, are prose
    for the agent to fill in rather than placeholders, and a lone ``{`` names nothing; both are ignored.
    """
    pattern = re.compile(r"\{(" + "|".join(map(re.escape, sorted(names))) + r")(?![A-Za-z0-9_])(?![^{}\n]*\})")
    return [(text.count("\n", 0, m.start()) + 1, m.group(1)) for m in pattern.finditer(text)]


def test_lint_flags_injected_partial_brace() -> None:
    sample = "plans/{plan_id}.md\nsee {plan_id here\nand {plan_id"
    assert unmatched_placeholders(sample, {"plan_id"}) == [(2, "plan_id"), (3, "plan_id")]


def test_lint_ignores_prose_braces_and_longer_names() -> None:
    sample = "{standard | elevated | critical}\n{\n{plan_identifier}\n{plan_id}\n{bullets with rule IDs}"
    assert unmatched_placeholders(sample, {"plan_id", "standard", "bullets"}) == []


def test_shipped_templates_have_no_unmatched_placeholders() -> None:
    assert TEMPLATES, "no holon-config/prompts/*.template.md found"
    texts = {path.name: path.read_text(encoding="utf-8") for path in TEMPLATES}
    names = known_names(list(texts.values()))
    defects = [(name, line, ph) for name, text in texts.items() for line, ph in unmatched_placeholders(text, names)]
    assert defects == [], f"unmatched placeholder braces (file, line, name): {defects}"
