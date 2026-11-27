from __future__ import annotations

import json
from pathlib import Path

from sandbox_executor.entrypoint.test_failure_record import (
    STAGE_3,
    STAGE_5,
    TestFailureEvidence,
    failure_signature,
    record_test_failure,
)
from sandbox_executor.knowledge.index import ActionIndexDatabase
from sandbox_executor.knowledge.wisdom import promote_pending_candidates, promote_repeated_failure_signatures

REPEATED_IDS = ("tests/test_demo.py::test_alpha", "tests/test_demo.py::test_beta")
SINGLE_IDS = ("tests/test_other.py::test_gamma",)
BRANCH = "I-1-demo/P-2-CLA-CO5.5/E-3-CLA-CO5.5/_"


def _excerpts(ids: tuple[str, ...], line: int, temp_dir: str) -> dict[str, str]:
    return {
        test_id: f"FAILED {test_id}\nE       AssertionError: {temp_dir}/out.txt missing\n{test_id}:{line}: Error"
        for test_id in ids
    }


def _write_failure_row(
    ledger: Path, run_id: str, ids: tuple[str, ...], stage: str = STAGE_3, classification: str = "introduced"
) -> str:
    excerpts = _excerpts(ids, 12 + len(run_id), f"/tmp/pytest-{run_id}")
    evidence = TestFailureEvidence(
        run_id=run_id,
        stage=stage,
        head_sha="c" * 40,
        failing_ids=ids,
        excerpts=excerpts,
        classification=classification,
    )
    record_test_failure(str(ledger), evidence, branch=BRANCH, execution_id="E-3-CLA-CO5.5")
    return failure_signature(ids, excerpts)


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl", tmp_path / "holon-knowledge" / "kb"


def _failure_mode_files(kb_dir: Path) -> list[Path]:
    return sorted((kb_dir / "failure_modes").glob("*.json"))


def test_signature_from_two_runs_creates_one_failure_mode_entry(tmp_path: Path) -> None:
    ledger, kb_dir = _layout(tmp_path)
    signature = _write_failure_row(ledger, "r1", REPEATED_IDS)
    assert _write_failure_row(ledger, "run-2", REPEATED_IDS, stage=STAGE_5) == signature
    single_signature = _write_failure_row(ledger, "r3", SINGLE_IDS)

    promoted = promote_repeated_failure_signatures(str(ledger), str(kb_dir))

    assert [item["kb_id"] for item in promoted] == [f"KB-failure-{signature}"]
    assert [path.name for path in _failure_mode_files(kb_dir)] == [f"KB-failure-{signature}.json"]
    entry = json.loads(_failure_mode_files(kb_dir)[0].read_text(encoding="utf-8"))
    assert entry["kb_type"] == "failure_mode"
    assert entry["payload"]["failing_test_ids"] == list(REPEATED_IDS)
    assert entry["payload"]["classification"] == "introduced"
    assert entry["payload"]["failure_signature"] == signature
    assert entry["payload"]["stages"] == [STAGE_3, STAGE_5]
    assert not (kb_dir / "failure_modes" / f"KB-failure-{single_signature}.json").exists()


def test_later_undiagnosed_stage5_row_keeps_the_earlier_real_diagnosis(tmp_path: Path) -> None:
    ledger, kb_dir = _layout(tmp_path)
    _write_failure_row(ledger, "r1", REPEATED_IDS, classification="pre-existing")
    _write_failure_row(ledger, "r2", REPEATED_IDS, stage=STAGE_5, classification="undiagnosed")

    [item] = promote_repeated_failure_signatures(str(ledger), str(kb_dir))

    assert item["payload"]["classification"] == "pre-existing"


def test_two_rows_of_one_run_do_not_promote(tmp_path: Path) -> None:
    ledger, kb_dir = _layout(tmp_path)
    _write_failure_row(ledger, "r1", REPEATED_IDS)
    _write_failure_row(ledger, "r1", REPEATED_IDS, classification="flaky")

    assert promote_repeated_failure_signatures(str(ledger), str(kb_dir)) == []
    assert _failure_mode_files(kb_dir) == []


def test_promoting_twice_creates_no_second_entry(tmp_path: Path) -> None:
    ledger, kb_dir = _layout(tmp_path)
    _write_failure_row(ledger, "r1", REPEATED_IDS)
    _write_failure_row(ledger, "r2", REPEATED_IDS)

    first = promote_repeated_failure_signatures(str(ledger), str(kb_dir))
    content = _failure_mode_files(kb_dir)[0].read_bytes()
    second = promote_repeated_failure_signatures(str(ledger), str(kb_dir))

    assert len(first) == 1
    assert second == []
    assert len(_failure_mode_files(kb_dir)) == 1
    assert _failure_mode_files(kb_dir)[0].read_bytes() == content


def test_missing_ledger_promotes_nothing(tmp_path: Path) -> None:
    ledger, kb_dir = _layout(tmp_path)

    assert promote_repeated_failure_signatures(str(ledger), str(kb_dir)) == []


def test_stage5_promotion_step_includes_repeated_signatures(tmp_path: Path) -> None:
    ledger, kb_dir = _layout(tmp_path)
    signature = _write_failure_row(ledger, "r1", REPEATED_IDS)
    _write_failure_row(ledger, "r2", REPEATED_IDS)
    db = ActionIndexDatabase(db_path=str(tmp_path / "actions.db"))

    promoted = promote_pending_candidates(index_db=db, kb_dir=str(kb_dir), wb_dir=str(tmp_path / "wisdom"))

    assert f"KB-failure-{signature}" in [item.get("kb_id") for item in promoted]
    assert promote_pending_candidates(index_db=db, kb_dir=str(kb_dir), wb_dir=str(tmp_path / "wisdom")) == []
