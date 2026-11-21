"""Stage 3 touch-set admission (flow/admission.py): overlap decisions, refusal text and the recorded override."""

from __future__ import annotations

import functools
import json
from pathlib import Path

import pytest
from sandbox_executor.calibration_ev import parse_summary_records
from sandbox_executor.flow import (
    FlowContext,
    FlowStage,
    StageResult,
    StageStatus,
    admission,
    admission_record,
    admission_state,
)
from sandbox_executor.flow.admission import AdmissionDecision, check_admission, format_refusal
from sandbox_executor.flow.checkpoint import save_checkpoint
from sandbox_executor.flow.checkpoint_discovery import get_default_checkpoint_path
from sandbox_executor.flow.git_refs import _latest_execution_summary
from sandbox_executor.flow.pipeline import PipelineEngine
from sandbox_executor.flow.stages import touch_set_guard
from sandbox_executor.ledger import query_ledger

from tests.flow.helpers import OTHER_HEAD, OTHER_PLAN, FakeGh, _git, make_plan_branch, no_worktrees

THIRD_PLAN = "I-300-third/P-301-eeeeeeee-claude-agent-claude-opus-5-5/_"

OWN_PLAN = "I-100-own/P-101-cccccccc-claude-agent-claude-opus-5-5/_"
REAL_CHECK = check_admission


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
    assert [entry.files for entry in decision.blocked_by] == [("src/app.py",)]
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


def test_override_is_recorded_once_per_plan(own_plan, monkeypatch):
    """A resume under the same override adds no plan-branch commit, so the plan tip partial work forked from stays."""
    _use_real_check(monkeypatch, other_pr(["src/app.py"]))
    assert admission.admission_stage_failure(own_context(own_plan, allow_overlap=True), "2026-11-14T00:00:00Z") is None
    tip = _git(own_plan, "rev-parse", f"refs/remotes/origin/{OWN_PLAN}")
    resumed = own_context(own_plan, allow_overlap=True)
    assert admission.admission_stage_failure(resumed, "2026-11-14T01:00:00Z") is None
    assert _git(own_plan, "rev-parse", f"refs/remotes/origin/{OWN_PLAN}") == tip
    content = _git(own_plan, "show", f"refs/remotes/origin/{OWN_PLAN}:holon-knowledge/ledger/executions.jsonl")
    assert [json.loads(line)["kind"] for line in content.splitlines() if line.strip()] == ["admission_override"]
    assert any("override already recorded on" in line for line in resumed.logs)


def test_an_override_row_of_another_plan_does_not_count(own_plan):
    row = {"kind": "admission_override", "plan_id": "P-9-other", "plan_branch": "I-100-own/P-9-other/_"}
    _git(own_plan, "checkout", "-q", OWN_PLAN)
    ledger = own_plan / "holon-knowledge" / "ledger" / "executions.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps(row) + "\n", encoding="utf-8")
    _git(own_plan, "add", "-A")
    _git(own_plan, "commit", "-q", "-m", "foreign override row")
    _git(own_plan, "push", "-q", "origin", OWN_PLAN)
    assert not admission_record.override_already_recorded(str(own_plan), OWN_PLAN)


def test_override_that_cannot_be_recorded_is_refused(own_plan, monkeypatch):
    _use_real_check(monkeypatch, other_pr(["src/app.py"]))
    monkeypatch.setattr(admission, "_append_ledger_row", lambda *args, **kwargs: "push rejected")
    context = own_context(own_plan, allow_overlap=True)
    result = admission.admission_stage_failure(context, "2026-11-14T00:00:00Z")
    assert result is not None and result.status == StageStatus.FAILED
    assert result.payload["dispatched"] is False
    assert "could not be recorded" in str(result.error) and "push rejected" in str(result.error)
    assert context.admission is None and _admission(own_plan) is None  # the admitted record was undone


def test_touch_set_violation_reports_an_unrecorded_superseding_row(own_plan, monkeypatch):
    monkeypatch.setattr(touch_set_guard, "touch_set_violation", lambda context, changed: ["lib/extra.py"])
    monkeypatch.setattr(touch_set_guard, "_append_superseding_execution_row", lambda *args: "push rejected")
    context = own_context(own_plan)
    summary_row = {"entry_type": "summary", "execution_id": "E-1-x", "status": "success"}
    base_payload = {"execution_branch": "I-100-own/P-101-x/E-102-y/_", "status": "success"}
    result = touch_set_guard.touch_set_violation_result(
        context, "2026-11-14T00:00:00Z", summary_row, ["lib/extra.py"], base_payload
    )
    assert result is not None and result.status == StageStatus.FAILED
    assert result.payload["superseding_row_recorded"] is False
    assert result.payload["touch_set_violation"] == ["lib/extra.py"]
    assert any("could not record the touch-set violation row" in line for line in context.logs)


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


