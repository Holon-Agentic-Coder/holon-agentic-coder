"""Hermetic tests: promote_pending_candidates returns only new items and skips already-promoted candidates."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from unittest.mock import patch

from sandbox_executor.knowledge import wisdom
from sandbox_executor.knowledge.index import ActionIndexDatabase
from sandbox_executor.knowledge.wisdom import promote_pending_candidates

# 10 successes reach likely-success; 22 are needed for proven-success (Wilson lower bound >= 0.85).
LIKELY_SUCCESS_TRIALS = 10
PROVEN_SUCCESS_TRIALS = 22


def _append_ledger(
    ledger_path: Path, fingerprint: str, verdict: str, count: int, action_origin: str | None = None
) -> None:
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
            if action_origin:
                row["action_origin"] = action_origin
            f.write(json.dumps(row) + "\n")


class _Workspace:
    """A tmp ledger, action index and KB/WB directories for one test."""

    def __init__(self, tmp_path: Path) -> None:
        self.ledger = tmp_path / "executions.jsonl"
        self.ledger.write_text("", encoding="utf-8")
        self.db = ActionIndexDatabase(db_path=str(tmp_path / "action_index.db"))
        self.kb_dir = str(tmp_path / "holon-knowledge" / "kb")
        self.wb_dir = str(tmp_path / "holon-knowledge" / "wisdom")

    def seed(self, fingerprint: str, verdict: str, count: int, action_origin: str | None = None) -> None:
        """Append ledger rows and sync them into the index."""
        _append_ledger(self.ledger, fingerprint, verdict, count, action_origin)
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


def test_proven_fail_veto_records_later_reproductions(tmp_path: Path) -> None:
    """A proven-fail veto is re-evaluated when new failures arrive so its reproduction count stays current."""
    ws = _Workspace(tmp_path)
    ws.seed("reprod01_fp", "failure", 3)
    assert _ids(ws.promote()) == ["KB-failure-reprod01"]

    ws.seed("reprod01_fp", "failure", 2)
    assert ws.promote() == []
    vetoes = {row["context_fingerprint"]: row for row in wisdom.list_vetoes(index_db=ws.db)}
    assert vetoes["reprod01_fp"]["state"] == "proven-fail"
    assert vetoes["reprod01_fp"]["reproduction_count"] == 5


def test_sibling_signatures_of_one_fingerprint_settle(tmp_path: Path) -> None:
    """Signatures sharing a fingerprint's veto row stop re-evaluating once it holds the highest count."""
    ws = _Workspace(tmp_path)
    ws.seed("sibling1_fp", "failure", 3)
    ws.seed("sibling1_fp", "failure", 4, action_origin="review_fix")
    assert _ids(ws.promote()) == ["KB-failure-sibling1"]
    ws.promote()

    real_evaluate = wisdom.evaluate_action_promotion
    with patch("sandbox_executor.knowledge.wisdom.evaluate_action_promotion", wraps=real_evaluate) as spy:
        assert ws.promote() == []
    assert spy.call_count == 0
    vetoes = {row["context_fingerprint"]: row for row in wisdom.list_vetoes(index_db=ws.db)}
    assert vetoes["sibling1_fp"]["reproduction_count"] == 4


def test_dry_run_counts_a_sibling_fingerprint_once(tmp_path: Path) -> None:
    """Sibling signatures of one fingerprint yield one dry-run item, matching what the real run writes."""
    ws = _Workspace(tmp_path)
    ws.seed("sibling2_fp", "failure", 3)
    ws.seed("sibling2_fp", "failure", 4, action_origin="review_fix")

    dry = ws.promote(dry_run=True)
    assert ws.snapshot() == {}
    real = ws.promote()

    assert _ids(dry) == _ids(real) == ["KB-failure-sibling2"]


_RUN_VARYING_KEYS = ("wilson_lower", "total_executions", "success_count", "failure_count", "ledger_refs")


def _assert_stable_file(path: Path) -> None:
    """A promoted file carries no counters, ledger refs or counter-bearing advisory text."""
    text = path.read_text(encoding="utf-8")
    assert not any(f'"{key}"' in text for key in _RUN_VARYING_KEYS)
    payload = json.loads(text)["payload"]
    advisory = str(payload.get("description") or payload.get("advisory"))
    assert not re.search(r"\d+\s*/\s*\d+", advisory)
    assert not re.search(r"\d+ executions", advisory)


