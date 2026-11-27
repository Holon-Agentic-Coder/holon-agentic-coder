"""Stage 5's final-head CI report: every outcome from fakes, with no real gh, docker, git remote or model call."""

from __future__ import annotations

import functools
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.github import ci_final_head
from sandbox_executor.flow.github.ci_failure_detail import gh_detail_lookup, parse_run_id
from sandbox_executor.flow.github.ci_final_head import (
    FinalHeadSeams,
    check_final_head_ci,
    format_final_head_ci,
)
from sandbox_executor.flow.github.ci_wait import CiWaitPolicy
from sandbox_executor.flow.review.consensus_withdrawal import WithdrawalSeams
from sandbox_executor.flow.review.merged_head_gate import MergedHeadSeams
from sandbox_executor.flow.review_restart import stage5_merged_head_gate
from sandbox_executor.flow.stages.calibrate import run_calibrate_stage

from tests.flow.github.fake_github import FakeGitHub

BRANCH = "I-1-x/P-2-y/E-3-z/_"
GATE = "CI Timing Gate (Check <= 5 min)"


def _status(checks: list[dict], source: str = "github_cli") -> dict:
    failed = sum(c["bucket"] == "fail" for c in checks)
    pending = sum(c["bucket"] == "pending" for c in checks)
    return {
        "source": source,
        "total": len(checks),
        "failed": failed,
        "pending": pending,
        "all_passed": bool(checks) and not failed and not pending,
        "checks": checks,
    }


def _check(name: str, bucket: str) -> dict:
    return {"name": name, "bucket": bucket, "link": f"https://github.com/o/r/actions/runs/42/job/{name}"}


def _detail(name: str, steps: list[str] | None, seconds: float | None = 360.0) -> dict | None:
    if steps is None:
        return None
    gate = bool(steps) and all(s.startswith("CI Timing Gate") for s in steps)
    return {"name": name, "failed_steps": steps, "duration_seconds": seconds, "timing_gate": gate}


class Fakes:
    def __init__(self, *statuses: dict, steps: dict[str, list[str] | None] | None = None) -> None:
        self.statuses = list(statuses)
        self.probes = 0
        self.now = 0.0
        self.sleeps: list[float] = []
        self.steps = steps or {}

    def probe(self) -> dict:
        self.probes += 1
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def seams(self, budget: float = 600.0) -> FinalHeadSeams:
        return FinalHeadSeams(
            policy=CiWaitPolicy(budget, clock=lambda: self.now, sleep=self.sleep),
            probe=self.probe,
            detail_lookup=lambda check: _detail(check["name"], self.steps.get(check["name"])),
            registration_timeout=0,
            pushed_head=lambda: None,
        )


def _ctx(tmp_path: Path, **kwargs: bool) -> FlowContext:
    return FlowContext(repo_dir=str(tmp_path), execution_branch=BRANCH, agent="a", model="m", **kwargs)


def _run(tmp_path: Path, fakes: Fakes, budget: float = 600.0, **kwargs: bool) -> dict:
    return check_final_head_ci(_ctx(tmp_path, **kwargs), str(tmp_path), BRANCH, fakes.seams(budget))


def test_all_green_is_success(tmp_path: Path) -> None:
    record = _run(tmp_path, Fakes(_status([_check("unit", "pass")])))
    assert record["state"] == "success"
    assert format_final_head_ci(record) == "final head CI: success"


def test_failing_test_job_lists_its_name(tmp_path: Path) -> None:
    fakes = Fakes(_status([_check("unit", "fail")]), steps={"unit": ["Run tests"]})
    record = _run(tmp_path, fakes)
    assert record["state"] == "failed"
    assert record["failed_checks"] == ["unit"]
    assert "unit" in format_final_head_ci(record)


def test_timing_gate_only_failure_fails_the_final_head_check(tmp_path: Path) -> None:
    fakes = Fakes(_status([_check("macos-unit", "fail")]), steps={"macos-unit": [GATE]})
    record = _run(tmp_path, fakes)
    assert record["state"] == "failed"
    assert record["failed_checks"] == ["macos-unit"]
    assert record["failed_details"][0]["timing_gate"] is True
    assert format_final_head_ci(record) == "final head CI: failed: macos-unit (CI Timing Gate, 6.0 min)"
    assert "timing gate only" not in format_final_head_ci(record)


def test_mixed_failure_is_a_test_failure(tmp_path: Path) -> None:
    fakes = Fakes(_status([_check("unit", "fail")]), steps={"unit": [GATE, "Run tests"]})
    assert _run(tmp_path, fakes)["state"] == "failed"


