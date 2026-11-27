import hashlib
import json
import re
import subprocess

import pytest
from sandbox_executor import review_report
from sandbox_executor.review_ensemble import (
    ParsedTrailer,
    ReviewCounts,
    ReviewerResult,
    ReviewerVerdict,
    ReviewFinding,
    deserialize_reviewer_result,
    render_review_trailer,
    serialize_reviewer_result,
)
from sandbox_executor.review_report import (
    CLOSING_NOTICE,
    ROLES_CLOSE,
    ROLES_OPEN,
    build_on_disk_review_report,
    build_pr_review_consensus_report,
    build_review_consensus_report,
    collect_review_data,
    parse_reviewer_detail,
    resolve_report_title,
    reviewer_detail_payload,
    with_reviewer_detail,
)
from sandbox_executor.review_target import ReviewTarget

from tests.review_fakes import failed_reviewer

# why: the posted shape is read from the shipped template, so a prompt or generator change not made to both fails here
from tests.test_prompt_templates import MARKDOWN_TEMPLATE, posted_shape_block

COMMIT = "abc1234"
FORMER_FALLBACK_TITLE = "Audit Remediation and Flow Review Hardening"
SECRET = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
APP_FINDING = ReviewFinding("important", "src/app.py", 12, "The handler drops the error.")
DOC_FINDING = ReviewFinding("nit", "docs/guide.md", None, "Typo in the heading.")


def _parsed(*findings: ReviewFinding) -> ParsedTrailer:
    if not findings:
        return ParsedTrailer(ReviewerVerdict.APPROVED, ReviewCounts())
    counts = ReviewCounts(**{name: sum(1 for f in findings if f.severity == name) for name in ("important", "nit")})
    return ParsedTrailer(ReviewerVerdict.CHANGES_REQUESTED, counts, findings)


def _report_text(parsed: ParsedTrailer, block: object | None = None, body: str = "# Review\n\nFull review.") -> str:
    data = "" if block is None else f"{ROLES_OPEN}\n{json.dumps(block)}\n{ROLES_CLOSE}\n"
    return f"{body}\n\n{data}{render_review_trailer(parsed)}"


def _reviewer(index: int, *findings: ReviewFinding, block: object | None = None, **kwargs) -> ReviewerResult:
    parsed = _parsed(*findings)
    text = _report_text(parsed, block, **kwargs)
    report = f"tmp/review/iter-1/reviewer-{index}/report.md"
    return ReviewerResult.completed(index, f"agent-{index}", f"model-{index}", report, text, parsed)


CHANGES_BLOCK = {
    "roles": [
        {"role": "Security", "verdict": "PASS", "review": "Checked reviewer text redaction; nothing leaks."},
        {"role": "Testing", "verdict": "CHANGES", "review": "The error path has no test."},
    ],
    "findings": [
        {"id": "SEC-1", "recommendation": "Return the error to the caller."},
        {"id": "DOC-1", "recommendation": "Fix the spelling."},
    ],
    "verified": [{"claim": "Secrets are redacted", "evidence": "tests/test_redaction.py"}],
}
APPROVED_BLOCK = {
    "roles": [{"role": "Engineering", "verdict": "PASS", "review": "The renderer stays under the complexity limit."}],
    "findings": [],
    "verified": [
        {"claim": "Failed reviewers cast no vote", "evidence": "apps/sandbox-executor/tests/test_review_report.py"},
        {"claim": "Everything else is fine", "evidence": ""},
    ],
}


def changes_reviewers() -> list[ReviewerResult]:
    return [
        _reviewer(1, APP_FINDING, DOC_FINDING, block=CHANGES_BLOCK),
        _reviewer(2, APP_FINDING),
        failed_reviewer(3, f"runner crashed with token {SECRET}"),
    ]


def approved_reviewers() -> list[ReviewerResult]:
    return [_reviewer(1, block=APPROVED_BLOCK), _reviewer(2), _reviewer(3)]


