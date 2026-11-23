"""Literal-only tests for the I/O-free consensus review posting decisions in ``review_policy.py``."""

from __future__ import annotations

import pytest
from sandbox_executor.flow.github.review_policy import (
    GH_UNAVAILABLE_MESSAGE,
    CommentAttempt,
    ReviewAttempt,
    comment_failed_output,
    comment_not_attempted_output,
    comment_posted_output,
    gh_unavailable_outcome,
    is_self_approval_rejection,
    posting_outcome,
    review_failed_output,
    review_flag,
    review_submitted_output,
)

NOT_ATTEMPTED = CommentAttempt(
    False, False, "Discussion comment not attempted: the official review submission posted the report."
)


def test_review_flag():
    assert review_flag(True) == "--approve"
    assert review_flag(False) == "--comment"


@pytest.mark.parametrize(
    "stderr",
    [
        "HTTP 422: Unprocessable Entity",
        "GraphQL: Can not approve your own pull request (addPullRequestReview)",
        "PullRequestReviewAuthorError",
    ],
)
def test_is_self_approval_rejection_positives(stderr):
    assert is_self_approval_rejection(stderr) is True


@pytest.mark.parametrize("stderr", ["", "HTTP 403: Forbidden", "can not approve your own pull request"])
def test_is_self_approval_rejection_negatives(stderr):
    assert is_self_approval_rejection(stderr) is False


def test_output_texts():
    assert review_submitted_output("") == "Official review submitted."
    assert review_submitted_output(None, fallback=True) == "Fallback review comment submitted."
    assert review_submitted_output(" https://x/1 \n") == "https://x/1"
    assert review_failed_output(1, "boom\n", "out") == "Official review submission failed (exit code 1): boom"
    assert review_failed_output(2, "", "out", fallback=True) == "Fallback review comment failed (exit code 2): out"
    assert review_failed_output(3, None, None) == "Official review submission failed (exit code 3):"
    assert comment_posted_output("") == "Discussion comment posted successfully."
    assert comment_failed_output(4, "", "nope") == "gh pr comment failed (exit code 4): nope"
    assert comment_not_attempted_output(True) == NOT_ATTEMPTED.output
    assert comment_not_attempted_output(False) == "Discussion comment not attempted: no posting target was resolved."


@pytest.mark.parametrize(("approved", "flag"), [(True, "--approve"), (False, "--comment")])
def test_gh_unavailable_outcome(approved, flag):
    assert gh_unavailable_outcome("12", approved) == {
        "target": "12",
        "gh_available": False,
        "diagnostics": GH_UNAVAILABLE_MESSAGE,
        "discussion_comment": {"attempted": False, "success": False, "output": GH_UNAVAILABLE_MESSAGE},
        "official_review": {
            "attempted": False,
            "flag": flag,
            "success": False,
            "self_approval_fallback": False,
            "output": GH_UNAVAILABLE_MESSAGE,
        },
        "dual_posted": False,
    }


def test_posting_outcome_review_success():
    outcome = posting_outcome("12", ReviewAttempt(True, "--approve", True, False, "ok "), NOT_ATTEMPTED)
    assert outcome == {
        "target": "12",
        "gh_available": True,
        "diagnostics": "Review consensus posted once to GitHub.",
        "discussion_comment": {"attempted": False, "success": False, "output": NOT_ATTEMPTED.output},
        "official_review": {
            "attempted": True,
            "flag": "--approve",
            "success": True,
            "self_approval_fallback": False,
            "output": "ok",
        },
        "dual_posted": True,
    }


def test_posting_outcome_self_approval_fallback_success():
    review = ReviewAttempt(True, "--comment", True, True, "Fallback review comment submitted.")
    outcome = posting_outcome("12", review, NOT_ATTEMPTED)
    assert outcome["official_review"]["self_approval_fallback"] is True
    assert outcome["official_review"]["flag"] == "--comment"
    assert outcome["dual_posted"] is True


def test_posting_outcome_fallback_failure_then_comment_success():
    review = ReviewAttempt(True, "--comment", False, True, "Fallback review comment failed (exit code 1): x")
    comment = CommentAttempt(True, True, "Discussion comment posted successfully.")
    outcome = posting_outcome("12", review, comment)
    assert outcome["diagnostics"] == "Review consensus posted once to GitHub."
    assert outcome["discussion_comment"] == {
        "attempted": True,
        "success": True,
        "output": "Discussion comment posted successfully.",
    }
    assert outcome["dual_posted"] is True


def test_posting_outcome_all_failed():
    review = ReviewAttempt(True, "--approve", False, False, "Official review submission failed (exit code 1): x")
    comment = CommentAttempt(True, False, "gh pr comment failed (exit code 1): y\n")
    outcome = posting_outcome("12", review, comment)
    assert outcome["diagnostics"] == "Review posting failed: review=False, comment=False."
    assert outcome["discussion_comment"]["output"] == "gh pr comment failed (exit code 1): y"
    assert outcome["dual_posted"] is False


def test_posting_outcome_without_a_target():
    review = ReviewAttempt(False, "--comment", False, False, "")
    comment = CommentAttempt(False, False, "Discussion comment not attempted: no posting target was resolved.")
    outcome = posting_outcome("", review, comment)
    assert outcome["official_review"]["attempted"] is False
    assert outcome["discussion_comment"]["attempted"] is False
    assert outcome["diagnostics"] == "Review posting failed: review=False, comment=False."