def test_unknown_steps_are_a_plain_failure(tmp_path: Path) -> None:
    assert _run(tmp_path, Fakes(_status([_check("unit", "fail")])))["state"] == "failed"


def test_pending_then_green_within_budget_is_success(tmp_path: Path) -> None:
    fakes = Fakes(_status([_check("unit", "pending")]), _status([_check("unit", "pass")]))
    assert _run(tmp_path, fakes)["state"] == "success"
    assert fakes.sleeps


def test_pending_past_budget_is_unknown_not_success(tmp_path: Path) -> None:
    fakes = Fakes(_status([_check("unit", "pending")]))
    record = _run(tmp_path, fakes, budget=30.0)
    assert record["state"] == "unknown"
    assert "unit" in record["reason"]
    assert record["ci_wait"]["state"] == "pending"


def test_local_tests_source_is_unknown(tmp_path: Path) -> None:
    fakes = Fakes(_status([_check("local_tests", "pass")], source="local_tests"))
    with patch.object(ci_final_head, "_wait_for_ci_registration"):
        assert _run(tmp_path, fakes)["state"] == "unknown"


def test_dry_run_and_skip_push_do_not_probe_or_wait(tmp_path: Path) -> None:
    for flag in ({"dry_run": True}, {"skip_push": True}):
        fakes = Fakes(_status([_check("unit", "pending")]))
        assert _run(tmp_path, fakes, **flag)["state"] == "skipped"
        assert fakes.probes == 0
        assert fakes.sleeps == []


def test_probe_exception_is_unknown(tmp_path: Path) -> None:
    fakes = Fakes(_status([]))
    fakes.probe = MagicMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    record = _run(tmp_path, fakes)
    assert record["state"] == "unknown"
    assert "boom" in record["reason"]


def test_parse_run_id_and_gh_step_lookup() -> None:
    assert parse_run_id("https://github.com/o/r/actions/runs/123/job/9") == "123"
    assert parse_run_id("https://example.com") is None
    steps = [{"name": "s", "conclusion": "success"}, {"name": "CI Timing Gate (x)", "conclusion": "failure"}]
    job = {"name": "u", "steps": steps, "startedAt": "2026-01-01T00:00:00Z", "completedAt": "2026-01-01T00:06:00Z"}
    jobs = json.dumps({"jobs": [job]})
    run = MagicMock(return_value=SimpleNamespace(returncode=0, stdout=jobs))
    lookup = gh_detail_lookup("/tmp", {}, run)
    detail = lookup({"name": "u", "link": "https://x/actions/runs/7/job/1"})
    assert detail == {
        "name": "u",
        "failed_steps": ["CI Timing Gate (x)"],
        "duration_seconds": 360.0,
        "timing_gate": True,
    }
    assert lookup({"name": "u", "link": "nope"}) is None
    run.side_effect = OSError("no gh")
    assert lookup({"name": "u", "link": "https://x/actions/runs/7/job/1"}) is None


def _run_stage(tmp_path: Path, record: dict) -> object:
    ctx = _approved_ctx(tmp_path)
    report = MagicMock(calibrated_branch=BRANCH + "calibrated")
    report.to_dict.return_value = {}
    ok = SimpleNamespace(returncode=0, stdout="", stderr="")
    with (
        patch(
            "sandbox_executor.flow.stages.calibrate.stage5_merged_head_gate", return_value=(None, {"state": "stage4"})
        ),
        patch("sandbox_executor.flow.stages.calibrate._checkout_execution_branch", return_value=None),
        patch("sandbox_executor.flow.stages.calibrate.sync_before_calibration", return_value=None),
        patch("sandbox_executor.flow.stages.calibrate.run_calibrate", return_value=report),
        patch("sandbox_executor.flow.stages.calibrate.run_git", return_value=ok),
        patch("sandbox_executor.flow.calibrate_sync.run_git", return_value=ok),
        patch("sandbox_executor.flow.stages.calibrate.check_final_head_ci", return_value=record),
    ):
        return run_calibrate_stage(ctx)


def test_stage_is_blocked_by_a_red_final_head_and_names_the_checks(tmp_path: Path) -> None:
    detail = _detail("macos-unit", [GATE])
    failed = {"state": "failed", "failed_checks": ["macos-unit"], "failed_details": [detail], "ci_wait": None}
    result = _run_stage(tmp_path, failed)
    assert result.status == StageStatus.FAILED
    assert "macos-unit (CI Timing Gate, 6.0 min)" in result.error
    assert result.payload["final_head_ci"] == failed
    assert result.payload["ci_verdict"]["verdict"] == "red"


