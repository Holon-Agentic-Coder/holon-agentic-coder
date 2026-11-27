from __future__ import annotations

from dataclasses import replace

import pytest
from sandbox_executor.review_finding_outcomes import (
    REQUIRED_VERDICTS,
    is_disposed,
    returned_verdict_count,
    undisposed_finding_ids,
    verdict_count_ok,
    with_commit,
)
from sandbox_executor.review_report import FindingDisposition, FindingRow

from tests.review_fakes import clean_reviewer, failed_reviewer

NIT = FindingRow("R1-F1", "nit", "src/app.py", 4, (1,), "trailing whitespace in the handler", "strip it")
MUTANT = FindingRow(
    "R1-F2", "important", "src/budget.py", 31, (2,), "mutant survives: < to <= in budget check", "add a boundary test"
)
LATER = FindingRow("R2-F1", "important", "src/app.py", 40, (3,), "missing timeout on the call", "pass a timeout")
LOOP_FINDINGS = (NIT, MUTANT, LATER)


def outcome(row: FindingRow, disposition: str, **fields: str | None) -> FindingDisposition:
    return FindingDisposition(
        row.id, row.severity, row.path, row.line, row.summary, row.recommendation, disposition, **fields
    )


def test_a_loop_that_leaves_a_nit_and_a_surviving_mutant_without_an_outcome_fails() -> None:
    dispositions = [outcome(LATER, "applied", commit_sha="abc123")]

    assert undisposed_finding_ids(LOOP_FINDINGS, dispositions) == ["R1-F1", "R1-F2"]


def test_a_loop_where_every_finding_is_applied_or_rejected_with_a_reason_passes() -> None:
    dispositions = [
        outcome(NIT, "rejected", rejection_reason="style matches the module convention"),
        outcome(MUTANT, "applied", commit_sha="abc123"),
        outcome(LATER, "applied", commit_sha="def456"),
    ]

    assert undisposed_finding_ids(LOOP_FINDINGS, dispositions) == []


@pytest.mark.parametrize(
    "disposition",
    [
        outcome(NIT, "open"),
        outcome(NIT, "rejected", rejection_reason=""),
        outcome(NIT, "rejected", rejection_reason="unspecified"),
        outcome(NIT, "rejected"),
        outcome(NIT, "applied"),
        outcome(NIT, "applied", commit_sha="  "),
    ],
)
def test_an_outcome_without_a_commit_or_a_reason_leaves_the_finding_undisposed(disposition: FindingDisposition) -> None:
    assert not is_disposed(disposition)
    assert undisposed_finding_ids([NIT], [disposition]) == ["R1-F1"]


def test_an_applied_fix_with_a_commit_disposes_the_finding() -> None:
    assert is_disposed(outcome(NIT, "applied", commit_sha="abc123"))


def test_a_disposition_matches_by_signature_not_by_id() -> None:
    same_id_other_finding = replace(outcome(NIT, "applied", commit_sha="abc123"), summary="a different finding")

    assert undisposed_finding_ids([NIT], [same_id_other_finding]) == ["R1-F1"]


def test_a_repeated_finding_id_is_reported_once() -> None:
    assert undisposed_finding_ids([NIT, NIT], []) == ["R1-F1"]


@pytest.mark.parametrize(("count", "ok"), [(0, False), (2, False), (REQUIRED_VERDICTS, True), (4, False)])
def test_approval_needs_exactly_three_verdicts(count: int, ok: bool) -> None:
    assert verdict_count_ok(count) is ok


def test_a_failed_reviewer_returns_no_verdict() -> None:
    reviewers = [clean_reviewer(1), clean_reviewer(2), failed_reviewer(3)]

    assert returned_verdict_count(reviewers) == 2


def test_with_commit_stamps_only_applied_fixes_that_lack_a_commit() -> None:
    applied, kept, rejected = with_commit(
        [
            outcome(NIT, "applied"),
            outcome(MUTANT, "applied", commit_sha="own"),
            outcome(LATER, "rejected", rejection_reason="inaccurate"),
        ],
        "pushed",
    )

    assert (applied.commit_sha, kept.commit_sha, rejected.commit_sha) == ("pushed", "own", None)


def test_with_commit_leaves_an_unpushed_fix_without_a_commit() -> None:
    assert not is_disposed(*with_commit([outcome(NIT, "applied")], None))
