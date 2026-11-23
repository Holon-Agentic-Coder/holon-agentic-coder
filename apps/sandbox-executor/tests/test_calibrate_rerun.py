"""Stage 5 rerun on a moved head: predicate, bounded rerun, idempotence and the stale signal. Hermetic: real commits
in temp repos, no network, no Docker, no model call."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sandbox_executor.calibration import parse_evaluated_commit_sha
from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.calibrate_rerun import (
    HeadMoveCheck,
    RerunSeams,
    head_move_since_calibration,
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
from sandbox_executor.flow.review.approval_fingerprint import approval_fingerprint_of
from sandbox_executor.flow.review.conflict_merge import _merge_target_ledger_only
from sandbox_executor.flow.stages.calibrate import run_calibrate_stage

from tests.test_calibrate_sync import CALIBRATED, EXEC, commit_file, git, make_repos

PLAN_ID = "P-2-y"
CAL_SUBJECT = f"chore(calibration): add calibration report for {PLAN_ID}"
CAL_REPORT_REL = f"plans/I-1-x/{PLAN_ID}_calibration.md"


def commit_calibration(
    repo: Path, rel: str = CAL_REPORT_REL, subject: str = CAL_SUBJECT, text: str = "report\n"
) -> str:
    """A commit in Stage 5's own shape: a calibration subject touching only calibration paths."""
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    git(repo, "add", rel)
    git(repo, "commit", "-qm", subject)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "predicate"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    commit_file(r, "base.txt")
    return r


def test_equal_heads_are_unmoved(repo: Path) -> None:
    head = git(repo, "rev-parse", "HEAD")
    check = head_move_since_calibration(str(repo), head, head)
    assert check == HeadMoveCheck(False, head, head, "the head is the evaluated commit")


def test_an_abbreviated_evaluated_sha_still_matches_the_head(repo: Path) -> None:
    head = git(repo, "rev-parse", "HEAD")
    assert head_move_since_calibration(str(repo), head[:12], head).moved is False


def test_calibration_commits_alone_are_not_a_move(repo: Path) -> None:
    evaluated = git(repo, "rev-parse", "HEAD")
    commit_calibration(repo)
    commit_calibration(repo, rel="holon-knowledge/ledger/executions.jsonl", subject="chore(calibration): record rows")
    head = git(repo, "rev-parse", "HEAD")
    check = head_move_since_calibration(str(repo), evaluated, head)
    assert check.moved is False
    assert check.reason == "only calibration and ledger commits landed after the evaluated commit"


def test_a_foreign_commit_between_the_calibration_commits_is_a_move(repo: Path) -> None:
    evaluated = git(repo, "rev-parse", "HEAD")
    commit_calibration(repo)
    commit_file(repo, "resolver-fix.txt")
    head = git(repo, "rev-parse", "HEAD")
    check = head_move_since_calibration(str(repo), evaluated, head)
    assert check.moved is True
    assert check.reason.startswith(
        "1 commit(s) landed after the evaluated commit"
    )  # the calibration commit is not counted


def test_a_calibration_subject_on_a_foreign_path_is_a_move(repo: Path) -> None:
    evaluated = git(repo, "rev-parse", "HEAD")
    commit_calibration(repo, rel="src/sneaky.py")
    assert head_move_since_calibration(str(repo), evaluated, git(repo, "rev-parse", "HEAD")).moved is True


def test_a_merge_is_a_move_when_the_pr_own_change_cannot_be_shown_unchanged(repo: Path) -> None:
    evaluated = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-qb", "side")
    commit_calibration(repo)
    git(repo, "checkout", "-q", "main")
    commit_calibration(repo, rel="holon-knowledge/ledger/executions.jsonl", subject="chore(calibration): record rows")
    git(repo, "merge", "-q", "--no-ff", "-m", "chore(calibration): merge side", "side")
    check = head_move_since_calibration(str(repo), evaluated, git(repo, "rev-parse", "HEAD"))  # no target ref
    assert check.moved is True
    assert check.reason.startswith("1 commit(s) landed") and "could not be compared" in check.reason


