"""The Stage 4 consensus comment follows the template's posted shape and renders only real reviewer data.

The shape tests read the two fenced blocks under ``## Posted review shape`` in the shipped prompt, so a drift between
the prompt the reviewers are given and the body the builder posts fails here. Fakes only: no runner, gh or remote.
"""

import hashlib
import json
import pathlib
import re

import pytest
from sandbox_executor.review_ensemble import (
    SEVERITIES,
    ParsedTrailer,
    ReviewCounts,
    ReviewerResult,
    ReviewerVerdict,
    ReviewFinding,
    parse_review_trailer,
    render_review_trailer,
    reviewer_report_rel,
)
from sandbox_executor.review_report import (
    CLOSING_NOTICE,
    DETAILS_CLOSE,
    DETAILS_OPEN,
    ReviewerDetails,
    RoleReview,
    VerifiedItem,
    build_pr_review_consensus_report,
    build_review_consensus_report,
    finding_id,
    parse_reviewer_details,
    render_reviewer_details,
    resolve_report_title,
)
from sandbox_executor.review_target import ReviewTarget

from tests.review_fakes import failed_reviewer

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
TEMPLATE = (REPO_ROOT / "holon-config" / "prompts" / "review_loop.template.md").read_text(encoding="utf-8")
COMMIT = "abc1234"
FORMER_FALLBACK_TITLE = "Audit Remediation and Flow Review Hardening"
PLACEHOLDER = re.compile(r"\{[a-z][a-z0-9_]*\}")
TRAILER_LINE = re.compile(r"^(VERDICT|CI|REPORT|CRITICAL|IMPORTANT|NIT|FINDINGS):", re.MULTILINE)

SHARED = ReviewFinding("important", "src/app.py", 12, "The handler swallows the error.")
SHARED_AGAIN = ReviewFinding("important", "./src/app.py", 12, "Errors are swallowed by the handler.")
NIT = ReviewFinding("nit", "docs/guide.md", None, "Typo in the heading.")
DETAILS_1 = {
    "roles": [
        {"role": "QA & Test Engineer", "verdict": "PASS", "review": "The new handler test covers the empty input."},
        {"role": "Security Architect", "verdict": "CHANGES", "review": "Errors are swallowed and never logged."},
    ],
    "recommendations": ["Re-raise the error after logging it."],
    "verified": [
        {"claim": "The greeting handles an empty name.", "evidence": "tests/test_app.py::test_greeting"},
        {"claim": "Everything else is fine.", "evidence": ""},
    ],
}
DETAILS_2 = {"roles": [{"role": "QA & Test Engineer", "verdict": "CHANGES", "review": ""}], "recommendations": []}


def posted_shape(label: str) -> str:
    """The fenced ``markdown`` block titled ``label`` under the template's ``## Posted review shape``."""
    section = TEMPLATE.split("\n## Posted review shape\n", 1)[1]
    blocks = dict(re.findall(r"^(Approved|Changes requested):\n\n```markdown\n(.*?)\n```$", section, re.S | re.M))
    return blocks[label]


def _normalise(text: str) -> str:
    """Collapse horizontal whitespace and table dash runs, so prettier padding cannot break the comparison."""
    return re.sub(r"[ \t]+", " ", re.sub(r"-{3,}", "---", text))


def _line_pattern(line: str) -> re.Pattern[str]:
    """A template line as a regex: literal text escaped, each ``{placeholder}`` any text on the same line."""
    return re.compile(r"[^\n]+?".join(re.escape(part) for part in PLACEHOLDER.split(_normalise(line.strip()))))


def assert_follows_shape(body: str, block: str) -> None:
    """Every non-empty template line appears in ``body`` in the template's order."""
    text, position = _normalise(body), 0
    for line in filter(None, (raw.strip() for raw in block.splitlines())):
        match = _line_pattern(line).search(text, position)
        assert match, f"template line {line!r} is missing from the body or out of order"
        position = match.end()


