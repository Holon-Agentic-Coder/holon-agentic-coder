"""Pure-append guarantee for the three ledgers: no write reorders, deduplicates or re-serialises existing rows."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor import flow

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
