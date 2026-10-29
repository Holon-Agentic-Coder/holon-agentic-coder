"""Pure-append guarantee for the three ledgers: no write reorders, deduplicates or re-serialises existing rows."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor import flow
from sandbox_executor.ledger import LedgerRewriteError, assert_ledger_prefix, atomic_ledger_append

LEDGER_NAMES = ("intents", "plans", "executions")

#: Rows that the old reconciliation reordered: out-of-order timestamps, a repeated branch key and a description that
#: mentions a diff3 marker inside a JSON string, which the old substring scan read as a conflict.
INTENT_ROWS: list[dict[str, Any]] = [
    {"slug": "late", "branch": "I-late", "created_at": "2026-10-28T12:00:00Z"},
    {"slug": "early", "branch": "I-early", "created_at": "2026-10-01T09:00:00Z"},
    {"slug": "late", "branch": "I-late", "created_at": "2026-10-29T08:00:00Z", "status": "again"},
    {"slug": "guard", "branch": "I-guard", "description": "mentions ||||||| inside text", "created_at": "2026-10-02"},
]


def _seed_ledgers(repo: Path) -> dict[str, bytes]:
    """Write the three ledgers under ``repo`` with compact separators and return their bytes."""
    ledger_dir = repo / "holon-knowledge" / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    rows = {
        "intents": INTENT_ROWS,
        "plans": [{"plan_id": "P-2", "created_at": "2026-10-05"}, {"plan_id": "P-1", "created_at": "2026-10-01"}],
        "executions": [{"execution_id": "E-2", "created_at": "2026-10-05"}, {"execution_id": "E-1"}],
    }
    for name, ledger_rows in rows.items():
        text = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in ledger_rows)
        (ledger_dir / f"{name}.jsonl").write_text(text, encoding="utf-8")
    return _snapshot(repo)


def _snapshot(repo: Path) -> dict[str, bytes]:
    """Return the current bytes of each ledger under ``repo``."""
    return {n: (repo / "holon-knowledge" / "ledger" / f"{n}.jsonl").read_bytes() for n in LEDGER_NAMES}


class FakeGit:
    """Records every ``run_git`` call and answers with a clean porcelain status."""

    def __init__(self, porcelain: str = "") -> None:
        self.calls: list[list[str]] = []
        self.porcelain = porcelain

    def __call__(self, args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        stdout = self.porcelain if args[:1] == ["status"] else ""
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    def ledger_adds(self) -> list[list[str]]:
        """Return each ``git add`` call that names a ledger path."""
        return [c for c in self.calls if c[:1] == ["add"] and any("holon-knowledge/ledger" in a for a in c)]


@pytest.fixture
def fake_git(monkeypatch: pytest.MonkeyPatch) -> FakeGit:
    """Replace git and the mergeability probe at their ``flow`` bindings."""
    fake = FakeGit()
    monkeypatch.setattr(flow, "run_git", fake)
    monkeypatch.setattr(flow, "check_pr_mergeability", lambda *_a, **_k: {"mergeable": "MERGEABLE"})
    return fake


def test_stage_precheck_leaves_ledgers_byte_identical(tmp_path: Path, fake_git: FakeGit) -> None:
    """Reproduction: the Stage 1 pre-check must not rewrite or stage a ledger that only mentions a marker."""
    before = _seed_ledgers(tmp_path)
    result = flow.check_and_resolve_stage_conflicts(str(tmp_path), stage_name="intent", branch=None)
    assert _snapshot(tmp_path) == before
    assert fake_git.ledger_adds() == []
    assert result["conflicting_files"] == []


class TestPrefixGuard:
    """``assert_ledger_prefix`` and the guarded ``atomic_ledger_append`` fail closed on any non-append write."""

    def test_pure_append_passes(self, tmp_path: Path) -> None:
        ledger = tmp_path / "plans.jsonl"
        ledger.write_bytes(b'{"plan_id":"P-1"}\n')
        before = ledger.read_bytes()
        with atomic_ledger_append(str(ledger)) as f:
            f.write('{"plan_id":"P-2"}\n')
        assert ledger.read_bytes() == before + b'{"plan_id":"P-2"}\n'
        assert_ledger_prefix(str(ledger), before)

    def test_missing_file_counts_as_empty(self, tmp_path: Path) -> None:
        ledger = tmp_path / "ledger" / "executions.jsonl"
        with atomic_ledger_append(str(ledger)) as f:
            f.write('{"execution_id":"E-1"}\n')
        assert_ledger_prefix(str(ledger), b"")

    @pytest.mark.parametrize("rewritten", [b'{"plan_id":"P-1"', b'{"plan_id":"P-2"}\n{"plan_id":"P-1"}\n'])
    def test_truncated_or_reordered_file_raises(self, tmp_path: Path, rewritten: bytes) -> None:
        ledger = tmp_path / "plans.jsonl"
        ledger.write_bytes(b'{"plan_id":"P-1"}\n{"plan_id":"P-2"}\n')
        before = ledger.read_bytes()
        ledger.write_bytes(rewritten)
        with pytest.raises(LedgerRewriteError, match="first divergence at byte"):
            assert_ledger_prefix(str(ledger), before)

    def test_rewrite_inside_append_context_raises(self, tmp_path: Path) -> None:
        ledger = tmp_path / "intents.jsonl"
        ledger.write_bytes(b'{"slug":"b"}\n{"slug":"a"}\n')
        with pytest.raises(LedgerRewriteError), atomic_ledger_append(str(ledger)) as f:
            ledger.write_bytes(b'{"slug":"a"}\n')
            f.write('{"slug":"c"}\n')
