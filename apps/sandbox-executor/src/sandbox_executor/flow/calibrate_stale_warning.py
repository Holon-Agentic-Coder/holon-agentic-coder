from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.approval_invalidation import (
    CommentEditor,
    CommentLister,
    CommentPoster,
    CommentRecord,
    CommentRecordLister,
    approval_pr_number,
    gh_comment_editor,
    gh_comment_poster,
    gh_comment_records,
)
from sandbox_executor.flow.github.ci_failure_detail import short_sha

logger = logging.getLogger(__name__)

STALE_MARKER = "<!-- holon-calibration-stale -->"
STALE_MARKER_FAMILY = "<!-- holon-calibration-stale"
SUPERSEDED_MARKER_PREFIX = "<!-- holon-calibration-superseded:"
REFRESHED_MARKER_PREFIX = "<!-- holon-calibration-refreshed:"
FRESH_REPORT = "the fresh calibration report, which evaluated `{sha}` and covers the pull request head"
NEWER_WARNING = "the newer stale-calibration warning on this pull request (the report evaluated `{sha}`)"
NO_PULL_REQUEST = {"posted": False, "edited": 0, "reason": "dry run, push skipped or no pull request"}
UNLISTED = {"posted": False, "edited": 0, "reason": "comments could not be listed"}


@dataclass(frozen=True)
class StaleRange:
    evaluated: str
    head: str
    reason: str


@dataclass(frozen=True)
class WarningSeams:
    comments: CommentRecordLister | None = None
    poster: CommentPoster | None = None
    editor: CommentEditor | None = None

    @classmethod
    def from_lister(
        cls, lister: CommentLister, poster: CommentPoster | None = None, editor: CommentEditor | None = None
    ) -> WarningSeams:

        def comments(pr_number: str) -> list[CommentRecord] | None:
            bodies = lister(pr_number)
            # why: an empty id lets body-only records deduplicate the warning without ever being edited
            return None if bodies is None else [CommentRecord("", body) for body in bodies]

        return cls(comments=comments, poster=poster, editor=editor)

    def lister_for(self, context: FlowContext) -> CommentRecordLister:
        return self.comments or gh_comment_records(context.target_worktree)

    def poster_for(self, context: FlowContext) -> CommentPoster:
        return self.poster or gh_comment_poster(context.target_worktree)

    def editor_for(self, context: FlowContext) -> CommentEditor:
        return self.editor or gh_comment_editor(context.target_worktree)


def stale_body(stale: StaleRange) -> str:
    return (
        "## ⚠️ Calibration report is stale\n\n"
        f"The calibration report covers `{short_sha(stale.evaluated)}` but the pull request head is "
        f"`{short_sha(stale.head)}` ({stale.reason}). The flow reruns Stage 5 for this head on its next run and "
        f"marks this warning superseded when the fresh report is pushed.\n\n{STALE_MARKER}"
    )


def superseded_body(evaluated: str, cause: str = FRESH_REPORT) -> str:
    return (
        "## Stale calibration warning superseded\n\n"
        f"This stale-calibration warning is superseded by {cause.format(sha=short_sha(evaluated))}."
        f"\n\n{STALE_MARKER}\n{SUPERSEDED_MARKER_PREFIX}{evaluated} -->"
    )


def _refreshed_marker(head: str) -> str:
    return f"{REFRESHED_MARKER_PREFIX}{head} -->"


def refreshed_body(head: str) -> str:
    return f"Calibration refreshed: the report now covers head `{short_sha(head)}`.\n\n{_refreshed_marker(head)}"


def _thread(
    context: FlowContext, pr_number: str | None, seams: WarningSeams
) -> tuple[str, list[CommentRecord]] | dict[str, Any]:
    # why: approval_pr_number may raise on a malformed review payload
    pr_number = pr_number or approval_pr_number(context)
    if context.dry_run or context.skip_push or not pr_number:
        return dict(NO_PULL_REQUEST)
    records = seams.lister_for(context)(pr_number)
    return dict(UNLISTED) if records is None else (pr_number, records)


def _stale_records(records: Iterable[CommentRecord]) -> list[CommentRecord]:
    return [record for record in records if STALE_MARKER_FAMILY in record.body]


def _supersede_open(records: Iterable[CommentRecord], body: str, editor: CommentEditor) -> int:
    open_records = (r for r in records if SUPERSEDED_MARKER_PREFIX not in r.body and r.id)
    return sum(1 for record in open_records if editor(record.id, body))


def _refresh_newest(newest: CommentRecord, body: str, editor: CommentEditor) -> tuple[int, str]:
    if newest.body == body:
        return 0, "already current"
    if not newest.id:
        return 0, "comment id unknown"
    if editor(newest.id, body):
        return 1, "updated in place"
    return 0, "the comment could not be edited"


def warn_stale(
    context: FlowContext, pr_number: str | None, stale: StaleRange, seams: WarningSeams | None = None
) -> dict[str, Any]:
    seams = seams or WarningSeams()
    try:
        thread = _thread(context, pr_number, seams)
        if isinstance(thread, dict):
            return thread
        target, records = thread
        warnings, body = _stale_records(records), stale_body(stale)
        if not warnings:
            posted = seams.poster_for(context)(target, body)
            return {"posted": posted, "edited": 0, "reason": "posted" if posted else "the comment could not be posted"}
        *older, newest = warnings
        editor = seams.editor_for(context)
        collapsed = _supersede_open(older, superseded_body(stale.evaluated, NEWER_WARNING), editor)
        edited, reason = _refresh_newest(newest, body, editor)
        return {"posted": False, "edited": edited + collapsed, "reason": reason}
    except Exception as exc:
        logger.warning("Stale-calibration warning failed: %s", exc)
        return {"posted": False, "edited": 0, "reason": "comment posting failed"}


def resolve_stale(
    context: FlowContext, pr_number: str | None, fresh_evaluated: str, seams: WarningSeams | None = None
) -> dict[str, Any]:
    seams = seams or WarningSeams()
    try:
        thread = _thread(context, pr_number, seams)
        if isinstance(thread, dict):
            return thread
        stale = _stale_records(thread[1])
        edited = _supersede_open(stale, superseded_body(fresh_evaluated), seams.editor_for(context))
        return {"posted": False, "edited": edited, "reason": "superseded" if edited else "no open stale warning"}
    except Exception as exc:
        logger.warning("Resolving the stale-calibration warning failed: %s", exc)
        return {"posted": False, "edited": 0, "reason": "comment editing failed"}


def post_refreshed(
    context: FlowContext, pr_number: str | None, head: str, seams: WarningSeams | None = None
) -> dict[str, Any]:
    seams = seams or WarningSeams()
    try:
        thread = _thread(context, pr_number, seams)
        if isinstance(thread, dict):
            return thread
        target, records = thread
        if any(_refreshed_marker(head) in record.body for record in records):
            return {"posted": False, "edited": 0, "reason": "already posted for this head"}
        posted = seams.poster_for(context)(target, refreshed_body(head))
        return {"posted": posted, "edited": 0, "reason": "posted" if posted else "the comment could not be posted"}
    except Exception as exc:
        logger.warning("Calibration refreshed comment failed: %s", exc)
        return {"posted": False, "edited": 0, "reason": "comment posting failed"}
