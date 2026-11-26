"""Reproduce and verify the three Stage 2 kill windows and resume adoption of complete plan branches."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sandbox_executor.flow import (
    FlowContext,
    FlowStage,
    PipelineEngine,
    PlanCandidate,
    StageResult,
    StageStatus,
    adopt_existing_plan,
    plan_candidates,
    publish_planned_branches,
    record_plan_branch,
    run_intent_stage,
    run_plan_stage,
)
from sandbox_executor.flow.checkpoint import load_checkpoint
from sandbox_executor.naming import model_code, ticker_for

from tests.flow.helpers import (
    FakeRoleAgents,
    _append_jsonl,
    _dispatch_context,
    _git,
    _push_child_branch,
    stage4_halts_at_consensus,
)
from tests.flow.stage_templates import StageStart


def _plan_branches(repo: Path) -> list[str]:
    """List the plan branch names ('.../P-.../_', not execution branches) in ``repo``'s refs/heads."""
    raw = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    return sorted(
        name
        for line in raw.splitlines()
        if "/P-" in (name := line.strip()) and name.endswith("/_") and "/E-" not in name
    )


def _after_stage_1(
    stage_start: Callable[..., StageStart], checkpoint_path: Path | None = None
) -> tuple[Path, Path, FlowContext]:
    """The host, origin and context after the real Stage 1, from its stage-start template; these tests are about
    Stage 2 (test_dispatch.py's Stage 1 tests and test_stage_templates.py prove the template's Stage 1)."""
    start = stage_start("intent")
    if checkpoint_path is not None:
        start.ctx.checkpoint_path = str(checkpoint_path)
    return start.host, start.origin, start.ctx


@contextmanager
def _kill_stage2_at(attr: str) -> Iterator[None]:
    """Simulate a kill inside Stage 2 by raising KeyboardInterrupt when ``stages.plan.<attr>`` is called."""
    with patch(f"sandbox_executor.flow.stages.plan.{attr}", side_effect=KeyboardInterrupt("simulated kill")):
        yield


def _resume_from_plan(
    checkpoint_path: Path,
    tmp_path: Path,
    origin: Path,
    clock_offset: int = 5,
) -> tuple[FlowContext, FakeRoleAgents, dict[str, StageResult]]:
    """Load ``checkpoint_path``, attach a fresh FakeRoleAgents with ``clock_offset`` and run from Stage 2."""
    checkpoint = load_checkpoint(str(checkpoint_path))
    ctx = checkpoint.context
    agents = FakeRoleAgents(tmp_path, origin, clock_offset=clock_offset)
    ctx.dispatcher = agents
    with stage4_halts_at_consensus():
        results = PipelineEngine(ctx, from_stage=FlowStage.PLAN, checkpoint_path=str(checkpoint_path)).run()
    return ctx, agents, results


class _KillBeforePushAgents(FakeRoleAgents):
    """Fake planner that commits its plan branch locally and is killed before the host can push."""

    def _plan(self, work: Path, args: list[str], kwargs: dict[str, Any]) -> int:
        super()._plan(work, args, kwargs)
        raise KeyboardInterrupt("killed after planner commit, before host push")


class _KillInsidePlannerAgents(FakeRoleAgents):
    """Fake planner that creates its local plan branch and is killed before committing the plan or row."""

    def _plan(self, work: Path, args: list[str], _kwargs: dict[str, Any]) -> int:
        intent_branch, agent, model = args
        plan_id = f"P-{self.now()}-{ticker_for(agent)}-{model_code(model)}"
        plan_branch = f"{intent_branch.removesuffix('/_')}/{plan_id}/_"
        _git(work, "checkout", "-q", "-B", plan_branch, f"refs/remotes/origin/{intent_branch}")
        raise KeyboardInterrupt("killed inside planner before plan commit")