def _consensus(**overrides):
    data = {
        "approved": False,
        "status": "changes_requested",
        "ensemble_verdict": "CHANGES_REQUESTED",
        "iterations": 2,
        "ensemble_size": 3,
        "target_commit": COMMIT,
        "base_branch": "main",
        "repo_name": "acme/widgets",
        "intent_data": {},
        "agent": "claude-agent",
        "model": "model-x",
        "ci_checks": {"total": 2, "passed": 2, "failed": 0, "pending": 0, "source": "github_cli", "all_passed": True},
        "mergeability": {"is_dirty": False, "merge_state_status": "CLEAN", "mergeable": "MERGEABLE"},
    }
    data.update(overrides)
    return data


def _approved(**overrides):
    return _consensus(approved=True, status="approved", ensemble_verdict="APPROVED", **overrides)


def _report(reviewers, title=None, **overrides) -> str:
    return build_pr_review_consensus_report("/nonexistent", "42", _consensus(**overrides), reviewers, title)


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _unpadded_table_header(line: str) -> str:
    return "| " + " | ".join(_cells(line)) + " |"


def _text_before_first_placeholder(line: str) -> str:
    return line.split("{", 1)[0]


def _is_example_bullet(line: str) -> bool:
    return line.startswith("- ") and "{" not in line


def shape_markers() -> list[str]:
    lines = posted_shape_block(MARKDOWN_TEMPLATE).splitlines()
    markers = []
    for number, line in enumerate(lines):
        following = lines[number + 1] if number + 1 < len(lines) else ""
        if line.startswith("|"):
            if following.startswith("| -"):
                markers.append(_unpadded_table_header(line))
            continue
        prefix = _text_before_first_placeholder(line)
        if prefix.strip() and not _is_example_bullet(line):
            markers.append(prefix)
    return markers


def _markers_without_the_other_verdict(markers: list[str], approved: bool) -> list[str]:
    other = ("CHANGES REQUESTED", "| ID |") if approved else ("APPROVED (", "**Verified:**")
    return [marker for marker in markers if not any(token in marker for token in other)]


def assert_in_order(body: str, markers: list[str]) -> None:
    position = 0
    for marker in markers:
        found = body.find(marker, position)
        assert found >= 0, f"marker {marker!r} missing or out of order after position {position}"
        position = found + len(marker)


def test_template_shape_yields_the_seven_parts():
    markers = shape_markers()
    assert markers[0].startswith("## ✅ Ensemble Consensus Review — APPROVED (")
    assert markers[1].startswith("## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration ")
    for expected in (
        "**Role reviews:**",
        "| Role (activated) | Verdict | Review |",
        "**Verified:**",
        "| ID | Severity | File:Line | Reviewer | Finding | Recommendation |",
        "**Checks:** Mergeability ",
        "### 🗳️ Ensemble Review Breakdown",
    ):
        assert expected in markers
    assert markers[-1] == CLOSING_NOTICE


def test_changes_requested_report_follows_the_template_shape():
    body = _report(changes_reviewers())

    assert_in_order(body, _markers_without_the_other_verdict(shape_markers(), approved=False))
    assert body.startswith("## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration 2)")
    assert "**Verified:**" not in body
    assert body.endswith(CLOSING_NOTICE)


def test_approved_report_follows_the_template_shape():
    body = build_review_consensus_report("/nonexistent", "42", _approved(), approved_reviewers())

    assert_in_order(body, _markers_without_the_other_verdict(shape_markers(), approved=True))
    assert body.startswith("## ✅ Ensemble Consensus Review — APPROVED (3/3)")
    assert "| ID |" not in body
    assert body.endswith(CLOSING_NOTICE)


FINGERPRINT = "pf1:0123456789abcdef0123456789abcdef01234567"
CARRIED_TO = "def5678def5678def5678def5678def5678def56"


def _stamp_line(body: str) -> str:
    return next(line for line in body.splitlines() if line.startswith("**Covers head**"))


def test_approval_stamp_without_a_fingerprint_is_unchanged():
    body = build_review_consensus_report("/nonexistent", "42", _approved(), approved_reviewers())

    assert _stamp_line(body) == f"**Covers head** `{COMMIT}`; CI at that commit: 2 of 2 checks passed."


