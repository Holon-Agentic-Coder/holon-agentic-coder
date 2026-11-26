"""Calibration analysis engine for Holon Agentic Coder.

Ingests predicted plan metrics and actual execution telemetry to compute
calibration errors, Expected Value deltas, entropy factors, and generates
markdown calibration reports written beside the plan file at `plans/{intent}/{plan_id}_calibration.md`.

The package re-exports every name importable from the former single ``calibration`` module. Its modules:
``predicted`` (``PredictedMetrics.from_plan``), ``actual`` (``ActualMetrics.from_execution`` and the per-metric
extractor table), ``deltas`` (pure delta computation), ``report`` (report model and rendering), ``generate`` (one
execution's calibration) and ``run`` (the I/O shell that reads ledgers and git and writes the report).
"""

from __future__ import annotations

from sandbox_executor.calibration.actual import ActualMetrics as ActualMetrics
from sandbox_executor.calibration.actual import parse_actual_metrics as parse_actual_metrics
from sandbox_executor.calibration.branches import extract_branch_components as extract_branch_components
from sandbox_executor.calibration.branches import is_calibration_stale as is_calibration_stale
from sandbox_executor.calibration.branches import parse_evaluated_commit_sha as parse_evaluated_commit_sha
from sandbox_executor.calibration.branches import verify_calibration_freshness as verify_calibration_freshness
from sandbox_executor.calibration.deltas import CalibrationDeltas as CalibrationDeltas
from sandbox_executor.calibration.deltas import compute_calibration_deltas as compute_calibration_deltas
from sandbox_executor.calibration.generate import generate_calibration as generate_calibration
from sandbox_executor.calibration.physics import load_metrics_physics as load_metrics_physics
from sandbox_executor.calibration.predicted import PredictedMetrics as PredictedMetrics
from sandbox_executor.calibration.predicted import parse_predicted_metrics as parse_predicted_metrics
from sandbox_executor.calibration.refs import fetch_ref_if_needed as fetch_ref_if_needed
from sandbox_executor.calibration.refs import fetch_remote_ref_if_needed as fetch_remote_ref_if_needed
from sandbox_executor.calibration.refs import read_git_file as read_git_file
from sandbox_executor.calibration.refs import resolve_commit_sha as resolve_commit_sha
from sandbox_executor.calibration.refs import resolve_git_ref as resolve_git_ref
from sandbox_executor.calibration.report import CalibrationReport as CalibrationReport
from sandbox_executor.calibration.report import EntropyFactors as EntropyFactors
from sandbox_executor.calibration.report import format_markdown_report as format_markdown_report
from sandbox_executor.calibration.run import calibration_commit_env as calibration_commit_env
from sandbox_executor.calibration.run import record_calibration_action as record_calibration_action
from sandbox_executor.calibration.run import run_calibrate as run_calibrate
from sandbox_executor.calibration_evaluated_sha import LEDGER_EXECUTIONS_REL as LEDGER_EXECUTIONS_REL
from sandbox_executor.calibration_format import CalibrationCommitError as CalibrationCommitError
from sandbox_executor.calibration_format import CalibrationFormatError as CalibrationFormatError
from sandbox_executor.calibration_format import format_calibration_report as format_calibration_report
from sandbox_executor.calibration_self_check import CalibrationSelfCheckError as CalibrationSelfCheckError
from sandbox_executor.git import METADATA_PATH_PREFIXES as METADATA_PATH_PREFIXES
from sandbox_executor.git import is_metadata_path as is_metadata_path
from sandbox_executor.git import run_git as run_git

__all__ = [
    "LEDGER_EXECUTIONS_REL",
    "METADATA_PATH_PREFIXES",
    "ActualMetrics",
    "CalibrationCommitError",
    "CalibrationDeltas",
    "CalibrationFormatError",
    "CalibrationReport",
    "CalibrationSelfCheckError",
    "EntropyFactors",
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
]
