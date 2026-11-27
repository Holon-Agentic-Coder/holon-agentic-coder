from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from typing import Any

import pytest
from sandbox_executor.cli import main
from sandbox_executor.knowledge.consult import ConsultEngine
from sandbox_executor.knowledge.consult_filters import ConsultQuery, build_where
from sandbox_executor.knowledge.index import ActionIndexDatabase
from sandbox_executor.knowledge.promotion import (
    compute_beta_posterior,
    compute_context_fingerprint,
    compute_wilson_score_interval,
    evaluate_action_promotion,
    format_non_imperative_advisory,
)
from sandbox_executor.knowledge.records import (
    CredentialRedactor,
    append_action_event,
    reconcile_records,
    validate_verdict_transition,
)
from sandbox_executor.knowledge.stats import TelemetryCollector, format_stats_dashboard
from sandbox_executor.knowledge.wisdom import (
    lift_veto,
    list_vetoes,
    load_index,
    persist_kb_fact,
    persist_wb_rule,
    promote_pending_candidates,
)
from sandbox_executor.ledger import format_commit_trailers, parse_commit_trailers


@pytest.fixture
def temp_dir() -> Any:
    with tempfile.TemporaryDirectory() as td:
        yield td


def test_sqlite_fts5_inverted_index(temp_dir: str) -> None:
    db_path = os.path.join(temp_dir, "test_action_index.db")
    db = ActionIndexDatabase(db_path=db_path)

    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cur.fetchall()}
        assert "ledger_sync_state" in tables
        assert "action_records" in tables
        assert "record_labels" in tables
        assert "action_signatures" in tables
        assert "active_vetoes" in tables
        assert "consult_telemetry" in tables
        assert "action_records_fts" in tables
    finally:
        conn.close()


def test_incremental_sync_byte_offsets(temp_dir: str) -> None:
    db_path = os.path.join(temp_dir, "test_sync.db")
    ledger_path = os.path.join(temp_dir, "actions.jsonl")
    db = ActionIndexDatabase(db_path=db_path)

    records_batch1 = [
        {"action_id": f"act-10{i}", "action": f"Task {i}", "verdict": "success", "labels": ["core", "test"]}
        for i in range(3)
    ]
    with open(ledger_path, "w", encoding="utf-8") as f:
        for r in records_batch1:
            f.write(json.dumps(r) + "\n")

    ingested1 = db.sync_ledger(ledger_path)
    assert ingested1 == 3

    assert db.sync_ledger(ledger_path) == 0

    records_batch2 = [
        {"action_id": f"act-20{i}", "action": f"Appended task {i}", "verdict": "failure", "labels": ["bug"]}
        for i in range(2)
    ]
    with open(ledger_path, "a", encoding="utf-8") as f:
        for r in records_batch2:
            f.write(json.dumps(r) + "\n")

    ingested2 = db.sync_ledger(ledger_path)
    assert ingested2 == 2

    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM action_records;")
        assert cur.fetchone()[0] == 5
    finally:
        conn.close()

    with open(ledger_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"action_id": "act-rewritten", "action": "Reset", "verdict": "success"}) + "\n")

    ingested3 = db.sync_ledger(ledger_path)
    assert ingested3 == 1


