"""Template hygiene: no unmatched placeholder braces, the shipped prompts carry the test-run and commit rules, and the
review prompt and its in-code twin carry one posted-review shape and the reviewer data block contract."""

import pathlib
import re
from collections.abc import Callable

import pytest
from sandbox_executor.entrypoint.executor import build_execution_prompt
from sandbox_executor.entrypoint.planner import render_placeholders
from sandbox_executor.review_ensemble import ReviewerResult, parse_review_trailer
from sandbox_executor.review_report import ROLES_CLOSE, ROLES_OPEN, parse_reviewer_detail
from sandbox_executor.scaffold import REVIEW_LOOP_TEMPLATE

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
REVIEW_TEMPLATE_PATH = REPO_ROOT / "holon-config" / "prompts" / "review_loop.template.md"
MARKDOWN_TEMPLATE = REVIEW_TEMPLATE_PATH.read_text(encoding="utf-8")
REVIEW_TEMPLATES = {"markdown": MARKDOWN_TEMPLATE, "scaffold": REVIEW_LOOP_TEMPLATE}
# The review prompt's line count before the posted-shape rewrite; the prompt must stay shorter.
FORMER_REVIEW_PROMPT_LINES = 288
# Lower-case loop invariants every review prompt must state.
REVIEW_INVARIANT_PHRASES = (
    "approves unanimously",
    "zero critical, zero important and zero open nit findings",
    "every ci check passes",
    "check mergeability",
    "before posting a review or adding calibration",
    "sandbox_executor.flow.record_review_action()",
    "try_append_action_record(..., preserve_shape=true)",
    "review only the increment since the last reviewed commit",
    "fresh context",
    "never pauses",
    "never run `gh pr merge`",
    "merging remains with the human maintainer",
)
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


def fenced_block(text: str, after: str) -> str:
    """The body of the first fenced block that follows the heading ``after``."""
    section = text.split(after, 1)[1]
    match = re.search(r"^```[a-z]*\n(.*?)^```$", section, re.MULTILINE | re.DOTALL)
    assert match, f"no fenced block after {after!r}"
    return match.group(1)


def posted_shape_block(text: str) -> str:
    """The fenced "Posted review shape" block of a review template."""
    return fenced_block(text, "## Posted review shape")


def reviewer_data_section(text: str) -> str:
    """The "Reviewer data block" section of a review template, up to the next rule."""
    return text.split("### Reviewer data block", 1)[1].split("\n---\n", 1)[0]


def test_review_prompt_and_scaffold_twin_share_one_posted_shape():
    assert posted_shape_block(MARKDOWN_TEMPLATE) == posted_shape_block(REVIEW_LOOP_TEMPLATE)
    assert reviewer_data_section(MARKDOWN_TEMPLATE) == reviewer_data_section(REVIEW_LOOP_TEMPLATE)
    assert "Stage 5 calibration will be added to this PR before merge. Merging remains with the human maintainer." in (
        posted_shape_block(MARKDOWN_TEMPLATE)
    )


@pytest.mark.parametrize("name", sorted(REVIEW_TEMPLATES))
def test_review_prompt_wraps_description_and_diff_in_four_backtick_fences(name):
    text = REVIEW_TEMPLATES[name]
    assert "````markdown\n{pr_description}\n````" in text
    assert "````diff\n{diff}\n````" in text
    assert "```markdown\n{pr_description}" not in text.replace("````markdown", "")
    assert "```diff\n{diff}" not in text.replace("````diff", "")


def test_review_prompt_is_shorter_and_has_no_registry_matrix_or_legend():
    assert len(MARKDOWN_TEMPLATE.splitlines()) < FORMER_REVIEW_PROMPT_LINES
    for text in REVIEW_TEMPLATES.values():
        for banned in ("Persona Registry", "Activation Matrix", "CRITICAL / BLOCKER", "Standard Review Output Schema"):
            assert banned not in text


@pytest.mark.parametrize("name", sorted(REVIEW_TEMPLATES))
def test_review_prompt_keeps_every_loop_invariant(name):
    text = normalised(REVIEW_TEMPLATES[name])
    missing = [phrase for phrase in REVIEW_INVARIANT_PHRASES if phrase not in text]
    assert missing == [], f"loop invariants missing from the {name} review prompt: {missing}"


def test_review_prompt_says_the_on_disk_trailer_is_never_posted():
    text = normalised(MARKDOWN_TEMPLATE)
    assert "written to on-disk reports only and never posted to the pr" in text
    assert "VERDICT: <APPROVED" not in posted_shape_block(MARKDOWN_TEMPLATE)


def test_review_prompt_example_report_parses_with_the_real_parsers():
    example = next(
        block
        for block in re.findall(r"^```text\n(.*?)^```$", MARKDOWN_TEMPLATE, re.MULTILINE | re.DOTALL)
        if ROLES_OPEN in block
    )
    assert ROLES_OPEN in reviewer_data_section(MARKDOWN_TEMPLATE) and ROLES_CLOSE in example
    parsed = parse_review_trailer(example)
    detail = parse_reviewer_detail(ReviewerResult.completed(1, "a", "m", "r", example, parsed))

    assert [(role.role, role.verdict) for role in detail.roles] == [("Testing", "PASS")]
    assert [claim.evidence for claim in detail.verified] == ["tests/test_review_report.py"]
