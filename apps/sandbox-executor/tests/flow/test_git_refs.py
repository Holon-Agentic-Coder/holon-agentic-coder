"""Tests that later stages name their branch refs instead of relying on the operator's checkout."""

import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sandbox_executor.flow import (
    ConflictResolutionError,
    FlowContext,
    PipelineEngine,
    StageStatus,
    check_and_resolve_stage_conflicts,
    run_calibrate_stage,
    run_intent_stage,
    run_review_loop,
    run_review_stage,
)
from sandbox_executor.flow.git_refs import _append_ledger_row
from sandbox_executor.git import run_git as real_run_git

from tests.flow.github.fake_github import FakeGitHub, ci_checks_payload
from tests.flow.helpers import (
    _REAL_RESOLVE_REVIEWED_TARGET,
    FakeRoleAgents,
    _configure_identity,
    _dispatch_context,
    _git,
    _init_mergeability_repo,
    _verified_diff,
)
from tests.flow.lookup_patch import patch_lookup_sites
from tests.flow.stage_templates import StageStart


class TestStageTargetsIgnoreOperatorHead:
    """Stages 1-3 no longer move the operator's checkout, so later stages must name their branch explicitly."""

    @pytest.fixture(autouse=True)
    def _no_gh(self):
        # Force the local git path so no test can reach GitHub over the network.
        with patch("shutil.which", return_value=None) as which:
            yield which

    @staticmethod
    def _after_stages_1_to_3(stage_start: Callable[..., StageStart]) -> tuple[Path, Path, FlowContext]:
        """The host, origin and context after the real Stages 1 to 3, copied from their stage-start template.

        These tests are about Stages 4 and 5; test_pipeline_dispatches_intent_plan_execute_with_resolved_runner proves
        the run of Stages 1 to 3 itself, and test_stage_templates.py that the template equals a fresh run.
        """
        start = stage_start("execute")
        # The executor container leaves the worktree on its execution branch; the operator's checkout is main.
        _git(start.host, "checkout", "-q", "main")
        return start.host, start.origin, start.ctx

    @staticmethod
    def _run_loop_capturing_consensus(ctx: FlowContext, target: str) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        def _capture(**kwargs: Any) -> str:
            captured.update(kwargs["consensus_data"])
            return "report"

        # No PR view, so mergeability stays on local git; the post lands on the fake. The CI summary stays the
        # source-less green status this test always used (the adapter cannot produce it, so the probe's summary is
        # scripted, not the loop's probe): the test is about the reviewed commit, not CI evidence.
        github = FakeGitHub(checks=ci_checks_payload(passed=1))
        # Resolve the reviewed target with real git, so the reported commit is the one the reviewers' diff names.
        with (
            patch("sandbox_executor.flow.review.loop._resolve_reviewed_target", _REAL_RESOLVE_REVIEWED_TARGET),
            patch("sandbox_executor.flow.github.review_post.build_pr_review_consensus_report", side_effect=_capture),
            patch("sandbox_executor.flow.github.ci.summarise_ci_checks", return_value={"all_passed": True}),
            _verified_diff(),
        ):
            # No CI registration wait (as when gh was absent): the scripted summary never registers as GitHub CI.
            run_review_loop(
                ctx, target=target, max_iterations=1, auto_pr=False, ci_registration_timeout=0, github=github
            )
        assert github.methods().count("post_review") == 1
        return captured

    def test_review_target_commit_is_execution_tip_not_operator_head(self, stage_start):
        host, _origin, ctx = self._after_stages_1_to_3(stage_start)
        exec_tip = _git(host, "rev-parse", f"refs/remotes/origin/{ctx.execution_branch}")
        assert exec_tip != _git(host, "rev-parse", "HEAD")

        consensus = self._run_loop_capturing_consensus(ctx, ctx.execution_branch)

        assert consensus["target_commit"] == exec_tip

    def test_review_target_commit_follows_origin_when_local_refs_are_stale(self, stage_start, tmp_path):
        host, origin, ctx = self._after_stages_1_to_3(stage_start)
        stale = _git(host, "rev-parse", f"refs/remotes/origin/{ctx.execution_branch}")
        # A resolver pushes a fix from elsewhere; the operator's local and tracking refs are now stale.
        work = tmp_path / "resolver-clone"
        subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
        _configure_identity(work)
        _git(work, "checkout", "-q", ctx.execution_branch)
        (work / "src" / "fix.py").write_text("print('fix')\n", encoding="utf-8")
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", "fix")
        _git(work, "push", "-q", "origin", ctx.execution_branch)
        new_tip = _git(work, "rev-parse", "HEAD")
        assert _git(host, "rev-parse", f"refs/remotes/origin/{ctx.execution_branch}") == stale

        consensus = self._run_loop_capturing_consensus(ctx, ctx.execution_branch)

        assert consensus["target_commit"] == new_tip

    def test_head_target_still_reads_the_checkout(self, flow_repos):
        host, _origin = flow_repos
        ctx = FlowContext(repo_dir=str(host), dry_run=False)

        consensus = self._run_loop_capturing_consensus(ctx, "HEAD")

        assert consensus["target_commit"] == _git(host, "rev-parse", "HEAD")

    def test_review_stage_checks_out_execution_branch_and_probes_it(self, stage_start):
        host, _origin, ctx = self._after_stages_1_to_3(stage_start)
        loop_res = {"consensus": {"approved": True}, "mergeability": {}, "halted_for_human": False, "approved": True}

        from sandbox_executor.flow.context import FlowStage
        from sandbox_executor.flow.target_sync_result import SyncBoundary

        with (
            patch("sandbox_executor.flow.stages.review.at_stage_boundary", return_value=None) as mock_probe,
            patch("sandbox_executor.flow.review.loop.run_review_loop", return_value=loop_res) as mock_loop,
        ):
            res = run_review_stage(ctx)

        assert res.status == StageStatus.APPROVED
        # Stage 4 ledger rows and Stage 5 calibration run in the execution branch's working tree.
        assert _git(host, "rev-parse", "--abbrev-ref", "HEAD") == ctx.execution_branch
        mock_probe.assert_called_once_with(ctx, FlowStage.REVIEW, SyncBoundary.STAGE_START)
        assert mock_loop.call_args.kwargs["target"] == ctx.execution_branch

    def test_review_stage_fails_when_execution_branch_cannot_be_checked_out(self, stage_start):
        host, _origin, ctx = self._after_stages_1_to_3(stage_start)
        ctx.execution_branch = ctx.execution_branch.replace("/E-", "/E-missing-")

        with patch("sandbox_executor.flow.review.loop.run_review_loop") as mock_loop:
            res = run_review_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "could not check out" in (res.error or "")
        mock_loop.assert_not_called()
        assert _git(host, "rev-parse", "--abbrev-ref", "HEAD") == "main"

    def test_calibrate_stage_alone_checks_out_execution_branch_from_main(self, stage_start):
        host, _origin, ctx = self._after_stages_1_to_3(stage_start)
        ctx.skip_push = True
        main_tip = _git(host, "rev-parse", "main")

        # A resume entering at Stage 5: the operator is still on 'main' and calibration throws. Calibration must
        # still run on the execution branch, never 'main', and the failed stage writes no report.
        heads: list[str] = []

        def _boom(**_kwargs: Any) -> None:
            heads.append(_git(host, "rev-parse", "--abbrev-ref", "HEAD"))
            raise RuntimeError("boom")

        with patch("sandbox_executor.flow.stages.calibrate.run_calibrate", side_effect=_boom):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert heads == [ctx.execution_branch]
        # The failure row is an untracked pending fragment, so nothing blocks restoring the starting branch.
        assert _git(host, "rev-parse", "--abbrev-ref", "HEAD") == "main"
        assert not list((host / "plans").rglob("P-*_calibration.md")), "a failed calibration must write no report"
        assert _git(host, "log", "-1", "--format=%H", "main") == main_tip

    def test_calibrate_stage_fails_cleanly_when_execution_branch_cannot_be_checked_out(self, stage_start):
        host, _origin, ctx = self._after_stages_1_to_3(stage_start)
        ctx.execution_branch = ctx.execution_branch.replace("/E-", "/E-missing-")
        status_before = _git(host, "status", "--porcelain")

        with patch("sandbox_executor.flow.stages.calibrate.run_calibrate") as mock_calibrate:
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "could not check out" in (res.error or "")
        mock_calibrate.assert_not_called()
        assert _git(host, "rev-parse", "--abbrev-ref", "HEAD") == "main"
        assert _git(host, "status", "--porcelain") == status_before

    def test_conflict_probe_inspects_named_branch_not_head(self, tmp_path):
        repo = _init_mergeability_repo(tmp_path / "repo")
        assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"

        assert check_and_resolve_stage_conflicts(str(repo), target_branch="main")["status"] == "clean"
        with pytest.raises(ConflictResolutionError, match=r"shared\.txt"):
            check_and_resolve_stage_conflicts(str(repo), target_branch="main", branch="conflict-feature")
        clean = check_and_resolve_stage_conflicts(str(repo), target_branch="main", branch="clean-feature")
        assert clean["status"] == "clean"
        assert _git(repo, "status", "--porcelain") == ""

    def test_stage_conflict_probes_name_each_stage_branch(self, flow_repos, tmp_path):
        from sandbox_executor.flow.target_sync_hooks import active_flow_branch, at_stage_boundary
        from sandbox_executor.flow.target_sync_result import SyncBoundary

        host, origin = flow_repos
        ctx = _dispatch_context(host, FakeRoleAgents(tmp_path, origin))
        branches: dict[str, str] = {}

        def _record_boundary(context, stage, boundary):
            if boundary is SyncBoundary.STAGE_START:
                branches[stage.value] = active_flow_branch(context, stage)
            return at_stage_boundary(context, stage, boundary)

        with patch_lookup_sites(
            "sandbox_executor.flow.stages.execute.at_stage_boundary",
            "sandbox_executor.flow.stages.intent.at_stage_boundary",
            "sandbox_executor.flow.stages.plan.at_stage_boundary",
            side_effect=_record_boundary,
        ):
            results = PipelineEngine(ctx).run()
        assert results["execute"].status == StageStatus.SUCCESS
        assert branches == {"intent": ctx.intent_branch, "plan": ctx.intent_branch, "execute": ctx.plan_branch}

    def test_intent_temp_file_is_world_readable_for_the_container_user(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        modes: list[int] = []

        def _record(role: str, runner: str, model: str, positional_args: list[str], **kwargs: Any) -> int:
            if kwargs.get("intent_file"):
                modes.append(os.stat(kwargs["intent_file"]).st_mode & 0o777)
            return agents(role, runner, model, positional_args, **kwargs)

        ctx = _dispatch_context(host, agents, dispatcher=_record)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert modes == [0o644]


def test_the_ledger_step_runs_its_whole_worktree_lifecycle_through_the_git_refs_runner(
    flow_repos, tmp_path, monkeypatch
):
    """The ledger worktree module imports no git runner, so a patch of ``git_refs.run_git`` sees every command."""
    host, _origin = flow_repos
    branch = "I-1-x/P-2-y/E-3-z/_"
    _git(host, "push", "-q", "origin", f"main:refs/heads/{branch}")
    _git(host, "fetch", "-q", "origin")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    worktree_commands: list[str] = []

    def recording_git(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if args[:1] == ["worktree"]:
            worktree_commands.append(args[1])
        return real_run_git(args, **kwargs)

    monkeypatch.setattr("sandbox_executor.flow.git_refs.run_git", recording_git)
    row = {"entry_type": "summary", "execution_id": "E-3-z", "plan_branch": "I-1-x/P-2-y/_", "status": "success"}
    context = FlowContext(repo_dir=str(host), token_reduce=False)

    assert _append_ledger_row(context, branch, "executions.jsonl", row, "execute: record success row") is None

    # Sweep (prune, list), the step's own worktree (add), then cleanup (remove, and the prune after the delete).
    assert worktree_commands == ["prune", "list", "add", "remove", "prune"]
