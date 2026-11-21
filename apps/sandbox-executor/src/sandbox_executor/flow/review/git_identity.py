"""The git identity every review-loop commit is made with: ledger commits, conflict merges and resolver pushes."""

from __future__ import annotations

REVIEW_GIT_ENV: dict[str, str] = {
    "GIT_AUTHOR_NAME": "Holon Review Loop Agent",
    "GIT_AUTHOR_EMAIL": "review-loop-agent@holon-agentic-coder.com",
    "GIT_COMMITTER_NAME": "Holon Review Loop Agent",
    "GIT_COMMITTER_EMAIL": "review-loop-agent@holon-agentic-coder.com",
}
