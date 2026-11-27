from __future__ import annotations

import copy
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sandbox_executor.calibration import parse_evaluated_commit_sha
from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.calibrate_rerun import (
    HeadMoveCheck,
    RerunSeams,
    post_stale_signal,
    report_staleness,
    rerun_calibration,
    review_calibration_freshness,
)
from sandbox_executor.flow.calibrate_stale_warning import (
    STALE_MARKER,
    SUPERSEDED_MARKER_PREFIX,
    StaleRange,
    WarningSeams,
    resolve_stale,
    stale_body,
)
from sandbox_executor.flow.stages.calibrate import _supersede_stale_warning, run_calibrate_stage

from tests.calibrate_scenarios import (
    CAL_REPORT_REL,
    CAL_SUBJECT,
    LEDGER_REL,
    PLAN_ID,
    TARGET_REF,
    FakeThread,
    advance_main,
    clone_flow_pair,
    commit_calibration,
    ledger_row,
    merge_main,
    own_copy,
    pr258_history,
    run_guarded_template,
)
from tests.repo_templates import shared_template
from tests.test_calibrate_sync import CALIBRATED, EXEC, commit_file, git, make_repos

__all__ = ["TARGET_REF", "advance_main", "commit_calibration", "ledger_row", "merge_main", "pr258_history"]


@pytest.fixture(scope="session")
def rerun_repos_template(tmp_path_factory: pytest.TempPathFactory, hermetic_git_config: str) -> SimpleNamespace:
    root, built = shared_template(tmp_path_factory, "calibrate-rerun-template", make_repos)
    return SimpleNamespace(root=root, repos=built)


@pytest.fixture
def repos(rerun_repos_template: SimpleNamespace, tmp_path: Path) -> SimpleNamespace:
    return own_copy(rerun_repos_template, tmp_path)


# why: a template built from a run a guard refused a call of would hide that call from every later test
def calibrate_a_copy(template: SimpleNamespace, root: Path, blocked: Sequence[list[str]]) -> SimpleNamespace:
    built = own_copy(template, root)
    ctx = FlowContext(target_worktree=str(built.host), execution_branch=EXEC, agent="a", model="m")
    result = stage_runner([])(ctx)
    assert result.status == StageStatus.SUCCESS
    assert not any(blocked), f"the Stage 5 run of the calibrated template made calls a guard blocked: {blocked}"
    return SimpleNamespace(repos=built, first_run=(ctx, result))


@pytest.fixture
def calibrated_repos_template(
    rerun_repos_template: SimpleNamespace,
    tmp_path_factory: pytest.TempPathFactory,
    gh_write_guard: list[str],
    network_guard_fixture: list[str],
) -> SimpleNamespace:
    # why: function-scoped so the one real Stage 5 run happens under the first asking test's autouse guards
    # why: (gh writes, network, credentials, configuration), not before them as a session fixture would run it
    def build(root: Path) -> SimpleNamespace:
        return calibrate_a_copy(rerun_repos_template, root, (gh_write_guard, network_guard_fixture))

    root, calibrated = shared_template(tmp_path_factory, "calibrated-template", build)
    return SimpleNamespace(root=root, repos=calibrated.repos, first_run=calibrated.first_run)


@pytest.fixture
def calibrated(calibrated_repos_template: SimpleNamespace, tmp_path: Path) -> SimpleNamespace:
    own = own_copy(calibrated_repos_template, tmp_path / "calibrated")
    own.first_run = clone_flow_pair(calibrated_repos_template.root, calibrated_repos_template.first_run, own.host)
    return own


@pytest.fixture
def calibrated_moved_template(
    calibrated_repos_template: SimpleNamespace,
    tmp_path_factory: pytest.TempPathFactory,
    gh_write_guard: list[str],
    network_guard_fixture: list[str],
) -> SimpleNamespace:
    def build(root: Path) -> SimpleNamespace:
        own = own_copy(calibrated_repos_template, root)
        first_run = clone_flow_pair(calibrated_repos_template.root, calibrated_repos_template.first_run, own.host)
        return SimpleNamespace(repos=own, first_run=first_run, ledger_before=exec_ledger(own), new_head=move_head(own))

    root, moved = run_guarded_template(
        tmp_path_factory, "calibrated-moved-template", build, (gh_write_guard, network_guard_fixture)
    )
    return SimpleNamespace(root=root, **vars(moved))


@pytest.fixture
def calibrated_moved(calibrated_moved_template: SimpleNamespace, tmp_path: Path) -> SimpleNamespace:
    own = own_copy(calibrated_moved_template, tmp_path / "moved")
    own.first_run = clone_flow_pair(calibrated_moved_template.root, calibrated_moved_template.first_run, own.host)
    own.new_head, own.ledger_before = calibrated_moved_template.new_head, calibrated_moved_template.ledger_before
    return own


@pytest.fixture
def calibrated_rerun_template(
    calibrated_moved_template: SimpleNamespace,
    tmp_path_factory: pytest.TempPathFactory,
    gh_write_guard: list[str],
    network_guard_fixture: list[str],
) -> SimpleNamespace:
    def build(root: Path) -> SimpleNamespace:
        own = own_copy(calibrated_moved_template, root)
        ctx, result = clone_flow_pair(calibrated_moved_template.root, calibrated_moved_template.first_run, own.host)
        approve_pr(ctx)
        calls, thread = ["stage"], FakeThread()
        refreshed = rerun_calibration(ctx, result, stage_runner(calls, thread), seams=thread.seams())
        return SimpleNamespace(
            repos=own,
            first_run=(ctx, result),
            refreshed=refreshed,
            new_head=calibrated_moved_template.new_head,
            ledger_before=calibrated_moved_template.ledger_before,
            calls=calls,
            posted=list(thread.posted),
            edits=list(thread.edits),
            records=list(thread.records),
        )

    root, done = run_guarded_template(
        tmp_path_factory, "calibrated-rerun-template", build, (gh_write_guard, network_guard_fixture)
    )
    return SimpleNamespace(root=root, **vars(done))