class TestKillWindowResume:
    """Acceptance criterion 1 & 2: each Stage 2 kill window resumes cleanly from plan without duplicate branches."""

    def test_w3_kill_after_push_resumes_and_adopts_pushed_plan_branch(self, flow_repos, tmp_path):
        host, origin = flow_repos
        cp_path = tmp_path / "checkpoint.json"
        first_agents = FakeRoleAgents(tmp_path, origin, clock_offset=0)
        first_ctx = _dispatch_context(host, first_agents)

        with (
            stage4_halts_at_consensus(),
            _kill_stage2_at("_fetch_branch_prefix_error"),
            pytest.raises(KeyboardInterrupt),
        ):
            PipelineEngine(first_ctx, checkpoint_path=str(cp_path)).run()

        origin_before = _plan_branches(origin)
        assert len(origin_before) == 1
        pushed_branch = origin_before[0]
        killed_cp = load_checkpoint(str(cp_path))
        assert killed_cp.current_stage == FlowStage.PLAN
        assert killed_cp.context.plan_branch == pushed_branch
        assert "plan" not in killed_cp.completed_stages

        resumed_ctx, resumed_agents, results = _resume_from_plan(cp_path, tmp_path, origin, clock_offset=10)

        assert results["plan"].status == StageStatus.SUCCESS, results["plan"].error
        assert _plan_branches(origin) == [pushed_branch]
        assert _plan_branches(host) == [pushed_branch]
        assert "planner" not in resumed_agents.roles()
        assert resumed_ctx.plan_branch == pushed_branch
        assert results["execute"].status == StageStatus.SUCCESS
        assert any(pushed_branch in line and "adopt" in line.lower() for line in resumed_ctx.logs)

    def test_w2_kill_before_push_resumes_publishes_and_adopts_local_plan_branch(self, flow_repos, tmp_path):
        host, origin = flow_repos
        cp_path = tmp_path / "checkpoint.json"
        first_agents = _KillBeforePushAgents(tmp_path, origin, clock_offset=0)
        first_ctx = _dispatch_context(host, first_agents)

        with stage4_halts_at_consensus(), pytest.raises(KeyboardInterrupt):
            PipelineEngine(first_ctx, checkpoint_path=str(cp_path)).run()

        local_before = _plan_branches(host)
        assert len(local_before) == 1
        local_branch = local_before[0]
        assert _plan_branches(origin) == []

        resumed_ctx, resumed_agents, results = _resume_from_plan(cp_path, tmp_path, origin, clock_offset=10)

        assert results["plan"].status == StageStatus.SUCCESS, results["plan"].error
        assert _plan_branches(origin) == [local_branch]
        assert _plan_branches(host) == [local_branch]
        assert "planner" not in resumed_agents.roles()
        assert resumed_ctx.plan_branch == local_branch
        assert results["execute"].status == StageStatus.SUCCESS
        assert any(local_branch in line and "adopt" in line.lower() for line in resumed_ctx.logs)

    def test_w1_kill_inside_planner_ignores_incomplete_local_branch_and_plans_afresh(self, flow_repos, tmp_path):
        host, origin = flow_repos
        cp_path = tmp_path / "checkpoint.json"
        first_agents = _KillInsidePlannerAgents(tmp_path, origin, clock_offset=0)
        first_ctx = _dispatch_context(host, first_agents)

        with stage4_halts_at_consensus(), pytest.raises(KeyboardInterrupt):
            PipelineEngine(first_ctx, checkpoint_path=str(cp_path)).run()

        local_before = _plan_branches(host)
        assert len(local_before) == 1
        stale_branch = local_before[0]
        assert _plan_branches(origin) == []

        resumed_ctx, resumed_agents, results = _resume_from_plan(cp_path, tmp_path, origin, clock_offset=10)

        assert resumed_agents.roles().count("planner") == 1
        assert results["plan"].status == StageStatus.SUCCESS, results["plan"].error
        assert results["execute"].status == StageStatus.SUCCESS, results["execute"].error
        origin_after = _plan_branches(origin)
        assert len(origin_after) == 1
        new_branch = origin_after[0]
        assert new_branch != stale_branch
        assert resumed_ctx.plan_branch == new_branch
        assert any(stale_branch in line and "ignored" in line.lower() for line in resumed_ctx.logs)


