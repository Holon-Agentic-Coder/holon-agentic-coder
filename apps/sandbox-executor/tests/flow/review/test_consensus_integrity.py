"""Consensus integrity: the Stage 4 loop decides only from what the reviewers returned.

The failing run: reviewers returned APPROVED, APPROVED and CHANGES_REQUESTED (critical 1, Security role CHANGES), an
earlier resolver row had rejected a finding as "out of scope", and the host still declared consensus and posted
"APPROVED (3/3)". These tests reproduce that shape through the injected ensemble and resolver seams.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.flow import run_review_loop
from sandbox_executor.review_ensemble import (
    ParsedTrailer,
    ReviewCounts,
    ReviewerResult,
    ReviewerVerdict,
    ReviewFinding,
    reviewer_report_rel,
)
from sandbox_executor.review_report import with_reviewer_detail

from tests.review_fakes import FakeEnsemble, clean_reviewer, install_fake_reviewers
from tests.test_review_dispatch import EXEC_TARGET
from tests.test_review_loop import _ensemble_ctx, _LoopStubs

_REJECTED_PATH = "src/sandbox/mounts.py"


def _reviewer(
    index: int,
    severity: str,
    summary: str,
    *,
    path: str = _REJECTED_PATH,
    line: int | None = 40,
    security: str | None = None,
) -> ReviewerResult:
    """A completed reviewer at CHANGES_REQUESTED with one finding; ``security`` sets its Security role verdict."""
    finding = ReviewFinding(severity, path, line, summary)
    parsed = ParsedTrailer(ReviewerVerdict.CHANGES_REQUESTED, ReviewCounts(**{severity: 1}), (finding,))
    result = ReviewerResult.completed(
        index, "fake-agent", "fake-model", reviewer_report_rel(1, index), "report", parsed
    )
    if security is None:
        return result
    roles = [{"role": "Security", "verdict": security, "review": "Mount escapes the sandbox."}]
    return with_reviewer_detail(result, {"roles": roles, "findings": [], "verified": []})


def _rejecting_resolver(calls: list[dict[str, Any]]):
    """A resolver that rejects every finding as bare ``out of scope`` and pushes nothing."""

    def resolver(target, iteration, findings, action_ids, rejected_findings):
        calls.append({"iteration": iteration, "findings": list(findings), "rejected": list(rejected_findings)})
        return {
            "dispositions": [
                {"id": row.id, "disposition": "rejected", "rejection_reason": "out of scope"} for row in findings
            ],
            "head_sha": target.head_sha,
            "pushed": False,
        }

    return resolver


def _run_two_iterations(tmp_path: Path, monkeypatch, first: ReviewerResult, second: ReviewerResult):
    """Iteration 1 returns ``first`` as reviewer 3, iteration 2 returns ``second``; reviewers 1 and 2 stay clean."""
    rounds = iter([first, second])

    def ensemble(size: int) -> list[ReviewerResult]:
        return [clean_reviewer(1), clean_reviewer(2), next(rounds)]

    fake = FakeEnsemble(ensemble)
    install_fake_reviewers(monkeypatch, fake)
    calls: list[dict[str, Any]] = []
    ctx = _ensemble_ctx(tmp_path)
    with _LoopStubs() as stubs:
        res = run_review_loop(
            ctx,
            target=EXEC_TARGET,
            max_iterations=2,
            ensemble_size=3,
            run_ensemble_fn=fake,
            run_resolver_fn=_rejecting_resolver(calls),
        )
    return res, stubs.post, calls, ctx


def _assert_no_false_consensus(res: dict[str, Any], post, calls: list[dict[str, Any]], severity: str) -> None:
    assert res["consensus"]["approved"] is False
    assert res["halted_for_human"] is False
    assert res["ensemble_verdict"] != "APPROVED"
    for call in post.call_args_list:
        assert call.kwargs["consensus_data"]["ensemble_verdict"] != "APPROVED"
        assert call.kwargs["consensus_data"]["approved"] is False
    assert [call["iteration"] for call in calls] == [1, 2]
    assert [row.severity for row in calls[1]["findings"]] == [severity]
    third = res["reviewers"][2]
    assert third["verdict"] == "CHANGES_REQUESTED"
    assert third["counts"][severity] == 1


def test_scenario_a_positional_id_collision_never_hides_a_critical(tmp_path, monkeypatch):
    """Iteration 1 rejects ``R3-F1`` (important); iteration 2's ``R3-F1`` is a different critical finding."""
    first = _reviewer(3, "important", "Mount options are logged verbosely.", path="src/sandbox/log.py", line=7)
    second = _reviewer(3, "critical", "Bind mount exposes the host Docker socket.", security="CHANGES")

    res, post, calls, _ = _run_two_iterations(tmp_path, monkeypatch, first, second)

    _assert_no_false_consensus(res, post, calls, "critical")


@pytest.mark.parametrize("first_severity", ["critical", "important"])
def test_scenario_b_signature_match_never_hides_a_critical(tmp_path, monkeypatch, first_severity):
    """The same path, line and text re-raised as critical: at the same severity, then at a higher one."""
    summary = "Bind mount exposes the host Docker socket."
    first = _reviewer(3, first_severity, summary)
    second = _reviewer(3, "critical", summary, security="CHANGES")

    res, post, calls, _ = _run_two_iterations(tmp_path, monkeypatch, first, second)

    _assert_no_false_consensus(res, post, calls, "critical")