def test_a_non_ancestor_evaluated_sha_is_a_move(repo: Path) -> None:
    git(repo, "checkout", "-qb", "rewritten")
    evaluated = commit_file(repo, "old-history.txt")
    git(repo, "checkout", "-q", "main")
    head = commit_file(repo, "new-history.txt")
    check = head_move_since_calibration(str(repo), evaluated, head)
    assert check.moved is True
    assert "not an ancestor" in check.reason


def test_an_unknown_sha_is_unmoved_with_the_gap_named(repo: Path) -> None:
    head = git(repo, "rev-parse", "HEAD")
    check = head_move_since_calibration(str(repo), "", head)
    assert (check.moved, "unknown" in check.reason) == (False, True)


# --- Bounded rerun and stale signal, on the flow-shaped harness (bare origin, host worktree, pushing clone). ---

LEDGER_REL = "holon-knowledge/ledger/executions.jsonl"
TARGET_REF = "refs/remotes/origin/main"


@pytest.fixture
def repos(tmp_path: Path) -> SimpleNamespace:
    return make_repos(tmp_path)


def ledger_row(repo: Path, label: str, subject: str = "chore(review): record review rows") -> str:
    """A ledger-only commit, as the review loop and the engine add: one appended row, no code."""
    path = repo / LEDGER_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"execution_id": label}) + "\n")
    git(repo, "add", LEDGER_REL)
    git(repo, "commit", "-qm", subject)
    return git(repo, "rev-parse", "HEAD")


def advance_main(repos: SimpleNamespace, name: str, text: str = "y\n") -> None:
    """Commit ``name`` and one ledger row on origin's main from the second clone, leaving it on the execution branch."""
    other = repos.other
    git(other, "checkout", "-q", "main")
    git(other, "pull", "-q", "--ff-only", "origin", "main")
    commit_file(other, name, text)
    ledger_row(other, f"E-main-{name}", subject=f"chore: record {name} on main")
    git(other, "push", "-q", "origin", "main")
    git(other, "checkout", "-q", EXEC)
    git(other, "fetch", "-q", "origin")


def merge_main(repos: SimpleNamespace, ledger_only: bool = False) -> str:
    """Merge origin/main into the second clone's execution branch the way the conflict gate does."""
    outcome = _merge_target_ledger_only(str(repos.other), TARGET_REF, "Merge main into the execution branch")
    assert (outcome.merged, outcome.ledger_only) == (True, ledger_only), outcome
    return git(repos.other, "rev-parse", "HEAD")


def pr258_history(repos: SimpleNamespace) -> SimpleNamespace:
    """The history of PR #258 on origin: a report on E, then a ledger-only merge of main and bookkeeping commits."""
    other = repos.other
    evaluated = commit_file(other, "feature.py", "x = 1\n")
    commit_calibration(other, text=f"# Calibration Report for {PLAN_ID}\n\n- **Evaluated Commit SHA:** {evaluated}\n")
    advance_main(repos, "main-only.txt")
    ledger_row(other, "E-pr")  # both sides now append to the ledger, so the merge conflicts there and nowhere else
    merge = merge_main(repos, ledger_only=True)
    ledger_row(other, "E-review")
    head = commit_calibration(other, rel="holon-knowledge/kb/k.json", subject="chore(calibration): record knowledge")
    git(other, "push", "-q", "origin", EXEC)
    return SimpleNamespace(evaluated=evaluated, merge=merge, head=head)


def test_the_pr_own_fingerprint_is_unchanged_across_a_pr258_shaped_history(repos: SimpleNamespace) -> None:
    """The hypothesis of the staleness rule: the approval fingerprint of E equals that of the merged head."""
    history = pr258_history(repos)
    other = str(repos.other)
    assert len(git(repos.other, "rev-list", "--parents", "-n", "1", history.merge).split()) == 3  # a real merge
    before = approval_fingerprint_of(other, history.evaluated, "main")
    assert before is not None
    assert approval_fingerprint_of(other, history.head, "main") == before
    advance_main(repos, "main-later.txt")  # the target ref moves past the merged commit
    assert approval_fingerprint_of(other, history.evaluated, "main") == before
    assert approval_fingerprint_of(other, history.head, "main") == before


