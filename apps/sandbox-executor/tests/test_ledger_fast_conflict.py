"""Ledger-only conflicts merge fast: the target rows keep their places, head-only rows are inserted by created_at.

Real git against a local bare ``origin`` (a filesystem remote, allowed by the network guard); no test command runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sandbox_executor.flow import reconcile_jsonl_ledger_conflict
from sandbox_executor.flow.review import conflict_gate, conflict_merge
from sandbox_executor.ledger.order import _order_appended_rows

from tests.flow.helpers import _git
from tests.ledger_conflict_repos import BRANCH, LEDGER, _commit, _row, build_conflicting_repos, refuse_test_runs


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path]:
    return build_conflicting_repos(tmp_path)


@pytest.fixture
def no_test_run(monkeypatch: pytest.MonkeyPatch) -> None:
    refuse_test_runs(monkeypatch)


def test_order_appended_rows_sorts_timestamped_rows_and_keeps_the_rest_in_place():
    rows = [_row("B", "2026-11-05T00:00:00Z"), "not json\n", _row("A", "2026-11-03"), _row("C", None)]
    rows.append(_row("A2", "2026-11-03T00:00:00+00:00"))

    ordered = _order_appended_rows(rows)

    assert ordered == [rows[2], "not json\n", rows[4], rows[3], rows[0]]


def _is_ordered_subsequence(kept: list[str], merged: list[str]) -> bool:
    remaining = iter(merged)
    return all(line in remaining for line in kept)


def test_conflict_resolution_keeps_the_prefix_rows_in_place_and_only_inserts():
    prefix = _row("P-late", "2026-11-09T00:00:00Z") + _row("P-early", "2026-11-01T00:00:00Z")
    content = (
        "<<<<<<< HEAD\n"
        + prefix
        + "=======\n"
        + _row("H-late", "2026-11-08T00:00:00Z")
        + _row("H-early", "2026-11-02T00:00:00Z")
        + ">>>>>>> main\n"
    )

    resolved = reconcile_jsonl_ledger_conflict(content)

    assert _is_ordered_subsequence(prefix.splitlines(), resolved.splitlines())
    # The unsorted prefix never moves; both head rows predate its running maximum, so they go before it.
    assert [json.loads(line)["execution_id"] for line in resolved.splitlines()] == [
        "H-early",
        "H-late",
        "P-late",
        "P-early",
    ]


def test_ledger_only_conflict_keeps_the_target_rows_and_inserts_head_rows_by_created_at(repos, no_test_run):
    origin, work = repos
    target_bytes = _git(work, "show", f"refs/remotes/origin/main:{LEDGER}") + "\n"

    outcome = conflict_merge._merge_and_push(str(work), BRANCH, "main")

    assert (outcome.merged, outcome.ledger_only, outcome.conflicting_paths) == (True, True, (LEDGER,))
    merged = _git(origin, "show", f"refs/heads/{BRANCH}:{LEDGER}") + "\n"
    assert _is_ordered_subsequence(target_bytes.splitlines(), merged.splitlines())
    assert len(merged.splitlines()) == len(target_bytes.splitlines()) + 2
    ids = [json.loads(line)["execution_id"] for line in merged.splitlines()]
    assert ids == ["E-base", "E-early", "E-main", "E-late"]
    assert _git(origin, "show", f"refs/heads/{BRANCH}:src/feature.py") == "x = 1"
    assert len(_git(origin, "rev-list", "--parents", "-n", "1", f"refs/heads/{BRANCH}").split()) == 3
    assert _git(work, "worktree", "list").count("\n") == 0


def test_code_conflict_aborts_the_merge_and_is_reported(repos, no_test_run):
    origin, work = repos
    _git(work, "checkout", "-q", BRANCH)
    _commit(work, "src/shared.py", "head = 1\n", "head code")
    _git(work, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    _git(work, "checkout", "-q", "main")
    _commit(work, "src/shared.py", "main = 1\n", "main code")
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    before = _git(origin, "rev-parse", f"refs/heads/{BRANCH}")

    outcome = conflict_merge._merge_and_push(str(work), BRANCH, "main")

    assert outcome.merged is False
    assert "src/shared.py" in outcome.conflicting_paths
    assert _git(origin, "rev-parse", f"refs/heads/{BRANCH}") == before
    assert "code conflicts with main in: " in conflict_gate._blocked_reason(outcome, "main")
