"""Two intent branches append through the real funnel and merge into ``main`` in either order without a conflict."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.ledger import ActionRecord, append_action_record, atomic_ledger_append, read_merged_rows_at_ref
from sandbox_executor.ledger.fragments import LEDGER_NAMES, ledger_commit_paths

from tests.flow.helpers import _git

LEDGER_REL = "holon-knowledge/ledger"
SEED = {
    "intents.jsonl": {"branch": "I-0-seed/_", "intent_id": "I-0-seed", "created_at": "2026-01-01T00:00:00Z"},
    "plans.jsonl": {"plan_id": "P-0", "intent_branch": "I-0-seed/_", "created_at": "2026-01-01T00:00:00Z"},
    "executions.jsonl": {"execution_id": "E-0", "plan_branch": "I-0-seed/P-0/_", "created_at": "2026-01-01T00:00:00Z"},
}
INTENTS = ("I-1-alpha", "I-2-beta")


def _append(work: Path, name: str, row: dict) -> None:
    with atomic_ledger_append(str(work / LEDGER_REL / name)) as f:
        f.write(json.dumps(row) + "\n")


def _write_intent_rows(work: Path, intent: str) -> None:
    _append(work, "intents.jsonl", {"branch": f"{intent}/_", "intent_id": intent})
    _append(work, "plans.jsonl", {"plan_id": "P-1", "intent_branch": f"{intent}/_"})
    _append(
        work, "executions.jsonl", {"execution_id": "E-1", "plan_branch": f"{intent}/P-1/_", "entry_type": "summary"}
    )
    record = ActionRecord(
        action_id=f"ACT-{intent}", execution_id="E-1", action="ran", category="code", target="x", outcome="success"
    )
    append_action_record(str(work / LEDGER_REL / "executions.jsonl"), record)


def _repo_with_two_intent_branches(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    (work / LEDGER_REL).mkdir(parents=True)
    for name, row in SEED.items():
        (work / LEDGER_REL / name).write_text(json.dumps(row) + "\n", encoding="utf-8")
    _git(work, "add", LEDGER_REL)
    _git(work, "commit", "-qm", "seed ledgers")
    for intent in INTENTS:
        _git(work, "checkout", "-q", "-b", f"{intent}/_", "main")
        _write_intent_rows(work, intent)
        _git(work, "add", "--", *ledger_commit_paths(str(work)))
        _git(work, "commit", "-qm", f"rows of {intent}")
    _git(work, "checkout", "-q", "main")
    return work


def _merge_in_order(work: Path, target: str, order: tuple[str, ...]) -> None:
    _git(work, "checkout", "-q", "-b", target, "main")
    for intent in order:
        merge = subprocess.run(
            ["git", "merge", "--no-ff", "-q", "-m", f"merge {intent}", f"{intent}/_"],
            cwd=work,
            capture_output=True,
            text=True,
            check=False,
        )
        assert merge.returncode == 0, merge.stdout + merge.stderr
    assert _git(work, "diff", "--name-only", "--diff-filter=U") == ""
    assert _git(work, "status", "--porcelain", "--", "holon-knowledge/") == ""


def _row_set(work: Path, ref: str, name: str) -> set[str]:
    return {json.dumps(row, sort_keys=True) for row in read_merged_rows_at_ref(str(work), ref, name)}


@pytest.fixture
def merged_both_ways(tmp_path: Path) -> Path:
    work = _repo_with_two_intent_branches(tmp_path)
    _merge_in_order(work, "merged-ab", INTENTS)
    _merge_in_order(work, "merged-ba", tuple(reversed(INTENTS)))
    return work


def test_both_merge_orders_yield_the_same_logical_ledgers(merged_both_ways: Path) -> None:
    work = merged_both_ways

    for name in LEDGER_NAMES:
        rows = _row_set(work, "merged-ab", name)
        assert rows == _row_set(work, "merged-ba", name)
        assert rows == _row_set(work, "main", name) | _row_set(work, "I-1-alpha/_", name) | _row_set(
            work, "I-2-beta/_", name
        )
    assert len(_row_set(work, "merged-ab", "executions.jsonl")) == 5


def test_merges_leave_canonical_files_untouched_and_keep_one_fragment_per_intent(merged_both_ways: Path) -> None:
    work = merged_both_ways
    canonical = [f"{LEDGER_REL}/{name}" for name in LEDGER_NAMES]

    for ref in ("merged-ab", "merged-ba"):
        assert _git(work, "diff", "--name-only", "main", ref, "--", *canonical) == ""
        listing = _git(work, "ls-tree", "-r", "--name-only", ref, "--", f"{LEDGER_REL}/pending").splitlines()
        assert listing == [
            f"{LEDGER_REL}/pending/{stem}/{intent}.jsonl"
            for stem in ("executions", "intents", "plans")
            for intent in INTENTS
        ]
