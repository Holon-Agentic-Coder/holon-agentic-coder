"""A bare ``origin`` and clone whose target and head branches both appended ledger rows: a ledger-only conflict.

A plain helper module (not a test module), so the conflict-gate tests import it without importing another test module.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.flow.helpers import _git

LEDGER = "holon-knowledge/ledger/executions.jsonl"
BRANCH = "I-1-x/P-2-y/E-3-z/_"


def _row(execution_id: str, created_at: str | None) -> str:
    row = {"execution_id": execution_id} | ({"created_at": created_at} if created_at else {})
    return json.dumps(row, separators=(",", ":")) + "\n"


def _commit(work: Path, rel: str, text: str, message: str) -> None:
    path = work / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _git(work, "add", rel)
    _git(work, "commit", "-q", "-m", message)


def build_conflicting_repos(tmp_path: Path) -> tuple[Path, Path]:
    """A bare ``origin`` and a clone whose ``main`` and ``BRANCH`` both appended rows after a shared base row."""
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    base = _row("E-base", "2026-11-01T00:00:00Z")
    _commit(work, LEDGER, base, "base")
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    _git(work, "checkout", "-q", "-b", BRANCH)
    head_rows = [_row("E-late", "2026-11-05T00:00:00Z"), _row("E-early", "2026-11-03T00:00:00+00:00")]
    _commit(work, LEDGER, base + "".join(head_rows), "head rows")
    _commit(work, "src/feature.py", "x = 1\n", "feature")
    _git(work, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    _git(work, "checkout", "-q", "main")
    _commit(work, LEDGER, base + _row("E-main", "2026-11-04T00:00:00Z"), "main row")
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    _git(work, "fetch", "-q", "origin")
    return origin, work


def refuse_test_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the review's local test runner fail the test if it is invoked."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("a ledger-only merge must not run the test suite")

    monkeypatch.setattr("sandbox_executor.flow.review.local_tests.run_measurement", refuse)
    monkeypatch.setattr("sandbox_executor.entrypoint.test_measurement.run_test_task", refuse)
