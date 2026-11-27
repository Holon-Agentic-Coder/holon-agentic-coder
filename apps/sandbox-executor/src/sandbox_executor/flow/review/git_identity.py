from __future__ import annotations

from collections.abc import Mapping

REVIEW_GIT_ENV: dict[str, str] = {
    "GIT_AUTHOR_NAME": "Holon Review Loop Agent",
    "GIT_AUTHOR_EMAIL": "review-loop-agent@holon-agentic-coder.com",
    "GIT_COMMITTER_NAME": "Holon Review Loop Agent",
    "GIT_COMMITTER_EMAIL": "review-loop-agent@holon-agentic-coder.com",
}


# why: a per-call env copy; the process env would leak the identity into every child process.
# why: never git config; a write could land in the config shared with host worktrees and misattribute host commits.
def _review_git_env(environ: Mapping[str, str]) -> dict[str, str]:
    return {**environ, **REVIEW_GIT_ENV}
