"""The PR head commit as GitHub reports it, read once via ``gh pr view --json headRefOid``."""

from __future__ import annotations

from collections.abc import Mapping

from sandbox_executor.flow.github.adapter import GitHubAdapter

PR_HEAD_TIMEOUT_SECONDS = 10.0


def pr_head_sha(
    repo_dir: str, target: str, proxy_env: Mapping[str, str] | None = None, timeout: float = PR_HEAD_TIMEOUT_SECONDS
) -> str | None:
    """The head commit of the PR for ``target`` (branch or number), or None when it cannot be read."""
    return GitHubAdapter(repo_dir, dict(proxy_env or {})).pr_head_sha(target, timeout)
