"""The merged ledger reader: canonical rows plus ``pending/<stem>/*.jsonl`` fragments, canonical winning."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from sandbox_executor.cli.commands import execute as execute_command
from sandbox_executor.flow.git_refs import _read_ledger_rows
from sandbox_executor.knowledge import ActionIndexDatabase, ConsultEngine
from sandbox_executor.ledger import query_ledger, read_merged_rows, read_merged_rows_at_ref, read_merged_text
from sandbox_executor.ledger.fragments import fragment_path, iter_fragment_files, ledger_name_of, root_intent_id

from tests.flow.helpers import _git

LEDGER_REL = "holon-knowledge/ledger"
SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor"


def _jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(row) + "\n" for row in rows)


def _write(root: Path, rel: str, rows: list[dict]) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_jsonl(rows), encoding="utf-8")
    return path


def _plan(plan_id: str, intent: str, created_at: str, revision: int | None = None, **extra: object) -> dict:
    row = {"intent_branch": f"{intent}/_", "plan_id": plan_id, "created_at": created_at, **extra}
    return row | ({"ledger_revision": revision} if revision is not None else {})


def test_mixed_canonical_and_pending_rows_are_each_returned_once(tmp_path: Path) -> None:
    canonical = _write(
        tmp_path,
        f"{LEDGER_REL}/plans.jsonl",
        [_plan("P-1", "I-1-a", "2026-11-25T09:00:00Z"), _plan("P-3", "I-1-a", "2026-11-25T11:00:00+00:00")],
    )
    _write(tmp_path, f"{LEDGER_REL}/pending/plans/I-1-a.jsonl", [_plan("P-2", "I-1-a", "2026-11-25T10:00:00Z")])
    _write(tmp_path, f"{LEDGER_REL}/pending/plans/I-2-b.jsonl", [_plan("P-4", "I-2-b", "2026-11-25T11:30:00+01:00")])
    rows = read_merged_rows(str(canonical))
    assert [row["plan_id"] for row in rows] == ["P-1", "P-2", "P-4", "P-3"]


def test_a_duplicate_identity_returns_the_canonical_payload(tmp_path: Path) -> None:
    canonical = _write(
        tmp_path, f"{LEDGER_REL}/plans.jsonl", [_plan("P-1", "I-1-a", "2026-11-25T09:00:00Z", summary="canonical")]
    )
    _write(
        tmp_path,
        f"{LEDGER_REL}/pending/plans/I-1-a.jsonl",
        [_plan("P-1", "I-1-a", "2026-11-25T10:00:00Z", summary="pending")],
    )
    assert [row["summary"] for row in read_merged_rows(str(canonical))] == ["canonical"]


def test_an_earlier_fragment_wins_over_a_later_fragment(tmp_path: Path) -> None:
    canonical = tmp_path / LEDGER_REL / "intents.jsonl"
    row = {"branch": "I-1-a/_", "created_at": "2026-11-25T09:00:00Z"}
    _write(tmp_path, f"{LEDGER_REL}/pending/intents/I-1-a.jsonl", [row | {"goal": "first"}])
    _write(tmp_path, f"{LEDGER_REL}/pending/intents/I-9-z.jsonl", [row | {"goal": "second"}])
    assert [r["goal"] for r in read_merged_rows(str(canonical))] == ["first"]


def test_plan_revision_rows_are_both_kept(tmp_path: Path) -> None:
    canonical = _write(tmp_path, f"{LEDGER_REL}/plans.jsonl", [_plan("P-1", "I-1-a", "2026-11-25T09:00:00Z")])
    _write(
        tmp_path, f"{LEDGER_REL}/pending/plans/I-1-a.jsonl", [_plan("P-1", "I-1-a", "2026-11-25T10:00:00Z", revision=2)]
    )
    assert [row.get("ledger_revision") for row in read_merged_rows(str(canonical))] == [None, 2]


def test_distinct_execution_summary_rows_of_one_execution_are_both_kept(tmp_path: Path) -> None:
    row = {"entry_type": "summary", "plan_branch": "I-1-a/P-1/_", "execution_id": "E-1"}
    canonical = _write(tmp_path, f"{LEDGER_REL}/executions.jsonl", [row | {"created_at": "2026-11-25T09:00:00Z"}])
    _write(tmp_path, f"{LEDGER_REL}/pending/executions/I-1-a.jsonl", [row | {"created_at": "2026-11-25T10:00:00Z"}])
    assert len(read_merged_rows(str(canonical))) == 2


def test_action_rows_are_identified_by_action_id(tmp_path: Path) -> None:
    action = {"entry_type": "action", "action_id": "ACT-1", "created_at": "2026-11-25T09:00:00Z"}
    canonical = _write(tmp_path, f"{LEDGER_REL}/executions.jsonl", [action | {"outcome": "success"}])
    _write(tmp_path, f"{LEDGER_REL}/pending/executions/I-1-a.jsonl", [action | {"outcome": "failure"}])
    assert [row["outcome"] for row in read_merged_rows(str(canonical))] == ["success"]


def test_consult_indexes_a_canonical_action_once_with_the_canonical_payload(tmp_path: Path) -> None:
    action = {"entry_type": "action", "action_id": "ACT-1", "action": "Wire cache", "context_fingerprint": "fp"}
    canonical = _write(tmp_path, f"{LEDGER_REL}/executions.jsonl", [action | {"verdict": "success"}])
    _write(tmp_path, f"{LEDGER_REL}/pending/executions/I-1-a.jsonl", [action | {"verdict": "failure"}])
    _write(tmp_path, f"{LEDGER_REL}/pending/executions/I-2-b.jsonl", [{**action, "action_id": "ACT-2"}])
    db = ActionIndexDatabase(db_path=str(tmp_path / "index.db"))
    result = ConsultEngine(db=db, auto_sync_ledgers=[str(canonical)]).consult_records(query_text="cache")
    indexed = sorted((r["action_id"], r["verdict"]) for r in result.records)
    assert indexed == [("ACT-1", "success"), ("ACT-2", "unknown")]
    conn = db.get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM action_records_fts").fetchone()[0] == 2
        assert conn.execute("SELECT SUM(failure_count) FROM action_signatures").fetchone()[0] == 0
    finally:
        conn.close()


def _index_counts(db: ActionIndexDatabase) -> tuple[int, int, int]:
    conn = db.get_connection()
    try:
        records = conn.execute("SELECT COUNT(*) FROM action_records").fetchone()[0]
        fts = conn.execute("SELECT COUNT(*) FROM action_records_fts").fetchone()[0]
        successes = conn.execute("SELECT SUM(success_count) FROM action_signatures").fetchone()[0]
        return records, fts, successes
    finally:
        conn.close()


def test_consult_indexes_fragment_rows_beside_a_non_object_json_line(tmp_path: Path) -> None:
    canonical = _write(tmp_path, f"{LEDGER_REL}/executions.jsonl", [])
    fragment = tmp_path / LEDGER_REL / "pending/executions/I-1-a.jsonl"
    fragment.parent.mkdir(parents=True, exist_ok=True)
    action = {"entry_type": "action", "action_id": "ACT-1", "action": "Wire cache", "verdict": "success"}
    fragment.write_text("[1]\n" + _jsonl([action]), encoding="utf-8")
    db = ActionIndexDatabase(db_path=str(tmp_path / "index.db"))
    result = ConsultEngine(db=db, auto_sync_ledgers=[str(canonical)]).consult_records(query_text="cache")
    assert [r["action_id"] for r in result.records] == ["ACT-1"]


def test_a_promoted_fragment_row_is_not_indexed_again_from_canonical(tmp_path: Path) -> None:
    action = {"entry_type": "action", "action_id": "ACT-1", "action": "Wire cache", "verdict": "success"}
    canonical = _write(tmp_path, f"{LEDGER_REL}/executions.jsonl", [])
    fragment = _write(tmp_path, f"{LEDGER_REL}/pending/executions/I-1-a.jsonl", [action])
    db = ActionIndexDatabase(db_path=str(tmp_path / "index.db"))
    engine = ConsultEngine(db=db, auto_sync_ledgers=[str(canonical)])
    engine.consult_records(query_text="cache")
    assert _index_counts(db) == (1, 1, 1)
    canonical.write_text(_jsonl([action]), encoding="utf-8")
    fragment.unlink()
    result = engine.consult_records(query_text="cache")
    assert [r["action_id"] for r in result.records] == ["ACT-1"]
    assert _index_counts(db) == (1, 1, 1)


def test_canonical_duplicates_are_never_collapsed(tmp_path: Path) -> None:
    row = _plan("P-1", "I-1-a", "2026-11-25T09:00:00Z")
    canonical = _write(tmp_path, f"{LEDGER_REL}/plans.jsonl", [row, row])
    _write(tmp_path, f"{LEDGER_REL}/pending/plans/I-1-a.jsonl", [row])
    assert read_merged_rows(str(canonical)) == [row, row]


def test_a_path_outside_the_ledger_layout_ignores_pending(tmp_path: Path) -> None:
    scratch = _write(tmp_path, "scratch/executions.jsonl", [{"execution_id": "E-1"}])
    _write(tmp_path, "scratch/pending/executions/I-1-a.jsonl", [{"execution_id": "E-2"}])
    assert ledger_name_of(str(scratch)) is None
    assert iter_fragment_files(str(scratch)) == []
    assert read_merged_rows(str(scratch)) == [{"execution_id": "E-1"}]


def test_a_missing_canonical_file_still_returns_fragment_rows(tmp_path: Path) -> None:
    canonical = tmp_path / LEDGER_REL / "executions.jsonl"
    row = {"entry_type": "summary", "execution_id": "E-1", "created_at": "2026-11-25T09:00:00Z"}
    _write(tmp_path, f"{LEDGER_REL}/pending/executions/I-1-a.jsonl", [row])
    assert read_merged_rows(str(canonical)) == [row]
    assert query_ledger(str(canonical), entry_type="summary") == [row]
    assert read_merged_text(str(canonical)) == _jsonl([row])


def test_without_fragments_the_text_is_the_raw_canonical_text(tmp_path: Path) -> None:
    canonical = tmp_path / LEDGER_REL / "executions.jsonl"
    assert read_merged_text(str(canonical)) is None
    canonical.parent.mkdir(parents=True)
    raw = '{"b": 1,   "a": 2}\n\nnot json\n[1]\n'
    canonical.write_text(raw, encoding="utf-8")
    assert read_merged_text(str(canonical)) == raw
    assert read_merged_rows(str(canonical)) == [{"b": 1, "a": 2}]


def test_bad_lines_reach_the_callback(tmp_path: Path) -> None:
    canonical = tmp_path / LEDGER_REL / "plans.jsonl"
    canonical.parent.mkdir(parents=True)
    canonical.write_text('{"plan_id": "P-1"}\n{oops\n\n[2]\n', encoding="utf-8")
    bad: list[int] = []
    assert read_merged_rows(str(canonical), on_bad_line=lambda line_no, _exc: bad.append(line_no)) == [
        {"plan_id": "P-1"}
    ]
    assert bad == [2, 4]


@pytest.mark.parametrize(
    ("row", "root"),
    [
        ({"branch": "I-1-a/P-2-b--1-child/_"}, "I-1-a"),
        ({"intent_branch": "origin/I-1-a/_"}, "I-1-a"),
        ({"plan_branch": "I-5-x--2-legacy-child/P-1/_"}, "I-5-x"),
        ({"branch": "", "parent_branch": "I-7-p/_"}, "I-7-p"),
        ({"execution_id": "E-1"}, "_unscoped"),
    ],
)
def test_root_intent_id_and_fragment_path(row: dict, root: str) -> None:
    assert root_intent_id(row) == root
    canonical = f"/repo/{LEDGER_REL}/executions.jsonl"
    assert fragment_path(canonical, row) == f"/repo/{LEDGER_REL}/pending/executions/{root}.jsonl"


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def ledger_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    _write(repo, f"{LEDGER_REL}/intents.jsonl", [{"branch": "I-1-a/_", "created_at": "2026-11-25T09:00:00Z"}])
    _commit_all(repo, "canonical only")
    _git(repo, "tag", "canonical-only")
    _write(
        repo,
        f"{LEDGER_REL}/pending/intents/I-2-b.jsonl",
        [{"branch": "I-2-b/_", "created_at": "2026-11-25T10:00:00Z", "target_branch": "I-0-root/P-0/_"}],
    )
    _commit_all(repo, "with fragment")
    return repo


def test_a_git_ref_reader_finds_fragment_rows(ledger_repo: Path) -> None:
    rows = read_merged_rows_at_ref(str(ledger_repo), "HEAD", "intents.jsonl")
    assert [row["branch"] for row in rows] == ["I-1-a/_", "I-2-b/_"]
    assert _read_ledger_rows(str(ledger_repo), "HEAD", "intents.jsonl") == rows


def test_a_git_ref_without_pending_returns_canonical_only(ledger_repo: Path) -> None:
    rows = read_merged_rows_at_ref(str(ledger_repo), "canonical-only", "intents.jsonl")
    assert [row["branch"] for row in rows] == ["I-1-a/_"]
    assert read_merged_rows_at_ref(str(ledger_repo), "refs/heads/no-such-branch", "intents.jsonl") == []


def test_the_execute_intent_lookup_finds_a_fragment_only_row(ledger_repo: Path) -> None:
    plan_branch = "I-2-b/P-3-c/_"
    _git(ledger_repo, "update-ref", f"refs/remotes/origin/{plan_branch}", "HEAD")
    assert execute_command.target_branch(str(ledger_repo), plan_branch) == "I-0-root/P-0/_"


_LEDGER_MENTION = re.compile(
    r"(?:intents|plans|executions)\.jsonl|LEDGER_EXECUTIONS_REL|EXECUTIONS_JSONL_REL|EXECUTIONS_LEDGER_REL"
    r"|ledger_path|ledger_file|exec_file"
)
# (module, function) pairs that open a ledger-named path for reading outside ``ledger/``, and why each may.
_READ_OPEN_ALLOW_LIST = {
    ("calibration_touch_set.py", "touch_set_metrics"),  # opens the plan file, only names plans.jsonl in prose
    ("entrypoint/intent_creator.py", "main"),  # opens the intent JSON input; the ledger is appended to
    ("entrypoint/planner.py", "main"),  # opens prompt, template and plan files
    ("flow/stage5_ledger.py", "recorded_verdicts"),  # Stage 5 reads back its own reconciliation rows (writer side)
    ("knowledge/ledger_sync.py", "read_new_rows"),  # per-file index sync; consult syncs fragment files too
    ("knowledge/records.py", "reconcile_records"),  # Stage 5 reconciliation read-then-append (writer side)
}


def _opens_for_reading(function: ast.AST) -> bool:
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "read_text":
            return True
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open"):
            continue
        mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "mode"), None)
        if mode is None or (isinstance(mode, ast.Constant) and "r" in str(mode.value) and "+" not in str(mode.value)):
            return True
    return False


def test_no_new_direct_ledger_reader_appears_outside_the_ledger_package() -> None:
    readers: set[tuple[str, str]] = set()
    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(SRC_ROOT).as_posix()
        if rel.startswith("ledger/"):
            continue
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) or not _opens_for_reading(node):
                continue
            if _LEDGER_MENTION.search(ast.get_source_segment(source, node) or ""):
                readers.add((rel, node.name))
    unlisted = readers - _READ_OPEN_ALLOW_LIST
    assert not unlisted, f"read ledgers through sandbox_executor.ledger.read_merged_rows: {unlisted}"


def test_a_row_repeated_within_one_fragment_is_indexed_once(tmp_path: Path) -> None:
    action = {"entry_type": "action", "action_id": "ACT-1", "action": "Wire cache", "verdict": "success"}
    canonical = _write(tmp_path, f"{LEDGER_REL}/executions.jsonl", [])
    _write(tmp_path, f"{LEDGER_REL}/pending/executions/I-1-a.jsonl", [action, action])
    db = ActionIndexDatabase(db_path=str(tmp_path / "index.db"))
    ConsultEngine(db=db, auto_sync_ledgers=[str(canonical)]).consult_records(query_text="cache")
    assert _index_counts(db) == (1, 1, 1)


def test_a_fragment_recreated_larger_at_the_same_path_is_indexed_in_full(tmp_path: Path) -> None:
    def action(action_id: str) -> dict:
        return {"entry_type": "action", "action_id": action_id, "action": "Wire cache", "verdict": "success"}

    canonical = _write(tmp_path, f"{LEDGER_REL}/executions.jsonl", [])
    fragment = _write(tmp_path, f"{LEDGER_REL}/pending/executions/I-1-a.jsonl", [action("ACT-1")])
    db = ActionIndexDatabase(db_path=str(tmp_path / "index.db"))
    engine = ConsultEngine(db=db, auto_sync_ledgers=[str(canonical)])
    engine.consult_records(query_text="cache")
    canonical.write_text(_jsonl([action("ACT-1")]), encoding="utf-8")
    fragment.unlink()
    _write(tmp_path, f"{LEDGER_REL}/pending/executions/I-1-a.jsonl", [action("ACT-2"), action("ACT-3")])
    result = engine.consult_records(query_text="cache")
    assert sorted(r["action_id"] for r in result.records) == ["ACT-1", "ACT-2", "ACT-3"]
    assert _index_counts(db) == (3, 3, 3)


def test_repeated_canonical_rows_without_fragments_are_each_indexed(tmp_path: Path) -> None:
    action = {"entry_type": "action", "action_id": "ACT-1", "action": "Wire cache", "verdict": "success"}
    canonical = _write(tmp_path, f"{LEDGER_REL}/executions.jsonl", [action, action])
    db = ActionIndexDatabase(db_path=str(tmp_path / "index.db"))
    ConsultEngine(db=db, auto_sync_ledgers=[str(canonical)]).consult_records(query_text="cache")
    _, fts, successes = _index_counts(db)
    assert (fts, successes) == (2, 2)
