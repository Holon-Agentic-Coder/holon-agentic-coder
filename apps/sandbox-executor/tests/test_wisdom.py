"""Hermetic unit tests for Knowledge Base (KB) and Wisdom Base (WB) immutability."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sandbox_executor.knowledge import wisdom as wisdom_module
from sandbox_executor.knowledge.index import ActionIndexDatabase
from sandbox_executor.knowledge.stable_envelope import STABLE_CREATED_TS
from sandbox_executor.knowledge.wisdom import (
    load_index,
    persist_kb_fact,
    persist_wb_rule,
    promote_pending_candidates,
)


def _compute_sha256(file_path: str | Path) -> str:
    """Compute the SHA-256 hexadecimal digest for a given file."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def _stat_metadata(file_path: str | Path) -> tuple[str, int, bytes]:
    """Return SHA-256 digest, nanosecond modification timestamp, and raw bytes."""
    path = Path(file_path)
    stat = path.stat()
    raw = path.read_bytes()
    return _compute_sha256(path), stat.st_mtime_ns, raw


def test_persist_kb_fact_idempotency_and_immutability(tmp_path: Path) -> None:
    """Pre-existing KB fact files are immutable and must not be touched or overwritten."""
    kb_dir = str(tmp_path / "holon-knowledge" / "kb")

    initial_fact = {
        "kb_type": "pattern",
        "context_fingerprint": "fp_test_immutable_001",
        "created_ts": "2026-11-06T10:00:00Z",
        "payload": {"name": "Initial Pattern", "details": "Demonstration pattern"},
    }

    out_file = persist_kb_fact(initial_fact, kb_dir=kb_dir)
    assert os.path.exists(out_file)

    initial_sha, initial_mtime_ns, initial_bytes = _stat_metadata(out_file)

    # Attempt to persist again with modified payload and altered timestamps
    mutated_fact = {
        "kb_type": "pattern",
        "context_fingerprint": "fp_test_immutable_001",
        "created_ts": "2026-11-06T12:00:00Z",
        "payload": {"name": "Mutated Pattern Overwrite Attempt", "details": "Should be ignored"},
    }

    re_persisted_file = persist_kb_fact(mutated_fact, kb_dir=kb_dir)
    assert re_persisted_file == out_file

    second_sha, second_mtime_ns, second_bytes = _stat_metadata(out_file)

    assert second_sha == initial_sha
    assert second_mtime_ns == initial_mtime_ns
    assert second_bytes == initial_bytes

    # Verify original payload remained bit-for-bit intact
    with open(out_file, encoding="utf-8") as f:
        data = json.load(f)
    assert data["payload"]["name"] == "Initial Pattern"
    assert data["created_ts"] == "2026-11-06T10:00:00Z"


def test_persist_wb_rule_idempotency_and_immutability(tmp_path: Path) -> None:
    """Pre-existing WB rule files are immutable and must not be touched or overwritten."""
    wb_dir = str(tmp_path / "holon-knowledge" / "wisdom")

    initial_rule = {
        "wb_type": "heuristic",
        "context_fingerprint": "fp_test_immutable_rule_001",
        "created_ts": "2026-11-06T10:00:00Z",
        "payload": {"name": "Initial Heuristic", "advisory": "Perform hermetic verification"},
    }

    out_file = persist_wb_rule(initial_rule, wb_dir=wb_dir)
    assert os.path.exists(out_file)

    initial_sha, initial_mtime_ns, initial_bytes = _stat_metadata(out_file)

    # Attempt to persist again with modified contents
    mutated_rule = {
        "wb_type": "heuristic",
        "context_fingerprint": "fp_test_immutable_rule_001",
        "created_ts": "2026-11-06T12:00:00Z",
        "payload": {"name": "Mutated Rule Overwrite Attempt", "advisory": "Ignored"},
    }

    re_persisted_file = persist_wb_rule(mutated_rule, wb_dir=wb_dir)
    assert re_persisted_file == out_file

    second_sha, second_mtime_ns, second_bytes = _stat_metadata(out_file)

    assert second_sha == initial_sha
    assert second_mtime_ns == initial_mtime_ns
    assert second_bytes == initial_bytes

    with open(out_file, encoding="utf-8") as f:
        data = json.load(f)
    assert data["payload"]["name"] == "Initial Heuristic"


def test_update_directory_index_is_removed() -> None:
    """No shared index writer remains in the wisdom module."""
    assert not hasattr(wisdom_module, "_update_directory_index")


