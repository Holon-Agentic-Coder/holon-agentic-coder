"""Stage 3 touch-set admission (flow/admission.py): overlap decisions, refusal text and the recorded override."""

from __future__ import annotations

import functools
import json
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.calibration_ev import parse_summary_records
from sandbox_executor.flow import FlowContext, StageStatus, admission
from sandbox_executor.flow.admission import AdmissionDecision, check_admission, format_refusal
from sandbox_executor.flow.git_refs import _latest_execution_summary
from sandbox_executor.ledger import query_ledger

from tests.flow.helpers import _git
from tests.flow.test_in_flight import OTHER_HEAD, OTHER_PLAN, FakeGh, make_plan_branch

OWN_PLAN = "I-100-own/P-101-cccccccc-claude-agent-claude-opus-5-5/_"
REAL_CHECK = check_admission


def no_worktrees(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["git", *args], 0, f"worktree {cwd}\n", "")


def own_context(host: Path, **overrides) -> FlowContext:
    return FlowContext(
        target_worktree=str(host), plan_branch=OWN_PLAN, intent_branch="I-100-own/_", token_reduce=False, **overrides
    )


def other_pr(diff: list[str]) -> FakeGh:
    return FakeGh([{"number": 7, "headRefName": OTHER_HEAD, "baseRefName": "main"}], {7: diff})


@pytest.fixture
def own_plan(flow_repos):
    """The run's own plan branch (declaring src/app.py and docs/), pushed so an override row can be appended."""
    host, _origin = flow_repos
    make_plan_branch(host, OWN_PLAN, [{"path": "src/app.py"}, {"path": "docs/"}])
    _git(host, "push", "-q", "origin", OWN_PLAN)
    return host


def test_overlap_with_a_declared_set_is_refused_and_named(own_plan):
    make_plan_branch(own_plan, OTHER_PLAN, [{"path": "src/"}, {"path": "lib/"}])
    decision = check_admission(own_context(own_plan), other_pr(["lib/x.py"]), no_worktrees)
    assert not decision.admitted and not decision.overridden
    assert [(c.label, c.files) for c in decision.conflicts] == [
        (f"pull request #7 (branch {OTHER_HEAD})", ("src/app.py (its entry src/)",))
    ]
    message = format_refusal(decision)
    assert message.startswith("Stage 3 refused: the touch set overlaps pull request #7")
    assert OTHER_HEAD in message and "src/app.py" in message and "--allow-overlap" in message


def test_overlap_with_a_real_pull_request_diff_is_refused(own_plan):
    decision = check_admission(own_context(own_plan), other_pr(["docs/guide.md", "src/other.py"]), no_worktrees)
    assert not decision.admitted
    assert decision.conflicts[0].files == ("docs/guide.md",)


def test_metadata_only_overlap_is_admitted(own_plan):
    diff = ["plans/P-201.md", "executions/E-202.md", "holon-knowledge/ledger/plans.jsonl"]
    decision = check_admission(own_context(own_plan), other_pr(diff), no_worktrees)
    assert decision == AdmissionDecision(admitted=True)


def test_a_plan_without_a_touch_set_is_refused_unless_overridden(flow_repos):
    host, _origin = flow_repos
    make_plan_branch(host, OWN_PLAN, None)
    gh = other_pr([])
    refused = check_admission(own_context(host), gh, no_worktrees)
    assert not refused.admitted and "records no touch set" in str(refused.reason)
    assert "re-plan" in format_refusal(refused)
    assert gh.calls == []  # nothing to compare, so discovery never runs
    overridden = check_admission(own_context(host, allow_overlap=True), gh, no_worktrees)
    assert overridden.admitted and overridden.overridden


def test_unavailable_discovery_fails_closed(own_plan):
    decision = check_admission(own_context(own_plan), FakeGh([], {}, fail=True), no_worktrees)
    assert not decision.admitted
    assert "could not be discovered" in str(decision.reason) and "HTTP 502" in str(decision.reason)


def _use_real_check(monkeypatch: pytest.MonkeyPatch, gh: FakeGh) -> None:
    monkeypatch.setattr(admission, "check_admission", functools.partial(REAL_CHECK, run_gh=gh, run_git=no_worktrees))


def test_refusal_result_names_conflicts(own_plan, monkeypatch):
    _use_real_check(monkeypatch, other_pr(["src/app.py"]))
    context = own_context(own_plan)
    result = admission.admission_stage_failure(context, "2026-11-14T00:00:00Z")
    assert result is not None and result.status == StageStatus.FAILED
    assert result.payload["admission_refused"] is True and result.payload["dispatched"] is False
    assert result.payload["conflicts"] == [{"flow": f"pull request #7 (branch {OTHER_HEAD})", "files": ["src/app.py"]}]


def test_override_appends_exactly_one_row(own_plan, monkeypatch):
    _use_real_check(monkeypatch, other_pr(["src/app.py"]))
    context = own_context(own_plan, allow_overlap=True)
    assert admission.admission_stage_failure(context, "2026-11-14T00:00:00Z") is None
    content = _git(own_plan, "show", f"refs/remotes/origin/{OWN_PLAN}:holon-knowledge/ledger/executions.jsonl")
    rows = [json.loads(line) for line in content.splitlines() if line.strip()]
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == row["entry_type"] == "admission_override"
    assert row["plan_id"] == OWN_PLAN.removesuffix("/_").rsplit("/", 1)[-1] and row["intent_id"] == "I-100-own/_"
    assert row["recorded_by"] == "flow" and row["created_at"]
    assert row["conflicts"][0]["files"] == ["src/app.py"]


def test_dry_run_only_logs(own_plan, monkeypatch):
    _use_real_check(monkeypatch, other_pr(["src/app.py"]))
    context = own_context(own_plan, dry_run=True)
    assert admission.admission_stage_failure(context, "2026-11-14T00:00:00Z") is None
    assert any("(dry run) admission: Stage 3 refused" in line for line in context.logs)


def test_ledger_readers_never_count_the_override_row(tmp_path):
    override = {"entry_type": "admission_override", "kind": "admission_override", "plan_branch": OWN_PLAN}
    summary = {"entry_type": "summary", "execution_id": "E-1-x", "plan_branch": OWN_PLAN, "status": "success"}
    ledger = tmp_path / "executions.jsonl"
    ledger.write_text(f"{json.dumps(summary)}\n{json.dumps(override)}\n", encoding="utf-8")
    assert parse_summary_records(ledger.read_text(encoding="utf-8")) == [summary]
    assert query_ledger(str(ledger), entry_type="summary") == [summary]
    assert query_ledger(str(ledger), entry_type="action") == []
    assert _latest_execution_summary([summary, override], "E-1-x") == summary