class TestPlanCandidatesAndAdoption:
    """Step 2 unit tests: candidate discovery, completeness checks, numeric timestamp ordering and adoption."""

    def test_branch_without_plans_jsonl_row_is_incomplete(self, stage_start, tmp_path):
        host, origin, ctx = _after_stage_1(stage_start)
        intent_prefix = ctx.intent_branch.removesuffix("/_")
        stale = _push_child_branch(tmp_path, origin, ctx.intent_branch, "P-1000-CLA-CO5.5")
        _git(host, "fetch", "-q", "origin")

        candidates = plan_candidates(str(host), intent_prefix)

        assert [c.branch for c in candidates] == [stale]
        assert candidates[0].published is True
        assert candidates[0].complete is False
        assert candidates[0].missing == "no plans.jsonl row"

    def test_branch_whose_row_names_an_absent_plan_file_is_incomplete(self, stage_start):
        host, _origin, ctx = _after_stage_1(stage_start)
        intent_prefix = ctx.intent_branch.removesuffix("/_")
        plan_id = "P-1000-CLA-CO5.5"
        plan_branch = f"{intent_prefix}/{plan_id}/_"
        _git(host, "checkout", "-q", "-B", plan_branch, f"refs/remotes/origin/{ctx.intent_branch}")
        _append_jsonl(
            host / "holon-knowledge/ledger/plans.jsonl",
            {"plan_id": plan_id, "intent_branch": ctx.intent_branch, "plan_file": f"plans/{plan_id}.md"},
        )
        _git(host, "add", "-A")
        _git(host, "commit", "-q", "-m", "row without plan file")

        candidates = plan_candidates(str(host), intent_prefix)

        assert len(candidates) == 1
        assert candidates[0] == PlanCandidate(
            branch=plan_branch,
            ref=f"refs/heads/{plan_branch}",
            published=False,
            missing="plan file not found",
        )
        assert candidates[0].complete is False

    def test_fake_planner_branch_is_complete_locally_and_when_pushed(self, stage_start):
        host, _origin, ctx = _after_stage_1(stage_start)
        agents = ctx.dispatcher
        intent_prefix = ctx.intent_branch.removesuffix("/_")
        agents("planner", ctx.agent, ctx.model, [ctx.intent_branch, ctx.agent, ctx.model], target_worktree=str(host))

        (local_cand,) = plan_candidates(str(host), intent_prefix)
        assert local_cand.complete is True
        assert local_cand.published is False
        assert local_cand.ref == f"refs/heads/{local_cand.branch}"

        _git(host, "push", "-q", "origin", local_cand.branch)
        (pushed_cand,) = plan_candidates(str(host), intent_prefix)
        assert pushed_cand.complete is True
        assert pushed_cand.published is True
        assert pushed_cand.ref == f"refs/remotes/origin/{local_cand.branch}"

    def test_candidates_order_by_numeric_timestamp_then_branch_name(self, stage_start):
        host, _origin, ctx = _after_stage_1(stage_start)
        intent_prefix = ctx.intent_branch.removesuffix("/_")
        for plan_id in ("P-1000-CLA-CO5.5", "P-999-ZZZ-CO5.5", "P-1000-ANA-G4A"):
            _git(host, "branch", f"{intent_prefix}/{plan_id}/_", f"refs/remotes/origin/{ctx.intent_branch}")

        candidates = plan_candidates(str(host), intent_prefix)

        assert [c.branch for c in candidates] == [
            f"{intent_prefix}/P-999-ZZZ-CO5.5/_",
            f"{intent_prefix}/P-1000-ANA-G4A/_",
            f"{intent_prefix}/P-1000-CLA-CO5.5/_",
        ]

    def test_adopt_existing_plan_returns_empty_with_no_or_only_incomplete_candidates_or_replan_feedback(
        self, stage_start, tmp_path
    ):
        host, origin, ctx = _after_stage_1(stage_start)
        agents = ctx.dispatcher
        intent_prefix = ctx.intent_branch.removesuffix("/_")

        assert adopt_existing_plan(ctx, intent_prefix) == ("", None)

        _push_child_branch(tmp_path, origin, ctx.intent_branch, "P-1000-CLA-CO5.5")
        _git(host, "fetch", "-q", "origin")
        assert adopt_existing_plan(ctx, intent_prefix) == ("", None)

        agents("planner", ctx.agent, ctx.model, [ctx.intent_branch, ctx.agent, ctx.model], target_worktree=str(host))
        ctx.intent_data["replan_feedback"] = "over budget: write a smaller plan"
        assert adopt_existing_plan(ctx, intent_prefix) == ("", None)

    def test_adopt_existing_plan_publishes_local_only_complete_branch_and_reports_push_errors(self, stage_start):
        host, origin, ctx = _after_stage_1(stage_start)
        agents = ctx.dispatcher
        intent_prefix = ctx.intent_branch.removesuffix("/_")
        agents("planner", ctx.agent, ctx.model, [ctx.intent_branch, ctx.agent, ctx.model], target_worktree=str(host))
        (local_branch,) = _plan_branches(host)

        _git(host, "config", "remote.origin.pushurl", "https://127.0.0.1:9/unreachable/repo.git")
        adopted, err = adopt_existing_plan(ctx, intent_prefix)
        assert adopted == ""
        assert err is not None and f"host push of '{local_branch}'" in err
        assert _plan_branches(origin) == []

        _git(host, "config", "--unset", "remote.origin.pushurl")
        adopted, err = adopt_existing_plan(ctx, intent_prefix)
        assert err is None
        assert adopted == local_branch
        assert _plan_branches(origin) == [local_branch]


