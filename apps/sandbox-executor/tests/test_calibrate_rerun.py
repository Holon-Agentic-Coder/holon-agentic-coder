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
    STALE_MARKER_FAMILY,
    SUPERSEDED_MARKER_PREFIX,
    StaleRange,
    WarningSeams,
    resolve_stale,
    stale_body,
)
from sandbox_executor.flow.github.approval_invalidation import CommentRecord
from sandbox_executor.flow.review.conflict_merge import _merge_target_ledger_only
from sandbox_executor.flow.stages.calibrate import _supersede_stale_warning, run_calibrate_stage

from tests.repo_templates import copy_template, shared_template
from tests.test_calibrate_sync import CALIBRATED, EXEC, commit_file, git, make_repos

PLAN_ID = "P-2-y"
CAL_SUBJECT = f"chore(calibration): add calibration report for {PLAN_ID}"
CAL_REPORT_REL = f"plans/I-1-x/{PLAN_ID}_calibration.md"


def commit_calibration(
    repo: Path, rel: str = CAL_REPORT_REL, subject: str = CAL_SUBJECT, text: str = "report\n"
) -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    git(repo, "add", rel)
    git(repo, "commit", "-qm", subject)
    return git(repo, "rev-parse", "HEAD")


LEDGER_REL = "holon-knowledge/ledger/executions.jsonl"
TARGET_REF = "refs/remotes/origin/main"


@pytest.fixture(scope="session")
def rerun_repos_template(tmp_path_factory: pytest.TempPathFactory, hermetic_git_config: str) -> SimpleNamespace:
    root, built = shared_template(tmp_path_factory, "calibrate-rerun-template", make_repos)
    return SimpleNamespace(root=root, repos=built)


def own_copy(template: SimpleNamespace, target: Path) -> SimpleNamespace:
    copy_template(template.root, target)
    built = vars(template.repos)
    return SimpleNamespace(**{name: target / path.relative_to(template.root) for name, path in built.items()})


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
    template = calibrated_repos_template
    own = own_copy(template, tmp_path)
    # why: one copy of both, so the context still holds this very result
    ctx, result = copy.deepcopy(template.first_run)
    ctx.target_worktree = str(own.host)
    assert str(template.root) not in repr((ctx, result)), "the first run recorded a path of the shared template"
    own.first_run = (ctx, result)
    return own


def ledger_row(repo: Path, label: str, subject: str = "chore(review): record review rows") -> str:
    path = repo / LEDGER_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"execution_id": label}) + "\n")
    git(repo, "add", LEDGER_REL)
    git(repo, "commit", "-qm", subject)
    return git(repo, "rev-parse", "HEAD")


def advance_main(repos: SimpleNamespace, name: str, text: str = "y\n") -> None:
    other = repos.other
    git(other, "checkout", "-q", "main")
    git(other, "pull", "-q", "--ff-only", "origin", "main")
    commit_file(other, name, text)
    ledger_row(other, f"E-main-{name}", subject=f"chore: record {name} on main")
    git(other, "push", "-q", "origin", "main")
    git(other, "checkout", "-q", EXEC)
    git(other, "fetch", "-q", "origin")


def merge_main(repos: SimpleNamespace, ledger_only: bool = False) -> str:
    outcome = _merge_target_ledger_only(str(repos.other), TARGET_REF, "Merge main into the execution branch")
    assert (outcome.merged, outcome.ledger_only) == (True, ledger_only), outcome
    return git(repos.other, "rev-parse", "HEAD")


# why: reproduces the history of PR #258: a report on E, then a ledger-only merge of main and bookkeeping commits
def pr258_history(repos: SimpleNamespace) -> SimpleNamespace:
    other = repos.other
    evaluated = commit_file(other, "feature.py", "x = 1\n")
    commit_calibration(other, text=f"# Calibration Report for {PLAN_ID}\n\n- **Evaluated Commit SHA:** {evaluated}\n")
    advance_main(repos, "main-only.txt")
    # why: both sides append to the ledger, so the merge conflicts there and nowhere else
    ledger_row(other, "E-pr")
    merge = merge_main(repos, ledger_only=True)
    ledger_row(other, "E-review")
    head = commit_calibration(other, rel="holon-knowledge/kb/k.json", subject="chore(calibration): record knowledge")
    git(other, "push", "-q", "origin", EXEC)
    return SimpleNamespace(evaluated=evaluated, merge=merge, head=head)


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


