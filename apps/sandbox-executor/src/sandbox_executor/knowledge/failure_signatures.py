from __future__ import annotations

import os
from typing import Any

from sandbox_executor.failure_classification import UNDIAGNOSED_CLASSIFICATION
from sandbox_executor.knowledge.stable_envelope import (
    build_kb_envelope,
    stable_advisory_text,
    write_envelope_once,
)
from sandbox_executor.ledger.merged import read_merged_rows

DEFAULT_KB_DIR = "holon-knowledge/kb"
TEST_FAILURE_ACTION = "test_failure"
MIN_SIGNATURE_RUNS = 2
EXECUTIONS_LEDGER_NAME = "executions.jsonl"
UNMEASURED_CLASSIFICATIONS = frozenset({UNDIAGNOSED_CLASSIFICATION})


def _test_failure_rows_by_signature(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("action") == TEST_FAILURE_ACTION and row.get("failure_signature") and row.get("run_id"):
            grouped.setdefault(str(row["failure_signature"]), []).append(row)
    return grouped


def _distinct_runs(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row["run_id"]) for row in rows}


def _signature_classification(rows: list[dict[str, Any]]) -> str:
    measured = (row.get("classification") for row in reversed(rows))
    return next((str(c) for c in measured if c and c not in UNMEASURED_CLASSIFICATIONS), "unclassified")


def _signature_failure_item(signature: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    classification = _signature_classification(rows)
    symptom = rows[0].get("error_context")
    return {
        "kb_id": f"KB-failure-{signature}",
        "kb_type": "failure_mode",
        "context_fingerprint": signature,
        "tags": ["test", "failure_signature", classification],
        "payload": {
            "name": f"Repeated test failure {signature}",
            "description": stable_advisory_text("failure", TEST_FAILURE_ACTION),
            "action": TEST_FAILURE_ACTION,
            "failure_signature": signature,
            "failing_test_ids": sorted({str(i) for row in rows for i in row.get("failing_test_ids") or []}),
            "classification": classification,
            "stages": sorted({str(row.get("stage")) for row in rows if row.get("stage")}),
            "symptoms": [symptom] if symptom else [],
        },
    }


def executions_ledger_of(kb_dir: str) -> str:
    return os.path.join(os.path.dirname(os.path.normpath(kb_dir)), "ledger", EXECUTIONS_LEDGER_NAME)


def promote_repeated_failure_signatures(
    ledger_path: str, kb_dir: str = DEFAULT_KB_DIR, dry_run: bool = False
) -> list[dict[str, Any]]:
    promoted: list[dict[str, Any]] = []
    for signature, rows in _test_failure_rows_by_signature(read_merged_rows(ledger_path)).items():
        out_file = os.path.join(kb_dir, "failure_modes", f"KB-failure-{signature}.json")
        if os.path.exists(out_file) or len(_distinct_runs(rows)) < MIN_SIGNATURE_RUNS:
            continue
        item = _signature_failure_item(signature, rows)
        if not dry_run:
            item["persisted_path"] = write_envelope_once(
                out_file, build_kb_envelope(dict(item), item["kb_id"], "failure_mode")
            )
        promoted.append(item)
    return promoted
