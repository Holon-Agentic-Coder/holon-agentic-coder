"""Tests that later stages name their branch refs instead of relying on the operator's checkout."""

import os
import subprocess
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


class TestStageTargetsIgnoreOperatorHead:
    """Stages 1-3 no longer move the operator's checkout, so later stages must name their branch explicitly."""

    @pytest.fixture(autouse=True)
    def _no_gh(self):
        # Force the local git path so no test can reach GitHub over the network.
        with patch("shutil.which", return_value=None) as which:
            yield which

    @staticmethod
    def _run_stages_1_to_3(host: Path, origin: Path, tmp_path: Path) -> FlowContext:
        ctx = _dispatch_context(host, FakeRoleAgents(tmp_path, origin))
        results = PipelineEngine(ctx).run()
        assert results["execute"].status == StageStatus.SUCCESS
        assert _git(host, "rev-parse", "--abbrev-ref", "HEAD") == "main"
        return ctx

    @staticmethod
    def _run_loop_capturing_consensus(ctx: FlowContext, target: str) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        def _capture(**kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs["consensus_data"])
            return {"dual_posted": True}

        # Resolve the reviewed target with real git, so the reported commit is the one the reviewers' diff names.
        with (
            patch("sandbox_executor.flow.review.loop._resolve_reviewed_target", _REAL_RESOLVE_REVIEWED_TARGET),
            patch("sandbox_executor.flow.review.loop.post_pr_review_consensus", side_effect=_capture),
            patch("sandbox_executor.flow.review.loop.probe_ci_checks", return_value={"all_passed": True}),
            _verified_diff(),
        ):
            run_review_loop(ctx, target=target, max_iterations=1, auto_pr=False)
        return captured

    def test_review_target_commit_is_execution_tip_not_operator_head(self, flow_repos, tmp_path):
        host, origin = flow_repos
        ctx = self._run_stages_1_to_3(host, origin, tmp_path)
        exec_tip = _git(host, "rev-parse", f"refs/remotes/origin/{ctx.execution_branch}")
        assert exec_tip != _git(host, "rev-parse", "HEAD")

        consensus = self._run_loop_capturing_consensus(ctx, ctx.execution_branch)

        assert consensus["target_commit"] == exec_tip

    def test_review_target_commit_follows_origin_when_local_refs_are_stale(self, flow_repos, tmp_path):
        host, origin = flow_repos
        ctx = self._run_stages_1_to_3(host, origin, tmp_path)
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

    def test_review_stage_checks_out_execution_branch_and_probes_it(self, flow_repos, tmp_path):
        host, origin = flow_repos
        ctx = self._run_stages_1_to_3(host, origin, tmp_path)
        loop_res = {"consensus": {"approved": True}, "mergeability": {}, "halted_for_human": True}

        with (
            patch("sandbox_executor.flow.stages.review.check_and_resolve_stage_conflicts") as mock_probe,
            patch("sandbox_executor.flow.review.loop.run_review_loop", return_value=loop_res) as mock_loop,
        ):
            res = run_review_stage(ctx)

        assert res.status == StageStatus.HALTED_FOR_HUMAN
        # Stage 4 ledger rows and Stage 5 calibration run in the execution branch's working tree.
        assert _git(host, "rev-parse", "--abbrev-ref", "HEAD") == ctx.execution_branch
        assert mock_probe.call_args.kwargs["branch"] == ctx.execution_branch
        assert mock_loop.call_args.kwargs["target"] == ctx.execution_branch

    def test_review_stage_fails_when_execution_branch_cannot_be_checked_out(self, flow_repos, tmp_path):
        host, origin = flow_repos
        ctx = self._run_stages_1_to_3(host, origin, tmp_path)
        ctx.execution_branch = ctx.execution_branch.replace("/E-", "/E-missing-")

        with patch("sandbox_executor.flow.review.loop.run_review_loop") as mock_loop:
            res = run_review_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "could not check out" in (res.error or "")
        mock_loop.assert_not_called()
        assert _git(host, "rev-parse", "--abbrev-ref", "HEAD") == "main"

    def test_calibrate_stage_alone_checks_out_execution_branch_from_main(self, flow_repos, tmp_path):
        host, origin = flow_repos
        ctx = self._run_stages_1_to_3(host, origin, tmp_path)
        ctx.skip_push = True
        main_tip = _git(host, "rev-parse", "main")

        # '--skip-review' or a resume entering at Stage 5: the operator is still on 'main' and calibration throws,
        # so the fallback report must still be written into the execution branch's tree, never 'main'.
        heads: list[str] = []

        def _boom(**_kwargs: Any) -> None:
            heads.append(_git(host, "rev-parse", "--abbrev-ref", "HEAD"))
            raise RuntimeError("boom")

        with patch("sandbox_executor.flow.stages.calibrate.run_calibrate", side_effect=_boom):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert heads == [ctx.execution_branch]
        assert _git(host, "rev-parse", "--abbrev-ref", "HEAD") == ctx.execution_branch
        assert list((host / "plans").glob("P-*_calibration.md")), "fallback report missing from the execution tree"
        assert _git(host, "log", "-1", "--format=%H", "main") == main_tip

    def test_calibrate_stage_fails_cleanly_when_execution_branch_cannot_be_checked_out(self, flow_repos, tmp_path):
        host, origin = flow_repos
        ctx = self._run_stages_1_to_3(host, origin, tmp_path)
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
        host, origin = flow_repos
        ctx = _dispatch_context(host, FakeRoleAgents(tmp_path, origin))
        with patch_lookup_sites(
            "sandbox_executor.flow.stages.execute.check_and_resolve_stage_conflicts",
            "sandbox_executor.flow.stages.intent.check_and_resolve_stage_conflicts",
            "sandbox_executor.flow.stages.plan.check_and_resolve_stage_conflicts",
            wraps=check_and_resolve_stage_conflicts,
        ) as probe:
            results = PipelineEngine(ctx).run()
        assert results["execute"].status == StageStatus.SUCCESS
        branches = {c.kwargs["stage_name"]: c.kwargs.get("branch", "HEAD") for c in probe.call_args_list}
        assert branches == {"intent": None, "plan": ctx.intent_branch, "execute": ctx.plan_branch}

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