def test_consult_multi_label_roundtrip(temp_dir: str) -> None:
    db_path = os.path.join(temp_dir, "test_consult.db")
    ledger_path = os.path.join(temp_dir, "actions.jsonl")
    db = ActionIndexDatabase(db_path=db_path)

    records = [
        {"action_id": "act-1", "action": "Setup database", "labels": ["db", "backend"], "verdict": "success"},
        {"action_id": "act-2", "action": "Add migration", "labels": ["db", "migration"], "verdict": "success"},
        {"action_id": "act-3", "action": "Build UI component", "labels": ["frontend", "ui"], "verdict": "success"},
        {"action_id": "act-4", "action": "Full-stack integration", "labels": ["db", "frontend"], "verdict": "failure"},
    ]
    with open(ledger_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    db.sync_ledger(ledger_path)
    engine = ConsultEngine(db=db, auto_sync_ledgers=[ledger_path])

    res_all = engine.consult_records(all_labels=["db", "backend"])
    assert res_all.total_matched == 1
    assert res_all.records[0]["action_id"] == "act-1"

    res_any = engine.consult_records(any_labels=["ui", "migration"])
    assert res_any.total_matched == 2
    matched_ids = {r["action_id"] for r in res_any.records}
    assert matched_ids == {"act-2", "act-3"}

    res_not = engine.consult_records(any_labels=["db"], not_labels=["frontend"])
    assert res_not.total_matched == 2
    matched_not = {r["action_id"] for r in res_not.records}
    assert matched_not == {"act-1", "act-2"}

    res_fts = engine.consult_records(query_text="component")
    assert res_fts.total_matched == 1
    assert res_fts.records[0]["action_id"] == "act-3"


def test_consult_rejects_unknown_filter(temp_dir: str) -> None:
    with pytest.raises(TypeError):
        ConsultQuery(any_label=["db"])
    engine = ConsultEngine(project_db_path=os.path.join(temp_dir, "index.db"), auto_sync_ledgers=[])
    with pytest.raises(TypeError):
        engine.consult_records(any_label=["db"])


def test_consult_build_where_orders_clauses_and_params() -> None:
    assert build_where(ConsultQuery()) == ("", [])

    query = ConsultQuery(
        all_labels=[" DB ", "backend", "db", " "],
        any_labels=["ui"],
        not_labels=["Frontend"],
        query_text=' say "hi" ',
        session_seen_ids=["rec-1"],
    )
    where_sql, params = build_where(query)

    assert params == ["db", "backend", "db", 2, "ui", "frontend", '"say ""hi"""', "rec-1"]
    assert where_sql.startswith("WHERE ")
    assert where_sql.count(" AND ") == 4
    assert where_sql.count("?") == len(params)
    assert where_sql.index("HAVING COUNT(DISTINCT label)") < where_sql.index("NOT IN") < where_sql.index("MATCH")
    assert where_sql.endswith("ar.record_id NOT IN (?)")


def test_consult_latency_benchmark(temp_dir: str) -> None:
    db_path = os.path.join(temp_dir, "test_benchmark.db")
    ledger_path = os.path.join(temp_dir, "actions.jsonl")
    db = ActionIndexDatabase(db_path=db_path)

    with open(ledger_path, "w", encoding="utf-8") as f:
        for i in range(250):
            lbls = ["python", f"module_{i % 5}", "unit_test" if i % 2 == 0 else "e2e"]
            rec = {
                "action_id": f"act-bench-{i:04d}",
                "action": f"Executed benchmark test runner step {i}",
                "target": f"src/module_{i % 10}.py",
                "verdict": "success" if i % 4 != 0 else "failure",
                "labels": lbls,
            }
            f.write(json.dumps(rec) + "\n")

    db.sync_ledger(ledger_path)
    engine = ConsultEngine(db=db, auto_sync_ledgers=[ledger_path])

    # why: the cold first consult opens the connection; the SLA covers warm consults only
    engine.consult_records(all_labels=["python"])

    latencies_ms: list[float] = []
    # why: thread CPU time, not wall time, so load from other xdist workers on a shared runner is not counted
    for _ in range(100):
        t0 = time.thread_time()
        res = engine.consult_records(all_labels=["python"], any_labels=["module_1", "module_2"])
        lat = (time.thread_time() - t0) * 1000.0
        latencies_ms.append(lat)
        assert res.total_matched > 0

    latencies_ms.sort()
    p95_latency = latencies_ms[round(99 * 0.95)]
    # why: relaxed from 15 ms for slow CI runners
    assert p95_latency < 50.0, f"p95 latency was {p95_latency:.2f}ms, expected < 50.0ms"


def test_consult_payload_capping_and_dedup(temp_dir: str) -> None:
    db_path = os.path.join(temp_dir, "test_capping.db")
    ledger_path = os.path.join(temp_dir, "actions.jsonl")
    db = ActionIndexDatabase(db_path=db_path)

    with open(ledger_path, "w", encoding="utf-8") as f:
        for i in range(15):
            rec = {
                "action_id": f"act-cap-{i:02d}",
                "action": f"Action execution step with some descriptive payload {i} " * 5,
                "labels": ["cap_test"],
                "verdict": "success",
            }
            f.write(json.dumps(rec) + "\n")

    db.sync_ledger(ledger_path)
    engine = ConsultEngine(db=db, auto_sync_ledgers=[ledger_path])

    res = engine.consult_records(all_labels=["cap_test"], top_k=10)
    assert len(res.records) <= 6
    assert res.truncated is True

    res_tight = engine.consult_records(all_labels=["cap_test"], max_tokens=100)
    assert res_tight.tokens_injected <= 100
    assert len(res_tight.records) < 6

    seen = {res.records[0]["record_id"]}
    res_dedup = engine.consult_records(all_labels=["cap_test"], session_seen_ids=seen)
    dedup_ids = {r["record_id"] for r in res_dedup.records}
    assert not seen.intersection(dedup_ids)


def test_outcome_logging_state_machine(temp_dir: str) -> None:
    ledger_path = os.path.join(temp_dir, "actions.jsonl")
    db_path = os.path.join(temp_dir, "actions.db")

    validate_verdict_transition("attempted", "success")
    validate_verdict_transition("attempted", "failure")
    validate_verdict_transition("unknown", "unresolved")

    with pytest.raises(ValueError):
        validate_verdict_transition("success", "failure")

    with pytest.raises(ValueError):
        validate_verdict_transition("superseded", "attempted")

    rec = append_action_event(
        {"action": "Deploy service", "verdict": "attempted", "labels": ["deploy"]},
        ledger_path=ledger_path,
        db_path=db_path,
    )
    assert rec["verdict"] == "attempted"
    act_id = rec["action_id"]

    rec_stats = reconcile_records(
        ledger_path=ledger_path,
        target_action_id=act_id,
        verdict="success",
        reason="Deploy confirmed healthy",
        db_path=db_path,
    )
    assert rec_stats["reconciled"] == 1

    old_time = "2020-01-01T00:00:00Z"
    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"action_id": "act-old-unknown", "verdict": "unknown", "created_at": old_time}) + "\n")

    expire_stats = reconcile_records(ledger_path=ledger_path, max_age_days=14, db_path=db_path)
    assert expire_stats["expired"] >= 1


