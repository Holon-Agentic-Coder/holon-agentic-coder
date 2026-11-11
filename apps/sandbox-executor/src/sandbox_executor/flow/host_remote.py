"""Host-side remote operations of the flow: every fetch and push of a stage goes through here.

Role containers never reach the remote. The host fetches the base a role works from before the container starts (the
role branches off the fetched ref inside the mounted flow worktree), and pushes the branch the role committed after the
container returns, always through the worktree's own configured remote (normally ``origin``) and the host's own
credentials. A failure is a ``HostRemoteError`` naming the operation, the branch and the remote, with credentials
stripped.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from sandbox_executor.flow.git_refs import _fetch_refspecs_error
from sandbox_executor.git import run_git
from sandbox_executor.redaction import redact_agent_secrets

DEFAULT_REMOTE = "origin"


class HostRemoteError(RuntimeError):
    """A host fetch or push failed; carries the operation, the branch and the remote name used."""

    def __init__(self, operation: str, branch: str, remote: str, detail: str) -> None:
        self.operation = operation
        self.branch = branch
        self.remote = remote
        self.detail = redact_agent_secrets(detail)
        super().__init__(f"host {operation} of '{branch}' through remote '{remote}' failed: {self.detail}")


def fetch_base(repo_dir: str, branch: str, remote: str = DEFAULT_REMOTE) -> None:
    """Fetch ``branch`` from the worktree's remote into ``refs/remotes/<remote>/<branch>``.

    Raises:
        HostRemoteError: If the fetch fails.
    """
    error = _fetch_refspecs_error(repo_dir, [f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"], remote)
    if error is not None:
        raise HostRemoteError("fetch", branch, remote, error)


def push_commit(repo_dir: str, source: str, branch: str, remote: str = DEFAULT_REMOTE) -> None:
    """Push ``source`` (a ref or commit) to ``branch`` on the worktree's remote; never forced, so only a fast-forward.

    Raises:
        HostRemoteError: If the push fails.
    """
    res = run_git(["push", remote, f"{source}:refs/heads/{branch}"], cwd=repo_dir, check=False, timeout=300)
    if res.returncode != 0:
        detail = (res.stderr or "").strip() if isinstance(res.stderr, str) else ""
        raise HostRemoteError("push", branch, remote, detail or f"git push exited with code {res.returncode}")


def push_branch(repo_dir: str, branch: str, remote: str = DEFAULT_REMOTE) -> None:
    """Push the local ``branch`` to the worktree's remote under the same name (fast-forward only).

    Raises:
        HostRemoteError: If the push fails.
    """
    push_commit(repo_dir, f"refs/heads/{branch}", branch, remote)


def local_branch_tip(repo_dir: str, branch: str) -> str | None:
    """Return the commit the local ``branch`` points at, or None when it does not exist."""
    res = run_git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}"], cwd=repo_dir, check=False)
    sha = res.stdout.strip() if res.returncode == 0 and isinstance(res.stdout, str) else ""
    return sha or None


def remote_error(operation: Callable[..., Any], *args: Any) -> str | None:
    """Run a host remote operation and return its ``HostRemoteError`` message, or None when it succeeded."""
    try:
        operation(*args)
    except HostRemoteError as exc:
        return str(exc)
    return None


def new_local_child_branches(repo_dir: str, parent_prefix: str, marker: str, before: set[str]) -> list[str]:
    """Return the local ``<parent_prefix>/<marker>-<timestamp>-.../_`` branches absent from the ``before`` snapshot."""
    pattern = re.compile(rf"^{re.escape(parent_prefix)}/{re.escape(marker)}-\d+-[^/]+/_$")
    res = run_git(
        ["for-each-ref", "--format=%(refname:short)", f"refs/heads/{parent_prefix}/"], cwd=repo_dir, check=False
    )
    stdout = res.stdout if isinstance(res.stdout, str) else ""
    return sorted(name for line in stdout.splitlines() if pattern.match(name := line.strip()) and name not in before)


def publish_new_child_branches(repo_dir: str, parent_prefix: str, marker: str, before: set[str]) -> list[str]:
    """Push every child branch a role committed during dispatch (absent from ``before``); return those pushed.

    Raises:
        HostRemoteError: If a push fails.
    """
    pushed = new_local_child_branches(repo_dir, parent_prefix, marker, before)
    for branch in pushed:
        push_branch(repo_dir, branch)
    return pushed
