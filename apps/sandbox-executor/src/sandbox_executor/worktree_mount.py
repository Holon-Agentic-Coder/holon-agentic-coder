"""Host-provisioned worktree bind mounts for role containers.

The host owns the worktree: it provisions (or reuses) the run's ``flow-<slug>`` worktree, validates it and
describes how it is mounted. The container only works in it. Three bind mounts keep the linked worktree's git
plumbing resolvable inside the container without rewriting any file:

- the worktree at the container workspace path, where the role entrypoint works;
- the worktree again at its own host path, so the admin directory's ``gitdir`` back-pointer stays valid and the
  worktree is never reported as prunable;
- the git common directory at its own host path, so the worktree's ``.git`` file (``gitdir: <common>/worktrees/...``)
  resolves unchanged. Skipped when the common directory already lies inside the worktree (a main checkout).

Runtime-only git config (``GIT_CONFIG_COUNT`` entries, never ``~/.gitconfig``) turns off automatic gc and gc's
worktree pruning, because every *other* worktree registered in the common directory is missing in the container and
``git gc`` would otherwise drop its registration on the host. An explicit ``git worktree prune`` ignores that config,
so role entrypoints must never run it.

Docker's ``--mount type=bind`` form is used instead of ``-v``: it refuses a missing source rather than silently
creating a root-owned host directory.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass

from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.git import run_git
from sandbox_executor.git_flow import (
    checked_out_branch,
    ensure_flow_worktree_off_default,
    ensure_worktree,
    is_default_branch_worktree,
    target_worktree_problem,
)
from sandbox_executor.redaction import redact_agent_secrets

logger = logging.getLogger(__name__)

__all__ = [
    "CONTAINER_RESERVED_PATHS",
    "WorktreeMount",
    "WorktreeMountError",
    "docker_mount_args",
    "git_config_entries",
    "resolve_worktree_mount",
]

#: Container paths a host mount may never be, or fall under: the agent user's home (session mounts and the
#: workspace itself) and the image's system directories.
CONTAINER_RESERVED_PATHS = ("/home/holon", "/bin", "/dev", "/etc", "/lib", "/proc", "/run", "/sbin", "/sys", "/usr")

#: The agent image's unprivileged user id (Dockerfile: ``useradd -m holon``, the image's first user).
CONTAINER_USER_UID = 1000


class WorktreeMountError(ValueError):
    """A host path cannot be bind-mounted as a role container's workspace."""


@dataclass(frozen=True)
class WorktreeMount:
    """One validated host worktree and where a role container sees it.

    Attributes:
        host_worktree: Real path of the attached, non-bare host worktree.
        git_common_dir: Real path of the repository's git common directory.
        container_workspace: Container path the worktree is mounted at for the role entrypoint.
    """

    host_worktree: str
    git_common_dir: str
    container_workspace: str = SANDBOX_WORKSPACE_MOUNT


def _is_within(path: str, root: str) -> bool:
    """Return True when ``path`` is ``root`` or lies under it."""
    return path == root or path.startswith(root.rstrip("/") + "/")


def _provision(host_worktree: str, slug: str | None, target_branch: str) -> str:
    """Return the worktree to mount, provisioning ``flow-<slug>`` when the path is absent or the default branch."""
    if not slug:
        return host_worktree
    if not os.path.exists(host_worktree):
        return ensure_worktree(host_worktree, slug, target_branch)
    if is_default_branch_worktree(host_worktree, target_branch):
        return ensure_flow_worktree_off_default(host_worktree, slug, target_branch)
    return host_worktree


def _git_path(worktree: str, flag: str) -> str:
    """Return the real path git reports for ``rev-parse --path-format=absolute <flag>``."""
    res = run_git(["rev-parse", "--path-format=absolute", flag], cwd=worktree, check=False)
    if res.returncode != 0 or not res.stdout.strip():
        raise WorktreeMountError(
            f"Cannot read '{flag}' of '{worktree}': {redact_agent_secrets(res.stderr.strip() or 'no output')}"
        )
    return os.path.realpath(res.stdout.strip())


def _check_mountable(path: str, role: str) -> None:
    """Refuse a mount path that is relative, contains a comma or would shadow a container-owned path."""
    if not os.path.isabs(path):
        raise WorktreeMountError(f"The {role} '{path}' is not an absolute path.")
    if "," in path:
        raise WorktreeMountError(f"The {role} '{path}' contains a comma, which docker --mount cannot express.")
    if path == "/" or any(_is_within(path, reserved) for reserved in CONTAINER_RESERVED_PATHS):
        raise WorktreeMountError(f"The {role} '{path}' would shadow a container path; move the worktree elsewhere.")


