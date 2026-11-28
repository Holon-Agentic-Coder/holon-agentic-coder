from __future__ import annotations

import functools
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus, admission
from sandbox_executor.flow.admission import AdmissionDecision, check_admission, format_refusal
from sandbox_executor.flow.checkpoint import save_checkpoint
from sandbox_executor.flow.in_flight_liveness import worktree_has_live_runner

from tests.flow.helpers import OTHER_HEAD, OTHER_PLAN, FakeGh, _git, make_plan_branch, no_worktrees, seed_base_files

OWN_PLAN = "I-100-own/P-101-cccccccc-claude-agent-claude-opus-5-5/_"
REAL_CHECK = check_admission


def _own_context(host: Path, **overrides) -> FlowContext:
    return FlowContext(
        target_worktree=str(host), plan_branch=OWN_PLAN, intent_branch="I-100-own/_", token_reduce=False, **overrides
    )


def _setup_own_plan(host: Path) -> None:
    seed_base_files(host, ["docs/guide.md"])
    make_plan_branch(host, OWN_PLAN, [{"path": "src/app.py"}, {"path": "docs/"}])
    _git(host, "push", "-q", "origin", OWN_PLAN)


def _save_other_checkpoint(
    worktree: Path,
    plan_branch: str,
    completed: list[FlowStage],
    slug: str,
    execution_branch: str | None = None,
) -> None:
    context = FlowContext(
        target_worktree=str(worktree),
        intent_data={"slug": slug},
        plan_branch=plan_branch,
        execution_branch=execution_branch,
    )
    for stage in completed:
        context.stage_results[stage.value] = StageResult(stage=stage, status=StageStatus.SUCCESS)
    save_checkpoint(context)


@pytest.mark.parametrize(
    "completed",
    [
        [FlowStage.INTENT, FlowStage.PLAN],
        [FlowStage.INTENT, FlowStage.PLAN, FlowStage.EXECUTE],
    ],
)
def test_merged_pr_with_stale_local_branch_and_worktree_does_not_block_after_prune(flow_repos, tmp_path, completed):
    host, origin = flow_repos
    _setup_own_plan(host)
    make_plan_branch(host, OTHER_PLAN, [{"path": "src/app.py"}, {"path": "docs/"}])
    _git(host, "branch", OTHER_HEAD, OTHER_PLAN)
    _git(host, "push", "-q", "origin", OTHER_PLAN, OTHER_HEAD)
    second = tmp_path / "second"
    _git(host, "worktree", "add", "-q", "--detach", str(second), "main")
    _save_other_checkpoint(second, OTHER_PLAN, completed, "merged-other", execution_branch=OTHER_HEAD)

    # why: delete remote branches directly on origin so host keeps stale remote-tracking refs until fetch --prune
    _git(origin, "branch", "-D", OTHER_HEAD)
    _git(origin, "branch", "-D", OTHER_PLAN)
    assert _git(host, "rev-parse", "--verify", f"refs/remotes/origin/{OTHER_HEAD}")

    gh = FakeGh([{"number": 7, "headRefName": OTHER_HEAD, "baseRefName": "main", "state": "MERGED"}], {})
    live_cmdlines = lambda: [f"holon flow --worktree {second}"]  # noqa: E731
    decision = check_admission(_own_context(host), gh, cmdlines=live_cmdlines)
    assert decision == AdmissionDecision(admitted=True)
    stale = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{OTHER_HEAD}"],
        cwd=host,
        capture_output=True,
        text=True,
        check=False,
    )
    assert stale.returncode != 0


def test_local_branch_and_worktree_without_origin_pr_or_live_runner_does_not_block(flow_repos, tmp_path):
    host, _origin = flow_repos
    _setup_own_plan(host)
    make_plan_branch(host, OTHER_PLAN, [{"path": "src/app.py"}, {"path": "docs/"}])
    second = tmp_path / "second"
    _git(host, "worktree", "add", "-q", "--detach", str(second), "main")
    _save_other_checkpoint(second, OTHER_PLAN, [FlowStage.INTENT, FlowStage.PLAN], "local-only")

    decision = check_admission(_own_context(host), FakeGh([], {}), cmdlines=lambda: [])
    assert decision == AdmissionDecision(admitted=True)

    with_runner = check_admission(
        _own_context(host), FakeGh([], {}), cmdlines=lambda: [f"holon flow --worktree {second}"]
    )
    assert with_runner == AdmissionDecision(admitted=True)


