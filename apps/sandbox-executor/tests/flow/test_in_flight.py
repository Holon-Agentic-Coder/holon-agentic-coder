"""Discovery of in-flight flows for Stage 3 admission (flow/in_flight.py): pull requests, checkpoints, lineage."""

from __future__ import annotations

import subprocess
from pathlib import Path

from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.checkpoint import save_checkpoint
from sandbox_executor.flow.in_flight import (
    MAX_OPEN_PULL_REQUESTS,
    AdmissionUnavailable,
    discover_in_flight_flows,
    local_blocked_waiters,
    local_in_flight_flows,
    open_flow_pull_requests,
    read_plan_touch_set,
)
from sandbox_executor.touch_set import TouchSet, TouchSetEntry

from tests.flow.helpers import OTHER_HEAD, OTHER_PLAN, FakeGh, _git, make_plan_branch

OWN_HEAD = "I-100-own/P-101-cccccccc-claude-agent-claude-opus-5-5/E-102-dddddddd-claude-agent-claude-opus-5-5/_"
CHILD_PLAN = "I-100-own--1-child/P-103-eeeeeeee-claude-agent-claude-opus-5-5/_"
CHILD_HEAD = CHILD_PLAN.removesuffix("/_") + "/E-104-ffffffff-claude-agent-claude-opus-5-5/_"


def write_checkpoint(
    worktree: Path, plan_branch: str, completed: list[FlowStage], slug: str, admission: dict | None = None
) -> None:
    context = FlowContext(
        target_worktree=str(worktree), intent_data={"slug": slug}, plan_branch=plan_branch, admission=admission
    )
    for stage in completed:
        context.stage_results[stage.value] = StageResult(stage=stage, status=StageStatus.SUCCESS)
    save_checkpoint(context)


def test_read_plan_touch_set(flow_repos):
    host, _origin = flow_repos
    make_plan_branch(host, OTHER_PLAN, [{"path": "src/"}])
    assert read_plan_touch_set(str(host), OTHER_PLAN) == TouchSet((TouchSetEntry("src/"),))
    assert read_plan_touch_set(str(host), "I-9-x/P-9-missing/_") is None


def test_pull_requests_keep_flow_heads_outside_own_lineage(flow_repos):
    host, _origin = flow_repos
    make_plan_branch(host, OTHER_PLAN, [{"path": "src/app.py"}])
    gh = FakeGh(
        prs=[
            {"number": 7, "headRefName": OTHER_HEAD, "baseRefName": "main"},
            {"number": 8, "headRefName": "feature/not-a-flow", "baseRefName": "main"},
            {"number": 9, "headRefName": OWN_HEAD, "baseRefName": "main"},
            {"number": 10, "headRefName": CHILD_HEAD, "baseRefName": "main"},
        ],
        diffs={7: ["src/app.py", "plans/P-201.md"]},
    )
    flows = open_flow_pull_requests(str(host), "I-100-own", gh)
    assert not isinstance(flows, AdmissionUnavailable)
    assert [flow.label for flow in flows] == [f"pull request #7 (branch {OTHER_HEAD})"]
    assert flows[0].plan_branch == OTHER_PLAN
    assert flows[0].changed_files == ("src/app.py",)
    assert flows[0].declared == TouchSet((TouchSetEntry("src/app.py"),))
    assert [call[:3] for call in gh.calls] == [["pr", "list", "--state"], ["pr", "diff", "7"]]


def test_pull_request_without_readable_plan_row_compares_only_its_diff(flow_repos, caplog):
    host, _origin = flow_repos
    gh = FakeGh([{"number": 7, "headRefName": OTHER_HEAD}], {7: ["src/app.py"]})
    flows = discover_in_flight_flows(str(host), OWN_HEAD, gh, lambda args, cwd: _ok("worktree " + cwd))
    assert not isinstance(flows, AdmissionUnavailable)
    assert flows[0].declared is None and flows[0].changed_files == ("src/app.py",)
    assert any(OTHER_PLAN in r.getMessage() and "only its diff" in r.getMessage() for r in caplog.records)