def _reviewer(index, findings=(), details=None, agent="fake-agent", model="fake-model"):
    """A completed reviewer whose report carries the details block (a mapping, or raw block text) before its trailer."""
    counts = ReviewCounts(**{name: sum(f.severity == name for f in findings) for name in SEVERITIES})
    verdict = ReviewerVerdict.CHANGES_REQUESTED if findings else ReviewerVerdict.APPROVED
    parsed = ParsedTrailer(verdict, counts, tuple(findings))
    block = details if isinstance(details, str) or details is None else json.dumps(details)
    text = render_review_trailer(parsed)
    if block is not None:
        text = f"# Review\n\n{DETAILS_OPEN}\n{block}\n{DETAILS_CLOSE}\n\n{text}\n"
    return ReviewerResult.completed(index, agent, model, reviewer_report_rel(1, index), text, parsed)


def _consensus(**overrides):
    data = {
        "approved": False,
        "status": "changes_requested",
        "ensemble_verdict": "CHANGES_REQUESTED",
        "iterations": 1,
        "ensemble_size": 3,
        "target_commit": COMMIT,
        "base_branch": "main",
        "repo_name": "acme/widgets",
        "intent_data": {},
        "ci_checks": {"total": 2, "passed": 2, "failed": 0, "pending": 0, "source": "github_cli", "all_passed": True},
        "mergeability": {"is_dirty": False, "mergeable": "MERGEABLE", "merge_state_status": "CLEAN"},
    }
    data.update(overrides)
    return data


def _approved(**overrides):
    return _consensus(approved=True, status="approved", ensemble_verdict="APPROVED", **overrides)


def changes_fixture():
    """Reviewers 1 and 3 raise the same finding; reviewer 3 has no details block."""
    return [_reviewer(1, [SHARED], DETAILS_1), _reviewer(2, [NIT], DETAILS_2), _reviewer(3, [SHARED_AGAIN])]


def approved_fixture():
    return [_reviewer(1, details=DETAILS_1 | {"recommendations": []}), _reviewer(2, details=DETAILS_2), _reviewer(3)]


def _report(reviewers, title=None, **overrides) -> str:
    return build_pr_review_consensus_report("/nonexistent", "42", _consensus(**overrides), reviewers, title)


def _lines(body: str, prefix: str) -> list[str]:
    return [line for line in body.splitlines() if line.startswith(prefix)]


@pytest.mark.parametrize(
    ("label", "consensus", "fixture"),
    [("Approved", _approved(), approved_fixture), ("Changes requested", _consensus(), changes_fixture)],
    ids=["approved", "changes-requested"],
)
def test_report_follows_the_posted_shape_in_the_template(label, consensus, fixture):
    body = build_review_consensus_report("/nonexistent", "42", consensus, fixture(), "Add greeting")

    assert_follows_shape(body, posted_shape(label))
    assert body.endswith(CLOSING_NOTICE)


def test_shape_guard_rejects_a_body_whose_sections_are_reordered():
    body = build_review_consensus_report("/nonexistent", "42", _consensus(), changes_fixture(), "Add greeting")
    checks = next(line for line in body.splitlines() if line.startswith("**Checks:**"))
    reordered = body.replace(checks + "\n", "").replace("**Role reviews:**", checks + "\n\n**Role reviews:**")

    with pytest.raises(AssertionError, match="Checks"):
        assert_follows_shape(reordered, posted_shape("Changes requested"))


def test_role_table_lists_only_activated_roles_and_merges_their_verdicts():
    body = _report(changes_fixture())

    rows = _lines(body, "| ")[2 : 2 + 2]
    assert rows == [
        "| QA & Test Engineer | CHANGES | The new handler test covers the empty input. |",
        "| Security Architect | CHANGES | Errors are swallowed and never logged. |",
    ]
    assert "Frontend" not in body and "Technical Writer" not in body
    assert body.count("| PASS |") == 0


def test_no_reported_roles_render_one_none_reported_row_instead_of_a_pass():
    body = _report([_reviewer(1), _reviewer(2)])

    assert "| none reported | — | No reviewer supplied role data. |" in body
    assert "| PASS |" not in body