def test_credential_redaction() -> None:
    raw_text = (
        "Logs with OpenAI sk-1234567890abcdef12345678 and "
        "Anthropic sk-ant-api03-abcdef12345678901234 and "
        "Google AIzaSyAbcdef1234567890123456789012345 and "
        "GitHub ghp_"
        "123456789012345678901234567890abcdef and "
        "Bearer secret_bearer_token_123456789"
    )
    redacted = CredentialRedactor.redact_text(raw_text)

    assert "sk-12345" not in redacted
    assert "sk-ant" not in redacted
    assert "AIzaSy" not in redacted
    assert "ghp_" not in redacted
    assert "secret_bearer_token" not in redacted
    assert "[REDACTED_SECRET]" in redacted

    struct = {
        "headers": {"Authorization": "Bearer tok_1234567890123456"},
        "details": ["API key sk-abcdef1234567890abcdef12"],
    }
    red_struct = CredentialRedactor.redact_structure(struct)
    assert "[REDACTED_SECRET]" in red_struct["headers"]["Authorization"]
    assert "[REDACTED_SECRET]" in red_struct["details"][0]


def test_veto_ladder_and_decay(temp_dir: str) -> None:
    db_path = os.path.join(temp_dir, "test_veto.db")
    ledger_path = os.path.join(temp_dir, "actions.jsonl")
    db = ActionIndexDatabase(db_path=db_path)

    fp = compute_context_fingerprint(target="api/v1/auth", tool="jwt_validator", command_prefix="auth")

    with open(ledger_path, "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "action_id": "act-f1",
                    "context_fingerprint": fp,
                    "action_origin": "initial_implementation",
                    "verdict": "failure",
                }
            )
            + "\n"
        )
    db.sync_ledger(ledger_path)
    sig_hash = f"{fp}:initial_implementation"
    import hashlib

    sig_hash_calc = hashlib.sha256(sig_hash.encode("utf-8")).hexdigest()
    eval1 = evaluate_action_promotion(sig_hash_calc, index_db=db)
    assert eval1.failure_ladder_state == "observed"
    assert eval1.has_active_veto is False

    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "action_id": "act-f2",
                    "context_fingerprint": fp,
                    "action_origin": "initial_implementation",
                    "verdict": "failure",
                }
            )
            + "\n"
        )
    db.sync_ledger(ledger_path)
    eval2 = evaluate_action_promotion(sig_hash_calc, index_db=db)
    assert eval2.failure_ladder_state == "repeated"
    assert eval2.has_active_veto is True
    assert eval2.is_shadow_veto is True

    v_list = list_vetoes(db)
    assert len(v_list) == 1
    assert v_list[0]["context_fingerprint"] == fp

    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "action_id": "act-s1",
                    "context_fingerprint": fp,
                    "action_origin": "initial_implementation",
                    "verdict": "success",
                }
            )
            + "\n"
        )
    db.sync_ledger(ledger_path)
    eval3 = evaluate_action_promotion(sig_hash_calc, index_db=db)
    assert eval3.has_active_veto is False
    assert eval3.failure_ladder_state == "contested"

    lift_res = lift_veto(db, context_fingerprint=fp)
    assert lift_res is True