def test_stage_succeeds_on_a_green_final_head_after_the_push(tmp_path: Path) -> None:
    result = _run_stage(tmp_path, {"state": "success", "failed_checks": [], "ci_wait": None})
    assert result.status == StageStatus.SUCCESS
    assert result.payload["ci_verdict"]["verdict"] == "green"


def test_stage_with_unverified_ci_is_never_reported_merge_ready(tmp_path: Path) -> None:
    result = _run_stage(tmp_path, {"state": "unknown", "failed_checks": [], "reason": "pending", "ci_wait": None})
    report = result.payload["ci_verdict"]["report"]
    assert result.payload["ci_verdict"]["verdict"] == "unverified"
    assert "not verified" in report
    assert "Not merge-ready" in report


def _commit_check_runs(*runs: dict) -> FakeGitHub:
    stdout = "".join(json.dumps(run) + "\n" for run in runs)
    return FakeGitHub(cli=lambda args, cwd: subprocess.CompletedProcess(["gh", *args], 0, stdout, ""))


def test_stale_prior_head_checks_are_not_reported_as_success(tmp_path: Path) -> None:
    fakes = Fakes(_status([_check("hygiene", "pass")]))
    run = {"id": 1, "name": "unit", "status": "completed", "conclusion": "success", "head_sha": "new"}
    github = _commit_check_runs(run, {**run, "id": 2, "conclusion": "failure", "head_sha": "old"})
    seams = FinalHeadSeams(
        policy=CiWaitPolicy(600.0, clock=lambda: fakes.now, sleep=fakes.sleep),
        probe=fakes.probe,
        detail_lookup=lambda check: None,
        registration_timeout=60,
        pushed_head=lambda: "new",
        pr_head=lambda: "old",
        github=github,
    )
    record = check_final_head_ci(_ctx(tmp_path), str(tmp_path), BRANCH, seams)
    assert record["state"] == "success"
    assert fakes.sleeps == []
    assert fakes.probes == 0
    assert github.methods() == ["gh"]


def test_pr_head_never_matching_pushed_head_is_unknown(tmp_path: Path) -> None:
    fakes = Fakes(_status([_check("hygiene", "pass")]))
    seams = FinalHeadSeams(
        policy=CiWaitPolicy(600.0, clock=lambda: fakes.now, sleep=fakes.sleep),
        probe=fakes.probe,
        registration_timeout=20,
        pushed_head=lambda: "new",
        pr_head=lambda: "old",
        github=_commit_check_runs(),
    )
    record = check_final_head_ci(_ctx(tmp_path), str(tmp_path), BRANCH, seams)
    assert record["state"] == "unknown"
    assert "pull request head lagged the pushed head" in record["reason"]
    assert fakes.probes == 0
    assert len(fakes.sleeps) == 1


def test_failure_ends_the_wait_early_while_other_checks_are_pending(tmp_path: Path) -> None:
    fakes = Fakes(
        _status([_check("lint", "fail"), _check("unit", "pending")]),
        _status([_check("lint", "fail"), _check("unit", "fail")]),
    )
    record = _run(tmp_path, fakes)
    assert record["state"] == "failed"
    assert record["failed_checks"] == ["lint"]
    assert fakes.sleeps == []
    assert fakes.probes == 1


# --- the merged-head CI gate runs before any calibration commit is pushed --------------------------------------------

APPROVED = "a" * 40
MERGED = "b" * 40


def _approved_ctx(tmp_path: Path) -> FlowContext:
    (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True, exist_ok=True)
    ctx = _ctx(tmp_path)
    ctx.target_worktree = str(tmp_path)
    ctx.stage_results["execute"] = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS)
    consensus = {"approved": True, "target_commit": APPROVED, "pr_number": 7}
    checks = {"checks": [{"name": "unit", "bucket": "pass", "link": "https://x/actions/runs/5/job/1"}]}
    ctx.stage_results["review"] = StageResult(
        stage=FlowStage.REVIEW,
        status=StageStatus.APPROVED,
        payload={"consensus": consensus, "posting": {"dual_posted": True}, "ci_checks": checks},
    )
    return ctx


