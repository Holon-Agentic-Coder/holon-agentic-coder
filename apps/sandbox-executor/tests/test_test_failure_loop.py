import dataclasses
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest
from sandbox_executor.entrypoint.test_failure_classify import (
    NOT_RUN,
    BaseSnapshot,
    RerunResult,
    ShortSummaryRunner,
    rerun_ids_command,
    run_failing_ids,
)
from sandbox_executor.entrypoint.test_failure_loop import (
    MAX_FIX_TURNS,
    FixRequest,
    LoopPorts,
    TestFailureLoop,
    fix_prompt,
    measure_selection,
    next_tier,
)
from sandbox_executor.entrypoint.test_failure_record import STAGE_3, TestFailureEvidence, record_test_failure
from sandbox_executor.entrypoint.test_measurement import TestMeasurement
from sandbox_executor.flow import parked_record_from_ci_status
from sandbox_executor.flow.review.local_tests import _attach_test_failure
from sandbox_executor.knowledge.wisdom import promote_repeated_failure_signatures

ALPHA = "tests/test_a.py::test_alpha"
BETA = "tests/test_a.py::test_beta"
LADDER = ("haiku-x", "sonnet-x", "opus-x")


def _passing() -> tuple[TestMeasurement, str]:
    return TestMeasurement(1.0, 3, 0, 0, 0, None, "3 passed in 0.1s"), "3 passed in 0.1s"


def _failing(test_id: str = ALPHA, message: str = "assert 1 == 2") -> tuple[TestMeasurement, str]:
    output = f"E       {message}\nFAILED {test_id} - {message}\n1 failed, 2 passed in 0.1s"
    return TestMeasurement(2 / 3, 2, 1, 0, 1, None, output), output


PASS = RerunResult(passed=True)
FAIL = RerunResult(passed=False, failing_ids=(ALPHA,), exit_code=1)


class FakeSuite:
    def __init__(self, *runs: tuple[TestMeasurement, str]) -> None:
        self.runs = list(runs)
        self.calls = 0

    def __call__(self) -> tuple[TestMeasurement, str]:
        self.calls += 1
        return self.runs.pop(0)


class FakeRerun:
    def __init__(self, *results: RerunResult) -> None:
        self.results = list(results)
        self.calls = 0

    def __call__(self, ids: tuple[str, ...], env: Mapping[str, str]) -> RerunResult:
        self.calls += 1
        return self.results.pop(0)


class FakeFix:
    def __init__(self) -> None:
        self.requests: list[FixRequest] = []

    def __call__(self, request: FixRequest) -> bool:
        self.requests.append(request)
        return True


class MemoryLedger:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def __call__(self, evidence: TestFailureEvidence) -> dict:
        row = {"action_id": f"ACT-{len(self.rows)}", "evidence": evidence}
        self.rows.append(row)
        return row


def _loop(suite: FakeSuite, rerun: FakeRerun, *, base: RerunResult = PASS, **overrides: object) -> TestFailureLoop:
    ports = LoopPorts(
        run_selection=suite,
        rerun_ids=rerun,
        base_rerun=lambda ids: base,
        record=MemoryLedger(),
        stage=STAGE_3,
        head_sha=lambda: "abc123",
        ladder=LADDER,
        start_tier=LADDER[0],
        remaining_seconds=lambda: 1000.0,
    )
    return TestFailureLoop(dataclasses.replace(ports, **overrides))


def test_passing_first_run_costs_nothing() -> None:
    suite, rerun = FakeSuite(), FakeRerun()

    outcome = _loop(suite, rerun).run(*_passing())

    assert outcome.status == "passed"
    assert (suite.calls, rerun.calls, outcome.rows) == (0, 0, [])