def test_persist_writes_only_the_entry_file(tmp_path: Path) -> None:
    """Persisting a KB fact or WB rule creates exactly the entry file and no index.json."""
    kb_file = persist_kb_fact({"kb_id": "KB-pattern-only", "kb_type": "pattern"}, kb_dir=str(tmp_path / "kb"))
    wb_file = persist_wb_rule({"wb_id": "WB-heuristic-only", "wb_type": "heuristic"}, wb_dir=str(tmp_path / "wisdom"))

    assert os.listdir(os.path.dirname(kb_file)) == ["KB-pattern-only.json"]
    assert os.listdir(os.path.dirname(wb_file)) == ["WB-heuristic-only.json"]


def test_load_index_orders_by_created_ts_then_id(tmp_path: Path) -> None:
    """load_index sorts by created_ts then kb_id, carries the KB index fields and ignores non-entry files."""
    kb_dir = str(tmp_path / "kb")
    for kb_id, created_ts in (
        ("KB-pattern-b", "2026-11-06T10:00:00+00:00"),
        ("KB-pattern-a", "2026-11-06T10:00:00+00:00"),
        ("KB-pattern-z", "2026-11-05T10:00:00+00:00"),
    ):
        persist_kb_fact({"kb_id": kb_id, "kb_type": "pattern", "created_ts": created_ts, "tags": ["t"]}, kb_dir=kb_dir)
    subfolder = os.path.join(kb_dir, "patterns")
    Path(subfolder, "index.json").write_text('{"entries": [{"kb_id": "KB-stale"}]}', encoding="utf-8")
    Path(subfolder, "notes.txt").write_text("not an entry", encoding="utf-8")

    entries = load_index(subfolder)

    assert [e["kb_id"] for e in entries] == ["KB-pattern-z", "KB-pattern-a", "KB-pattern-b"]
    for entry in entries:
        assert set(entry) == {"kb_id", "file", "kb_type", "status", "created_ts", "tags"}
        assert entry["file"] == f"{entry['kb_id']}.json"
        assert entry["tags"] == ["t"]


def test_load_index_wb_entries_and_missing_folder(tmp_path: Path) -> None:
    """WB entries carry the WB index fields; a missing folder yields an empty index."""
    wb_file = persist_wb_rule({"wb_id": "WB-heuristic-1", "wb_type": "heuristic"}, wb_dir=str(tmp_path / "wisdom"))

    entries = load_index(os.path.dirname(wb_file))

    assert len(entries) == 1
    assert set(entries[0]) == {"wb_id", "file", "wb_type", "status", "created_ts"}
    assert entries[0]["file"] == "WB-heuristic-1.json"
    assert load_index(str(tmp_path / "missing")) == []


def test_load_index_skips_unreadable_entry(tmp_path: Path) -> None:
    """One invalid entry file is skipped without hiding the valid entries."""
    kb_file = persist_kb_fact({"kb_id": "KB-pattern-good", "kb_type": "pattern"}, kb_dir=str(tmp_path / "kb"))
    subfolder = os.path.dirname(kb_file)
    Path(subfolder, "KB-pattern-bad.json").write_text("{not json", encoding="utf-8")
    Path(subfolder, "KB-pattern-list.json").write_text("[]", encoding="utf-8")

    assert [e["kb_id"] for e in load_index(subfolder)] == ["KB-pattern-good"]


class _ShiftingClock:
    """A datetime stand-in whose now() returns a later instant on every call."""

    calls = 0

    @classmethod
    def now(cls, tz: object = None) -> datetime:
        """Return a distinct instant per call."""
        cls.calls += 1
        return datetime(2026, 11, cls.calls, 12, 0, tzinfo=UTC)


def _kb_fact(success_count: int, wilson_lower: float, ladder_state: str) -> dict:
    """A KB pattern fact whose evidence counters vary per run."""
    return {
        "kb_id": "KB-pattern-deadbeef",
        "kb_type": "pattern",
        "evidence": {
            "ledger_refs": [f"act-{i}" for i in range(success_count - 5, success_count)],
            "success_count": success_count,
            "failure_count": 0,
            "wilson_lower": wilson_lower,
            "ladder_state": ladder_state,
        },
        "tags": ["refactor", "proven"],
        "payload": {"name": "Proven approach for src/a.py", "action": "Refactor a"},
    }