def test_duplicate_finding_appears_once_with_its_contributors_and_ledger_id():
    body = _report(changes_fixture())

    assert "| ID | Severity | File:Line | Reviewer | Finding | Recommendation |" in body
    assert _lines(body, "| FIND-") == [
        "| FIND-R1-001-01 | Important | `src/app.py:12` | Reviewer 1 | The handler swallows the error. "
        "(raised by Reviewer 1, Reviewer 3) | Re-raise the error after logging it. |",
        "| FIND-R2-001-01 | Nit | `docs/guide.md` | Reviewer 2 | Typo in the heading. | not supplied |",
    ]
    assert "Errors are swallowed by the handler" not in body
    assert "0 Critical, 1 Important and 1 Nit findings" in body
    assert finding_id(3, 7, 2) == "FIND-R3-007-02"


def test_verified_bullets_need_evidence_and_nothing_constant_is_asserted():
    body = build_review_consensus_report("/nonexistent", "42", _approved(), approved_fixture())

    verified = body.split("**Verified:**", 1)[1].split("**Checks:**", 1)[0]
    assert [line for line in verified.splitlines() if line.startswith("- ")] == [
        "- The greeting handles an empty name. (evidence: `tests/test_app.py::test_greeting`)"
    ]
    assert "Everything else is fine." not in body

    bare = build_review_consensus_report("/nonexistent", "42", _approved(), [_reviewer(1), _reviewer(2)])
    assert "- No reviewer supplied evidenced verification." in bare
    assert _lines(bare.split("**Verified:**", 1)[1].split("**Checks:**", 1)[0], "- ") == [
        "- No reviewer supplied evidenced verification."
    ]


@pytest.mark.parametrize("verdict", [None, "FAILED", "CHANGES_REQUESTED", "approved", "unknown"])
def test_heading_follows_the_ensemble_verdict_alone(verdict):
    body = _report(approved_fixture(), approved=True, status="approved", ensemble_verdict=verdict)

    assert body.startswith("## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration 1)")
    assert "APPROVED (" not in body
    assert "**Verified:**" not in body


def test_approved_heading_counts_completed_reviewers_only():
    reviewers = [_reviewer(1), failed_reviewer(2, "runner crashed"), _reviewer(3)]
    body = build_review_consensus_report("/nonexistent", "42", _approved(), reviewers)

    assert body.startswith("## ✅ Ensemble Consensus Review — APPROVED (2/2)")


@pytest.mark.parametrize(
    ("mergeability", "expected"),
    [
        ({"merge_state_status": "CLEAN"}, "CLEAN"),
        ({"merge_state_status": "DIRTY", "is_dirty": True}, "DIRTY"),
        ({"mergeable": "CONFLICTING", "merge_state_status": "CLEAN"}, "CONFLICTING"),
        ({"merge_state_status": "MISSING_REF"}, "MISSING_REF"),
        ({"merge_state_status": "UNKNOWN"}, "unmeasured"),
        ({"merge_state_status": "BLOCKED"}, "unmeasured"),
        ({}, "unmeasured"),
    ],
)
def test_checks_line_renders_the_mergeability_probe_verbatim_or_unmeasured(mergeability, expected):
    body = _report([_reviewer(1)], mergeability=mergeability)

    assert f"**Checks:** mergeability `{expected}` · CI `2/2 passed, 0 failing, 0 pending`" in body
    assert f"; mergeability {expected}." in body


@pytest.mark.parametrize(
    "ci",
    [{"measured": False, "all_passed": False}, {}, {"all_passed": True, "source": "local_tests"}],
    ids=["unmeasured", "empty", "no-counts"],
)
def test_unmeasured_ci_is_never_reported_as_a_pass(ci):
    body = _report([_reviewer(1)], ci_checks=ci)

    assert "CI `unmeasured`" in body
    assert "; CI unmeasured;" in body
    assert "passed," not in body


def test_ci_counts_render_with_failing_and_pending():
    body = _report([_reviewer(1)], ci_checks={"total": 5, "passed": 3, "failed": 1, "pending": 1})

    assert "CI `3/5 passed, 1 failing, 1 pending`" in body