def test_open_pr_with_remote_branch_blocks_and_logs_provenance(flow_repos, monkeypatch):
    host, _origin = flow_repos
    _setup_own_plan(host)
    _git(host, "branch", OTHER_HEAD, "main")
    _git(host, "push", "-q", "origin", OTHER_HEAD)
    gh = FakeGh(
        [{"number": 42, "headRefName": OTHER_HEAD, "baseRefName": "main", "state": "OPEN"}],
        {42: ["src/app.py"]},
    )
    monkeypatch.setattr(admission, "check_admission", functools.partial(REAL_CHECK, run_gh=gh, run_git=no_worktrees))
    context = _own_context(host)
    result = admission.admission_stage_failure(context, "2026-11-27T00:00:00Z")
    assert result is not None and result.status == StageStatus.FAILED
    error = str(result.error)
    assert "#42" in error and "OPEN" in error
    assert f"refs/remotes/origin/{OTHER_HEAD}" in error and "remote" in error
    assert any(
        "source=remote" in line
        and f"ref=refs/remotes/origin/{OTHER_HEAD}" in line
        and "pr=#42" in line
        and "pr_state=OPEN" in line
        for line in context.logs
    )


def test_failing_git_fetch_prune_refuses_fail_closed_with_redacted_reason(flow_repos):
    host, _origin = flow_repos
    _setup_own_plan(host)
    secret = "ghp_" + "x" * 36

    def failing_fetch(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
        if args[:3] == ["fetch", "--prune", "origin"]:
            return subprocess.CompletedProcess(
                ["git", *args], 128, "", f"fatal: could not read from remote repository token={secret}"
            )
        return no_worktrees(args, cwd)

    decision = check_admission(_own_context(host), FakeGh([], {}), failing_fetch)
    assert not decision.admitted and not decision.overridden
    assert decision.reason is not None
    assert "git fetch --prune origin" in decision.reason
    assert "could not read from remote repository" in decision.reason
    assert secret not in decision.reason
    assert "git fetch --prune origin" in format_refusal(decision)


def test_live_runner_on_remote_branch_blocks_unless_pr_merged_or_closed(flow_repos, tmp_path, monkeypatch):
    host, _origin = flow_repos
    _setup_own_plan(host)
    make_plan_branch(host, OTHER_PLAN, [{"path": "docs/"}])
    _git(host, "branch", OTHER_HEAD, OTHER_PLAN)
    _git(host, "push", "-q", "origin", OTHER_PLAN, OTHER_HEAD)
    second = tmp_path / "second"
    _git(host, "worktree", "add", "-q", "--detach", str(second), "main")
    _save_other_checkpoint(second, OTHER_PLAN, [FlowStage.INTENT, FlowStage.PLAN], "live-other", OTHER_HEAD)
    live_cmdlines = lambda: [f"holon flow --worktree {second}"]  # noqa: E731

    monkeypatch.setattr(
        admission, "check_admission", functools.partial(REAL_CHECK, run_gh=FakeGh([], {}), cmdlines=live_cmdlines)
    )
    context = _own_context(host)
    result = admission.admission_stage_failure(context, "2026-11-27T00:00:00Z")
    assert result is not None and result.status == StageStatus.FAILED
    assert f"refs/remotes/origin/{OTHER_HEAD}" in str(result.error) and "remote" in str(result.error)
    assert any(
        "source=remote" in line and f"ref=refs/remotes/origin/{OTHER_HEAD}" in line and "pr=none" in line
        for line in context.logs
    )

    for state in ("MERGED", "CLOSED"):
        gh = FakeGh([{"number": 9, "headRefName": OTHER_HEAD, "baseRefName": "main", "state": state}], {})
        decision = REAL_CHECK(_own_context(host), gh, cmdlines=live_cmdlines)
        assert decision == AdmissionDecision(admitted=True)


def test_worktree_runner_detection_checks_path_boundaries():
    assert not worktree_has_live_runner("/tmp/wt", cmdlines=lambda: ["holon flow --worktree /tmp/wt-other"])
    assert not worktree_has_live_runner("/tmp/wt", cmdlines=lambda: ["holon flow --worktree /var/tmp/wt"])
    assert worktree_has_live_runner("/tmp/wt", cmdlines=lambda: ["holon flow --worktree /tmp/wt"])
    assert worktree_has_live_runner("/tmp/wt", cmdlines=lambda: ["holon flow --worktree=/tmp/wt/sub"])
