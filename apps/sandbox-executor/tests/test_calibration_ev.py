"""Hermetic tests for a plan's final summary row: flow order by instant and superseded sync conflicts."""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from sandbox_executor.calibration import PredictedMetrics, parse_actual_metrics
from sandbox_executor.calibration_ev import evaluate_dual_ev_outcomes
from sandbox_executor.calibration_outcome import (
    is_converged_record,
    latest_summary_row,
    summary_row_order_key,
)

PLAN = "I-t/P-t/_"
IMPACT, COST = 50.0, 4.0


def _row(status: str, created_at: str, **extra: Any) -> dict[str, Any]:
    return {"entry_type": "summary", "plan_branch": PLAN, "status": status, "created_at": created_at, **extra}


def _conflict_flow(
    *, conflict_exit_code: int = 0, resolved: bool = True, resolved_status: str = "halted_for_human"
) -> list[dict[str, Any]]:
    rows = [
        _row("success", "2026-11-20T01:00:00+00:00", execution_id="E-1", exit_code=0, ledger_revision=1),
        _row(
            "sync_conflict",
            "2026-11-20T01:05:00+00:00",
            execution_id="E-1",
            exit_code=conflict_exit_code,
            ledger_revision=2,
        ),
    ]
    if resolved:
        rows.append(_row(resolved_status, "2026-11-20T02:00:00Z", execution_id="REV-1"))
    return rows


def _evaluate(rows: list[dict[str, Any]]) -> Any:
    return evaluate_dual_ev_outcomes(rows, "E-1", PLAN, IMPACT, COST)


class TestResolvedSyncConflict(unittest.TestCase):
    def test_resolved_conflict_converges_with_full_impact(self) -> None:
        for resolved_status in ("halted_for_human", "approved"):
            with self.subTest(resolved_status=resolved_status):
                metrics = _evaluate(_conflict_flow(resolved_status=resolved_status))
                self.assertEqual(metrics.p_success_clean, 1.0)
                self.assertEqual(metrics.impact_clean, IMPACT)
                self.assertEqual(metrics.p_success_with_bugs, 1.0)
                self.assertEqual(metrics.impact_with_bugs, IMPACT)
                self.assertEqual(metrics.ev_with_bugs, metrics.ev_clean)
                self.assertEqual(metrics.bug_ev_drag, 0.0)

    def test_resolved_conflict_with_nonzero_exit_is_not_a_bug(self) -> None:
        metrics = _evaluate(_conflict_flow(conflict_exit_code=1))
        self.assertEqual(metrics.p_success_with_bugs, 1.0)
        self.assertEqual(metrics.bug_ev_drag, 0.0)


class TestUnresolvedSyncConflict(unittest.TestCase):
    def _assert_failed(self, metrics: Any) -> None:
        self.assertEqual(metrics.p_success_clean, 0.0)
        self.assertEqual(metrics.impact_clean, 0.0)
        self.assertEqual(metrics.p_success_with_bugs, 0.0)
        self.assertEqual(metrics.ev_with_bugs, -COST)

    def test_final_conflict_with_copied_zero_exit_scores_failed(self) -> None:
        self._assert_failed(_evaluate(_conflict_flow(resolved=False)))

    def test_final_conflict_with_nonzero_exit_scores_failed(self) -> None:
        self._assert_failed(_evaluate(_conflict_flow(conflict_exit_code=1, resolved=False)))


class TestOrderingByInstant(unittest.TestCase):
    def test_offset_and_z_formats_order_by_instant_not_string(self) -> None:
        failed = _row("failed", "2026-11-20T09:00:00+00:00", execution_id="E-1", exit_code=1)
        middle = _row("failed", "2026-11-20T10:30:00+01:00", execution_id="E-1", exit_code=1)
        success = _row("success", "2026-11-20T10:00:00Z", execution_id="E-1", exit_code=0)
        rows = [success, middle, failed]
        self.assertEqual(max(r["created_at"] for r in rows), middle["created_at"])
        self.assertIs(latest_summary_row(rows), success)
        self.assertEqual(_evaluate(rows).p_success_clean, 1.0)

    def test_revision_breaks_a_tie_at_the_same_instant(self) -> None:
        later = _row("success", "2026-11-20T10:00:00Z", exit_code=0, ledger_revision=2)
        earlier = _row("failed", "2026-11-20T10:00:00+00:00", exit_code=1, ledger_revision=1)
        self.assertIs(latest_summary_row([later, earlier]), later)

    def test_full_tie_keeps_ledger_line_order(self) -> None:
        first = _row("failed", "2026-11-20T10:00:00Z", exit_code=1)
        second = _row("success", "2026-11-20T10:00:00Z", exit_code=0)
        self.assertIs(latest_summary_row([first, second]), second)

    def test_missing_or_malformed_timestamp_sorts_earliest(self) -> None:
        dated = _row("success", "2026-11-20T10:00:00", exit_code=0)
        self.assertLess(summary_row_order_key({"created_at": "not-a-date"}), summary_row_order_key(dated))
        self.assertLess(summary_row_order_key({}), summary_row_order_key(dated))
        self.assertEqual(summary_row_order_key({"ledger_revision": "x"})[1], 1)
        self.assertIsNone(latest_summary_row([]))


