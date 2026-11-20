"""Hermetic tests: promote_pending_candidates returns only new items and skips already-promoted candidates."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

from sandbox_executor.knowledge import wisdom
from sandbox_executor.knowledge.index import ActionIndexDatabase
from sandbox_executor.knowledge.wisdom import promote_pending_candidates

# 10 successes reach likely-success; 22 are needed for proven-success (Wilson lower bound >= 0.85).
LIKELY_SUCCESS_TRIALS = 10
PROVEN_SUCCESS_TRIALS = 22


def _append_ledger(ledger_path: Path, fingerprint: str, verdict: str, count: int) -> None:
    """Append ``count`` action rows with the given verdict for one context fingerprint."""
    offset = len(ledger_path.read_text(encoding="utf-8").splitlines())
    with open(ledger_path, "a", encoding="utf-8") as f:
        for i in range(count):
            row = {
                "action_id": f"act-{fingerprint}-{verdict}-{offset + i}",
                "context_fingerprint": fingerprint,
                "action": f"Action for {fingerprint}",
                "target": f"src/{fingerprint}.py",
                "verdict": verdict,
                "error_context": "RuntimeError: boom" if verdict == "failure" else "",
            }
            f.write(json.dumps(row) + "\n")


class _Workspace:
    """A tmp ledger, action index and KB/WB directories for one test."""

    def __init__(self, tmp_path: Path) -> None:
        self.ledger = tmp_path / "executions.jsonl"
        self.ledger.write_text("", encoding="utf-8")
        self.db = ActionIndexDatabase(db_path=str(tmp_path / "action_index.db"))
        self.kb_dir = str(tmp_path / "holon-knowledge" / "kb")
        self.wb_dir = str(tmp_path / "holon-knowledge" / "wisdom")

    def seed(self, fingerprint: str, verdict: str, count: int) -> None:
        """Append ledger rows and sync them into the index."""
        _append_ledger(self.ledger, fingerprint, verdict, count)
        self.db.sync_ledger(str(self.ledger))

    def promote(self, dry_run: bool = False) -> list[dict]:
        """Run one promotion pass over the workspace."""
        return promote_pending_candidates(
            index_db=self.db, kb_dir=self.kb_dir, wb_dir=self.wb_dir, min_trials=2, dry_run=dry_run
        )

    def snapshot(self) -> dict[str, tuple[bytes, int]]:
        """Map every file under the KB and WB directories to its bytes and nanosecond mtime."""
        files: dict[str, tuple[bytes, int]] = {}
        for base in (self.kb_dir, self.wb_dir):
            for root, _dirs, names in os.walk(base):
                for name in names:
                    path = Path(root) / name
                    files[str(path)] = (path.read_bytes(), path.stat().st_mtime_ns)
        return files


def _ids(items: list[dict]) -> list[str]:
    return sorted(str(item.get("kb_id") or item.get("wb_id")) for item in items)


def test_second_call_returns_only_the_new_candidates(tmp_path: Path) -> None:
    """With N candidates already promoted and M new ones, the next call returns exactly the M new items."""
    ws = _Workspace(tmp_path)
    old = [f"oldfail{i}_fp" for i in range(3)]
    new = [f"newfail{i}_fp" for i in range(2)]
    for fp in old:
        ws.seed(fp, "failure", 3)
    assert _ids(ws.promote()) == sorted(f"KB-failure-{fp[:8]}" for fp in old)

    for fp in new:
        ws.seed(fp, "failure", 3)
    promoted = ws.promote()

    assert _ids(promoted) == sorted(f"KB-failure-{fp[:8]}" for fp in new)
    assert all(os.path.exists(item["persisted_path"]) for item in promoted)


def test_promoting_twice_promotes_nothing_the_second_time(tmp_path: Path) -> None:
    """A second call against an unchanged index returns [] and leaves every KB/WB file untouched."""
    ws = _Workspace(tmp_path)
    ws.seed("succ0001_fp", "success", PROVEN_SUCCESS_TRIALS)
    ws.seed("fail0001_fp", "failure", 3)
    first = ws.promote()
    assert _ids(first) == ["KB-failure-fail0001", "KB-pattern-succ0001", "WB-heuristic-succ0001"]
    before = ws.snapshot()

    assert ws.promote() == []
    assert ws.snapshot() == before


def test_terminal_candidates_are_not_re_evaluated(tmp_path: Path) -> None:
    """Candidates whose terminal artefacts exist are skipped before evaluate_action_promotion runs."""
    ws = _Workspace(tmp_path)
    ws.seed("succ0002_fp", "success", PROVEN_SUCCESS_TRIALS)
    ws.seed("fail0002_fp", "failure", 3)
    ws.promote()

    real_evaluate = wisdom.evaluate_action_promotion
    with patch("sandbox_executor.knowledge.wisdom.evaluate_action_promotion", wraps=real_evaluate) as spy:
        assert ws.promote() == []
    assert spy.call_count == 0


def test_pattern_only_candidate_still_ascends_to_a_heuristic(tmp_path: Path) -> None:
    """A likely-success pattern is re-evaluated and later returns only its new WB heuristic."""
    ws = _Workspace(tmp_path)
    ws.seed("ascend01_fp", "success", LIKELY_SUCCESS_TRIALS)
    first = ws.promote()
    assert _ids(first) == ["KB-pattern-ascend01"]
    assert first[0]["evidence"]["ladder_state"] == "likely-success"

    ws.seed("ascend01_fp", "success", PROVEN_SUCCESS_TRIALS - LIKELY_SUCCESS_TRIALS)
    second = ws.promote()

    assert _ids(second) == ["WB-heuristic-ascend01"]
    assert second[0]["ascension_evidence"]["provenance"] == [{"kb_id": "KB-pattern-ascend01"}]
    assert ws.promote() == []


def test_failure_mode_with_a_later_success_is_still_evaluated(tmp_path: Path) -> None:
    """A recorded failure mode stays terminal only while the fingerprint has no success, so its veto can lift."""
    ws = _Workspace(tmp_path)
    ws.seed("contest1_fp", "failure", 3)
    assert _ids(ws.promote()) == ["KB-failure-contest1"]

    ws.seed("contest1_fp", "success", 1)
    real_evaluate = wisdom.evaluate_action_promotion
    with patch("sandbox_executor.knowledge.wisdom.evaluate_action_promotion", wraps=real_evaluate) as spy:
        assert ws.promote() == []
    assert spy.call_count == 1
    vetoes = {row["context_fingerprint"]: row["state"] for row in wisdom.list_vetoes(index_db=ws.db)}
    assert vetoes["contest1_fp"] == "lifted"


def test_dry_run_reports_what_the_real_run_writes(tmp_path: Path) -> None:
    """A dry run returns the ids the next real run writes, writes nothing, and returns [] once they exist."""
    ws = _Workspace(tmp_path)
    ws.seed("succ0003_fp", "success", PROVEN_SUCCESS_TRIALS)
    ws.seed("fail0003_fp", "failure", 3)

    dry = ws.promote(dry_run=True)
    assert ws.snapshot() == {}
    real = ws.promote()

    assert _ids(dry) == _ids(real)
    assert len(real) == 3
    assert ws.promote(dry_run=True) == []


def test_fresh_index_with_existing_failure_mode_still_records_the_veto(tmp_path: Path) -> None:
    """A rebuilt index re-creates the veto for a committed failure mode without returning it again."""
    ws = _Workspace(tmp_path)
    ws.seed("rebuild1_fp", "failure", 3)
    assert _ids(ws.promote()) == ["KB-failure-rebuild1"]

    ws.db = ActionIndexDatabase(db_path=str(tmp_path / "fresh_index.db"))
    ws.db.sync_ledger(str(ws.ledger))
    assert ws.promote() == []
    vetoes = {row["context_fingerprint"]: row["state"] for row in wisdom.list_vetoes(index_db=ws.db)}
    assert vetoes["rebuild1_fp"] == "proven-fail"


def test_repeated_veto_still_escalates_to_proven_fail(tmp_path: Path) -> None:
    """A repeated failure mode is re-evaluated until its veto reaches proven-fail."""
    ws = _Workspace(tmp_path)
    ws.seed("escal001_fp", "failure", 2)
    assert _ids(ws.promote()) == ["KB-failure-escal001"]
    states = {r["context_fingerprint"]: r["state"] for r in wisdom.list_vetoes(index_db=ws.db)}
    assert states["escal001_fp"] == "repeated"

    ws.seed("escal001_fp", "failure", 1)
    assert ws.promote() == []
    vetoes = {row["context_fingerprint"]: row for row in wisdom.list_vetoes(index_db=ws.db)}
    assert vetoes["escal001_fp"]["state"] == "proven-fail"
    assert vetoes["escal001_fp"]["reproduction_count"] == 3
