"""Unit and concurrency tests for LedgerLock advisory file locking (Bean 0127)."""

import concurrent.futures
import json
import multiprocessing
import os
import tempfile
import time
from pathlib import Path

import pytest
from sandbox_executor.ledger import (
    LedgerLock,
    append_action_ledger,
    append_action_record,
    append_execution_record,
    atomic_ledger_append,
    try_append_action_record,
)


def test_ledger_lock_basic() -> None:
    """Verify LedgerLock acquires, writes, and releases cleanly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger_file = os.path.join(tmpdir, "test.jsonl")

        with LedgerLock(ledger_file) as f:
            f.write(json.dumps({"test": 1}) + "\n")

        assert os.path.exists(ledger_file)
        with open(ledger_file, encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == {"test": 1}


def test_ledger_lock_timeout() -> None:
    """Verify LedgerLock raises TimeoutError when lock is held beyond timeout threshold."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger_file = os.path.join(tmpdir, "test.jsonl")

        with (
            LedgerLock(ledger_file) as _,
            pytest.raises(TimeoutError) as exc_info,
            LedgerLock(ledger_file, timeout=0.1, initial_delay=0.01),
        ):
            pass
        assert "Timed out waiting for exclusive lock" in str(exc_info.value)


def test_atomic_ledger_append_context_manager() -> None:
    """Verify atomic_ledger_append functions correctly with LedgerLock."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger_file = os.path.join(tmpdir, "test.jsonl")

        with atomic_ledger_append(ledger_file) as f:
            f.write(json.dumps({"action": "first"}) + "\n")

        with atomic_ledger_append(ledger_file) as f:
            f.write(json.dumps({"action": "second"}) + "\n")

        with open(ledger_file, encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]

        assert len(lines) == 2
        assert lines[0]["action"] == "first"
        assert lines[1]["action"] == "second"


def test_canonical_aliases() -> None:
    """Verify append_action_ledger and append_execution_record canonical aliases work."""
    with tempfile.TemporaryDirectory() as tmpdir:
        actions_file = os.path.join(tmpdir, "actions.jsonl")
        executions_file = os.path.join(tmpdir, "executions.jsonl")

        act = append_action_ledger(
            actions_file,
            {
                "execution_id": "E-test-1",
                "action": "build_package",
                "category": "build",
                "target": "package",
                "outcome": "success",
            },
        )
        assert act["action"] == "build_package"
        assert act["entry_type"] == "action"

        summ = append_execution_record(
            executions_file,
            {
                "execution_id": "E-test-1",
                "plan_branch": "P-test",
                "agent": "test-agent",
                "model": "test-model",
                "status": "success",
            },
        )
        assert summ["execution_id"] == "E-test-1"
        assert summ["entry_type"] == "summary"

        with open(actions_file, encoding="utf-8") as f:
            act_lines = f.readlines()
        assert len(act_lines) == 1
        assert json.loads(act_lines[0])["action"] == "build_package"


def test_ledger_lock_concurrency() -> None:
    """Verify high-concurrency writes from multiple workers append without line interleaving."""
    num_workers = 10
    writes_per_worker = 20
    total_expected = num_workers * writes_per_worker

    with tempfile.TemporaryDirectory() as tmpdir:
        ledger_file = os.path.join(tmpdir, "concurrent_ledger.jsonl")

        def worker_task(worker_id: int) -> None:
            for i in range(writes_per_worker):
                rec = {
                    "worker_id": worker_id,
                    "seq": i,
                    "payload": f"worker-{worker_id}-seq-{i}-" + ("x" * 50),
                    "timestamp": time.time(),
                }
                with LedgerLock(ledger_file, timeout=15.0) as f:
                    f.write(json.dumps(rec) + "\n")

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(worker_task, wid) for wid in range(num_workers)]
            concurrent.futures.wait(futures)
            for fut in futures:
                fut.result()  # Re-raise any unexpected exceptions

        assert os.path.exists(ledger_file)
        with open(ledger_file, encoding="utf-8") as f:
            raw_lines = f.readlines()

        assert len(raw_lines) == total_expected, f"Expected {total_expected} lines, got {len(raw_lines)}"

        # Validate that every line is valid JSON with zero interleaving
        seen_records: set[tuple[int, int]] = set()
        for idx, line in enumerate(raw_lines):
            try:
                parsed = json.loads(line)
            except Exception as e:
                pytest.fail(f"Line {idx} corrupted or interleaved: {line!r} (error: {e})")
            wid = parsed["worker_id"]
            seq = parsed["seq"]
            assert (wid, seq) not in seen_records, f"Duplicate record detected: worker {wid}, seq {seq}"
            seen_records.add((wid, seq))

        assert len(seen_records) == total_expected


# ---------------------------------------------------------------------------
# Validated, shape-preserving action appends (Stage 4/5 ledger provenance)
# ---------------------------------------------------------------------------

_LARGE_PAYLOAD = "p" * 8192


def _review_record(worker_id: int, seq: int) -> dict:
    """Build a Stage 4 shaped action record of roughly 8 KiB."""
    return {
        "action_id": f"ACT-REV-{worker_id}-{seq:04d}",
        "execution_id": f"REV-ITR-{seq + 1}-{worker_id}",
        "action": f"PR Review Loop iteration {seq + 1} evaluation for worker {worker_id}",
        "category": "code",
        "target": "I-1/P-2/E-3/_",
        "outcome": "success",
        "entry_type": "action",
        "action_origin": "pr_review",
        "review_severity": None,
        "review_iteration": seq + 1,
        "review_finding_ids": None,
        "labels": ["review", "review-loop", f"iteration-{seq + 1}"],
        "error_context": _LARGE_PAYLOAD,
        "created_at": "2026-10-23T00:00:00+00:00",
    }


def _process_append_worker(ledger_file: str, worker_id: int, count: int) -> None:
    """Append ``count`` records from a separate process."""
    for seq in range(count):
        append_action_record(ledger_file, _review_record(worker_id, seq), preserve_shape=True)


def _assert_clean_ledger(ledger_file: str, expected: int) -> None:
    with open(ledger_file, encoding="utf-8") as f:
        raw_lines = f.read().splitlines()
    assert len(raw_lines) == expected
    ids: set[str] = set()
    for idx, line in enumerate(raw_lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            pytest.fail(f"Line {idx} corrupted or interleaved: {line[:120]!r} ({exc})")
        assert row["error_context"] == _LARGE_PAYLOAD
        ids.add(row["action_id"])
    assert len(ids) == expected


def test_preserve_shape_keeps_key_order_and_extra_keys(tmp_path: Path) -> None:
    """preserve_shape=True writes the dictionary verbatim after validation."""
    ledger_file = tmp_path / "executions.jsonl"
    record = {
        "action_id": "ACT-CAL-1-abcdef12",
        "execution_id": "CAL-P-1-1",
        "action": "Calibration report generated",
        "category": "doc",
        "target": "I-1/P-1/E-1/calibrated",
        "outcome": "success",
        "entry_type": "action",
        "action_origin": "calibration",
        "predicted_metrics": {"p_success": 0.8},
        "calibration_report_path": "plans/P-1_calibration.md",
    }
    written = append_action_record(str(ledger_file), record, preserve_shape=True)
    assert written == record
    assert ledger_file.read_text(encoding="utf-8") == json.dumps(record) + "\n"


def test_default_append_action_record_shape_unchanged(tmp_path: Path) -> None:
    """The default (normalising) path still emits the full ActionRecord field set."""
    ledger_file = tmp_path / "executions.jsonl"
    written = append_action_record(
        str(ledger_file),
        {"execution_id": "E-1", "action": "a", "category": "code", "target": "t", "outcome": "success", "x": 1},
    )
    assert "x" not in written
    assert written["event_type"] == "command_execution"
    assert list(written)[:6] == ["action_id", "execution_id", "action", "category", "target", "outcome"]


@pytest.mark.parametrize(
    "bad",
    [
        {"execution_id": "E-1", "action": "a", "category": "code", "target": "t"},
        {"execution_id": "E-1", "action": "a", "category": "code", "target": "t", "outcome": "maybe"},
        {
            "execution_id": "E-1",
            "action": "a",
            "category": "code",
            "target": "t",
            "outcome": "success",
            "action_origin": "nowhere",
        },
    ],
)
def test_preserve_shape_rejects_invalid_records_before_writing(tmp_path: Path, bad: dict) -> None:
    """Invalid records raise ValueError and leave no partial write behind."""
    ledger_file = tmp_path / "executions.jsonl"
    with pytest.raises(ValueError):
        append_action_record(str(ledger_file), bad, preserve_shape=True)
    assert not ledger_file.exists()


def test_try_append_action_record_warns_on_failure(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A failed append returns None, logs a WARNING with the path and mirrors it to log_fn."""
    caplog.set_level("WARNING", logger="sandbox_executor.ledger")
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    ledger_file = str(blocker / "executions.jsonl")
    mirrored: list[str] = []

    result = try_append_action_record(ledger_file, _review_record(1, 0), label="unit test", log_fn=mirrored.append)

    assert result is None
    messages = [rec.getMessage() for rec in caplog.records if rec.levelname == "WARNING"]
    assert any(ledger_file in msg and "unit test" in msg and "ACT-REV-1-0000" in msg for msg in messages)
    assert len(mirrored) == 1 and ledger_file in mirrored[0]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["not-a-directory"]


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="fork start method unavailable")
def test_concurrent_process_appends_do_not_interleave(tmp_path: Path) -> None:
    """Eight processes appending ~8 KiB records via LedgerLock produce 400 intact lines."""
    ledger_file = str(tmp_path / "executions.jsonl")
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_process_append_worker, args=(ledger_file, wid, 50)) for wid in range(8)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=60)
        assert proc.exitcode == 0
    _assert_clean_ledger(ledger_file, 400)


def test_concurrent_thread_appends_do_not_interleave(tmp_path: Path) -> None:
    """Eight threads appending ~8 KiB records via LedgerLock produce 400 intact lines."""
    ledger_file = str(tmp_path / "executions.jsonl")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_process_append_worker, ledger_file, wid, 50) for wid in range(8)]
        for fut in futures:
            fut.result()
    _assert_clean_ledger(ledger_file, 400)
