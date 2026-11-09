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
- checked_out_branch: Read the branch a working tree has checked out.
- normalise_base_branch: Strip 'origin/' and 'refs/heads/' so 'origin/main' and 'main' compare equal.
- repository_default_branch: Read the branch origin/HEAD points at (default: 'main').
- is_default_branch_worktree: Decide whether a working tree has the default branch checked out.
- ensure_flow_worktree_off_default: Provision or reuse the flow-<slug> sibling of a default-branch worktree.
- default_branch_worktree_problem: Explain, actionably, why a default-branch worktree is refused.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from sandbox_executor.git import branch_exists, resolve_ref, run_git

logger = logging.getLogger(__name__)

__all__ = [
    "checked_out_branch",
    "default_branch_worktree_problem",
    "derive_worktree_layout",
    "ensure_flow_worktree_off_default",
    "ensure_worktree",
    "is_bare_repository",
    "is_default_branch_worktree",
    "is_working_tree",
    "normalise_base_branch",
    "prune_worktrees",
    "repository_default_branch",
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


def _resolve_start_point(base_git_dir: str, *preferred: str) -> str:
    """Resolve an available starting revision or ref for a new worktree branch.

    Args:
        base_git_dir: Base Git repository directory.
        preferred: Preferred refs, tried in order before the 'origin/main', 'main', 'HEAD', 'master' fallbacks.

    Returns:
        str: Existing symbolic ref or commit to branch from.
    """
    candidates = [*preferred, "origin/main", "main", "HEAD", "master"]
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

    return _attach_or_create_worktree(base_git_dir, worktree_path, branch_name, (target_branch,))


def _attach_or_create_worktree(
    base_git_dir: str,
    worktree_path: str,
    branch_name: str,
    preferred_start_points: tuple[str, ...],
) -> str:
    """Add a worktree at worktree_path on branch_name, creating the branch when it is new.

    Args:
        base_git_dir: Git repository (working tree or bare clone) to run 'git worktree add' in.
        worktree_path: Absolute path of the worktree to create.
        branch_name: Branch to attach, or to create with '--no-track' when it exists neither locally nor on origin.
        preferred_start_points: Refs a new branch prefers to start from, before the standard fallbacks.

    Returns:
        str: Absolute path to the new worktree.

    Raises:
        RuntimeError: If 'git worktree add' fails.
    """
    os.makedirs(os.path.dirname(worktree_path), exist_ok=True)

    if not branch_exists(base_git_dir, branch_name, check_remote=True):
        start_point = _resolve_start_point(base_git_dir, *preferred_start_points)
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


def checked_out_branch(worktree: str) -> str | None:
    """Return the short name of the branch a working tree has checked out.

    Args:
        worktree: Path to a working tree (or a subdirectory of one).

    Returns:
        str | None: The branch name, or None for a detached HEAD, a missing directory or a non-repository.
    """
    if not worktree or not os.path.isdir(worktree):
        return None
    res = run_git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=worktree, check=False)
    branch = res.stdout.strip()
    return branch if res.returncode == 0 and branch else None