class FakeThread:
    def __init__(self, *bodies: str) -> None:
        self.records = [CommentRecord(f"IC_{n}", body) for n, body in enumerate(bodies)]
        self.posted: list[str] = []
        self.edits: list[tuple[str, str]] = []

    def comments(self, pr_number: str) -> list[CommentRecord]:
        return list(self.records)

    def poster(self, pr_number: str, body: str) -> bool:
        self.posted.append(body)
        self.records.append(CommentRecord(f"IC_{len(self.records)}", body))
        return True

    def editor(self, comment_id: str, body: str) -> bool:
        index = next((i for i, record in enumerate(self.records) if comment_id and record.id == comment_id), None)
        if index is None:
            return False
        self.edits.append((comment_id, body))
        self.records[index] = CommentRecord(comment_id, body)
        return True

    def seams(self) -> RerunSeams:
        return RerunSeams(comments=self.comments, poster=self.poster, editor=self.editor)

    def stale_bodies(self) -> list[str]:
        return [record.body for record in self.records if STALE_MARKER_FAMILY in record.body]


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
    calibrated: SimpleNamespace,
) -> None:
    ctx, result, calls, run = calibrated_flow(calibrated)
    ledger_before = exec_ledger(calibrated)
    new_head = move_head(calibrated)
    refreshed = rerun_calibration(ctx, result, run)
    assert (len(calls), refreshed.status) == (2, StageStatus.SUCCESS)
    assert refreshed is not result
    assert refreshed.payload["calibrated_head"] == new_head
    assert refreshed.payload.get("calibration_stale") is None
    assert "Stage 5 rerun refreshed the report to" in refreshed.payload["ci_verdict"]["report"]
    report = git(calibrated.host, "show", f"{EXEC}:{CAL_REPORT_REL}")
    assert parse_evaluated_commit_sha(report) == new_head
    ledger_after = exec_ledger(calibrated)
    assert ledger_after.startswith(ledger_before)
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


def test_a_rerun_does_not_loop_on_its_own_calibration_commits(calibrated: SimpleNamespace) -> None:
    ctx, result, calls, run = calibrated_flow(calibrated)
    move_head(calibrated)
    refreshed = rerun_calibration(ctx, result, run)
    assert len(calls) == 2
    assert report_staleness(ctx).moved is False
    assert rerun_calibration(ctx, refreshed, run) is refreshed
    assert len(calls) == 2


def test_a_head_that_outruns_both_attempts_marks_the_payload_stale(calibrated: SimpleNamespace) -> None:
    from sandbox_executor.cli.commands.flow import _calibrated_head_line

    ctx, result, _, _ = calibrated_flow(calibrated)
    new_head = move_head(calibrated)
    attempts: list[str] = []

    def never_catching_up(context: FlowContext) -> StageResult:
        attempts.append("stage")
        return result

    stale = rerun_calibration(ctx, result, never_catching_up)
    assert len(attempts) == 2
    assert stale.payload["calibration_stale"] is True
    assert new_head[:12] in stale.payload["stale_reason"]
    line = _calibrated_head_line(stale)
    assert line.startswith("calibration report is STALE")
    assert "not ready to merge" in line and "covers" in line


