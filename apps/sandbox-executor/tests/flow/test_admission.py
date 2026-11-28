from __future__ import annotations

import functools
import json
import subprocess
from collections.abc import Callable
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
from sandbox_executor.flow.run_lock import acquire_flow_run_lock, release_flow_run_lock
from sandbox_executor.flow.stages import touch_set_guard
from sandbox_executor.ledger import query_ledger, read_merged_rows_at_ref

from tests.flow.helpers import OTHER_HEAD, OTHER_PLAN, FakeGh, _git, make_plan_branch, no_worktrees, seed_base_files
from tests.flow_repo_templates import derived_template, replace_with_copy, template_name

THIRD_PLAN = "I-300-third/P-301-eeeeeeee-claude-agent-claude-opus-5-5/_"

OWN_PLAN = "I-100-own/P-101-cccccccc-claude-agent-claude-opus-5-5/_"
REAL_CHECK = check_admission


def own_context(host: Path, **overrides) -> FlowContext:
    return FlowContext(
        target_worktree=str(host), plan_branch=OWN_PLAN, intent_branch="I-100-own/_", token_reduce=False, **overrides
    )


def other_pr(diff: list[str] | str) -> FakeGh:
    return FakeGh([{"number": 7, "headRefName": OTHER_HEAD, "baseRefName": "main"}], {7: diff})


@pytest.fixture
def own_plan_with(flow_repos, tmp_path, tmp_path_factory, _flow_repos_template) -> Callable[..., Path]:
    names = ("origin.git", "host")

    def make(touch_set: list[dict], before: tuple[str, ...] = (), after: tuple[str, ...] = ()) -> Path:
        def scenario(root: Path) -> None:
            host = root / "host"
            if before:
                seed_base_files(host, list(before))
            make_plan_branch(host, OWN_PLAN, touch_set)
            # why: the plan branch is pushed so an override row can be appended.
            _git(host, "push", "-q", "origin", OWN_PLAN)
            if after:
                seed_base_files(host, list(after))

        name = template_name("admission-own-plan", touch_set, before, after)
        replace_with_copy(
            derived_template(tmp_path_factory, name, _flow_repos_template, names, scenario), tmp_path, names
        )
        return flow_repos[0]

    return make


@pytest.fixture
def own_plan(own_plan_with):
    # why: the base holds docs/guide.md, so both touch-set entries name an existing file.
    return own_plan_with([{"path": "src/app.py"}, {"path": "docs/"}], before=("docs/guide.md",))


def test_a_pull_request_is_compared_by_its_diff_not_its_planned_touch_set(own_plan):
    make_plan_branch(own_plan, OTHER_PLAN, [{"path": "src/"}, {"path": "docs/"}])
    decision = check_admission(own_context(own_plan), other_pr(["lib/x.py"]), no_worktrees)
    assert decision == AdmissionDecision(admitted=True)


