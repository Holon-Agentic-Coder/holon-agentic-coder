"""Git worktree resolution and lifecycle automation for Holon Flow.

Provides typed utilities for inspecting Git repository topology, self-healing
stale worktree administrative registrations, and ensuring dedicated worktree
provisioning for autonomous pipeline runs:
- is_bare_repository: Detect whether a repository directory is a bare Git repo.
- is_working_tree: Check whether a directory is a valid non-bare Git working tree.
- prune_worktrees: Prune stale worktree administrative records.
- derive_worktree_layout: Determine target worktree path and branch name from a slug.
- ensure_worktree: Provision or attach an isolated worktree directory for a flow run.
- target_worktree_problem: Explain, actionably, why a path is not a usable flow target worktree.
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
    "target_worktree_problem",
]

TARGET_WORKTREE_PROBLEM_TEMPLATE: str = (
    "{subject} '{path}' {reason}. holon flow needs --target-worktree to be an attached, non-bare git worktree "
    "because Stages 4 and 5 run host git operations in it. Create one with 'git worktree add <path> <branch>' "
    "from your repository, or pass a bare clone as --target-worktree to auto-provision one{hint}."
)


def is_bare_repository(target_worktree: str) -> bool:
    """Detect whether a repository directory is a bare Git repository.

    Args:
        target_worktree: Path to directory to inspect.

    Returns:
        bool: True if target_worktree exists and represents a bare Git repository,
            False otherwise.
    """
    if not target_worktree or not os.path.exists(target_worktree):
        return False

    res = run_git(["rev-parse", "--is-bare-repository"], cwd=target_worktree, check=False)
    return res.returncode == 0 and res.stdout.strip().lower() == "true"


def is_working_tree(target_worktree: str) -> bool:
    """Check whether a directory is an active non-bare Git working tree.

    Args:
        target_worktree: Path to directory to inspect.

    Returns:
        bool: True if target_worktree exists, is a valid Git repository, and is not
            a bare repository, False otherwise.
    """
    if not target_worktree or not os.path.isdir(target_worktree):
        return False

    res = run_git(["rev-parse", "--is-inside-work-tree"], cwd=target_worktree, check=False)
    if res.returncode != 0 or res.stdout.strip().lower() != "true":
        return False

    return not is_bare_repository(target_worktree)


def prune_worktrees(target_worktree: str) -> None:
    """Prune stale Git worktree records where linked directories were deleted.

    Args:
        target_worktree: Path to Git repository or bare repository directory.

    Side-effects:
        Executes 'git worktree prune' to self-heal orphaned metadata under
        '.git/worktrees/' or repository administrative folders.
    """
    if not target_worktree or not os.path.exists(target_worktree):
        return
    res = run_git(["worktree", "prune"], cwd=target_worktree, check=False)
    if res.returncode == 0:
        logger.debug("Pruned stale worktrees in %s", target_worktree)
    else:
        logger.warning("git worktree prune failed in %s: %s", target_worktree, res.stderr.strip())


def derive_worktree_layout(base_target_worktree: str, slug: str) -> tuple[str, str]:
    """Derive canonical target worktree path and branch name for a flow task.

    Args:
        base_target_worktree: Base repository path (working tree or bare clone).
        slug: Intent slug identifier.

    Returns:
        tuple[str, str]: Tuple of (worktree_path, branch_name).
    """
    clean_slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", slug.strip()).strip("-")
    if not clean_slug:
        clean_slug = "flow"
    branch_name = f"flow-{clean_slug}"

    resolved_base = Path(base_target_worktree).resolve()
    if not os.path.exists(base_target_worktree) and resolved_base.name == branch_name:
        worktree_path = resolved_base
    else:
        parent_dir = resolved_base.parent
        worktree_path = parent_dir / branch_name

    return os.path.normpath(str(worktree_path)), branch_name


def _resolve_base_git_dir(target_worktree: str) -> str:
    """Resolve the root Git or bare repository directory associated with target_worktree.

    Args:
        target_worktree: Target path or repository directory.

    Returns:
        str: Absolute path to the resolved Git directory.

    Raises:
        RuntimeError: If no valid Git repository can be found.
    """
    if os.path.exists(target_worktree) and (
        is_bare_repository(target_worktree) or (Path(target_worktree) / ".git").exists()
    ):
        return os.path.abspath(target_worktree)

    curr = Path(target_worktree).resolve()
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

    raise RuntimeError(f"Unable to resolve a valid Git repository from directory: '{target_worktree}'")


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


def ensure_worktree(target_worktree: str, slug: str, target_branch: str = "origin/main") -> str:
    """Ensure a dedicated Git worktree is provisioned and active for a flow run.

    Detects if target_worktree is already an active working tree. If not (or if bare),
    self-heals stale worktree registrations using 'git worktree prune', resolves
    the underlying Git directory, derives the worktree layout, and attaches to
    an existing branch or creates a new dedicated worktree branch.

    Args:
        target_worktree: Target repository path or bare clone directory.
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

    if os.path.exists(target_worktree) and is_working_tree(target_worktree):
        return os.path.abspath(target_worktree)

    base_git_dir = _resolve_base_git_dir(target_worktree)
    prune_worktrees(base_git_dir)

    worktree_path, branch_name = derive_worktree_layout(target_worktree, clean_slug)
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


def _target_worktree_reason(target_worktree: str) -> str | None:
    """Return why target_worktree is not an attached, non-bare git working tree, or None when it is."""
    if not target_worktree or not os.path.exists(target_worktree):
        return "does not exist (it may have been deleted)"
    if not os.path.isdir(target_worktree):
        return "is not a directory"
    if is_bare_repository(target_worktree):
        return "is a bare repository with no working tree"
    if not is_working_tree(target_worktree):
        return "is not inside a git working tree"
    return None


def target_worktree_problem(
    target_worktree: str,
    subject: str = "Target worktree",
    hint: str = "",
) -> str | None:
    """Explain why a path cannot serve as the target worktree of a flow run.

    Only read-only 'git rev-parse' queries are issued; nothing is created, pruned or modified.

    Args:
        target_worktree: Path to validate (a subdirectory of a worktree is accepted).
        subject: Leading noun phrase naming where the path came from (default: 'Target worktree').
        hint: Optional extra remedy appended to the message, starting with a conjunction (e.g. ', or ...').

    Returns:
        str | None: None when the path is an attached, non-bare git working tree, otherwise one actionable
            error message naming the absolute path, the reason and how to fix it.
    """
    reason = _target_worktree_reason(target_worktree)
    if reason is None:
        return None
    return TARGET_WORKTREE_PROBLEM_TEMPLATE.format(
        subject=subject,
        path=os.path.abspath(target_worktree) if target_worktree else "",
        reason=reason,
        hint=hint,
    )