def test_approval_stamp_names_the_head_the_fingerprint_and_the_ci_result():
    consensus = _approved(approval_fingerprint=FINGERPRINT)
    body = build_review_consensus_report("/nonexistent", "42", consensus, approved_reviewers())

    assert _stamp_line(body) == (
        f"**Covers head** `{COMMIT}`; change fingerprint `pf1:0123456789ab`; CI at that commit: 2 of 2 checks passed."
    )


def test_carried_approval_stamp_names_both_heads_and_never_the_old_heads_ci():
    not_measured = {"measured": False, "reason": "CI on merged head not yet measured"}
    consensus = _approved(
        approval_fingerprint=FINGERPRINT, carried_from=COMMIT, carried_to=CARRIED_TO, ci_checks=not_measured
    )
    body = build_review_consensus_report("/nonexistent", "42", consensus, approved_reviewers())

    assert _stamp_line(body) == (
        "**Covers head** `def5678`; change fingerprint `pf1:0123456789ab`; carried over from "
        f"`{COMMIT}` (same change); CI at that commit: not measured."
    )


def test_evaluation_paragraph_names_commit_base_counts_ci_and_mergeability():
    body = _report(changes_reviewers(), title="Widget fix")
    paragraph = body.split("\n\n")[1]

    assert paragraph.startswith("Reviewed [Widget fix](https://github.com/acme/widgets/pull/42) at")
    assert f"`{COMMIT}`" in paragraph and "against `main`" in paragraph
    assert "0 critical, 1 important and 1 nit findings" in paragraph
    assert "CI passing" in paragraph and "mergeability CLEAN" in paragraph


def test_role_table_lists_only_roles_reviewers_declared():
    body = _report(changes_reviewers())

    assert "| Security | PASS | Checked reviewer text redaction; nothing leaks. |" in body
    assert "| Testing | CHANGES | The error path has no test. |" in body
    for absent in ("Frontend", "Product Owner", "DevOps", "Persona"):
        assert absent not in body


def test_without_any_data_block_no_role_row_is_invented():
    body = _report([_reviewer(1, APP_FINDING), _reviewer(2)])

    assert "**Role reviews:** No reviewer reported an activated role." in body
    assert "| Role (activated) |" not in body


def test_a_role_any_reviewer_marked_changes_renders_changes():
    first = {"roles": [{"role": "Security", "verdict": "PASS", "review": "Fine."}]}
    second = {"roles": [{"role": "security", "verdict": "CHANGES", "review": "Token leaks."}]}
    data = collect_review_data([_reviewer(1, block=first), _reviewer(2, block=second)])

    assert [(role.role, role.verdict, role.review) for role in data.roles] == [("Security", "CHANGES", "Token leaks.")]


def test_a_finding_several_reviewers_raised_renders_once_with_every_contributor():
    body = _report(changes_reviewers())

    rows = [line for line in body.splitlines() if "src/app.py:12" in line]
    assert rows == [
        "| SEC-1 | important | src/app.py:12 | 1, 2 | [Flagged by Reviewer 1 & 2] The handler drops the error. "
        "| Return the error to the caller. |"
    ]
    assert "| DOC-1 | nit | docs/guide.md | 1 | Typo in the heading. | Fix the spelling. |" in body


def test_distinct_findings_at_one_location_stay_distinct_rows():
    other = ReviewFinding("important", "src/app.py", 12, "The retry loop never terminates.")
    data = collect_review_data([_reviewer(1, APP_FINDING), _reviewer(2, other)])

    assert [(row.summary, row.reviewers) for row in data.findings] == [
        ("The handler drops the error.", (1,)),
        ("The retry loop never terminates.", (2,)),
    ]


def test_colliding_block_ids_across_reviewers_are_disambiguated():
    block = {"findings": [{"id": "F-1", "recommendation": "x"}]}
    reviewers = [_reviewer(1, APP_FINDING, block=block), _reviewer(2, DOC_FINDING, block=block)]
    data = collect_review_data(reviewers)

    ids = [row.id for row in data.findings]
    assert ids == ["F-1", "R2-F2"]
    assert len(set(ids)) == len(ids)


