from __future__ import annotations

import json
from pathlib import Path

import pytest
from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus, admission_state
from sandbox_executor.flow.admission import check_admission
from sandbox_executor.flow.admission_order import blocker_is_clear, older_ready_waiter
from sandbox_executor.flow.admission_state import AdmissionRecord, BlockedBy
from sandbox_executor.flow.checkpoint import (
    load_checkpoint,
    save_admission,
    save_checkpoint,
)
from sandbox_executor.flow.checkpoint_discovery import get_default_checkpoint_path
from sandbox_executor.flow.in_flight import BlockedWaiter, InFlightFlow
from sandbox_executor.flow.run_lock import acquire_flow_run_lock, release_flow_run_lock
from sandbox_executor.touch_set import TouchSet, TouchSetEntry
from sandbox_executor.touch_set_overlap import TouchSetOnBase

from tests.flow.helpers import OTHER_PLAN, FakeGh, _git, make_plan_branch

OLDER_PLAN = "I-100-own/P-101-cccccccc-claude-agent-claude-opus-5-5/_"
NEWER_PLAN = OTHER_PLAN
MERGING_HEAD = "I-300-third/P-301-eeeeeeee-claude-agent-claude-opus-5-5/E-302-ffffffff-claude-agent-claude-opus-5-5/_"
OPEN = FakeGh([{"number": 7, "headRefName": MERGING_HEAD, "baseRefName": "main"}], {7: ["src/app.py"]})
MERGED = FakeGh([], {})


def _saved_stage3_flow(worktree: Path, plan_branch: str, slug: str) -> FlowContext:
    context = FlowContext(
        target_worktree=str(worktree),
        intent_data={"slug": slug},
        intent_branch=plan_branch.split("/", 1)[0] + "/_",
        plan_branch=plan_branch,
        token_reduce=False,
    )
    for stage in (FlowStage.INTENT, FlowStage.PLAN):
        context.stage_results[stage.value] = StageResult(stage=stage, status=StageStatus.SUCCESS)
    save_checkpoint(context)
    return context


def _retry_as_resumed_flow(context: FlowContext, gh: FakeGh) -> dict:
    path = get_default_checkpoint_path(context.target_worktree, context.intent_data["slug"])
    resumed = load_checkpoint(path).context
    check_admission(resumed, gh)
    return json.loads(Path(path).read_text(encoding="utf-8"))["admission"]


@pytest.fixture
def two_waiters(flow_repos, tmp_path, monkeypatch):
    host, _origin = flow_repos
    make_plan_branch(host, OLDER_PLAN, [{"path": "src/app.py"}])
    make_plan_branch(host, NEWER_PLAN, [{"path": "src/"}])
    second_worktree = tmp_path / "second"
    _git(host, "worktree", "add", "-q", "--detach", str(second_worktree), "main")
    readings = iter(f"2026-11-20T01:{minute:02d}:00.000Z" for minute in range(60))
    monkeypatch.setattr(admission_state, "utc_now", lambda: next(readings))
    older, newer = (
        _saved_stage3_flow(host, OLDER_PLAN, "older"),
        _saved_stage3_flow(second_worktree, NEWER_PLAN, "newer"),
    )
    assert _retry_as_resumed_flow(older, OPEN)["blocked_by"][0]["number"] == 7
    assert _retry_as_resumed_flow(newer, OPEN)["blocked_by"][0]["number"] == 7
    return older, newer


@pytest.fixture(autouse=True)
def _release_run_locks():
    yield
    release_flow_run_lock()


def _local_blockers(recorded: dict) -> list[tuple[str, str]]:
    return [(entry["kind"], entry["plan_branch"]) for entry in recorded["blocked_by"]]


def test_newer_flow_retrying_while_older_waiter_is_parked_is_admitted(two_waiters):
    _older, newer = two_waiters
    assert _retry_as_resumed_flow(newer, MERGED) == {"state": "admitted", "waited_since": "2026-11-20T01:01:00.000Z"}


