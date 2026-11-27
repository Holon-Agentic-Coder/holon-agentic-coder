from __future__ import annotations

import pytest
from sandbox_executor.flow.review.ledger_commit import _ledger_commit_message
from sandbox_executor.ledger.rows import format_commit_trailers

from scripts.check_commit_trailers import find_attribution_lines

EVERY_TRAILER = format_commit_trailers(
    "pr_review",
    review_severity="high",
    review_iteration=3,
    review_finding_ids=["F-1", "F-2"],
    review_disposition="fixed",
    rejection_reason="not reproducible",
    introduced_by_action_id="A-1",
    consulted_knowledge_ids=["K-1", "K-2"],
)


@pytest.mark.parametrize(
    "message",
    [
        _ledger_commit_message(0),
        _ledger_commit_message(1),
        _ledger_commit_message(3),
        _ledger_commit_message(2, reason="final head"),
    ],
)
def test_review_ledger_commit_message_has_no_attribution(message: str) -> None:
    assert "Action-Origin: pr_review" in message
    assert find_attribution_lines(message) == []


@pytest.mark.parametrize("trailers", [format_commit_trailers(), EVERY_TRAILER])
def test_commit_trailer_block_has_no_attribution(trailers: str) -> None:
    assert trailers.startswith("Action-Origin: ")
    assert find_attribution_lines(trailers) == []


def test_every_trailer_field_is_emitted() -> None:
    assert len(EVERY_TRAILER.splitlines()) == 8