def test_the_stale_signal_is_superseded_by_the_rerun_and_no_second_comment_is_posted(
    calibrated: SimpleNamespace,
) -> None:
    # why: Stage 5 here cannot reach the thread (its own edit goes nowhere), so the trigger's explicit number supersedes
    ctx, result, _, run = calibrated_flow(calibrated)
    approve_pr(ctx)
    thread = FakeThread(stale_body(StaleRange("a" * 40, "b" * 40, "an earlier flow")))
    seams = thread.seams()
    move_head(calibrated)
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
    calibrated: SimpleNamespace,
) -> None:
    thread = FakeThread()
    ctx, _, _, _ = calibrated_flow(calibrated, thread)
    approve_pr(ctx)
    new_head = move_head(calibrated)
    post_stale_signal(ctx, report_staleness(ctx), thread.seams(), pr_number="7")
    assert len(stale_warnings(thread.stale_bodies())) == 1
    seen_at_ci_check: list[str] = []

    def final_head_ci(context: FlowContext, repo_dir: str, branch: str) -> dict[str, str]:
        (warning,) = thread.stale_bodies()
        seen_at_ci_check.append(warning)
        assert "Stale calibration warning superseded" in warning and "Calibration report is stale" not in warning
        assert f"{SUPERSEDED_MARKER_PREFIX}{new_head} -->" in warning and f"`{new_head[:7]}`" in warning
        assert parse_evaluated_commit_sha(git(calibrated.origin, "show", f"{EXEC}:{CAL_REPORT_REL}")) == new_head
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


def test_a_failed_publish_leaves_the_warning_open_and_records_no_supersession(calibrated: SimpleNamespace) -> None:
    thread = FakeThread()
    ctx, _, _, _ = calibrated_flow(calibrated, thread)
    approve_pr(ctx)
    move_head(calibrated)
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


def test_a_rerun_adds_the_refreshed_note_only_when_no_warning_was_superseded(calibrated: SimpleNamespace) -> None:
    thread = FakeThread()
    ctx, result, calls, run = calibrated_flow(calibrated, thread)
    approve_pr(ctx)
    first_head = move_head(calibrated, "first-fix.txt")
    first = rerun_calibration(ctx, result, run, seams=thread.seams())
    assert first.payload["stale_warning"]["edited"] == 0
    (note,) = thread.posted
    assert "Calibration refreshed" in note and first_head[:7] in note

    second_head = move_head(calibrated, "second-fix.txt")
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


def test_a_successful_rerun_on_a_code_push_posts_no_stale_warning(calibrated: SimpleNamespace) -> None:
    ctx, result, calls, run = calibrated_flow(calibrated)
    approve_pr(ctx)
    seams, thread = recording_thread()
    new_head = move_head(calibrated)
    refreshed = rerun_calibration(ctx, result, run, seams=seams)
    assert (len(calls), refreshed.status) == (2, StageStatus.SUCCESS)
    assert thread.stale_bodies() == [] and thread.edits == []
    (note,) = thread.posted
    assert "Calibration refreshed" in note and new_head[:7] in note


def test_a_still_stale_head_never_duplicates_the_stale_comment(calibrated: SimpleNamespace) -> None:
    ctx, result, _, _ = calibrated_flow(calibrated)
    approve_pr(ctx)
    seams, thread = recording_thread()
    new_head = move_head(calibrated)
    posted_at_attempt: list[int] = []

    def never_catching_up(context: FlowContext) -> StageResult:
        posted_at_attempt.append(len(thread.posted))
        return result

    rerun_calibration(ctx, result, never_catching_up, seams=seams)
    assert posted_at_attempt == [0, 0]
    (warning,) = thread.stale_bodies()
    assert "Calibration report is stale" in warning and new_head[:7] in warning
    rerun_calibration(ctx, result, never_catching_up, seams=seams)
    assert (len(thread.posted), len(thread.edits)) == (1, 0)


def test_a_failed_stage5_result_with_a_stale_old_report_posts_the_warning_once(calibrated: SimpleNamespace) -> None:
    ctx, _, calls, run = calibrated_flow(calibrated)
    approve_pr(ctx)
    seams, thread = recording_thread()
    new_head = move_head(calibrated)
    failed = StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.FAILED, error="calibrate blew up")
    assert rerun_calibration(ctx, failed, run, seams=seams) is failed
    assert rerun_calibration(ctx, failed, run, seams=seams) is failed
    assert len(calls) == 1
    (warning,) = thread.stale_bodies()
    assert "Calibration report is stale" in warning and new_head[:7] in warning
    assert (len(thread.posted), len(thread.edits)) == (1, 0)


