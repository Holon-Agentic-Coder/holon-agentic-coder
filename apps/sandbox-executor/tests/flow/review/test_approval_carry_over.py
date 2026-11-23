"""The approval carry-over helpers: the decision, the append-only ACT-CARRY row and the consensus annotation.

The fingerprint is stubbed here (``tests/test_approval_fingerprint.py`` covers it with real git), so these cases run
no git and touch no repository.
"""

from __future__ import annotations

import pytest
from sandbox_executor.flow.review import approval_carry_over
from sandbox_executor.flow.review.approval_carry_over import (
    CarryOver,
    apply_carry_over,
    carry_over_message,
    carry_over_record,
    decide_carry_over,
)
from sandbox_executor.flow.review.approval_fingerprint import fingerprints_match
from sandbox_executor.ledger import ActionRecord
from sandbox_executor.review_report import _mergeability_word

from tests.leak_assertions import assert_no_leak, make_gh_probe

APPROVED = "a" * 40
MERGED = "b" * 40
FINGERPRINT = "pf1:" + "c" * 40
CARRY = CarryOver(APPROVED, MERGED, FINGERPRINT)


def _consensus(**overrides) -> dict:
    data = {
        "approved": True,
        "target_commit": APPROVED,
        "approval_fingerprint": FINGERPRINT,
        "base_branch": "main",
        "ci_checks": {"total": 3, "passed": 3, "failed": 0, "pending": 0},
        "mergeability": {"merge_state_status": "CLEAN", "mergeable": "MERGEABLE", "is_dirty": False},
    }
    return data | overrides


@pytest.fixture
def merged_fingerprint(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Stub the merged head's fingerprint as ``FINGERPRINT`` and record each call."""
    calls: list[tuple] = []

    def fake(repo_dir, head, base_branch):
        calls.append((repo_dir, head, base_branch))
        return FINGERPRINT

    monkeypatch.setattr(approval_carry_over, "approval_fingerprint_of", fake)
    return calls


def test_the_same_fingerprint_carries_the_approval(merged_fingerprint) -> None:
    assert decide_carry_over("/repo", _consensus(), MERGED, "main") == CARRY
    assert merged_fingerprint == [("/repo", MERGED, "main")]


@pytest.mark.parametrize(
    "overrides",
    [
        {"approval_fingerprint": None},  # A legacy approval: no fingerprint recorded.
        {"approval_fingerprint": "pf1:" + "d" * 40},  # The PR-own change differs.
        {"target_commit": ""},
    ],
)
def test_no_carry_over_without_a_matching_recorded_fingerprint(merged_fingerprint, overrides) -> None:
    assert decide_carry_over("/repo", _consensus(**overrides), MERGED, "main") is None


def test_no_carry_over_without_a_merged_head(merged_fingerprint) -> None:
    assert decide_carry_over("/repo", _consensus(), "", "main") is None
    assert merged_fingerprint == []


def test_an_unreadable_merged_fingerprint_never_carries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approval_carry_over, "approval_fingerprint_of", lambda *_: None)
    assert decide_carry_over("/repo", _consensus(), MERGED, "main") is None


def test_none_never_matches_none() -> None:
    assert fingerprints_match(None, None) is False


def test_the_record_is_a_valid_append_only_action_row() -> None:
    record = carry_over_record("feature/x", CARRY)

    ActionRecord.from_dict(record)  # Validates category, outcome and origin; raises on a bad row.
    assert record["action_id"].startswith("ACT-CARRY-")
    assert record["outcome"] == "success" and record["entry_type"] == "action"
    assert record["action_origin"] == "pr_review"
    assert record["labels"] == ["review", "conflict-gate", "approval-carry-over"]
    assert (record["approved_head"], record["merged_head"]) == (APPROVED, MERGED)
    assert record["approval_fingerprint"] == FINGERPRINT
    assert APPROVED in record["action"] and MERGED in record["action"] and FINGERPRINT in record["action"]


def test_the_record_free_text_is_redacted() -> None:
    secret = make_gh_probe()
    record = carry_over_record(f"feature/x token={secret}", CARRY)

    assert_no_leak(secret, record["action"])


def test_apply_never_touches_target_commit() -> None:
    consensus = _consensus()

    apply_carry_over(consensus, CARRY)

    assert consensus["target_commit"] == APPROVED
    assert (consensus["carried_from"], consensus["carried_to"]) == (APPROVED, MERGED)
    assert consensus["ci_checks"]["measured"] is False
    assert MERGED in consensus["ci_checks"]["reason"]
    assert _mergeability_word(consensus) == "UNMEASURED"
    assert MERGED in consensus["mergeability"]["reason"]


def test_the_message_names_both_shas_the_fingerprint_and_the_ci_wait() -> None:
    message = carry_over_message(CARRY)

    assert APPROVED in message and MERGED in message and FINGERPRINT in message
    assert f"CI on {MERGED} is still required" in message
