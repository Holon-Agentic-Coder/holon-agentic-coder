"""Fake reviewer ensembles for Stage 4 tests: no runner, no Docker, no gh, no LLM.

``sandboxed_clean_reviewers`` puts a test in the world the old review-loop tests assumed: the loop runs inside a
sandbox and every reviewer approves with zero findings, so mergeability and CI decide the outcome as before. Tests of
the ensemble wiring itself inject their own fake through ``run_ensemble_fn`` or ``FakeEnsemble``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from typing import Any

import pytest
from sandbox_executor import flow
from sandbox_executor.review_ensemble import (
    ParsedTrailer,
    ReviewCounts,
    ReviewerResult,
    ReviewerVerdict,
    ReviewFinding,
    ReviewRequest,
    reviewer_report_rel,
)
from sandbox_executor.review_target import ReviewTarget

FAKE_HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"


def clean_reviewer(index: int, agent: str = "fake-agent", model: str = "fake-model") -> ReviewerResult:
    """A reviewer that approved with zero findings."""
    parsed = ParsedTrailer(ReviewerVerdict.APPROVED, ReviewCounts())
    return ReviewerResult.completed(index, agent, model, reviewer_report_rel(1, index), "clean report", parsed)


def objecting_reviewer(
    index: int, severity: str = "important", verdict: ReviewerVerdict = ReviewerVerdict.CHANGES_REQUESTED
) -> ReviewerResult:
    """A completed reviewer with one finding of ``severity``."""
    finding = ReviewFinding(severity, "src/app.py", 12, f"{severity} problem in the handler")
    counts = ReviewCounts(**{severity: 1})
    parsed = ParsedTrailer(verdict, counts, (finding,))
    return ReviewerResult.completed(index, "fake-agent", "fake-model", reviewer_report_rel(1, index), "report", parsed)


def failed_reviewer(index: int, reason: str = "runner exited before writing a report") -> ReviewerResult:
    """A reviewer that produced no usable report."""
    return ReviewerResult.failed(index, "fake-agent", "fake-model", reviewer_report_rel(1, index), reason)


class FakeEnsemble:
    """A ``run_ensemble`` stand-in that records its calls; by default ``size`` clean reviewers."""

    def __init__(self, results: Sequence[ReviewerResult] | Callable[[int], Sequence[ReviewerResult]] | None = None):
        """Return ``results`` (or ``results(size)``) on every call; ``None`` means clean reviewers."""
        self._results = results
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request: ReviewRequest, *, agent: str, model: str, size: int, **_: Any) -> list[ReviewerResult]:
        """Record the call and return the configured results."""
        self.calls.append({"request": request, "agent": agent, "model": model, "size": size})
        if self._results is None:
            return [clean_reviewer(i, agent, model) for i in range(1, size + 1)]
        if callable(self._results):
            return list(self._results(size))
        return list(self._results)


def fake_resolved_target(repo_dir: str, review_target: str) -> tuple[ReviewTarget, None]:
    """Resolve any target to a fixed head SHA without git or gh."""
    del repo_dir
    return ReviewTarget(raw=review_target, head_branch=review_target, head_sha=FAKE_HEAD_SHA), None


def fake_review_request(  # noqa: PLR0913
    target: ReviewTarget, base_ref: str, iteration: int, pr_title: str, pr_body: str, repo_dir: str
) -> ReviewRequest:
    """A request with a fixed diff, built without git."""
    return ReviewRequest(repo_dir, target.head_sha, base_ref, pr_title, pr_body, "diff --git a/x b/x\n", iteration)


def install_fake_reviewers(monkeypatch: pytest.MonkeyPatch, ensemble: FakeEnsemble | None = None) -> FakeEnsemble:
    """Mark the process as sandboxed and replace target resolution, the diff and the ensemble with fakes."""
    ensemble = ensemble or FakeEnsemble()
    monkeypatch.setenv("HOLON_IN_SANDBOX", "1")
    monkeypatch.setattr(flow, "_resolve_reviewed_target", fake_resolved_target)
    monkeypatch.setattr(flow, "_review_base_ref", lambda repo_dir, target, target_branch: f"origin/{target_branch}")
    monkeypatch.setattr(flow, "build_review_request", fake_review_request)
    monkeypatch.setattr(flow, "run_ensemble", ensemble)
    return ensemble


@pytest.fixture
def sandboxed_clean_reviewers(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeEnsemble]:
    """Run the test inside a simulated sandbox with an ensemble of clean fake reviewers."""
    yield install_fake_reviewers(monkeypatch)