@pytest.fixture
def calibrated_rerun(calibrated_rerun_template: SimpleNamespace, tmp_path: Path) -> SimpleNamespace:
    t = calibrated_rerun_template
    own = own_copy(t, tmp_path / "rerun")
    own.ctx, own.result = clone_flow_pair(t.root, t.first_run, own.host)
    own.refreshed = copy.deepcopy(t.refreshed)
    assert str(t.root) not in repr(own.refreshed), "the cached rerun recorded a path of the shared template"
    own.thread = FakeThread()
    own.thread.records, own.thread.posted, own.thread.edits = copy.deepcopy(t.records), list(t.posted), list(t.edits)
    own.new_head, own.ledger_before, own.calls = t.new_head, t.ledger_before, list(t.calls)
    return own


@pytest.fixture
def calibrated_outran_template(
    calibrated_moved_template: SimpleNamespace,
    tmp_path_factory: pytest.TempPathFactory,
    gh_write_guard: list[str],
    network_guard_fixture: list[str],
) -> SimpleNamespace:
    def build(root: Path) -> SimpleNamespace:
        own = own_copy(calibrated_moved_template, root)
        ctx, result = clone_flow_pair(calibrated_moved_template.root, calibrated_moved_template.first_run, own.host)
        approve_pr(ctx)
        thread, attempts, posted_at_attempt = FakeThread(), [], []

        def never_catching_up(context: FlowContext) -> StageResult:
            attempts.append("stage")
            posted_at_attempt.append(len(thread.posted))
            return result

        stale = rerun_calibration(ctx, result, never_catching_up, seams=thread.seams())
        return SimpleNamespace(
            repos=own,
            first_run=(ctx, result),
            stale=stale,
            new_head=calibrated_moved_template.new_head,
            attempts=attempts,
            posted_at_attempt=posted_at_attempt,
            posted=list(thread.posted),
            edits=list(thread.edits),
            records=list(thread.records),
        )

    root, done = run_guarded_template(
        tmp_path_factory, "calibrated-outran-template", build, (gh_write_guard, network_guard_fixture)
    )
    return SimpleNamespace(root=root, **vars(done))


@pytest.fixture
def calibrated_outran(calibrated_outran_template: SimpleNamespace, tmp_path: Path) -> SimpleNamespace:
    t = calibrated_outran_template
    own = own_copy(t, tmp_path / "outran")
    own.ctx, own.result = clone_flow_pair(t.root, t.first_run, own.host)
    own.stale = copy.deepcopy(t.stale)
    assert str(t.root) not in repr(own.stale), "the cached outran run recorded a path of the shared template"
    own.thread = FakeThread()
    own.thread.records, own.thread.posted, own.thread.edits = copy.deepcopy(t.records), list(t.posted), list(t.edits)
    own.new_head, own.attempts, own.posted_at_attempt = t.new_head, list(t.attempts), list(t.posted_at_attempt)
    return own


@pytest.fixture
def pr258_repos_template(
    rerun_repos_template: SimpleNamespace,
    tmp_path_factory: pytest.TempPathFactory,
    gh_write_guard: list[str],
    network_guard_fixture: list[str],
) -> SimpleNamespace:
    def build(root: Path) -> SimpleNamespace:
        own = own_copy(rerun_repos_template, root)
        return SimpleNamespace(repos=own, history=pr258_history(own))

    root, built = run_guarded_template(
        tmp_path_factory, "pr258-repos-template", build, (gh_write_guard, network_guard_fixture)
    )
    return SimpleNamespace(root=root, repos=built.repos, history=built.history)


@pytest.fixture
def pr258_repos(pr258_repos_template: SimpleNamespace, tmp_path: Path) -> SimpleNamespace:
    own = own_copy(pr258_repos_template, tmp_path / "pr258")
    own.history = pr258_repos_template.history
    return own


def realistic_calibrate(**kwargs: object) -> SimpleNamespace:
    cwd = Path(str(kwargs["target_worktree"]))
    local = subprocess.run(["git", "rev-parse", "--verify", "-q", f"refs/heads/{CALIBRATED}"], cwd=cwd)
    git(cwd, "checkout", "-q", CALIBRATED) if local.returncode == 0 else git(cwd, "checkout", "-qb", CALIBRATED)
    evaluated = git(cwd, "rev-parse", "HEAD")
    report = cwd / CAL_REPORT_REL
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(f"# Calibration Report for {PLAN_ID}\n\n- **Evaluated Commit SHA:** {evaluated}\n")
    ledger = cwd / LEDGER_REL
    ledger.parent.mkdir(parents=True, exist_ok=True)
    row = {"action_id": f"ACT-CAL-{evaluated}", "entry_type": "action", "verdict": "success"}
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    git(cwd, "add", CAL_REPORT_REL, LEDGER_REL)
    git(cwd, "commit", "-qm", CAL_SUBJECT)
    report = {"plan_id": PLAN_ID, "evaluated_commit_sha": evaluated}
    return SimpleNamespace(calibrated_branch=CALIBRATED, to_dict=lambda: report)


def stage5_resolve_stale(thread: FakeThread) -> object:
    seams = WarningSeams(comments=thread.comments, poster=thread.poster, editor=thread.editor)
    return lambda context, pr_number, evaluated: resolve_stale(context, pr_number, evaluated, seams)


