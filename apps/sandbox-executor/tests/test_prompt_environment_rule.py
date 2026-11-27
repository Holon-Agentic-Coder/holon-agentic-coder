import pathlib
from collections.abc import Callable

import pytest
from sandbox_executor.entrypoint.planner import render_placeholders
from sandbox_executor.review_ensemble import ReviewRequest, build_reviewer_prompt

from tests.test_prompt_templates import PLANNER_KEYS, normalised

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
PROMPTS_DIR = REPO_ROOT / "holon-config" / "prompts"
PLANNER_TEMPLATE_PATH = PROMPTS_DIR / "planner.template.md"
REVIEW_TEMPLATE_PATH = PROMPTS_DIR / "review_loop.template.md"
ENVIRONMENT_RULE_PHRASES = (
    "sets, overlays or removes environment variables, cwd or git config",
    "for a process that spawns children",
    "list the child processes affected",
    "a test that runs one of them under the new environment",
)
PLANNER_CONSEQUENCE_PHRASES = (
    "the plan lists those child processes",
    "names that test in the step that makes the change",
)
REVIEW_CONSEQUENCE_PHRASES = (
    "a missing list or a missing test as an important finding",
    "blocks approval",
)
PLANNER_PLACEHOLDER_COUNTS = {
    "agent": 1,
    "agent_version": 1,
    "model": 1,
    "intent_id": 1,
    "parent_intent_id": 1,
    "plan_id": 1,
    "plan_file": 2,
    "date": 1,
    "intent_json": 1,
    "budget_from_intent": 2,
}
REVIEW_PLACEHOLDER_COUNTS = {
    "repo_dir": 1,
    "target": 2,
    "target_branch": 5,
    "ensemble_size": 2,
    "max_iterations": 2,
    "iteration": 4,
    "pr_title": 1,
    "pr_description": 1,
    "diff": 1,
    "sha": 1,
}
REVIEW_ITERATION = 7351
REVIEW_SENTINELS = {
    "repo_dir": "sentinel-repo_dir",
    "target": "sentinel-head_sha",
    "target_branch": "sentinel-base_ref",
    "iteration": str(REVIEW_ITERATION),
    "pr_title": "sentinel-pr_title",
    "pr_description": "sentinel-pr_body",
    "diff": "sentinel-diff_text",
    "sha": "sentinel-head_sha",
}
PLANNER_SENTINELS = {name: f"sentinel-{name}" for name in PLANNER_PLACEHOLDER_COUNTS}


def planner_text() -> str:
    return PLANNER_TEMPLATE_PATH.read_text(encoding="utf-8")


def review_text() -> str:
    return REVIEW_TEMPLATE_PATH.read_text(encoding="utf-8")


def rendered_planner_with_sentinels() -> str:
    return render_placeholders(planner_text(), {name: f"sentinel-{name}" for name in PLANNER_KEYS})


def rendered_review_with_sentinels() -> str:
    request = ReviewRequest(
        repo_dir="sentinel-repo_dir",
        head_sha="sentinel-head_sha",
        base_ref="sentinel-base_ref",
        pr_title="sentinel-pr_title",
        pr_body="sentinel-pr_body",
        diff_text="sentinel-diff_text",
        iteration=REVIEW_ITERATION,
    )
    return build_reviewer_prompt(review_text(), request, "tmp/review/sentinel/report.md")


def missing_phrases(text: str, phrases: tuple[str, ...]) -> list[str]:
    flat = normalised(text)
    return [phrase for phrase in phrases if phrase not in flat]


def test_planner_template_requires_child_processes_and_environment_test() -> None:
    missing = missing_phrases(planner_text(), ENVIRONMENT_RULE_PHRASES + PLANNER_CONSEQUENCE_PHRASES)
    assert not missing, f"planner template lacks environment overlay rule phrases: {missing}"


def test_review_template_blocks_missing_child_processes_or_environment_test() -> None:
    missing = missing_phrases(review_text(), ENVIRONMENT_RULE_PHRASES + REVIEW_CONSEQUENCE_PHRASES)
    assert not missing, f"review template lacks environment overlay rule phrases: {missing}"


def test_environment_rule_sits_outside_the_plan_format_block() -> None:
    flat = normalised(planner_text())
    rule_at = flat.index(ENVIRONMENT_RULE_PHRASES[0])
    assert rule_at < flat.index("the plan format is as below.")


def test_environment_rule_is_a_reviewer_turn_contract_item() -> None:
    flat = normalised(review_text())
    contract_at = flat.index("## reviewer turn contract")
    data_block_at = flat.index("### reviewer data block")
    assert contract_at < flat.index(ENVIRONMENT_RULE_PHRASES[0]) < data_block_at


@pytest.mark.parametrize(
    ("template", "render", "counts", "sentinels"),
    [
        (planner_text, rendered_planner_with_sentinels, PLANNER_PLACEHOLDER_COUNTS, PLANNER_SENTINELS),
        (review_text, rendered_review_with_sentinels, REVIEW_PLACEHOLDER_COUNTS, REVIEW_SENTINELS),
    ],
    ids=["planner", "review"],
)
def test_placeholders_that_rendered_before_still_render(
    template: Callable[[], str], render: Callable[[], str], counts: dict[str, int], sentinels: dict[str, str]
) -> None:
    raw = template()
    rendered = render()
    for name, count in counts.items():
        assert raw.count(f"{{{name}}}") == count, name
        assert f"{{{name}}}" not in rendered, name
        if name in sentinels:
            assert sentinels[name] in rendered, name