def test_introduced_failure_is_fixed_and_rerun() -> None:
    fix, ledger = FakeFix(), MemoryLedger()

    outcome = _loop(FakeSuite(_passing()), FakeRerun(FAIL, FAIL), fix_turn=fix, record=ledger).run(*_failing())

    assert outcome.status == "passed"
    assert outcome.measurement.rate == 1.0
    assert fix.requests[0].classification == "introduced"
    assert fix.requests[0].failing_ids == (ALPHA,)
    assert "assert 1 == 2" in fix.requests[0].excerpts[ALPHA]
    assert [row["evidence"].classification for row in ledger.rows] == ["introduced", "introduced"]
    assert ledger.rows[-1]["evidence"].outcome == "success"


def test_flaky_failure_is_rerun_recorded_and_passes() -> None:
    ledger = MemoryLedger()
    suite, rerun = FakeSuite(_passing()), FakeRerun(PASS, PASS, PASS)

    outcome = _loop(suite, rerun, record=ledger).run(*_failing())

    assert outcome.status == "flaky_passed"
    assert outcome.classification == "flaky"
    assert rerun.calls == 3
    assert [row["evidence"].classification for row in ledger.rows] == ["flaky", "flaky", "flaky"]


def test_flaky_failure_that_fails_again_is_blocked() -> None:
    outcome = _loop(FakeSuite(), FakeRerun(PASS, FAIL)).run(*_failing())

    assert outcome.status == "blocked"
    assert outcome.blocked_reason == "flaky test failed again on re-run"


def test_persistent_flakiness_is_blocked() -> None:
    outcome = _loop(FakeSuite(_failing()), FakeRerun(PASS, PASS, PASS, PASS)).run(*_failing())

    assert outcome.status == "blocked"
    assert outcome.blocked_reason == "persistent flakiness"


def test_leaked_identity_is_blocked_with_environment_evidence() -> None:
    def identity_sensitive(ids: tuple[str, ...], env: Mapping[str, str]) -> RerunResult:
        return FAIL if "GIT_AUTHOR_NAME" in env else PASS

    ledger = MemoryLedger()
    loop = TestFailureLoop(
        LoopPorts(
            run_selection=FakeSuite(),
            rerun_ids=identity_sensitive,
            base_rerun=lambda ids: PASS,
            record=ledger,
            stage=STAGE_3,
            fix_turn=FakeFix(),
            environment=lambda: {"GIT_AUTHOR_NAME": "Reviewer"},
        )
    )

    outcome = loop.run(*_failing())

    assert outcome.status == "blocked"
    assert outcome.classification == "environment"
    assert "GIT_AUTHOR_NAME" in outcome.evidence_text
    assert "Reviewer" not in outcome.evidence_text
    assert ALPHA in outcome.evidence_text
    assert ledger.rows[0]["evidence"].environment["identity_overrides"] == ["GIT_AUTHOR_NAME"]


def test_pre_existing_failure_is_blocked() -> None:
    fix = FakeFix()

    outcome = _loop(FakeSuite(), FakeRerun(FAIL, FAIL), base=FAIL, fix_turn=fix).run(*_failing())

    assert outcome.status == "blocked"
    assert outcome.classification == "pre-existing"
    assert fix.requests == []


def test_repeated_signature_steps_up_one_tier() -> None:
    fix = FakeFix()

    outcome = _loop(FakeSuite(_failing(), _passing()), FakeRerun(FAIL, FAIL), fix_turn=fix).run(*_failing())

    assert outcome.status == "passed"
    assert [request.tier for request in fix.requests] == ["haiku-x", "sonnet-x"]
    assert outcome.final_tier == "sonnet-x"


def test_exhausted_ladder_stops_with_the_trail() -> None:
    fix, ledger = FakeFix(), MemoryLedger()
    suite = FakeSuite(_failing(), _failing(), _failing())

    outcome = _loop(suite, FakeRerun(FAIL, FAIL), fix_turn=fix, record=ledger).run(*_failing())

    assert outcome.status == "exhausted"
    assert [request.tier for request in fix.requests] == list(LADDER)
    assert len(outcome.rows) == 4
    assert [row["action_id"] for row in ledger.rows] == [row["action_id"] for row in outcome.rows]
    assert "ACT-3" in outcome.evidence_text


