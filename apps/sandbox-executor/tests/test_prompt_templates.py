"""Template hygiene: no unmatched placeholder braces, and the shipped prompts carry the test-run and commit rules."""

import pathlib
import re
from collections.abc import Callable

import pytest
from sandbox_executor.entrypoint.executor import build_execution_prompt
from sandbox_executor.entrypoint.planner import render_placeholders

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
# Lower-case test-run rule phrases both the planner and the executor prompt must carry.
TEST_RUN_RULE_PHRASES = (
    "only when the intent requires measuring performance or timing behaviour",
    "state n and the reason",
    'never open-ended or "many" runs',
    "-n auto --dist worksteal",
    "median",
)


def known_names(texts: list[str]) -> set[str]:
    """Return every name used as a complete ``{name}`` placeholder in ``texts``, plus the planner's keys."""
    return PLANNER_KEYS | {name for text in texts for name in COMPLETE.findall(text)}


def unmatched_placeholders(text: str, names: set[str]) -> list[tuple[int, str]]:
    """Return ``(line_number, name)`` for each known ``{name`` that is not a well-formed ``{name}`` placeholder.

    A planner key must close immediately, because ``render_placeholders`` substitutes only ``{key}``: ``{plan_id here}``
    would render as ``P-1 here}``. Any other name may open a brace group closed later on its line, such as
    ``{standard | elevated}`` or ``{bullets with rule IDs}``, which is prose for the agent to fill in. A lone ``{``
    names nothing and is ignored.
    """

    def opener(group: set[str], close_ahead: str) -> str:
        alternatives = "|".join(map(re.escape, sorted(group))) or r"(?!)"
        return r"\{(" + alternatives + r")(?![A-Za-z0-9_])" + close_ahead

    planner = opener(names & PLANNER_KEYS, r"(?!\})")
    prose = opener(names - PLANNER_KEYS, r"(?![^{}\n]*\})")
    pattern = re.compile(planner + "|" + prose)
    return [(text.count("\n", 0, m.start()) + 1, m.group(m.lastindex)) for m in pattern.finditer(text)]


def test_lint_flags_injected_partial_brace() -> None:
    sample = "plans/{plan_id}.md\nsee {plan_id here\nand {plan_id"
    assert unmatched_placeholders(sample, {"plan_id"}) == [(2, "plan_id"), (3, "plan_id")]


def test_lint_ignores_prose_braces_and_longer_names() -> None:
    sample = "{standard | elevated | critical}\n{\n{plan_identifier}\n{plan_id}\n{bullets with rule IDs}"
    assert unmatched_placeholders(sample, {"plan_id", "standard", "bullets"}) == []


def test_lint_flags_closed_but_malformed_planner_placeholder() -> None:
    sample = "plans/{plan_id here}.md\n{standard here}\n{plan_id}"
    assert unmatched_placeholders(sample, {"plan_id", "standard"}) == [(1, "plan_id")]


def test_shipped_templates_have_no_unmatched_placeholders() -> None:
    assert TEMPLATES, "no holon-config/prompts/*.template.md found"
    texts = {path.name: path.read_text(encoding="utf-8") for path in TEMPLATES}
    names = known_names(list(texts.values()))
    defects = [(name, line, ph) for name, text in texts.items() for line, ph in unmatched_placeholders(text, names)]
    assert defects == [], f"unmatched placeholder braces (file, line, name): {defects}"


def rendered_planner() -> str:
    """Return the shipped planner template rendered with a dummy value for every planner key."""
    template = (REPO_ROOT / "holon-config" / "prompts" / "planner.template.md").read_text(encoding="utf-8")
    return render_placeholders(template, {name: f"dummy-{name}" for name in PLANNER_KEYS})


def rendered_executor() -> str:
    """Return the shipped executor template rendered through the executor's own prompt builder."""
    return build_execution_prompt(
        repo_dir=str(REPO_ROOT),
        plan_branch="P-1-test",
        plan_data={"plan_id": "P-1-test"},
        plan_content="Dummy plan.",
        intent_data={"intent_id": "I-1-test"},
        exec_id="E-1-test",
    )


def normalised(text: str) -> str:
    """Collapse every whitespace run to one space and lower-case, so prettier rewrapping cannot break assertions."""
    return " ".join(text.split()).lower()


@pytest.mark.parametrize("render", [rendered_planner, rendered_executor], ids=["planner", "executor"])
def test_rendered_templates_carry_test_run_rule(render: Callable[[], str]) -> None:
    text = normalised(render())
    missing = [phrase for phrase in TEST_RUN_RULE_PHRASES if phrase not in text]
    assert missing == [], f"test-run rule phrases missing: {missing}"


def test_rendered_executor_runs_suite_once_and_commits_each_step() -> None:
    text = normalised(rendered_executor())
    assert "uv run pytest <paths>" in text
    assert "uv run task test" in text
    assert ".venv/bin/pytest" not in text
    assert "after each completed plan step, stage and commit the work on the current branch" in text
    assert "never push, rebase, amend, squash or switch branches" in text


def test_rendered_executor_expands_every_placeholder() -> None:
    text = rendered_executor()
    leftovers = [
        name for name in ("repo_dir", "plan_id", "intent_id", "exec_id", "notes_path") if f"{{{name}}}" in text
    ]
    assert leftovers == []
    assert "executions/E-1-test.md" in text
