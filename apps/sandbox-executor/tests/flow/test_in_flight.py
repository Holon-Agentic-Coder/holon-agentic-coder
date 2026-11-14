"""Discovery of in-flight flows for Stage 3 admission (flow/in_flight.py): pull requests, checkpoints, lineage."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.checkpoint import save_checkpoint
from sandbox_executor.flow.in_flight import (
    AdmissionUnavailable,
    discover_in_flight_flows,
    local_in_flight_flows,
    open_flow_pull_requests,
    read_plan_touch_set,
)
from sandbox_executor.touch_set import TouchSet, TouchSetEntry

from tests.flow.helpers import _git

OTHER_PLAN = "I-200-other/P-201-aaaaaaaa-claude-agent-claude-opus-5-5/_"
OTHER_HEAD = "I-200-other/P-201-aaaaaaaa-claude-agent-claude-opus-5-5/E-202-bbbbbbbb-claude-agent-claude-opus-5-5/_"
OWN_HEAD = "I-100-own/P-101-cccccccc-claude-agent-claude-opus-5-5/E-102-dddddddd-claude-agent-claude-opus-5-5/_"


class FakeGh:
    """Answers ``gh pr list`` and ``gh pr diff`` from fixed data; ``fail`` makes every call exit 1."""

    def __init__(self, prs: list[dict[str, Any]], diffs: dict[int, list[str]], fail: bool = False):
        self.prs, self.diffs, self.fail = prs, diffs, fail
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        if self.fail:
            return subprocess.CompletedProcess(["gh", *args], 1, "", "HTTP 502")
        if args[:2] == ["pr", "list"]:
            return subprocess.CompletedProcess(["gh", *args], 0, json.dumps(self.prs), "")
        return subprocess.CompletedProcess(["gh", *args], 0, "\n".join(self.diffs[int(args[2])]), "")


def make_plan_branch(host: Path, plan_branch: str, touch_set: list[dict[str, Any]] | None) -> None:
    """Commit a plan branch whose plans.jsonl row records ``touch_set`` (omitted when None), then return to main."""
    plan_id = plan_branch.removesuffix("/_").rsplit("/", 1)[-1]
    _git(host, "checkout", "-q", "-b", plan_branch, "main")
    row: dict[str, Any] = {"plan_id": plan_id, "intent_branch": plan_branch.split("/", 1)[0] + "/_"}
    if touch_set is not None:
        row["touch_set"] = touch_set
    with open(host / "holon-knowledge/ledger/plans.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    _git(host, "commit", "-qam", f"plan: {plan_id}")
    _git(host, "checkout", "-q", "main")


def write_checkpoint(worktree: Path, plan_branch: str, completed: list[FlowStage], slug: str) -> None:
    context = FlowContext(target_worktree=str(worktree), intent_data={"slug": slug}, plan_branch=plan_branch)
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


def test_pull_request_without_readable_plan_row_compares_only_its_diff(flow_repos):
    host, _origin = flow_repos
    gh = FakeGh([{"number": 7, "headRefName": OTHER_HEAD}], {7: ["src/app.py"]})
    flows = discover_in_flight_flows(str(host), OWN_HEAD, gh, lambda args, cwd: _ok("worktree " + cwd))
    assert not isinstance(flows, AdmissionUnavailable)
    assert flows[0].declared is None and flows[0].changed_files == ("src/app.py",)


def test_gh_failure_returns_admission_unavailable(flow_repos):
    host, _origin = flow_repos
    result = discover_in_flight_flows(str(host), OWN_HEAD, FakeGh([], {}, fail=True))
    assert isinstance(result, AdmissionUnavailable)
    assert "gh pr list" in result.reason and "HTTP 502" in result.reason


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
    (second / ".holon" / "flow" / "checkpoint-broken.json").write_text("{not json", encoding="utf-8")
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
