from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus, in_flight
from sandbox_executor.flow.admission import check_admission
from sandbox_executor.flow.checkpoint import save_checkpoint
from sandbox_executor.flow.in_flight import (
    MAX_OPEN_PULL_REQUESTS,
    AdmissionUnavailable,
    discover_in_flight_flows,
    local_blocked_waiters,
    local_in_flight_flows,
    open_flow_pull_requests,
    other_checkpoints,
    read_plan_touch_set,
)
from sandbox_executor.touch_set import TouchSet, TouchSetEntry

from tests.flow.helpers import OTHER_HEAD, OTHER_PLAN, FakeGh, _git, make_plan_branch, no_worktrees, seed_base_files

OWN_PLAN = "I-100-own/P-101-cccccccc-claude-agent-claude-opus-5-5/_"

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
    # why: a pull request is compared by its diff, never by its planned touch set
    assert flows[0].declared is None
    assert [call[:3] for call in gh.calls] == [["pr", "list", "--state"], ["pr", "diff", "7"]]


def test_pull_request_without_its_plan_branch_on_the_host_compares_only_its_diff(flow_repos, caplog):
    host, _origin = flow_repos
    gh = FakeGh([{"number": 7, "headRefName": OTHER_HEAD}], {7: ["src/app.py"]})
    flows = discover_in_flight_flows(str(host), OWN_HEAD, gh, lambda args, cwd: _ok("worktree " + cwd))
    assert not isinstance(flows, AdmissionUnavailable)
    assert flows[0].declared is None and flows[0].changed_files == ("src/app.py",)
    # why: its plan row is never read, so a missing plan branch warns of nothing
    assert caplog.records == []


def test_a_pull_requests_planned_touch_set_is_never_read(flow_repos, monkeypatch):
    host, _origin = flow_repos
    make_plan_branch(host, OTHER_PLAN, [{"path": "src/"}, {"path": "docs/"}])
    monkeypatch.setattr(in_flight, "read_plan_touch_set", lambda *args: pytest.fail("a plan row was read"))
    flows = open_flow_pull_requests(str(host), "I-100-own", FakeGh([{"number": 7, "headRefName": OTHER_HEAD}], {7: []}))
    assert not isinstance(flows, AdmissionUnavailable)
    assert [(flow.declared, flow.changed_files) for flow in flows] == [(None, ())]


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

    result = other_checkpoints(str(host), "I-100-own", failing_git)
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
    own_worktree = host
    write_checkpoint(own_worktree, OTHER_PLAN, executing, "this-worktree")

    checkpoints = other_checkpoints(str(host), "I-100-own")
    assert not isinstance(checkpoints, AdmissionUnavailable)
    flows = local_in_flight_flows(str(host), checkpoints)
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
    write_checkpoint(second, FOURTH_PLAN, executing, "older-flow", admission=None)

    checkpoints = other_checkpoints(str(host), "I-100-own")
    assert not isinstance(checkpoints, AdmissionUnavailable)
    flows = local_in_flight_flows(str(host), checkpoints)
    assert sorted(flow.plan_branch for flow in flows) == sorted([THIRD_PLAN, FOURTH_PLAN])
    assert {(flow.kind, flow.worktree) for flow in flows} == {("local_flow", str(second))}

    waiters = local_blocked_waiters(str(host), checkpoints)
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


DIFF_TOO_LARGE_STDERR = "gh: PullRequest.diff too_large: sorry, this diff is taking too long to generate (HTTP 406)"


class _FallbackGh:
    def __init__(
        self,
        prs: list[dict[str, Any]],
        diff_responses: dict[int, tuple[int, str, str]],
        api_responses: dict[int, tuple[int, Any, str]] | None = None,
    ) -> None:
        self.prs = prs
        self.diff_responses = diff_responses
        self.api_responses = api_responses or {}
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        if args[:2] == ["pr", "list"]:
            return subprocess.CompletedProcess(["gh", *args], 0, json.dumps(self.prs), "")
        if args[:2] == ["pr", "diff"]:
            code, out, err = self.diff_responses[int(args[2])]
            return subprocess.CompletedProcess(["gh", *args], code, out, err)
        if args[:1] == ["api"]:
            number = int(args[-1].split("/")[-2])
            code, payload, err = self.api_responses[number]
            stdout = payload if isinstance(payload, str) else json.dumps(payload)
            return subprocess.CompletedProcess(["gh", *args], code, stdout, err)
        raise AssertionError(f"unexpected gh invocation: {args}")


