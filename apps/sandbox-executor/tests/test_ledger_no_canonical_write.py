"""No append writes a canonical ledger file: every row lands in ``pending/<stem>/<root-intent-id>.jsonl``."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import MagicMock

from sandbox_executor.entrypoint.sync_conflict import record_sync_failure
from sandbox_executor.knowledge import append_action_event
from sandbox_executor.ledger import (
    ActionRecord,
    SummaryRecord,
    append_action_record,
    append_summary_record,
    atomic_ledger_append,
    read_merged_rows,
    try_append_action_record,
    try_append_ledger_line,
)
from sandbox_executor.ledger.fragments import LEDGER_NAMES

LEDGER_REL = "holon-knowledge/ledger"
INTENT = "I-7-guard"
SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor"

# Modules that may name the lock or open a ledger for append outside the ledger package, each with its reason.
# Empty today: the merge-time canonical repairs (flow/ledger_interleave.py, flow/target_sync_merge.py,
# flow/review/conflict_merge.py) rewrite canonical files only through ledger/reconcile.py.
CANONICAL_WRITER_ALLOWLIST: dict[str, str] = {}

SEED_ROWS = {
    "intents.jsonl": {"branch": "I-0-seed/_", "intent_id": "I-0-seed", "created_at": "2026-01-01T00:00:00Z"},
    "plans.jsonl": {"plan_id": "P-0", "intent_branch": "I-0-seed/_", "created_at": "2026-01-01T00:00:00Z"},
    "executions.jsonl": {"execution_id": "E-0", "plan_branch": "I-0-seed/P-0/_", "created_at": "2026-01-01T00:00:00Z"},
}


def _seed_repo(root: Path) -> dict[str, bytes]:
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text(f"ref: refs/heads/{INTENT}/P-1/E-1/_\n", encoding="utf-8")
    ledger_dir = root / LEDGER_REL
    ledger_dir.mkdir(parents=True)
    for name, row in SEED_ROWS.items():
        (ledger_dir / name).write_text(json.dumps(row) + "\n", encoding="utf-8")
    return {name: (ledger_dir / name).read_bytes() for name in LEDGER_NAMES}


def _action(action_id: str) -> ActionRecord:
    return ActionRecord(
        action_id=action_id, execution_id="E-1", action="ran", category="code", target="x", outcome="success"
    )


def _drive_every_append_api(root: Path) -> None:
    ledger_dir = root / LEDGER_REL
    executions, plans, intents = (
        str(ledger_dir / name) for name in ("executions.jsonl", "plans.jsonl", "intents.jsonl")
    )
    append_action_record(executions, _action("ACT-1"))
    assert try_append_action_record(executions, _action("ACT-2"), label="guard") is not None
    assert try_append_ledger_line(plans, {"plan_id": "P-1", "intent_branch": f"{INTENT}/_"}, label="guard")
    append_summary_record(executions, SummaryRecord("E-1", f"{INTENT}/P-1/_", "agent", "model", "success"))
    append_action_event({"action_id": "ACT-3", "action": "x"}, ledger_path=executions, db_path=str(root / "idx.db"))
    with atomic_ledger_append(intents) as f:
        f.write(json.dumps({"branch": f"{INTENT}/_", "intent_id": INTENT}) + "\n")
    exec_entry = {"execution_id": "E-1", "plan_branch": f"{INTENT}/P-1/_", "status": "failure"}
    record_sync_failure(MagicMock(), exec_entry, "E-1", str(ledger_dir), "network down")


def test_every_append_api_leaves_canonical_bytes_unchanged(tmp_path: Path) -> None:
    before = _seed_repo(tmp_path)

    _drive_every_append_api(tmp_path)

    ledger_dir = tmp_path / LEDGER_REL
    assert {name: (ledger_dir / name).read_bytes() for name in LEDGER_NAMES} == before
    fragments = sorted(p.relative_to(ledger_dir).as_posix() for p in (ledger_dir / "pending").rglob("*.jsonl"))
    assert fragments == [f"pending/{stem}/{INTENT}.jsonl" for stem in ("executions", "intents", "plans")]
    merged = {name: read_merged_rows(str(ledger_dir / name)) for name in LEDGER_NAMES}
    assert [len(rows) for rows in merged.values()] == [2, 2, 6]
    assert all(rows[0] == SEED_ROWS[name] for name, rows in merged.items())
    assert all(row["created_at"].endswith("Z") for rows in merged.values() for row in rows)


def _source_files() -> list[Path]:
    return [p for p in SRC_ROOT.rglob("*.py") if p.relative_to(SRC_ROOT).parts[0] != "ledger"]


def _is_append_open_of_a_ledger(node: ast.Call, source: str) -> bool:
    func = node.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    if name != "open":
        return False
    modes = [arg for arg in node.args[1:2] if isinstance(arg, ast.Constant)]
    modes += [kw.value for kw in node.keywords if kw.arg == "mode" and isinstance(kw.value, ast.Constant)]
    if not any(isinstance(mode.value, str) and "a" in mode.value for mode in modes):
        return False
    target = ast.get_source_segment(source, node.args[0] if node.args else func) or ""
    return any(marker in target.lower() for marker in ("ledger", "jsonl"))


def _canonical_writers(path: Path, root: Path = SRC_ROOT) -> list[str]:
    source = path.read_text(encoding="utf-8")
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and _is_append_open_of_a_ledger(node, source):
            found.append(f"{path.relative_to(root)}:{node.lineno} opens a ledger for append")
        ident = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None
        if ident == "LedgerLock":
            found.append(f"{path.relative_to(root)}:{node.lineno} uses LedgerLock")
    return found


def test_only_the_ledger_package_locks_or_appends_to_a_ledger() -> None:
    offenders = [
        finding
        for path in _source_files()
        if path.relative_to(SRC_ROOT).as_posix() not in CANONICAL_WRITER_ALLOWLIST
        for finding in _canonical_writers(path)
    ]

    assert offenders == []


def test_the_static_scan_flags_a_canonical_append_or_lock(tmp_path: Path) -> None:
    leak = tmp_path / "leak.py"
    leak.write_text(
        'def _leak(p, ledger_path):\n    open(p + "/executions.jsonl", "a")\n    open(ledger_path, mode="ab")\n'
        '    open(p, "a")\n    open(ledger_path)\n    return ledger.LedgerLock(p)\n',
        encoding="utf-8",
    )

    source = leak.read_text(encoding="utf-8")
    calls = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call)]

    assert sum(_is_append_open_of_a_ledger(call, source) for call in calls) == 2
    assert len(_canonical_writers(leak, tmp_path)) == 3
