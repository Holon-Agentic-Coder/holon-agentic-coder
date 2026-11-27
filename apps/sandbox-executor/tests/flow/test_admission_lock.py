from __future__ import annotations

import fcntl
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus, admission_state
from sandbox_executor.flow.admission import AdmissionDecision, check_admission, format_refusal
from sandbox_executor.flow.admission_lock import (
    ADMISSION_LOCK_NAME,
    AdmissionLockError,
    _live_runner_worktrees,
    admission_lock,
    admission_lock_path,
)
from sandbox_executor.flow.checkpoint import load_checkpoint, save_checkpoint
from sandbox_executor.flow.checkpoint_discovery import get_default_checkpoint_path
from sandbox_executor.flow.run_lock import acquire_flow_run_lock, release_flow_run_lock, run_lock_path
from sandbox_executor.flow.run_lock_record import RunLockHolder

from tests.flow.helpers import OTHER_PLAN, FakeGh, _git, make_plan_branch

OWN_PLAN = "I-100-own/P-101-cccccccc-claude-agent-claude-opus-5-5/_"


@pytest.fixture(autouse=True)
def _release_run_locks():
    yield
    release_flow_run_lock()


def test_lock_path_is_in_the_common_git_directory_of_every_worktree(flow_repos, tmp_path):
    host, _origin = flow_repos
    second = tmp_path / "second"
    _git(host, "worktree", "add", "-q", "--detach", str(second), "main")
    expected = os.path.realpath(host / ".git" / ADMISSION_LOCK_NAME)
    assert os.path.realpath(admission_lock_path(str(host))) == expected
    assert os.path.realpath(admission_lock_path(str(second))) == expected


def test_lock_path_fails_closed_when_git_fails(tmp_path):
    def failing_git(args, cwd):
        return subprocess.CompletedProcess(["git", *args], 128, "", "fatal: not a git repository")

    with pytest.raises(AdmissionLockError, match="git-common-dir"):
        admission_lock_path(str(tmp_path), failing_git)


def test_a_second_holder_times_out_then_acquires_after_release(flow_repos, tmp_path):
    host, _origin = flow_repos
    second = tmp_path / "second"
    _git(host, "worktree", "add", "-q", "--detach", str(second), "main")
    held, release = threading.Event(), threading.Event()

    def hold() -> None:
        with admission_lock(str(host)):
            held.set()
            release.wait(timeout=10)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert held.wait(timeout=10)
        with (
            pytest.raises(AdmissionLockError, match="held the admission lock"),
            admission_lock(str(second), timeout_seconds=0.2, poll_seconds=0.02),
        ):
            pass
    finally:
        release.set()
        holder.join(timeout=10)
    with admission_lock(str(second), timeout_seconds=5, poll_seconds=0.02) as path:
        assert path.endswith(ADMISSION_LOCK_NAME)