def test_the_same_finding_raised_by_several_reviewers_keeps_one_shared_id():
    block = {"findings": [{"id": "F-1", "recommendation": "x"}]}
    data = collect_review_data([_reviewer(1, APP_FINDING, block=block), _reviewer(2, APP_FINDING, block=block)])

    assert [(row.id, row.reviewers) for row in data.findings] == [("F-1", (1, 2))]


def test_a_reviewer_without_a_block_gets_generated_ids_and_no_recommendation():
    body = _report([_reviewer(2, APP_FINDING)])

    assert "| R2-F1 | important | src/app.py:12 | 2 | The handler drops the error. | not supplied |" in body


@pytest.mark.parametrize(
    "block",
    [
        {"findings": [{"id": "A-1", "recommendation": "x"}, {"id": "A-2", "recommendation": "y"}]},
        {"findings": [{"id": "has spaces and is far too long to be an id", "recommendation": ""}]},
    ],
    ids=["misaligned", "bad-id"],
)
def test_unusable_block_findings_fall_back_to_generated_ids(block):
    rows = parse_reviewer_detail(_reviewer(1, APP_FINDING, block=block)).findings

    assert [(row.id, row.recommendation) for row in rows] == [("R1-F1", "not supplied")]


@pytest.mark.parametrize(
    "text",
    [
        f"{ROLES_OPEN}\nnot json\n{ROLES_CLOSE}\n",
        f"{ROLES_OPEN}\n[1, 2]\n{ROLES_CLOSE}\n",
        f"{ROLES_OPEN}\n{json.dumps(CHANGES_BLOCK)}\n{ROLES_CLOSE}\nMore prose after the block.\n",
        f"{ROLES_OPEN}\n{json.dumps({**CHANGES_BLOCK, 'pad': 'x' * 20_001})}\n{ROLES_CLOSE}\n",
    ],
    ids=["malformed", "not-an-object", "not-before-trailer", "oversized"],
)
def test_a_block_that_is_malformed_or_misplaced_yields_nothing(text):
    parsed = _parsed(APP_FINDING, DOC_FINDING)
    result = ReviewerResult.completed(1, "a", "m", "r", f"# Review\n\n{text}{render_review_trailer(parsed)}", parsed)
    detail = parse_reviewer_detail(result)

    assert detail.roles == () and detail.verified == ()
    assert [row.id for row in detail.findings] == ["R1-F1", "R1-F2"]


def test_a_role_with_an_unknown_verdict_is_dropped():
    block = {"roles": [{"role": "Security", "verdict": "LGTM"}, {"role": "", "verdict": "PASS"}, "Testing"]}

    assert parse_reviewer_detail(_reviewer(1, block=block)).roles == ()


def test_a_failed_reviewer_appears_only_as_failed_in_the_breakdown():
    body = _report(changes_reviewers())

    assert "- **Reviewer 3** (`fake-agent / fake-model`): `FAILED` (runner crashed with token" in body
    assert SECRET not in body
    assert parse_reviewer_detail(failed_reviewer(3)).roles == ()
    assert "- **Reviewer 1** (`agent-1 / model-1`): `CHANGES_REQUESTED` (critical: 0, important: 1, nit: 1)" in body
    assert "- **Ensemble Consensus Verdict**: `CHANGES_REQUESTED`" in body
    assert "Reviewed by `claude-agent` (3-Agent Ensemble) · Model `model-x`" in body


def test_review_data_survives_the_container_transport():
    reviewers = changes_reviewers()
    transported = []
    for result in reviewers:
        item = serialize_reviewer_result(result)
        restored = deserialize_reviewer_result(json.loads(json.dumps(item)))
        # why: the strict container transport drops the report text, so the detail payload must carry the data
        assert not restored.report_text
        transported.append(with_reviewer_detail(restored, reviewer_detail_payload(result)))

    assert collect_review_data(transported) == collect_review_data(reviewers)


@pytest.mark.parametrize("verdict", [None, "FAILED", "CHANGES_REQUESTED", "approved"])
def test_heading_follows_the_ensemble_verdict_alone(verdict):
    body = _report(approved_reviewers(), approved=True, status="approved", ensemble_verdict=verdict)

    assert body.startswith("## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration 2)")
    assert "APPROVED (" not in body