def test_failed_reviewer_is_shown_failed_with_its_redacted_reason_and_never_as_a_vote():
    secret = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    reviewers = [_reviewer(1, agent="claude-agent", model="model-one"), failed_reviewer(2, f"crashed with {secret}")]
    body = build_review_consensus_report("/nonexistent", "42", _approved(), reviewers)

    assert "- **Reviewer 1** (`claude-agent/model-one`): `APPROVED` (Critical: 0, Important: 0, Nit: 0)" in body
    assert "- **Reviewer 2** (`fake-agent/fake-model`): `FAILED` (crashed with" in body
    assert "- **Reviewer 3**: `FAILED` (no result returned)" in body
    assert secret not in body
    assert body.startswith("## ✅ Ensemble Consensus Review — APPROVED (1/1)")
    assert "> 🤖 **Reviewed by**: `claude-agent, fake-agent` · **Model**: `model-one, fake-model`" in body


def test_breakdown_names_each_reviewer_and_the_consensus_verdict():
    body = _report(changes_fixture())

    assert _lines(body, "- **Reviewer") == [
        "- **Reviewer 1** (`fake-agent/fake-model`): `CHANGES_REQUESTED` (Critical: 0, Important: 1, Nit: 0)",
        "- **Reviewer 2** (`fake-agent/fake-model`): `CHANGES_REQUESTED` (Critical: 0, Important: 0, Nit: 1)",
        "- **Reviewer 3** (`fake-agent/fake-model`): `CHANGES_REQUESTED` (Critical: 0, Important: 1, Nit: 0)",
    ]
    assert "- **Ensemble Consensus Verdict**: `CHANGES_REQUESTED`" in body
    assert "- **Ensemble Consensus Verdict**: `not decided`" in _report([_reviewer(1)], ensemble_verdict=None)


@pytest.mark.parametrize(
    "raw",
    [
        "{not json",
        "[]",
        json.dumps({"roles": [{"role": "QA", "verdict": "MAYBE", "review": "x"}], "recommendations": "x"}),
        json.dumps({"roles": [{"role": "QA", "verdict": "PASS", "review": "x" * 70_000}]}),
        "",
    ],
    ids=["malformed", "not-an-object", "bad-entries", "oversized", "empty"],
)
def test_malformed_or_oversized_details_yield_no_roles_and_no_recommendations(raw):
    reviewer = _reviewer(1, [SHARED], raw)
    body = _report([reviewer])

    assert parse_reviewer_details(reviewer.report_text) == ReviewerDetails()
    assert "| none reported | — | No reviewer supplied role data. |" in body
    assert _lines(body, "| FIND-R1-001-01 ")[0].endswith("| not supplied |")


def test_missing_details_block_still_counts_as_a_vote():
    reviewer = _reviewer(1, [SHARED])

    assert parse_reviewer_details(reviewer.report_text) == ReviewerDetails()
    assert "(Critical: 0, Important: 1, Nit: 0)" in _report([reviewer])


def test_details_parse_keeps_valid_entries_and_round_trips():
    details = parse_reviewer_details(_reviewer(1, [SHARED], DETAILS_1).report_text)

    assert details.roles[1] == RoleReview("Security Architect", "CHANGES", "Errors are swallowed and never logged.")
    assert details.recommendations == ("Re-raise the error after logging it.",)
    expected = VerifiedItem("The greeting handles an empty name.", "tests/test_app.py::test_greeting")
    assert details.verified == (expected,)
    assert parse_reviewer_details(render_reviewer_details(details)) == details


def test_trailer_parser_accepts_a_report_that_carries_a_details_block():
    reviewer = _reviewer(1, [SHARED], DETAILS_1)

    assert DETAILS_OPEN in reviewer.report_text
    assert parse_review_trailer(reviewer.report_text).findings == (SHARED,)


def test_untrusted_reviewer_text_cannot_break_out_of_its_cell():
    secret = "ghp_" + "Z9" * 18
    hostile = f"line one\n## injected <script>`x`</script> | col {secret}"
    finding = ReviewFinding("nit", "a`b|c.py", None, hostile)
    details = {
        "roles": [{"role": "Ro|le\n# x", "verdict": "PASS", "review": hostile}],
        "recommendations": [hostile],
        "verified": [{"claim": hostile, "evidence": "tests/test_x.py"}],
    }
    body = _report([_reviewer(1, [finding], details)])
    approved = build_review_consensus_report("/nonexistent", "42", _approved(), [_reviewer(1, details=details)])

    for text in (body, approved):
        assert "<script>" not in text
        assert "\n## injected" not in text
        assert secret not in text
    assert "| Ro\\|le # x | PASS |" in body
    finding_row = _lines(body, "| FIND-R1-001-01 ")[0]
    assert "`a'b\\|c.py`" in finding_row
    assert "&lt;script&gt;'x'&lt;/script&gt; \\| col" in finding_row
    assert finding_row.count(" | ") == 5


