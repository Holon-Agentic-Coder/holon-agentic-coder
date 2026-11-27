from __future__ import annotations

import os
import subprocess
import threading

import pytest
from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.admission import AdmissionDecision, check_admission
from sandbox_executor.flow.admission_lock import (
    ADMISSION_LOCK_NAME,
    AdmissionLockError,
    admission_lock,
    admission_lock_path,
)

from tests.flow.helpers import OTHER_PLAN, FakeGh, _git, make_plan_branch

OWN_PLAN = "I-100-own/P-101-cccccccc-claude-agent-claude-opus-5-5/_"


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

    threads = [threading.Thread(target=admit, args=(plan,)) for plan in contexts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
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
