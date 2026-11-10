"""Consensus integrity: the Stage 4 loop decides only from what the reviewers returned.

The failing run: reviewers returned APPROVED, APPROVED and CHANGES_REQUESTED (critical 1, Security role CHANGES), an
earlier resolver row had rejected a finding as "out of scope", and the host still declared consensus and posted
"APPROVED (3/3)". These tests reproduce that shape through the injected ensemble and resolver seams.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.flow import run_review_loop
from sandbox_executor.flow.review.adjudication import (
    _augment_pr_text_with_rejected,
    _filter_adjudicated_findings,
    record_nit_rejections,
)
from sandbox_executor.flow.review.verdict import (
    consensus_blockers,
    decide_returned_consensus,
    not_approved_verdicts,
    render_final_verdicts,
)
from sandbox_executor.review_ensemble import (
    ConsensusVerdict,
    ParsedTrailer,
    ReviewCounts,
    ReviewerResult,
    ReviewerVerdict,
    ReviewFinding,
    reviewer_report_rel,
    serialize_review_outcome,
)
from sandbox_executor.review_report import FindingDisposition, finding_signature, with_reviewer_detail

from tests.review_fakes import FakeEnsemble, clean_reviewer, install_fake_reviewers
from tests.test_review_dispatch import EXEC_TARGET
from tests.test_review_loop import _ensemble_ctx, _LoopStubs

_REJECTED_PATH = "src/sandbox/mounts.py"


def _reviewer(
    severity: str,
    summary: str,
    location: tuple[str, int | None] = (_REJECTED_PATH, 40),
    security: str | None = None,
    index: int = 3,
) -> ReviewerResult:
    """A completed reviewer at CHANGES_REQUESTED with one finding; ``security`` sets its Security role verdict."""
    path, line = location
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
    first = _reviewer("important", "Mount options are logged verbosely.", ("src/sandbox/log.py", 7))
    second = _reviewer("critical", "Bind mount exposes the host Docker socket.", security="CHANGES")

    res, post, calls, _ = _run_two_iterations(tmp_path, monkeypatch, first, second)

    _assert_no_false_consensus(res, post, calls, "critical")


@pytest.mark.parametrize("first_severity", ["critical", "important"])
def test_scenario_b_signature_match_never_hides_a_critical(tmp_path, monkeypatch, first_severity):
    """The same path, line and text re-raised as critical: at the same severity, then at a higher one."""
    summary = "Bind mount exposes the host Docker socket."
    first = _reviewer(first_severity, summary)
    second = _reviewer("critical", summary, security="CHANGES")

    res, post, calls, _ = _run_two_iterations(tmp_path, monkeypatch, first, second)

    _assert_no_false_consensus(res, post, calls, "critical")


# --- Step 2: adjudication suppression is for nits only and never touches a verdict ----------------------------------


def _history(*dispositions: FindingDisposition) -> dict:
    history: dict = {}
    record_nit_rejections(history, dispositions)
    return history


def _rejected(severity: str, summary: str, path: str = _REJECTED_PATH, line: int | None = 40) -> FindingDisposition:
    return FindingDisposition("R3-F1", severity, path, line, summary, rejection_reason="out of scope")


def _filter(reviewer: ReviewerResult, history: dict):
    before = (reviewer.verdict, reviewer.counts, reviewer.findings, reviewer.report_text)
    adjudication = _filter_adjudicated_findings([reviewer], history)
    assert (reviewer.verdict, reviewer.counts, reviewer.findings, reviewer.report_text) == before
    return adjudication


@pytest.mark.parametrize("severity", ["critical", "important"])
def test_critical_and_important_findings_are_never_suppressed(severity):
    summary = "Bind mount exposes the host Docker socket."
    history = {finding_signature(severity, _REJECTED_PATH, 40, summary): _rejected(severity, summary)}

    adjudication = _filter(_reviewer(severity, summary), history)

    assert [row.severity for row in adjudication.actionable] == [severity]
    assert adjudication.suppressed == ()


def test_a_rejected_nit_with_the_same_signature_is_suppressed_without_changing_the_verdict():
    reviewer = _reviewer("nit", "Rename the helper.")

    adjudication = _filter(reviewer, _history(_rejected("nit", "Rename the helper.")))

    assert adjudication.actionable == ()
    assert [row.id for row in adjudication.suppressed] == ["R3-F1"]
    assert reviewer.verdict is ReviewerVerdict.CHANGES_REQUESTED
    assert reviewer.counts == ReviewCounts(nit=1)


def test_a_nit_raised_by_a_reviewer_whose_security_role_objects_is_never_suppressed():
    reviewer = _reviewer("nit", "Rename the helper.", security="CHANGES")

    adjudication = _filter(reviewer, _history(_rejected("nit", "Rename the helper.")))

    assert [row.severity for row in adjudication.actionable] == ["nit"]


def test_a_nit_re_raised_at_a_higher_severity_is_a_new_finding():
    adjudication = _filter(
        _reviewer("important", "Rename the helper."), _history(_rejected("nit", "Rename the helper."))
    )

    assert [row.severity for row in adjudication.actionable] == ["important"]


def test_a_positional_id_collision_with_different_text_is_not_suppressed():
    history = _history(_rejected("nit", "Rename the helper."))

    adjudication = _filter(_reviewer("nit", "Document the mount flags."), history)

    assert [row.id for row in adjudication.actionable] == ["R3-F1"]
    assert adjudication.suppressed == ()


def test_only_rejected_nits_are_remembered_and_listed_for_reviewers():
    history = _history(_rejected("critical", "Escape."), _rejected("important", "Leak."), _rejected("nit", "Typo."))

    assert [d.severity for d in history.values()] == ["nit"]
    _, body = _augment_pr_text_with_rejected(("t", "body"), [_rejected("critical", "Escape."), *history.values()])
    assert "Typo." in body
    assert "Escape." not in body


# --- Step 3: the verdict comes from what the reviewers returned ------------------------------------------------------


def _clean_with_security(index: int, verdict: str) -> ReviewerResult:
    roles = [{"role": "Security", "verdict": verdict, "review": "Checked the mounts."}]
    return with_reviewer_detail(clean_reviewer(index), {"roles": roles, "findings": [], "verified": []})


def test_a_role_at_changes_blocks_consensus_when_every_reviewer_approved_cleanly(tmp_path, monkeypatch):
    fake = FakeEnsemble([clean_reviewer(1), clean_reviewer(2), _clean_with_security(3, "CHANGES")])
    install_fake_reviewers(monkeypatch, fake)
    ctx = _ensemble_ctx(tmp_path)
    with _LoopStubs() as stubs:
        res = run_review_loop(ctx, target=EXEC_TARGET, max_iterations=2, ensemble_size=3, run_ensemble_fn=fake)

    assert res["consensus"]["approved"] is False
    assert res["ensemble_verdict"] == "CHANGES_REQUESTED"
    assert res["consensus_blockers"] == ["reviewer 3 Security role CHANGES"]
    assert stubs.post.call_args.kwargs["consensus_data"]["ensemble_verdict"] == "CHANGES_REQUESTED"
    assert any(
        "reviewer 3 Security role CHANGES" in line and "no resolver pass applicable" in line for line in ctx.logs
    )


def test_a_passing_role_table_still_approves(tmp_path, monkeypatch):
    fake = FakeEnsemble([clean_reviewer(1), clean_reviewer(2), _clean_with_security(3, "PASS")])
    install_fake_reviewers(monkeypatch, fake)
    ctx = _ensemble_ctx(tmp_path)
    with _LoopStubs():
        res = run_review_loop(ctx, target=EXEC_TARGET, max_iterations=2, ensemble_size=3, run_ensemble_fn=fake)

    assert res["consensus"]["approved"] is True


def test_a_container_verdict_other_than_approved_blocks_a_clean_host_view(tmp_path, monkeypatch):
    install_fake_reviewers(monkeypatch)
    monkeypatch.delenv("HOLON_IN_SANDBOX", raising=False)
    monkeypatch.delenv("HOLON_ROLE", raising=False)

    def fake_dispatch(role, runner, model, args, **kwargs):
        reviewers = [clean_reviewer(1), clean_reviewer(2), clean_reviewer(3)]
        payload = serialize_review_outcome(reviewers, ConsensusVerdict.CHANGES_REQUESTED)
        Path(kwargs["result_file"]).write_text(json.dumps(payload), encoding="utf-8")
        return 0

    ctx = _ensemble_ctx(tmp_path, dispatcher=fake_dispatch)
    with _LoopStubs():
        res = run_review_loop(ctx, target=EXEC_TARGET, max_iterations=1, ensemble_size=3)

    assert res["consensus"]["approved"] is False
    assert res["ensemble_verdict"] == "CHANGES_REQUESTED"
    assert any("reviewer container reported ensemble verdict CHANGES_REQUESTED" in line for line in ctx.logs)


def test_a_reviewer_whose_findings_were_all_suppressed_is_logged_and_never_approved(tmp_path, monkeypatch):
    nit = _reviewer("nit", "Rename the helper.", index=1)
    fake = FakeEnsemble([nit])
    install_fake_reviewers(monkeypatch, fake)
    calls: list[dict[str, Any]] = []
    ctx = _ensemble_ctx(tmp_path)
    with _LoopStubs():
        res = run_review_loop(
            ctx,
            target=EXEC_TARGET,
            max_iterations=3,
            ensemble_size=1,
            run_ensemble_fn=fake,
            run_resolver_fn=_rejecting_resolver(calls),
        )

    assert res["consensus"]["approved"] is False
    assert [call["iteration"] for call in calls] == [1]
    assert [d.severity for d in calls[0]["findings"]] == ["nit"]
    mismatch = [line for line in ctx.logs if "would read APPROVED after adjudication" in line]
    assert mismatch
    assert "reviewer 1 returned CHANGES_REQUESTED" in mismatch[0]
    assert "(suppressed: R1-F1)" in mismatch[0]


def test_decide_returned_consensus_downgrades_approval_only_while_a_blocker_stands():
    clean = [clean_reviewer(1)]

    assert decide_returned_consensus(clean, 1, True, True, []) is ConsensusVerdict.APPROVED
    assert decide_returned_consensus(clean, 1, True, True, ["x"]) is ConsensusVerdict.CHANGES_REQUESTED
    assert decide_returned_consensus([], 1, True, True, ["x"]) is ConsensusVerdict.FAILED
    assert consensus_blockers(clean, None, _filter_adjudicated_findings(clean, {})) == []


def test_final_verdicts_name_every_reviewer_and_role_objection():
    reviewers = [clean_reviewer(1), clean_reviewer(2), _reviewer("critical", "Escape.", security="CHANGES")]

    line = render_final_verdicts(reviewers)

    assert line.startswith("Reviewer 1 APPROVED (0/0/0); Reviewer 2 APPROVED (0/0/0); ")
    assert "Reviewer 3 CHANGES_REQUESTED (1/0/0); Reviewer 3 Security role CHANGES" in line
    assert not_approved_verdicts(reviewers) == ["reviewer 3 final verdict CHANGES_REQUESTED"]


# --- Step 4: the post path follows the report's approval guard -------------------------------------------------------


def test_an_approved_verdict_with_a_dissenting_reviewer_is_posted_as_a_comment_never_an_approval():
    from unittest.mock import MagicMock, patch

    from sandbox_executor.flow import post_pr_review_consensus

    reviewers = [clean_reviewer(1), clean_reviewer(2), _reviewer("critical", "Escape.", security="CHANGES")]
    consensus_data = {"approved": True, "ensemble_verdict": "APPROVED", "status": "approved", "ensemble_size": 3}
    with patch("shutil.which", return_value="/usr/bin/gh"), patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Success", stderr="")
        res = post_pr_review_consensus(
            repo_dir="/tmp", target="123", consensus_data=consensus_data, reviewers=reviewers
        )

    assert res["official_review"]["flag"] == "--comment"
    review_call = mock_run.call_args_list[0][0][0]
    assert "--approve" not in review_call
    body = review_call[review_call.index("--body") + 1]
    assert body.splitlines()[0].endswith("— 2/3 approved")


# --- Step 6: end-of-flow wording names every final verdict -----------------------------------------------------------


def _stage4(tmp_path: Path, loop_res: dict[str, Any]):
    from unittest.mock import patch

    from sandbox_executor.flow import FlowContext, run_review_stage

    ctx = FlowContext(
        repo_dir=str(tmp_path), execution_branch=EXEC_TARGET, dry_run=True, token_reduce=False, max_review_iterations=5
    )
    with patch("sandbox_executor.flow.review.loop.run_review_loop", return_value=loop_res):
        return run_review_stage(ctx), ctx


def _summaries_of_the_failing_shape() -> list[dict[str, Any]]:
    from sandbox_executor.flow.review.records import _reviewer_summary

    third = _reviewer("critical", "Bind mount exposes the host Docker socket.", security="CHANGES")
    return [_reviewer_summary(r) for r in (clean_reviewer(1), clean_reviewer(2), third)]


@pytest.mark.parametrize("claimed_approved", [False, True])
def test_stage4_names_each_final_verdict_and_never_reports_a_dissent_as_approved(tmp_path, claimed_approved):
    """``claimed_approved`` simulates an upstream regression handing Stage 4 an approved consensus anyway."""
    loop_res = {
        "consensus": {"approved": claimed_approved, "status": "approved" if claimed_approved else "changes_requested"},
        "ensemble_verdict": "APPROVED" if claimed_approved else "CHANGES_REQUESTED",
        "reviewers": _summaries_of_the_failing_shape(),
        "total_iterations": 1,
    }

    result, ctx = _stage4(tmp_path, loop_res)

    assert result.status.value == "failed"
    assert result.payload["halted_for_human"] is False
    message = result.error or ""
    assert "Reviewer 1 APPROVED (0/0/0); Reviewer 2 APPROVED (0/0/0); Reviewer 3 CHANGES_REQUESTED (1/0/0)" in message
    assert "not approved: reviewer 3 final verdict CHANGES_REQUESTED" in message
    for text in [message, *ctx.logs]:
        assert "ready for merge" not in text
        assert "consensus approval" not in text
        assert "[SAFETY HALT]" not in text


def test_stage4_names_a_blocker_rather_than_test_failures_when_every_reviewer_approved_cleanly(tmp_path):
    from sandbox_executor.flow.review.records import _reviewer_summary

    loop_res = {
        "consensus": {"approved": False, "status": "changes_requested"},
        "ensemble_verdict": "CHANGES_REQUESTED",
        "reviewers": [_reviewer_summary(clean_reviewer(i)) for i in (1, 2, 3)],
        "consensus_blockers": ["reviewer 3 Security role CHANGES"],
        "total_iterations": 1,
    }

    result, _ = _stage4(tmp_path, loop_res)

    message = result.error or ""
    assert result.status.value == "failed"
    assert "Autonomous review blocked: consensus withheld by reviewer 3 Security role CHANGES" in message
    assert "test suite reported failures" not in message


def test_summary_lines_and_exit_status_name_a_reviewer_that_did_not_approve():
    from sandbox_executor.flow import review_loop_approved, review_summary_lines

    loop_res = {
        "consensus": {"approved": True},
        "ensemble_verdict": "APPROVED",
        "reviewers": _summaries_of_the_failing_shape(),
    }

    assert review_loop_approved(loop_res) is False
    assert "  Not approved   : reviewer 3 final verdict CHANGES_REQUESTED" in review_summary_lines(loop_res)


def test_the_loop_summary_row_states_the_final_verdicts(tmp_path, monkeypatch):
    from tests.test_review_loop import _ledger

    first = _reviewer("important", "Mount options are logged verbosely.")
    second = _reviewer("critical", "Bind mount exposes the host Docker socket.", security="CHANGES")
    _run_two_iterations(tmp_path, monkeypatch, first, second)

    (summary,) = [row for row in _ledger(tmp_path) if row.get("entry_type") == "summary"]
    assert summary["status"] == "failed"
    assert "not approved: reviewer 3 final verdict CHANGES_REQUESTED" in summary["summary"]
    assert "final verdicts: Reviewer 1 APPROVED (0/0/0)" in summary["summary"]


@pytest.mark.parametrize(
    ("mergeability", "expected"),
    [
        ({"is_dirty": True, "merge_state_status": "DIRTY", "mergeable": "CONFLICTING"}, "CI SIGNAL SUPPRESSED"),
        ({"is_dirty": False, "merge_state_status": "MISSING_REF", "mergeable": "UNKNOWN"}, "MISSING_REF"),
    ],
)
def test_stage4_mergeability_halts_also_name_each_final_verdict(tmp_path, mergeability, expected):
    loop_res = {
        "consensus": {"approved": False, "status": "changes_requested"},
        "ensemble_verdict": "CHANGES_REQUESTED",
        "reviewers": _summaries_of_the_failing_shape(),
        "mergeability": mergeability,
        "total_iterations": 1,
    }

    result, ctx = _stage4(tmp_path, loop_res)

    message = result.error or ""
    assert result.status.value == "failed"
    assert expected in message
    assert "Final verdicts: Reviewer 1 APPROVED (0/0/0)" in message
    assert "not approved: reviewer 3 final verdict CHANGES_REQUESTED" in message
    assert any(message in line for line in ctx.logs)
