"""The once-per-head approval invalidation comment and the approval stamp, with injected seams and no network."""

from __future__ import annotations

from sandbox_executor.approval_stamp import approval_stamp, ci_result_at_commit
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.github.approval_invalidation import (
    invalidate_approval,
    invalidation_marker,
    render_invalidation,
)
from sandbox_executor.flow.github.ci_reverify import reverify_final_head

APPROVED = "a" * 40
FAILING = "b" * 40
RECORD = {
    "state": "failed",
    "failed_checks": ["macos-unit", "lint"],
    "failed_details": [
        {
            "name": "macos-unit",
            "failed_steps": ["CI Timing Gate (Check <= 5 min)"],
            "duration_seconds": 360.0,
            "timing_gate": True,
        },
        {"name": "lint", "failed_steps": ["ruff"], "duration_seconds": 30.0, "timing_gate": False},
    ],
}


class Fakes:
    def __init__(self, comments: list[str] | None = None) -> None:
        self.comments = comments
        self.posted: list[tuple[str, str]] = []

    def lister(self, pr: str) -> list[str] | None:
        return None if self.comments is None else list(self.comments)

    def poster(self, pr: str, body: str) -> bool:
        self.posted.append((pr, body))
        assert self.comments is not None
        self.comments.append(body)
        return True


def _context(approved: bool = True, posted: bool = True, **flags: bool) -> FlowContext:
    context = FlowContext(repo_dir="/tmp", execution_branch="I-1-x/P-2-y/E-3-z/_", agent="a", model="m", **flags)
    context.stage_results["review"] = StageResult(
        stage=FlowStage.REVIEW,
        status=StageStatus.HALTED_FOR_HUMAN,
        payload={
            "consensus": {"approved": approved, "target_commit": APPROVED, "pr_number": 7},
            "posting": {"dual_posted": posted},
        },
    )
    return context


def _verdict(head: str = FAILING) -> dict:
    return reverify_final_head(RECORD, APPROVED, head)


def test_comment_is_posted_once_per_failing_head() -> None:
    fakes, context = Fakes([]), _context()
    first = invalidate_approval(context, _verdict(), fakes.lister, fakes.poster)
    second = invalidate_approval(context, _verdict(), fakes.lister, fakes.poster)
    assert first["posted"] is True
    assert second == {"posted": False, "reason": "already posted for this head"}
    assert len(fakes.posted) == 1
    other = invalidate_approval(context, _verdict("c" * 40), fakes.lister, fakes.poster)
    assert other["posted"] is True
    assert len(fakes.posted) == 2


def test_comment_names_both_heads_and_each_failing_check() -> None:
    fakes = Fakes([])
    invalidate_approval(_context(), _verdict(), fakes.lister, fakes.poster)
    pr, body = fakes.posted[0]
    assert pr == "7"
    assert "no longer stands" in body
    assert APPROVED[:7] in body
    assert FAILING[:7] in body
    assert "macos-unit (CI Timing Gate, 6 min)" in body
    assert "CI Timing Gate (Check <= 5 min)" in body
    assert "lint" in body
    assert invalidation_marker(FAILING) in body
    assert "a human decides" in body


def test_no_comment_without_a_posted_approval() -> None:
    for context in (
        _context(approved=False),
        _context(posted=False),
        FlowContext(repo_dir="/tmp", agent="a", model="m"),
    ):
        fakes = Fakes([])
        assert invalidate_approval(context, _verdict(), fakes.lister, fakes.poster)["posted"] is False
        assert fakes.posted == []


def test_no_comment_in_dry_run_or_skip_push_or_when_comments_cannot_be_listed() -> None:
    for context in (_context(dry_run=True), _context(skip_push=True)):
        fakes = Fakes([])
        assert invalidate_approval(context, _verdict(), fakes.lister, fakes.poster)["posted"] is False
        assert fakes.posted == []
    unlistable = Fakes(None)
    result = invalidate_approval(_context(), _verdict(), unlistable.lister, unlistable.poster)
    assert result == {"posted": False, "reason": "comments could not be listed"}


def test_render_degrades_when_the_approved_head_is_unknown() -> None:
    body = render_invalidation(None, FAILING, RECORD["failed_details"])
    assert "an earlier commit" in body
    assert FAILING[:7] in body


def test_approval_stamp_names_the_head_and_the_measured_ci_result() -> None:
    stamp = approval_stamp(APPROVED, {"total": 11, "passed": 11, "failed": 0, "pending": 0})
    assert stamp == f"**Covers head** `{APPROVED[:7]}`; CI at that commit: 11 of 11 checks passed."
    assert "all CI checks" not in stamp
    assert approval_stamp(FAILING, {"total": 3, "passed": 3}).startswith(f"**Covers head** `{FAILING[:7]}`")
    assert ci_result_at_commit({}) == "not measured"
    assert ci_result_at_commit({"total": 4, "failed": 1}) == "1 of 4 checks failing"
