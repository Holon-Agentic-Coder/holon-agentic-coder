"""The git identity every review-loop commit is made with: ledger commits, conflict merges and resolver commits.

The identity reaches only the review loop's own commits, through a per-call copy of the environment passed to
that one ``git commit``. It is never written into the process environment, which every child process (tests
and tools included) would inherit, and never into git config: a config write could land in the repository
config shared with the host worktrees and misattribute host commits.
"""

from __future__ import annotations

from collections.abc import Mapping

REVIEW_GIT_ENV: dict[str, str] = {
    "GIT_AUTHOR_NAME": "Holon Review Loop Agent",
    "GIT_AUTHOR_EMAIL": "review-loop-agent@holon-agentic-coder.com",
    "GIT_COMMITTER_NAME": "Holon Review Loop Agent",
    "GIT_COMMITTER_EMAIL": "review-loop-agent@holon-agentic-coder.com",
}


def review_git_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Return a new environment: ``environ`` overlaid with the ``REVIEW_GIT_ENV`` identity.

    The result is meant for the ``env`` argument of one review-loop git commit. ``environ`` is never mutated;
    the function has no side effects.
    """
    return {**environ, **REVIEW_GIT_ENV}
