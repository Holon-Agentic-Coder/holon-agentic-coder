"""The merged-head CI gate: every check on the exact merged SHA must complete and pass before Stage 5 calibrates."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.github import ci_head_checks
from sandbox_executor.flow.github.ci_head_checks import (
    classify_head_checks,
    latest_checks_for_head,
    parse_check_runs,
    probe_head_checks,
)
from sandbox_executor.flow.github.ci_wait import CiWaitPolicy
from sandbox_executor.flow.review.consensus_withdrawal import WithdrawalSeams
from sandbox_executor.flow.review.merged_head_gate import MergedHeadSeams, format_merged_head_ci, gate_merged_head_ci
from sandbox_executor.flow.review_restart import stage5_merged_head_gate
from sandbox_executor.ledger import read_merged_rows

BRANCH = "I-1-x/P-1-A/E-1-A/_"
APPROVED = "a" * 40
MERGED = "b" * 40
OTHER = "c" * 40


def check(name: str = "unit", conclusion: str | None = "success", status: str = "completed", **extra: Any) -> dict:
    """One check run on the merged head; ``extra`` overrides ``check_run_id``, ``run_id``, ``head_sha`` or the suite."""
    base = {"check_run_id": 1, "name": name, "status": status, "conclusion": conclusion or "", "head_sha": MERGED}
    base["check_suite_id"] = 0
    return {**base, "run_id": "11", **extra}


class Fakes:
    """A scripted probe (one list per poll, the last repeating), a rerun recorder and a fake clock."""

    def __init__(self, *polls: list[dict[str, Any]] | None, rerun_ok: bool = True) -> None:
        self.polls = list(polls)
        self.probed: list[str] = []
        self.reruns: list[str] = []
        self.rerun_ok = rerun_ok
        self.now = 0.0
        self.sleeps: list[float] = []

    def probe(self, sha: str) -> list[dict[str, Any]] | None:
        self.probed.append(sha)
        return self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]

    def rerun(self, run_id: str) -> bool:
        self.reruns.append(run_id)
        return self.rerun_ok

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def seams(self, budget: float = 600.0, registration: float = 0.0, **extra: Any) -> MergedHeadSeams:
        return MergedHeadSeams(
            policy=CiWaitPolicy(budget, clock=lambda: self.now, sleep=self.sleep),
            probe=self.probe,
            rerun_dispatcher=self.rerun,
            registration_timeout=registration,
            **extra,
        )


def _ctx(tmp_path: Path, **kwargs: Any) -> FlowContext:
    return FlowContext(repo_dir=str(tmp_path), execution_branch=BRANCH, agent="a", model="m", **kwargs)


def _gate(tmp_path: Path, fakes: Fakes, **seam_kwargs: Any) -> dict[str, Any]:
    return gate_merged_head_ci(_ctx(tmp_path), str(tmp_path), MERGED, fakes.seams(**seam_kwargs))


# --- classification -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "conclusion", "expected"),
    [
        ("completed", "success", "passed"),
        ("completed", "neutral", "passed"),
        ("completed", "skipped", "passed"),
        ("completed", "failure", "failed"),
        ("completed", "timed_out", "failed"),
        ("completed", "action_required", "failed"),
        ("completed", "startup_failure", "failed"),
        ("completed", "something_new", "failed"),
        ("completed", "cancelled", "cancelled"),
        ("completed", "stale", "cancelled"),
        ("queued", None, "pending"),
        ("in_progress", None, "pending"),
    ],
)
def test_classification_is_fail_closed(status: str, conclusion: str | None, expected: str) -> None:
    assert classify_head_checks([check(), check("lint", conclusion, status, check_run_id=2)]) == expected


def test_no_checks_never_pass_and_failure_beats_pending_beats_cancelled() -> None:
    assert classify_head_checks([]) == "pending"
    pending, cancelled = check("a", None, "in_progress", check_run_id=2), check("b", "cancelled", check_run_id=3)
    assert classify_head_checks([pending, cancelled]) == "pending"
    assert classify_head_checks([pending, cancelled, check("c", "failure", check_run_id=4)]) == "failed"


def test_superseded_runs_are_ignored_and_the_newest_run_per_name_wins() -> None:
    checks = [
        check("unit", "failure", check_run_id=5, head_sha=OTHER),  # a newer run, but on another commit
        check("unit", "cancelled", check_run_id=1),
        check("unit", "success", check_run_id=3),
        check("lint", "success", check_run_id=2),
    ]
    latest = latest_checks_for_head(checks, MERGED)
    assert [(c["name"], c["check_run_id"], c["conclusion"]) for c in latest] == [
        ("lint", 2, "success"),
        ("unit", 3, "success"),
    ]


def test_same_named_checks_of_two_suites_are_kept_apart() -> None:
    checks = [
        check("build", "failure", check_run_id=4, check_suite_id=100),  # docker-build.yml
        check("build", "success", check_run_id=9, check_suite_id=200),  # build-images.yml, newer
    ]
    latest = latest_checks_for_head(checks, MERGED)
    assert [(c["check_suite_id"], c["conclusion"]) for c in latest] == [(100, "failure"), (200, "success")]
    assert classify_head_checks(latest) == "failed"


def test_check_runs_are_parsed_from_the_api_with_their_actions_run_id() -> None:
    line = {
        "id": 77,
        "name": "unit",
        "status": "COMPLETED",
        "conclusion": "Success",
        "head_sha": MERGED,
        "details_url": "https://github.com/o/r/actions/runs/4242/job/9",
    }
    external = {"id": 78, "name": "codecov", "status": "queued", "conclusion": None, "head_sha": MERGED}
    parsed = parse_check_runs(json.dumps(line) + "\n\n" + json.dumps(external) + "\n")
    assert parsed[0] == check("unit", "success", check_run_id=77, run_id="4242")
    assert parsed[1]["run_id"] is None and parsed[1]["status"] == "queued" and parsed[1]["conclusion"] == ""


def test_probe_reads_the_exact_sha_and_fails_closed_on_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    run = {"id": 1, "name": "unit", "status": "completed", "conclusion": "success", "head_sha": MERGED}
    outputs = {"check-runs": json.dumps({**run, "check_suite_id": 5}), "check-suites": json.dumps({"id": 5})}

    def fake_run(argv: list[str], **_: Any) -> SimpleNamespace:
        calls.append(argv)
        out = outputs[argv[3].rsplit("/", 1)[1]]
        return SimpleNamespace(returncode=0, stdout=out.replace('"id": 5}', '"id": 5, "status": "completed"}'))

    monkeypatch.setattr(ci_head_checks.subprocess, "run", fake_run)
    expected = check(run_id=None, check_suite_id=5, suite_status="completed")
    assert probe_head_checks(str(tmp_path), MERGED) == [expected]
    assert calls[0][:3] == ["gh", "api", "--paginate"]
    assert calls[0][3] == f"repos/{{owner}}/{{repo}}/commits/{MERGED}/check-runs"
    assert calls[1][3] == f"repos/{{owner}}/{{repo}}/commits/{MERGED}/check-suites"

    outputs["check-suites"] = ""  # a suite the endpoint does not list never counts as completed
    assert probe_head_checks(str(tmp_path), MERGED) == [{**expected, "suite_status": ""}]

    monkeypatch.setattr(ci_head_checks.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
    assert probe_head_checks(str(tmp_path), MERGED) is None

    def boom(*_: Any, **__: Any) -> None:
        raise OSError("no gh")

    monkeypatch.setattr(ci_head_checks.subprocess, "run", boom)
    assert probe_head_checks(str(tmp_path), MERGED) is None


# --- the wait --------------------------------------------------------------------------------------------------------


def test_all_checks_passed_on_the_merged_head(tmp_path: Path) -> None:
    fakes = Fakes([check(), check("lint", "skipped", check_run_id=2)])
    record = _gate(tmp_path, fakes)
    assert record["state"] == "passed"
    assert record["head_sha"] == MERGED
    assert fakes.probed == [MERGED]
    assert record["runs"] == [
        {"run_id": "11", "check_run_id": 1, "name": "unit", "conclusion": "success"},
        {"run_id": "11", "check_run_id": 2, "name": "lint", "conclusion": "skipped"},
    ]


def test_pending_then_passed_waits_on_the_fake_clock(tmp_path: Path) -> None:
    fakes = Fakes([check(conclusion=None, status="in_progress")], [check(conclusion=None, status="queued")], [check()])
    record = _gate(tmp_path, fakes)
    assert record["state"] == "passed"
    assert len(fakes.sleeps) == 2


def test_a_transient_probe_failure_keeps_polling_until_the_checks_settle(tmp_path: Path) -> None:
    fakes = Fakes([check(conclusion=None, status="in_progress")], None, [check()])
    record = _gate(tmp_path, fakes)
    assert record["state"] == "passed"
    assert len(fakes.probed) == 3


def test_a_needs_job_registering_after_every_visible_run_completed_keeps_the_head_pending(tmp_path: Path) -> None:
    build = check("build", check_suite_id=7, suite_status="in_progress")
    deploy = check("deploy", "failure", check_run_id=2, check_suite_id=7, suite_status="completed")
    assert classify_head_checks([build]) == "pending"  # the needs: job has no check run yet
    fakes = Fakes([build], [{**build, "suite_status": "completed"}, deploy])
    record = _gate(tmp_path, fakes)
    assert record["state"] == "failed"
    assert [run["name"] for run in record["runs"]] == ["build", "deploy"]
    assert len(fakes.probed) == 2


def test_a_suite_still_running_past_the_budget_is_pending(tmp_path: Path) -> None:
    record = _gate(tmp_path, Fakes([check(check_suite_id=7, suite_status="queued")]), budget=30.0)
    assert record["state"] == "pending" and "unit" in record["reason"]


def test_pending_past_the_budget_is_pending_never_passed(tmp_path: Path) -> None:
    fakes = Fakes([check(), check("slow", None, "in_progress", check_run_id=2)])
    record = _gate(tmp_path, fakes, budget=30.0)
    assert record["state"] == "pending"
    assert "slow" in record["reason"]
    assert fakes.now == pytest.approx(30.0)


def test_a_failed_check_is_failed(tmp_path: Path) -> None:
    fakes = Fakes([check(), check("lint", "failure", check_run_id=2, run_id="12")])
    record = _gate(tmp_path, fakes)
    assert record["state"] == "failed"
    assert fakes.reruns == []


def test_a_cancelled_run_is_retriggered_once_and_its_rerun_counts(tmp_path: Path) -> None:
    cancelled = [check(), check("lint", "cancelled", check_run_id=2, run_id="12")]
    rerun_pending = [check(), check("lint", None, "queued", check_run_id=3, run_id="12")]
    rerun_passed = [check(), check("lint", "success", check_run_id=3, run_id="12")]
    fakes = Fakes(cancelled, cancelled, rerun_pending, rerun_passed)
    record = _gate(tmp_path, fakes)
    assert fakes.reruns == ["12"]  # the stale snapshot of the cancelled run is not re-triggered again
    assert record["state"] == "passed"
    assert record["reruns"] == ["12"]


def test_a_run_cancelled_twice_fails_closed(tmp_path: Path) -> None:
    first = [check("lint", "cancelled", check_run_id=2, run_id="12")]
    again = [check("lint", "cancelled", check_run_id=3, run_id="12")]
    fakes = Fakes(first, again)
    record = _gate(tmp_path, fakes)
    assert record["state"] == "failed_closed"
    assert "cancelled twice" in record["reason"]
    assert fakes.reruns == ["12"]


def test_a_cancelled_check_without_an_actions_run_or_a_failed_dispatch_fails_closed(tmp_path: Path) -> None:
    record = _gate(tmp_path, Fakes([check("external", "stale", run_id=None)]))
    assert record["state"] == "failed_closed"
    assert "no Actions run" in record["reason"]
    fakes = Fakes([check("lint", "cancelled", run_id="12")], rerun_ok=False)
    record = _gate(tmp_path, fakes)
    assert record["state"] == "failed_closed"
    assert "re-triggering cancelled run 12 failed" in record["reason"]


def test_no_registered_checks_or_an_unreadable_probe_is_unknown(tmp_path: Path) -> None:
    fakes = Fakes([])
    record = _gate(tmp_path, fakes, registration=60.0)
    assert record["state"] == "unknown"
    assert fakes.now == pytest.approx(60.0)
    unreadable = Fakes(None)
    assert _gate(tmp_path, unreadable, budget=30.0)["state"] == "unknown"
    assert unreadable.now == pytest.approx(30.0)


def test_a_probe_exception_is_unknown_with_a_redacted_reason(tmp_path: Path) -> None:
    def boom(_: str) -> list[dict[str, Any]]:
        raise RuntimeError("token ghp_" + "x" * 36 + " leaked")

    record = gate_merged_head_ci(_ctx(tmp_path), str(tmp_path), MERGED, MergedHeadSeams(probe=boom))
    assert record["state"] == "unknown"
    assert "ghp_" + "x" * 36 not in record["reason"]


def test_the_log_line_names_the_full_sha_run_ids_and_conclusions(tmp_path: Path) -> None:
    record = _gate(tmp_path, Fakes([check(), check("lint", "failure", check_run_id=2, run_id="12")]))
    line = format_merged_head_ci(record)
    assert MERGED in line
    assert "unit (run 11, check run 1): success" in line
    assert "lint (run 12, check run 2): failure" in line


# --- the Stage 5 decision --------------------------------------------------------------------------------------------


def approved_review(target_commit: str = APPROVED) -> StageResult:
    return StageResult(
        stage=FlowStage.REVIEW,
        status=StageStatus.APPROVED,
        payload={
            "consensus": {"approved": True, "target_commit": target_commit, "pr_number": 7},
            "posting": {"dual_posted": True},
            "ci_checks": {"checks": [{"name": "unit", "bucket": "pass", "link": "https://x/actions/runs/5/job/1"}]},
        },
    )


def _approved_ctx(tmp_path: Path, **kwargs: Any) -> FlowContext:
    ctx = _ctx(tmp_path, **kwargs)
    ctx.stage_results["execute"] = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS)
    ctx.stage_results["review"] = approved_review()
    return ctx


def _seams(fakes: Fakes, head: str | None = MERGED, bookkeeping: bool = False, **kwargs: Any) -> MergedHeadSeams:
    return fakes.seams(head_reader=lambda: head, bookkeeping_move=lambda _a, _h: bookkeeping, **kwargs)


@pytest.mark.parametrize("flags", [{"dry_run": True}, {"skip_push": True}])
def test_dry_run_and_skip_push_keep_todays_path(tmp_path: Path, flags: dict[str, bool]) -> None:
    fakes = Fakes([check("unit", "failure")])
    stopped, record = stage5_merged_head_gate(_approved_ctx(tmp_path, **flags), _seams(fakes))
    assert stopped is None
    assert record["state"] == "skipped"
    assert fakes.probed == []


def test_no_recorded_approval_fails_stage5_closed(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    fakes = Fakes([check("unit", "failure")])
    stopped, record = stage5_merged_head_gate(ctx, _seams(fakes))
    assert stopped is not None and stopped.status == StageStatus.FAILED
    assert record["state"] == "refused"
    assert all("Merged-head CI gate skipped: no Stage 4 approval recorded" not in line for line in ctx.logs)
    assert fakes.probed == []


@pytest.mark.parametrize(("head", "bookkeeping"), [(APPROVED, False), (APPROVED[:12], False), (MERGED, True)])
def test_an_unmerged_head_relies_on_stage4_ci_without_probing(tmp_path: Path, head: str, bookkeeping: bool) -> None:
    ctx = _approved_ctx(tmp_path)
    fakes = Fakes([check("unit", "failure")])
    stopped, record = stage5_merged_head_gate(ctx, _seams(fakes, head=head, bookkeeping=bookkeeping))
    assert stopped is None
    assert fakes.probed == []
    assert record["state"] == "stage4"
    assert record["runs"] == [{"run_id": "5", "check_run_id": None, "name": "unit", "conclusion": "pass"}]
    assert any("Stage 5 relies on Stage 4 CI" in line for line in ctx.logs)


def test_a_green_merged_head_lets_stage5_calibrate(tmp_path: Path) -> None:
    ctx = _approved_ctx(tmp_path)
    stopped, record = stage5_merged_head_gate(ctx, _seams(Fakes([check()])))
    assert stopped is None
    assert record["state"] == "passed"
    assert any(MERGED in line and "unit (run 11, check run 1): success" in line for line in ctx.logs)


@pytest.mark.parametrize(
    "polls",
    [
        [[check(conclusion=None, status="in_progress")]],  # pending past the budget
        [[]],  # nothing registered
        [[check("lint", "cancelled", run_id=None)]],  # failed closed
    ],
)
def test_an_unsettled_merged_head_fails_stage5_closed_without_withdrawing(tmp_path: Path, polls: list) -> None:
    ctx = _approved_ctx(tmp_path)
    posted: list[str] = []
    seams = _seams(Fakes(*polls), budget=10.0, withdrawal=WithdrawalSeams(lambda _: [], lambda _p, b: posted.append(b)))
    stopped, record = stage5_merged_head_gate(ctx, seams)
    assert stopped is not None and stopped.status == StageStatus.FAILED
    assert MERGED in str(stopped.error)
    assert stopped.payload["merged_head_ci"] is record
    assert posted == []
    assert ctx.stage_results["review"].payload["consensus"]["approved"] is True


def test_an_unreadable_head_fails_stage5_closed(tmp_path: Path) -> None:
    fakes = Fakes([check()])
    stopped, record = stage5_merged_head_gate(_approved_ctx(tmp_path), _seams(fakes, head=None))
    assert stopped is not None and stopped.status == StageStatus.FAILED
    assert record["state"] == "unknown"
    assert fakes.probed == []


def test_a_withdrawn_consensus_restarts_stage4_on_resume_without_a_second_withdrawal(tmp_path: Path) -> None:
    ctx = _approved_ctx(tmp_path)
    consensus = {**ctx.stage_results["review"].payload["consensus"], "approved": False}
    withdrawn = {"merged_head": MERGED, "approved_head": APPROVED, "action_id": "ACT-WITHDRAW-1"}
    ctx.stage_results["review"].payload["consensus"] = {**consensus, "withdrawn": withdrawn}
    fakes = Fakes([check()])
    stopped, record = stage5_merged_head_gate(ctx, _seams(fakes))
    assert stopped is not None and stopped.status == StageStatus.SKIPPED
    assert stopped.payload["review_restart"] is True
    assert stopped.payload["consensus_withdrawal"] == withdrawn
    assert record["head_sha"] == MERGED and record["state"] == "failed"
    assert fakes.probed == []
    assert not (tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl").exists()


# --- the PR-own change fingerprint at the merged head ---------------------------------------------------------------

FINGERPRINT = "pf2:" + "f" * 40
OTHER_FINGERPRINT = "pf2:" + "e" * 40


def _fingerprinted_ctx(
    tmp_path: Path, merged_fingerprint: str | None, monkeypatch: pytest.MonkeyPatch
) -> tuple[FlowContext, list[tuple]]:
    """An approval of ``APPROVED`` recording ``FINGERPRINT``, and the recorded fingerprint computations.

    The merged head's fingerprint is ``merged_fingerprint``.
    """
    from sandbox_executor.flow.review import approval_coverage

    ctx = _approved_ctx(tmp_path)
    consensus = ctx.stage_results["review"].payload["consensus"]
    consensus.update(approval_fingerprint=FINGERPRINT, base_branch="main")
    computed: list[tuple] = []

    def fingerprint_of(repo_dir: str, head: str | None, base: str | None) -> str | None:
        computed.append((head, base))
        return merged_fingerprint

    monkeypatch.setattr(approval_coverage, "approval_fingerprint_of", fingerprint_of)
    return ctx, computed


def _withdrawal_seams(posted: list[str]) -> WithdrawalSeams:
    return WithdrawalSeams(lambda _: [], lambda _p, body: posted.append(body) or True)


def test_a_carried_approval_still_waits_for_ci_on_the_merged_head_and_proceeds_when_green(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, computed = _fingerprinted_ctx(tmp_path, FINGERPRINT, monkeypatch)
    fakes = Fakes([check()])
    stopped, record = stage5_merged_head_gate(ctx, _seams(fakes))
    assert stopped is None
    assert fakes.probed == [MERGED]  # CI on the exact merged head was awaited, the fingerprint notwithstanding.
    assert record["state"] == "passed"
    assert record["approval_carry_over"] == {
        "approved_head": APPROVED,
        "merged_head": MERGED,
        "approval_fingerprint": FINGERPRINT,
    }
    assert any("approval carried over" in line and f"waiting for CI on {MERGED}" in line for line in ctx.logs)
    assert computed == [(MERGED, "main")]


def test_a_carried_approval_with_red_ci_is_withdrawn_and_stage4_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, _ = _fingerprinted_ctx(tmp_path, FINGERPRINT, monkeypatch)
    posted: list[str] = []
    fakes = Fakes([check("unit", "failure")])
    stopped, record = stage5_merged_head_gate(ctx, _seams(fakes, withdrawal=_withdrawal_seams(posted)))
    assert fakes.probed == [MERGED]
    assert stopped is not None and stopped.status == StageStatus.SKIPPED
    assert stopped.payload["review_restart"] is True
    assert record["state"] == "failed" and record["approval_carry_over"]["merged_head"] == MERGED
    withdrawal = stopped.payload["consensus_withdrawal"]
    assert withdrawal["approval_fingerprint"] == FINGERPRINT
    assert "CI failed on merged head" in posted[0]
    assert ctx.stage_results["review"].payload["consensus"]["approved"] is False


def test_a_changed_pr_own_change_withdraws_the_approval_without_waiting_for_ci(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, _ = _fingerprinted_ctx(tmp_path, OTHER_FINGERPRINT, monkeypatch)
    posted: list[str] = []
    fakes = Fakes([check()])
    stopped, record = stage5_merged_head_gate(ctx, _seams(fakes, withdrawal=_withdrawal_seams(posted)))
    assert fakes.probed == []
    assert stopped is not None and stopped.status == StageStatus.SKIPPED
    assert stopped.payload["review_restart"] is True
    assert record["state"] == "failed" and record["head_sha"] == MERGED
    assert "changed since approval" in record["withdrawal_reason"]
    assert FINGERPRINT in record["withdrawal_reason"] and OTHER_FINGERPRINT in record["withdrawal_reason"]
    assert "changed since approval" in posted[0] and "CI failed" not in posted[0]
    rows = read_merged_rows(str(tmp_path / "holon-knowledge/ledger/executions.jsonl"))
    (row,) = rows
    assert row["action_id"].startswith("ACT-WITHDRAW-") and "changed since approval" in row["action"]
    assert ctx.stage_results["review"].payload["consensus"]["approved"] is False


def test_a_legacy_approval_without_a_fingerprint_keeps_todays_ci_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, computed = _fingerprinted_ctx(tmp_path, OTHER_FINGERPRINT, monkeypatch)
    del ctx.stage_results["review"].payload["consensus"]["approval_fingerprint"]
    fakes = Fakes([check()])
    stopped, record = stage5_merged_head_gate(ctx, _seams(fakes))
    assert stopped is None and record["state"] == "passed"
    assert fakes.probed == [MERGED]
    assert "approval_carry_over" not in record and "withdrawal_reason" not in record
    assert computed == []


def test_an_unreadable_merged_fingerprint_keeps_todays_ci_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, _ = _fingerprinted_ctx(tmp_path, None, monkeypatch)
    fakes = Fakes([check()])
    stopped, record = stage5_merged_head_gate(ctx, _seams(fakes))
    assert stopped is None and record["state"] == "passed"
    assert fakes.probed == [MERGED]
    assert "approval_carry_over" not in record


# --- 15-second poll ceiling and fail-early --------------------------------------------------------------------------


def test_merged_head_gate_caps_poll_interval_at_15s_and_backs_off_from_smaller_interval(tmp_path: Path) -> None:
    pending_run = [check("unit", None, "in_progress")]

    fakes_small = Fakes(pending_run)
    seams_small = MergedHeadSeams(
        policy=CiWaitPolicy(50.0, poll_interval=3.0, clock=lambda: fakes_small.now, sleep=fakes_small.sleep),
        probe=fakes_small.probe,
        registration_timeout=0.0,
    )
    record_small = gate_merged_head_ci(_ctx(tmp_path), str(tmp_path), MERGED, seams_small)
    assert record_small["state"] == "pending"
    assert fakes_small.sleeps == [3.0, 6.0, 12.0, 15.0, 14.0]

    fakes_default = Fakes(pending_run)
    record_default = _gate(tmp_path, fakes_default, budget=50.0)
    assert record_default["state"] == "pending"
    assert fakes_default.sleeps == [15.0, 15.0, 15.0, 5.0]

    fakes_clamped = Fakes(pending_run)
    seams_clamped = MergedHeadSeams(
        policy=CiWaitPolicy(35.0, poll_interval=30.0, clock=lambda: fakes_clamped.now, sleep=fakes_clamped.sleep),
        probe=fakes_clamped.probe,
        registration_timeout=0.0,
    )
    record_clamped = gate_merged_head_ci(_ctx(tmp_path), str(tmp_path), MERGED, seams_clamped)
    assert record_clamped["state"] == "pending"
    assert fakes_clamped.sleeps == [15.0, 15.0, 5.0]


def test_merged_head_gate_fails_early_while_other_checks_are_pending(tmp_path: Path) -> None:
    in_progress = check("unit", None, "in_progress", check_run_id=1, run_id="11")
    failed = check("lint", "failure", "completed", check_run_id=2, run_id="12")
    passed = check("unit", "success", "completed", check_run_id=1, run_id="11")

    fakes_initial = Fakes([failed, in_progress], [passed])
    rec_initial = _gate(tmp_path, fakes_initial)
    assert rec_initial["state"] == "failed"
    assert fakes_initial.sleeps == []
    assert len(fakes_initial.probed) == 1

    fakes_later = Fakes([in_progress], [failed, in_progress], [passed])
    rec_later = _gate(tmp_path, fakes_later)
    assert rec_later["state"] == "failed"
    assert fakes_later.sleeps == [15.0]
    assert len(fakes_later.probed) == 2


def test_the_stage4_ci_record_lives_with_the_gate_and_the_new_seams_default_to_gh(tmp_path: Path) -> None:
    from sandbox_executor.flow.review.merged_head_gate import stage4_ci_record

    ctx = _approved_ctx(tmp_path)
    checks = [{"name": "unit", "bucket": "pass", "link": "https://x/actions/runs/11/job/1"}]
    ctx.stage_results["review"].payload["ci_checks"] = {"checks": checks}
    record = stage4_ci_record(ctx, MERGED)
    assert record["state"] == "stage4" and record["runs"][0]["run_id"] == "11"
    assert MergedHeadSeams().detail_lookup is None and MergedHeadSeams().slowest_lookup is None