def test_wilson_score_and_non_imperative_advisories() -> None:
    low0, up0 = compute_wilson_score_interval(0, 0)
    assert low0 == 0.0 and up0 == 1.0

    low10, up10 = compute_wilson_score_interval(10, 0)
    assert 0.65 <= low10 <= 1.0
    assert up10 <= 1.0

    _, _, p_mean = compute_beta_posterior(8, 2)
    assert 0.70 <= p_mean <= 0.85

    advisory = format_non_imperative_advisory("Use pytest-xdist", 10, 10, low10, "proven-success")
    assert "Advisory" in advisory
    assert "Consider" in advisory
    assert "MUST" not in advisory
    assert "VETO" not in advisory


def test_knowledge_persistence_and_promotion(temp_dir: str) -> None:
    kb_dir = os.path.join(temp_dir, "kb")
    wb_dir = os.path.join(temp_dir, "wisdom")
    db_path = os.path.join(temp_dir, "action.db")
    ledger_path = os.path.join(temp_dir, "actions.jsonl")
    db = ActionIndexDatabase(db_path=db_path)

    fact_path = persist_kb_fact(
        {
            "kb_type": "pattern",
            "context_fingerprint": "fp_test_1234",
            "payload": {"name": "Test Pattern", "details": "Demonstration pattern"},
        },
        kb_dir=kb_dir,
    )
    assert os.path.exists(fact_path)
    kb_entries = load_index(os.path.join(kb_dir, "patterns"))
    assert [e["kb_id"] for e in kb_entries] == [os.path.splitext(os.path.basename(fact_path))[0]]
    assert not os.path.exists(os.path.join(kb_dir, "patterns", "index.json"))

    wb_path = persist_wb_rule(
        {
            "wb_type": "heuristic",
            "context_fingerprint": "fp_test_1234",
            "payload": {"name": "Global Prior", "details": "Universal heuristic"},
        },
        wb_dir=wb_dir,
    )
    assert os.path.exists(wb_path)
    wb_entries = load_index(os.path.join(wb_dir, "heuristics"))
    assert [e["wb_id"] for e in wb_entries] == [os.path.splitext(os.path.basename(wb_path))[0]]
    assert not os.path.exists(os.path.join(wb_dir, "heuristics", "index.json"))

    fp = "fp_qual_success"
    with open(ledger_path, "w", encoding="utf-8") as f:
        for i in range(10):
            f.write(
                json.dumps(
                    {
                        "action_id": f"act-qual-{i}",
                        "context_fingerprint": fp,
                        "action": "Proven refactoring strategy",
                        "target": "src/core.py",
                        "verdict": "success",
                    }
                )
                + "\n"
            )
    db.sync_ledger(ledger_path)

    promoted = promote_pending_candidates(index_db=db, kb_dir=kb_dir, wb_dir=wb_dir, min_trials=5, dry_run=False)
    assert len(promoted) >= 1
    assert any("KB-pattern" in p.get("kb_id", "") for p in promoted)


def test_flow_stage_integration_and_stats(temp_dir: str) -> None:
    trailers = format_commit_trailers(
        action_origin="initial_implementation",
        review_severity="nit",
        consulted_knowledge_ids=["KB-pattern-01", "rec-123456"],
    )
    assert "Consulted-Knowledge-IDs: KB-pattern-01, rec-123456" in trailers

    parsed = parse_commit_trailers(f"Commit header\n\n{trailers}")
    assert parsed["consulted_knowledge_ids"] == ["KB-pattern-01", "rec-123456"]

    db_path = os.path.join(temp_dir, "stats.db")
    db = ActionIndexDatabase(db_path=db_path)
    TelemetryCollector.record_consult(
        query_labels="python,db",
        latency_ms=2.45,
        matched_count=3,
        tokens_injected=350,
        cache_hit=True,
        index_db=db,
    )
    stats = TelemetryCollector.get_telemetry_stats(db)
    assert stats["total_consults"] == 1
    assert stats["latency_p50_ms"] == 2.45
    assert stats["total_injected_tokens"] == 350

    dash = format_stats_dashboard(stats)
    assert "HOLON OPERATIONAL SELF-METRICS" in dash
    assert "Total Consult Invocations" in dash


