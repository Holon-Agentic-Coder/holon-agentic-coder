"""Unit and concurrency tests for LedgerLock advisory file locking (Bean 0127)."""

import concurrent.futures
import json
import os
import tempfile
import time

import pytest
from sandbox_executor.ledger import (
    LedgerLock,
    append_action_ledger,
    append_execution_record,
    atomic_ledger_append,
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
