"""Git worktree resolution and lifecycle automation for Holon Flow.

Provides typed utilities for inspecting Git repository topology, self-healing
stale worktree administrative registrations, and ensuring dedicated worktree
provisioning for autonomous pipeline runs:
- is_bare_repository: Detect whether a repository directory is a bare Git repo.
- is_working_tree: Check whether a directory is a valid non-bare Git working tree.
- prune_worktrees: Prune stale worktree administrative records.
- derive_worktree_layout: Determine target worktree path and branch name from a slug.
- ensure_worktree: Provision or attach an isolated worktree directory for a flow run.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from sandbox_executor.git import branch_exists, resolve_ref, run_git

logger = logging.getLogger(__name__)

__all__ = [
    "derive_worktree_layout",
    "ensure_worktree",
    "is_bare_repository",
    "is_working_tree",
    "prune_worktrees",
]


def is_bare_repository(repo_dir: str) -> bool:
    """Detect whether a repository directory is a bare Git repository.

    Args:
        repo_dir: Path to directory to inspect.

    Returns:
        bool: True if repo_dir exists and represents a bare Git repository,
            False otherwise.
    """
    if not repo_dir or not os.path.exists(repo_dir):
        return False

    res = run_git(["rev-parse", "--is-bare-repository"], cwd=repo_dir, check=False)
    return res.returncode == 0 and res.stdout.strip().lower() == "true"


def is_working_tree(repo_dir: str) -> bool:
    """Check whether a directory is an active non-bare Git working tree.

    Args:
        repo_dir: Path to directory to inspect.

    Returns:
        bool: True if repo_dir exists, is a valid Git repository, and is not
            a bare repository, False otherwise.
    """
    if not repo_dir or not os.path.isdir(repo_dir):
        return False

    res = run_git(["rev-parse", "--is-inside-work-tree"], cwd=repo_dir, check=False)
    if res.returncode != 0 or res.stdout.strip().lower() != "true":
        return False

    return not is_bare_repository(repo_dir)


def prune_worktrees(repo_dir: str) -> None:
    """Prune stale Git worktree records where linked directories were deleted.

    Args:
        repo_dir: Path to Git repository or bare repository directory.

    Side-effects:
        Executes 'git worktree prune' to self-heal orphaned metadata under
        '.git/worktrees/' or repository administrative folders.
    """
    if not repo_dir or not os.path.exists(repo_dir):
        return
    res = run_git(["worktree", "prune"], cwd=repo_dir, check=False)
    if res.returncode == 0:
        logger.debug("Pruned stale worktrees in %s", repo_dir)
    else:
        logger.warning("git worktree prune failed in %s: %s", repo_dir, res.stderr.strip())


def derive_worktree_layout(base_repo_dir: str, slug: str) -> tuple[str, str]:
    """Derive canonical target worktree path and branch name for a flow task.

    Args:
        base_repo_dir: Base repository path (working tree or bare clone).
        slug: Intent slug identifier.

    Returns:
        tuple[str, str]: Tuple of (worktree_path, branch_name).
    """
    clean_slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", slug.strip()).strip("-")
    if not clean_slug:
        clean_slug = "flow"
    branch_name = f"flow-{clean_slug}"

    resolved_base = Path(base_repo_dir).resolve()
    if not os.path.exists(base_repo_dir) and resolved_base.name == branch_name:
        worktree_path = resolved_base
    else:
        parent_dir = resolved_base.parent
        worktree_path = parent_dir / branch_name

    return os.path.normpath(str(worktree_path)), branch_name


def _resolve_base_git_dir(repo_dir: str) -> str:
    """Resolve the root Git or bare repository directory associated with repo_dir.

    Args:
        repo_dir: Target path or repository directory.

    Returns:
        str: Absolute path to the resolved Git directory.

    Raises:
        RuntimeError: If no valid Git repository can be found.
    """
    if os.path.exists(repo_dir) and (is_bare_repository(repo_dir) or (Path(repo_dir) / ".git").exists()):
        return os.path.abspath(repo_dir)

    curr = Path(repo_dir).resolve()
    while curr != curr.parent:
        if is_bare_repository(str(curr)) or (curr / ".git").exists():
            return str(curr)
        curr = curr.parent

    res = run_git(["rev-parse", "--git-dir"], cwd=".", check=False)
    if res.returncode == 0:
        top_res = run_git(["rev-parse", "--show-toplevel"], cwd=".", check=False)
        if top_res.returncode == 0 and top_res.stdout.strip():
            return os.path.abspath(top_res.stdout.strip())
        return os.path.abspath(res.stdout.strip())

    raise RuntimeError(f"Unable to resolve a valid Git repository from directory: '{repo_dir}'")


def _resolve_start_point(base_git_dir: str, target_branch: str) -> str:
    """Resolve an available starting revision or ref for a new worktree branch.

    Args:
        base_git_dir: Base Git repository directory.
        target_branch: Preferred target branch name.

    Returns:
        str: Existing symbolic ref or commit to branch from.
    """
    candidates = [target_branch, "origin/main", "main", "HEAD", "master"]
    for candidate in candidates:
        if candidate and resolve_ref(base_git_dir, candidate) is not None:
            return candidate
    return "HEAD"


def ensure_worktree(repo_dir: str, slug: str, target_branch: str = "origin/main") -> str:
    """Ensure a dedicated Git worktree is provisioned and active for a flow run.

    Detects if repo_dir is already an active working tree. If not (or if bare),
    self-heals stale worktree registrations using 'git worktree prune', resolves
    the underlying Git directory, derives the worktree layout, and attaches to
    an existing branch or creates a new dedicated worktree branch.

    Args:
        repo_dir: Target repository path or bare clone directory.
        slug: Intent slug identifier.
        target_branch: Target base branch to branch off if creating a new branch
            (default: 'origin/main').

    Returns:
        str: Absolute path to the active, verified worktree directory.

    Raises:
        ValueError: If slug is empty.
        RuntimeError: If Git repository cannot be resolved or worktree creation fails.
    """
    clean_slug = slug.strip()
    if not clean_slug:
        raise ValueError("Intent slug must be non-empty to ensure worktree.")

    if os.path.exists(repo_dir) and is_working_tree(repo_dir):
        return os.path.abspath(repo_dir)

    base_git_dir = _resolve_base_git_dir(repo_dir)
    prune_worktrees(base_git_dir)

    worktree_path, branch_name = derive_worktree_layout(repo_dir, clean_slug)
    if os.path.exists(worktree_path) and is_working_tree(worktree_path):
        return os.path.abspath(worktree_path)

    os.makedirs(os.path.dirname(worktree_path), exist_ok=True)

    if not branch_exists(base_git_dir, branch_name, check_remote=True):
        start_point = _resolve_start_point(base_git_dir, target_branch)
        run_git(
            ["worktree", "add", "--no-track", "-b", branch_name, worktree_path, start_point],
            cwd=base_git_dir,
            check=True,
        )
        logger.info(
            "Created worktree at '%s' on new branch '%s' from '%s'",
            worktree_path,
            branch_name,
            start_point,
        )
    else:
        run_git(["worktree", "add", worktree_path, branch_name], cwd=base_git_dir, check=True)
        logger.info("Attached worktree at '%s' to existing branch '%s'", worktree_path, branch_name)

    return os.path.abspath(worktree_path)