def test_newer_flow_retrying_first_defers_to_live_older_waiter(two_waiters):
    older, newer = two_waiters
    first_refusal_time = "2026-11-20T01:01:00.000Z"
    older_lock = acquire_flow_run_lock(older.target_worktree, "older", "live-older")
    assert older_lock is not None
    try:
        newer_state = _retry_as_resumed_flow(newer, MERGED)
        assert newer_state["state"] == "blocked" and _local_blockers(newer_state) == [("local_flow", OLDER_PLAN)]
        assert newer_state["blocked_since"] == first_refusal_time
        assert Path(newer_state["blocked_by"][0]["worktree"]).resolve() == Path(older.target_worktree).resolve()
        assert _retry_as_resumed_flow(older, MERGED) == {
            "state": "admitted",
            "waited_since": "2026-11-20T01:00:00.000Z",
        }
        again = _retry_as_resumed_flow(newer, MERGED)
        assert again["state"] == "blocked" and _local_blockers(again) == [("local_flow", OLDER_PLAN)]
    finally:
        release_flow_run_lock(older_lock.path)


def test_older_flow_retrying_first_is_admitted_and_blocks_the_newer(two_waiters):
    older, newer = two_waiters
    older_lock = acquire_flow_run_lock(older.target_worktree, "older", "live-older")
    assert older_lock is not None
    try:
        assert _retry_as_resumed_flow(older, MERGED) == {
            "state": "admitted",
            "waited_since": "2026-11-20T01:00:00.000Z",
        }
        newer_state = _retry_as_resumed_flow(newer, MERGED)
        assert newer_state["state"] == "blocked" and _local_blockers(newer_state) == [("local_flow", OLDER_PLAN)]
    finally:
        release_flow_run_lock(older_lock.path)


def _reload(context: FlowContext) -> FlowContext:
    return load_checkpoint(get_default_checkpoint_path(context.target_worktree, context.intent_data["slug"])).context


def test_an_admitted_flow_resuming_stage_3_does_not_defer_to_a_ready_older_waiter(two_waiters):
    older, newer = two_waiters
    older_waiting_on_pr_7_alone = _reload(older).admission
    older_waiting_on_pr_7_alone["blocked_by"] = [
        entry for entry in older_waiting_on_pr_7_alone["blocked_by"] if entry["kind"] == "pull_request"
    ]
    assert save_admission(_reload(older), older_waiting_on_pr_7_alone) is None
    admitted_earlier_under_allow_overlap = {"state": "admitted"}
    assert save_admission(_reload(newer), admitted_earlier_under_allow_overlap) is None
    older_lock = acquire_flow_run_lock(older.target_worktree, "older", "live-older")
    assert older_lock is not None
    try:
        # why: an admitted flow resuming Stage 3 never defers to an older waiter, even once PR #7 merges
        assert _retry_as_resumed_flow(newer, MERGED) == {"state": "admitted"}
    finally:
        release_flow_run_lock(older_lock.path)


def _waiter(plan_branch: str, since: str, *blocked_by: BlockedBy) -> BlockedWaiter:
    record = AdmissionRecord("blocked", blocked_since=since, blocked_by=blocked_by)
    return BlockedWaiter(plan_branch, "/w", TouchSet((TouchSetEntry("src/"),)), record)


BASE = frozenset({"src/app.py"})