def test_two_approvals_and_one_open_nit_render_as_two_of_three_with_the_nit_listed():
    reviewers = [_reviewer(1), _reviewer(2), _reviewer(3, DOC_FINDING)]
    (nit_row,) = collect_review_data(reviewers).findings

    body = _report(reviewers)
    heading = body.splitlines()[0]

    assert heading == "## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration 2) — 2/3 approved"
    assert "APPROVED (" not in body
    assert "0 critical, 0 important and 1 nit findings" in body
    assert f"| {nit_row.id} | nit | docs/guide.md | 3 | Typo in the heading. |" in body
    assert "- **Reviewer 3** (`agent-3 / model-3`): `CHANGES_REQUESTED` (critical: 0, important: 0, nit: 1)" in body


def test_approval_fraction_counts_only_completed_approving_reviewers():
    body = _report([_reviewer(1), failed_reviewer(2), _reviewer(3, APP_FINDING)])

    assert body.splitlines()[0].endswith("— 1/3 approved")


# why: this report was formerly headed APPROVED (1/3); an open finding now forbids that heading
def test_an_approved_verdict_with_an_open_finding_renders_as_changes_requested():
    body = build_review_consensus_report("/nonexistent", "42", _approved(), [_reviewer(1), _reviewer(2, DOC_FINDING)])

    assert body.splitlines()[0].endswith("CHANGES REQUESTED (Iteration 2) — 1/3 approved")
    assert "APPROVED (" not in body
    assert "Typo in the heading." in body


# why: reproduces the incident of APPROVED, APPROVED and a critical CHANGES_REQUESTED with Security at CHANGES
def _failing_shape() -> list[ReviewerResult]:
    critical = ReviewFinding("critical", "src/sandbox/mounts.py", 40, "Bind mount exposes the host Docker socket.")
    parsed = ParsedTrailer(ReviewerVerdict.CHANGES_REQUESTED, ReviewCounts(critical=1), (critical,))
    block = {"roles": [{"role": "Security", "verdict": "CHANGES", "review": "Mount escapes the sandbox."}]}
    third = ReviewerResult.completed(3, "agent-3", "model-3", "r3.md", _report_text(parsed, block), parsed)
    return [_reviewer(1), _reviewer(2), third]


def test_the_failing_shape_is_never_headed_approved_even_with_an_approved_ensemble_verdict():
    body = build_review_consensus_report("/nonexistent", "42", _approved(), _failing_shape())

    assert body.splitlines()[0] == "## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration 2) — 2/3 approved"
    assert "APPROVED (" not in body
    assert "- **Reviewer 3** (`agent-3 / model-3`): `CHANGES_REQUESTED` (critical: 1, important: 0, nit: 0)" in body
    assert "| Security | CHANGES |" in body


def test_a_role_at_changes_forbids_the_approved_heading_when_every_reviewer_is_clean():
    block = {"roles": [{"role": "Security", "verdict": "CHANGES", "review": "Mount escapes the sandbox."}]}
    reviewers = [_reviewer(1), _reviewer(2), _reviewer(3, block=block)]

    body = build_review_consensus_report("/nonexistent", "42", _approved(), reviewers)

    assert body.splitlines()[0].endswith("— 3/3 approved")
    assert "APPROVED (" not in body
    assert review_report.report_approves(_approved(), reviewers) is False
    assert review_report.report_approves(_approved(), approved_reviewers()) is True


def test_an_approved_finding_does_not_count_as_a_clean_approval():
    noisy = ReviewerResult.completed(
        2,
        "agent-2",
        "model-2",
        "r2.md",
        "",
        ParsedTrailer(ReviewerVerdict.APPROVED, ReviewCounts(nit=1), (DOC_FINDING,)),
    )

    body = _report([_reviewer(1), noisy, _reviewer(3)])

    assert body.splitlines()[0].endswith("— 2/3 approved")