class TestRealFailureStillCounts(unittest.TestCase):
    def test_review_approval_does_not_turn_a_failed_execution_into_a_delivery(self) -> None:
        for resolved_status in ("halted_for_human", "approved"):
            with self.subTest(resolved_status=resolved_status):
                rows = [
                    _row("failed", "2026-11-20T01:00:00Z", execution_id="E-1", exit_code=1),
                    _row(resolved_status, "2026-11-20T02:00:00Z", execution_id="REV-1"),
                ]
                metrics = _evaluate(rows)
                self.assertEqual(metrics.p_success_clean, 0.0)
                self.assertEqual(metrics.p_success_with_bugs, 0.0)
                self.assertEqual(metrics.ev_with_bugs, -COST)
                self.assertEqual(metrics.bug_ev_drag, 0.0)

    def test_failed_execution_attempt_after_delivery_is_a_bug(self) -> None:
        rows = [
            _row("success", "2026-11-20T01:00:00Z", execution_id="E-1", exit_code=0),
            _row("failed", "2026-11-20T02:00:00Z", execution_id="E-2", exit_code=1, summary="3 tests failed"),
        ]
        metrics = _evaluate(rows)
        self.assertEqual(metrics.p_success_clean, 1.0)
        self.assertEqual(metrics.p_success_with_bugs, 0.0)
        self.assertEqual(metrics.bug_ev_drag, IMPACT)

    def test_failed_execution_resolved_by_a_later_delivery_is_not_a_bug(self) -> None:
        rows = [
            _row("failed", "2026-11-20T01:00:00Z", execution_id="E-1", exit_code=1),
            _row("success", "2026-11-20T02:00:00Z", execution_id="E-2", exit_code=0),
        ]
        metrics = _evaluate(rows)
        self.assertEqual(metrics.p_success_with_bugs, 1.0)
        self.assertEqual(metrics.bug_ev_drag, 0.0)


class TestSupersedingRevision(unittest.TestCase):
    def test_later_revision_of_the_same_execution_replaces_its_success(self) -> None:
        rows = [
            _row("success", "2026-11-20T01:00:00Z", execution_id="E-1", exit_code=0, ledger_revision=1),
            _row("failed", "2026-11-20T02:00:00Z", execution_id="E-1", exit_code=1, ledger_revision=2),
        ]
        metrics = _evaluate(rows)
        self.assertEqual(metrics.p_success_clean, 0.0)
        self.assertEqual(metrics.bug_ev_drag, 0.0)


class TestParseActualMetricsFinalRow(unittest.TestCase):
    def test_resolved_conflict_flow_reports_success(self) -> None:
        predicted = PredictedMetrics(p_success=0.95, entropy=1.0, impact=IMPACT, cost=COST, learning_value=2.0, ev=46.0)

        def mock_subp(cmd: list[str], **kwargs: object) -> MagicMock:
            if "diff" in cmd:
                return MagicMock(returncode=0, stdout="1 file changed, 10 insertions(+), 2 deletions(-)\n")
            return MagicMock(returncode=0, stdout="")

        for resolved_status in ("halted_for_human", "approved"):
            with self.subTest(resolved_status=resolved_status):
                jsonl = "".join(json.dumps(row) + "\n" for row in _conflict_flow(resolved_status=resolved_status))
                with (
                    patch("sandbox_executor.calibration.actual.read_git_file", return_value=jsonl),
                    patch("subprocess.run", side_effect=mock_subp),
                ):
                    actual, _meta = parse_actual_metrics("E-1", PLAN, predicted)
                self.assertEqual(actual.p_success, 1.0)
                self.assertEqual(actual.exit_code, 0)
                self.assertEqual(actual.p_success_with_bugs, 1.0)
                self.assertEqual(actual.bug_ev_drag, 0.0)


class TestConvergedRecords(unittest.TestCase):
    def test_converged_record_statuses(self) -> None:
        # Both fresh 'approved' and legacy 'halted_for_human' converge
        self.assertTrue(is_converged_record({"status": "approved"}))
        self.assertTrue(is_converged_record({"status": "halted_for_human"}))
        self.assertTrue(is_converged_record({"status": "success"}))
        self.assertTrue(is_converged_record({"status": "decomposed", "integrated": True}))
        self.assertFalse(is_converged_record({"status": "failed"}))
        self.assertFalse(is_converged_record({"status": "sync_conflict"}))