def predicate(repos: SimpleNamespace, evaluated: str, target: str | None = "main") -> HeadMoveCheck:
    """The staleness verdict in the second clone, from ``evaluated`` to its checked-out execution head."""
    return head_move_since_calibration(str(repos.other), evaluated, git(repos.other, "rev-parse", "HEAD"), target)


def test_a_clean_merge_of_a_target_that_touched_other_files_is_unmoved(repos: SimpleNamespace) -> None:
    evaluated = commit_file(repos.other, "feature.py", "x = 1\n")
    advance_main(repos, "main-only.txt")
    merge_main(repos)
    check = predicate(repos, evaluated)
    assert check.moved is False
    assert (
        check.reason == "the pull request's own change is unchanged (1 commit(s) after the evaluated commit discounted)"
    )


def test_a_merge_with_ledger_only_conflicts_is_unmoved_and_never_counts_the_targets_commits(
    repos: SimpleNamespace,
) -> None:
    history = pr258_history(repos)
    in_range = git(repos.other, "rev-list", "--count", f"{history.evaluated}..{history.head}")
    check = predicate(repos, history.evaluated)
    assert check.moved is False
    # First-parent only: the report commit, the PR's ledger row, the merge, the review row and the knowledge commit.
    assert (in_range, check.reason) == (
        "7",
        "the pull request's own change is unchanged (5 commit(s) after the evaluated commit discounted)",
    )


def test_a_merge_that_brings_a_target_change_to_a_pr_file_is_a_move(repos: SimpleNamespace) -> None:
    lines = ["a", "b", "c", "d", "e", "f", "g", "h"]
    advance_main(repos, "shared.py", "\n".join(lines) + "\n")
    merge_main(repos)  # the execution branch now holds the shared file
    evaluated = commit_file(repos.other, "shared.py", "\n".join(["from the pr", *lines[1:]]) + "\n")
    advance_main(repos, "shared.py", "\n".join([*lines[:-1], "from main"]) + "\n")
    merge_main(repos)  # clean: the two edits do not overlap, but the PR's file is no longer what the report saw
    check = predicate(repos, evaluated)
    assert check.moved is True
    assert check.reason == "1 commit(s) after the evaluated commit changed the pull request's own change"


def test_a_review_ledger_row_commit_after_the_evaluated_commit_is_unmoved(repos: SimpleNamespace) -> None:
    evaluated = commit_file(repos.other, "feature.py")
    ledger_row(repos.other, "E-review")
    ledger_row(repos.other, "E-engine", subject="record the execution")  # path-based: no subject is required
    check = predicate(repos, evaluated, target=None)
    assert (check.moved, check.reason) == (
        False,
        "only calibration and ledger commits landed after the evaluated commit",
    )


def test_a_calibration_commit_after_the_evaluated_commit_is_unmoved(repos: SimpleNamespace) -> None:
    evaluated = commit_file(repos.other, "feature.py")
    commit_calibration(repos.other)
    assert predicate(repos, evaluated).moved is False


def test_a_code_push_is_a_move_and_the_reason_counts_exactly_one_commit(repos: SimpleNamespace) -> None:
    evaluated = commit_file(repos.other, "feature.py")
    commit_calibration(repos.other)
    ledger_row(repos.other, "E-review")
    commit_file(repos.other, "resolver-fix.py")
    commit_calibration(repos.other, rel="holon-knowledge/kb/k.json")
    check = predicate(repos, evaluated)
    assert check.moved is True
    assert check.reason == "1 commit(s) after the evaluated commit changed the pull request's own change"


def test_a_merge_with_no_target_branch_given_is_a_move(repos: SimpleNamespace) -> None:
    evaluated = commit_file(repos.other, "feature.py", "x = 1\n")
    advance_main(repos, "main-only.txt")
    merge_main(repos)
    check = predicate(repos, evaluated, target=None)
    assert check.moved is True
    assert "could not be compared" in check.reason