def test_a_failed_stage5_result_with_a_current_report_or_a_dry_run_posts_nothing(calibrated: SimpleNamespace) -> None:
    ctx, _, calls, run = calibrated_flow(calibrated)
    approve_pr(ctx)
    seams, thread = recording_thread()
    failed = StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.FAILED, error="calibrate blew up")
    assert rerun_calibration(ctx, failed, run, seams=seams) is failed
    move_head(calibrated)
    ctx.dry_run = True
    assert rerun_calibration(ctx, failed, run, seams=seams) is failed
    assert (thread.posted, thread.edits, len(calls)) == ([], [], 1)


def test_a_review_restart_result_posts_nothing_and_is_not_checked(calibrated: SimpleNamespace) -> None:
    ctx, result, calls, run = calibrated_flow(calibrated)
    approve_pr(ctx)
    thread, head_lookups = FakeThread(), []
    seams = RerunSeams(
        head_resolver=lambda context: head_lookups.append("head") or "",
        comments=thread.comments,
        poster=thread.poster,
        editor=thread.editor,
    )
    move_head(calibrated)
    restart = StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SKIPPED, payload={"review_restart": True})
    assert rerun_calibration(ctx, restart, run, seams=seams) is restart
    assert (thread.posted, thread.edits, head_lookups, len(calls)) == ([], [], [], 1)
    assert rerun_calibration(ctx, result, lambda context: restart, seams=thread.seams()) is restart
    assert (thread.posted, thread.edits) == ([], [])


def test_a_rerun_that_fails_after_publishing_a_covering_report_posts_nothing(calibrated: SimpleNamespace) -> None:
    ctx, result, calls, run = calibrated_flow(calibrated)
    approve_pr(ctx)
    seams, thread = recording_thread()
    move_head(calibrated)

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
    repos: SimpleNamespace, trigger_site: str
) -> None:
    history = pr258_history(repos)
    ctx = standalone_context(repos)
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
    calibrated: SimpleNamespace,
) -> None:
    ctx, _, calls, run = calibrated_flow(calibrated)
    approve_pr(ctx)
    seams, thread = recording_thread()
    new_head = move_head(calibrated)
    record = review_calibration_freshness(ctx, "7", run_stage=run, seams=seams)
    assert (record["moved"], record["rerun"], record["rerun_status"]) == (True, True, "success")
    assert "rerun_error" not in record and "rerun_stale" not in record
    assert record["current_head"] == new_head
    assert record["signal"] == {"posted": False, "edited": 0, "reason": "the Stage 5 rerun replaced the stale report"}
    assert thread.stale_bodies() == [] and len(refreshed_notes(thread.posted)) == 1
    assert len(calls) == 2
    assert report_staleness(ctx).moved is False


def test_the_review_gate_records_a_failed_rerun_outcome(calibrated: SimpleNamespace) -> None:
    ctx, _, _, _ = calibrated_flow(calibrated)
    approve_pr(ctx)
    seams, thread = recording_thread()
    new_head = move_head(calibrated)
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


def test_the_review_gate_records_a_rerun_the_head_outran_as_stale(calibrated: SimpleNamespace) -> None:
    ctx, _, _, _ = calibrated_flow(calibrated)
    approve_pr(ctx)
    seams, thread = recording_thread()
    move_head(calibrated)
    posted_at_attempt: list[int] = []

    def never_catching_up(context: FlowContext) -> StageResult:
        posted_at_attempt.append(len(thread.posted))
        return StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS)

    record = review_calibration_freshness(ctx, "7", run_stage=never_catching_up, seams=seams)
    assert (record["rerun"], record["rerun_status"], record["rerun_stale"]) == (True, "success", True)
    assert posted_at_attempt == [0, 0]
    assert (record["signal"]["posted"], len(stale_warnings(thread.posted)), len(thread.posted)) == (True, 1, 1)


