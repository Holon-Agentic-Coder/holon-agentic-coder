"""Literal-only tests for the I/O-free semantic PR metadata in ``flow/github/pr_metadata.py``."""

from __future__ import annotations

import pytest
from sandbox_executor.flow.github.pr_metadata import generate_semantic_pr_metadata


def title_of(intent: dict) -> str:
    return generate_semantic_pr_metadata(intent)[0]


def body_of(intent: dict, plan: dict | None = None, runner: dict | None = None) -> str:
    return generate_semantic_pr_metadata(intent, plan, runner=runner)[1]


def runner_lines_of(body: str) -> tuple[str, str]:
    lines = body.splitlines()
    agent_line = next(line for line in lines if line.startswith("- **Agent:**"))
    model_line = next(line for line in lines if line.startswith("- **Model:**"))
    return agent_line, model_line


@pytest.mark.parametrize(
    ("category", "prefix"),
    [
        ("Feature", "feat"),
        ("bugfix", "fix"),
        ("refactoring", "refactor"),
        ("doc", "docs"),
        ("tests", "test"),
        ("perf", "perf"),
        ("unknown", "feat"),
        (None, "feat"),
    ],
)
def test_category_maps_to_a_conventional_prefix(category, prefix):
    assert title_of({"category": category, "title": "T"}) == f"{prefix}(core): T"


@pytest.mark.parametrize(
    ("intent", "scope"),
    [
        ({"labels": ["GitHub", "x"]}, "github"),
        ({"labels": "Review, ci"}, "review"),
        ({"labels": [], "slug": "adapter-pr-review"}, "adapter"),
        ({"labels": [" "], "slug": "ci-x"}, "ci"),
        ({"labels": "  "}, "core"),
        ({"labels": ["Core Lib!"]}, "corelib"),
        ({"labels": ["!!!"]}, "core"),
        ({}, "core"),
    ],
)
def test_scope_from_labels_slug_or_core(intent, scope):
    assert title_of({**intent, "title": "T"}) == f"feat({scope}): T"


def test_title_strips_an_existing_prefix_and_extra_lines():
    assert title_of({"category": "fix", "title": "FEAT(x): Do it\nsecond line"}) == "fix(core): Do it"
    assert title_of({"description": "fix: From description"}) == "feat(core): From description"
    assert title_of({}) == "feat(core): Update components"


def test_title_is_truncated_to_one_hundred_characters():
    title = title_of({"title": "a" * 200})
    assert len(title) == 100
    assert title == "feat(core): " + "a" * 85 + "..."


def test_body_for_an_empty_intent():
    assert body_of({}) == (
        "## Summary\nNo description provided.\n\n"
        "## Key Changes\n- Implement Update components\n\n"
        "## References\nNone\n\n"
        "## Plan & Validation Metadata\n"
        "- **Plan ID:** `N/A`\n- **Expected Value (EV):** `N/A`\n- **Agent:** `N/A`\n- **Model:** `N/A`\n"
    )


def test_summary_with_description_and_distinct_goal():
    body = body_of({"description": "Desc", "goal": "Goal text"})
    assert body.startswith("## Summary\nDesc\n\n**Goal:**\nGoal text\n\n## Key Changes\n- Implement Desc\n\n")


def test_summary_omits_a_goal_equal_to_the_description():
    assert body_of({"description": "Same", "goal": "Same"}).startswith("## Summary\nSame\n\n## Key Changes\n")


def test_key_changes_from_numbered_and_bulleted_goal_lines():
    body = body_of({"title": "T", "goal": "- one\n* two\n3) three\n10. ten\nplain"})
    assert "## Key Changes\n- one\n- two\n- three\n- ten\n\n" in body


def test_key_changes_with_only_empty_bullets_lists_nothing():
    assert "## Key Changes\n\n## References" in body_of({"title": "T", "goal": "-\n1."})


@pytest.mark.parametrize(
    ("references", "line"),
    [
        (["a", " ", "b"], "References: a, b"),
        ("x, ,y", "References: x, y"),
        ([], "None"),
        ("", "None"),
        ([" "], "None"),
        (5, "None"),
    ],
)
def test_references(references, line):
    assert f"## References\n{line}\n\n" in body_of({"references": references})


@pytest.mark.parametrize(
    ("plan", "ev"),
    [
        ({"ev": 3}, "3.00"),
        ({"metrics": {"ev": 2.345}}, "2.35"),
        ({"ev": None, "metrics": {"ev": 1}}, "1.00"),
        ({"ev": "high"}, "high"),
        ({"ev": 0}, "0.00"),
        ({"metrics": "x"}, "N/A"),
        (None, "N/A"),
    ],
)
def test_expected_value_formatting(plan, ev):
    assert f"- **Expected Value (EV):** `{ev}`\n" in body_of({}, plan)


def test_plan_metadata_prefers_plan_agent_and_model_over_intent():
    body = body_of({"agent": "ia", "model": "im"}, {"plan_id": "P-1", "model": "pm"})
    assert body.endswith(
        "- **Plan ID:** `P-1`\n- **Expected Value (EV):** `N/A`\n- **Agent:** `ia`\n- **Model:** `pm`\n"
    )


def test_plan_metadata_names_the_supplied_runner():
    body = body_of(
        {},
        {"plan_id": "P-1795696276-CLA-CO5.5", "ev": 76.51},
        {"agent": "claude-agent", "model": "claude-opus-5-5"},
    )
    agent_line, model_line = runner_lines_of(body)
    assert agent_line == "- **Agent:** `claude-agent`"
    assert model_line == "- **Model:** `claude-opus-5-5`"
    assert "N/A" not in agent_line and "N/A" not in model_line
    assert "- **Plan ID:** `P-1795696276-CLA-CO5.5`" in body
    assert "- **Expected Value (EV):** `76.51`" in body


def test_plan_payload_agent_and_model_win_over_the_supplied_runner():
    body = body_of(
        {"agent": "ia", "model": "im"},
        {"plan_id": "P-1", "agent": "pa", "model": "pm"},
        {"agent": "ra", "model": "rm"},
    )
    assert runner_lines_of(body) == ("- **Agent:** `pa`", "- **Model:** `pm`")


@pytest.mark.parametrize(
    ("intent", "plan", "runner", "agent", "model"),
    [
        ({}, {"plan_id": "P-1"}, {"agent": "ra", "model": "rm"}, "ra", "rm"),
        ({"agent": "ia", "model": "im"}, {"plan_id": "P-1"}, None, "ia", "im"),
        ({"agent": "ia", "model": "im"}, {"agent": None, "model": ""}, {"agent": "ra", "model": "rm"}, "ra", "rm"),
        ({}, {"plan_id": "P-1"}, {}, "N/A", "N/A"),
        ({}, None, None, "N/A", "N/A"),
    ],
)
def test_agent_and_model_fall_back_through_runner_then_intent_then_na(intent, plan, runner, agent, model):
    assert runner_lines_of(body_of(intent, plan, runner)) == (f"- **Agent:** `{agent}`", f"- **Model:** `{model}`")
