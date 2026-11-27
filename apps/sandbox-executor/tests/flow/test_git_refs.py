import json
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
    _dispatch_context,
    _git,
    _verified_diff,
    stage4_halts_at_consensus,
)
from tests.flow.lookup_patch import patch_lookup_sites
from tests.flow.stage_templates import (
    StageStart,
    _point_consult_at,
    _replace_template_paths_in_flow_state,
    _with_template_paths_replaced,
)
from tests.repo_templates import copy_template, shared_template


def _fast_import_inline_file(path: str, content: str) -> str:
    payload = content.encode("utf-8")
    return f"M 100644 inline {path}\ndata {len(payload)}\n{content}"


def _fast_import_commit(
    ref: str,
    message: str,
    files: dict[str, str],
    *,
    parent: str | None = None,
    mark: int | None = None,
) -> str:
    msg_bytes = message.encode("utf-8")
    lines = [f"commit {ref}"]
    if mark is not None:
        lines.append(f"mark :{mark}")
    lines.append("committer Holon Test <test@holon.invalid> 1700000000 +0000")
    lines.append(f"data {len(msg_bytes)}\n{message}")
    if parent is not None:
        lines.append(f"from {parent}")
    for rel, text in files.items():
        lines.append(_fast_import_inline_file(rel, text))
    return "\n".join(lines) + "\n"


def _push_fix_directly_to_origin(origin: Path, branch: str) -> str:
    stream = _fast_import_commit(
        f"refs/heads/{branch}",
        "fix",
        {"src/fix.py": "print('fix')\n"},
        parent=f"refs/heads/{branch}^0",
    )
    subprocess.run(["git", "--git-dir", str(origin), "fast-import", "--quiet"], input=stream, text=True, check=True)
    return _git(origin, "rev-parse", f"refs/heads/{branch}")


def _build_mergeability_template(root: Path) -> None:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    stream = "".join(
        [
            _fast_import_commit("refs/heads/main", "base", {"shared.txt": "base line\n"}, mark=1),
            _fast_import_commit(
                "refs/heads/clean-feature", "clean feature", {"feature.txt": "feature only\n"}, parent=":1"
            ),
            _fast_import_commit(
                "refs/heads/conflict-feature", "conflicting feature", {"shared.txt": "feature change\n"}, parent=":1"
            ),
            _fast_import_commit("refs/heads/main", "main change", {"shared.txt": "main change\n"}, parent=":1"),
        ]
    )
    subprocess.run(["git", "fast-import", "--quiet"], cwd=repo, input=stream, text=True, check=True)
    _git(repo, "checkout", "-q", "-f", "main")


def _build_stage3_on_main(root: Path, stage_start: Callable[..., StageStart]) -> dict[str, Any]:
    start = stage_start("execute", target=root)
    # why: the executor container leaves the worktree on its execution branch; the operator's checkout is main
    _git(start.host, "checkout", "-q", "main")
    return {"root": str(root), "context": start.ctx.to_dict()}


