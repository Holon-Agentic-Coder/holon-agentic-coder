"""Turn a branch name into an unambiguous git revision argument.

Nested intent branches (``I-.../P-.../E-.../_``) contain slashes and end in ``/_``. Passed bare, a name git cannot
resolve as a ref is read as a path instead, and ``git diff`` or ``git rev-list`` then exits 128 with "ambiguous
argument ...: unknown revision or path not in the working tree". Callers therefore hand git a fully qualified ref (or
a commit SHA) and end the revision arguments with ``--``, so git never reads a revision as a path. Nothing here
fetches: a ref the host does not have is reported as None for the caller to handle.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable

from sandbox_executor.git import run_git

GitRunner = Callable[..., subprocess.CompletedProcess[str]]

_SHA_PATTERN = re.compile(r"[0-9a-fA-F]{7,40}")


def _verifies(repo_dir: str, revision: str, run: GitRunner) -> bool:
    res = run(["rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"], cwd=repo_dir, check=False, timeout=30)
    return res.returncode == 0


def _bare_branch_name(name: str, remote: str) -> str:
    """Strip whitespace and a ``refs/heads/``, ``refs/remotes/<remote>/`` or ``<remote>/`` prefix.

    A bare branch name that itself starts with ``<remote>/`` would lose that segment; flow branches start with
    ``I-``, so this cannot happen for them.
    """
    bare = name.strip()
    for prefix in ("refs/heads/", f"refs/remotes/{remote}/", f"{remote}/"):
        if bare.startswith(prefix):
            return bare[len(prefix) :]
    return bare


def qualify_branch_revision(
    repo_dir: str, name: str, remote: str = "origin", run: GitRunner | None = None
) -> str | None:
    """Return a fully qualified, verified revision for a branch name or commit SHA.

    Lookup order: a 7 to 40 character hex commit SHA (returned unchanged), then ``refs/heads/<name>``, then
    ``refs/remotes/<remote>/<name>``. Already-qualified input is accepted, so the call is idempotent.

    Args:
        repo_dir: Git worktree to resolve in.
        name: Branch name, remote-tracking name, qualified ref or commit SHA.
        remote: Remote whose tracking refs are consulted.
        run: The caller's git runner (defaults to ``run_git``), so a module whose tests replace its runner never
            spawns a real git process here.

    Returns:
        The qualified ref or SHA, or None when nothing verifies as a commit. Runs read-only ``git rev-parse``
        calls; never fetches and never raises for an unknown ref.
    """
    git = run or run_git
    bare = _bare_branch_name(name, remote)
    if not bare:
        return None
    if _SHA_PATTERN.fullmatch(bare) and _verifies(repo_dir, bare, git):
        return bare
    for candidate in (f"refs/heads/{bare}", f"refs/remotes/{remote}/{bare}"):
        if _verifies(repo_dir, candidate, git):
            return candidate
    return None


def revision_args(*revisions: str) -> list[str]:
    """Return the revisions followed by the ``--`` separator; callers append any pathspecs after it."""
    return [*revisions, "--"]
