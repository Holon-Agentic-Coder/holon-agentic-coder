"""Findings a halted Stage 4 pass left open are carried in the checkpoint, resolved first on resume, and block approval.

Hermetic: fakes for every reviewer and resolver container dispatch, CI probe, mergeability probe and post; the host
push is patched out and no subprocess runs.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor.flow import (
    carried_blocker,
    deserialize_carried,
    load_checkpoint,
    merge_findings,
    save_checkpoint,
    serialize_open,
    undisposed,
)
from sandbox_executor.flow.review.loop import _run_engine_review_loop
from sandbox_executor.ledger import read_merged_rows
from sandbox_executor.review_ensemble import (
    ConsensusVerdict,
    ParsedTrailer,
    ReviewCounts,
    ReviewerResult,
    ReviewerVerdict,
    ReviewFinding,
    serialize_review_outcome,
)
from sandbox_executor.review_report import FindingDisposition, FindingRow

from tests.test_review_dispatch import _host_loop_patches, _make_context

NIT_A = FindingRow("R1-F1", "nit", "flow/calibrate_rerun.py", 40, (1,), "approval_pr_number runs outside try", "Move.")
NIT_B = FindingRow("R1-F2", "nit", "flow/calibrate_rerun.py", 90, (1,), "refreshed result is not stored", "Store.")
SESSION_LIMIT = "You've hit your session limit"


# --- the carried-findings helpers -----------------------------------------------------------------------------------


def test_open_findings_round_trip_through_the_checkpoint_shape():
    raw = serialize_open([NIT_A, NIT_B], {"R1-F1": "ACT-FIND-1"})

    rows, action_ids = deserialize_carried(json.loads(json.dumps(raw)))

    assert rows == [NIT_A, NIT_B]
    assert action_ids["R1-F1"] == "ACT-FIND-1"
    assert "R1-F2" not in action_ids


@pytest.mark.parametrize(
    "raw", [None, "nonsense", [None], [{"severity": "nit"}], [{"id": "R1-F1"}], [{"id": " ", "summary": "x"}]]
)
def test_malformed_carried_entries_are_skipped_never_treated_as_approved(raw):
    assert deserialize_carried(raw) == ([], {})


def test_merge_findings_puts_carried_first_and_deduplicates_by_signature():
    again = FindingRow("R2-F1", "nit", NIT_A.path, NIT_A.line, (2,), NIT_A.summary, "Again.")
    fresh = FindingRow("R2-F2", "important", "src/app.py", 3, (2,), "A new problem.", "Fix it.")

    assert merge_findings([again, fresh], [NIT_A]) == [NIT_A, fresh]


def test_undisposed_keeps_rows_without_an_applied_or_rejected_disposition():
    def disp(row: FindingRow, disposition: str) -> FindingDisposition:
        return FindingDisposition(row.id, row.severity, row.path, row.line, row.summary, disposition=disposition)

    assert undisposed([NIT_A, NIT_B], [disp(NIT_A, "rejected"), disp(NIT_B, "open")]) == [NIT_B]
    assert undisposed([NIT_A, NIT_B], [disp(NIT_A, "applied"), disp(NIT_B, "rejected")]) == []


def test_the_carried_blocker_names_each_finding():
    assert carried_blocker([]) == ""
    assert "R1-F2 (flow/calibrate_rerun.py:90)" in carried_blocker([NIT_B])


# --- the PR #203 resume scenario ------------------------------------------------------------------------------------


def _reviewers(objecting: bool) -> tuple[list[ReviewerResult], ConsensusVerdict]:
    """Reviewer 1 raises both nits when ``objecting``; otherwise all three approve cleanly."""
    clean = ParsedTrailer(ReviewerVerdict.APPROVED, ReviewCounts(), ())
    first = clean
    if objecting:
        nits = tuple(ReviewFinding("nit", row.path, row.line, row.summary) for row in (NIT_A, NIT_B))
        first = ParsedTrailer(ReviewerVerdict.CHANGES_REQUESTED, ReviewCounts(nit=2), nits)
    parsed = {1: first, 2: clean, 3: clean}
    reviewers = [ReviewerResult.completed(i, "test-agent", "test-model", f"r{i}.md", "", parsed[i]) for i in (1, 2, 3)]
    return reviewers, ConsensusVerdict.CHANGES_REQUESTED if objecting else ConsensusVerdict.APPROVED


def _dispatcher(log: list[str], objecting: bool, resolve: Callable[[list[dict]], tuple[int, dict]]):
    """A fake role dispatcher appending ``ensemble`` or ``resolver`` to one shared ``log`` in call order."""

    def fake_dispatcher(_role, _runner, _model, positional_args, **kwargs):
        result_file = Path(kwargs["result_file"])
        if "--resolve" in positional_args:
            findings = json.loads(result_file.read_text(encoding="utf-8")).get("findings", [])
            log.append("resolver:" + ",".join(f["id"] for f in findings))
            exit_code, outcome = resolve(findings)
            result_file.write_text(json.dumps(outcome), encoding="utf-8")
            return exit_code
        log.append("ensemble")
        reviewers, verdict = _reviewers(objecting)
        result_file.write_text(json.dumps(serialize_review_outcome(reviewers, verdict)), encoding="utf-8")
        return 0 if verdict is ConsensusVerdict.APPROVED else 1

    return fake_dispatcher


def _session_limit(_findings: list[dict]) -> tuple[int, dict]:
    """The resolver turn exits 1 on a usage limit and decides nothing."""
    return 1, {
        "resolver": True,
        "dispositions": [],
        "error": SESSION_LIMIT,
        "exit_code": 1,
        "output_tail": SESSION_LIMIT,
    }


def _resolved(dispositions: list[dict], head_sha: str) -> Callable[[list[dict]], tuple[int, dict]]:
    def resolve(_findings: list[dict]) -> tuple[int, dict]:
        return 0, {"resolver": True, "dispositions": dispositions, "head_sha": head_sha, "error": None}

    return resolve


def _engine_pass(ctx) -> dict:
    patches = [*_host_loop_patches(), patch("sandbox_executor.flow.review.resolver.push_commit")]
    for p in patches:
        p.start()
    try:
        return _run_engine_review_loop(ctx)
    finally:
        for p in reversed(patches):
            p.stop()


def _res_rows(tmp_path: Path) -> list[dict]:
    ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
    rows = read_merged_rows(str(ledger))
    return [r for r in rows if str(r.get("action_id", "")).startswith("ACT-RES-") and r.get("review_finding_ids")]


@pytest.fixture
def halted_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run 1: reviewer 1 raises both nits, the resolver hits its session limit; return the saved checkpoint path."""
    monkeypatch.delenv("HOLON_IN_SANDBOX", raising=False)
    monkeypatch.delenv("HOLON_ROLE", raising=False)
    log: list[str] = []
    ctx = _make_context(tmp_path, dispatcher=_dispatcher(log, True, _session_limit), intent_data={"slug": "rescrash"})

    res = _engine_pass(ctx)

    assert log == ["ensemble", "resolver:R1-F1,R1-F2"]
    assert "session limit" in res["resolver_error"]
    assert res["consensus"]["approved"] is False
    assert _res_rows(tmp_path) == []  # A crash is never recorded as a decision.
    assert [f["id"] for f in ctx.carried_review_findings] == ["R1-F1", "R1-F2"]
    return save_checkpoint(ctx)