class TestStage2AdoptionWiring:
    """Step 3 tests: two complete plan branches, no/incomplete branches, and failed push of local-only branch."""

    def test_two_complete_plan_branches_adopt_the_newest_without_redispatching(self, flow_repos, tmp_path):
        host, origin = flow_repos
        first_agents = FakeRoleAgents(tmp_path, origin, clock_offset=0)
        ctx = _dispatch_context(host, first_agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS
        first_branch = ctx.plan_branch
        assert first_branch is not None

        second_agents = FakeRoleAgents(tmp_path, origin, clock_offset=10)
        ctx.dispatcher = second_agents
        ctx.intent_data["replan_feedback"] = "re-plan requested"
        ctx.plan_branch = None
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS
        # A re-plan request dispatches the planner although a complete plan branch already exists.
        assert second_agents.roles() == ["planner"]
        second_branch = ctx.plan_branch
        assert second_branch is not None and second_branch != first_branch

        third_agents = FakeRoleAgents(tmp_path, origin, clock_offset=20)
        ctx.dispatcher = third_agents
        ctx.intent_data.pop("replan_feedback", None)
        ctx.logs.clear()
        res = run_plan_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert "planner" not in third_agents.roles()
        assert ctx.plan_branch == second_branch
        assert _plan_branches(origin) == sorted([first_branch, second_branch])
        assert any(first_branch in line and second_branch in line for line in ctx.logs)
        assert any(second_branch in line and "newest complete" in line for line in ctx.logs)

    def test_no_complete_plan_branch_dispatches_planner_and_succeeds(self, stage_start, tmp_path):
        _host, origin, ctx = _after_stage_1(stage_start)
        agents = ctx.dispatcher

        stale = _push_child_branch(tmp_path, origin, ctx.intent_branch, "P-1000-CLA-CO5.5")
        res = run_plan_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        # Stage 1 ran in the template; Stage 2 dispatched the planner once.
        assert agents.roles() == ["planner"]
        assert ctx.plan_branch is not None and ctx.plan_branch != stale
        assert any(stale in line and "ignored" in line for line in ctx.logs)

    def test_failed_push_of_local_only_complete_branch_fails_stage2_and_leaves_plan_branch_unset(self, stage_start):
        host, _origin, ctx = _after_stage_1(stage_start)
        agents = ctx.dispatcher
        agents("planner", ctx.agent, ctx.model, [ctx.intent_branch, ctx.agent, ctx.model], target_worktree=str(host))
        agents.calls.clear()
        (local_branch,) = _plan_branches(host)

        _git(host, "config", "remote.origin.pushurl", "https://127.0.0.1:9/unreachable/repo.git")
        ctx.plan_branch = None
        res = run_plan_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert f"host push of '{local_branch}'" in (res.error or "")
        assert ctx.plan_branch is None
        assert "planner" not in agents.roles()


class TestRecordPlanBranchBeforePush:
    """Step 4 tests: checkpoint records plan_branch before host push, restores live context and strips feedback."""

    def test_checkpoint_records_plan_branch_before_failed_push(self, stage_start, tmp_path):
        cp_path = tmp_path / "checkpoint.json"
        host, origin, ctx = _after_stage_1(stage_start, checkpoint_path=cp_path)
        _git(host, "config", "remote.origin.pushurl", "https://127.0.0.1:9/unreachable/repo.git")

        res = run_plan_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "host push of" in (res.error or "")
        assert _plan_branches(origin) == []
        (local_branch,) = _plan_branches(host)
        recorded_cp = load_checkpoint(str(cp_path))
        assert recorded_cp.current_stage == FlowStage.PLAN
        assert recorded_cp.context.plan_branch == local_branch
        assert "plan" not in recorded_cp.completed_stages
        assert ctx.plan_branch is None

    def test_save_error_fails_stage2_before_push_and_next_run_adopts_without_planner(self, stage_start, tmp_path):
        cp_path = tmp_path / "checkpoint.json"
        host, origin, ctx = _after_stage_1(stage_start, checkpoint_path=cp_path)
        agents = ctx.dispatcher

        with patch(
            "sandbox_executor.flow.stages.plan_resume.save_checkpoint",
            side_effect=OSError("disk full"),
        ):
            res = run_plan_stage(ctx)

        assert res.status == StageStatus.FAILED
        (local_branch,) = _plan_branches(host)
        assert str(cp_path) in (res.error or "")
        assert local_branch in (res.error or "")
        assert _plan_branches(origin) == []
        assert ctx.plan_branch is None

        agents.calls.clear()
        res_retry = run_plan_stage(ctx)
        assert res_retry.status == StageStatus.SUCCESS
        assert "planner" not in agents.roles()
        assert ctx.plan_branch == local_branch
        assert _plan_branches(origin) == [local_branch]

    def test_replan_record_never_persists_replan_feedback_and_restores_live_context(self, stage_start, tmp_path):
        cp_path = tmp_path / "checkpoint.json"
        _host, _origin, ctx = _after_stage_1(stage_start, checkpoint_path=cp_path)
        ctx.plan_branch = "I-1-old/P-1-CLA-CO5.5/_"
        ctx.stage_results["plan"] = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
        ctx.intent_data["replan_feedback"] = "over budget: shrink plan"
        new_branch = f"{ctx.intent_branch.removesuffix('/_')}/P-2-CLA-CO5.5/_"

        err = record_plan_branch(ctx, new_branch)

        assert err is None
        saved_cp = load_checkpoint(str(cp_path))
        assert saved_cp.current_stage == FlowStage.PLAN
        assert saved_cp.context.plan_branch == new_branch
        assert "plan" not in saved_cp.completed_stages
        assert "replan_feedback" not in saved_cp.context.intent_data
        # Live context was restored in full:
        assert ctx.plan_branch == "I-1-old/P-1-CLA-CO5.5/_"
        assert ctx.stage_results["plan"].status == StageStatus.SUCCESS
        assert ctx.intent_data["replan_feedback"] == "over budget: shrink plan"

    def test_successful_stage2_leaves_live_context_with_plan_branch_and_success_result(self, stage_start, tmp_path):
        cp_path = tmp_path / "checkpoint.json"
        _host, _origin, ctx = _after_stage_1(stage_start, checkpoint_path=cp_path)

        res = run_plan_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert ctx.plan_branch is not None
        assert ctx.stage_results["plan"].status == StageStatus.SUCCESS
        assert publish_planned_branches is not None
