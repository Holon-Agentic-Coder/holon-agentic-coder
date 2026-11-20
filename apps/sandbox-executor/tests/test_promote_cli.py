"""Hermetic tests: `holon promote --pending` reports only the knowledge items it newly promoted."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from sandbox_executor.cli.commands import promote
from sandbox_executor.knowledge.index import ActionIndexDatabase


def _seed_worktree(worktree: Path) -> None:
    """Seed the worktree's action index with one qualifying failure mode."""
    index_dir = worktree / "holon-knowledge" / ".index"
    index_dir.mkdir(parents=True)
    seed_ledger = index_dir / "seed.jsonl"
    rows = [{"action_id": f"act-cli-{i}", "context_fingerprint": "clifail1_fp", "verdict": "failure"} for i in range(3)]
    seed_ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    ActionIndexDatabase(db_path=str(index_dir / "action_index.db")).sync_ledger(str(seed_ledger))


def _run(worktree: Path, dry_run: bool = False) -> int:
    args = argparse.Namespace(pending=True, dry_run=dry_run, min_trials=2, target_worktree=str(worktree))
    return promote.run(args)


def test_promote_cli_reports_newly_promoted_items_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The first run reports the new item; the second reports zero."""
    _seed_worktree(tmp_path)

    assert _run(tmp_path, dry_run=True) == 0
    assert "Dry-run: newly promotable knowledge items: 1" in capsys.readouterr().out

    assert _run(tmp_path) == 0
    first = capsys.readouterr().out
    assert "Newly promoted knowledge items: 1" in first
    assert "KB-failure-clifail1" in first

    assert _run(tmp_path) == 0
    assert "Newly promoted knowledge items: 0" in capsys.readouterr().out