@pytest.mark.parametrize("fixture", [changes_fixture, approved_fixture])
def test_posted_body_never_carries_the_trailer_or_the_details_block(fixture):
    for consensus in (_consensus(), _approved()):
        body = build_review_consensus_report("/nonexistent", "42", consensus, fixture())

        assert not TRAILER_LINE.search(body)
        assert "holon-review-trailer" not in body
        assert "holon-review-details" not in body


def test_alias_and_keyword_parameters_keep_working():
    assert build_pr_review_consensus_report is build_review_consensus_report
    body = build_pr_review_consensus_report(
        repo_dir="/nonexistent", target="42", consensus_data=_consensus(), reviewers=changes_fixture(), title="T"
    )
    assert body == build_review_consensus_report("/nonexistent", "42", _consensus(), changes_fixture(), "T")
    assert body.startswith("## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration 1)")


def test_report_has_no_commit_derived_reviewer_ids_or_fabricated_sections():
    body = _report([_reviewer(i) for i in range(1, 4)])

    hashed = {hashlib.sha256(f"{COMMIT}-reviewer-{i}".encode()).hexdigest()[:8] for i in range(1, 10)}
    assert not hashed & set(re.findall(r"[0-9a-f]{8}", body))
    assert FORMER_FALLBACK_TITLE not in body
    assert "Persona" not in body
    assert "Bean 0034" not in body


@pytest.mark.parametrize(
    ("intent", "target", "expected"),
    [
        (
            {"title": "Intent title"},
            ReviewTarget("42", "feat/x", "sha", pr_number=42, title="PR title"),
            "Intent title",
        ),
        ({}, ReviewTarget("42", "feat/x", "sha", pr_number=42, title="PR title"), "PR title"),
        ({"title": "  "}, ReviewTarget("42", "feat/x", "sha", pr_number=42), "PR #42"),
        ({}, ReviewTarget("feat/x", "feat/x", "sha"), "feat/x"),
        (None, None, "feat/raw"),
    ],
)
def test_title_is_intent_then_pr_title_then_pr_number_or_branch(intent, target, expected):
    title = resolve_report_title(intent, target, "feat/raw")

    assert title == expected
    assert title != FORMER_FALLBACK_TITLE


def test_evaluation_paragraph_names_title_link_commit_and_base():
    body = _report([], title="PR title")

    assert (
        "Evaluated PR title ([acme/widgets#42](https://github.com/acme/widgets/pull/42)) at head commit `abc1234` "
        "against base branch `main`: 0 Critical, 0 Important and 0 Nit findings; CI 2/2 passed, 0 failing, 0 pending; "
        "mergeability CLEAN."
    ) in body
    assert "Evaluated 42 (" in _report([])
    assert "No reviewer reported a finding." in _report([])


@pytest.mark.parametrize("target", ["42", "feat/raw"])
def test_report_without_a_github_origin_renders_the_target_without_a_link(tmp_path, monkeypatch, target):
    import subprocess

    from sandbox_executor import review_report

    (tmp_path / ".git").mkdir()

    def fake_git(args, cwd=None, check=False):
        out = "https://gitlab.example.com/acme/widgets.git" if args[:2] == ["remote", "get-url"] else ""
        return subprocess.CompletedProcess(["git", *args], 0, stdout=out, stderr="")

    monkeypatch.setattr(review_report, "run_git", fake_git)
    body = build_pr_review_consensus_report(str(tmp_path), target, _consensus(repo_name=""), [])

    assert f"Evaluated {target} (`{target}`) at head commit" in body
    assert "](" not in body
    assert "github.com" not in body
    assert "Holon-Agentic-Coder" not in body
