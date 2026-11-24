"""The stale-calibration warning comment: its text and its one-comment lifecycle (stale, superseded, reopened).

Hermetic: an in-memory comment thread behind the lister, poster and editor seams; no git, no network, no ``gh``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sandbox_executor.flow import FlowContext
from sandbox_executor.flow.calibrate_stale_warning import (
    NO_PULL_REQUEST,
    STALE_MARKER,
    SUPERSEDED_MARKER_PREFIX,
    StaleRange,
    WarningSeams,
    resolve_stale,
    stale_body,
    warn_stale,
)
from sandbox_executor.flow.github.approval_invalidation import CommentRecord

EVALUATED, HEAD, NEWER_HEAD, FRESH = "a" * 40, "b" * 40, "d" * 40, "c" * 40
REASON = "1 commit(s) after the evaluated commit changed the pull request's own change"
STALE = StaleRange(EVALUATED, HEAD, REASON)
# A warning as an earlier version of the flow worded it: an instruction to the reader, under the same hidden marker.
OLD_WORDING = (
    "## ⚠️ Calibration report is stale\n\n"
    "The calibration report covers `aaaaaaa` but the pull request head is `bbbbbbb` (2 non-calibration commit(s) "
    f"landed after the evaluated commit). Rerun Stage 5 first; do not merge on the stale report.\n\n{STALE_MARKER}"
)


class Thread:
    """An in-memory pull request comment thread behind ``WarningSeams``; it counts listings, posts and edits."""

    def __init__(self, *bodies: str) -> None:
        self.records = [CommentRecord(f"IC_{n}", body) for n, body in enumerate(bodies)]
        self.listings = 0
        self.posted: list[str] = []
        self.edited: list[str] = []

    def _comments(self, pr_number: str) -> list[CommentRecord]:
        self.listings += 1
        return list(self.records)

    def _post(self, pr_number: str, body: str) -> bool:
        self.posted.append(body)
        self.records.append(CommentRecord(f"IC_{len(self.records)}", body))
        return True

    def _edit(self, comment_id: str, body: str) -> bool:
        index = next(i for i, record in enumerate(self.records) if record.id == comment_id)
        self.edited.append(comment_id)
        self.records[index] = CommentRecord(comment_id, body)
        return True

    @property
    def seams(self) -> WarningSeams:
        return WarningSeams(comments=self._comments, poster=self._post, editor=self._edit)

    def bodies(self) -> list[str]:
        return [record.body for record in self.records]


@pytest.fixture
def ctx(tmp_path: Path) -> FlowContext:
    return FlowContext(target_worktree=str(tmp_path), execution_branch="I-1-x/P-2-y/E-3-z/_", agent="a", model="m")


def test_the_stale_text_says_the_flow_reruns_stage5_and_gives_the_reader_no_instruction() -> None:
    body = stale_body(STALE)
    assert body.startswith("## ⚠️ Calibration report is stale\n\n")
    assert "The calibration report covers `aaaaaaa` but the pull request head is `bbbbbbb`" in body
    assert f"({REASON})" in body
    assert "The flow reruns Stage 5 for this head on its next run" in body
    assert "marks this warning superseded when the fresh report is pushed" in body
    assert "do not merge" not in body.lower() and "must rerun" not in body.lower()
    assert body.endswith(STALE_MARKER) and SUPERSEDED_MARKER_PREFIX not in body


def test_warn_stale_posts_once_and_a_newer_head_edits_the_same_comment_in_place(ctx: FlowContext) -> None:
    thread = Thread("an unrelated review comment")
    first = warn_stale(ctx, "7", STALE, thread.seams)
    again = warn_stale(ctx, "7", STALE, thread.seams)
    newer = warn_stale(ctx, "7", StaleRange(EVALUATED, NEWER_HEAD, REASON), thread.seams)

    assert first == {"posted": True, "edited": 0, "reason": "posted"}
    assert again == {"posted": False, "edited": 0, "reason": "already current"}
    assert newer == {"posted": False, "edited": 1, "reason": "updated in place"}
    assert (len(thread.posted), thread.edited) == (1, ["IC_1"])
    assert thread.bodies() == ["an unrelated review comment", stale_body(StaleRange(EVALUATED, NEWER_HEAD, REASON))]


def test_resolve_stale_supersedes_the_open_warning_naming_the_fresh_sha_and_never_posts(ctx: FlowContext) -> None:
    thread = Thread()
    assert resolve_stale(ctx, "7", FRESH, thread.seams) == {
        "posted": False,
        "edited": 0,
        "reason": "no open stale warning",
    }
    warn_stale(ctx, "7", STALE, thread.seams)

    resolved = resolve_stale(ctx, "7", FRESH, thread.seams)
    repeated = resolve_stale(ctx, "7", FRESH, thread.seams)

    assert resolved == {"posted": False, "edited": 1, "reason": "superseded"}
    assert repeated == {"posted": False, "edited": 0, "reason": "no open stale warning"}
    (warning,) = thread.bodies()
    assert warning.startswith("## Stale calibration warning superseded")
    assert "superseded by the fresh calibration report, which evaluated `ccccccc`" in warning
    assert STALE_MARKER in warning and f"{SUPERSEDED_MARKER_PREFIX}{FRESH} -->" in warning
    assert "Calibration report is stale" not in warning
    assert (len(thread.posted), thread.edited) == (1, ["IC_0"])  # the one post is the warning itself


def test_warn_stale_after_a_superseded_warning_reopens_the_same_comment(ctx: FlowContext) -> None:
    thread = Thread()
    warn_stale(ctx, "7", STALE, thread.seams)
    resolve_stale(ctx, "7", FRESH, thread.seams)

    reopened = warn_stale(ctx, "7", StaleRange(FRESH, NEWER_HEAD, REASON), thread.seams)

    assert reopened == {"posted": False, "edited": 1, "reason": "updated in place"}
    assert thread.bodies() == [stale_body(StaleRange(FRESH, NEWER_HEAD, REASON))]
    assert (len(thread.posted), thread.edited) == (1, ["IC_0", "IC_0"])  # never a second comment


def test_a_comment_with_the_old_wording_is_adopted_and_edited_not_duplicated(ctx: FlowContext) -> None:
    thread = Thread(OLD_WORDING)

    signal = warn_stale(ctx, "7", StaleRange(EVALUATED, NEWER_HEAD, REASON), thread.seams)

    assert signal == {"posted": False, "edited": 1, "reason": "updated in place"}
    (warning,) = thread.bodies()
    assert "The flow reruns Stage 5" in warning and "do not merge" not in warning
    assert (thread.posted, thread.edited) == ([], ["IC_0"])

    adopted = Thread(OLD_WORDING)
    assert resolve_stale(ctx, "7", FRESH, adopted.seams)["edited"] == 1
    assert f"{SUPERSEDED_MARKER_PREFIX}{FRESH} -->" in adopted.bodies()[0] and adopted.posted == []


@pytest.mark.parametrize("flag", ["dry_run", "skip_push"])
def test_a_dry_run_and_a_skipped_push_post_and_edit_nothing(ctx: FlowContext, flag: str) -> None:
    setattr(ctx, flag, True)
    thread = Thread(stale_body(STALE))

    assert warn_stale(ctx, "7", StaleRange(EVALUATED, NEWER_HEAD, REASON), thread.seams) == NO_PULL_REQUEST
    assert resolve_stale(ctx, "7", FRESH, thread.seams) == NO_PULL_REQUEST
    assert (thread.listings, thread.posted, thread.edited) == (0, [], [])
    assert thread.bodies() == [stale_body(STALE)]