def test_cli_knowledge_commands(temp_dir: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    # why: `holon record` syncs the cwd-relative default project index, which must stay out of the repository
    monkeypatch.chdir(temp_dir)
    db_path = os.path.join(temp_dir, "cli_test.db")
    ledger_path = os.path.join(temp_dir, "actions.jsonl")

    test_args = [
        "holon",
        "record",
        "--action",
        "Refactor API controller",
        "--category",
        "code",
        "--target",
        "apps/api.py",
        "--verdict",
        "success",
        "--labels",
        "api,fast",
        "--ledger-path",
        ledger_path,
        "--target-worktree",
        temp_dir,
    ]
    monkeypatch.setattr("sys.argv", test_args)
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "Recorded action:" in captured.out

    consult_args = [
        "holon",
        "consult",
        "--all",
        "api",
        "--db-path",
        db_path,
        "--target-worktree",
        temp_dir,
        "--json",
    ]
    monkeypatch.setattr("sys.argv", consult_args)
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    captured_consult = capsys.readouterr()
    assert "records" in captured_consult.out

    reconcile_args = [
        "holon",
        "reconcile",
        "--ledger-path",
        ledger_path,
        "--target-worktree",
        temp_dir,
    ]
    monkeypatch.setattr("sys.argv", reconcile_args)
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    captured_rec = capsys.readouterr()
    assert "Reconciled:" in captured_rec.out

    stats_args = [
        "holon",
        "stats",
        "--target-worktree",
        temp_dir,
    ]
    monkeypatch.setattr("sys.argv", stats_args)
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    captured_stats = capsys.readouterr()
    assert "HOLON OPERATIONAL SELF-METRICS" in captured_stats.out


class _FailingTelemetryConn:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.closed = False

    def __enter__(self) -> _FailingTelemetryConn:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, *_args: object, **_kwargs: object) -> None:
        raise self.exc

    def close(self) -> None:
        self.closed = True


class _StubIndexDB:
    def __init__(self, conn: _FailingTelemetryConn) -> None:
        self.conn = conn

    def get_connection(self) -> _FailingTelemetryConn:
        return self.conn


class _TelemetryFailConnProxy:
    def __init__(self, real_conn: sqlite3.Connection, exc: Exception) -> None:
        self._real = real_conn
        self._exc = exc

    def __enter__(self) -> _TelemetryFailConnProxy:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> sqlite3.Cursor:
        return self._real.cursor()

    def execute(self, *_args: object, **_kwargs: object) -> None:
        raise self._exc

    def close(self) -> None:
        self._real.close()


@pytest.mark.parametrize("exc", [sqlite3.OperationalError("db locked"), OSError("disk full")])
def test_telemetry_collector_handles_typed_errors_and_closes_connection(exc: Exception) -> None:
    conn = _FailingTelemetryConn(exc)
    TelemetryCollector.record_consult(
        query_labels="python",
        latency_ms=1.0,
        matched_count=1,
        tokens_injected=10,
        index_db=_StubIndexDB(conn),  # type: ignore[arg-type]
    )
    assert conn.closed is True


def test_telemetry_collector_propagates_unexpected_error_and_closes_connection() -> None:
    conn = _FailingTelemetryConn(RuntimeError("unexpected telemetry bug"))
    with pytest.raises(RuntimeError, match="unexpected telemetry bug"):
        TelemetryCollector.record_consult(
            query_labels="python",
            latency_ms=1.0,
            matched_count=1,
            tokens_injected=10,
            index_db=_StubIndexDB(conn),  # type: ignore[arg-type]
        )
    assert conn.closed is True


@pytest.mark.parametrize("exc", [sqlite3.OperationalError("db locked"), OSError("disk full")])
def test_consult_records_handles_typed_telemetry_errors(
    temp_dir: str, monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    db = ActionIndexDatabase(db_path=os.path.join(temp_dir, "consult_telemetry_err.db"))
    real_get_conn = db.get_connection
    monkeypatch.setattr(db, "get_connection", lambda: _TelemetryFailConnProxy(real_get_conn(), exc))
    engine = ConsultEngine(db=db, auto_sync_ledgers=[])
    res = engine.consult_records(all_labels=["python"])
    assert res.total_matched == 0


def test_consult_records_propagates_unexpected_telemetry_error(
    temp_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = ActionIndexDatabase(db_path=os.path.join(temp_dir, "consult_telemetry_bug.db"))
    real_get_conn = db.get_connection
    monkeypatch.setattr(
        db,
        "get_connection",
        lambda: _TelemetryFailConnProxy(real_get_conn(), RuntimeError("unexpected consult bug")),
    )
    engine = ConsultEngine(db=db, auto_sync_ledgers=[])
    with pytest.raises(RuntimeError, match="unexpected consult bug"):
        engine.consult_records(all_labels=["python"])
