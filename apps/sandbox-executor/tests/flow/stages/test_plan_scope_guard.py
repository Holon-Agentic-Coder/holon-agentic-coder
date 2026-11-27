from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sandbox_executor.flow import PipelineEngine, StageStatus, run_intent_stage, run_plan_stage
from sandbox_executor.flow.stages.plan_scope_guard import (
    PLAN_SCOPE_VIOLATION,
    _is_provenance_path,
    _out_of_scope_touch_set_paths,
    _plan_scope_failure_message,
)

from tests.flow.helpers import FakeRoleAgents, _dispatch_context


class ScopedPlanRoleAgents(FakeRoleAgents):
    def __init__(
        self,
        root: Path,
        origin: Path,
        plan_extra_markdown: str = "",
        plan_row_extra: dict[str, Any] | None = None,
    ):
        super().__init__(root, origin)
        self.plan_extra_markdown = plan_extra_markdown
        self.plan_row_extra = plan_row_extra or {}

    def _plan(self, work: Path, args: list[str], kwargs: dict[str, Any]) -> int:
        exit_code = super()._plan(work, args, kwargs)
        if not self.plan_extra_markdown:
            return exit_code
        plan_files = sorted((work / "plans").glob("P-*.md"))
        plan_path = plan_files[-1]
        plan_path.write_text(plan_path.read_text(encoding="utf-8") + "\n" + self.plan_extra_markdown, encoding="utf-8")
        self._commit(work, "plan: add touch set section")
        return exit_code

    def _recorded(self, work: Path, row: dict[str, Any], plan_content: str) -> dict[str, Any]:
        return {**super()._recorded(work, row, plan_content), **self.plan_row_extra}


def _plan_with_touch_set(*paths: str, extra_section: str = "") -> str:
    bullets = "\n".join(f"- `{path}`" for path in paths)
    body = f"# Plan\n\n## Touch Set\n\n{bullets}\n"
    if extra_section:
        body += f"\n{extra_section}\n"
    return body


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("plans/P-1.md", True),
        ("executions/E-1.md", True),
        ("holon-knowledge/ledger/plans.jsonl", True),
        ("intents/I-1.json", True),
        ("intents", True),
        ("./plans/nested/P-2.md", True),
        ("src/app.py", False),
        ("intent_helpers.py", False),
    ],
)
def test_is_provenance_path_matches_only_provenance_prefixes(path: str, expected: bool):
    assert _is_provenance_path(path) is expected


def test_in_scope_exact_dot_slash_and_new_markers_pass():
    intent = {"scope": ["src/app.py", "new: src/helper.py", "tests/test_app.py (new)"]}
    plan = _plan_with_touch_set("./src/app.py", "src/helper.py", "tests/test_app.py")
    assert _out_of_scope_touch_set_paths(intent, plan) == []


def test_provenance_paths_pass_even_when_absent_from_intent_scope():
    intent = {"scope": ["src/app.py"]}
    plan = _plan_with_touch_set(
        "src/app.py",
        "plans/P-1.md",
        "executions/E-1.md",
        "holon-knowledge/ledger/plans.jsonl",
        "intents/I-1.json",
    )
    assert _out_of_scope_touch_set_paths(intent, plan) == []


@pytest.mark.parametrize("broad_entry", ["src/", "src/*.py", "src/**"])
def test_broad_touch_set_entries_fail_against_narrow_intent_scope(broad_entry: str):
    intent = {"scope": ["src/app.py"]}
    plan = _plan_with_touch_set("src/app.py", broad_entry)
    assert _out_of_scope_touch_set_paths(intent, plan) == [broad_entry]


@pytest.mark.parametrize("intent_data", [None, {}, {"scope": []}, {"scope": ["   "]}])
def test_missing_or_empty_intent_scope_is_unconstrained(intent_data: dict[str, Any] | None):
    plan = _plan_with_touch_set("src/app.py", "src/other.py")
    assert _out_of_scope_touch_set_paths(intent_data, plan) == []


def test_explicit_expansions_on_intent_data_plan_row_and_markdown_allow_expanded_paths():
    plan = _plan_with_touch_set(
        "src/app.py",
        "src/from_intent_single.py",
        "src/from_intent_list.py",
        "src/from_plan_row.py",
        "src/from_markdown.py",
        "src/unrecorded.py",
        extra_section=("## Scope Expansion\n\n- `src/from_markdown.py` — needed for shared helper\n- none\n"),
    )
    intent = {
        "scope": ["src/app.py"],
        "scope_expansion": "src/from_intent_single.py",
        "scope_expansions": [{"path": "src/from_intent_list.py"}],
    }
    plan_row = {"scope_expansion": [{"paths": ["src/from_plan_row.py"]}]}
    assert _out_of_scope_touch_set_paths(intent, plan, plan_row) == ["src/unrecorded.py"]


