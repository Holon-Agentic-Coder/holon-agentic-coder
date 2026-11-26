"""Stage 4 CI inspection: the reviewer-objected fast probe, the post-review gate and the fallback probe."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from sandbox_executor.flow import FlowContext
from sandbox_executor.flow.github.ci_wait import CiWaitPolicy
from sandbox_executor.flow.review import ci_inspection
from sandbox_executor.flow.review.ci_inspection import CiInspectionInputs, inspect_ci
from sandbox_executor.review_accounting import accounting_of

GREEN = {"all_passed": True, "passed": 1, "failed": 0, "pending": 0, "total": 1, "source": "github_cli"}
RED = {"all_passed": False, "passed": 0, "failed": 1, "pending": 0, "total": 1, "source": "github_cli"}
PENDING = {"all_passed": False, "passed": 0, "failed": 0, "pending": 1, "total": 1, "source": "github_cli"}


class _TestGate:
    """A Stage 3 evidence fake: a fixed pass rate, failed or not, and whether CI registration is still awaited."""

    def __init__(self, tests_failed: bool = False, needs_registration: bool = False) -> None:
        self.tests_failed = tests_failed
        self.needs_registration = needs_registration

    def evidence(self, ci_status: dict[str, Any], dry_run: bool = False) -> tuple[float, bool]:
        del ci_status, dry_run
        return (0.0 if self.tests_failed else 1.0), self.tests_failed

    def needs_ci_registration(self) -> bool:
        return self.needs_registration


def _inputs(ci: dict[str, Any], **kwargs: Any) -> CiInspectionInputs:
    base: dict[str, Any] = {
        "repo_dir": "/repo",
        "review_target": "feature",
        "proxy_env": {},
        "github": MagicMock(available=True),
        "test_gate": _TestGate(),
        "ci_policy": CiWaitPolicy(60.0, 1.0, clock=lambda: 0.0, sleep=lambda _s: None),
        "probe_ci": lambda: dict(ci),
        "wait_registration": MagicMock(),
        "pr_allowed": True,
        "ci_registration_timeout": 30.0,
        "ci_poll_interval": 1.0,
        "clock": lambda: 0.0,
        "sleep": lambda _s: None,
        "iteration_timer": MagicMock(remaining_capped=MagicMock(return_value=7.0)),
    }
    return CiInspectionInputs(**{**base, **kwargs})


def _ctx(tmp_path: Path) -> FlowContext:
    return FlowContext(repo_dir=str(tmp_path), agent="a", model="m", token_reduce=False)


def test_reviewer_objected_probes_once_and_reports_only_a_failure(tmp_path):
    stale = MagicMock()
    pending = inspect_ci(_inputs(PENDING, reviewer_objected=True, ci_wait=stale), _ctx(tmp_path))
    assert (pending.ci_green, pending.ci_finding, pending.decides, pending.ci_wait) == (None, False, True, stale)

    red = inspect_ci(_inputs(RED, reviewer_objected=True, ci_wait=stale), _ctx(tmp_path))
    assert (red.ci_green, red.ci_finding, red.ci_wait, red.tests_not_measured) == (False, True, None, False)


def test_post_review_gate_waits_for_registration_and_credits_the_settle_wait(tmp_path):
    waited = MagicMock(waited_seconds=12.0, exhausted=True)
    inputs = _inputs(PENDING, test_gate=_TestGate(needs_registration=True))
    with patch.object(ci_inspection, "settle_pending_ci", return_value=(dict(GREEN), waited)) as settle:
        result = inspect_ci(inputs, _ctx(tmp_path))

    inputs.wait_registration.assert_called_once()
    assert inputs.wait_registration.call_args.args[4] == 7.0  # Capped by the iteration timer.
    settle.assert_called_once()
    inputs.iteration_timer.credit.assert_called_once_with(12.0)
    assert (result.ci_green, result.ci_finding, result.ci_wait, result.decides) == (True, False, waited, True)


def test_post_review_gate_without_a_wait_credits_nothing_and_drops_a_stale_wait(tmp_path):
    inputs = _inputs(RED, ci_wait=MagicMock())
    result = inspect_ci(inputs, _ctx(tmp_path))

    inputs.wait_registration.assert_not_called()
    inputs.iteration_timer.credit.assert_called_once_with(0.0)
    assert (result.ci_green, result.ci_finding, result.ci_wait) == (False, True, None)


def test_fallback_under_dry_run_decides_nothing(tmp_path):
    result = inspect_ci(_inputs(GREEN, effective_dry_run=True), _ctx(tmp_path))
    assert result.decides is False
    assert result.ci_finding is False


def test_fallback_on_a_dirty_merge_still_reports_ci(tmp_path):
    inputs = _inputs(RED, is_dirty=True)
    result = inspect_ci(inputs, _ctx(tmp_path))

    inputs.iteration_timer.credit.assert_not_called()
    assert (result.ci_green, result.ci_finding, result.decides) == (False, True, True)


def test_fallback_counts_failed_local_tests(tmp_path):
    result = inspect_ci(_inputs(GREEN, is_dirty=True, test_gate=_TestGate(tests_failed=True)), _ctx(tmp_path))
    assert (result.tests_failed, result.ci_green, result.ci_finding) == (True, False, True)


def test_the_noise_rerun_seams_default_to_none_and_change_nothing(tmp_path):
    inputs = _inputs(RED)
    assert inputs.detail_lookup is None and inputs.rerun_failed is None
    seamed = _inputs(RED, detail_lookup=MagicMock(), rerun_failed=MagicMock())
    assert inspect_ci(seamed, _ctx(tmp_path)) == inspect_ci(inputs, _ctx(tmp_path))
    seamed.detail_lookup.assert_not_called()
    seamed.rerun_failed.assert_not_called()


def test_a_settled_pipeline_counts_one_run_per_head_and_run_id(tmp_path):
    context = _ctx(tmp_path)
    run = {"name": "test", "bucket": "fail", "link": "https://github.com/o/r/actions/runs/42/job/7"}
    red = {**RED, "checks": [run]}
    inspect_ci(_inputs(red, reviewer_objected=True), context)
    inspect_ci(_inputs(red), context)  # The same run inspected again (a resume or a rerun) is no new run.
    inspect_ci(_inputs(GREEN, is_dirty=True), context)  # No run id: counted once per head.
    assert accounting_of(context)["pipeline_runs"] == 2


def test_a_rerun_without_run_ids_that_turns_green_is_no_new_run(tmp_path):
    context = _ctx(tmp_path)
    inspect_ci(_inputs(RED, is_dirty=True), context)
    inspect_ci(_inputs(GREEN, is_dirty=True), context)
    assert accounting_of(context)["pipeline_runs"] == 1


def test_a_pending_or_local_status_is_no_pipeline_run(tmp_path):
    context = _ctx(tmp_path)
    inspect_ci(_inputs(PENDING, reviewer_objected=True), context)
    inspect_ci(_inputs({**GREEN, "source": "local_tests"}, is_dirty=True), context)
    assert accounting_of(context) is None