def stage_runner(calls: list[str], thread: FakeThread | None = None) -> object:
    # why: Stage 5's own supersede-after-push edits the given thread, else an empty thread nobody reads
    resolver = stage5_resolve_stale(thread or FakeThread())

    def run(ctx: FlowContext) -> StageResult:
        calls.append("stage")
        with (
            patch("sandbox_executor.flow.stages.calibrate._stage5_conflict_gate", return_value=None),
            patch("sandbox_executor.flow.stages.calibrate.sync_before_calibration", return_value=None),
            patch("sandbox_executor.flow.stages.calibrate.run_calibrate", side_effect=realistic_calibrate),
            patch("sandbox_executor.flow.stages.calibrate.resolve_stale", side_effect=resolver),
            patch("sandbox_executor.flow.stages.calibrate.check_final_head_ci", return_value={"state": "success"}),
        ):
            return run_calibrate_stage(ctx)

    return run


def calibrated_flow(
    calibrated: SimpleNamespace, thread: FakeThread | None = None
) -> tuple[FlowContext, StageResult, list[str], object]:
    ctx, result = calibrated.first_run
    # why: the recorder starts at the template's one run, which did not reach the thread as no review named a PR
    calls = ["stage"]
    return ctx, result, calls, stage_runner(calls, thread)


def move_head(repos: SimpleNamespace, name: str = "resolver-fix.txt") -> str:
    git(repos.other, "fetch", "-q", "origin")
    git(repos.other, "reset", "-q", "--hard", f"origin/{EXEC}")
    sha = commit_file(repos.other, name)
    git(repos.other, "push", "-q", "origin", EXEC)
    return sha


def approve_pr(ctx: FlowContext, number: str = "7") -> None:
    ctx.stage_results["review"] = StageResult(
        stage=FlowStage.REVIEW,
        status=StageStatus.HALTED_FOR_HUMAN,
        payload={"consensus": {"approved": True, "pr_number": number}, "posting": {"dual_posted": True}},
    )


def recording_thread() -> tuple[RerunSeams, FakeThread]:
    thread = FakeThread()
    return thread.seams(), thread


def recording_seams() -> tuple[RerunSeams, list[str]]:
    seams, thread = recording_thread()
    return seams, thread.posted


def exec_ledger(repos: SimpleNamespace) -> str:
    return git(repos.host, "show", f"{EXEC}:{LEDGER_REL}")


def stale_warnings(bodies: list[str]) -> list[str]:
    return [body for body in bodies if "Calibration report is stale" in body]


def refreshed_notes(bodies: list[str]) -> list[str]:
    return [body for body in bodies if "Calibration refreshed" in body]


def push_bookkeeping(repos: SimpleNamespace) -> str:
    git(repos.other, "fetch", "-q", "origin")
    git(repos.other, "reset", "-q", "--hard", f"origin/{EXEC}")
    ledger_row(repos.other, "E-review")
    head = commit_calibration(
        repos.other, rel="holon-knowledge/kb/k.json", subject="chore(calibration): record knowledge"
    )
    git(repos.other, "push", "-q", "origin", EXEC)
    return head


def standalone_context(repos: SimpleNamespace) -> FlowContext:
    ctx = FlowContext(target_worktree=str(repos.host), execution_branch=EXEC, agent="a", model="m")
    ctx.intent_data = {"target_branch": "main"}
    return ctx


def test_a_head_move_after_calibration_reruns_stage5_and_the_report_names_the_new_head(
    calibrated_rerun: SimpleNamespace,
) -> None:
    refreshed, result, new_head = calibrated_rerun.refreshed, calibrated_rerun.result, calibrated_rerun.new_head
    assert (len(calibrated_rerun.calls), refreshed.status) == (2, StageStatus.SUCCESS)
    assert refreshed is not result
    assert refreshed.payload["calibrated_head"] == new_head
    assert refreshed.payload.get("calibration_stale") is None
    assert "Stage 5 rerun refreshed the report to" in refreshed.payload["ci_verdict"]["report"]
    report = git(calibrated_rerun.host, "show", f"{EXEC}:{CAL_REPORT_REL}")
    assert parse_evaluated_commit_sha(report) == new_head
    ledger_after = exec_ledger(calibrated_rerun)
    assert ledger_after.startswith(calibrated_rerun.ledger_before)
    act_cal = [line for line in ledger_after.splitlines() if '"ACT-CAL-' in line]
    assert len(act_cal) == 2


def test_no_calibrated_template_is_built_from_a_run_a_guard_blocked(
    rerun_repos_template: SimpleNamespace, tmp_path: Path, gh_write_guard: list[str]
) -> None:
    def stage5_with_a_swallowed_gh_write(ctx: FlowContext) -> StageResult:
        gh_write_guard.append("gh pr comment 7")
        return StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS)

    with (
        patch(f"{__name__}.stage_runner", return_value=stage5_with_a_swallowed_gh_write),
        pytest.raises(AssertionError, match="made calls a guard blocked"),
    ):
        calibrate_a_copy(rerun_repos_template, tmp_path, (gh_write_guard, []))
    # why: the blocked call was this test's own
    gh_write_guard.clear()


def test_an_unmoved_head_is_a_no_op_with_no_new_ledger_rows(calibrated: SimpleNamespace) -> None:
    ctx, result, calls, run = calibrated_flow(calibrated)
    refs_before, ledger_before = git(calibrated.host, "for-each-ref"), exec_ledger(calibrated)
    unchanged = rerun_calibration(ctx, result, run)
    assert unchanged is result
    assert len(calls) == 1
    assert git(calibrated.host, "for-each-ref") == refs_before
    assert exec_ledger(calibrated) == ledger_before


def test_a_rerun_does_not_loop_on_its_own_calibration_commits(calibrated_rerun: SimpleNamespace) -> None:
    ctx, refreshed, calls = calibrated_rerun.ctx, calibrated_rerun.refreshed, calibrated_rerun.calls
    run = stage_runner(calls)
    assert len(calls) == 2
    assert report_staleness(ctx).moved is False
    assert rerun_calibration(ctx, refreshed, run) is refreshed
    assert len(calls) == 2