def test_diff_http_406_too_large_falls_back_to_paginated_rest_api(flow_repos):
    host, _origin = flow_repos
    gh = _FallbackGh(
        prs=[{"number": 7, "headRefName": OTHER_HEAD, "baseRefName": "main"}],
        diff_responses={7: (1, "", DIFF_TOO_LARGE_STDERR)},
        api_responses={7: (0, [[{"filename": "src/a.py"}, {"filename": "src/b.py"}]], "")},
    )
    flows = open_flow_pull_requests(str(host), "I-100-own", gh)
    assert not isinstance(flows, AdmissionUnavailable)
    assert [flow.changed_files for flow in flows] == [("src/a.py", "src/b.py")]
    assert gh.calls[1:] == [
        ["pr", "diff", "7", "--color", "never"],
        ["api", "--paginate", "--slurp", "repos/{owner}/{repo}/pulls/7/files"],
    ]


def test_fallback_merges_paginated_pages_normalises_and_deduplicates_in_order(flow_repos):
    host, _origin = flow_repos
    pages = [
        [{"filename": "./src/a.py"}, {"filename": r"src\b.py"}],
        [{"filename": "src/a.py"}, {"filename": "src/c.py"}],
        [{"filename": "./src/b.py"}, {"filename": "src/d.py"}],
    ]
    gh = _FallbackGh(
        prs=[{"number": 7, "headRefName": OTHER_HEAD}],
        diff_responses={7: (1, "", DIFF_TOO_LARGE_STDERR)},
        api_responses={7: (0, pages, "")},
    )
    flows = open_flow_pull_requests(str(host), "I-100-own", gh)
    assert not isinstance(flows, AdmissionUnavailable)
    assert flows[0].changed_files == ("src/a.py", "src/b.py", "src/c.py", "src/d.py")


def test_fallback_includes_rename_previous_filename_and_filters_metadata(flow_repos):
    host, _origin = flow_repos
    pages = [
        [
            {"filename": "./src/new_name.py", "previous_filename": r"src\old_name.py"},
            {"filename": "plans/P-201.md", "previous_filename": "plans/P-200.md"},
            {"filename": "src/from_meta.py", "previous_filename": "holon-knowledge/ledger/plans.jsonl"},
            {"filename": "holon-knowledge/ledger/executions.jsonl", "previous_filename": "src/to_meta.py"},
        ]
    ]
    gh = _FallbackGh(
        prs=[{"number": 7, "headRefName": OTHER_HEAD}],
        diff_responses={7: (1, "", DIFF_TOO_LARGE_STDERR)},
        api_responses={7: (0, pages, "")},
    )
    flows = open_flow_pull_requests(str(host), "I-100-own", gh)
    assert not isinstance(flows, AdmissionUnavailable)
    assert flows[0].changed_files == ("src/new_name.py", "src/old_name.py", "src/from_meta.py", "src/to_meta.py")


@pytest.mark.parametrize("empty_pages", [[], [[]]])
def test_fallback_empty_slurped_pages_returns_empty_tuple(flow_repos, empty_pages):
    host, _origin = flow_repos
    gh = _FallbackGh(
        prs=[{"number": 7, "headRefName": OTHER_HEAD}],
        diff_responses={7: (1, "", DIFF_TOO_LARGE_STDERR)},
        api_responses={7: (0, empty_pages, "")},
    )
    flows = open_flow_pull_requests(str(host), "I-100-own", gh)
    assert not isinstance(flows, AdmissionUnavailable)
    assert flows[0].changed_files == ()