def _admission(host: Path) -> dict | None:
    """The top-level admission object of the run's own checkpoint (own_context has no slug: ``default``)."""
    path = Path(get_default_checkpoint_path(str(host), "default"))
    return json.loads(path.read_text(encoding="utf-8")).get("admission") if path.exists() else None


def _local_flow(host: Path, tmp_path: Path) -> Path:
    """A flow at execute in a second worktree whose plan declares docs/ (overlapping the own plan's docs/)."""
    make_plan_branch(host, THIRD_PLAN, [{"path": "docs/"}])
    second = tmp_path / "second"
    _git(host, "worktree", "add", "-q", "--detach", str(second), "main")
    context = FlowContext(target_worktree=str(second), intent_data={"slug": "third"}, plan_branch=THIRD_PLAN)
    for stage in (FlowStage.INTENT, FlowStage.PLAN):
        context.stage_results[stage.value] = StageResult(stage=stage, status=StageStatus.SUCCESS)
    save_checkpoint(context)
    return second


def _clock(monkeypatch: pytest.MonkeyPatch, *times: str) -> None:
    readings = iter(times)
    monkeypatch.setattr(admission_state, "utc_now", lambda: next(readings))


def test_refusal_records_blocked_with_each_pull_request_and_local_flow(own_plan, tmp_path, monkeypatch):
    second = _local_flow(own_plan, tmp_path)
    _clock(monkeypatch, "2026-11-20T01:00:00.000Z", "2026-11-20T01:00:00.000Z")
    decision = check_admission(own_context(own_plan), other_pr(["src/app.py"]))
    assert not decision.admitted
    recorded = _admission(own_plan)
    assert recorded is not None and recorded["state"] == "blocked"
    assert recorded["blocked_since"] == "2026-11-20T01:00:00.000Z"
    pull_request, local = recorded["blocked_by"]
    assert pull_request == {"kind": "pull_request", "number": 7, "branch": OTHER_HEAD, "files": ["src/app.py"]}
    assert local["kind"] == "local_flow" and local["plan_branch"] == THIRD_PLAN and local["files"] == ["docs/"]
    assert Path(local["worktree"]).resolve() == second.resolve()


def test_admission_records_admitted(own_plan):
    assert check_admission(own_context(own_plan), other_pr(["lib/other.py"]), no_worktrees).admitted
    assert _admission(own_plan) == {"state": "admitted"}


def test_override_records_admitted_with_the_override(own_plan):
    decision = check_admission(own_context(own_plan, allow_overlap=True), other_pr(["src/app.py"]), no_worktrees)
    assert decision.admitted and decision.overridden
    assert _admission(own_plan) == {
        "state": "admitted",
        "override": True,
        "override_conflicts": [{"flow": f"pull request #7 (branch {OTHER_HEAD})", "files": ["src/app.py"]}],
    }


def test_blocked_since_is_kept_and_blocked_by_replaced_across_refusals(own_plan, monkeypatch):
    _clock(monkeypatch, *(f"2026-11-20T0{hour}:00:00.000Z" for hour in range(1, 5)))
    context = own_context(own_plan)
    check_admission(context, other_pr(["src/app.py"]), no_worktrees)
    first = _admission(own_plan)
    later_pr = FakeGh([{"number": 8, "headRefName": OTHER_HEAD, "baseRefName": "main"}], {8: ["docs/guide.md"]})
    check_admission(context, later_pr, no_worktrees)
    second = _admission(own_plan)
    assert first is not None and second is not None
    assert first["blocked_since"] == second["blocked_since"] == "2026-11-20T01:00:00.000Z"
    assert [entry["number"] for entry in first["blocked_by"]] == [7]
    assert [(entry["number"], entry["files"]) for entry in second["blocked_by"]] == [(8, ["docs/guide.md"])]


def test_non_overlap_refusals_record_nothing(own_plan):
    decision = check_admission(own_context(own_plan), FakeGh([], {}, fail=True), no_worktrees)
    assert not decision.admitted and _admission(own_plan) is None


def test_a_lock_that_cannot_be_taken_fails_closed(tmp_path):
    context = FlowContext(target_worktree=str(tmp_path), plan_branch=OWN_PLAN, token_reduce=False)
    decision = check_admission(context, FakeGh([], {}), no_worktrees)
    assert not decision.admitted and "admission lock could not be taken" in str(decision.reason)


def test_a_lock_failure_refuses_even_under_allow_overlap(tmp_path):
    context = FlowContext(target_worktree=str(tmp_path), plan_branch=OWN_PLAN, token_reduce=False, allow_overlap=True)
    decision = check_admission(context, FakeGh([], {}), no_worktrees)
    assert not decision.admitted and not decision.overridden


