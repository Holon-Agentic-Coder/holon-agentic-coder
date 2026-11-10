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
- find_worktree_for_branch: Find the attached worktree that has a branch checked out.
- ensure_pr_worktree: Reuse the worktree of a PR head branch (normally flow-<slug>), or provision pr<N>-<slug>.
- resolve_review_worktree: Pick the host worktree a review-loop container works in for a review target.
- provision_reviewer_worktree / prune_reviewer_worktree / ephemeral_reviewer_worktree: Ephemeral flow-<slug>-rev<i>
  sibling worktrees for reviewer turns that need an isolated checkout.
- sandbox_worktree_mounts: Validate a worktree and build the docker bind mounts that expose it to a role container.
- fetch_origin_branch: Fetch a branch on the host, so a container working on a mounted worktree never fetches.
"""

from __future__ import annotations

import logging
import os
import re
import stat
import struct
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sandbox_executor.git import branch_exists, resolve_ref, run_git
from sandbox_executor.redaction import redact_agent_secrets

logger = logging.getLogger(__name__)

#: The unprivileged uid and gid the sandbox image runs as; a Linux bind mount keeps host ownership, so a mounted role
#: container (review loop or executor) can only write a worktree and git directories that this user may write.
SANDBOX_CONTAINER_UID = 1000
SANDBOX_CONTAINER_GID = 1000

#: POSIX ACL entry tags and the write permission bit, as stored in the system.posix_acl_access xattr.
_ACL_USER = 0x02
_ACL_MASK = 0x10
_ACL_WRITE = 0x02

__all__ = [
    "checked_out_branch",
    "default_branch_worktree_problem",
    "derive_worktree_layout",
    "ensure_flow_worktree_off_default",
    "ensure_pr_worktree",
    "ensure_worktree",
    "ephemeral_reviewer_worktree",
    "fetch_origin_branch",
    "find_worktree_for_branch",
    "is_bare_repository",
    "is_default_branch_worktree",
    "is_working_tree",
    "normalise_base_branch",
    "provision_reviewer_worktree",
    "prune_reviewer_worktree",
    "prune_worktrees",
    "repository_default_branch",
    "resolve_review_worktree",
    "sandbox_worktree_mounts",
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


def find_worktree_for_branch(worktree: str, branch: str) -> str | None:
    """Return the attached worktree of the repository containing worktree that has branch checked out.

    Args:
        worktree: Path inside the repository (any of its worktrees, or a bare clone).
        branch: Short branch name.

    Returns:
        str | None: The worktree's absolute path, or None when no existing worktree has the branch checked out.
    """
    if not worktree or not os.path.isdir(worktree) or not branch.strip():
        return None
    res = run_git(["worktree", "list", "--porcelain"], cwd=worktree, check=False)
    if res.returncode != 0:
        return None
    path: str | None = None
    for line in res.stdout.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree ") :]
        elif line == f"branch refs/heads/{branch.strip()}" and path and os.path.isdir(path):
            return os.path.normpath(path)
    return None


def _clean_slug(text: str) -> str:
    """Return text reduced to a path- and branch-safe slug."""
    return re.sub(r"[^a-zA-Z0-9_\-]+", "-", text.strip()).strip("-")


def ensure_pr_worktree(worktree: str, pr_number: int, head_branch: str) -> str:
    """Reuse the worktree that has a PR's head branch checked out, or provision a pr<N>-<slug> sibling for it.

    A flow run's flow-<slug> worktree is the one that normally has the head branch checked out, so a review of that
    PR reuses it and never creates a second worktree for the same branch.

    Args:
        worktree: Path inside the repository; it only selects the repository and the sibling directory.
        pr_number: Pull request number.
        head_branch: The PR's head branch.

    Returns:
        str: Absolute path to the worktree with head_branch checked out.

    Raises:
        RuntimeError: If the head branch exists neither locally nor on origin, if the pr<N>-<slug> path exists but is
            not that branch's worktree, or if 'git worktree add' fails.
    """
    toplevel = _worktree_toplevel(worktree)
    prune_worktrees(toplevel)
    existing = find_worktree_for_branch(toplevel, head_branch)
    if existing:
        logger.info("Reused worktree '%s' for PR #%s (%s)", existing, pr_number, head_branch)
        return existing
    path = os.path.join(os.path.dirname(toplevel), f"pr{pr_number}-{_clean_slug(head_branch) or 'head'}")
    if os.path.exists(path):
        raise RuntimeError(
            f"PR worktree path '{path}' exists but does not have '{head_branch}' checked out; check the branch out "
            f"in it or remove it with 'git worktree remove {path}'."
        )
    run_git(
        ["fetch", "--no-tags", "origin", f"+refs/heads/{head_branch}:refs/remotes/origin/{head_branch}"],
        cwd=toplevel,
        check=False,
        timeout=300,
    )
    if not branch_exists(toplevel, head_branch, check_remote=True):
        raise RuntimeError(
            f"PR #{pr_number} head branch '{head_branch}' exists neither locally nor on origin (a fork PR?); check it "
            "out in a worktree and pass --target-worktree <path>."
        )
    return _attach_or_create_worktree(toplevel, path, head_branch, (f"origin/{head_branch}",))


def resolve_review_worktree(worktree: str, head_branch: str, pr_number: int | None = None) -> str:
    """Pick the host worktree a review-loop container works in for a review of head_branch.

    The given worktree when it has head_branch checked out; otherwise the existing worktree of head_branch (normally
    the run's flow-<slug>); otherwise, for a PR, a new pr<N>-<slug> worktree (see ensure_pr_worktree).

    Args:
        worktree: The --target-worktree path; it also selects the repository.
        head_branch: The branch under review (a PR's head branch).
        pr_number: The PR number when the target is a pull request.

    Returns:
        str: Absolute path to a worktree with head_branch checked out.

    Raises:
        RuntimeError: If no worktree of a branch target exists, or a PR worktree cannot be provisioned.
    """
    if checked_out_branch(worktree) == head_branch:
        return _worktree_toplevel(worktree)
    existing = find_worktree_for_branch(worktree, head_branch)
    if existing:
        return existing
    if pr_number is not None:
        return ensure_pr_worktree(worktree, pr_number, head_branch)
    raise RuntimeError(
        f"No worktree has branch '{head_branch}' checked out; the review-loop container reviews a host worktree in "
        f"place. Create one with 'git worktree add <path> {head_branch}' and pass --target-worktree <path>."
    )


def _reviewer_worktree_path(worktree: str, index: int) -> str:
    """Return the flow-<slug>-rev<index> sibling path of worktree's top-level directory."""
    if isinstance(index, bool) or not isinstance(index, int) or index < 1:
        raise ValueError(f"reviewer index must be a positive integer, got {index!r}")
    return f"{_worktree_toplevel(worktree)}-rev{index}"


def prune_reviewer_worktree(worktree: str, index: int) -> None:
    """Remove the flow-<slug>-rev<index> sibling of worktree, if registered, and prune stale records.

    Only 'git worktree remove' deletes anything, so a directory that is not a registered worktree is left in place.

    Args:
        worktree: The worktree the reviewer worktree was provisioned from.
        index: 1-based reviewer index.
    """
    path = _reviewer_worktree_path(worktree, index)
    if os.path.exists(path):
        res = run_git(["worktree", "remove", "--force", path], cwd=worktree, check=False)
        if res.returncode != 0:
            logger.warning("git worktree remove failed for '%s': %s", path, res.stderr.strip())
    prune_worktrees(worktree)


def provision_reviewer_worktree(worktree: str, index: int) -> str:
    """Provision the ephemeral flow-<slug>-rev<index> sibling worktree, detached at worktree's HEAD.

    A survivor of an earlier crashed iteration is removed first.

    Args:
        worktree: The flow worktree under review.
        index: 1-based reviewer index.

    Returns:
        str: Absolute path to the new reviewer worktree.

    Raises:
        ValueError: If index is not a positive integer.
        RuntimeError: If 'git worktree add' fails (for example, a non-worktree directory occupies the path).
    """
    path = _reviewer_worktree_path(worktree, index)
    prune_reviewer_worktree(worktree, index)
    run_git(["worktree", "add", "--detach", path, "HEAD"], cwd=worktree, check=True)
    return os.path.abspath(path)


@contextmanager
def ephemeral_reviewer_worktree(worktree: str, index: int) -> Iterator[str]:
    """Yield a flow-<slug>-rev<index> reviewer worktree that is removed and pruned when the block exits, however."""
    path = provision_reviewer_worktree(worktree, index)
    try:
        yield path
    finally:
        prune_reviewer_worktree(worktree, index)


def fetch_origin_branch(worktree: str, branch: str) -> None:
    """Fetch origin's branch into refs/remotes/origin/<branch> on the host, so a container mounting worktree need not.

    Raises:
        RuntimeError: If the fetch fails (the redacted git error is included).
    """
    res = run_git(
        ["fetch", "--no-tags", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}"],
        cwd=worktree,
        check=False,
    )
    if res.returncode != 0:
        detail = redact_agent_secrets((res.stderr or "").strip()) or f"exit code {res.returncode}"
        raise RuntimeError(f"could not fetch branch '{branch}' from origin into '{worktree}': {detail}")


def _read_pointer(path: str, prefix: str = "") -> str | None:
    """Return the path a git pointer file (.git, commondir) names, made absolute relative to its directory."""
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read().strip()
    except OSError:
        return None
    if not content.startswith(prefix):
        return None
    target = content[len(prefix) :].strip()
    return os.path.normpath(os.path.join(os.path.dirname(path), target)) if target else None


def _within(path: str, root: str) -> bool:
    """Return True when path is root or lies under it."""
    return path == root or path.startswith(root + os.sep)


def _acl_user_write(path: str, uid: int) -> bool | None:
    """Return whether the access ACL of path lets named user uid write past the mask; None when uid has no entry."""
    try:
        acl = os.getxattr(path, "system.posix_acl_access")
    except (AttributeError, OSError):
        return None
    # Linux xattr layout: a 4-byte version header, then 8-byte entries of (u16 tag, u16 perm, u32 id), little-endian.
    entries = [struct.unpack_from("<HHI", acl, offset) for offset in range(4, len(acl) - 7, 8)]
    user_perms = [perm for tag, perm, entry_id in entries if tag == _ACL_USER and entry_id == uid]
    if not user_perms:
        return None
    mask_perms = [perm for tag, perm, _ in entries if tag == _ACL_MASK]
    return bool(user_perms[0] & _ACL_WRITE) and all(perm & _ACL_WRITE for perm in mask_perms)


def _container_cannot_write(path: str) -> bool:
    """Return True when the sandbox user may not write the directory path, judged by owner, named-user ACL and mode."""
    st = os.stat(path)
    if st.st_uid == SANDBOX_CONTAINER_UID:
        return not st.st_mode & stat.S_IWUSR
    acl_write = _acl_user_write(path, SANDBOX_CONTAINER_UID)
    if acl_write is not None:
        return not acl_write
    if st.st_gid == SANDBOX_CONTAINER_GID:
        return not st.st_mode & stat.S_IWGRP
    return not st.st_mode & stat.S_IWOTH


def _container_write_problem(role: str, *paths: str) -> str | None:
    """On Linux, explain which of paths the sandbox user cannot write through a bind mount; None when all are fine."""
    if not sys.platform.startswith("linux"):
        return None
    unwritable = [path for path in dict.fromkeys(paths) if _container_cannot_write(path)]
    if not unwritable:
        return None
    return (
        f"the {role} container runs as uid {SANDBOX_CONTAINER_UID} and a Linux bind mount keeps host ownership, "
        f"so it cannot write {', '.join(repr(p) for p in unwritable)}. Run holon as uid {SANDBOX_CONTAINER_UID}, "
        f"or grant it write access (for example 'setfacl -R -m u:{SANDBOX_CONTAINER_UID}:rwX,"
        f"d:u:{SANDBOX_CONTAINER_UID}:rwX <path>')."
    )


def _validated_sandbox_toplevel(target_worktree: str | None, role: str) -> str:
    """Return the top-level directory of a worktree that may be mounted, or raise RuntimeError explaining why not."""
    if not target_worktree:
        raise RuntimeError(
            f"the {role} container works on a host worktree in place and needs an explicit target worktree; pass "
            "--target-worktree <path> (never the current directory implicitly)."
        )
    problem = target_worktree_problem(target_worktree, subject="Sandbox worktree")
    if problem:
        raise RuntimeError(problem)
    toplevel = _worktree_toplevel(target_worktree)
    problem = default_branch_worktree_problem(
        toplevel, repository_default_branch(toplevel), command=f"The {role} container", subject="Sandbox worktree"
    )
    if problem:
        raise RuntimeError(problem)
    worktree_config = run_git(
        ["config", "--type=bool", "--get", "extensions.worktreeConfig"], cwd=toplevel, check=False
    )
    if (worktree_config.stdout or "").strip() == "true":
        raise RuntimeError(
            f"Sandbox worktree '{toplevel}' belongs to a repository with extensions.worktreeConfig enabled; a "
            "container could plant a config.worktree that later runs code on the host. Disable it with 'git config "
            "--unset extensions.worktreeConfig' or review in a fresh clone."
        )
    return toplevel


def sandbox_worktree_mounts(target_worktree: str | None, mountpoint: str, role: str = "review-loop") -> list[str]:
    """Validate a host worktree and return the docker arguments that bind-mount it into a role container.

    The worktree must be an attached, non-bare working tree that does not have the default branch checked out. Its
    top-level directory is mounted read-write at mountpoint. A linked worktree's git directory and the repository's
    common git directory are mounted at their host paths, which the worktree's '.git' pointer names, so git works in
    the container without network access. Everything that makes host git run code is overlaid read-only, so the
    container cannot plant a hook or configuration that later executes on the host: the repository config (and any
    config.worktree), hooks/, modules/, the whole worktrees/ administrative directory, and the '.git', 'gitdir' and
    'commondir' pointers. The worktrees/ overlay is what keeps every SIBLING worktree's administrative files
    (commondir, gitdir, HEAD, a plantable config.worktree) and the creation of new worktrees/ entries out of the
    container's reach -- host git reads those files on its next command in that worktree. The mounted worktree's own
    administrative directory is re-mounted read-write on top (docker layers the mount with the deeper destination over
    the shallower one), restoring only the writes its own git needs (HEAD, index, FETCH_HEAD); its pointer files and
    config.worktree stay read-only through the still-deeper file overlays, and absent hooks/, modules/, worktrees/
    and config.worktree are created first so the container cannot create them itself. A repository with
    extensions.worktreeConfig enabled is refused: git would then read every worktree's config.worktree, which the
    container could rewrite in its own writable git directory. On Linux, where a bind mount keeps host
    ownership, a worktree or git directory that the image's user (uid SANDBOX_CONTAINER_UID) cannot write is refused
    up front rather than failing every pass inside the container.

    Args:
        target_worktree: Host worktree path (a subdirectory of one is accepted); required.
        mountpoint: Container path of the workspace.
        role: Role of the container the worktree is mounted into, named in refusals.

    Returns:
        list[str]: Docker '-v' arguments.

    Raises:
        RuntimeError: If no worktree is given, it is not an attached non-default-branch worktree, its '.git'
            pointer cannot be read, the repository enables extensions.worktreeConfig, or (on Linux) the container
            user cannot write the worktree or its git directories.
    """
    toplevel = _validated_sandbox_toplevel(target_worktree, role)
    dot_git = os.path.join(toplevel, ".git")
    args = ["-v", f"{toplevel}:{mountpoint}"]
    if os.path.isdir(dot_git):
        git_dir = common_dir = dot_git
        read_only: list[str] = []
    else:
        git_dir = _read_pointer(dot_git, "gitdir:") or ""
        if not os.path.isdir(git_dir):
            raise RuntimeError(f"cannot resolve the git directory of worktree '{toplevel}' from its .git file")
        common_dir = _read_pointer(os.path.join(git_dir, "commondir")) or git_dir
        args += ["-v", f"{common_dir}:{common_dir}"]
        if git_dir != common_dir:
            # Re-mounted read-write on top of the read-only worktrees/ overlay below (docker layers the deeper
            # destination over the shallower one): the mounted worktree's own git must write its HEAD, index and
            # FETCH_HEAD, while its pointer files stay read-only through the still-deeper file overlays.
            args += ["-v", f"{git_dir}:{git_dir}"]
        read_only = [dot_git, os.path.join(git_dir, "commondir"), os.path.join(git_dir, "gitdir")]
    problem = _container_write_problem(role, toplevel, git_dir, common_dir)
    if problem:
        raise RuntimeError(problem)
    # An absent hooks/, modules/ or worktrees/ could be created by the container (planting modules/<name>/hooks for a
    # later host 'git submodule' command, or a new worktrees/ entry whose pointer files host git would follow), and an
    # absent config.worktree could be planted against a host operator who later enables extensions.worktreeConfig, so
    # make each exist before overlaying it. worktrees/ is overlaid read-only as a whole: a sibling worktree's
    # administrative files (commondir, gitdir, HEAD, config.worktree) are what the next host git command run in that
    # sibling reads, so none of them may be container-writable.
    for name in ("hooks", "modules", "worktrees"):
        os.makedirs(os.path.join(common_dir, name), exist_ok=True)
    for path in dict.fromkeys((os.path.join(common_dir, "config.worktree"), os.path.join(git_dir, "config.worktree"))):
        open(path, "a", encoding="utf-8").close()
    read_only += [
        os.path.join(common_dir, name) for name in ("config", "config.worktree", "hooks", "modules", "worktrees")
    ]
    read_only.append(os.path.join(git_dir, "config.worktree"))

    for host_path in dict.fromkeys(read_only):
        if not os.path.exists(host_path):
            continue
        rel = os.path.relpath(host_path, toplevel)
        container_path = os.path.join(mountpoint, rel) if _within(host_path, toplevel) else host_path
        args += ["-v", f"{host_path}:{container_path}:ro"]
    return args