@pytest.mark.parametrize("stderr", ["HTTP 502 Bad Gateway", "HTTP 404 Not Found", "PR #406 failed: HTTP 500"])
def test_non_406_diff_errors_fail_closed_without_calling_api(flow_repos, stderr):
    host, _origin = flow_repos
    gh = _FallbackGh(
        prs=[{"number": 7, "headRefName": OTHER_HEAD}],
        diff_responses={7: (1, "", stderr)},
    )
    result = open_flow_pull_requests(str(host), "I-100-own", gh)
    assert isinstance(result, AdmissionUnavailable)
    assert "gh pr diff 7" in result.reason
    assert not any(call[:1] == ["api"] for call in gh.calls)


def test_fallback_api_failure_fails_closed_and_redacts_secrets(flow_repos):
    host, _origin = flow_repos
    secret = "ghp_" + "a" * 36
    gh = _FallbackGh(
        prs=[{"number": 7, "headRefName": OTHER_HEAD}],
        diff_responses={7: (1, "", DIFF_TOO_LARGE_STDERR)},
        api_responses={7: (1, "", f"HTTP 500 token={secret}")},
    )
    result = open_flow_pull_requests(str(host), "I-100-own", gh)
    assert isinstance(result, AdmissionUnavailable)
    assert "gh api repos/{owner}/{repo}/pulls/7/files" in result.reason
    assert secret not in result.reason


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "   \n",
        "{bad",
        {"message": "Not Found"},
        [{"filename": "src/a.py"}],
        [["src/a.py"]],
        [[{"status": "modified"}]],
        [[{"filename": ""}]],
        [[{"filename": 123}]],
        [[{"filename": "src/a.py", "previous_filename": 42}]],
    ],
)
def test_fallback_malformed_or_unexpected_json_fails_closed(flow_repos, payload):
    host, _origin = flow_repos
    gh = _FallbackGh(
        prs=[{"number": 7, "headRefName": OTHER_HEAD}],
        diff_responses={7: (1, "", DIFF_TOO_LARGE_STDERR)},
        api_responses={7: (0, payload, "")},
    )
    result = open_flow_pull_requests(str(host), "I-100-own", gh)
    assert isinstance(result, AdmissionUnavailable)


@pytest.mark.parametrize(
    ("plan_branch", "touch_path", "expected_overlap"),
    [
        (OWN_PLAN, "src/page2.py", ("src/page2.py",)),
        ("I-100-own/P-102-cccccccc-claude-agent-claude-opus-5-5/_", "src/old_name.py", ("src/old_name.py",)),
        ("I-100-own/P-103-cccccccc-claude-agent-claude-opus-5-5/_", "src/disjoint.py", ()),
    ],
)
def test_stage3_admission_overlap_refusal_and_disjoint_admit_via_406_fallback(
    flow_repos, plan_branch, touch_path, expected_overlap
):
    host, _origin = flow_repos
    seed_base_files(host, ["src/page2.py", "src/old_name.py", "src/disjoint.py"])
    make_plan_branch(host, plan_branch, [{"path": touch_path}])
    gh = _FallbackGh(
        prs=[{"number": 7, "headRefName": OTHER_HEAD, "baseRefName": "main"}],
        diff_responses={7: (1, "", DIFF_TOO_LARGE_STDERR)},
        api_responses={
            7: (
                0,
                [
                    [{"filename": "src/new_name.py", "previous_filename": "src/old_name.py"}],
                    [{"filename": "src/page2.py"}],
                ],
                "",
            )
        },
    )
    context = FlowContext(
        target_worktree=str(host), plan_branch=plan_branch, intent_branch="I-100-own/_", token_reduce=False
    )
    decision = check_admission(context, gh, no_worktrees)
    if expected_overlap:
        assert not decision.admitted
        assert decision.reason is None
        assert [(c.label, c.files) for c in decision.conflicts] == [
            (f"pull request #7 (branch {OTHER_HEAD})", expected_overlap)
        ]
        assert [entry.files for entry in decision.blocked_by] == [expected_overlap]
    else:
        assert decision.admitted
        assert decision.conflicts == ()
        assert decision.blocked_by == ()
