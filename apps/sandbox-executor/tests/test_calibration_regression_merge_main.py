"""End-to-end regression for PR #249's wrong calibration report, on a throwaway repository with the same topology.

``main`` advances with unrelated files and the execution branch merges it; Stage 4 records a failed reviewer
container dispatch and then the approval; the conflict gate commits the review ledger rows before Stage 5; and Stage 5
adds its own calibration commit to ``/calibrated``, which fast-forwards ``_``. The fixed calibrator must report no bug
drag, count exactly the PR's files, evaluate the approved head and pass its self-check. The rendered report is pinned
by ``fixtures/calibration_merge_main_expected.md``: commit SHAs are replaced by the symbolic names below (they depend
on the git object format) and the evaluation timestamp is frozen.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sandbox_executor.calibration import generate_calibration
from sandbox_executor.calibration_self_check import report_self_check_inputs, self_check_report

INTENT_ID = "I-1795409225-regress"
PLAN_ID = "P-1795409242-REG"
EXEC_ID = "E-1795409573-REG"
INTENT = f"{INTENT_ID}/_"
PLAN = f"{INTENT_ID}/{PLAN_ID}/_"
EXEC = f"{INTENT_ID}/{PLAN_ID}/{EXEC_ID}/_"
CALIBRATED = f"{INTENT_ID}/{PLAN_ID}/{EXEC_ID}/calibrated"
PR_FILES = {"src/feature.py", "tests/test_feature.py"}
MAIN_FILES = {"src/unrelated.py", "docs/unrelated.md"}
EXPECTED = Path(__file__).parent / "fixtures" / "calibration_merge_main_expected.md"
FROZEN_NOW = datetime(2026, 11, 23, 5, 45, 26, tzinfo=UTC)


class FrozenDatetime(datetime):
    """``datetime`` whose ``now`` is the frozen evaluation instant."""

    @classmethod
    def now(cls, tz=None):  # type: ignore[no-untyped-def,override]
        return FROZEN_NOW if tz is None else FROZEN_NOW.astimezone(tz)


class Repo:
    """A git repository whose commits carry a fixed identity and a clock that ticks one minute per commit."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.minute = 0

    def git(self, *args: str) -> str:
        stamp = f"2026-11-23T04:{self.minute:02d}:00+00:00"
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Holon Test",
            "GIT_AUTHOR_EMAIL": "test@holon.invalid",
            "GIT_COMMITTER_NAME": "Holon Test",
            "GIT_COMMITTER_EMAIL": "test@holon.invalid",
            "GIT_AUTHOR_DATE": stamp,
            "GIT_COMMITTER_DATE": stamp,
        }
        res = subprocess.run(["git", *args], cwd=self.path, env=env, capture_output=True, text=True, check=True)
        return res.stdout.strip()

    def commit(self, message: str, files: dict[str, str], append: bool = False) -> str:
        for rel, text in files.items():
            path = self.path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a" if append else "w", encoding="utf-8") as f:
                f.write(text)
        self.git("add", "--", *files)
        self.minute += 1
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")


def _jsonl(*rows: dict) -> str:
    return "".join(json.dumps(row) + "\n" for row in rows)


def _summary(execution_id: str, status: str, created_at: str, summary: str) -> dict:
    return {
        "entry_type": "summary",
        "execution_id": execution_id,
        "plan_branch": PLAN,
        "agent": "claude-agent",
        "agent_version": "2.1.302",
        "model": "claude-opus-5-5",
        "status": status,
        "summary": summary,
        "created_at": created_at,
    }


PLANS_ROW = {
    "plan_id": PLAN_ID,
    "intent_branch": INTENT,
    "agent": "claude-agent",
    "agent_version": "2.1.302",
    "model": "claude-opus-5-5",
    "p_success": 0.9,
    "entropy": 2.0,
    "impact": 40.0,
    "cost": 5.0,
    "learning_value": 2.0,
    "ev": 31.4,
    "plan_file": f"plans/{INTENT_ID}/{PLAN_ID}.md",
    "touch_set": [{"path": path} for path in sorted(PR_FILES)],
}
LEDGER = "holon-knowledge/ledger/executions.jsonl"