def test_a_head_that_outruns_both_attempts_marks_the_payload_stale(calibrated_outran: SimpleNamespace) -> None:
    from sandbox_executor.cli.commands.flow import _calibrated_head_line

    stale = calibrated_outran.stale
    assert len(calibrated_outran.attempts) == 2
    assert stale.payload["calibration_stale"] is True
    assert calibrated_outran.new_head[:12] in stale.payload["stale_reason"]
    line = _calibrated_head_line(stale)
    assert line.startswith("calibration report is STALE")
    assert "not ready to merge" in line and "covers" in line


def test_the_stale_signal_is_superseded_by_the_rerun_and_no_second_comment_is_posted(
    calibrated_moved: SimpleNamespace,
) -> None:
    # why: Stage 5 here cannot reach the thread (its own edit goes nowhere), so the trigger's explicit number supersedes
    ctx, result, _, run = calibrated_flow(calibrated_moved)
    approve_pr(ctx)
    thread = FakeThread(stale_body(StaleRange("a" * 40, "b" * 40, "an earlier flow")))
    seams = thread.seams()
    refreshed = rerun_calibration(ctx, result, run, seams=seams)
    assert refreshed.status == StageStatus.SUCCESS
    assert refreshed.payload["stale_warning"] == {"posted": False, "edited": 0, "reason": "no open stale warning"}
    assert thread.posted == []
    (warning,) = (record.body for record in thread.records)
    assert "superseded" in warning and "Calibration report is stale" not in warning
    assert f"{SUPERSEDED_MARKER_PREFIX}{refreshed.payload['calibrated_head']} -->" in warning
    assert rerun_calibration(ctx, refreshed, run, seams=seams) is refreshed
    assert (len(thread.posted), len(thread.edits)) == (0, 1)


def test_stage5_supersedes_the_warning_right_after_the_push_and_before_the_final_head_ci_check(
    calibrated_moved: SimpleNamespace,
) -> None:
    thread = FakeThread()
    ctx, _, _, _ = calibrated_flow(calibrated_moved, thread)
    approve_pr(ctx)
    new_head = calibrated_moved.new_head
    post_stale_signal(ctx, report_staleness(ctx), thread.seams(), pr_number="7")
    assert len(stale_warnings(thread.stale_bodies())) == 1
    seen_at_ci_check: list[str] = []

    def final_head_ci(context: FlowContext, repo_dir: str, branch: str) -> dict[str, str]:
        (warning,) = thread.stale_bodies()
        seen_at_ci_check.append(warning)
        assert "Stale calibration warning superseded" in warning and "Calibration report is stale" not in warning
        assert f"{SUPERSEDED_MARKER_PREFIX}{new_head} -->" in warning and f"`{new_head[:7]}`" in warning
        assert parse_evaluated_commit_sha(git(calibrated_moved.origin, "show", f"{EXEC}:{CAL_REPORT_REL}")) == new_head
        return {"state": "success"}

    with (
        patch("sandbox_executor.flow.stages.calibrate._stage5_conflict_gate", return_value=None),
        patch("sandbox_executor.flow.stages.calibrate.sync_before_calibration", return_value=None),
        patch("sandbox_executor.flow.stages.calibrate.run_calibrate", side_effect=realistic_calibrate),
        patch("sandbox_executor.flow.stages.calibrate.resolve_stale", side_effect=stage5_resolve_stale(thread)),
        patch("sandbox_executor.flow.stages.calibrate.check_final_head_ci", side_effect=final_head_ci),
    ):
        result = run_calibrate_stage(ctx)

    assert (result.status, len(seen_at_ci_check)) == (StageStatus.SUCCESS, 1)
    assert result.payload["stale_warning"] == {"posted": False, "edited": 1, "reason": "superseded"}
    assert (len(thread.records), len(thread.posted), len(thread.edits)) == (1, 1, 1)
    assert thread.records[0].body == seen_at_ci_check[0]


def test_a_failed_publish_leaves_the_warning_open_and_records_no_supersession(
    calibrated_moved: SimpleNamespace,
) -> None:
    thread = FakeThread()
    ctx, _, _, _ = calibrated_flow(calibrated_moved, thread)
    approve_pr(ctx)
    post_stale_signal(ctx, report_staleness(ctx), thread.seams(), pr_number="7")
    with (
        patch("sandbox_executor.flow.stages.calibrate._stage5_conflict_gate", return_value=None),
        patch("sandbox_executor.flow.stages.calibrate.sync_before_calibration", return_value=None),
        patch("sandbox_executor.flow.stages.calibrate.run_calibrate", side_effect=realistic_calibrate),
        patch("sandbox_executor.flow.stages.calibrate.publish_calibration", return_value=False),
        patch("sandbox_executor.flow.stages.calibrate.resolve_stale", side_effect=stage5_resolve_stale(thread)) as hook,
        patch("sandbox_executor.flow.stages.calibrate.check_final_head_ci") as final_head_ci,
    ):
        result = run_calibrate_stage(ctx)

    assert (result.payload["stale_warning"], result.payload["calibrated_head"]) == (None, None)
    hook.assert_not_called()
    final_head_ci.assert_not_called()
    (warning,) = thread.stale_bodies()
    assert "Calibration report is stale" in warning and thread.edits == []


def test_a_rerun_adds_the_refreshed_note_only_when_no_warning_was_superseded(calibrated_rerun: SimpleNamespace) -> None:
    ctx, first, calls, thread = (
        calibrated_rerun.ctx,
        calibrated_rerun.refreshed,
        calibrated_rerun.calls,
        calibrated_rerun.thread,
    )
    run = stage_runner(calls, thread)
    assert first.payload["stale_warning"]["edited"] == 0
    (note,) = thread.posted
    assert "Calibration refreshed" in note and calibrated_rerun.new_head[:7] in note

    second_head = move_head(calibrated_rerun, "second-fix.txt")
    post_stale_signal(ctx, report_staleness(ctx), thread.seams(), pr_number="7")
    second = rerun_calibration(ctx, first, run, seams=thread.seams())
    assert (len(calls), second.payload["calibrated_head"]) == (3, second_head)
    assert second.payload["stale_warning"] == {"posted": False, "edited": 1, "reason": "superseded"}
    (warning,) = thread.stale_bodies()
    assert f"{SUPERSEDED_MARKER_PREFIX}{second_head} -->" in warning
    assert refreshed_notes(thread.posted) == [note]
    assert len(thread.records) == 2