def test_an_approval_asserts_only_evidenced_claims():
    body = build_review_consensus_report("/nonexistent", "42", _approved(), approved_reviewers())

    assert "- Failed reviewers cast no vote — `apps/sandbox-executor/tests/test_review_report.py`" in body
    assert "Everything else is fine" not in body


def test_an_approval_without_evidenced_claims_has_no_verified_bullet():
    body = build_review_consensus_report("/nonexistent", "42", _approved(), [_reviewer(1), _reviewer(2)])

    section = body.split("**Verified:**", 1)[1].split("**Checks:**", 1)[0]
    assert section.strip() == "No reviewer supplied an evidenced claim."
    assert not [line for line in section.splitlines() if line.startswith("- ")]


@pytest.mark.parametrize(
    "ci_checks",
    [{"measured": False, "all_passed": False}, {}, {"total": 0, "passed": 0, "source": "github_cli"}],
    ids=["unmeasured", "missing", "no-checks"],
)
def test_unmeasured_ci_is_never_reported_as_a_pass(ci_checks):
    body = _report([_reviewer(1)], ci_checks=ci_checks)

    assert "· CI unmeasured" in body
    assert "CI unmeasured; mergeability" in body
    assert "passed," not in body


def test_pending_ci_after_a_spent_wait_names_the_wait_in_the_evaluation():
    ci = {"total": 2, "passed": 1, "failed": 0, "pending": 1, "source": "github_cli"}
    wait = {"state": "pending", "budget_seconds": 1800.0, "waited_seconds": 1800.0, "pending_checks": ["unit"]}

    assert "CI pending after a 1800 s wait; mergeability" in _report([], ci_checks=ci, ci_wait=wait)
    assert "CI pending; mergeability" in _report([], ci_checks=ci)


def test_measured_ci_renders_passed_total_failing_and_pending():
    ci = {"total": 5, "passed": 3, "failed": 1, "pending": 1, "source": "github_cli"}

    assert "**Checks:** Mergeability CLEAN · CI 3/5 passed, 1 failing, 1 pending" in _report([], ci_checks=ci)


@pytest.mark.parametrize(
    ("mergeability", "note", "word"),
    [
        ({"is_dirty": False, "merge_state_status": "CLEAN"}, None, "CLEAN"),
        ({"is_dirty": True, "merge_state_status": "DIRTY"}, None, "DIRTY"),
        ({"is_dirty": True, "merge_state_status": "DIRTY", "mergeable": "CONFLICTING"}, None, "CONFLICTING"),
        ({"merge_state_status": "MISSING_REF"}, "cannot evaluate mergeability for ref x", "MISSING_REF"),
        ({"merge_state_status": "UNKNOWN", "mergeable": "UNKNOWN"}, None, "UNMEASURED"),
        ({"merge_state_status": "BLOCKED"}, None, "UNMEASURED"),
        (None, None, "UNMEASURED"),
    ],
)
def test_mergeability_renders_the_probe_word(mergeability, note, word):
    body = _report([], mergeability=mergeability, mergeability_note=note)

    assert f"**Checks:** Mergeability {word} · " in body
    assert f"mergeability {word}" in body
    if note:
        assert f"mergeability MISSING_REF ({note})." in body


def test_untrusted_block_text_is_escaped_redacted_and_bounded():
    hostile = f"a | b `code` <script>x</script>\n## heading {SECRET} " + "word " * 60
    block = {
        "roles": [{"role": "Sec|urity <b>", "verdict": "pass", "review": hostile}],
        "findings": [{"id": "S-1", "recommendation": hostile}],
    }
    body = _report([_reviewer(1, APP_FINDING, block=block)])

    row = next(line for line in body.splitlines() if line.startswith("| Sec"))
    assert row.startswith("| Sec\\|urity &lt;b&gt; | PASS | a \\| b 'code' &lt;script&gt;x&lt;/script&gt; ## heading")
    assert len(row.split(" | ")[2].split()) <= 26
    assert SECRET not in body
    assert "<script>" not in body and "\n## heading" not in body
    finding_row = next(line for line in body.splitlines() if line.startswith("| S-1 "))
    assert len(finding_row) < 700