def test_an_unsaved_admission_state_refuses_even_under_allow_overlap(own_plan, monkeypatch):
    """A blocked checkpoint that cannot be replaced would hide the overriding run from discovery: refuse it."""
    monkeypatch.setattr(admission, "save_admission", lambda context, record: "disk full")
    context = own_context(own_plan, allow_overlap=True)
    decision = check_admission(context, other_pr(["src/app.py"]), no_worktrees)
    assert not decision.admitted and not decision.overridden
    assert decision.reason == "the admission state could not be recorded: disk full"
    _use_real_check(monkeypatch, other_pr(["src/app.py"]))
    result = admission.admission_stage_failure(context, "2026-11-14T00:00:00Z")
    assert result is not None and result.status == StageStatus.FAILED and result.payload["dispatched"] is False


def test_the_lock_uses_the_injected_git_runner(own_plan):
    calls: list[list[str]] = []

    def recording_git(args, cwd):
        calls.append(args)
        return no_worktrees(args, cwd)

    check_admission(own_context(own_plan), other_pr(["lib/x.py"]), recording_git)
    assert ["rev-parse", "--git-common-dir"] in calls


def test_admission_state_is_saved_at_the_engines_checkpoint_path(own_plan, tmp_path):
    custom = tmp_path / "custom-checkpoint.json"
    context = own_context(own_plan)
    PipelineEngine(context, checkpoint_path=str(custom))  # the engine hands its --checkpoint path to the context
    assert check_admission(context, other_pr(["src/app.py"]), no_worktrees).blocked_by
    assert json.loads(custom.read_text(encoding="utf-8"))["context"]["admission"]["state"] == "blocked"
    assert _admission(own_plan) is None  # no frozen copy at the default path


def test_dry_run_writes_no_admission_state(own_plan, monkeypatch):
    _use_real_check(monkeypatch, other_pr(["src/app.py"]))
    context = own_context(own_plan, dry_run=True)
    assert admission.admission_stage_failure(context, "2026-11-14T00:00:00Z") is None
    assert context.admission is None and _admission(own_plan) is None


BLOCKED_AT = "2026-11-20T01:00:00.000Z"
PLAN = "I-1-x/P-2-claude-agent-claude-opus-5-5/_"


def _blocked() -> admission_state.AdmissionRecord:
    entry = admission_state.BlockedBy(admission_state.BLOCKED_BY_PULL_REQUEST, ("src/app.py",), number=7, branch="b")
    return admission_state.blocked_record(None, (entry,), BLOCKED_AT)


def test_an_admission_after_a_refusal_keeps_the_wait_and_owes_a_refresh():
    record = admission_state.admitted_record(None, _blocked())
    assert record.to_dict() == {"state": "admitted", "waited_since": BLOCKED_AT}
    assert record.refresh_due(PLAN)


def test_a_first_check_admission_records_no_wait_and_owes_no_refresh():
    record = admission_state.admitted_record(None, None)
    assert record.to_dict() == {"state": "admitted"}
    assert not record.refresh_due(PLAN)


def test_an_admitted_resume_keeps_the_wait_and_the_refresh():
    waited = admission_state.admitted_record(None, _blocked())
    resumed = admission_state.admitted_record(None, admission_state.AdmissionRecord.from_dict(waited.to_dict()))
    assert resumed.waited_since == BLOCKED_AT and resumed.refresh_due(PLAN)
    refreshed = admission_state.AdmissionRecord.from_dict(resumed.with_refresh("abc123", PLAN).to_dict())
    assert refreshed is not None and refreshed.refreshed_to == "abc123" and not refreshed.refresh_due(PLAN)
    again = admission_state.admitted_record([], refreshed)
    assert again.to_dict() == {
        "state": "admitted",
        "override": True,
        "override_conflicts": [],
        "waited_since": BLOCKED_AT,
        "refreshed_to": "abc123",
        "refreshed_branch": PLAN,
    }


def test_a_re_planned_branch_owes_its_own_refresh():
    refreshed = admission_state.admitted_record(None, _blocked()).with_refresh("abc123", PLAN)
    replanned = "I-1-x/P-3-claude-agent-claude-opus-5-5/_"
    assert refreshed.refresh_due(replanned) and refreshed.refreshed_on(replanned) is None
    legacy = admission_state.AdmissionRecord.from_dict({**refreshed.to_dict(), "refreshed_branch": None})
    assert legacy is not None and legacy.refresh_due(PLAN)  # a checkpoint without the branch refreshes again


def test_malformed_wait_and_refresh_values_are_ignored():
    record = admission_state.AdmissionRecord.from_dict({"state": "admitted", "waited_since": 5, "refreshed_to": []})
    assert record is not None and record.to_dict() == {"state": "admitted"} and not record.refresh_due(PLAN)