@pytest.mark.parametrize(
    ("review_payload", "pr_number"),
    [
        ({"consensus": {"approved": True, "pr_number": 7}, "pr": {"pr_number": 9}}, "7"),
        ({"consensus": {"approved": False}, "pr": {"pr_number": 9}}, "9"),
        ({"consensus": "approved", "pr": ["9"]}, None),
        (None, None),
    ],
)
def test_stage5_supersedes_on_the_reviewed_pull_request_approved_or_not(
    tmp_path: Path, review_payload: dict | None, pr_number: str | None
) -> None:
    ctx = pr_context(tmp_path)
    if review_payload is not None:
        ctx.stage_results["review"] = StageResult(
            stage=FlowStage.REVIEW, status=StageStatus.HALTED_FOR_HUMAN, payload=review_payload
        )
    record = {"posted": False, "edited": 0, "reason": "no open stale warning"}
    with patch("sandbox_executor.flow.stages.calibrate.resolve_stale", return_value=record) as hook:
        assert _supersede_stale_warning(ctx, "c" * 40) is record
    hook.assert_called_once_with(ctx, pr_number, "c" * 40)


def test_a_successful_rerun_on_a_code_push_posts_no_stale_warning(calibrated_rerun: SimpleNamespace) -> None:
    thread = calibrated_rerun.thread
    assert (len(calibrated_rerun.calls), calibrated_rerun.refreshed.status) == (2, StageStatus.SUCCESS)
    assert thread.stale_bodies() == [] and thread.edits == []
    (note,) = thread.posted
    assert "Calibration refreshed" in note and calibrated_rerun.new_head[:7] in note


def test_a_still_stale_head_never_duplicates_the_stale_comment(calibrated_outran: SimpleNamespace) -> None:
    ctx, result, thread = calibrated_outran.ctx, calibrated_outran.result, calibrated_outran.thread
    assert calibrated_outran.posted_at_attempt == [0, 0]
    (warning,) = thread.stale_bodies()
    assert "Calibration report is stale" in warning and calibrated_outran.new_head[:7] in warning
    rerun_calibration(ctx, result, lambda context: result, seams=thread.seams())
    assert (len(thread.posted), len(thread.edits)) == (1, 0)


def test_a_failed_stage5_result_with_a_stale_old_report_posts_the_warning_once(
    calibrated_moved: SimpleNamespace,
) -> None:
    ctx, _, calls, run = calibrated_flow(calibrated_moved)
    approve_pr(ctx)
    seams, thread = recording_thread()
    new_head = calibrated_moved.new_head
    failed = StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.FAILED, error="calibrate blew up")
    assert rerun_calibration(ctx, failed, run, seams=seams) is failed
    assert rerun_calibration(ctx, failed, run, seams=seams) is failed
    assert len(calls) == 1
    (warning,) = thread.stale_bodies()
    assert "Calibration report is stale" in warning and new_head[:7] in warning
    assert (len(thread.posted), len(thread.edits)) == (1, 0)


def test_a_failed_stage5_result_with_a_current_report_or_a_dry_run_posts_nothing(
    calibrated: SimpleNamespace, calibrated_moved: SimpleNamespace
) -> None:
    ctx, _, calls, run = calibrated_flow(calibrated)
    approve_pr(ctx)
    seams, thread = recording_thread()
    failed = StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.FAILED, error="calibrate blew up")
    assert rerun_calibration(ctx, failed, run, seams=seams) is failed
    moved_ctx, _, moved_calls, moved_run = calibrated_flow(calibrated_moved)
    approve_pr(moved_ctx)
    moved_ctx.dry_run = True
    assert rerun_calibration(moved_ctx, failed, moved_run, seams=seams) is failed
    assert (thread.posted, thread.edits, len(calls), len(moved_calls)) == ([], [], 1, 1)


def test_a_review_restart_result_posts_nothing_and_is_not_checked(calibrated_moved: SimpleNamespace) -> None:
    ctx, result, calls, run = calibrated_flow(calibrated_moved)
    approve_pr(ctx)
    thread, head_lookups = FakeThread(), []
    seams = RerunSeams(
        head_resolver=lambda context: head_lookups.append("head") or "",
        comments=thread.comments,
        poster=thread.poster,
        editor=thread.editor,
    )
    restart = StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SKIPPED, payload={"review_restart": True})
    assert rerun_calibration(ctx, restart, run, seams=seams) is restart
    assert (thread.posted, thread.edits, head_lookups, len(calls)) == ([], [], [], 1)
    assert rerun_calibration(ctx, result, lambda context: restart, seams=thread.seams()) is restart
    assert (thread.posted, thread.edits) == ([], [])


def test_a_rerun_that_fails_after_publishing_a_covering_report_posts_nothing(calibrated_moved: SimpleNamespace) -> None:
    ctx, result, calls, run = calibrated_flow(calibrated_moved)
    approve_pr(ctx)
    seams, thread = recording_thread()

    def red_final_head_ci(context: FlowContext) -> StageResult:
        run(context)
        return StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.FAILED, error="final-head CI is red")

    failed = rerun_calibration(ctx, result, red_final_head_ci, seams=seams)
    assert (failed.status, failed.error, len(calls)) == (StageStatus.FAILED, "final-head CI is red", 2)
    assert report_staleness(ctx).moved is False
    assert (thread.posted, thread.edits) == ([], [])


def stale_check(evaluated: str, head: str) -> HeadMoveCheck:
    return HeadMoveCheck(True, evaluated, head, "non-calibration commits landed")


