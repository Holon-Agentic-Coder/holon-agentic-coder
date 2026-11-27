from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus

from tests.flow.fake_gh import FakeGitHub

APPROVED = {"approved": True, "score": 1.0, "iterations": 1, "status": "approved"}


# why: the worktree is tmp_path so the stage never writes the real .holon/.
def parent_ctx(tmp_path: Path) -> FlowContext:
    return FlowContext(
        repo_dir=str(tmp_path), token_reduce=False, intent_data={"slug": "parent", "goal": "Change the application"}
    )


def halted_review(
    number: int | None, url: str | None, *, status: StageStatus = StageStatus.HALTED_FOR_HUMAN
) -> StageResult:
    pr = {"attempted": True, "success": True, "action": "create", "pr_number": number, "output": url or ""}
    return StageResult(
        stage=FlowStage.REVIEW,
        status=status,
        payload={
            "consensus": dict(APPROVED),
            "halted_for_human": status == StageStatus.HALTED_FOR_HUMAN,
            "pr": pr,
        },
    )


class ChildReviews:
    def __init__(
        self,
        gh: FakeGitHub,
        fail_once_for: str | None = None,
        bases: dict[str, str] | None = None,
        review_status: StageStatus = StageStatus.HALTED_FOR_HUMAN,
    ):
        self.gh = gh
        self.fail_once_for = fail_once_for
        # why: a PR base per slug overrides the intent's target_branch to model a mis-recorded or edited base.
        self.bases = bases or {}
        self.review_status = review_status
        self.reviewed: list[dict[str, Any]] = []
        self.created: list[int] = []

    def __call__(self, ctx: FlowContext) -> StageResult:
        head = str(ctx.execution_branch)
        slug = str(ctx.intent_data.get("slug"))
        base = self.bases.get(slug) or str(ctx.intent_data.get("target_branch") or "main")
        existing = next((pr for pr in self.gh.prs.values() if pr["head"] == head), None)
        if existing is None:
            number = self.gh.open_pr(head, base)
            self.created.append(number)
        else:
            number = existing["number"]
        self.reviewed.append({"slug": slug, "head": head, "base": base, "number": number})
        if self.fail_once_for and slug == self.fail_once_for:
            self.fail_once_for = None
            # why: like the real stage, a failed review still records the PR it opened.
            failed = halted_review(number, self.gh.prs[number]["url"])
            failed.status, failed.error = StageStatus.FAILED, "Autonomous review requested changes."
            failed.payload.update(halted_for_human=False, consensus={**APPROVED, "approved": False})
            return failed
        return halted_review(number, self.gh.prs[number]["url"], status=self.review_status)


@contextlib.contextmanager
def decomposed_flow(origin: Path, tmp_path: Path, **review_options: Any) -> Iterator[ChildReviews]:
    scratch = tmp_path / "fake-gh"
    scratch.mkdir(exist_ok=True)
    reviews = ChildReviews(FakeGitHub(origin, scratch), **review_options)
    with (
        patch("sandbox_executor.flow.pipeline.run_review_stage", side_effect=reviews),
        patch("sandbox_executor.flow.github.merge.run_gh", reviews.gh),
    ):
        yield reviews