@pytest.fixture
def stage3_on_main(
    tmp_path_factory: pytest.TempPathFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage_start: Callable[..., StageStart],
) -> tuple[Path, Path, FlowContext]:
    template, built = shared_template(
        tmp_path_factory, "git-refs-stage3-main", lambda root: _build_stage3_on_main(root, stage_start)
    )
    names = tuple(entry for entry in ("origin.git", "host", "home") if (template / entry).exists())
    copy_template(template, tmp_path, names=names)
    host, origin = tmp_path / "host", tmp_path / "origin.git"
    _replace_template_paths_in_flow_state(host / ".holon", built["root"], tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _point_consult_at(monkeypatch, host)
    ctx = FlowContext.from_dict(
        json.loads(_with_template_paths_replaced(json.dumps(built["context"]), built["root"], tmp_path))
    )
    ctx.target_worktree = str(host)
    ctx.dispatcher = FakeRoleAgents(tmp_path, origin)
    return host, origin, ctx


# why: Stages 1-3 no longer move the operator's checkout, so later stages must name their branch explicitly
class TestStageTargetsIgnoreOperatorHead:
    @pytest.fixture(autouse=True)
    def _no_gh(self):
        # why: forcing the local git path keeps every test off GitHub and the network
        with patch("shutil.which", return_value=None) as which:
            yield which

    @staticmethod
    def _run_loop_capturing_consensus(ctx: FlowContext, target: str) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        def _capture(**kwargs: Any) -> str:
            captured.update(kwargs["consensus_data"])
            return "report"

        # why: the CI summary is scripted as the source-less green status the adapter cannot produce, because these
        # why: tests are about the reviewed commit, not CI evidence
        github = FakeGitHub(checks=ci_checks_payload(passed=1))
        resolve_reviewed_target_with_real_git = patch(
            "sandbox_executor.flow.review.loop._resolve_reviewed_target", _REAL_RESOLVE_REVIEWED_TARGET
        )
        with (
            resolve_reviewed_target_with_real_git,
            patch("sandbox_executor.flow.github.review_post.build_pr_review_consensus_report", side_effect=_capture),
            patch("sandbox_executor.flow.github.ci.summarise_ci_checks", return_value={"all_passed": True}),
            _verified_diff(),
        ):
            # why: the scripted summary never registers as GitHub CI, so waiting for registration would only stall
            run_review_loop(
                ctx, target=target, max_iterations=1, auto_pr=False, ci_registration_timeout=0, github=github
            )
        assert github.methods().count("post_review") == 1
        return captured

    def test_review_target_commit_is_execution_tip_not_operator_head(self, stage3_on_main):
        host, _origin, ctx = stage3_on_main
        exec_tip = _git(host, "rev-parse", f"refs/remotes/origin/{ctx.execution_branch}")
        assert exec_tip != _git(host, "rev-parse", "HEAD")

        consensus = self._run_loop_capturing_consensus(ctx, ctx.execution_branch)

        assert consensus["target_commit"] == exec_tip

    def test_review_target_commit_follows_origin_when_local_refs_are_stale(self, stage3_on_main):
        host, origin, ctx = stage3_on_main
        stale = _git(host, "rev-parse", f"refs/remotes/origin/{ctx.execution_branch}")
        new_tip = _push_fix_directly_to_origin(origin, ctx.execution_branch)
        assert _git(host, "rev-parse", f"refs/remotes/origin/{ctx.execution_branch}") == stale

        consensus = self._run_loop_capturing_consensus(ctx, ctx.execution_branch)

        assert consensus["target_commit"] == new_tip

    def test_head_target_still_reads_the_checkout(self, flow_repos):
        host, _origin = flow_repos
        ctx = FlowContext(repo_dir=str(host), dry_run=False)

        consensus = self._run_loop_capturing_consensus(ctx, "HEAD")

        assert consensus["target_commit"] == _git(host, "rev-parse", "HEAD")

    def test_review_stage_checks_out_execution_branch_and_probes_it(self, stage3_on_main):
        host, _origin, ctx = stage3_on_main
        loop_res = {"consensus": {"approved": True}, "mergeability": {}, "halted_for_human": False, "approved": True}

        from sandbox_executor.flow.context import FlowStage
        from sandbox_executor.flow.target_sync_result import SyncBoundary

        with (
            patch("sandbox_executor.flow.stages.review.at_stage_boundary", return_value=None) as mock_probe,
            patch("sandbox_executor.flow.review.loop.run_review_loop", return_value=loop_res) as mock_loop,
        ):
            res = run_review_stage(ctx)

        assert res.status == StageStatus.APPROVED
        # why: Stage 4 ledger rows and Stage 5 calibration write to the execution branch's working tree
        assert _git(host, "rev-parse", "--abbrev-ref", "HEAD") == ctx.execution_branch
        mock_probe.assert_called_once_with(ctx, FlowStage.REVIEW, SyncBoundary.STAGE_START)
        assert mock_loop.call_args.kwargs["target"] == ctx.execution_branch

    def test_review_stage_fails_when_execution_branch_cannot_be_checked_out(self, stage3_on_main):
        host, _origin, ctx = stage3_on_main
        ctx.execution_branch = ctx.execution_branch.replace("/E-", "/E-missing-")

        with patch("sandbox_executor.flow.review.loop.run_review_loop") as mock_loop:
            res = run_review_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "could not check out" in (res.error or "")
        mock_loop.assert_not_called()
        assert _git(host, "rev-parse", "--abbrev-ref", "HEAD") == "main"

    def test_calibrate_stage_alone_checks_out_execution_branch_from_main(self, stage3_on_main):
        host, _origin, ctx = stage3_on_main
        ctx.skip_push = True
        main_tip = _git(host, "rev-parse", "main")

        # why: a resume entering at Stage 5 finds the operator still on 'main'; a throwing calibration must still
        # why: have run on the execution branch, never 'main', and the failed stage must write no report
        heads: list[str] = []

        def _boom(**_kwargs: Any) -> None:
            heads.append(_git(host, "rev-parse", "--abbrev-ref", "HEAD"))
            raise RuntimeError("boom")

        with patch("sandbox_executor.flow.stages.calibrate.run_calibrate", side_effect=_boom):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert heads == [ctx.execution_branch]
        # why: the failure row is an untracked pending fragment, so nothing blocks restoring the starting branch
        assert _git(host, "rev-parse", "--abbrev-ref", "HEAD") == "main"
        assert not list((host / "plans").rglob("P-*_calibration.md")), "a failed calibration must write no report"
        assert _git(host, "log", "-1", "--format=%H", "main") == main_tip

    def test_calibrate_stage_fails_cleanly_when_execution_branch_cannot_be_checked_out(self, stage3_on_main):
        host, _origin, ctx = stage3_on_main
        ctx.execution_branch = ctx.execution_branch.replace("/E-", "/E-missing-")
        status_before = _git(host, "status", "--porcelain")

        with patch("sandbox_executor.flow.stages.calibrate.run_calibrate") as mock_calibrate:
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "could not check out" in (res.error or "")
        mock_calibrate.assert_not_called()
        assert _git(host, "rev-parse", "--abbrev-ref", "HEAD") == "main"
        assert _git(host, "status", "--porcelain") == status_before

    def test_conflict_probe_inspects_named_branch_not_head(self, tmp_path_factory, tmp_path):
        template, _ = shared_template(tmp_path_factory, "git-refs-mergeability", _build_mergeability_template)
        copy_template(template, tmp_path, names=("repo",))
        repo = tmp_path / "repo"
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

        with (
            stage4_halts_at_consensus(),
            patch_lookup_sites(
                "sandbox_executor.flow.stages.execute.at_stage_boundary",
                "sandbox_executor.flow.stages.intent.at_stage_boundary",
                "sandbox_executor.flow.stages.plan.at_stage_boundary",
                side_effect=_record_boundary,
            ),
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


# why: the ledger worktree module imports no git runner, so a patch of git_refs.run_git sees every command
def test_the_ledger_step_runs_its_whole_worktree_lifecycle_through_the_git_refs_runner(
    flow_repos, tmp_path, monkeypatch
):
    host, origin = flow_repos
    branch = "I-1-x/P-2-y/E-3-z/_"
    main_sha = _git(host, "rev-parse", "main")
    _git(origin, "update-ref", f"refs/heads/{branch}", main_sha)
    _git(host, "update-ref", f"refs/remotes/origin/{branch}", main_sha)
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

    sweep_then_own_worktree_then_cleanup = ["prune", "list", "add", "remove", "prune"]
    assert worktree_commands == sweep_then_own_worktree_then_cleanup
