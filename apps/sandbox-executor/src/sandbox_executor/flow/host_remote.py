from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from sandbox_executor.git import run_git
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.ref_names import is_over_limit_rejection, named_push_rejection

DEFAULT_REMOTE = "origin"
PUSH_ATTEMPTS = 3
PUSH_BACKOFF_SECONDS = 1.0
_STALE_TIP_MARKERS = ("non-fast-forward", "fetch first")

_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_BRANCH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@+-]*\Z")
_REMOTE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class HostRemoteError(RuntimeError):
    def __init__(self, operation: str, branch: str, remote: str, detail: str) -> None:
        self.operation = operation
        self.branch = branch
        self.remote = remote
        self.detail = redact_agent_secrets(detail)
        super().__init__(f"host {operation} of '{branch}' through remote '{remote}' failed: {self.detail}")


def fetch_base(repo_dir: str, branch: str, remote: str = DEFAULT_REMOTE) -> None:
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
    if not _is_branch_name(branch):
        return f"refusing to {verb}: the target branch name is not a plain branch name"
    if not _REMOTE_NAME.match(remote) or remote.endswith((".", ".lock")):
        return f"refusing to {verb}: the remote name is not a plain remote name"
    return None


def _push_input_error(source: str, branch: str, remote: str) -> str | None:
    # why: a role container supplies the source, so option-looking values and '+' must be refused
    if not (_OBJECT_ID.match(source) or (source.startswith("refs/heads/") and _is_branch_name(source[11:]))):
        return "refusing to push: the source is not a full hex object id or a refs/heads/ ref"
    return _name_error("push", branch, remote)


def push_commit_once(repo_dir: str, source: str, branch: str, remote: str = DEFAULT_REMOTE) -> None:
    unsafe = _push_input_error(source, branch, remote)
    if unsafe:
        raise HostRemoteError("push", branch, remote, unsafe)
    res = run_git(["push", "--", remote, f"{source}:refs/heads/{branch}"], cwd=repo_dir, check=False, timeout=300)
    if res.returncode != 0:
        detail = (res.stderr or "").strip() if isinstance(res.stderr, str) else ""
        detail = detail or f"git push exited with code {res.returncode}"
        raise HostRemoteError("push", branch, remote, named_push_rejection(branch, detail))


def _is_permanent_refusal(detail: str) -> bool:
    # why: retrying an input refusal, an over-long ref, a stale tip or a missing workflow scope cannot succeed
    lowered = detail.lower()
    return (
        lowered.startswith("refusing to push:")
        or is_over_limit_rejection(lowered)
        or any(marker in lowered for marker in _STALE_TIP_MARKERS)
        or ("workflow" in lowered and "scope" in lowered)
    )


def _backoff_seconds(attempt: int) -> float:
    return PUSH_BACKOFF_SECONDS * 2 ** (attempt - 1)


def push_commit(
    repo_dir: str,
    source: str,
    branch: str,
    remote: str = DEFAULT_REMOTE,
    log: Callable[[str], None] | None = None,
) -> None:
    for attempt in range(1, PUSH_ATTEMPTS + 1):
        try:
            push_commit_once(repo_dir, source, branch, remote)
            return
        except HostRemoteError as exc:
            if log is not None:
                log(
                    f"host push of '{branch}' through '{remote}' failed on attempt {attempt} of {PUSH_ATTEMPTS}: "
                    f"{exc.detail}"
                )
            if _is_permanent_refusal(exc.detail) or attempt == PUSH_ATTEMPTS:
                raise
            time.sleep(_backoff_seconds(attempt))


def push_branch(
    repo_dir: str, branch: str, remote: str = DEFAULT_REMOTE, log: Callable[[str], None] | None = None
) -> None:
    push_commit(repo_dir, f"refs/heads/{branch}", branch, remote, log)


def remote_error(operation: Callable[..., Any], *args: Any) -> str | None:
    try:
        operation(*args)
    except HostRemoteError as exc:
        return str(exc)
    return None


def new_local_child_branches(repo_dir: str, parent_prefix: str, marker: str, before: set[str]) -> list[str]:
    pattern = re.compile(rf"^{re.escape(parent_prefix)}/{re.escape(marker)}-\d+-[^/]+/_$")
    res = run_git(
        ["for-each-ref", "--format=%(refname:short)", f"refs/heads/{parent_prefix}/"], cwd=repo_dir, check=False
    )
    stdout = res.stdout if isinstance(res.stdout, str) else ""
    return sorted(name for line in stdout.splitlines() if pattern.match(name := line.strip()) and name not in before)


def publish_new_child_branches(
    repo_dir: str, parent_prefix: str, marker: str, before: set[str], log: Callable[[str], None] | None = None
) -> list[str]:
    pushed = new_local_child_branches(repo_dir, parent_prefix, marker, before)
    for branch in pushed:
        push_branch(repo_dir, branch, DEFAULT_REMOTE, log)
    return pushed