def build_pr_249_topology(path: Path) -> SimpleNamespace:
    """The PR #249 shape; returns the SHAs the assertions and the golden normalisation need."""
    origin = path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    repo = Repo(path / "host")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("remote", "add", "origin", str(origin))
    repo.commit("base", {"README.md": "base\n", LEDGER: ""})
    repo.git("push", "-q", "origin", "main")
    repo.git("checkout", "-qb", INTENT)
    intent_row = {"branch": INTENT_ID, "target_branch": "main"}
    repo.commit("intent", {"holon-knowledge/ledger/intents.jsonl": _jsonl(intent_row)})
    repo.git("checkout", "-qb", PLAN)
    plan_md = f"# Plan for {INTENT_ID}\n\n- **Plan ID:** {PLAN_ID}\n"
    repo.commit(
        "plan", {f"plans/{INTENT_ID}/{PLAN_ID}.md": plan_md, "holon-knowledge/ledger/plans.jsonl": _jsonl(PLANS_ROW)}
    )
    repo.git("checkout", "-qb", EXEC)
    repo.commit("execute: step 1", {"src/feature.py": "VALUE = 1\n"})
    stage3 = _summary(EXEC_ID, "success", "2026-11-23T04:52:53+00:00", "Plan executed successfully")
    repo.commit("execute: step 2", {"tests/test_feature.py": "def test_value():\n    pass\n", LEDGER: _jsonl(stage3)})
    repo.git("checkout", "-q", "main")
    main_head = repo.commit("feat: unrelated PR on main", dict.fromkeys(sorted(MAIN_FILES), "main\n"))
    repo.git("push", "-q", "origin", "main")
    repo.git("checkout", "-q", EXEC)
    repo.git("fetch", "-q", "origin")
    repo.minute += 1
    repo.git("merge", "-q", "--no-edit", "origin/main", "-m", "merge origin/main into regress")
    review_failed = _summary(
        "REV-1795410989-b2a486df-claude-agent",
        "failed",
        "2026-11-23T05:16:29.233Z",
        "Review Loop halted without consensus after 2 iteration(s): Reviewer ensemble container dispatch exited "
        "with code 1 without writing results",
    )
    repo.commit("chore(review): record review ledger rows (iteration 1)", {LEDGER: _jsonl(review_failed)}, append=True)
    last_code = repo.commit("fix(review): resolve findings R1-F1 (iteration 2)", {"src/feature.py": "VALUE = 2\n"})
    approval = _summary(
        "REV-1795412634-3cf610db-claude-agent", "approved", "2026-11-23T05:43:54.358Z", "Review Loop reached consensus"
    )
    approved_head = repo.commit(
        "chore(review): record review ledger rows (before Stage 5)", {LEDGER: _jsonl(approval)}, append=True
    )
    repo.git("checkout", "-qb", CALIBRATED)
    cal_row = {"entry_type": "action", "execution_id": f"CAL-{PLAN_ID}-1", "plan_branch": PLAN, "outcome": "success"}
    repo.commit(
        f"chore(calibration): add calibration report for {PLAN_ID}",
        {f"plans/{INTENT_ID}/{PLAN_ID}_calibration.md": "# old report\n", LEDGER: _jsonl(cal_row)},
        append=True,
    )
    repo.git("checkout", "-q", EXEC)
    repo.git("merge", "-q", "--ff-only", CALIBRATED)
    names = {
        "main_head": main_head,
        "merge_base": repo.git("merge-base", "origin/main", EXEC),
        "last_code": last_code,
        "approved_head": approved_head,
    }
    return SimpleNamespace(path=repo.path, **names)


@pytest.fixture
def topology(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setattr("sandbox_executor.calibration.generate.datetime", FrozenDatetime)
    monkeypatch.delenv("HOLON_PR_HEAD_SHA", raising=False)
    return build_pr_249_topology(tmp_path)


def normalise(markdown: str, topology: SimpleNamespace) -> str:
    """Replace each commit SHA of the topology with its symbolic name."""
    for name in ("main_head", "merge_base", "last_code", "approved_head"):
        markdown = markdown.replace(getattr(topology, name), f"<{name}>")
    return markdown + "\n"


def test_pr_249_topology_reports_only_the_pr_delivery(topology: SimpleNamespace) -> None:
    report = generate_calibration(EXEC, target_worktree=str(topology.path))

    assert report.actual.bug_ev_drag == 0.0
    assert report.actual.ev_with_bugs == report.actual.ev_clean
    assert set(report.changed_paths or []) == PR_FILES
    assert report.actual.files_changed == len(PR_FILES)
    assert report.touch_set is not None
    assert not MAIN_FILES & set(report.touch_set["unpredicted_paths"])
    assert report.diff_base == "target_merge_base"
    assert report.merge_base_sha == topology.main_head
    assert report.evaluated_commit_sha == topology.approved_head
    for approved in (topology.approved_head, topology.last_code):
        assert self_check_report(report_self_check_inputs(report, str(topology.path), approved)) == []

    assert normalise(report.markdown_content, topology) == EXPECTED.read_text(encoding="utf-8")