def _resume(checkpoint: str, log: list[str], resolve: Callable[[list[dict]], tuple[int, dict]]):
    ctx = load_checkpoint(checkpoint).context
    ctx.dispatcher = _dispatcher(log, False, resolve)
    return ctx, _engine_pass(ctx)


def test_resume_resolves_carried_findings_before_reviewing_and_then_approves(tmp_path: Path, halted_checkpoint: str):
    log: list[str] = []
    dispositions = [
        {"id": "R1-F1", "disposition": "applied"},
        {"id": "R1-F2", "disposition": "rejected", "rejection_reason": "inaccurate"},
    ]

    ctx, res = _resume(halted_checkpoint, log, _resolved(dispositions, "b" * 40))

    assert log == ["resolver:R1-F1,R1-F2", "ensemble"]
    assert res["consensus"]["approved"] is True
    rows = {r["review_finding_ids"][0]: r for r in _res_rows(tmp_path)}
    assert (rows["R1-F1"]["review_disposition"], rows["R1-F2"]["review_disposition"]) == ("applied", "rejected")
    assert rows["R1-F2"]["rejection_reason"] == "inaccurate"
    assert ctx.carried_review_findings == []


def test_resume_refuses_approval_while_a_carried_finding_has_no_disposition(tmp_path: Path, halted_checkpoint: str):
    log: list[str] = []
    dispositions = [{"id": "R1-F2", "disposition": "rejected", "rejection_reason": "inaccurate"}]

    _ctx, res = _resume(halted_checkpoint, log, _resolved(dispositions, "a" * 40))

    # why: a finding still without an outcome goes back to the resolver until the no-convergence guard trips.
    assert log == ["resolver:R1-F1,R1-F2", *["ensemble", "resolver:R1-F1"] * 2, "ensemble"]
    assert res["ensemble_verdict"] != str(ConsensusVerdict.APPROVED)
    assert res["consensus"]["approved"] is False
    assert any("R1-F1" in blocker for blocker in res["consensus_blockers"])