def test_untrusted_finding_text_cannot_break_out_of_its_row():
    finding = ReviewFinding("nit", "a`b|c.py", None, "line one\n## injected heading <script>`x`</script>")
    body = _report([_reviewer(1, finding)])

    row = next(line for line in body.splitlines() if line.startswith("| R1-F1 "))
    assert row == (
        "| R1-F1 | nit | a'b\\|c.py | 1 | line one ## injected heading &lt;script&gt;'x'&lt;/script&gt; "
        "| not supplied |"
    )
    assert "\n## injected" not in body


def test_the_on_disk_trailer_never_appears_in_a_posted_body():
    reviewers = changes_reviewers()
    body = _report(reviewers)
    on_disk = build_on_disk_review_report(body, _consensus(), reviewers, "tmp/review/r.md")

    for key in ("VERDICT:", "CI:", "REPORT:", "CRITICAL:", "IMPORTANT:", "NIT:", "FINDINGS:"):
        assert not [line for line in body.splitlines() if line.startswith(key)]
    assert on_disk.startswith(body)
    assert on_disk.rstrip().splitlines()[-7:] == [
        "VERDICT: CHANGES_REQUESTED",
        "CI: PASS",
        "REPORT: tmp/review/r.md",
        "CRITICAL: 0",
        "IMPORTANT: 1",
        "NIT: 1",
        "FINDINGS: 2",
    ]


@pytest.mark.parametrize("verdict", ["FAILED", ""], ids=["failed", "missing"])
def test_the_trailer_fails_closed_on_a_failed_or_missing_ensemble_verdict(verdict):
    reviewers = changes_reviewers()
    on_disk = build_on_disk_review_report("body", _consensus(ensemble_verdict=verdict), reviewers, "tmp/review/r.md")

    trailer_lines = on_disk.rstrip().splitlines()
    assert "VERDICT: CHANGES_REQUESTED" in trailer_lines
    assert not [line for line in trailer_lines if line.startswith("VERDICT: COMMENT")]


def test_the_alias_keeps_its_positional_and_keyword_parameters():
    reviewers = changes_reviewers()
    positional = build_pr_review_consensus_report("/nonexistent", "42", _consensus(), reviewers, "T")
    keyword = build_pr_review_consensus_report(
        repo_dir="/nonexistent", target="42", consensus_data=_consensus(), reviewers=reviewers, title="T"
    )

    assert positional == keyword == build_review_consensus_report("/nonexistent", "42", _consensus(), reviewers, "T")
    assert build_pr_review_consensus_report is build_review_consensus_report


def test_report_has_no_commit_derived_reviewer_ids_or_fabricated_sections():
    body = _report([_reviewer(i) for i in range(1, 4)])

    hashed = {hashlib.sha256(f"{COMMIT}-reviewer-{i}".encode()).hexdigest()[:8] for i in range(1, 10)}
    assert not hashed & set(re.findall(r"[0-9a-f]{8}", body))
    assert FORMER_FALLBACK_TITLE not in body
    assert "-Agent Independent" not in body
    assert "Persona" not in body
    assert "Verified Positive Findings" not in body


@pytest.mark.parametrize(
    ("intent", "target", "expected"),
    [
        (
            {"title": "Intent title"},
            ReviewTarget("42", "feat/x", "sha", pr_number=42, title="PR title"),
            "Intent title",
        ),
        ({}, ReviewTarget("42", "feat/x", "sha", pr_number=42, title="PR title"), "PR title"),
        (
            {"slug": "intent-slug"},
            ReviewTarget("42", "feat/x", "sha", pr_number=42, title="PR title"),
            "intent-slug",
        ),
        ({"title": "Intent title", "slug": "intent-slug"}, None, "Intent title"),
        ({"title": "  "}, ReviewTarget("42", "feat/x", "sha", pr_number=42), "PR #42"),
        ({}, ReviewTarget("feat/x", "feat/x", "sha"), "feat/x"),
        (None, None, "feat/raw"),
    ],
)
def test_title_is_intent_title_then_slug_then_pr_title_then_pr_number_or_branch(intent, target, expected):
    title = resolve_report_title(intent, target, "feat/raw")

    assert title == expected
    assert title != FORMER_FALLBACK_TITLE