def test_gh_failure_returns_admission_unavailable(flow_repos):
    host, _origin = flow_repos
    result = discover_in_flight_flows(str(host), OWN_HEAD, FakeGh([], {}, fail=True))
    assert isinstance(result, AdmissionUnavailable)
    assert "gh pr list" in result.reason and "HTTP 502" in result.reason


def test_gh_non_list_json_returns_admission_unavailable(flow_repos):
    host, _origin = flow_repos
    gh = FakeGh({"message": "rate limited"}, {})  # type: ignore[arg-type]
    result = open_flow_pull_requests(str(host), "I-100-own", gh)
    assert isinstance(result, AdmissionUnavailable) and "not a list" in result.reason


def test_more_open_pull_requests_than_the_limit_fail_closed(flow_repos):
    host, _origin = flow_repos
    prs = [{"number": n, "headRefName": OTHER_HEAD} for n in range(1, MAX_OPEN_PULL_REQUESTS + 2)]
    result = open_flow_pull_requests(str(host), "I-100-own", FakeGh(prs, {}))
    assert isinstance(result, AdmissionUnavailable)
    assert f"more than {MAX_OPEN_PULL_REQUESTS} open flow pull requests" in result.reason


def test_non_flow_pull_requests_do_not_count_toward_the_limit(flow_repos):
    host, _origin = flow_repos
    prs = [{"number": n, "headRefName": f"feature/{n}"} for n in range(1, MAX_OPEN_PULL_REQUESTS + 2)]
    gh = FakeGh([*prs, {"number": 999, "headRefName": OTHER_HEAD}], {999: ["src/x.py"]})
    result = open_flow_pull_requests(str(host), "I-100-own", gh)
    assert not isinstance(result, AdmissionUnavailable)
    assert [flow.changed_files for flow in result] == [("src/x.py",)]


def test_git_failure_returns_admission_unavailable(flow_repos):
    host, _origin = flow_repos

    def failing_git(args, cwd):
        return subprocess.CompletedProcess(["git", *args], 128, "", "fatal: not a git repository")

    result = local_in_flight_flows(str(host), "I-100-own", failing_git)
    assert isinstance(result, AdmissionUnavailable) and "git worktree list" in result.reason


def _ok(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["git"], 0, stdout, "")


def test_local_checkpoints_keep_live_flows_only(flow_repos, tmp_path):
    host, _origin = flow_repos
    make_plan_branch(host, OTHER_PLAN, [{"path": "src/"}])
    second = tmp_path / "second"
    _git(host, "worktree", "add", "-q", "--detach", str(second), "main")
    executing = [FlowStage.INTENT, FlowStage.PLAN]
    write_checkpoint(second, OTHER_PLAN, executing, "live")
    write_checkpoint(second, "I-300-gone/P-301-eeeeeeee-x-y/_", executing, "deleted-plan")
    write_checkpoint(second, OTHER_PLAN, [FlowStage.INTENT], "still-planning")
    write_checkpoint(second, "I-100-own/P-101-cccccccc-claude-agent-claude-opus-5-5/_", executing, "own")
    make_plan_branch(host, CHILD_PLAN, [{"path": "src/"}])
    write_checkpoint(second, CHILD_PLAN, executing, "own-child")
    (second / ".holon" / "flow" / "checkpoint-broken.json").write_text("{not json", encoding="utf-8")
    (second / ".holon" / "flow" / "checkpoint-list.json").write_text("[]", encoding="utf-8")
    write_checkpoint(host, OTHER_PLAN, executing, "this-worktree")  # this run's own worktree is never read

    flows = local_in_flight_flows(str(host), "I-100-own")
    assert not isinstance(flows, AdmissionUnavailable)
    assert [(flow.plan_branch, flow.label) for flow in flows] == [
        (OTHER_PLAN, f"local flow on branch {OTHER_PLAN} (worktree {second})")
    ]
    assert flows[0].declared == TouchSet((TouchSetEntry("src/"),)) and flows[0].changed_files is None


