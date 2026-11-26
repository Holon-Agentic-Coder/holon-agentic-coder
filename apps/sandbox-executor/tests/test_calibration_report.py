"""Report rendering and public surface of ``sandbox_executor.calibration``.

The rendering golden (``fixtures/calibration_report_golden.txt``) was produced by the single-module ``calibration.py``
before it became a package: every later change to the report renderer must keep the output byte-identical.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import sandbox_executor.calibration as calibration
from sandbox_executor.calibration import (
    ActualMetrics,
    EntropyFactors,
    PredictedMetrics,
    compute_calibration_deltas,
    format_markdown_report,
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


def test_format_markdown_report_with_none_evaluated_commit_sha() -> None:
    report_dict = _golden_report_dict()
    report_dict["evaluated_commit_sha"] = None

    md = format_markdown_report(report_dict)

    assert "- **Evaluated Commit SHA:** N/A\n" in md


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