def pr_context(tmp_path: Path) -> FlowContext:
    return FlowContext(target_worktree=str(tmp_path), execution_branch=EXEC, agent="a", model="m")


def test_a_stale_warning_followed_by_a_fresh_calibration_is_superseded_naming_the_evaluated_sha(
    tmp_path: Path,
) -> None:
    ctx, thread = pr_context(tmp_path), FakeThread()
    post_stale_signal(ctx, stale_check("a" * 40, "b" * 40), thread.seams(), pr_number="7")
    fresh = "c" * 40

    seams = WarningSeams(comments=thread.comments, poster=thread.poster, editor=thread.editor)
    resolved = resolve_stale(ctx, "7", fresh, seams)

    assert resolved == {"posted": False, "edited": 1, "reason": "superseded"}
    (warning,) = thread.stale_bodies()
    assert "superseded by the fresh calibration report" in warning and f"`{fresh[:7]}`" in warning
    assert STALE_MARKER in warning and f"{SUPERSEDED_MARKER_PREFIX}{fresh} -->" in warning
    assert "do not merge" not in warning
    assert resolve_stale(ctx, "7", fresh, seams)["edited"] == 0
    assert (len(thread.posted), len(thread.edits)) == (1, 1)


def test_a_stale_warning_followed_by_more_commits_leaves_exactly_one_updated_warning(tmp_path: Path) -> None:
    ctx, thread = pr_context(tmp_path), FakeThread()
    first = post_stale_signal(ctx, stale_check("a" * 40, "b" * 40), thread.seams(), pr_number="7")
    second = post_stale_signal(ctx, stale_check("a" * 40, "d" * 40), thread.seams(), pr_number="7")
    third = post_stale_signal(ctx, stale_check("a" * 40, "d" * 40), thread.seams(), pr_number="7")

    assert (first["posted"], second["edited"], third["edited"]) == (True, 1, 0)
    (warning,) = thread.stale_bodies()
    assert "`ddddddd`" in warning and "`bbbbbbb`" not in warning
    assert "The flow reruns Stage 5 for this head on its next run" in warning
    assert (len(thread.posted), len(thread.edits)) == (1, 1)


def test_a_legacy_per_pair_warning_is_edited_in_place_not_duplicated(tmp_path: Path) -> None:
    legacy = f"## ⚠️ Calibration report is stale\n\nold\n\n<!-- holon-calibration-stale:{'a' * 40}:{'b' * 40} -->"
    ctx, thread = pr_context(tmp_path), FakeThread("unrelated review comment", legacy)

    signal = post_stale_signal(ctx, stale_check("a" * 40, "e" * 40), thread.seams(), pr_number="7")

    assert (signal["posted"], signal["edited"]) == (False, 1)
    assert thread.posted == []
    assert [record.id for record in thread.records if STALE_MARKER in record.body] == ["IC_1"]
    assert thread.records[0].body == "unrelated review comment"


def test_a_current_report_posts_no_warning(calibrated: SimpleNamespace) -> None:
    ctx, result, calls, run = calibrated_flow(calibrated)
    approve_pr(ctx)
    seams, thread = recording_thread()
    assert rerun_calibration(ctx, result, run, seams=seams) is result
    assert review_calibration_freshness(ctx, "7", run_stage=run, seams=seams) is None
    assert (thread.posted, thread.edits, len(calls)) == ([], [], 1)


def test_a_current_report_supersedes_a_leftover_warning(calibrated: SimpleNamespace) -> None:
    ctx, _, _, run = calibrated_flow(calibrated)
    thread = FakeThread(stale_body(StaleRange("a" * 40, "b" * 40, "an earlier flow")))
    evaluated = report_staleness(ctx).evaluated_sha

    assert review_calibration_freshness(ctx, "7", run_stage=run, seams=thread.seams()) is None

    (warning,) = thread.stale_bodies()
    assert f"{SUPERSEDED_MARKER_PREFIX}{evaluated} -->" in warning
    assert thread.posted == []


@pytest.mark.parametrize("trigger_site", ["review gate", "pipeline rerun"])
def test_a_leftover_warning_on_a_covered_pr258_shaped_head_is_superseded_and_nothing_is_posted(
    pr258_repos: SimpleNamespace, trigger_site: str
) -> None:
    history = pr258_repos.history
    ctx = standalone_context(pr258_repos)
    thread = FakeThread(stale_body(StaleRange(history.evaluated, history.merge, "an earlier flow")))
    if trigger_site == "review gate":
        assert review_calibration_freshness(ctx, "7", seams=thread.seams()) is None
    else:
        calibrated = StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS)
        calls: list[str] = []
        assert rerun_calibration(ctx, calibrated, stage_runner(calls), thread.seams(), pr_number="7") is calibrated
        assert calls == []
    (warning,) = thread.stale_bodies()
    assert "Stale calibration warning superseded" in warning
    assert f"{SUPERSEDED_MARKER_PREFIX}{history.evaluated} -->" in warning
    assert (thread.posted, len(thread.edits)) == ([], 1)


def test_flow_summary_renders_covering_and_unrefreshed_states() -> None:
    from sandbox_executor.cli.commands.flow import _calibrated_head_line

    covering = SimpleNamespace(payload={"calibrated_head": "a" * 40})
    assert _calibrated_head_line(covering) == f"calibration report covers execution head {'a' * 12}"
    unrefreshed = SimpleNamespace(payload={})
    assert _calibrated_head_line(unrefreshed) == "calibration report not refreshed for the current head"