# why: gh pr list waits (bounded) for a second concurrent discovery; without the lock both would see nothing.
class OverlappingDiscovery(FakeGh):
    def __init__(self) -> None:
        super().__init__([], {})
        self.inside, self.most_inside = 0, 0
        self.guard, self.both_inside = threading.Lock(), threading.Event()

    def __call__(self, args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
        if args[:2] != ["pr", "list"]:
            return super().__call__(args, cwd)
        with self.guard:
            self.inside += 1
            self.most_inside = max(self.most_inside, self.inside)
            if self.inside == 2:
                self.both_inside.set()
        self.both_inside.wait(timeout=0.5)
        with self.guard:
            self.inside -= 1
        return super().__call__(args, cwd)


def _stage3_context(worktree, plan_branch: str, slug: str) -> FlowContext:
    context = FlowContext(
        target_worktree=str(worktree),
        intent_data={"slug": slug},
        intent_branch=plan_branch.split("/", 1)[0] + "/_",
        plan_branch=plan_branch,
        token_reduce=False,
    )
    for stage in (FlowStage.INTENT, FlowStage.PLAN):
        context.stage_results[stage.value] = StageResult(stage=stage, status=StageStatus.SUCCESS)
    return context


def test_racing_admission_checks_admit_exactly_one_overlapping_flow(flow_repos, tmp_path):
    host, _origin = flow_repos
    make_plan_branch(host, OWN_PLAN, [{"path": "src/app.py"}])
    make_plan_branch(host, OTHER_PLAN, [{"path": "src/"}])
    second = tmp_path / "second"
    _git(host, "worktree", "add", "-q", "--detach", str(second), "main")
    contexts = {OWN_PLAN: _stage3_context(host, OWN_PLAN, "own"), OTHER_PLAN: _stage3_context(second, OTHER_PLAN, "b")}
    gh, start = OverlappingDiscovery(), threading.Barrier(2, timeout=10)
    decisions: dict[str, AdmissionDecision] = {}

    def admit(plan_branch: str) -> None:
        start.wait()
        decisions[plan_branch] = check_admission(contexts[plan_branch], gh)

    own_lock = acquire_flow_run_lock(str(host), "own", "race-own")
    other_lock = acquire_flow_run_lock(str(second), "b", "race-b")
    assert own_lock is not None and other_lock is not None
    try:
        threads = [threading.Thread(target=admit, args=(plan,)) for plan in contexts]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    finally:
        release_flow_run_lock(own_lock.path)
        release_flow_run_lock(other_lock.path)
    assert not any(thread.is_alive() for thread in threads), "an admission check hung"
    # why: a peak of one concurrent discovery proves the lock serialised them.
    assert gh.most_inside == 1
    admitted = [plan for plan, decision in decisions.items() if decision.admitted]
    assert len(admitted) == 1
    (refused,) = set(contexts) - set(admitted)
    record = contexts[refused].admission
    assert record is not None and record["state"] == "blocked"
    assert [(entry["kind"], entry["plan_branch"]) for entry in record["blocked_by"]] == [("local_flow", admitted[0])]
    assert contexts[admitted[0]].admission == {"state": "admitted"}


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_live_runner_worktrees_excludes_released_stale_and_dead_pid_locks(flow_repos, tmp_path, lock_holder):
    host, _origin = flow_repos
    lock_holder(host, "live-slug")

    released_dir = tmp_path / "released"
    _git(host, "worktree", "add", "-q", "--detach", str(released_dir), "main")
    released = acquire_flow_run_lock(str(released_dir), "released-slug", "rel01")
    assert released is not None
    release_flow_run_lock(released.path)

    stale_path = str(run_lock_path(str(host), "stale-slug"))
    stale = RunLockHolder(os.getpid(), socket.gethostname(), "2026-01-01T00:00:00+00:00", "stale-slug", "/stale", "s1")
    Path(stale_path).write_text(stale.to_json(), encoding="utf-8")

    dead_path = str(run_lock_path(str(host), "dead-slug"))
    dead = RunLockHolder(_dead_pid(), socket.gethostname(), "2026-01-01T00:00:00+00:00", "dead-slug", "/dead", "d1")
    Path(dead_path).write_text(dead.to_json(), encoding="utf-8")
    descriptor = os.open(dead_path, os.O_RDWR)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    inheritor = subprocess.Popen(["sleep", "30"], pass_fds=(descriptor,))
    os.close(descriptor)
    try:
        assert _live_runner_worktrees(str(host)) == frozenset({os.path.realpath(host)})
    finally:
        inheritor.kill()
        inheritor.wait()


def test_two_parked_flows_resuming_at_once_admit_exactly_one_with_named_live_blocker(flow_repos, tmp_path, monkeypatch):
    host, _origin = flow_repos
    make_plan_branch(host, OWN_PLAN, [{"path": "src/app.py"}])
    make_plan_branch(host, OTHER_PLAN, [{"path": "src/"}])
    second = tmp_path / "second"
    _git(host, "worktree", "add", "-q", "--detach", str(second), "main")
    readings = iter(f"2026-11-20T01:{minute:02d}:00.000Z" for minute in range(20))
    monkeypatch.setattr(admission_state, "utc_now", lambda: next(readings))

    older = _stage3_context(host, OWN_PLAN, "older")
    newer = _stage3_context(second, OTHER_PLAN, "newer")
    save_checkpoint(older)
    save_checkpoint(newer)
    open_pr = FakeGh(
        [{"number": 7, "headRefName": "I-300-third/P-301-e/E-302-f/_", "baseRefName": "main"}], {7: ["src/app.py"]}
    )
    assert not check_admission(older, open_pr).admitted
    assert not check_admission(newer, open_pr).admitted

    older_resumed = load_checkpoint(get_default_checkpoint_path(str(host), "older")).context
    newer_resumed = load_checkpoint(get_default_checkpoint_path(str(second), "newer")).context
    resumed = {OWN_PLAN: older_resumed, OTHER_PLAN: newer_resumed}
    gh, start = OverlappingDiscovery(), threading.Barrier(2, timeout=10)
    decisions: dict[str, AdmissionDecision] = {}

    def resume(plan_branch: str) -> None:
        start.wait()
        decisions[plan_branch] = check_admission(resumed[plan_branch], gh)

    older_lock = acquire_flow_run_lock(str(host), "older", "resume-older")
    newer_lock = acquire_flow_run_lock(str(second), "newer", "resume-newer")
    assert older_lock is not None and newer_lock is not None
    try:
        threads = [threading.Thread(target=resume, args=(plan,)) for plan in resumed]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    finally:
        release_flow_run_lock(older_lock.path)
        release_flow_run_lock(newer_lock.path)

    assert not any(thread.is_alive() for thread in threads), "a resumed admission check hung"
    assert gh.most_inside == 1
    assert decisions[OWN_PLAN].admitted and not decisions[OTHER_PLAN].admitted
    assert older_resumed.admission == {"state": "admitted", "waited_since": "2026-11-20T01:00:00.000Z"}
    assert newer_resumed.admission is not None and newer_resumed.admission["state"] == "blocked"
    assert [(e["kind"], e["plan_branch"]) for e in newer_resumed.admission["blocked_by"]] == [("local_flow", OWN_PLAN)]
    assert OWN_PLAN in format_refusal(decisions[OTHER_PLAN])