def test_different_signature_after_a_fix_is_rediagnosed() -> None:
    fix = FakeFix()
    beta_fail = RerunResult(passed=False, failing_ids=(BETA,), exit_code=1)
    suite = FakeSuite(_failing(BETA, "ValueError: boom"), _passing())

    outcome = _loop(suite, FakeRerun(FAIL, FAIL, beta_fail, beta_fail), fix_turn=fix).run(*_failing())

    assert outcome.status == "passed"
    assert [request.failing_ids for request in fix.requests] == [(ALPHA,), (BETA,)]
    assert [request.tier for request in fix.requests] == ["haiku-x", "haiku-x"]


def test_fix_turns_are_bounded() -> None:
    fix = FakeFix()
    messages = [f"assert {n} == 0" for n in "abcdefgh"]
    suite = FakeSuite(*(_failing(message=m) for m in messages))
    reruns = FakeRerun(*([FAIL] * 20))

    outcome = _loop(suite, reruns, fix_turn=fix).run(*_failing())

    assert outcome.status == "exhausted"
    assert len(fix.requests) == MAX_FIX_TURNS


def test_no_remaining_time_stops_before_a_fix_turn() -> None:
    fix = FakeFix()

    outcome = _loop(FakeSuite(), FakeRerun(FAIL, FAIL), fix_turn=fix, remaining_seconds=lambda: 0.0).run(*_failing())

    assert outcome.status == "exhausted"
    assert fix.requests == []


def test_unmeasured_first_run_is_blocked_and_recorded() -> None:
    ledger = MemoryLedger()
    unmeasured = TestMeasurement(None, None, None, None, 124, "the test task timed out after 900 seconds", "")

    outcome = _loop(FakeSuite(), FakeRerun(), record=ledger).run(unmeasured, "")

    assert outcome.status == "blocked"
    assert outcome.classification == "environment"
    assert "timed out" in (outcome.blocked_reason or "")
    assert len(ledger.rows) == 1


def test_introduced_failure_without_a_fix_runner_is_blocked() -> None:
    outcome = _loop(FakeSuite(), FakeRerun(FAIL, FAIL)).run(*_failing())

    assert outcome.status == "blocked"
    assert outcome.classification == "introduced"
    assert outcome.blocked_reason == "no fix runner available"


def test_fix_prompt_forbids_weakening_tests_and_background_commands() -> None:
    prompt = fix_prompt(FixRequest("introduced", (ALPHA,), {ALPHA: "E assert 1 == 2"}, "sig", "haiku-x", 1))

    assert ALPHA in prompt
    assert "E assert 1 == 2" in prompt
    assert "Do not weaken" in prompt
    assert "Never background" in prompt


def test_next_tier_walks_the_ladder() -> None:
    assert next_tier(LADDER, "haiku-x") == "sonnet-x"
    assert next_tier(LADDER, "opus-x") is None
    assert next_tier((), None) is None