def test_placeholder_and_fenced_scope_expansion_sections_grant_no_expansion():
    extra = "```markdown\n## Scope Expansion\n\n- `src/fenced.py`\n```\n\n## Scope Expansions\n\n- none\n- N/A\n"
    plan = _plan_with_touch_set("src/app.py", "src/fenced.py", extra_section=extra)
    assert _out_of_scope_touch_set_paths({"scope": ["src/app.py"]}, plan) == ["src/fenced.py"]


def test_out_of_scope_paths_in_plan_row_touch_set_are_caught_even_when_omitted_from_markdown():
    intent = {"scope": ["src/app.py"]}
    plan_row = {"touch_set": [{"path": "src/app.py"}, {"path": "src/hub.py"}, {"path": "intents/I-1.json"}]}
    assert _out_of_scope_touch_set_paths(intent, "# Plan without touch set\n", plan_row) == ["src/hub.py"]


def test_plan_scope_failure_message_names_branch_and_paths():
    msg = _plan_scope_failure_message("I-1/P-1/_", ["src/hub.py", "src/other.py"])
    assert "I-1/P-1/_" in msg
    assert "src/hub.py, src/other.py" in msg
    assert "scope expansion" in msg


def test_pipeline_fails_at_stage2_before_stage3_admission_on_unrecorded_out_of_scope_touch_set(flow_repos, tmp_path):
    host, origin = flow_repos
    agents = ScopedPlanRoleAgents(
        tmp_path,
        origin,
        plan_extra_markdown="## Touch Set\n\n- `src/app.py`\n- `src/unplanned_hub.py`\n",
    )
    ctx = _dispatch_context(
        host,
        agents,
        intent_data={
            "slug": "scope-guard-fail",
            "goal": "Update app only",
            "scope": ["src/app.py"],
            "labels": ["flow"],
        },
    )
    with patch("sandbox_executor.flow.admission.check_admission") as admission_check:
        results = PipelineEngine(ctx).run()

    plan_result = results["plan"]
    assert plan_result.status == StageStatus.FAILED
    assert plan_result.payload["failure_reason"] == PLAN_SCOPE_VIOLATION
    assert plan_result.payload["out_of_scope_touch_set"] == ["src/unplanned_hub.py"]
    assert ctx.plan_branch is None
    assert "execute" not in results
    assert agents.roles() == ["intent-creator", "planner"]
    admission_check.assert_not_called()


def test_run_plan_stage_passes_with_in_scope_provenance_and_recorded_expansion(flow_repos, tmp_path):
    host, origin = flow_repos
    agents = ScopedPlanRoleAgents(
        tmp_path,
        origin,
        plan_extra_markdown=(
            "## Touch Set\n\n"
            "- `./src/app.py`\n"
            "- `src/new_helper.py`\n"
            "- `src/expanded.py`\n"
            "- `plans/P-1.md`\n"
            "- `executions/E-1.md`\n"
            "- `holon-knowledge/ledger/plans.jsonl`\n"
            "- `intents/I-1.json`\n\n"
            "## Scope Expansion\n\n"
            "- `src/expanded.py` — required by adapter\n"
        ),
    )
    ctx = _dispatch_context(
        host,
        agents,
        intent_data={
            "slug": "scope-guard-pass",
            "goal": "Update app and helper",
            "scope": ["src/app.py", "new: src/new_helper.py"],
            "labels": ["flow"],
        },
    )
    assert run_intent_stage(ctx).status == StageStatus.SUCCESS
    result = run_plan_stage(ctx)

    assert result.status == StageStatus.SUCCESS, result.error
    assert ctx.plan_branch is not None


def test_run_plan_stage_fails_when_plan_row_touch_set_exceeds_intent_scope(flow_repos, tmp_path):
    host, origin = flow_repos
    agents = ScopedPlanRoleAgents(
        tmp_path,
        origin,
        plan_row_extra={"touch_set": [{"path": "src/app.py"}, {"path": "src/rogue.py"}]},
    )
    ctx = _dispatch_context(
        host,
        agents,
        intent_data={
            "slug": "scope-guard-row-fail",
            "goal": "Update app only",
            "scope": ["src/app.py"],
            "labels": ["flow"],
        },
    )
    assert run_intent_stage(ctx).status == StageStatus.SUCCESS
    result = run_plan_stage(ctx)

    assert result.status == StageStatus.FAILED
    assert result.payload["failure_reason"] == PLAN_SCOPE_VIOLATION
    assert result.payload["out_of_scope_touch_set"] == ["src/rogue.py"]
    assert ctx.plan_branch is None