def test_overlap_with_a_pull_request_diff_is_refused_and_named(own_plan):
    decision = check_admission(own_context(own_plan), other_pr(["src/app.py", "lib/x.py"]), no_worktrees)
    assert not decision.admitted and not decision.overridden
    assert [(c.label, c.files) for c in decision.conflicts] == [
        (f"pull request #7 (branch {OTHER_HEAD})", ("src/app.py",))
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
    # why: without a touch set there is nothing to compare, so discovery never runs.
    assert gh.calls == []
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
    rows = read_merged_rows_at_ref(str(own_plan), f"refs/remotes/origin/{OWN_PLAN}", "executions.jsonl")
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == row["entry_type"] == "admission_override"
    assert row["plan_id"] == OWN_PLAN.removesuffix("/_").rsplit("/", 1)[-1] and row["intent_id"] == "I-100-own/_"
    assert row["recorded_by"] == "flow" and row["created_at"]
    assert row["conflicts"][0]["files"] == ["src/app.py"]


def test_override_is_recorded_once_per_plan(own_plan, monkeypatch):
    # why: partial work forks from the plan tip, so a resume under the same override must not move it.
    _use_real_check(monkeypatch, other_pr(["src/app.py"]))
    assert admission.admission_stage_failure(own_context(own_plan, allow_overlap=True), "2026-11-14T00:00:00Z") is None
    tip = _git(own_plan, "rev-parse", f"refs/remotes/origin/{OWN_PLAN}")
    resumed = own_context(own_plan, allow_overlap=True)
    assert admission.admission_stage_failure(resumed, "2026-11-14T01:00:00Z") is None
    assert _git(own_plan, "rev-parse", f"refs/remotes/origin/{OWN_PLAN}") == tip
    rows = read_merged_rows_at_ref(str(own_plan), f"refs/remotes/origin/{OWN_PLAN}", "executions.jsonl")
    assert [row["kind"] for row in rows] == ["admission_override"]
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
    admitted_record_rolled_back = context.admission is None and _default_checkpoint_admission(own_plan) is None
    assert admitted_record_rolled_back


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


def _default_checkpoint_admission(host: Path) -> dict | None:
    # why: own_context has no slug, so its checkpoint is the "default" one.
    path = Path(get_default_checkpoint_path(str(host), "default"))
    return json.loads(path.read_text(encoding="utf-8")).get("admission") if path.exists() else None


@pytest.fixture(autouse=True)
def _release_run_locks():
    yield
    release_flow_run_lock()


def _second_worktree_flow_at_execute(
    host: Path,
    tmp_path: Path,
    touch_set: list[dict] | None = None,
    live: bool = True,
    admission_data: dict | None = None,
) -> Path:
    make_plan_branch(host, THIRD_PLAN, touch_set or [{"path": "docs/"}])
    second = tmp_path / "second"
    _git(host, "worktree", "add", "-q", "--detach", str(second), "main")
    context = FlowContext(
        target_worktree=str(second),
        intent_data={"slug": "third"},
        plan_branch=THIRD_PLAN,
        admission=admission_data,
    )
    for stage in (FlowStage.INTENT, FlowStage.PLAN):
        context.stage_results[stage.value] = StageResult(stage=stage, status=StageStatus.SUCCESS)
    save_checkpoint(context)
    if live:
        assert acquire_flow_run_lock(str(second), "third", "live-third") is not None
    return second


def _clock(monkeypatch: pytest.MonkeyPatch, *times: str) -> None:
    readings = iter(times)
    monkeypatch.setattr(admission_state, "utc_now", lambda: next(readings))


def test_refusal_records_blocked_with_each_pull_request_and_local_flow(own_plan, tmp_path, monkeypatch):
    second = _second_worktree_flow_at_execute(own_plan, tmp_path)
    _clock(monkeypatch, "2026-11-20T01:00:00.000Z", "2026-11-20T01:00:00.000Z")
    decision = check_admission(own_context(own_plan), other_pr(["src/app.py"]))
    assert not decision.admitted
    recorded = _default_checkpoint_admission(own_plan)
    assert recorded is not None and recorded["state"] == "blocked"
    assert recorded["blocked_since"] == "2026-11-20T01:00:00.000Z"
    pull_request, local = recorded["blocked_by"]
    assert pull_request == {"kind": "pull_request", "number": 7, "branch": OTHER_HEAD, "files": ["src/app.py"]}
    assert local["kind"] == "local_flow" and local["plan_branch"] == THIRD_PLAN and local["files"] == ["docs/guide.md"]
    assert Path(local["worktree"]).resolve() == second.resolve()


def test_admission_records_admitted(own_plan):
    assert check_admission(own_context(own_plan), other_pr(["lib/other.py"]), no_worktrees).admitted
    assert _default_checkpoint_admission(own_plan) == {"state": "admitted"}


def test_override_records_admitted_with_the_override(own_plan):
    decision = check_admission(own_context(own_plan, allow_overlap=True), other_pr(["src/app.py"]), no_worktrees)
    assert decision.admitted and decision.overridden
    assert _default_checkpoint_admission(own_plan) == {
        "state": "admitted",
        "override": True,
        "override_conflicts": [{"flow": f"pull request #7 (branch {OTHER_HEAD})", "files": ["src/app.py"]}],
    }


def test_blocked_since_is_kept_and_blocked_by_replaced_across_refusals(own_plan, monkeypatch):
    _clock(monkeypatch, *(f"2026-11-20T0{hour}:00:00.000Z" for hour in range(1, 5)))
    context = own_context(own_plan)
    check_admission(context, other_pr(["src/app.py"]), no_worktrees)
    first = _default_checkpoint_admission(own_plan)
    later_pr = FakeGh([{"number": 8, "headRefName": OTHER_HEAD, "baseRefName": "main"}], {8: ["docs/guide.md"]})
    check_admission(context, later_pr, no_worktrees)
    second = _default_checkpoint_admission(own_plan)
    assert first is not None and second is not None
    assert first["blocked_since"] == second["blocked_since"] == "2026-11-20T01:00:00.000Z"
    assert [entry["number"] for entry in first["blocked_by"]] == [7]
    assert [(entry["number"], entry["files"]) for entry in second["blocked_by"]] == [(8, ["docs/guide.md"])]


def test_non_overlap_refusals_record_nothing(own_plan):
    decision = check_admission(own_context(own_plan), FakeGh([], {}, fail=True), no_worktrees)
    assert not decision.admitted and _default_checkpoint_admission(own_plan) is None


def test_a_lock_that_cannot_be_taken_fails_closed(tmp_path):
    context = FlowContext(target_worktree=str(tmp_path), plan_branch=OWN_PLAN, token_reduce=False)
    decision = check_admission(context, FakeGh([], {}), no_worktrees)
    assert not decision.admitted and "admission lock could not be taken" in str(decision.reason)


def test_a_lock_failure_refuses_even_under_allow_overlap(tmp_path):
    context = FlowContext(target_worktree=str(tmp_path), plan_branch=OWN_PLAN, token_reduce=False, allow_overlap=True)
    decision = check_admission(context, FakeGh([], {}), no_worktrees)
    assert not decision.admitted and not decision.overridden


def test_an_unsaved_admission_state_refuses_even_under_allow_overlap(own_plan, monkeypatch):
    # why: a blocked checkpoint that cannot be replaced would hide the overriding run from discovery.
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


def _bind_engine_checkpoint_path(context, checkpoint_path):
    PipelineEngine(context, checkpoint_path=str(checkpoint_path))


def test_admission_state_is_saved_at_the_engines_checkpoint_path(own_plan, tmp_path):
    custom = tmp_path / "custom-checkpoint.json"
    context = own_context(own_plan)
    _bind_engine_checkpoint_path(context, custom)
    assert check_admission(context, other_pr(["src/app.py"]), no_worktrees).blocked_by
    assert json.loads(custom.read_text(encoding="utf-8"))["context"]["admission"]["state"] == "blocked"
    assert _default_checkpoint_admission(own_plan) is None


def test_dry_run_writes_no_admission_state(own_plan, monkeypatch):
    _use_real_check(monkeypatch, other_pr(["src/app.py"]))
    context = own_context(own_plan, dry_run=True)
    assert admission.admission_stage_failure(context, "2026-11-14T00:00:00Z") is None
    assert context.admission is None and _default_checkpoint_admission(own_plan) is None


WILDCARDS = [{"path": "tests/test_*.py"}, {"path": "tests/flow/**/test_*.py"}, {"path": "apps/x/tests/"}]
NEW_TESTS = ["tests/test_new.py", "tests/flow/a/test_new.py", "apps/x/tests/test_new.py"]
BASE_TESTS = ["tests/test_old.py", "tests/flow/a/test_old.py", "apps/x/tests/test_old.py"]


def test_new_files_never_overlap_folder_or_wildcard_entries(own_plan_with, tmp_path):
    # why: base files under the wildcards prove the entries match existing files, never the new ones.
    host = own_plan_with(WILDCARDS, after=tuple(BASE_TESTS))
    assert check_admission(own_context(host), other_pr(NEW_TESTS), no_worktrees).admitted
    _second_worktree_flow_at_execute(host, tmp_path, [{"path": path} for path in NEW_TESTS])
    decision = check_admission(own_context(host), other_pr([]))
    assert decision == AdmissionDecision(admitted=True)


def test_an_identical_new_literal_path_still_overlaps(own_plan_with, tmp_path):
    host = own_plan_with([{"path": "src/new.py"}, {"path": "apps/x/tests/"}])
    assert check_admission(own_context(host), other_pr(["src/new.py"]), no_worktrees).conflicts[0].files == (
        "src/new.py",
    )
    _second_worktree_flow_at_execute(host, tmp_path, [{"path": "src/new.py"}])
    decision = check_admission(own_context(host), other_pr([]))
    assert not decision.admitted and [c.files for c in decision.conflicts] == [("src/new.py",)]


def test_an_existing_file_under_a_folder_entry_still_overlaps_and_is_named_concretely(own_plan_with, tmp_path):
    host = own_plan_with([{"path": "apps/x/tests/"}], after=tuple(BASE_TESTS))
    by_pr = check_admission(own_context(host), other_pr(["apps/x/tests/test_old.py"]), no_worktrees)
    assert not by_pr.admitted and by_pr.conflicts[0].files == ("apps/x/tests/test_old.py",)
    _second_worktree_flow_at_execute(
        host, tmp_path, [{"path": "apps/x/tests/test_old.py"}, {"path": "apps/x/tests/test_new.py"}]
    )
    by_local = check_admission(own_context(host), other_pr([]))
    assert not by_local.admitted
    assert [c.files for c in by_local.conflicts] == [("apps/x/tests/test_old.py",)]
    assert [entry.files for entry in by_local.blocked_by] == [("apps/x/tests/test_old.py",)]
    message = format_refusal(by_local)
    assert "on apps/x/tests/test_old.py;" in message and "apps/x/tests/ " not in message
    assert "apps/x/tests/," not in message and "(its entry" not in message


def test_a_folder_against_folder_refusal_lists_twenty_files_and_counts_the_rest(own_plan_with, tmp_path):
    seeded = [f"lib/m{n:02d}.py" for n in range(25)]
    host = own_plan_with([{"path": "lib/"}], after=tuple(seeded))
    _second_worktree_flow_at_execute(host, tmp_path, [{"path": "lib/"}, {"path": "lib/brand_new.py"}])
    decision = check_admission(own_context(host), other_pr([]))
    assert decision.conflicts[0].files == tuple(seeded) and decision.blocked_by[0].files == tuple(seeded)
    assert decision.to_payload()["conflicts"][0]["files"] == seeded
    message = format_refusal(decision)
    assert f"on {', '.join(seeded[:20])} and 5 more;" in message and "lib/m20.py" not in message


def test_a_base_listing_failure_fails_closed(own_plan):
    def no_ls_tree(args, cwd):
        if args[:1] == ["ls-tree"]:
            return subprocess.CompletedProcess(["git", *args], 128, "", "fatal: not a tree object")
        return no_worktrees(args, cwd)

    decision = check_admission(own_context(own_plan), other_pr(["lib/x.py"]), no_ls_tree)
    assert not decision.admitted and not decision.blocked_by and _default_checkpoint_admission(own_plan) is None
    assert decision.reason is not None and decision.reason.startswith(
        "the files of the base branch could not be listed"
    )
    assert "not a tree object" in decision.reason


def test_an_unresolved_target_branch_fails_closed(own_plan):
    context = own_context(own_plan, intent_data={"target_branch": "release-9"})
    decision = check_admission(context, other_pr(["lib/x.py"]), no_worktrees)
    assert not decision.admitted and "the base branch release-9 could not be resolved" in str(decision.reason)


def test_with_nothing_in_flight_the_base_is_never_listed(own_plan):
    # why: an unresolvable base proves the base is never read.
    context = own_context(own_plan, intent_data={"target_branch": "release-9"})
    assert check_admission(context, FakeGh([], {}), no_worktrees) == AdmissionDecision(admitted=True)


def test_a_pull_request_renaming_a_file_out_of_a_folder_entry_overlaps(own_plan_with):
    # why: the rename's source exists on the base, so it overlaps although its destination is new.
    host = own_plan_with([{"path": "apps/x/tests/"}], after=tuple(BASE_TESTS))
    rename = (
        "diff --git a/apps/x/tests/test_old.py b/lib/test_old.py\nsimilarity index 100%\n"
        "rename from apps/x/tests/test_old.py\nrename to lib/test_old.py\n"
    )
    decision = check_admission(own_context(host), other_pr(rename), no_worktrees)
    assert not decision.admitted and decision.conflicts[0].files == ("apps/x/tests/test_old.py",)


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
    checkpoint_without_branch = admission_state.AdmissionRecord.from_dict(
        {**refreshed.to_dict(), "refreshed_branch": None}
    )
    assert checkpoint_without_branch is not None and checkpoint_without_branch.refresh_due(PLAN)


def test_malformed_wait_and_refresh_values_are_ignored():
    record = admission_state.AdmissionRecord.from_dict({"state": "admitted", "waited_since": 5, "refreshed_to": []})
    assert record is not None and record.to_dict() == {"state": "admitted"} and not record.refresh_due(PLAN)


PARKED_BLOCKED = {
    "state": "blocked",
    "blocked_since": BLOCKED_AT,
    "blocked_by": [{"kind": "pull_request", "number": 7, "branch": OTHER_HEAD, "files": ["docs/guide.md"]}],
}


@pytest.mark.parametrize("parked_admission", [None, {"state": "admitted"}, PARKED_BLOCKED])
def test_candidate_overlapping_a_parked_flow_with_no_live_runner_is_admitted(own_plan, tmp_path, parked_admission):
    _second_worktree_flow_at_execute(
        own_plan, tmp_path, [{"path": "docs/"}], live=False, admission_data=parked_admission
    )
    decision = check_admission(own_context(own_plan), FakeGh([], {}))
    assert decision == AdmissionDecision(admitted=True)


def test_parked_flow_with_an_open_pull_request_still_blocks_admission(own_plan, tmp_path):
    _second_worktree_flow_at_execute(own_plan, tmp_path, [{"path": "docs/"}], live=False)
    decision = check_admission(own_context(own_plan), other_pr(["src/app.py"]))
    assert not decision.admitted
    assert [(c.label, c.files) for c in decision.conflicts] == [
        (f"pull request #7 (branch {OTHER_HEAD})", ("src/app.py",))
    ]