def test_rerun_command_selects_only_the_failing_ids_without_syncing() -> None:
    command = rerun_ids_command((ALPHA, BETA))

    assert command[:3] == ["uv", "run", "--no-sync"]
    assert command[-2:] == [ALPHA, BETA]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def base_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "apps" / "demo" / "src").mkdir(parents=True)
    (repo / "apps" / "demo" / "src" / "marker.py").write_text("BASE = True\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "apps" / "demo" / "src" / "marker.py").write_text("BASE = False\n")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "-am", "change")
    return repo


def test_base_snapshot_runs_the_merge_base_and_removes_it(base_repo: Path) -> None:
    seen: dict[str, object] = {}

    def session(argv: list[str], cwd: str, timeout: float, env: Mapping[str, str]) -> subprocess.CompletedProcess:
        seen["cwd"] = cwd
        seen["marker"] = Path(cwd, "apps", "demo", "src", "marker.py").read_text()
        seen["pythonpath"] = env["PYTHONPATH"]
        return subprocess.CompletedProcess(argv, 1, f"FAILED {ALPHA} - boom\n", "")

    result = BaseSnapshot(str(base_repo), "main", session)((ALPHA,))

    assert result.ran and not result.passed
    assert result.failing_ids == (ALPHA,)
    assert seen["marker"] == "BASE = True\n"
    assert seen["pythonpath"] == os.path.join(str(seen["cwd"]), "apps", "demo", "src")
    assert not os.path.exists(str(seen["cwd"]))


def test_base_snapshot_is_removed_when_the_runner_raises(base_repo: Path) -> None:
    seen: dict[str, str] = {}

    def session(argv: list[str], cwd: str, timeout: float, env: Mapping[str, str]) -> subprocess.CompletedProcess:
        seen["cwd"] = cwd
        raise RuntimeError("runner exploded")

    result = BaseSnapshot(str(base_repo), "main", session)((ALPHA,))

    assert result == NOT_RUN
    assert not os.path.exists(seen["cwd"])


def test_base_snapshot_without_a_merge_base_is_unavailable(base_repo: Path) -> None:
    assert BaseSnapshot(str(base_repo), "no-such-branch")((ALPHA,)) == NOT_RUN


def test_short_summary_runner_keeps_the_full_output_and_asks_for_ids() -> None:
    seen: dict[str, str] = {}

    def session(argv: list[str], cwd: str, timeout: float, env: Mapping[str, str]) -> subprocess.CompletedProcess:
        seen["addopts"] = env["PYTEST_ADDOPTS"]
        return subprocess.CompletedProcess(argv, 1, "x" * 5000 + f"\nFAILED {ALPHA}", "")

    runner = ShortSummaryRunner(session)
    measurement, output = measure_selection("/nonexistent", runner)

    assert "-rfE" in seen["addopts"]
    assert len(output) > 5000
    assert measurement.exit_code == 1


def test_failing_id_rerun_that_cannot_start_is_not_run() -> None:
    def session(argv: list[str], cwd: str, timeout: float, env: Mapping[str, str]) -> subprocess.CompletedProcess:
        raise FileNotFoundError("uv")

    assert run_failing_ids((ALPHA,), {}, "/nonexistent", session) == NOT_RUN


def test_a_fix_turn_that_raises_is_blocked_with_the_reason() -> None:
    def moving_fix(request: FixRequest) -> bool:
        raise RuntimeError("fix turn moved HEAD branch")

    outcome = _loop(FakeSuite(), FakeRerun(FAIL, FAIL), fix_turn=moving_fix).run(*_failing())

    assert outcome.status == "blocked"
    assert outcome.blocked_reason == "fix turn failed: fix turn moved HEAD branch"


def test_environment_failure_parks_with_blocked_on_and_ledger_rows() -> None:
    def identity_sensitive(ids: tuple[str, ...], env: Mapping[str, str]) -> RerunResult:
        return FAIL if "GIT_AUTHOR_NAME" in env else PASS

    ledger = MemoryLedger()
    checkpoint = "/tmp/checkpoints/I-1795780932.json"
    outcome = _loop(
        FakeSuite(),
        FakeRerun(),
        rerun_ids=identity_sensitive,
        record=ledger,
        fix_turn=FakeFix(),
        environment=lambda: {"GIT_AUTHOR_NAME": "Reviewer"},
        blocker_map={"GIT_AUTHOR_NAME": "BEAN-42"},
        checkpoint_path=checkpoint,
    ).run(*_failing())

    assert outcome.status == "blocked"
    assert outcome.parked is True
    assert outcome.blocked_on == "BEAN-42"
    assert outcome.parked_record is not None
    assert outcome.parked_record["status"] == "parked"
    assert outcome.parked_record["blocked_on"] == "BEAN-42"
    assert outcome.parked_record["classification"] == "environment"
    assert outcome.parked_record["failing_ids"] == [ALPHA]
    assert outcome.parked_record["signature"] == outcome.signature
    assert ALPHA in outcome.parked_record["evidence"]
    assert outcome.parked_record["ledger_rows"] == ledger.rows
    assert outcome.parked_record["checkpoint_path"] == checkpoint


def test_pre_existing_failure_extracts_pr_blocker_from_evidence() -> None:
    measurement, output = _failing(message="AssertionError: waiting on PR#77 migration")
    outcome = _loop(FakeSuite(), FakeRerun(FAIL, FAIL), base=FAIL, fix_turn=FakeFix()).run(measurement, output)

    assert outcome.status == "blocked"
    assert outcome.parked is True
    assert outcome.blocked_on == "PR#77"
    assert outcome.parked_record is not None
    assert outcome.parked_record["blocked_on"] == "PR#77"
    assert outcome.parked_record["classification"] == "pre-existing"


def test_exhausted_unresolved_failure_without_blocker_parks_with_none_and_watcher_schema() -> None:
    fix, ledger = FakeFix(), MemoryLedger()
    suite = FakeSuite(_failing(), _failing(), _failing())
    checkpoint = "/tmp/checkpoints/flow-exhausted.json"

    outcome = _loop(
        suite,
        FakeRerun(FAIL, FAIL),
        fix_turn=fix,
        record=ledger,
        checkpoint_path=checkpoint,
    ).run(*_failing())

    assert outcome.status == "exhausted"
    assert outcome.parked is True
    assert outcome.blocked_on == "none"
    assert outcome.parked_record is not None
    assert outcome.parked_record["blocked_on"] == "none"
    assert outcome.parked_record["evidence"]
    assert outcome.parked_record["ledger_rows"] == ledger.rows
    assert len(outcome.parked_record["ledger_rows"]) == 4
    assert outcome.parked_record["checkpoint_path"] == checkpoint

    ci_status: dict[str, object] = {}
    _attach_test_failure(ci_status, outcome, checkpoint_path=checkpoint)
    extracted = parked_record_from_ci_status(ci_status)
    assert extracted is not None
    assert extracted["status"] == "parked"
    assert extracted["blocked_on"] == "none"
    assert extracted["classification"] == "introduced"
    assert extracted["failing_ids"] == [ALPHA]
    assert extracted["signature"] == outcome.signature
    assert extracted["evidence"] == outcome.evidence_text
    assert extracted["ledger_rows"] == ledger.rows
    assert extracted["checkpoint_path"] == checkpoint


def test_introduced_failure_fixed_by_runner_does_not_park() -> None:
    outcome = _loop(FakeSuite(_passing()), FakeRerun(FAIL, FAIL), fix_turn=FakeFix()).run(*_failing())

    assert outcome.status == "passed"
    assert outcome.parked is False
    assert outcome.blocked_on is None
    assert outcome.parked_record is None


def test_single_run_failure_signature_creates_no_kb_failure_mode(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger" / "executions.jsonl"
    kb_dir = tmp_path / "kb"
    suite = FakeSuite(_failing(), _failing(), _failing())

    outcome = _loop(
        suite,
        FakeRerun(FAIL, FAIL),
        fix_turn=FakeFix(),
        record=lambda evidence: record_test_failure(
            str(ledger_path),
            evidence,
            branch="I-1795780932-exhausted-ladder-blocked-on",
            execution_id="E-single-run",
        ),
    ).run(*_failing())

    assert outcome.status == "exhausted"
    assert outcome.parked is True
    assert len(outcome.rows) == 4
    promoted = promote_repeated_failure_signatures(str(ledger_path), kb_dir=str(kb_dir))
    assert promoted == []
    failure_modes_dir = kb_dir / "failure_modes"
    assert not failure_modes_dir.exists() or list(failure_modes_dir.glob("*.json")) == []