def test_a_carried_finding_left_open_on_an_unchanged_head_halts_as_no_convergence(
    tmp_path: Path, halted_checkpoint: str
):
    log: list[str] = []
    dispositions = [{"id": "R1-F2", "disposition": "rejected", "rejection_reason": "inaccurate"}]

    ctx, res = _resume(halted_checkpoint, log, _resolved(dispositions, "a" * 40))

    assert res["no_convergence"] is True
    assert log.count("ensemble") == 3
    assert ctx.carried_review_findings == []
    rows = {r["review_finding_ids"][0]: r for r in _res_rows(tmp_path) if r["review_disposition"]}
    assert (rows["R1-F1"]["review_disposition"], rows["R1-F1"]["rejection_reason"]) == ("rejected", "no-convergence")
    assert rows["R1-F2"]["rejection_reason"] == "inaccurate"


def test_resume_whose_resolver_fails_again_starts_no_review_iteration(tmp_path: Path, halted_checkpoint: str):
    log: list[str] = []

    ctx, res = _resume(halted_checkpoint, log, _session_limit)

    assert log == ["resolver:R1-F1,R1-F2"]
    assert res["consensus"]["approved"] is False
    assert "session limit" in res["resolver_error"]
    assert _res_rows(tmp_path) == []
    assert [f["id"] for f in ctx.carried_review_findings] == ["R1-F1", "R1-F2"]


def test_resume_with_an_unpushed_applied_fix_records_it_and_blocks_approval(tmp_path: Path, halted_checkpoint: str):
    log: list[str] = []
    dispositions = [
        {"id": "R1-F1", "disposition": "applied"},
        {"id": "R1-F2", "disposition": "rejected", "rejection_reason": "inaccurate"},
    ]

    ctx, res = _resume(halted_checkpoint, log, _resolved(dispositions, ""))

    # why: a finding still without an outcome goes back to the resolver on every later iteration.
    assert log[:3] == ["resolver:R1-F1,R1-F2", "ensemble", "resolver:R1-F1"]
    assert set(log[2:]) == {"ensemble", "resolver:R1-F1"}
    assert res["consensus"]["approved"] is False
    assert any(
        b.endswith("without a resolver outcome (applied with a commit or rejected with a reason): R1-F1")
        for b in res["consensus_blockers"]
    )
    # why: a fix never pushed leaves the head unchanged, so the no-convergence guard rejects the carried finding.
    assert res["no_convergence"] is True
    rows = {r["review_finding_ids"][0]: r.get("rejection_reason") for r in _res_rows(tmp_path)}
    assert rows == {"R1-F1": "no-convergence", "R1-F2": "inaccurate"}
    assert ctx.carried_review_findings == []


def test_a_failed_carried_pass_records_its_rejected_attempt(tmp_path: Path, halted_checkpoint: str):
    def tests_failed(_findings: list[dict]) -> tuple[int, dict]:
        return 1, {
            "resolver": True,
            "dispositions": [],
            "error": "pre-push tests failed",
            "exit_code": 1,
            "failed_tests": ["tests/test_x.py::test_y"],
            "test_summary": "1 failed",
        }

    with patch("sandbox_executor.flow.review.loop._record_rejected_attempt") as record:
        _ctx, res = _resume(halted_checkpoint, [], tests_failed)

    assert res["resolver_error"]
    record.assert_called_once()


def test_a_dry_run_keeps_the_carried_findings_unchanged(tmp_path: Path, halted_checkpoint: str):
    ctx = load_checkpoint(halted_checkpoint).context
    ctx.dry_run = True
    log: list[str] = []
    ctx.dispatcher = _dispatcher(log, False, _session_limit)

    res = _engine_pass(ctx)

    assert "open_findings" not in res
    assert log == []
    assert [f["id"] for f in ctx.carried_review_findings] == ["R1-F1", "R1-F2"]