def _wb_rule(total_executions: int, wilson_lower: float) -> dict:
    """A WB heuristic rule whose ascension counters vary per run."""
    return {
        "wb_id": "WB-heuristic-deadbeef",
        "wb_type": "heuristic",
        "ascension_evidence": {
            "project_count": 1,
            "total_executions": total_executions,
            "wilson_lower": wilson_lower,
            "provenance": [{"kb_id": "KB-pattern-deadbeef"}],
        },
        "payload": {"name": "Universal heuristic: src/a.py", "advisory": "Consider the proven approach."},
    }


def test_persist_kb_fact_bytes_independent_of_clock_and_counters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runs with different clocks and counters write byte-identical KB files free of counters and clock times."""
    monkeypatch.setattr(wisdom_module, "datetime", _ShiftingClock)
    first = persist_kb_fact(_kb_fact(10, 0.7, "likely-success"), kb_dir=str(tmp_path / "a" / "kb"))
    second = persist_kb_fact(_kb_fact(37, 0.9, "proven-success"), kb_dir=str(tmp_path / "b" / "kb"))

    assert Path(first).read_bytes() == Path(second).read_bytes()
    data = json.loads(Path(first).read_text(encoding="utf-8"))
    assert data["created_ts"] == data["updated_ts"] == STABLE_CREATED_TS
    assert data["evidence"] == {}


def test_persist_wb_rule_bytes_independent_of_clock_and_counters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runs with different clocks and counters write byte-identical WB files keeping only stable evidence."""
    monkeypatch.setattr(wisdom_module, "datetime", _ShiftingClock)
    first = persist_wb_rule(_wb_rule(22, 0.86), wb_dir=str(tmp_path / "a" / "wisdom"))
    second = persist_wb_rule(_wb_rule(31, 0.89), wb_dir=str(tmp_path / "b" / "wisdom"))

    assert Path(first).read_bytes() == Path(second).read_bytes()
    data = json.loads(Path(first).read_text(encoding="utf-8"))
    assert data["created_ts"] == STABLE_CREATED_TS
    assert data["ascension_evidence"] == {"project_count": 1, "provenance": [{"kb_id": "KB-pattern-deadbeef"}]}


_LEGACY_KB = {
    "kb_id": "KB-pattern-legacy01",
    "kb_type": "pattern",
    "version": "1.0",
    "created_ts": "2026-11-25T23:52:44.461824+00:00",
    "updated_ts": "2026-11-25T23:52:44.461824+00:00",
    "created_by": "holon-promotion-engine",
    "status": "active",
    "evidence": {"ledger_refs": ["act-1"], "success_count": 21, "wilson_lower": 0.85},
    "tags": ["legacy"],
    "human_approved": False,
    "payload": {"description": "Succeeded in 21/21 executions (0.85)."},
}

_LEGACY_WB = {
    "wb_id": "WB-heuristic-legacy01",
    "wb_type": "heuristic",
    "version": "1.0",
    "created_ts": "2026-11-25T23:52:44.461824+00:00",
    "scope": "global",
    "status": "active",
    "ascension_evidence": {"project_count": 1, "total_executions": 21, "wilson_lower": 0.85},
    "human_approved": False,
    "payload": {"advisory": "Succeeded in 21/21 executions (0.85)."},
}