def test_report_staleness_reads_a_pr258_shaped_history_as_unmoved(repos: SimpleNamespace) -> None:
    history = pr258_history(repos)
    ctx = FlowContext(target_worktree=str(repos.host), execution_branch=EXEC, agent="a", model="m")
    ctx.intent_data = {"target_branch": "main"}
    check = report_staleness(ctx)  # fetches the execution branch and the target into the host worktree
    assert (check.moved, check.evaluated_sha, check.current_head) == (False, history.evaluated, history.head)
    assert "own change is unchanged" in check.reason


def realistic_calibrate(**kwargs: object) -> SimpleNamespace:
    """Stand in for run_calibrate in Stage 5's own commit shape: report plus ACT-CAL row, calibration subject."""
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
    return SimpleNamespace(calibrated_branch=CALIBRATED, to_dict=lambda: {"plan_id": PLAN_ID})


def stage_runner(calls: list[str]) -> object:
    """A run_calibrate_stage wrapper with the network-facing seams faked, recording each invocation."""

    def run(ctx: FlowContext) -> StageResult:
        calls.append("stage")
        with (
            patch("sandbox_executor.flow.stages.calibrate.check_and_resolve_stage_conflicts"),
            patch("sandbox_executor.flow.stages.calibrate.run_calibrate", side_effect=realistic_calibrate),
            patch("sandbox_executor.flow.stages.calibrate.check_final_head_ci", return_value={"state": "success"}),
        ):
            return run_calibrate_stage(ctx)

    return run


def calibrated_flow(repos: SimpleNamespace) -> tuple[FlowContext, StageResult, list[str], object]:
    """A context whose Stage 5 already ran once, with the stage-call recorder the rerun trigger reuses."""
    calls: list[str] = []
    run = stage_runner(calls)
    ctx = FlowContext(target_worktree=str(repos.host), execution_branch=EXEC, agent="a", model="m")
    result = run(ctx)
    assert result.status == StageStatus.SUCCESS
    return ctx, result, calls, run


def move_head(repos: SimpleNamespace, name: str = "resolver-fix.txt") -> str:
    """A resolver-style push on origin after calibration: the pull request head moves past the report."""
    git(repos.other, "fetch", "-q", "origin")
    git(repos.other, "reset", "-q", "--hard", f"origin/{EXEC}")
    sha = commit_file(repos.other, name)
    git(repos.other, "push", "-q", "origin", EXEC)
    return sha


def approve_pr(ctx: FlowContext, number: str = "7") -> None:
    """A Stage 4 result whose posted consensus approval names the PR, as approval_pr_number requires."""
    ctx.stage_results["review"] = StageResult(
        stage=FlowStage.REVIEW,
        status=StageStatus.HALTED_FOR_HUMAN,
        payload={"consensus": {"approved": True, "pr_number": number}, "posting": {"dual_posted": True}},
    )


class FakeThread:
    """An in-memory PR comment thread behind the records lister, poster and editor seams; no network."""

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


def test_a_head_move_after_calibration_reruns_stage5_and_the_report_names_the_new_head(
    repos: SimpleNamespace,
) -> None:
    ctx, result, calls, run = calibrated_flow(repos)
    ledger_before = exec_ledger(repos)
    new_head = move_head(repos)
    refreshed = rerun_calibration(ctx, result, run)
    assert (len(calls), refreshed.status) == (2, StageStatus.SUCCESS)
    assert refreshed is not result
    assert refreshed.payload["calibrated_head"] == new_head
    assert refreshed.payload.get("calibration_stale") is None
    assert "Stage 5 rerun refreshed the report to" in refreshed.payload["ci_verdict"]["report"]
    report = git(repos.host, "show", f"{EXEC}:{CAL_REPORT_REL}")
    assert parse_evaluated_commit_sha(report) == new_head
    ledger_after = exec_ledger(repos)
    assert ledger_after.startswith(ledger_before)  # rows appended, never rewritten
    act_cal = [line for line in ledger_after.splitlines() if '"ACT-CAL-' in line]
    assert len(act_cal) == 2  # exactly one ACT-CAL row per run