def test_the_review_gate_reruns_without_posting_the_signal_inside_a_calibrated_flow(
    calibrated_moved: SimpleNamespace,
) -> None:
    ctx, _, calls, run = calibrated_flow(calibrated_moved)
    approve_pr(ctx)
    seams, thread = recording_thread()
    new_head = calibrated_moved.new_head
    record = review_calibration_freshness(ctx, "7", run_stage=run, seams=seams)
    assert (record["moved"], record["rerun"], record["rerun_status"]) == (True, True, "success")
    assert "rerun_error" not in record and "rerun_stale" not in record
    assert record["current_head"] == new_head
    assert record["signal"] == {"posted": False, "edited": 0, "reason": "the Stage 5 rerun replaced the stale report"}
    assert thread.stale_bodies() == [] and len(refreshed_notes(thread.posted)) == 1
    assert len(calls) == 2
    assert report_staleness(ctx).moved is False


def test_the_review_gate_records_a_failed_rerun_outcome(calibrated_moved: SimpleNamespace) -> None:
    ctx, _, _, _ = calibrated_flow(calibrated_moved)
    approve_pr(ctx)
    seams, thread = recording_thread()
    new_head = calibrated_moved.new_head
    posted_at_attempt: list[int] = []

    def failing(context: FlowContext) -> StageResult:
        posted_at_attempt.append(len(thread.posted))
        return StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.FAILED, error="calibrate blew up")

    record = review_calibration_freshness(ctx, "7", run_stage=failing, seams=seams)
    assert (record["rerun"], record["rerun_status"]) == (True, "failed")
    assert record["rerun_error"] == "calibrate blew up"
    assert posted_at_attempt == [0]
    assert (record["signal"]["posted"], len(thread.posted)) == (True, 1)
    (warning,) = thread.stale_bodies()
    assert "Calibration report is stale" in warning and new_head[:7] in warning


def test_the_review_gate_records_a_rerun_the_head_outran_as_stale(calibrated_moved: SimpleNamespace) -> None:
    ctx, _, _, _ = calibrated_flow(calibrated_moved)
    approve_pr(ctx)
    seams, thread = recording_thread()
    posted_at_attempt: list[int] = []

    def never_catching_up(context: FlowContext) -> StageResult:
        posted_at_attempt.append(len(thread.posted))
        return StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS)

    record = review_calibration_freshness(ctx, "7", run_stage=never_catching_up, seams=seams)
    assert (record["rerun"], record["rerun_status"], record["rerun_stale"]) == (True, "success", True)
    assert posted_at_attempt == [0, 0]
    assert (record["signal"]["posted"], len(stale_warnings(thread.posted)), len(thread.posted)) == (True, 1, 1)


def test_the_review_gate_signals_only_without_a_prior_stage5_result(calibrated_moved: SimpleNamespace) -> None:
    _, _, calls, _ = calibrated_flow(calibrated_moved)
    standalone = FlowContext(target_worktree=str(calibrated_moved.host), execution_branch=EXEC, agent="a", model="m")
    seams, posted = recording_seams()
    record = review_calibration_freshness(standalone, "7", run_stage=stage_runner(calls), seams=seams)
    assert (record["moved"], record["rerun"]) == (True, False)
    assert len(calls) == 1
    assert (record["signal"]["posted"], len(stale_warnings(posted))) == (True, 1)


def test_a_standalone_gate_on_a_code_push_warns_once_and_a_second_pass_does_not_post_again(
    calibrated_moved: SimpleNamespace,
) -> None:
    calibrated_flow(calibrated_moved)
    standalone = standalone_context(calibrated_moved)
    seams, thread = recording_thread()
    new_head = calibrated_moved.new_head
    first = review_calibration_freshness(standalone, "7", seams=seams)
    second = review_calibration_freshness(standalone, "7", seams=seams)
    assert (first["moved"], first["signal"]["posted"], first["rerun"]) == (True, True, False)
    assert "1 commit(s) after the evaluated commit changed the pull request's own change" in first["reason"]
    assert second["signal"] == {"posted": False, "edited": 0, "reason": "already current"}
    (warning,) = thread.stale_bodies()
    assert "Calibration report is stale" in warning and new_head[:7] in warning
    assert (len(thread.posted), len(thread.edits)) == (1, 0)


def test_the_gate_with_skip_calibrate_and_an_unfinished_stage5_result_still_warns(
    calibrated_moved: SimpleNamespace,
) -> None:
    calibrated_flow(calibrated_moved)
    ctx = standalone_context(calibrated_moved)
    # why: no Stage 5 may replace the report in this run
    ctx.skip_calibrate = True
    ctx.stage_results["calibrate"] = StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SKIPPED)
    seams, thread = recording_thread()
    record = review_calibration_freshness(ctx, "7", seams=seams)
    assert (record["signal"]["posted"], record["rerun"], len(stale_warnings(thread.posted))) == (True, False, 1)


def test_the_gate_on_a_pr258_shaped_head_posts_nothing(pr258_repos: SimpleNamespace) -> None:
    seams, thread = recording_thread()
    assert review_calibration_freshness(standalone_context(pr258_repos), "7", seams=seams) is None
    assert (thread.posted, thread.edits) == ([], [])


def test_the_gate_after_review_ledger_row_and_calibration_commits_posts_nothing(calibrated: SimpleNamespace) -> None:
    ctx, result, calls, run = calibrated_flow(calibrated)
    approve_pr(ctx)
    seams, thread = recording_thread()
    head = push_bookkeeping(calibrated)
    assert report_staleness(ctx).current_head == head
    assert review_calibration_freshness(ctx, "7", run_stage=run, seams=seams) is None
    assert review_calibration_freshness(standalone_context(calibrated), "7", seams=seams) is None
    assert rerun_calibration(ctx, result, run, seams=seams) is result
    assert (thread.posted, thread.edits, len(calls)) == ([], [], 1)