def _run_gated_stage(tmp_path: Path, head: str, conclusion: str) -> tuple[FlowContext, object, MagicMock, list[str]]:
    ctx = _approved_ctx(tmp_path)
    probed: list[str] = []
    comments: list[str] = []

    def probe(sha: str) -> list[dict]:
        probed.append(sha)
        return [{"check_run_id": 1, "name": "unit", "status": "completed", "conclusion": conclusion, "run_id": "9"}]

    seams = MergedHeadSeams(
        policy=CiWaitPolicy(60.0, clock=lambda: 0.0, sleep=lambda _s: None),
        probe=probe,
        registration_timeout=0,
        head_reader=lambda: head,
        bookkeeping_move=lambda _a, _h: False,
        withdrawal=WithdrawalSeams(lister=lambda _pr: list(comments), poster=lambda _t, b: comments.append(b) or True),
    )
    report = MagicMock(calibrated_branch=BRANCH + "calibrated")
    report.to_dict.return_value = {}
    ok = SimpleNamespace(returncode=0, stdout="", stderr="")
    green = {"state": "success", "failed_checks": [], "ci_wait": None}
    publish = MagicMock(return_value=True)
    with (
        patch("sandbox_executor.flow.stages.calibrate._stage5_conflict_gate", return_value=None),
        patch(
            "sandbox_executor.flow.stages.calibrate.stage5_merged_head_gate",
            functools.partial(stage5_merged_head_gate, seams=seams),
        ),
        patch("sandbox_executor.flow.stages.calibrate._checkout_execution_branch", return_value=None),
        patch("sandbox_executor.flow.stages.calibrate.sync_before_calibration", return_value=None),
        patch("sandbox_executor.flow.stages.calibrate.run_calibrate", return_value=report),
        patch("sandbox_executor.flow.stages.calibrate.run_git", return_value=ok),
        patch("sandbox_executor.flow.calibrate_sync.run_git", return_value=ok),
        patch("sandbox_executor.flow.stages.calibrate.publish_calibration", publish),
        patch("sandbox_executor.flow.stages.calibrate.check_final_head_ci", return_value=green),
        patch("sandbox_executor.flow.stages.calibrate.reverify_and_report", return_value={"verdict": "green"}),
    ):
        result = run_calibrate_stage(ctx)
    return ctx, result, publish, probed


def test_an_unmerged_green_head_calibrates_as_before_without_an_extra_probe(tmp_path: Path) -> None:
    ctx, result, publish, probed = _run_gated_stage(tmp_path, APPROVED, "failure")
    assert result.status == StageStatus.SUCCESS
    publish.assert_called_once()
    assert probed == []
    assert result.payload["merged_head_ci"]["state"] == "stage4"
    assert any(f"Stage 5 relies on Stage 4 CI for head {APPROVED}" in line for line in ctx.logs)


def test_a_red_merged_head_pushes_no_calibration_and_asks_for_a_review_restart(tmp_path: Path) -> None:
    ctx, result, publish, probed = _run_gated_stage(tmp_path, MERGED, "failure")
    publish.assert_not_called()
    assert probed == [MERGED]
    assert result.status == StageStatus.SKIPPED
    assert result.payload["review_restart"] is True
    assert result.payload["merged_head_ci"]["head_sha"] == MERGED
    assert result.payload["consensus_withdrawal"]["merged_head"] == MERGED
    assert ctx.review_iterations_done == 0


def test_a_green_merged_head_is_calibrated_and_the_record_names_its_runs(tmp_path: Path) -> None:
    ctx, result, publish, probed = _run_gated_stage(tmp_path, MERGED, "success")
    publish.assert_called_once()
    assert probed == [MERGED]
    assert result.status == StageStatus.SUCCESS
    record = result.payload["merged_head_ci"]
    assert (record["state"], record["head_sha"]) == ("passed", MERGED)
    assert record["runs"] == [{"run_id": "9", "check_run_id": 1, "name": "unit", "conclusion": "success"}]
    assert any(MERGED in line and "unit (run 9, check run 1): success" in line for line in ctx.logs)


# --- an approval carried over a target merge is not a bookkeeping move ------------------------------------------------


def _carried_timing_check(tmp_path: Path, changed: list[str]) -> ci_final_head._FinalHeadCheck:
    """A final-head check whose approval of A carried over by fingerprint to the merged head B."""
    approved, merged = "a" * 40, "b" * 40
    context = FlowContext(repo_dir=str(tmp_path), execution_branch=BRANCH, agent="a", model="m")
    consensus = {
        "approved": True,
        "target_commit": approved,
        "approval_fingerprint": "pf1:" + "f" * 40,
        "carried_from": approved,
        "carried_to": merged,
    }
    context.stage_results["review"] = StageResult(
        stage=FlowStage.REVIEW, status=StageStatus.APPROVED, payload={"consensus": consensus}
    )
    seams = ci_final_head.FinalHeadSeams(pr_head=lambda: merged, changed_paths=lambda _a, _h: list(changed))
    return ci_final_head._FinalHeadCheck(context, str(tmp_path), BRANCH, seams, FakeGitHub())