def test_an_unmoved_head_is_a_no_op_with_no_new_ledger_rows(repos: SimpleNamespace) -> None:
    ctx, result, calls, run = calibrated_flow(repos)
    refs_before, ledger_before = git(repos.host, "for-each-ref"), exec_ledger(repos)
    unchanged = rerun_calibration(ctx, result, run)
    assert unchanged is result
    assert len(calls) == 1
    assert git(repos.host, "for-each-ref") == refs_before
    assert exec_ledger(repos) == ledger_before


def test_a_rerun_does_not_loop_on_its_own_calibration_commits(repos: SimpleNamespace) -> None:
    ctx, result, calls, run = calibrated_flow(repos)
    move_head(repos)
    refreshed = rerun_calibration(ctx, result, run)
    assert len(calls) == 2
    assert report_staleness(ctx).moved is False
    assert rerun_calibration(ctx, refreshed, run) is refreshed
    assert len(calls) == 2


def test_a_head_that_outruns_both_attempts_marks_the_payload_stale(repos: SimpleNamespace) -> None:
    from sandbox_executor.cli.commands.flow import _calibrated_head_line

    ctx, result, _, _ = calibrated_flow(repos)
    new_head = move_head(repos)
    attempts: list[str] = []

    def never_catching_up(context: FlowContext) -> StageResult:
        attempts.append("stage")
        return result

    stale = rerun_calibration(ctx, result, never_catching_up)
    assert len(attempts) == 2  # MAX_RERUN_ATTEMPTS bounds the work
    assert stale.payload["calibration_stale"] is True
    assert new_head[:12] in stale.payload["stale_reason"]
    line = _calibrated_head_line(stale)
    assert line.startswith("calibration report is STALE")
    assert "not ready to merge" in line and "covers" in line


def test_the_stale_signal_is_superseded_by_the_rerun_and_the_refreshed_note_posts_once(repos: SimpleNamespace) -> None:
    ctx, result, _, run = calibrated_flow(repos)
    approve_pr(ctx)
    seams, thread = recording_thread()
    move_head(repos)
    refreshed = rerun_calibration(ctx, result, run, seams=seams)
    assert refreshed.status == StageStatus.SUCCESS
    stale_comments = [body for body in thread.posted if "Calibration report is stale" in body]
    cleared_comments = [body for body in thread.posted if "Calibration refreshed" in body]
    assert (len(stale_comments), len(cleared_comments)) == (1, 1)
    assert "do not merge on this report" in stale_comments[0]
    assert refreshed.payload["calibrated_head"][:7] in cleared_comments[0]
    (warning,) = thread.stale_bodies()
    assert "superseded" in warning and "do not merge" not in warning
    assert f"{SUPERSEDED_MARKER_PREFIX}{refreshed.payload['calibrated_head']} -->" in warning
    assert rerun_calibration(ctx, refreshed, run, seams=seams) is refreshed
    assert (len(thread.posted), len(thread.edits)) == (2, 1)  # nothing new on a repeated check


def test_a_still_stale_head_never_duplicates_the_stale_comment(repos: SimpleNamespace) -> None:
    ctx, result, _, _ = calibrated_flow(repos)
    approve_pr(ctx)
    seams, thread = recording_thread()
    new_head = move_head(repos)
    rerun_calibration(ctx, result, lambda context: result, seams=seams)
    (warning,) = thread.stale_bodies()
    assert "Calibration report is stale" in warning and new_head[:7] in warning
    assert len(thread.posted) == 1


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
    assert resolve_stale(ctx, "7", fresh, seams)["edited"] == 0  # idempotent
    assert (len(thread.posted), len(thread.edits)) == (1, 1)


