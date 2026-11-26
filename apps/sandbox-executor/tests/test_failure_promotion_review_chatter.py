"""Hermetic tests: Stage 4 review chatter inside one flow never promotes a KB-failure file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.knowledge.index import ActionIndexDatabase
from sandbox_executor.knowledge.promotion_rules import is_single_flow_review_chatter
from sandbox_executor.knowledge.wisdom import promote_pending_candidates

FINGERPRINT = "9f57ffa8deadbeef"
KB_FAILURE_ID = f"KB-failure-{FINGERPRINT[:8]}"
FLOW_A = "I-1790000000-demo/P-1790000001-CLA-CO5.5/E-1790000002-CLA-CO5.5/_"
FLOW_B = "I-1790000100-other/P-1790000101-CLA-CO5.5/E-1790000102-CLA-CO5.5/_"


def _row(action_id: str, target: str, minute: int) -> dict[str, Any]:
    return {
        "action_id": action_id,
        "context_fingerprint": FINGERPRINT,
        "action": "Review finding",
        "target": target,
        "verdict": "failure",
        "action_origin": "pr_review",
        "error_context": "Reviewer flagged a defect",
        "created_at": f"2026-11-26T10:{minute:02d}:00+00:00",
    }


class _Workspace:
    """A tmp ledger, action index and KB/WB directories for one test."""

    def __init__(self, tmp_path: Path) -> None:
        self.ledger = tmp_path / "executions.jsonl"
        self.ledger.write_text("", encoding="utf-8")
        self.db = ActionIndexDatabase(db_path=str(tmp_path / "action_index.db"))
        self.kb_dir = tmp_path / "holon-knowledge" / "kb"
        self.wb_dir = tmp_path / "holon-knowledge" / "wisdom"

    def append_rows(self, rows: list[dict[str, Any]]) -> None:
        with open(self.ledger, "a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        self.db.sync_ledger(str(self.ledger))

    def promote(self, dry_run: bool = False) -> list[dict[str, Any]]:
        return promote_pending_candidates(
            index_db=self.db, kb_dir=str(self.kb_dir), wb_dir=str(self.wb_dir), min_trials=2, dry_run=dry_run
        )

    def failure_file(self) -> Path:
        return self.kb_dir / "failure_modes" / f"{KB_FAILURE_ID}.json"


def _failure_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if str(item.get("kb_id", "")).startswith("KB-failure-")]


def test_single_flow_review_rows_produce_no_kb_failure(tmp_path: Path) -> None:
    ws = _Workspace(tmp_path)
    ws.append_rows(
        [
            _row("ACT-FIND-1", FLOW_A, 1),
            _row("ACT-FIND-2", FLOW_A, 2),
            _row("ACT-FIND-3", FLOW_A, 3),
            _row("ACT-REV-1", FLOW_A, 4),
        ]
    )

    assert _failure_items(ws.promote(dry_run=True)) == []
    assert _failure_items(ws.promote()) == []
    failure_dir = ws.kb_dir / "failure_modes"
    assert not failure_dir.exists() or not any(failure_dir.iterdir())


def test_review_failures_across_two_flows_still_promote(tmp_path: Path) -> None:
    ws = _Workspace(tmp_path)
    ws.append_rows([_row("ACT-FIND-A", FLOW_A, 1), _row("ACT-FIND-B", FLOW_B, 2)])

    items = _failure_items(ws.promote())

    assert [item["kb_id"] for item in items] == [KB_FAILURE_ID]
    assert items[0]["evidence"]["ledger_refs"] == ["ACT-FIND-B", "ACT-FIND-A"]
    payload = json.loads(ws.failure_file().read_text(encoding="utf-8"))["payload"]
    assert payload["action"] == "Review finding"
    assert payload["name"] == f"Failure mode in {FLOW_B}"
    assert payload["symptoms"] == ["Reviewer flagged a defect"]


def test_review_failures_across_flows_promote_beyond_recent_sample(tmp_path: Path) -> None:
    ws = _Workspace(tmp_path)
    ws.append_rows([_row(f"ACT-FIND-A{i}", FLOW_A, i) for i in range(2)])
    ws.append_rows([_row(f"ACT-FIND-B{i}", FLOW_B, 10 + i) for i in range(5)])

    assert [item["kb_id"] for item in _failure_items(ws.promote())] == [KB_FAILURE_ID]
    assert ws.failure_file().exists()


def test_non_review_repeated_failure_still_promotes(tmp_path: Path) -> None:
    ws = _Workspace(tmp_path)
    ws.append_rows([_row("act-x-1", "src/x.py", 1), _row("act-x-2", "src/x.py", 2)])

    assert [item["kb_id"] for item in _failure_items(ws.promote())] == [KB_FAILURE_ID]
    assert ws.failure_file().exists()


def test_mixed_review_and_non_review_rows_still_promote(tmp_path: Path) -> None:
    ws = _Workspace(tmp_path)
    ws.append_rows([_row("ACT-FIND-1", FLOW_A, 1), _row("act-impl-1", FLOW_A, 2)])

    assert [item["kb_id"] for item in _failure_items(ws.promote())] == [KB_FAILURE_ID]
    assert ws.failure_file().exists()


@pytest.mark.parametrize(
    ("records", "expected"),
    [
        ([], False),
        ([{"record_id": "ACT-REV-1", "target": FLOW_A}, {"record_id": "ACT-FIND-2", "target": FLOW_A}], True),
        ([{"record_id": "ACT-FIND-1", "target": FLOW_A}, {"record_id": "ACT-FIND-2", "target": FLOW_B}], False),
        ([{"record_id": "ACT-REV-1", "target": "85"}, {"record_id": "ACT-FIND-2", "target": " 85 "}], True),
        ([{"record_id": "ACT-FIND-1", "target": None}, {"record_id": "ACT-FIND-2", "target": None}], True),
        ([{"record_id": None, "target": FLOW_A}, {"record_id": "ACT-FIND-2", "target": FLOW_A}], False),
        (
            [{"record_id": "ACT-FIND-1", "target": "I-1790000000-demo"}, {"record_id": "ACT-REV-2", "target": FLOW_A}],
            True,
        ),
    ],
)
def test_is_single_flow_review_chatter(records: list[dict[str, Any]], expected: bool) -> None:
    assert is_single_flow_review_chatter(records) is expected