def test_the_gate_defers_to_the_stage5_still_ahead_in_this_run_and_posts_nothing(
    calibrated_moved: SimpleNamespace,
) -> None:
    _, _, calls, run = calibrated_flow(calibrated_moved)
    ctx = standalone_context(calibrated_moved)
    ctx.stage_results["calibrate"] = StageResult(
        stage=FlowStage.CALIBRATE, status=StageStatus.SKIPPED, payload={"review_restart": True}
    )
    seams, thread = recording_thread()
    record = review_calibration_freshness(ctx, "7", run_stage=run, seams=seams)
    assert (record["moved"], record["rerun"]) == (True, False)
    assert record["signal"] == {"posted": False, "edited": 0, "reason": "deferred to Stage 5 of this run"}
    assert (thread.posted, thread.edits, len(calls)) == ([], [], 1)


def test_the_review_gate_posts_the_refreshed_comment_without_an_approved_consensus(
    calibrated_moved: SimpleNamespace,
) -> None:
    ctx, _, calls, run = calibrated_flow(calibrated_moved)
    seams, posted = recording_seams()
    record = review_calibration_freshness(ctx, "7", run_stage=run, seams=seams)
    assert (record["moved"], record["rerun"]) == (True, True)
    assert len(calls) == 2
    assert (len(stale_warnings(posted)), len(refreshed_notes(posted))) == (0, 1)


def test_posting_gate_reports_calibration_freshness(tmp_path: Path) -> None:
    from sandbox_executor.flow import run_review_loop

    from tests.test_review_loop import _green_loop_patches, _loop_ctx

    sentinel = {"moved": True, "rerun": False}
    stack, _, _ = _green_loop_patches()
    with (
        stack,
        patch("sandbox_executor.flow.review.loop.review_calibration_freshness", return_value=sentinel) as gate,
    ):
        res = run_review_loop(_loop_ctx(tmp_path, dry_run=True), target="feature/x", dry_run=True, max_iterations=1)

    gate.assert_called_once()
    assert gate.call_args.args[1] is None
    assert res["calibration_freshness"] is sentinel


def test_the_review_gate_is_silent_without_a_report_or_a_pr(repos: SimpleNamespace) -> None:
    ctx = FlowContext(target_worktree=str(repos.host), execution_branch=EXEC, agent="a", model="m")
    seams, posted = recording_seams()
    assert review_calibration_freshness(ctx, "7", seams=seams) is None
    assert review_calibration_freshness(ctx, None, seams=seams) is None
    assert posted == []


def test_both_trigger_sites_firing_on_one_move_cause_exactly_one_rerun(calibrated_rerun: SimpleNamespace) -> None:
    ctx, calls, thread = calibrated_rerun.ctx, calibrated_rerun.calls, calibrated_rerun.thread
    run = stage_runner(calls, thread)
    assert len(calls) == 2
    assert review_calibration_freshness(ctx, "7", run_stage=run, seams=thread.seams()) is None
    assert len(calls) == 2
    assert (len(stale_warnings(thread.posted)), len(refreshed_notes(thread.posted)), len(thread.posted)) == (0, 1, 1)


def test_the_pipeline_passes_the_stage5_result_through_the_rerun_trigger(tmp_path: Path) -> None:
    from sandbox_executor.flow.pipeline import PipelineEngine

    ctx = FlowContext(target_worktree=str(tmp_path), execution_branch=EXEC, agent="a", model="m")
    stage_result = StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS)
    refreshed = StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS, payload={"refreshed": True})
    engine = PipelineEngine(ctx, from_stage="calibrate")
    with (
        patch.object(engine, "execute_stage", return_value=stage_result),
        patch("sandbox_executor.flow.pipeline.rerun_calibration", return_value=refreshed) as trigger,
        patch("sandbox_executor.flow.pipeline.pre_run_guard_error", return_value=None),
        patch.object(engine, "save_checkpoint"),
        patch("sandbox_executor.flow.pipeline.validate_stage_ledger"),
    ):
        engine.run()
    trigger.assert_called_once()
    assert trigger.call_args.args[1] is stage_result
    assert ctx.stage_results["calibrate"] is refreshed


def test_marked_comment_never_raises_on_a_malformed_review_payload(tmp_path: Path) -> None:
    ctx = FlowContext(target_worktree=str(tmp_path), execution_branch=EXEC, agent="a", model="m")
    # why: a non-dict consensus makes approval_pr_number raise AttributeError
    ctx.stage_results["review"] = StageResult(
        stage=FlowStage.REVIEW, status=StageStatus.HALTED_FOR_HUMAN, payload={"consensus": "approved"}
    )
    seams, posted = recording_seams()

    signal = post_stale_signal(ctx, HeadMoveCheck(True, "a" * 40, "b" * 40, "moved"), seams, pr_number=None)

    assert signal == {"posted": False, "edited": 0, "reason": "comment posting failed"}
    assert posted == []


def test_the_review_gate_stores_the_refreshed_stage5_result_for_the_checkpoint(tmp_path: Path) -> None:
    from sandbox_executor.flow import load_checkpoint, save_checkpoint

    ctx = FlowContext(target_worktree=str(tmp_path), execution_branch=EXEC, agent="a", model="m")
    superseded, final = "a" * 40, "b" * 40
    ctx.stage_results["calibrate"] = StageResult(
        stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS, payload={"calibrated_head": superseded}
    )

    def rerun(_ctx: FlowContext) -> StageResult:
        return StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS, payload={"calibrated_head": final})

    moved = HeadMoveCheck(True, superseded, final, "a resolver push")
    checks = [moved, moved, HeadMoveCheck(False, final, final)]
    seams, _ = recording_seams()
    with patch("sandbox_executor.flow.calibrate_rerun.report_staleness", side_effect=checks):
        record = review_calibration_freshness(ctx, "7", run_stage=rerun, seams=seams)

    assert (record["rerun"], record["rerun_status"]) == (True, "success")
    assert ctx.stage_results["calibrate"].payload["calibrated_head"] == final
    saved = load_checkpoint(save_checkpoint(ctx, str(tmp_path / "checkpoint.json")))
    assert saved.context.stage_results["calibrate"].payload["calibrated_head"] == final