def test_the_review_gate_signals_only_without_a_prior_stage5_result(calibrated: SimpleNamespace) -> None:
    _, _, calls, _ = calibrated_flow(calibrated)
    standalone = FlowContext(target_worktree=str(calibrated.host), execution_branch=EXEC, agent="a", model="m")
    seams, posted = recording_seams()
    move_head(calibrated)
    record = review_calibration_freshness(standalone, "7", run_stage=stage_runner(calls), seams=seams)
    assert (record["moved"], record["rerun"]) == (True, False)
    assert len(calls) == 1
    assert (record["signal"]["posted"], len(stale_warnings(posted))) == (True, 1)


def test_a_standalone_gate_on_a_code_push_warns_once_and_a_second_pass_does_not_post_again(
    calibrated: SimpleNamespace,
) -> None:
    calibrated_flow(calibrated)
    standalone = standalone_context(calibrated)
    seams, thread = recording_thread()
    new_head = move_head(calibrated)
    first = review_calibration_freshness(standalone, "7", seams=seams)
    second = review_calibration_freshness(standalone, "7", seams=seams)
    assert (first["moved"], first["signal"]["posted"], first["rerun"]) == (True, True, False)
    assert "1 commit(s) after the evaluated commit changed the pull request's own change" in first["reason"]
    assert second["signal"] == {"posted": False, "edited": 0, "reason": "already current"}
    (warning,) = thread.stale_bodies()
    assert "Calibration report is stale" in warning and new_head[:7] in warning
    assert (len(thread.posted), len(thread.edits)) == (1, 0)


def test_the_gate_with_skip_calibrate_and_an_unfinished_stage5_result_still_warns(calibrated: SimpleNamespace) -> None:
    calibrated_flow(calibrated)
    ctx = standalone_context(calibrated)
    # why: no Stage 5 may replace the report in this run
    ctx.skip_calibrate = True
    ctx.stage_results["calibrate"] = StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SKIPPED)
    seams, thread = recording_thread()
    move_head(calibrated)
    record = review_calibration_freshness(ctx, "7", seams=seams)
    assert (record["signal"]["posted"], record["rerun"], len(stale_warnings(thread.posted))) == (True, False, 1)


def test_the_gate_on_a_pr258_shaped_head_posts_nothing(repos: SimpleNamespace) -> None:
    pr258_history(repos)
    seams, thread = recording_thread()
    assert review_calibration_freshness(standalone_context(repos), "7", seams=seams) is None
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


def test_the_gate_defers_to_the_stage5_still_ahead_in_this_run_and_posts_nothing(calibrated: SimpleNamespace) -> None:
    _, _, calls, run = calibrated_flow(calibrated)
    ctx = standalone_context(calibrated)
    ctx.stage_results["calibrate"] = StageResult(
        stage=FlowStage.CALIBRATE, status=StageStatus.SKIPPED, payload={"review_restart": True}
    )
    seams, thread = recording_thread()
    move_head(calibrated)
    record = review_calibration_freshness(ctx, "7", run_stage=run, seams=seams)
    assert (record["moved"], record["rerun"]) == (True, False)
    assert record["signal"] == {"posted": False, "edited": 0, "reason": "deferred to Stage 5 of this run"}
    assert (thread.posted, thread.edits, len(calls)) == ([], [], 1)


def test_the_review_gate_posts_the_refreshed_comment_without_an_approved_consensus(calibrated: SimpleNamespace) -> None:
    ctx, _, calls, run = calibrated_flow(calibrated)
    seams, posted = recording_seams()
    move_head(calibrated)
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


def test_both_trigger_sites_firing_on_one_move_cause_exactly_one_rerun(calibrated: SimpleNamespace) -> None:
    ctx, result, calls, run = calibrated_flow(calibrated)
    approve_pr(ctx)
    seams, posted = recording_seams()
    move_head(calibrated)
    rerun_calibration(ctx, result, run, seams=seams)
    assert len(calls) == 2
    assert review_calibration_freshness(ctx, "7", run_stage=run, seams=seams) is None
    assert len(calls) == 2
    assert (len(stale_warnings(posted)), len(refreshed_notes(posted)), len(posted)) == (0, 1, 1)


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
