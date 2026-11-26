"""Calibration and admission read the same answers whether a row is canonical, pending or mixed.

Also pins behaviour preservation on the real ledgers: with no fragments, the merged reader returns exactly what the
old per-reader parse loops returned, row for row.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.calibration_execution_owner import unique_plan_branch_of_execution
from sandbox_executor.calibration_predicted import execution_predicted_metrics, read_plans_rows, select_plans_row
from sandbox_executor.entrypoint.execution_args import _read_intent_row, _read_plan_row
from sandbox_executor.entrypoint.plan_id_unique import _ledger_plan_ids
from sandbox_executor.ledger import query_ledger, read_merged_rows, read_merged_text, validate_stage_ledger
from sandbox_executor.ledger.fragments import LEDGER_NAMES, root_intent_id

LEDGER_REL = Path("holon-knowledge") / "ledger"
REPO_ROOT = Path(__file__).resolve().parents[3]
PLAN_A = "I-1-a/P-10-a/_"
PLAN_B = "I-2-b/P-20-b/_"

ROWS: dict[str, list[dict[str, Any]]] = {
    "intents.jsonl": [
        {"branch": "I-1-a/_", "intent_id": "I-1-a", "goal": "a", "created_at": "2026-11-25T08:00:00Z"},
        {"branch": "I-2-b/_", "intent_id": "I-2-b", "goal": "b", "created_at": "2026-11-25T08:30:00+00:00"},
    ],
    "plans.jsonl": [
        {"intent_branch": "I-1-a/_", "plan_id": "P-10-a", "ev_pred": 1.0, "created_at": "2026-11-25T09:00:00Z"},
        {"intent_branch": "I-2-b/_", "plan_id": "P-20-b", "ev_pred": 2.0, "created_at": "2026-11-25T09:30:00+00:00"},
        {
            "intent_branch": "I-1-a/_",
            "plan_id": "P-10-a",
            "ev_pred": 3.0,
            "ledger_revision": 2,
            "created_at": "2026-11-25T11:00:00+01:00",
        },
        {"intent_branch": "I-1-a/_", "plan_id": "P-11-a", "ev_pred": 4.0, "created_at": "2026-11-25T10:30:00Z"},
    ],
    "executions.jsonl": [
        {
            "entry_type": "summary",
            "plan_branch": PLAN_A,
            "execution_id": "E-100-a",
            "predicted_metrics": {"ev": 1.5},
            "created_at": "2026-11-25T12:00:00Z",
        },
        {
            "entry_type": "action",
            "action_id": "ACT-1",
            "plan_branch": PLAN_B,
            "execution_id": "E-200-b",
            "created_at": "2026-11-25T12:30:00+00:00",
        },
        {
            "entry_type": "summary",
            "plan_branch": PLAN_B,
            "execution_id": "E-200-b",
            "predicted_metrics": {"ev": 2.5},
            "created_at": "2026-11-25T13:00:00Z",
        },
        {
            "entry_type": "summary",
            "plan_branch": PLAN_A,
            "execution_id": "E-100-a",
            "predicted_metrics": {"ev": 9.5},
            "created_at": "2026-11-25T15:00:00+01:00",
        },
    ],
}


def _append(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.writelines(json.dumps(row) + "\n" for row in rows)


def _lay_out(repo: Path, form: str) -> None:
    for name, rows in ROWS.items():
        for index, row in enumerate(rows):
            pending = form == "pending" or (form == "mixed" and index % 2 == 1)
            stem = name.removesuffix(".jsonl")
            target = repo / LEDGER_REL / "pending" / stem / f"{root_intent_id(row)}.jsonl" if pending else None
            _append(target or repo / LEDGER_REL / name, [row])


def _answers(repo: Path) -> dict[str, Any]:
    ledger = repo / LEDGER_REL
    plans = read_plans_rows(str(ledger / "plans.jsonl"))
    validate_stage_ledger(str(repo), "plan")
    validate_stage_ledger(str(repo), "execute")
    return {
        "plans_row_a": select_plans_row(plans, PLAN_A),
        "plans_row_b": select_plans_row(plans, PLAN_B),
        "owner": unique_plan_branch_of_execution("E-200-b", str(ledger / "executions.jsonl")),
        "predicted_a": execution_predicted_metrics(str(ledger / "executions.jsonl"), "E-100-a", PLAN_A),
        "admission_plan_row": _read_plan_row(str(repo), PLAN_A),
        "admission_intent_row": _read_intent_row(str(repo), "I-2-b/_"),
        "taken_plan_ids": _ledger_plan_ids(str(repo)),
        "actions": query_ledger(str(ledger / "executions.jsonl"), entry_type="action"),
    }


@pytest.mark.parametrize("form", ["pending", "mixed"])
def test_calibration_and_admission_are_identical_in_every_form(tmp_path: Path, form: str) -> None:
    _lay_out(tmp_path / "canonical", "canonical")
    _lay_out(tmp_path / form, form)
    expected = _answers(tmp_path / "canonical")
    assert expected["plans_row_a"]["ev_pred"] == 3.0
    assert expected["predicted_a"] == {"ev": 9.5}
    assert expected["owner"] == PLAN_B
    assert expected["taken_plan_ids"] == {"P-10-a", "P-11-a", "P-20-b"}
    assert _answers(tmp_path / form) == expected
    for name in LEDGER_NAMES:
        canonical_rows = read_merged_rows(str(tmp_path / "canonical" / LEDGER_REL / name))
        assert read_merged_rows(str(tmp_path / form / LEDGER_REL / name)) == canonical_rows


def _old_parse_loop(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


@pytest.mark.parametrize("name", LEDGER_NAMES)
def test_real_ledger_reads_are_unchanged_without_fragments(tmp_path: Path, name: str) -> None:
    source = REPO_ROOT / LEDGER_REL / name
    if not source.is_file():
        pytest.skip(f"{source} is not in this checkout")
    copy = tmp_path / LEDGER_REL / name
    copy.parent.mkdir(parents=True)
    shutil.copyfile(source, copy)
    raw = copy.read_text(encoding="utf-8")
    assert read_merged_text(str(copy)) == raw
    assert read_merged_rows(str(copy)) == _old_parse_loop(raw)
