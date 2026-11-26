"""Report rendering, report model, evaluated-SHA staleness and public surface of ``sandbox_executor.calibration``.

The rendering golden (``fixtures/calibration_report_golden.txt``) was produced by the single-module ``calibration.py``
before it became a package: every later change to the report renderer must keep the output byte-identical.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import sandbox_executor.calibration as calibration
from sandbox_executor.calibration import (
    ActualMetrics,
    CalibrationDeltas,
    CalibrationReport,
    EntropyFactors,
    PredictedMetrics,
    compute_calibration_deltas,
    format_markdown_report,
    is_calibration_stale,
    parse_evaluated_commit_sha,
    verify_calibration_freshness,
)

GOLDEN = Path(__file__).parent / "fixtures" / "calibration_report_golden.txt"

# Every public name importable from sandbox_executor.calibration before the split, re-exports included.
PUBLIC_NAMES = (
    "ActualMetrics",
    "CalibrationCommitError",
    "CalibrationDeltas",
    "CalibrationFormatError",
    "CalibrationReport",
    "CalibrationSelfCheckError",
    "EntropyFactors",
    "LEDGER_EXECUTIONS_REL",
    "METADATA_PATH_PREFIXES",
    "PredictedMetrics",
    "calibration_commit_env",
    "compute_calibration_deltas",
    "extract_branch_components",
    "fetch_ref_if_needed",
    "fetch_remote_ref_if_needed",
    "format_calibration_report",
    "format_markdown_report",
    "generate_calibration",
    "is_calibration_stale",
    "is_metadata_path",
    "load_metrics_physics",
    "parse_actual_metrics",
    "parse_evaluated_commit_sha",
    "parse_predicted_metrics",
    "read_git_file",
    "record_calibration_action",
    "resolve_commit_sha",
    "resolve_git_ref",
    "run_calibrate",
    "run_git",
    "verify_calibration_freshness",
)


def _golden_report_dict() -> dict[str, Any]:
    """A fixed report input: evaluated SHA, merge base with merged commits, bug drag, touch set and review churn."""
    pred = PredictedMetrics(p_success=0.85, entropy=2.4, impact=40.0, cost=6.0, learning_value=2.0, ev=29.28)
    act = ActualMetrics(
        p_success=1.0,
        entropy=0.86,
        impact=40.0,
        cost=5.1,
        learning_value=2.0,
        ev=35.64,
        files_changed=3,
        insertions=120,
        deletions=14,
        ev_clean=35.64,
        ev_with_bugs=27.64,
        p_success_clean=1.0,
        p_success_with_bugs=0.8,
        impact_clean=40.0,
        impact_with_bugs=32.0,
        bug_ev_drag=8.0,
    )
    deltas, ratings, biases = compute_calibration_deltas(pred, act)
    return {
        "plan_id": "P-1795000001-CLA-CO5.5",
        "execution_id": "E-1795000002-CLA-CO5.5",
        "intent_branch": "I-1795000000-golden/_",
        "plan_branch": "I-1795000000-golden/P-1795000001-CLA-CO5.5/_",
        "execution_branch": "I-1795000000-golden/P-1795000001-CLA-CO5.5/E-1795000002-CLA-CO5.5/_",
        "calibrated_branch": "I-1795000000-golden/P-1795000001-CLA-CO5.5/E-1795000002-CLA-CO5.5/calibrated",
        "agent_id": "claude-agent",
        "agent_version": "2.1.302",
        "model_name": "claude-opus-5-5",
        "timestamp": "2026-11-26T05:30:00.000Z",
        "predicted": pred.to_dict(),
        "actual": act.to_dict(),
        "deltas": deltas.to_dict(),
        "entropy_factors": EntropyFactors(ssa=(0.6, 0.7)).to_dict(),
        "accuracy_ratings": ratings,
        "bias_directions": biases,
        "lambda": 0.3,
        "mu": 0.5,
        "evaluated_commit_sha": "0123456789abcdef0123456789abcdef01234567",
        "committed": True,
        "base_sha": "1111111111111111111111111111111111111111",
        "merge_base_sha": "2222222222222222222222222222222222222222",
        "merged_commit_count": 3,
        "diff_base": "target_merge_base",
        "touch_set": {
            "precision": 0.5,
            "recall": 0.6667,
            "unpredicted_paths": ["src/extra.py"],
            "unused_entries": ["docs/unused.md"],
        },
        "review_accounting": {
            "full_reviews": 1,
            "scoped_reviews": 2,
            "pipeline_runs": 3,
            "pipeline_reruns": 0,
            "approval_carry_overs": 1,
            "non_defect_churn": {"target_merge": 1, "bookkeeping": 0, "ci_noise": 0},
        },
    }


def test_format_markdown_report_matches_the_pre_split_golden() -> None:
    assert format_markdown_report(_golden_report_dict()) == GOLDEN.read_text(encoding="utf-8")


def test_every_pre_split_public_name_is_still_importable() -> None:
    assert tuple(sorted(PUBLIC_NAMES)) == PUBLIC_NAMES
    missing = [name for name in PUBLIC_NAMES if not hasattr(calibration, name)]
    assert missing == []


# CalibrationReport.to_dict() key order before the split: the --json output and the ACT-CAL row depend on it.
REPORT_DICT_KEYS = (
    "plan_id",
    "execution_id",
    "intent_branch",
    "plan_branch",
    "execution_branch",
    "calibrated_branch",
    "agent_id",
    "agent_version",
    "model_name",
    "timestamp",
    "predicted",
    "actual",
    "deltas",
    "entropy_factors",
    "accuracy_ratings",
    "bias_directions",
    "evaluated_commit_sha",
    "committed",
    "base_sha",
    "merge_base_sha",
    "merged_commit_count",
    "touch_set",
    "diff_base",
    "review_accounting",
)


def test_report_to_dict_keeps_the_pre_split_key_order() -> None:
    data = _golden_report_dict()
    report = calibration.CalibrationReport(
        **{
            key: data[key]
            for key in REPORT_DICT_KEYS
            if key not in ("predicted", "actual", "deltas", "entropy_factors")
        },
        predicted=PredictedMetrics(),
        actual=ActualMetrics(),
        deltas=calibration.CalibrationDeltas(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        entropy_factors=EntropyFactors(),
        markdown_content="# not serialised",
        changed_paths=["src/a.py"],
    )

    serialised = report.to_dict()

    assert tuple(serialised) == REPORT_DICT_KEYS
    assert serialised["predicted"] == PredictedMetrics().to_dict()
    assert serialised["entropy_factors"]["ssa"] == {"predicted": 0.5, "observed": 0.4}
    assert serialised["accuracy_ratings"] is data["accuracy_ratings"]


class TestReportFormatting(unittest.TestCase):
    """Test markdown generation conforming to calibration schema."""

    def test_format_markdown_report_structure(self):
        pred = PredictedMetrics(p_success=0.98, entropy=1.20, impact=35.0, cost=4.0, learning_value=2.5, ev=31.19)
        act = ActualMetrics(p_success=1.00, entropy=0.40, impact=35.0, cost=3.5, learning_value=2.5, ev=32.63)
        deltas, ratings, biases = compute_calibration_deltas(pred, act)
        ef = EntropyFactors()

        report_dict = {
            "plan_id": "P-1787051525",
            "execution_id": "E-1787051559",
            "intent_branch": "I-1787051498-executor-plan-calibration/_",
            "agent_id": "antigravity-agent",
            "model_name": "gemini-3.5-flash",
            "timestamp": "2026-08-18T11:13:50.000Z",
            "evaluated_commit_sha": "abcdef1234567890abcdef1234567890abcdef12",
            "predicted": pred.to_dict(),
            "actual": act.to_dict(),
            "deltas": deltas.to_dict(),
            "entropy_factors": ef.to_dict(),
            "accuracy_ratings": ratings,
            "bias_directions": biases,
        }

        md = format_markdown_report(report_dict)
        self.assertIn("# Plan Calibration Report: P-1787051525", md)
        self.assertIn("- **Execution ID:** `E-1787051559`", md)
        self.assertNotIn("executions/E-", md)
        self.assertIn("- **Evaluated Commit SHA:** `abcdef1234567890abcdef1234567890abcdef12`", md)
        self.assertIn("## 1. Executive Calibration Summary", md)
        self.assertIn("## 2. Mathematical Derivations & Calibration Errors", md)
        self.assertIn("## 3. Entropy Factor Breakdown", md)
        self.assertIn("## 4. Calibration Assessment", md)
        self.assertIn("| **$P(\\text{success})$**", md)
        self.assertIn("EV_{\\text{pred}}", md)
        self.assertIn("EV_{\\text{actual}}", md)


class TestEvaluatedCommitShaAndStaleness(unittest.TestCase):
    """Test commit SHA extraction and staleness detection."""

    def test_parse_evaluated_commit_sha(self):
        sha = "fedcba9876543210fedcba9876543210fedcba98"
        md_text = f"# Report\n\n- **Evaluated Commit SHA:** `{sha}`\n"
        parsed = parse_evaluated_commit_sha(md_text)
        self.assertEqual(parsed, sha)

        # None/missing cases return None
        na_text = "# Report\n\n- **Evaluated Commit SHA:** N/A\n"
        self.assertIsNone(parse_evaluated_commit_sha(na_text))
        empty_text = "# Report\n\n- **Evaluated Commit SHA:** ``\n"
        self.assertIsNone(parse_evaluated_commit_sha(empty_text))

    def test_calibration_report_evaluated_commit_sha_defaults_to_none(self):
        pred = PredictedMetrics()
        act = ActualMetrics()
        deltas = CalibrationDeltas(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        ef = EntropyFactors()
        report = CalibrationReport(
            plan_id="P-1",
            execution_id="E-1",
            intent_branch="I-1",
            plan_branch="P-1",
            execution_branch="E-1",
            calibrated_branch="C-1",
            agent_id="test-agent",
            agent_version="1.0",
            model_name="model",
            timestamp="2026-10-05T00:00:00.000Z",
            predicted=pred,
            actual=act,
            deltas=deltas,
            entropy_factors=ef,
            accuracy_ratings={},
            bias_directions={},
            markdown_content="",
        )
        self.assertIsNone(report.evaluated_commit_sha)
        self.assertIsNone(report.to_dict()["evaluated_commit_sha"])

    def test_format_markdown_report_with_none_evaluated_commit_sha(self):
        pred = PredictedMetrics()
        act = ActualMetrics()
        deltas, ratings, biases = compute_calibration_deltas(pred, act)
        ef = EntropyFactors()
        report_dict = {
            "plan_id": "P-1",
            "execution_id": "E-1",
            "intent_branch": "I-1",
            "agent_id": "test-agent",
            "model_name": "model",
            "timestamp": "2026-10-05T00:00:00.000Z",
            "evaluated_commit_sha": None,
            "predicted": pred.to_dict(),
            "actual": act.to_dict(),
            "deltas": deltas.to_dict(),
            "entropy_factors": ef.to_dict(),
            "accuracy_ratings": ratings,
            "bias_directions": biases,
        }
        md = format_markdown_report(report_dict)
        self.assertIn("- **Evaluated Commit SHA:** N/A", md)

    def test_is_calibration_stale_detection(self):
        eval_sha = "1111111111111111111111111111111111111111"
        fresh_head = "1111111111111111111111111111111111111111"
        stale_head = "2222222222222222222222222222222222222222"

        report_md = f"# Report\n- **Evaluated Commit SHA:** `{eval_sha}`\n"

        # Case 1: Fresh (SHA matches HEAD)
        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout=f"{fresh_head}\n"),
        ):
            is_stale, e_sha, h_sha = is_calibration_stale(report_md)
            self.assertFalse(is_stale)
            self.assertEqual(e_sha, eval_sha)
            self.assertEqual(h_sha, fresh_head)
            self.assertTrue(verify_calibration_freshness(report_md))

        # Case 2: Stale (HEAD has advanced)
        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout=f"{stale_head}\n"),
        ):
            is_stale, e_sha, h_sha = is_calibration_stale(report_md)
            self.assertTrue(is_stale)
            self.assertEqual(e_sha, eval_sha)
            self.assertEqual(h_sha, stale_head)
            self.assertFalse(verify_calibration_freshness(report_md))

    def test_is_calibration_stale_missing_sha(self):
        report_md = "# Report without SHA\n- **Evaluation Timestamp:** 2026-10-04\n"
        is_stale, _e_sha, h_sha = is_calibration_stale(report_md)
        self.assertTrue(is_stale)
        self.assertEqual(h_sha, "missing_evaluated_sha")
