"""Stage 2 after a failed planner: the planner's API error reaches the stage error and no plan file is left behind."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sandbox_executor.flow import StageStatus, run_intent_stage, run_plan_stage
from sandbox_executor.plan_attempt import PLAN_FAILURE_DETAIL_REL

from tests.flow.helpers import FakeRoleAgents, _dispatch_context, _git

API_ERROR_LINE = (
    'API Error: 400 {"type":"error","error":{"type":"invalid_request_error","message":"system.2.cache_control.ttl: '
    "a ttl='1h' cache_control block must not come after a ttl='5m' cache_control block\"}}"
)


class _ApiErrorPlanner(FakeRoleAgents):
    """A planner container killed after an API error: its plan file and failure detail stay in the mounted worktree."""

    def __init__(self, root: Path, origin: Path, *, writes_detail: bool = True) -> None:
        super().__init__(root, origin)
        self.writes_detail = writes_detail

    def _plan(self, work: Path, args: list[str], _kwargs: dict[str, Any]) -> int:
        intent_branch = args[0]
        plan_file = work / "plans" / intent_branch.removesuffix("/_") / "P-9-claude-agent-CO5.5.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text(API_ERROR_LINE + "\n", encoding="utf-8")
        if self.writes_detail:
            detail = work / PLAN_FAILURE_DETAIL_REL
            detail.parent.mkdir(parents=True, exist_ok=True)
            detail.write_text(f"the planner agent's API call failed: {API_ERROR_LINE}", encoding="utf-8")
        return 1


def _untracked_plans(host: Path) -> str:
    return _git(host, "status", "--porcelain", "--untracked-files=all", "--", "plans/")


def _run_failed_plan(host: Path, agents: FakeRoleAgents):
    ctx = _dispatch_context(host, agents)
    assert run_intent_stage(ctx).status == StageStatus.SUCCESS
    return ctx, run_plan_stage(ctx)


def test_an_api_error_fails_the_plan_stage_with_its_text_and_leaves_no_plan_file(flow_repos, tmp_path):
    host, origin = flow_repos
    _ctx, result = _run_failed_plan(host, _ApiErrorPlanner(tmp_path, origin))

    assert result.status == StageStatus.FAILED
    error = result.error or ""
    assert "cache_control.ttl" in error
    assert "planner agent exited with code 1" in error
    assert "missing or non-numeric overall metrics" not in error
    assert _untracked_plans(host) == ""
    assert not (host / PLAN_FAILURE_DETAIL_REL).exists()


def test_a_failed_planner_without_detail_keeps_the_no_plan_error(flow_repos, tmp_path):
    host, origin = flow_repos
    ctx, result = _run_failed_plan(host, _ApiErrorPlanner(tmp_path, origin, writes_detail=False))

    assert result.status == StageStatus.FAILED
    assert (result.error or "").startswith("Plan stage failed: no planner agent produced plans/P-*.md")
    assert ctx.intent_branch in (result.error or "")
    assert _untracked_plans(host) == ""


def test_an_untracked_plan_file_present_before_the_dispatch_survives(flow_repos, tmp_path):
    host, origin = flow_repos
    agents = _ApiErrorPlanner(tmp_path, origin)
    ctx = _dispatch_context(host, agents)
    assert run_intent_stage(ctx).status == StageStatus.SUCCESS
    scratch = host / "plans" / ctx.intent_branch.removesuffix("/_") / "P-1-human-draft.md"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text("# A human's draft\n", encoding="utf-8")

    result = run_plan_stage(ctx)

    assert result.status == StageStatus.FAILED
    assert scratch.exists()
    assert _untracked_plans(host).splitlines() == [f"?? {scratch.relative_to(host).as_posix()}"]