def _two_workspaces(tmp_path: Path) -> tuple[_Workspace, _Workspace]:
    """Two independent flow workspaces under one tmp directory."""
    for name in ("flow_a", "flow_b"):
        (tmp_path / name).mkdir()
    return _Workspace(tmp_path / "flow_a"), _Workspace(tmp_path / "flow_b")


def test_same_context_promotes_identical_files_across_workspaces(tmp_path: Path) -> None:
    """Two flows promoting one fingerprint with different execution counts write byte-identical KB and WB files."""
    ws_a, ws_b = _two_workspaces(tmp_path)
    ws_a.seed("ascend02_fp", "success", PROVEN_SUCCESS_TRIALS)
    ws_b.seed("ascend02_fp", "success", PROVEN_SUCCESS_TRIALS + 9)

    items_a, items_b = ws_a.promote(), ws_b.promote()

    assert _ids(items_a) == _ids(items_b) == ["KB-pattern-ascend02", "WB-heuristic-ascend02"]
    for file_a, file_b in (
        (Path(ws.kb_dir, "patterns", "KB-pattern-ascend02.json") for ws in (ws_a, ws_b)),
        (Path(ws.wb_dir, "heuristics", "WB-heuristic-ascend02.json") for ws in (ws_a, ws_b)),
    ):
        assert file_a.read_bytes() == file_b.read_bytes()
        _assert_stable_file(file_a)
    counts = [next(i for i in items if "kb_id" in i)["evidence"]["success_count"] for items in (items_a, items_b)]
    assert counts == [PROVEN_SUCCESS_TRIALS, PROVEN_SUCCESS_TRIALS + 9]


def test_same_failure_context_promotes_identical_files_across_workspaces(tmp_path: Path) -> None:
    """Two flows promoting one failing fingerprint with different failure counts write byte-identical KB files."""
    ws_a, ws_b = _two_workspaces(tmp_path)
    ws_a.seed("failsame_fp", "failure", 3)
    ws_b.seed("failsame_fp", "failure", 7)

    items_a, items_b = ws_a.promote(), ws_b.promote()

    assert _ids(items_a) == _ids(items_b) == ["KB-failure-failsame"]
    file_a = Path(ws_a.kb_dir, "failure_modes", "KB-failure-failsame.json")
    assert file_a.read_bytes() == Path(ws_b.kb_dir, "failure_modes", "KB-failure-failsame.json").read_bytes()
    _assert_stable_file(file_a)
    assert [items_a[0]["evidence"]["failure_count"], items_b[0]["evidence"]["failure_count"]] == [3, 7]


def _append_dated_failures(ws: _Workspace, fingerprint: str, rows: list[tuple[str, str, str]]) -> None:
    """Append failure rows given as (created_at, action, error_context) and sync them into the index."""
    offset = len(ws.ledger.read_text(encoding="utf-8").splitlines())
    with open(ws.ledger, "a", encoding="utf-8") as f:
        for i, (created_at, action, error_context) in enumerate(rows):
            row = {
                "action_id": f"act-{fingerprint}-failure-{offset + i}",
                "context_fingerprint": fingerprint,
                "action": action,
                "target": f"src/{fingerprint}.py",
                "verdict": "failure",
                "error_context": error_context,
                "created_at": created_at,
            }
            f.write(json.dumps(row) + "\n")
    ws.db.sync_ledger(str(ws.ledger))


def test_newer_divergent_failures_do_not_change_failure_file_bytes(tmp_path: Path) -> None:
    """Flows sharing early failures but holding different newest rows write byte-identical KB-failure files."""
    ws_a, ws_b = _two_workspaces(tmp_path)
    shared = [(f"2026-11-0{i}T00:00:00+00:00", "Run the build", "RuntimeError: boom") for i in range(1, 4)]
    _append_dated_failures(ws_a, "diverge1_fp", [*shared, ("2026-11-20T00:00:00+00:00", "Retry A", "KeyError: a")])
    _append_dated_failures(ws_b, "diverge1_fp", [*shared, ("2026-11-21T00:00:00+00:00", "Retry B", "OSError: b")])

    assert _ids(ws_a.promote()) == _ids(ws_b.promote()) == ["KB-failure-diverge1"]
    file_a = Path(ws_a.kb_dir, "failure_modes", "KB-failure-diverge1.json")
    assert file_a.read_bytes() == Path(ws_b.kb_dir, "failure_modes", "KB-failure-diverge1.json").read_bytes()
    payload = json.loads(file_a.read_text(encoding="utf-8"))["payload"]
    assert (payload["action"], payload["symptoms"]) == ("Run the build", ["RuntimeError: boom"])