def test_a_fingerprint_carried_merged_head_is_not_eligible_for_the_automatic_timing_rerun(tmp_path: Path) -> None:
    record = {"failed_checks": ["unit"], "failed_details": [{"name": "unit", "timing_gate": True}]}
    ci_status = {"checks": [{"name": "unit", "link": "https://github.com/o/r/actions/runs/77/job/1"}]}
    merged_main = ["src/from_main.py", "holon-knowledge/ledger/executions.jsonl"]

    carried = _carried_timing_check(tmp_path, merged_main)
    bookkeeping = _carried_timing_check(tmp_path, ["holon-knowledge/ledger/executions.jsonl"])

    # ci_final_head stays bookkeeping-only: a merge of main is not a bookkeeping move, even when the approval carried.
    assert ci_final_head._eligible_run_ids(carried, record, ci_status) is None
    assert ci_final_head._eligible_run_ids(bookkeeping, record, ci_status) == ["77"]  # The positive control.
    assert ci_final_head._eligible_run_ids(_carried_timing_check(tmp_path, []), record, ci_status) == [
        "77"
    ]  # Unchanged.


# --- 15-second poll ceiling, fail-early, and timing-rerun pending guard ---------------------------------------------


def test_final_head_ci_caps_poll_interval_at_15s_and_fails_early(tmp_path: Path) -> None:
    pending = _status([_check("unit", "pending")])
    passed = _status([_check("unit", "pass")])
    fakes_cap = Fakes(pending, pending, pending, passed)
    rec_cap = _run(tmp_path, fakes_cap)
    assert rec_cap["state"] == "success"
    assert fakes_cap.sleeps == [15.0, 15.0, 15.0]

    mixed = _status([_check("lint", "fail"), _check("unit", "pending")])
    both_failed = _status([_check("lint", "fail"), _check("unit", "fail")])
    fakes_initial = Fakes(mixed, both_failed)
    rec_initial = _run(tmp_path, fakes_initial)
    assert rec_initial["state"] == "failed"
    assert rec_initial["failed_checks"] == ["lint"]
    assert fakes_initial.sleeps == []
    assert fakes_initial.probes == 1

    fakes_later = Fakes(_status([_check("lint", "pending"), _check("unit", "pending")]), mixed, both_failed)
    rec_later = _run(tmp_path, fakes_later)
    assert rec_later["state"] == "failed"
    assert rec_later["failed_checks"] == ["lint"]
    assert fakes_later.sleeps == [15.0]
    assert fakes_later.probes == 2


def test_timing_gate_rerun_is_ineligible_while_sibling_checks_are_pending(tmp_path: Path) -> None:
    """Fail-early exit with a pending sibling check must not dispatch an automatic timing-gate rerun."""
    record = {"failed_checks": ["unit"], "failed_details": [{"name": "unit", "timing_gate": True}]}
    bookkeeping = _carried_timing_check(tmp_path, ["holon-knowledge/ledger/executions.jsonl"])
    pending_status = _status([_check("unit", "fail"), _check("lint", "pending")])
    settled_status = _status([_check("unit", "fail"), _check("lint", "pass")])

    assert ci_final_head._eligible_run_ids(bookkeeping, record, pending_status) is None
    assert ci_final_head._eligible_run_ids(bookkeeping, record, settled_status) == ["42"]

    dispatched: list[str] = []
    fakes = Fakes(pending_status, steps={"unit": [GATE]})
    seams = FinalHeadSeams(
        policy=CiWaitPolicy(600.0, clock=lambda: fakes.now, sleep=fakes.sleep),
        probe=fakes.probe,
        detail_lookup=lambda c: _detail(c["name"], fakes.steps.get(c["name"])),
        registration_timeout=0,
        pushed_head=lambda: None,
        pr_head=lambda: "b" * 40,
        approved_head=lambda: "a" * 40,
        changed_paths=lambda _a, _h: ["holon-knowledge/ledger/executions.jsonl"],
        rerun_dispatcher=lambda run_id: dispatched.append(run_id) or True,
    )
    out = check_final_head_ci(_ctx(tmp_path), str(tmp_path), BRANCH, seams)
    assert out["state"] == "failed"
    assert "timing_rerun" not in out
    assert dispatched == []
