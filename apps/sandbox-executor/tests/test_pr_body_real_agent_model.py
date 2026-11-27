from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus, run_review_loop
from sandbox_executor.flow.review.ensemble import _semantic_pr_text
from sandbox_executor.flow.stages.plan_support import plan_metrics, plan_stage_payload

from tests.flow.github.fake_github import FakeGitHub, merge_view
from tests.flow.helpers import _verified_diff
from tests.review_fakes import FakeEnsemble, install_fake_reviewers

PLAN_BRANCH = "I-1795742095-pr-body-real-agent-model/P-1795742153-CLA-CO5.5/_"
EXECUTION_BRANCH = "I-1795742095-pr-body-real-agent-model/P-1795742153-CLA-CO5.5/E-1795742362-CLA-CO5.5/_"
PLAN_REL_PATH = "plans/I-1795742095-pr-body-real-agent-model/P-1795742153-CLA-CO5.5.md"
PLACEHOLDER_RUNNER = {"agent": "N/A", "model": "N/A"}
RUNNER_AGENT_LINE = "- **Agent:** `claude-agent`"
RUNNER_MODEL_LINE = "- **Model:** `claude-opus-5-5`"

PLAN_TEXT = """# Plan for I-1795742095-pr-body-real-agent-model

- **Plan ID:** P-1795742153-CLA-CO5.5
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.86  |
| entropy_pred        | 3.6   |
| impact_pred         | 55.0  |
| cost_pred           | 9.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 38.72 |
"""


def _write_plan_file(tmp_path: Path) -> Path:
    plan_path = tmp_path / PLAN_REL_PATH
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(PLAN_TEXT, encoding="utf-8")
    return plan_path


def _context_with_plan_from_file(
    tmp_path: Path,
    *,
    agent: str = "claude-agent",
    model: str = "claude-opus-5-5",
    intent: dict[str, Any] | None = None,
    payload_overrides: dict[str, Any] | None = None,
) -> FlowContext:
    plan_text = _write_plan_file(tmp_path).read_text(encoding="utf-8")
    ctx = FlowContext(
        repo_dir=str(tmp_path), execution_branch=EXECUTION_BRANCH, agent=agent, model=model, dry_run=False
    )
    ctx.intent_data = {"slug": "pr-body-real-agent-model", **(intent or {})}
    metrics = plan_metrics(str(tmp_path), plan_text, {})
    payload = plan_stage_payload(ctx, PLAN_BRANCH, PLAN_REL_PATH, metrics, [])
    payload.update(payload_overrides or {})
    ctx.stage_results["plan"] = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS, payload=payload)
    return ctx


def _metadata_line(body: str, label: str) -> str:
    return next(line for line in body.splitlines() if line.startswith(f"- **{label}:**"))


def _rendered_runner_lines(ctx: FlowContext) -> tuple[str, str]:
    _, body = _semantic_pr_text(ctx)
    return _metadata_line(body, "Agent"), _metadata_line(body, "Model")


def test_body_from_a_real_plan_file_names_the_planning_runner(tmp_path):
    ctx = _context_with_plan_from_file(tmp_path)
    _, body = _semantic_pr_text(ctx)
    assert _metadata_line(body, "Agent") == RUNNER_AGENT_LINE
    assert _metadata_line(body, "Model") == RUNNER_MODEL_LINE
    assert _metadata_line(body, "Plan ID") == "- **Plan ID:** `P-1795742153-CLA-CO5.5`"
    assert _metadata_line(body, "Expected Value (EV)") == "- **Expected Value (EV):** `38.72`"


def test_placeholder_plan_payload_does_not_hide_the_runner(tmp_path):
    ctx = _context_with_plan_from_file(tmp_path, payload_overrides=PLACEHOLDER_RUNNER)
    assert _rendered_runner_lines(ctx) == (RUNNER_AGENT_LINE, RUNNER_MODEL_LINE)


@pytest.mark.parametrize("placeholder", ["n/a", "", "  ", None, "unknown", "None"])
def test_placeholder_variants_in_the_plan_payload_do_not_hide_the_runner(tmp_path, placeholder):
    ctx = _context_with_plan_from_file(tmp_path, payload_overrides={"agent": placeholder, "model": placeholder})
    assert _rendered_runner_lines(ctx) == (RUNNER_AGENT_LINE, RUNNER_MODEL_LINE)


def test_real_plan_payload_values_win_over_the_runner(tmp_path):
    ctx = _context_with_plan_from_file(tmp_path, payload_overrides={"agent": "plan-agent", "model": "plan-model"})
    assert _rendered_runner_lines(ctx) == ("- **Agent:** `plan-agent`", "- **Model:** `plan-model`")


def test_intent_supplies_the_value_when_plan_and_runner_lack_it(tmp_path):
    intent = {"agent": "intent-agent", "model": "intent-model"}
    ctx = _context_with_plan_from_file(
        tmp_path, agent="", model="", intent=intent, payload_overrides=PLACEHOLDER_RUNNER
    )
    assert _rendered_runner_lines(ctx) == ("- **Agent:** `intent-agent`", "- **Model:** `intent-model`")


@pytest.mark.parametrize("intent", [{}, {"agent": "N/A", "model": "n/a"}])
def test_na_only_when_plan_runner_and_intent_all_lack_the_value(tmp_path, intent):
    ctx = _context_with_plan_from_file(
        tmp_path, agent="", model="N/A", intent=intent, payload_overrides=PLACEHOLDER_RUNNER
    )
    assert _rendered_runner_lines(ctx) == ("- **Agent:** `N/A`", "- **Model:** `N/A`")


def _open_or_update_calls(github: FakeGitHub) -> list[tuple[str, tuple]]:
    return [(name, args) for name, args in github.calls if name == "open_or_update"]


def _bodies_written_by_review_loop(ctx: FlowContext, github: FakeGitHub, monkeypatch) -> list[str]:
    install_fake_reviewers(monkeypatch, FakeEnsemble())
    ctx.stage_results["execute"] = StageResult(
        stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"test_pass_rate": 1.0, "exit_code": 0}
    )
    with patch("shutil.which", return_value=None), _verified_diff():
        run_review_loop(
            ctx,
            auto_pr=True,
            ensemble_size=1,
            max_iterations=1,
            ci_registration_timeout=0,
            ci_wait_budget=0,
            sleep=lambda _seconds: None,
            github=github,
        )
    return [body for name, (branch, _base, _title, body) in _open_or_update_calls(github) if branch == EXECUTION_BRANCH]


@pytest.mark.parametrize("open_prs", [{}, {EXECUTION_BRANCH: 7}], ids=["create", "edit"])
def test_review_loop_create_and_edit_write_the_runner(tmp_path, monkeypatch, open_prs):
    ctx = _context_with_plan_from_file(tmp_path, payload_overrides=PLACEHOLDER_RUNNER)
    github = FakeGitHub(prs=open_prs, standing_view=merge_view())
    bodies = _bodies_written_by_review_loop(ctx, github, monkeypatch)
    assert bodies
    for body in bodies:
        assert _metadata_line(body, "Agent") == RUNNER_AGENT_LINE
        assert _metadata_line(body, "Model") == RUNNER_MODEL_LINE
