"""Fakes for decomposed flows: a Stage 4 that opens PRs in a ``FakeGitHub`` and the patches that wire both in.

``ChildReviews`` replaces ``run_review_stage`` (looked up in ``sandbox_executor.flow.pipeline``) for the parent and each
child pipeline: it opens (or, like ``create_or_update_pr``, reuses) a PR from the execution branch into the intent's
``target_branch`` and halts at consensus approval with the payload shape the real stage returns. ``decomposed_flow``
patches it in together with the ``gh`` seam of the guarded merge, so children really merge into the plan branch on the
fixture's bare origin.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus

from tests.flow.fake_gh import FakeGitHub

APPROVED = {"approved": True, "score": 1.0, "iterations": 1, "status": "approved"}


def halted_review(number: int | None, url: str | None) -> StageResult:
    """The Stage 4 result at a consensus halt, with the PR recorded under ``payload["pr"]`` as the real stage does."""
    pr = {"attempted": True, "success": True, "action": "create", "pr_number": number, "output": url or ""}
    return StageResult(
        stage=FlowStage.REVIEW,
        status=StageStatus.HALTED_FOR_HUMAN,
        payload={"consensus": dict(APPROVED), "halted_for_human": True, "pr": pr},
    )


class ChildReviews:
    """Fake ``run_review_stage``: open or reuse the PR, then halt at consensus (or fail for a chosen slug, once)."""

    def __init__(self, gh: FakeGitHub, fail_once_for: str | None = None):
        self.gh = gh
        self.fail_once_for = fail_once_for
        self.reviewed: list[dict[str, Any]] = []
        self.created: list[int] = []

    def __call__(self, ctx: FlowContext) -> StageResult:
        head = str(ctx.execution_branch)
        base = str(ctx.intent_data.get("target_branch") or "main")
        existing = next((pr for pr in self.gh.prs.values() if pr["head"] == head), None)
        if existing is None:
            number = self.gh.open_pr(head, base)
            self.created.append(number)
        else:
            number = existing["number"]
        self.reviewed.append({"slug": ctx.intent_data.get("slug"), "head": head, "base": base, "number": number})
        if self.fail_once_for and ctx.intent_data.get("slug") == self.fail_once_for:
            self.fail_once_for = None
            return StageResult(
                stage=FlowStage.REVIEW, status=StageStatus.FAILED, error="Autonomous review requested changes."
            )
        return halted_review(number, self.gh.prs[number]["url"])


@contextlib.contextmanager
def decomposed_flow(origin: Path, tmp_path: Path, fail_once_for: str | None = None) -> Iterator[ChildReviews]:
    """Patch in ``ChildReviews`` as Stage 4 and a ``FakeGitHub`` over ``origin`` as the merge's ``gh`` runner."""
    scratch = tmp_path / "fake-gh"
    scratch.mkdir(exist_ok=True)
    reviews = ChildReviews(FakeGitHub(origin, scratch), fail_once_for=fail_once_for)
    with (
        patch("sandbox_executor.flow.pipeline.run_review_stage", side_effect=reviews),
        patch("sandbox_executor.flow.github.merge.run_gh", reviews.gh),
    ):
        yield reviews
