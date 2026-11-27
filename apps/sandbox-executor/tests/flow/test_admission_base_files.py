from __future__ import annotations

import subprocess

from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.admission import AdmissionDecision, check_admission
from sandbox_executor.flow.base_files import base_branch_name, list_base_files
from sandbox_executor.flow.checkpoint import save_checkpoint
from sandbox_executor.flow.in_flight import AdmissionUnavailable
from sandbox_executor.git import run_git

from tests.flow.helpers import OTHER_PLAN, FakeGh, _git, make_plan_branch, no_worktrees, seed_base_files


def test_lists_the_origin_tracking_tree_without_metadata(flow_repos):
    host, _origin = flow_repos
    seed_base_files(host, ["docs/guide.md", "plans/P-1.md", "dir with space/a b.py"])
    # why: the local main lags, so only a listing of origin/main sees every seeded file.
    _git(host, "reset", "-q", "--hard", "HEAD~1")
    files = list_base_files(str(host), "main", no_worktrees)
    assert isinstance(files, frozenset)
    assert {"src/app.py", "docs/guide.md", "dir with space/a b.py", ".gitignore"} <= files
    assert not any(path.startswith(("plans/", "holon-knowledge/")) for path in files)


def test_an_unresolved_base_branch_fails_closed(flow_repos):
    host, _origin = flow_repos
    assert list_base_files(str(host), "release-9") == AdmissionUnavailable(
        "the base branch release-9 could not be resolved"
    )


def test_a_listing_failure_fails_closed_with_the_redacted_reason(flow_repos):
    host, _origin = flow_repos

    def failing_git(args, cwd):
        if args[:1] != ["ls-tree"]:
            return no_worktrees(args, cwd)
        return subprocess.CompletedProcess(["git", *args], 128, "", "fatal: bad object")

    result = list_base_files(str(host), "main", failing_git)
    assert isinstance(result, AdmissionUnavailable)
    assert result.reason == "'git ls-tree refs/remotes/origin/main' failed: fatal: bad object"


def test_the_base_branch_is_the_intents_target_branch():
    assert base_branch_name(FlowContext(token_reduce=False)) == "main"
    assert base_branch_name(FlowContext(token_reduce=False, intent_data={"target_branch": "release"})) == "release"


def test_the_base_ref_is_resolved_through_the_injected_runner(flow_repos):
    host, _origin = flow_repos
    calls: list[list[str]] = []

    def recording_git(args, cwd):
        calls.append(list(args))
        return no_worktrees(args, cwd)

    assert isinstance(list_base_files(str(host), "main", recording_git), frozenset)
    assert [call[0] for call in calls] == ["rev-parse", "ls-tree"]


def test_only_parked_local_flows_never_list_the_base_branch(flow_repos, tmp_path):
    host, _origin = flow_repos
    own_plan = "I-100-own/P-101-cccccccc-claude-agent-claude-opus-5-5/_"
    make_plan_branch(host, own_plan, [{"path": "src/app.py"}])
    make_plan_branch(host, OTHER_PLAN, [{"path": "src/"}])
    second = tmp_path / "second"
    _git(host, "worktree", "add", "-q", "--detach", str(second), "main")
    parked = FlowContext(target_worktree=str(second), intent_data={"slug": "parked"}, plan_branch=OTHER_PLAN)
    for stage in (FlowStage.INTENT, FlowStage.PLAN):
        parked.stage_results[stage.value] = StageResult(stage=stage, status=StageStatus.SUCCESS)
    save_checkpoint(parked)

    calls: list[list[str]] = []

    def recording_git(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        return run_git(args, cwd=cwd, check=False)

    context = FlowContext(
        target_worktree=str(host),
        plan_branch=own_plan,
        intent_branch="I-100-own/_",
        intent_data={"target_branch": "release-9"},
        token_reduce=False,
    )
    assert check_admission(context, FakeGh([], {}), recording_git) == AdmissionDecision(admitted=True)
    assert not any(call[:1] == ["ls-tree"] for call in calls)
