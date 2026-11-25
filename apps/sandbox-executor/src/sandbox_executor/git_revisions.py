from __future__ import annotations

import re
import subprocess
from collections.abc import Callable

from sandbox_executor.git import run_git

GitRunner = Callable[..., subprocess.CompletedProcess[str]]

_SHA_PATTERN = re.compile(r"[0-9a-fA-F]{7,40}")
_PATHSPEC_SEPARATOR = "--"


def _verifies(repo_dir: str, revision: str, run: GitRunner) -> bool:
    res = run(["rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"], cwd=repo_dir, check=False, timeout=30)
    return res.returncode == 0


def _bare_branch_name(name: str, remote: str) -> str:
    bare_name = name.strip()
    for prefix in ("refs/heads/", f"refs/remotes/{remote}/", f"{remote}/"):
        if bare_name.startswith(prefix):
            return bare_name[len(prefix) :]
    return bare_name


def qualify_branch_revision(
    repo_dir: str, name: str, remote: str = "origin", run: GitRunner | None = None
) -> str | None:
    git = run or run_git
    bare_name = _bare_branch_name(name, remote)
    if not bare_name:
        return None
    if _SHA_PATTERN.fullmatch(bare_name) and _verifies(repo_dir, bare_name, git):
        return bare_name
    qualified_candidates = (f"refs/heads/{bare_name}", f"refs/remotes/{remote}/{bare_name}")
    for candidate in qualified_candidates:
        if _verifies(repo_dir, candidate, git):
            return candidate
    return None


def revision_args(*revisions: str) -> list[str]:
    return [*revisions, _PATHSPEC_SEPARATOR]
