"""Stage 5 bounded retry of the origin fetch of the execution branch.

A single git fetch can hit an SSH, keychain or network hiccup that clears within seconds. The fetch is retried a few
times with a short backoff; a failure that cannot clear by waiting (the branch is absent on origin, the worktree or the
remote is not a git repository, or a name is unsafe) is raised at once. After the last attempt the final
``HostRemoteError`` is raised unchanged.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.host_remote import DEFAULT_REMOTE, HostRemoteError, fetch_base

FETCH_BACKOFF_SECONDS: tuple[float, ...] = (4.0, 8.0)
FETCH_ATTEMPTS = len(FETCH_BACKOFF_SECONDS) + 1
_PERMANENT_DETAILS = (
    "couldn't find remote ref",
    "does not appear to be a git repository",
    "not a git repository",
    "refusing to fetch:",
)


def _is_permanent(exc: HostRemoteError) -> bool:
    detail = exc.detail.lower()
    return any(marker in detail for marker in _PERMANENT_DETAILS)


def _fetch_execution_branch_with_retry(
    context: FlowContext,
    branch: str,
    *,
    fetch: Callable[[str, str, str], None] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Fetch ``origin/<branch>``, retrying a transient ``HostRemoteError`` with a bounded backoff.

    Raises:
        HostRemoteError: If the failure is permanent or every attempt failed.
    """
    do_fetch = fetch or fetch_base
    do_sleep = sleep or time.sleep
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            do_fetch(context.target_worktree, branch, DEFAULT_REMOTE)
            return
        except HostRemoteError as exc:
            if _is_permanent(exc) or attempt == FETCH_ATTEMPTS:
                raise
            delay = FETCH_BACKOFF_SECONDS[attempt - 1]
            context.log(
                f"Stage 5 sync: fetch of origin/{branch} failed on attempt {attempt}/{FETCH_ATTEMPTS} "
                f"({exc.detail}); retrying in {delay:g}s."
            )
            do_sleep(delay)
