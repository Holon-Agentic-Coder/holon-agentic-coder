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

from sandbox_executor.git import run_git
from sandbox_executor.redaction import redact_agent_secrets

DEFAULT_REMOTE = "origin"

_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_BRANCH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@+-]*\Z")
_REMOTE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\Z")


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

    The branch and remote are validated like a push's and follow ``--``, so neither can inject a ``git fetch`` option.

    Raises:
        HostRemoteError: If an argument is unsafe or the fetch fails.
    """
    unsafe = _name_error("fetch", branch, remote)
    if unsafe:
        raise HostRemoteError("fetch", branch, remote, unsafe)
    refspec = f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"
    res = run_git(["fetch", "--no-tags", "--", remote, refspec], cwd=repo_dir, check=False, timeout=300)
    if res.returncode != 0:
        detail = (res.stderr or "").strip() if isinstance(res.stderr, str) else ""
        raise HostRemoteError("fetch", branch, remote, detail or f"git fetch exited with code {res.returncode}")


def _is_branch_name(name: str) -> bool:
    return (
        bool(_BRANCH_NAME.match(name))
        and not any(bad in name for bad in ("..", "//", "/.", ".lock/"))
        and not name.endswith(("/", ".", ".lock"))
    )


def _name_error(verb: str, branch: str, remote: str) -> str | None:
    """Return why ``branch`` or ``remote`` is unsafe to hand to ``git <verb>``, or None when both are fine."""
    if not _is_branch_name(branch):
        return f"refusing to {verb}: the target branch name is not a plain branch name"
    if not _REMOTE_NAME.match(remote) or remote.endswith((".", ".lock")):
        return f"refusing to {verb}: the remote name is not a plain remote name"
    return None


def _push_input_error(source: str, branch: str, remote: str) -> str | None:
    """Return why ``source``, ``branch`` or ``remote`` is unsafe to hand to ``git push``, or None when all are fine.

    The source comes from a role container's result and so is untrusted: only a full hex object id or a
    ``refs/heads/`` ref is accepted, which rules out option-looking values (``--receive-pack=...``) and a ``+``
    force prefix.
    """
    if not (_OBJECT_ID.match(source) or (source.startswith("refs/heads/") and _is_branch_name(source[11:]))):
        return "refusing to push: the source is not a full hex object id or a refs/heads/ ref"
    return _name_error("push", branch, remote)


def push_commit(repo_dir: str, source: str, branch: str, remote: str = DEFAULT_REMOTE) -> None:
    """Push ``source`` (a full hex object id or ``refs/heads/`` ref) to ``branch``; fast-forward only, never forced.

    The arguments follow ``--`` and are validated first, so an untrusted ``source`` cannot inject a ``git push``
    option or a ``+`` force refspec.

    Raises:
        HostRemoteError: If an argument is unsafe or the push fails.
    """
    unsafe = _push_input_error(source, branch, remote)
    if unsafe:
        raise HostRemoteError("push", branch, remote, unsafe)
    res = run_git(["push", "--", remote, f"{source}:refs/heads/{branch}"], cwd=repo_dir, check=False, timeout=300)
    if res.returncode != 0:
        detail = (res.stderr or "").strip() if isinstance(res.stderr, str) else ""
        raise HostRemoteError("push", branch, remote, detail or f"git push exited with code {res.returncode}")


def push_branch(repo_dir: str, branch: str, remote: str = DEFAULT_REMOTE) -> None:
    """Push the local ``branch`` to the worktree's remote under the same name (fast-forward only).

    Raises:
        HostRemoteError: If the push fails.
    """
    push_commit(repo_dir, f"refs/heads/{branch}", branch, remote)


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