def test_a_waiter_still_blocked_or_waiting_on_this_flow_is_not_ready():
    own = TouchSetOnBase(TouchSet((TouchSetEntry("src/app.py"),)), BASE)
    own_plan = "I-9-own/P-9-x/_"
    own_key = ("2026-11-20T09:00:00.000Z", own_plan)
    by_pr = BlockedBy("pull_request", ("src/app.py",), number=7, branch=MERGING_HEAD)
    by_own_pr = BlockedBy("pull_request", ("src/app.py",), number=5, branch="I-9-own/P-9-x/E-9-y/_")
    by_own_flow = BlockedBy("local_flow", ("src/app.py",), plan_branch=own_plan, worktree="/w/own")
    by_own_child = BlockedBy("local_flow", ("src/app.py",), plan_branch="I-9-own--1-c/P-9-z/_", worktree="/w/c")
    assert not blocker_is_clear(by_pr, {7}, set(), own_plan)
    assert blocker_is_clear(by_pr, set(), set(), own_plan)
    assert not blocker_is_clear(by_own_pr, set(), set(), own_plan)
    assert not blocker_is_clear(by_own_child, set(), set(), own_plan)
    # why: a waiter blocked only by this flow is clear, because this flow is re-checking rather than running
    assert blocker_is_clear(by_own_flow, set(), set(), own_plan)
    open_pr = InFlightFlow("pull request #7", "I-300-third/P-301-x/_", number=7, head=MERGING_HEAD)
    since = "2026-11-20T01:00:00.000Z"
    assert older_ready_waiter(own_key, own, [open_pr], [_waiter(OLDER_PLAN, since, by_pr)]) is None
    assert older_ready_waiter(own_key, own, [], [_waiter(OLDER_PLAN, since, by_own_pr)]) is None
    waiting_on_us = _waiter(OLDER_PLAN, since, by_own_flow)
    assert older_ready_waiter(own_key, own, [], [waiting_on_us]) is waiting_on_us
    assert older_ready_waiter(own_key, own, [], [_waiter(OLDER_PLAN, "2026-11-20T10:00:00.000Z", by_pr)]) is None
    oldest = _waiter(NEWER_PLAN, "2026-11-20T00:30:00.000Z", by_pr)
    assert older_ready_waiter(own_key, own, [], [_waiter(OLDER_PLAN, since, by_pr), oldest]) is oldest


def test_a_waiter_overlapping_only_on_new_files_is_not_deferred_to():
    own_key = ("2026-11-20T09:00:00.000Z", "I-9-own/P-9-x/_")
    by_merged_pr = BlockedBy("pull_request", ("src/app.py",), number=7, branch=MERGING_HEAD)
    waiter_declaring_src = _waiter(OLDER_PLAN, "2026-11-20T01:00:00.000Z", by_merged_pr)
    adds_a_file_missing_from_base = TouchSetOnBase(TouchSet((TouchSetEntry("src/new.py"),)), BASE)
    assert older_ready_waiter(own_key, adds_a_file_missing_from_base, [], [waiter_declaring_src]) is None
    edits_a_file = TouchSetOnBase(TouchSet((TouchSetEntry("src/app.py"),)), BASE)
    assert older_ready_waiter(own_key, edits_a_file, [], [waiter_declaring_src]) is waiter_declaring_src


def test_a_waiter_without_blocked_since_takes_no_place_in_the_order():
    own = TouchSetOnBase(TouchSet((TouchSetEntry("src/app.py"),)), BASE)
    own_key = ("2026-11-20T09:00:00.000Z", "I-9-own/P-9-x/_")
    undated = BlockedWaiter(OLDER_PLAN, "/w", TouchSet((TouchSetEntry("src/"),)), AdmissionRecord("blocked"))
    assert older_ready_waiter(own_key, own, [], [undated]) is None


def test_a_foreign_blocked_by_entry_with_non_string_references_never_crashes_admission():
    pr = BlockedBy.from_dict({"kind": "pull_request", "number": 7, "branch": 42, "files": []})
    flow = BlockedBy.from_dict({"kind": "local_flow", "plan_branch": ["x"], "worktree": {}, "files": []})
    assert pr is not None and pr.branch is None
    assert flow is not None and (flow.plan_branch, flow.worktree) == (None, None)
    assert blocker_is_clear(pr, set(), set(), "I-9-own/P-9-x/_")
    assert blocker_is_clear(flow, set(), set(), "I-9-own/P-9-x/_")