def _write_legacy(path: Path, envelope: dict) -> Path:
    """Write a legacy-shaped envelope by hand, as older promotion runs did."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    return path


def test_persist_never_rewrites_existing_file(tmp_path: Path) -> None:
    """An existing legacy KB or WB file keeps its bytes and mtime when the same id is persisted again."""
    kb_file = _write_legacy(tmp_path / "kb" / "patterns" / "KB-pattern-legacy01.json", _LEGACY_KB)
    wb_file = _write_legacy(tmp_path / "wisdom" / "heuristics" / "WB-heuristic-legacy01.json", _LEGACY_WB)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (kb_file, wb_file)}

    kb_out = persist_kb_fact({"kb_id": "KB-pattern-legacy01", "kb_type": "pattern"}, kb_dir=str(tmp_path / "kb"))
    wb_out = persist_wb_rule({"wb_id": "WB-heuristic-legacy01"}, wb_dir=str(tmp_path / "wisdom"))

    assert (kb_out, wb_out) == (str(kb_file), str(wb_file))
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (kb_file, wb_file)} == before


def test_load_index_reads_legacy_and_stable_files(tmp_path: Path) -> None:
    """load_index lists a legacy file and a newly written stable file together, the epoch-dated one first."""
    kb_dir = tmp_path / "kb"
    _write_legacy(kb_dir / "patterns" / "KB-pattern-legacy01.json", _LEGACY_KB)
    persist_kb_fact({"kb_id": "KB-pattern-stable01", "kb_type": "pattern"}, kb_dir=str(kb_dir))
    wb_dir = tmp_path / "wisdom"
    _write_legacy(wb_dir / "heuristics" / "WB-heuristic-legacy01.json", _LEGACY_WB)
    persist_wb_rule({"wb_id": "WB-heuristic-stable01"}, wb_dir=str(wb_dir))

    kb_entries = load_index(str(kb_dir / "patterns"))
    wb_entries = load_index(str(wb_dir / "heuristics"))

    assert [(e["kb_id"], e["file"], e["created_ts"]) for e in kb_entries] == [
        ("KB-pattern-stable01", "KB-pattern-stable01.json", STABLE_CREATED_TS),
        ("KB-pattern-legacy01", "KB-pattern-legacy01.json", _LEGACY_KB["created_ts"]),
    ]
    assert [(e["wb_id"], e["file"], e["created_ts"]) for e in wb_entries] == [
        ("WB-heuristic-stable01", "WB-heuristic-stable01.json", STABLE_CREATED_TS),
        ("WB-heuristic-legacy01", "WB-heuristic-legacy01.json", _LEGACY_WB["created_ts"]),
    ]


def test_promote_pending_candidates_zero_touch_immutability(tmp_path: Path) -> None:
    """promote_pending_candidates leaves all fact files untouched on repeat runs."""
    db_path = str(tmp_path / "test_action_index.db")
    db = ActionIndexDatabase(db_path=db_path)
    ledger_path = str(tmp_path / "executions.jsonl")

    kb_dir = str(tmp_path / "holon-knowledge" / "kb")
    wb_dir = str(tmp_path / "holon-knowledge" / "wisdom")

    # 1. Seed ledger with 10 successes for pattern/heuristic promotion and 3 failures for failure mode promotion
    fp_success = "fp_success_immutability"
    fp_failure = "fp_failure_immutability"

    with open(ledger_path, "w", encoding="utf-8") as f:
        for i in range(10):
            f.write(
                json.dumps(
                    {
                        "action_id": f"act-succ-{i}",
                        "context_fingerprint": fp_success,
                        "action": "Deterministic hermetic builder",
                        "target": "src/builder.py",
                        "verdict": "success",
                    }
                )
                + "\n"
            )
        for i in range(3):
            f.write(
                json.dumps(
                    {
                        "action_id": f"act-fail-{i}",
                        "context_fingerprint": fp_failure,
                        "action": "Unchecked file write operation",
                        "target": "src/legacy.py",
                        "verdict": "failure",
                        "error_context": "FileExistsError: file already locked",
                    }
                )
                + "\n"
            )

    db.sync_ledger(ledger_path)

    # First promotion cycle: persists new candidate facts
    first_promoted = promote_pending_candidates(
        index_db=db,
        kb_dir=kb_dir,
        wb_dir=wb_dir,
        min_trials=2,
        dry_run=False,
    )
    assert len(first_promoted) >= 2

    # Snapshot all files in kb_dir and wb_dir
    file_snapshots: dict[str, tuple[str, int, bytes]] = {}
    for base_dir in (kb_dir, wb_dir):
        for root, _dirs, files in os.walk(base_dir):
            for file_name in files:
                file_path = os.path.join(root, file_name)
                file_snapshots[file_path] = _stat_metadata(file_path)

    assert len(file_snapshots) > 0

    # Second promotion cycle against unchanged database
    second_promoted = promote_pending_candidates(
        index_db=db,
        kb_dir=kb_dir,
        wb_dir=wb_dir,
        min_trials=2,
        dry_run=False,
    )
    assert second_promoted == []

    # Collect current files and assert exact identity
    current_files: set[str] = set()
    for base_dir in (kb_dir, wb_dir):
        for root, _dirs, files in os.walk(base_dir):
            for file_name in files:
                file_path = os.path.join(root, file_name)
                current_files.add(file_path)

    assert current_files == set(file_snapshots.keys())

    for file_path, (prev_sha, prev_mtime_ns, prev_bytes) in file_snapshots.items():
        curr_sha, curr_mtime_ns, curr_bytes = _stat_metadata(file_path)
        assert curr_sha == prev_sha, f"SHA-256 digest mutated for {file_path}"
        assert curr_mtime_ns == prev_mtime_ns, f"Nanosecond mtime mutated for {file_path}"
        assert curr_bytes == prev_bytes, f"Bytes mutated for {file_path}"