def test_a_stale_warning_followed_by_more_commits_leaves_exactly_one_updated_warning(tmp_path: Path) -> None:
    ctx, thread = pr_context(tmp_path), FakeThread()
    first = post_stale_signal(ctx, stale_check("a" * 40, "b" * 40), thread.seams(), pr_number="7")
    second = post_stale_signal(ctx, stale_check("a" * 40, "d" * 40), thread.seams(), pr_number="7")
    third = post_stale_signal(ctx, stale_check("a" * 40, "d" * 40), thread.seams(), pr_number="7")

    assert (first["posted"], second["edited"], third["edited"]) == (True, 1, 0)
    (warning,) = thread.stale_bodies()
    assert "`ddddddd`" in warning and "`bbbbbbb`" not in warning
    assert "do not merge on this report" in warning
    assert (len(thread.posted), len(thread.edits)) == (1, 1)


def test_a_legacy_per_pair_warning_is_edited_in_place_not_duplicated(tmp_path: Path) -> None:
    legacy = f"## ⚠️ Calibration report is stale\n\nold\n\n<!-- holon-calibration-stale:{'a' * 40}:{'b' * 40} -->"
    ctx, thread = pr_context(tmp_path), FakeThread("unrelated review comment", legacy)

    signal = post_stale_signal(ctx, stale_check("a" * 40, "e" * 40), thread.seams(), pr_number="7")

    assert (signal["posted"], signal["edited"]) == (False, 1)
    assert thread.posted == []
    assert [record.id for record in thread.records if STALE_MARKER in record.body] == ["IC_1"]
    assert thread.records[0].body == "unrelated review comment"


def test_a_current_report_posts_no_warning(repos: SimpleNamespace) -> None:
    ctx, result, calls, run = calibrated_flow(repos)
    approve_pr(ctx)
    seams, thread = recording_thread()
    assert rerun_calibration(ctx, result, run, seams=seams) is result
    assert review_calibration_freshness(ctx, "7", run_stage=run, seams=seams) is None
    assert (thread.posted, thread.edits, len(calls)) == ([], [], 1)


def test_a_current_report_supersedes_a_leftover_warning(repos: SimpleNamespace) -> None:
    ctx, _, _, run = calibrated_flow(repos)
    thread = FakeThread(stale_body(StaleRange("a" * 40, "b" * 40, "an earlier flow")))
    evaluated = report_staleness(ctx).evaluated_sha

    assert review_calibration_freshness(ctx, "7", run_stage=run, seams=thread.seams()) is None

    (warning,) = thread.stale_bodies()
    assert f"{SUPERSEDED_MARKER_PREFIX}{evaluated} -->" in warning
    assert thread.posted == []


def test_flow_summary_renders_covering_and_unrefreshed_states() -> None:
    from sandbox_executor.cli.commands.flow import _calibrated_head_line

    covering = SimpleNamespace(payload={"calibrated_head": "a" * 40})
    assert _calibrated_head_line(covering) == f"calibration report covers execution head {'a' * 12}"
    unrefreshed = SimpleNamespace(payload={})
    assert _calibrated_head_line(unrefreshed) == "calibration report not refreshed for the current head"


def test_the_review_gate_posts_the_signal_and_reruns_inside_a_calibrated_flow(repos: SimpleNamespace) -> None:
    ctx, _, calls, run = calibrated_flow(repos)
    approve_pr(ctx)
    seams, _ = recording_seams()
    new_head = move_head(repos)
    record = review_calibration_freshness(ctx, "7", run_stage=run, seams=seams)
    assert (record["moved"], record["rerun"], record["rerun_status"]) == (True, True, "success")
    assert "rerun_error" not in record and "rerun_stale" not in record
    assert record["current_head"] == new_head
    assert record["signal"]["posted"] is True
    assert len(calls) == 2
    assert report_staleness(ctx).moved is False


