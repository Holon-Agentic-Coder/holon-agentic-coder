"""Reproduce and verify the three Stage 2 kill windows and resume adoption of complete plan branches."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sandbox_executor.flow import (
    FlowContext,
    FlowStage,
    PipelineEngine,
    StageResult,
    StageStatus,
    run_intent_stage,
    run_plan_stage,
)
from sandbox_executor.flow.checkpoint import load_checkpoint
from sandbox_executor.naming import model_code, ticker_for

from tests.flow.helpers import (
    FakeRoleAgents,
    _dispatch_context,
    _git,
    stage4_halts_at_consensus,
)


def _plan_branches(repo: Path) -> list[str]:
    """List the plan branch names ('.../P-.../_', not execution branches) in ``repo``'s refs/heads."""
    raw = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    return sorted(
        name
        for line in raw.splitlines()
        if "/P-" in (name := line.strip()) and name.endswith("/_") and "/E-" not in name
    )


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