def test_local_flow_with_an_open_pull_request_is_listed_once(flow_repos, tmp_path):
    host, _origin = flow_repos
    make_plan_branch(host, OTHER_PLAN, [{"path": "src/"}])
    second = tmp_path / "second"
    _git(host, "worktree", "add", "-q", "--detach", str(second), "main")
    write_checkpoint(second, OTHER_PLAN, [FlowStage.INTENT, FlowStage.PLAN, FlowStage.EXECUTE], "reviewing")
    gh = FakeGh([{"number": 7, "headRefName": OTHER_HEAD}], {7: ["src/app.py"]})
    flows = discover_in_flight_flows(str(host), OWN_HEAD, gh)
    assert not isinstance(flows, AdmissionUnavailable)
    assert [flow.label for flow in flows] == [f"pull request #7 (branch {OTHER_HEAD})"]


def test_parent_and_child_intents_share_one_lineage(flow_repos):
    host, _origin = flow_repos
    gh = FakeGh([{"number": 9, "headRefName": OWN_HEAD}, {"number": 10, "headRefName": CHILD_HEAD}], {})
    no_worktrees = lambda args, cwd: _ok("worktree " + cwd)  # noqa: E731
    assert discover_in_flight_flows(str(host), OWN_HEAD, gh, no_worktrees) == []
    assert discover_in_flight_flows(str(host), CHILD_HEAD, gh, no_worktrees) == []


BLOCKED = {
    "state": "blocked",
    "blocked_since": "2026-11-20T01:00:00.000Z",
    "blocked_by": [{"kind": "pull_request", "number": 9, "branch": "b", "files": ["src/app.py"]}],
}
THIRD_PLAN = "I-300-third/P-301-eeeeeeee-claude-agent-claude-opus-5-5/_"
FOURTH_PLAN = "I-400-fourth/P-401-ffffffff-claude-agent-claude-opus-5-5/_"


def test_a_blocked_checkpoint_is_not_in_flight_but_is_a_waiter(flow_repos, tmp_path):
    host, _origin = flow_repos
    for plan in (OTHER_PLAN, THIRD_PLAN, FOURTH_PLAN):
        make_plan_branch(host, plan, [{"path": "src/"}])
    second = tmp_path / "second"
    _git(host, "worktree", "add", "-q", "--detach", str(second), "main")
    executing = [FlowStage.INTENT, FlowStage.PLAN]
    write_checkpoint(second, OTHER_PLAN, executing, "blocked", BLOCKED)
    write_checkpoint(second, THIRD_PLAN, executing, "admitted", {"state": "admitted"})
    write_checkpoint(second, FOURTH_PLAN, executing, "older-flow")  # no admission field: still in flight

    flows = local_in_flight_flows(str(host), "I-100-own")
    assert not isinstance(flows, AdmissionUnavailable)
    assert sorted(flow.plan_branch for flow in flows) == sorted([THIRD_PLAN, FOURTH_PLAN])
    assert {(flow.kind, flow.worktree) for flow in flows} == {("local_flow", str(second))}

    waiters = local_blocked_waiters(str(host), "I-100-own")
    assert not isinstance(waiters, AdmissionUnavailable)
    assert [(w.plan_branch, w.worktree, w.record.blocked_since) for w in waiters] == [
        (OTHER_PLAN, str(second), "2026-11-20T01:00:00.000Z")
    ]
    assert waiters[0].declared == TouchSet((TouchSetEntry("src/"),))


def test_a_blocked_flow_with_an_open_pull_request_stays_in_flight(flow_repos, tmp_path):
    host, _origin = flow_repos
    make_plan_branch(host, OTHER_PLAN, [{"path": "src/"}])
    second = tmp_path / "second"
    _git(host, "worktree", "add", "-q", "--detach", str(second), "main")
    write_checkpoint(second, OTHER_PLAN, [FlowStage.INTENT, FlowStage.PLAN], "blocked", BLOCKED)
    gh = FakeGh([{"number": 7, "headRefName": OTHER_HEAD}], {7: ["src/app.py"]})
    flows = discover_in_flight_flows(str(host), OWN_HEAD, gh)
    assert not isinstance(flows, AdmissionUnavailable)
    assert [(flow.kind, flow.number, flow.head) for flow in flows] == [("pull_request", 7, OTHER_HEAD)]