def _check_attached(worktree: str, target_branch: str) -> tuple[str, str]:
    """Return ``(git dir, common dir)`` of an attached, non-default-branch worktree top level, or raise."""
    if worktree != _git_path(worktree, "--show-toplevel"):
        raise WorktreeMountError(f"'{worktree}' is a subdirectory of a worktree; mount its top-level directory.")
    if checked_out_branch(worktree) is None:
        raise WorktreeMountError(f"'{worktree}' has a detached HEAD; check out the run's branch before mounting it.")
    if is_default_branch_worktree(worktree, target_branch):
        raise WorktreeMountError(
            f"'{worktree}' has the default branch checked out; mount the run's flow-<slug> worktree instead."
        )
    git_dir = _git_path(worktree, "--git-dir")
    common_dir = _git_path(worktree, "--git-common-dir")
    if git_dir != common_dir and os.path.dirname(git_dir) != os.path.join(common_dir, "worktrees"):
        raise WorktreeMountError(f"'{worktree}' is not registered as a worktree of '{common_dir}'.")
    return git_dir, common_dir


def _warn_on_foreign_owner(worktree: str) -> None:
    """Warn once when a Linux host worktree is not owned by the container user's uid."""
    if not sys.platform.startswith("linux"):
        return
    owner = os.stat(worktree).st_uid
    if owner != CONTAINER_USER_UID:
        logger.warning(
            "Worktree '%s' is owned by uid %d, but role containers run as uid %d and may not be able to write to it.",
            worktree,
            owner,
            CONTAINER_USER_UID,
        )


def resolve_worktree_mount(
    host_worktree: str, *, slug: str | None = None, target_branch: str = "origin/main"
) -> WorktreeMount:
    """Validate (and, with a slug, provision) the host worktree a role container will work in.

    With ``slug``, an absent path is provisioned through :func:`git_flow.ensure_worktree` and a default-branch
    worktree is swapped for its ``flow-<slug>`` sibling through :func:`git_flow.ensure_flow_worktree_off_default`.
    An existing worktree is never recreated, reset or cleaned.

    Args:
        host_worktree: Host path of the worktree to mount.
        slug: Intent slug used to provision ``flow-<slug>``; without it the path must already be usable.
        target_branch: The base branch ('origin/main' and 'main' are equivalent).

    Returns:
        WorktreeMount: The validated mount description.

    Raises:
        WorktreeMountError: If the path is relative, bare, not an attached worktree top level, detached, on the
            default branch, or would shadow a container path.
    """
    _check_mountable(host_worktree, "worktree")
    try:
        worktree = _provision(host_worktree, slug, target_branch)
    except (RuntimeError, ValueError) as exc:
        raise WorktreeMountError(
            f"Cannot provision a worktree at '{host_worktree}': {redact_agent_secrets(str(exc))}"
        ) from exc
    problem = target_worktree_problem(worktree, subject="Worktree to mount")
    if problem:
        raise WorktreeMountError(problem)
    worktree = os.path.realpath(worktree)
    _check_mountable(worktree, "worktree")
    _, common_dir = _check_attached(worktree, target_branch)
    _check_mountable(common_dir, "git common directory")
    _warn_on_foreign_owner(worktree)
    return WorktreeMount(host_worktree=worktree, git_common_dir=common_dir)


def _bind(source: str, target: str) -> list[str]:
    """One ``--mount type=bind`` argument pair."""
    return ["--mount", f"type=bind,source={source},target={target}"]


def docker_mount_args(mount: WorktreeMount) -> list[str]:
    """Return the ``docker run`` arguments that bind-mount the worktree and keep its git directory resolvable.

    Args:
        mount: The validated worktree mount.

    Returns:
        list[str]: ``--mount type=bind`` pairs for the container workspace, the worktree's own host path and (for a
            linked worktree) the git common directory's host path.
    """
    args = _bind(mount.host_worktree, mount.container_workspace)
    if mount.host_worktree != mount.container_workspace:
        args += _bind(mount.host_worktree, mount.host_worktree)
    if not _is_within(mount.git_common_dir, mount.host_worktree):
        args += _bind(mount.git_common_dir, mount.git_common_dir)
    return args


def git_config_entries(mount: WorktreeMount) -> list[tuple[str, str]]:
    """Return runtime-only git config protecting a mounted worktree and its siblings' registrations.

    Args:
        mount: The validated worktree mount.

    Returns:
        list[tuple[str, str]]: ``(key, value)`` pairs for ``GIT_CONFIG_KEY_<n>``/``GIT_CONFIG_VALUE_<n>``.
    """
    return [
        ("gc.auto", "0"),
        ("gc.worktreePruneExpire", "never"),
        ("safe.directory", mount.container_workspace),
        ("safe.directory", mount.host_worktree),
    ]