def test_report_uses_the_resolved_title_and_falls_back_to_the_target_without_one():
    assert "\n\nReviewed [PR title](https://github.com/acme/widgets/pull/42) at" in _report([], title="PR title")
    assert "\n\nReviewed [acme/widgets#42](https://github.com/acme/widgets/pull/42) at" in _report([])


HIERARCHICAL_REF = "/".join(["I-1795167569-" + "x" * 40, "P-1795167586-" + "y" * 40, "E-1795178363-CLA-CO5.5", "_"])


def test_evaluation_names_a_long_hierarchical_ref_at_most_once_and_uses_the_intent_slug():
    assert len(HIERARCHICAL_REF) >= 120
    pr_url = "https://github.com/acme/widgets/pull/42"
    consensus = _consensus(intent_data={"slug": "review-report-ci-log-redaction"}, pr_url=pr_url, pr_number=42)
    body = build_pr_review_consensus_report("/nonexistent", HIERARCHICAL_REF, consensus, [])
    paragraph = body.split("\n\n")[1]

    assert paragraph.count(HIERARCHICAL_REF) <= 1
    assert paragraph.startswith(f"Reviewed [review-report-ci-log-redaction]({pr_url}) at")


def test_evaluation_without_a_label_names_a_branch_link_once():
    body = build_pr_review_consensus_report("/nonexistent", "feat/raw", _consensus(), [])
    paragraph = body.split("\n\n")[1]

    assert paragraph.startswith("Reviewed `feat/raw` at")
    assert paragraph.count("feat/raw") == 1


def test_evaluation_shows_an_eight_character_commit():
    full_sha = "".join(f"{n:x}" for n in range(16)) * 2 + "abcdef12"
    assert len(full_sha) == 40
    paragraph = _report([], target_commit=full_sha).split("\n\n")[1]

    assert f"at `{full_sha[:8]}` against" in paragraph
    assert full_sha[:9] not in paragraph


def test_evaluation_keeps_a_non_hex_commit_and_asks_git_for_eight_characters(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    calls = []

    def fake_git(args, cwd=None, check=False):
        calls.append(args)
        return subprocess.CompletedProcess(["git", *args], 1, stdout="", stderr="")

    monkeypatch.setattr(review_report, "run_git", fake_git)
    body = build_pr_review_consensus_report(str(tmp_path), "42", _consensus(target_commit="HEAD"), [])

    assert "at `HEAD` against" in body
    assert ["rev-parse", "--short=8", "HEAD"] in calls


@pytest.mark.parametrize("target", ["42", "feat/raw"])
def test_report_without_a_github_origin_renders_the_target_without_a_link(tmp_path, monkeypatch, target):
    (tmp_path / ".git").mkdir()

    def fake_git(args, cwd=None, check=False):
        out = "https://gitlab.example.com/acme/widgets.git" if args[:2] == ["remote", "get-url"] else ""
        return subprocess.CompletedProcess(["git", *args], 0, stdout=out, stderr="")

    monkeypatch.setattr(review_report, "run_git", fake_git)
    body = build_pr_review_consensus_report(str(tmp_path), target, _consensus(repo_name=""), [])

    assert f"\n\nReviewed `{target}` at `{COMMIT}`" in body
    assert "](" not in body
    assert "github.com" not in body
    assert "Holon-Agentic-Coder" not in body


NIT_ROW = review_report.FindingRow("R1-F1", "nit", "flow/calibrate_rerun.py", 40, (1,), "A nit.", "Fix it.")


def test_a_nit_without_a_resolver_entry_stays_open():
    (disp,) = review_report.deserialize_finding_dispositions([], [NIT_ROW])

    assert (disp.disposition, disp.rejection_reason) == ("open", None)


def test_a_nit_rejected_without_a_reason_stays_open():
    (disp,) = review_report.deserialize_finding_dispositions([{"id": "R1-F1", "disposition": "rejected"}], [NIT_ROW])

    assert (disp.disposition, disp.rejection_reason) == ("open", None)