def test_the_review_gate_records_a_failed_rerun_outcome(repos: SimpleNamespace) -> None:
    ctx, _, _, _ = calibrated_flow(repos)
    approve_pr(ctx)
    seams, _ = recording_seams()
    move_head(repos)

    def failing(context: FlowContext) -> StageResult:
        return StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.FAILED, error="calibrate blew up")

    record = review_calibration_freshness(ctx, "7", run_stage=failing, seams=seams)
    assert (record["rerun"], record["rerun_status"]) == (True, "failed")
    assert record["rerun_error"] == "calibrate blew up"


def test_the_review_gate_records_a_rerun_the_head_outran_as_stale(repos: SimpleNamespace) -> None:
    ctx, _, _, _ = calibrated_flow(repos)
    approve_pr(ctx)
    seams, _ = recording_seams()
    move_head(repos)

    def never_catching_up(context: FlowContext) -> StageResult:
        return StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SUCCESS)

    record = review_calibration_freshness(ctx, "7", run_stage=never_catching_up, seams=seams)
    assert (record["rerun"], record["rerun_status"], record["rerun_stale"]) == (True, "success", True)


def test_the_review_gate_signals_only_without_a_prior_stage5_result(repos: SimpleNamespace) -> None:
    _, _, calls, _ = calibrated_flow(repos)
    standalone = FlowContext(target_worktree=str(repos.host), execution_branch=EXEC, agent="a", model="m")
    seams, posted = recording_seams()
    move_head(repos)
    record = review_calibration_freshness(standalone, "7", run_stage=stage_runner(calls), seams=seams)
    assert (record["moved"], record["rerun"]) == (True, False)
    assert len(calls) == 1  # the standalone loop cannot run Stage 5; the signal still marks the PR
    assert len([body for body in posted if "Calibration report is stale" in body]) == 1


def test_the_review_gate_posts_the_refreshed_comment_without_an_approved_consensus(repos: SimpleNamespace) -> None:
    """The gate's explicit PR number reaches the refreshed comment even when no approval names the PR."""
    ctx, _, calls, run = calibrated_flow(repos)
    seams, posted = recording_seams()
    move_head(repos)
    record = review_calibration_freshness(ctx, "7", run_stage=run, seams=seams)
    assert (record["moved"], record["rerun"]) == (True, True)
    assert len(calls) == 2
    assert len([body for body in posted if "Calibration report is stale" in body]) == 1
    assert len([body for body in posted if "Calibration refreshed" in body]) == 1


def test_posting_gate_reports_calibration_freshness(tmp_path: Path) -> None:
    """The loop runs the calibration staleness gate after the posting decision and reports its record."""
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
    assert gate.call_args.args[1] is None  # no PR was created, so the gate gets no PR number
    assert res["calibration_freshness"] is sentinel


def test_the_review_gate_is_silent_without_a_report_or_a_pr(repos: SimpleNamespace) -> None:
    ctx = FlowContext(target_worktree=str(repos.host), execution_branch=EXEC, agent="a", model="m")
    seams, posted = recording_seams()
    assert review_calibration_freshness(ctx, "7", seams=seams) is None
    assert review_calibration_freshness(ctx, None, seams=seams) is None
    assert posted == []


def test_both_trigger_sites_firing_on_one_move_cause_exactly_one_rerun(repos: SimpleNamespace) -> None:
    ctx, result, calls, run = calibrated_flow(repos)
    approve_pr(ctx)
    seams, _ = recording_seams()
    move_head(repos)
    rerun_calibration(ctx, result, run, seams=seams)
    assert len(calls) == 2
    assert review_calibration_freshness(ctx, "7", run_stage=run, seams=seams) is None
    assert len(calls) == 2


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
    assert ctx.stage_results["calibrate"] is refreshed  # the checkpoint sees the refreshed result


# --- PR #203 nits: the comment poster never raises; the gate stores the refreshed Stage 5 result -------------------


def test_marked_comment_never_raises_on_a_malformed_review_payload(tmp_path: Path) -> None:
    ctx = FlowContext(target_worktree=str(tmp_path), execution_branch=EXEC, agent="a", model="m")
    # A non-dict consensus makes approval_pr_number raise AttributeError ('str' has no attribute 'get').
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
