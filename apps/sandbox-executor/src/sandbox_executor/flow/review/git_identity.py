"""The git identity every review-loop commit is made with: ledger commits, conflict merges and resolver pushes.

The identity reaches every review-loop git process through the ``GIT_AUTHOR_*`` and ``GIT_COMMITTER_*``
environment, never through git config: a config write could land in the repository config shared with the
host worktrees and misattribute host commits.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, MutableMapping

REVIEW_GIT_ENV: dict[str, str] = {
    "GIT_AUTHOR_NAME": "Holon Review Loop Agent",
    "GIT_AUTHOR_EMAIL": "review-loop-agent@holon-agentic-coder.com",
    "GIT_COMMITTER_NAME": "Holon Review Loop Agent",
    "GIT_COMMITTER_EMAIL": "review-loop-agent@holon-agentic-coder.com",
}


def apply_review_git_identity(environ: MutableMapping[str, str]) -> dict[str, str | None]:
    """Write the ``REVIEW_GIT_ENV`` entries into ``environ`` in place.

    Returns the previous value of each key, ``None`` where the key was absent, so the caller can restore them.
    """
    previous = {key: environ.get(key) for key in REVIEW_GIT_ENV}
    environ.update(REVIEW_GIT_ENV)
    return previous


@contextlib.contextmanager
def review_git_identity(environ: MutableMapping[str, str]) -> Iterator[None]:
    """Overlay the review-loop identity on ``environ`` for the block, then restore the previous values.

    Keys that were absent before are deleted again; restoring also happens when the block raises, including
    ``SystemExit``, so an in-process caller never keeps the identity.
    """
    previous = apply_review_git_identity(environ)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                environ.pop(key, None)
            else:
                environ[key] = value