def normalise_base_branch(target_branch: str | None) -> str:
    """Return the local branch name a target branch refers to, so 'origin/main' and 'main' compare equal.

    Args:
        target_branch: Branch as written in an intent or CLI option (e.g. 'origin/main', 'refs/heads/main', 'main').

    Returns:
        str: The name without a 'refs/heads/', 'refs/remotes/origin/' or 'origin/' prefix (default: 'main').
    """
    name = (target_branch or "").strip()
    for prefix in ("refs/heads/", "refs/remotes/origin/", "origin/"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    return name or "main"


def repository_default_branch(worktree: str) -> str:
    """Return the repository's default branch: the branch 'refs/remotes/origin/HEAD' points at.

    Args:
        worktree: Path inside the repository.

    Returns:
        str: The default branch name, or 'main' when origin/HEAD is not set or the path is not a repository.
    """
    if not worktree or not os.path.isdir(worktree):
        return "main"
    res = run_git(["symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD"], cwd=worktree, check=False)
    if res.returncode != 0 or not res.stdout.strip():
        return "main"
    return normalise_base_branch(res.stdout.strip())


def is_default_branch_worktree(worktree: str, target_branch: str | None) -> bool:
    """Decide whether worktree is an attached working tree with the base branch checked out.

    Args:
        worktree: Path to inspect.
        target_branch: The base branch ('origin/main' and 'main' are equivalent).

    Returns:
        bool: True only when worktree is a non-bare working tree whose checked-out branch is the base branch. A
            detached HEAD is never a default-branch worktree.
    """
    return is_working_tree(worktree) and checked_out_branch(worktree) == normalise_base_branch(target_branch)


def _worktree_toplevel(worktree: str) -> str:
    """Return the absolute top-level directory of the working tree containing worktree."""
    res = run_git(["rev-parse", "--show-toplevel"], cwd=worktree, check=False)
    top = res.stdout.strip()
    return os.path.normpath(top) if res.returncode == 0 and top else os.path.abspath(worktree)


def flow_worktree_layout(default_worktree: str, slug: str) -> tuple[str, str]:
    """Derive the flow-<slug> sibling of a working tree's top-level directory, read-only.

    Args:
        default_worktree: Path to a working tree (or a subdirectory of one).
        slug: Intent slug identifier.

    Returns:
        tuple[str, str]: Tuple of (worktree_path, branch_name); nothing is created.
    """
    return derive_worktree_layout(_worktree_toplevel(default_worktree), slug)


def ensure_flow_worktree_off_default(default_worktree: str, slug: str, target_branch: str | None) -> str:
    """Provision or reuse the flow-<slug> sibling of a default-branch worktree, branched off origin/<base>.

    The default-branch worktree only selects the repository and the base branch; it is never checked out, committed
    to or written into. An existing attached flow-<slug> worktree is reused as it is.

    Args:
        default_worktree: Working tree that has the base branch checked out.
        slug: Intent slug identifier.
        target_branch: The base branch ('origin/main' and 'main' are equivalent).

    Returns:
        str: Absolute path to the flow worktree.

    Raises:
        ValueError: If slug is empty.
        RuntimeError: If the derived path exists but is not a working tree, if it has the base branch checked out,
            or if 'git worktree add' fails.
    """
    if not slug.strip():
        raise ValueError("Intent slug must be non-empty to provision a flow worktree.")
    base = normalise_base_branch(target_branch)
    toplevel = _worktree_toplevel(default_worktree)
    prune_worktrees(toplevel)
    worktree_path, branch_name = derive_worktree_layout(toplevel, slug)
    if os.path.exists(worktree_path):
        if not is_working_tree(worktree_path):
            raise RuntimeError(
                f"Flow worktree path '{worktree_path}' exists but is not a git working tree; move it aside or "
                f"attach it with 'git worktree add {worktree_path} {branch_name}'."
            )
        if checked_out_branch(worktree_path) == base:
            raise RuntimeError(
                f"Flow worktree '{worktree_path}' has the base branch '{base}' checked out; check out "
                f"'{branch_name}' in it or remove it with 'git worktree remove {worktree_path}'."
            )
        logger.info("Reused flow worktree at '%s'", worktree_path)
        return os.path.abspath(worktree_path)
    if resolve_ref(toplevel, f"origin/{base}") is None:
        logger.warning("'origin/%s' does not resolve in '%s'; the flow branch starts from a fallback.", base, toplevel)
    return _attach_or_create_worktree(toplevel, worktree_path, branch_name, (f"origin/{base}", base))


def default_branch_worktree_problem(
    worktree: str,
    base_branch: str | None,
    flow_worktree: str | None = None,
    command: str = "holon flow",
    subject: str = "Target worktree",
) -> str | None:
    """Explain why a worktree with the default branch checked out is refused, or return None when it is not one.

    Only read-only git queries are issued; nothing is created or modified.

    Args:
        worktree: Path to check.
        base_branch: The default branch ('origin/main' and 'main' are equivalent).
        flow_worktree: The flow-<slug> worktree to use instead, when known.
        command: The refusing command, named in the message.
        subject: Leading noun phrase naming where the path came from.

    Returns:
        str | None: One actionable message naming the absolute path, the branch, why it is refused and how to fix
            it, or None when worktree does not have the default branch checked out.
    """
    if not is_default_branch_worktree(worktree, base_branch):
        return None
    base = normalise_base_branch(base_branch)
    if flow_worktree:
        remedy = f"Use the flow worktree instead: pass --target-worktree {flow_worktree}"
    else:
        remedy = (
            f"Run it in the run's flow-<slug> worktree or another dedicated worktree instead: create one with "
            f"'git worktree add <path> <branch>' (or 'git worktree add <path> -b <branch> origin/{base}' for a new "
            f"branch) and pass --target-worktree <path>"
        )
    return (
        f"{subject} '{os.path.abspath(worktree)}' has the default branch '{base}' checked out. {command} checks out "
        f"branches, commits and writes ledger or calibration records in the worktree it runs in, and the "
        f"default-branch worktree must stay identical to origin/{base}. {remedy}."
    )
