"""Classify executions-ledger summary rows for Stage 5 calibration: which stage wrote a row, and whether its failure
was the harness's rather than the delivery's.

Only Stage 3 execution rows measure a plan's delivery. Stage 4 review rows (``REV-`` ids) and Stage 5 calibration rows
(``CAL-`` ids) share the plan branch but describe later stages, so they never feed the dual EV. A Stage 3 row whose
failure text names a harness condition (a container dispatch exit, a usage limit, a turn or stage budget timeout) is an
infrastructure failure and never counts as bug drag.
"""

from __future__ import annotations

import re
from typing import Any

ROW_STAGE_EXECUTION = "execution"
ROW_STAGE_REVIEW = "review"
ROW_STAGE_CALIBRATE = "calibrate"
ROW_STAGE_OTHER = "other"

_STAGE_BY_ID_PREFIX: tuple[tuple[str, str], ...] = (
    ("REV-", ROW_STAGE_REVIEW),
    ("CAL-", ROW_STAGE_CALIBRATE),
    ("E-", ROW_STAGE_EXECUTION),
)
_KNOWN_STAGES = frozenset({ROW_STAGE_EXECUTION, ROW_STAGE_REVIEW, ROW_STAGE_CALIBRATE})

# Harness phrases only: a bare "failed", "error" or "timed out" (a test timing out) is the delivery's failure.
INFRASTRUCTURE_FAILURE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"container dispatch (?:exited|failed)", re.IGNORECASE),
    re.compile(r"\b(?:usage|session)[ -]limit", re.IGNORECASE),
    re.compile(r"\b(?:harness|turn) time(?:d)?[ -]?out", re.IGNORECASE),
    re.compile(r"\bbudget of [\d.]+ ?s exceeded", re.IGNORECASE),
)
_FAILURE_TEXT_FIELDS: tuple[str, ...] = ("summary", "error", "failure_reason")


def row_stage(record: dict[str, Any], execution_id: str = "") -> str:
    """The stage that wrote a summary row: ``execution``, ``review``, ``calibrate`` or ``other``.

    An explicit ``stage`` field wins. Otherwise the ``execution_id`` prefix decides (``REV-``, ``CAL-``, ``E-``); a
    legacy row with no id, or with the plan's own execution id, is an execution row.
    """
    explicit = str(record.get("stage") or "").strip().lower()
    if explicit:
        return explicit if explicit in _KNOWN_STAGES else ROW_STAGE_OTHER
    row_id = str(record.get("execution_id") or "")
    if not row_id or (execution_id and row_id == execution_id):
        return ROW_STAGE_EXECUTION
    return next((stage for prefix, stage in _STAGE_BY_ID_PREFIX if row_id.startswith(prefix)), ROW_STAGE_OTHER)


def is_execution_row(record: dict[str, Any], execution_id: str = "") -> bool:
    """True for a Stage 3 execution summary row (see ``row_stage``)."""
    return row_stage(record, execution_id) == ROW_STAGE_EXECUTION


def is_infrastructure_failure(record: dict[str, Any]) -> bool:
    """True when the row's ``summary``, ``error`` or ``failure_reason`` names a harness failure, not a delivery one."""
    text = " ".join(str(record.get(field) or "") for field in _FAILURE_TEXT_FIELDS)
    return any(pattern.search(text) for pattern in INFRASTRUCTURE_FAILURE_PATTERNS)
