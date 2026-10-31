"""The Stage 4 consensus report renders only real reviewer data (fakes only: no runner, gh or remote)."""

import hashlib
import re

import pytest
from sandbox_executor.review_ensemble import ParsedTrailer, ReviewCounts, ReviewerResult, ReviewerVerdict, ReviewFinding
from sandbox_executor.review_report import (
    build_pr_review_consensus_report,
    build_review_consensus_report,
    resolve_report_title,
)
from sandbox_executor.review_target import ReviewTarget

from tests.review_fakes import failed_reviewer, objecting_reviewer

COMMIT = "abc1234"
FORMER_FALLBACK_TITLE = "Audit Remediation and Flow Review Hardening"


def _reviewer(index: int, agent: str, model: str) -> ReviewerResult:
    parsed = ParsedTrailer(ReviewerVerdict.APPROVED, ReviewCounts())
    return ReviewerResult.completed(
        index, agent, model, f".holon/review/iter-1/reviewer-{index}/report.md", "ok", parsed
    )


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
        "mergeability": {"is_dirty": False, "merge_state_status": "CLEAN"},
    }
    data.update(overrides)
    return data


def _report(reviewers, title=None, **overrides) -> str:
    return build_pr_review_consensus_report("/nonexistent", "42", _consensus(**overrides), reviewers, title)


def test_report_names_each_reviewers_real_agent_and_model():
    reviewers = [
        _reviewer(1, "claude-agent", "model-one"),
        _reviewer(2, "codex-agent", "model-two"),
        objecting_reviewer(3, "important"),
    ]
    body = _report(reviewers)

    assert body.startswith("## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration 1)")
    for agent, model in (("claude-agent", "model-one"), ("codex-agent", "model-two"), ("fake-agent", "fake-model")):
        assert f"agent `{agent}`, model `{model}`" in body
    assert "verdict `CHANGES_REQUESTED` (critical: 0, important: 1, nit: 0)" in body
    assert "| `important` | `src/app.py:12` | Reviewer 3 | important problem in the handler |" in body
    assert "Reviewers: 3 configured, 3 completed." in body
    assert "**Verdict:** `CHANGES_REQUESTED`" in body
    assert "- **CI:** 2/2 passed, 0 failing, 0 pending (github_cli)" in body


def test_report_has_no_commit_derived_reviewer_ids_or_fabricated_sections():
    body = _report([_reviewer(i, "a", "m") for i in range(1, 4)])

    hashed = {hashlib.sha256(f"{COMMIT}-reviewer-{i}".encode()).hexdigest()[:8] for i in range(1, 10)}
    assert not hashed & set(re.findall(r"[0-9a-f]{8}", body))
    assert FORMER_FALLBACK_TITLE not in body
    assert "-Agent Independent" not in body
    assert "Persona" not in body
    assert "Verified Positive Findings" not in body


def test_failed_reviewer_is_shown_failed_with_its_redacted_reason():
    secret = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    body = _report([_reviewer(1, "a", "m"), failed_reviewer(2, f"runner crashed with token {secret}")])

    assert "- **Reviewer 2**: agent `fake-agent`, model `fake-model`: `FAILED` (runner crashed with token" in body
    assert secret not in body
    assert "Reviewers: 3 configured, 1 completed." in body


def test_untrusted_finding_text_cannot_break_out_of_its_line():
    finding = ReviewFinding("nit", "a`b.py", None, "line one\n## injected heading <script>`x`</script>")
    parsed = ParsedTrailer(ReviewerVerdict.CHANGES_REQUESTED, ReviewCounts(nit=1), (finding,))
    reviewer = ReviewerResult.completed(1, "a", "m", ".holon/review/iter-1/reviewer-1/report.md", "r", parsed)
    body = _report([reviewer])

    line = next(line for line in body.splitlines() if line.startswith("| `nit`"))
    assert "\n## injected" not in body
    assert "<script>" not in body
    assert line == "| `nit` | `a'b.py` | Reviewer 1 | line one ## injected heading &lt;script&gt;'x'&lt;/script&gt; |"


def test_unmeasured_ci_is_never_reported_as_a_pass():
    body = _report([_reviewer(1, "a", "m")], ci_checks={"measured": False, "all_passed": False})
    assert "- **CI:** not measured" in body


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


def test_report_uses_the_resolved_title_and_falls_back_to_the_target_without_one():
    approved = _report([], ensemble_verdict="APPROVED", approved=True)
    assert approved.startswith("## ✅ Ensemble Consensus Review — APPROVED (3/3)")
    changes_requested = _report([], title="PR title")
    assert changes_requested.startswith("## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration 1)")
    assert _report([]).startswith("## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration 1)")
    assert "No findings were reported." in _report([])
    assert build_review_consensus_report("/nonexistent", "42", _consensus(), []) == build_pr_review_consensus_report(
        "/nonexistent", "42", _consensus(), []
    )


def test_approved_report_renders_consensus_header_verified_bullets_and_human_merge_notice():
    reviewers = [
        _reviewer(1, "claude-agent", "model-one"),
        _reviewer(2, "codex-agent", "model-two"),
        _reviewer(3, "antigravity-agent", "model-three"),
    ]
    body = _report(reviewers, approved=True, ensemble_verdict="APPROVED")

    assert body.startswith("## ✅ Ensemble Consensus Review — APPROVED (3/3)")
    assert "- **Verified:** Architectural refactorings and code hygiene verified." in body
    assert (
        "- **Verified:** Backward compatibility and API surface stability maintained "
        "(including legacy function aliases)." in body
    )
    assert (
        "- **Verified:** Test execution counts and passing assertions verified (CI checks and unit test pass rates)."
        in body
    )
    assert "- **Verified:** Quality gates and complexity baselines fully satisfied." in body
    assert (
        "- **Verified:** 3-ledger provenance integrity maintained across "
        "`intents.jsonl`, `plans.jsonl`, and `executions.jsonl`." in body
    )
    assert (
        "> **Bean 0034 Notice:** Under Bean 0034 (Human-Only PR Merging), autonomous merging to `main` is strictly "
        "prohibited. Pipeline halts for manual human approval." in body
    )
    assert "### Reviewers" in body
    assert "Reviewers: 3 configured, 3 completed." in body


def test_changes_requested_report_renders_header_and_findings_table():
    reviewers = [
        _reviewer(1, "claude-agent", "model-one"),
        objecting_reviewer(2, "critical"),
        objecting_reviewer(3, "important"),
    ]
    body = _report(reviewers, ensemble_verdict="CHANGES_REQUESTED", iterations=2)

    assert body.startswith("## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration 2)")
    assert "| Severity | Location | Reviewer | Summary |" in body
    assert "| -------- | -------- | -------- | ------- |" in body
    assert "| `critical` | `src/app.py:12` | Reviewer 2 | critical problem in the handler |" in body
    assert "| `important` | `src/app.py:12` | Reviewer 3 | important problem in the handler |" in body
    assert "Bean 0034 Notice" not in body


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

    assert f"**Pull Request:** `{target}`" in body
    assert "](" not in body
    assert "github.com" not in body
    assert "Holon-Agentic-Coder" not in body
