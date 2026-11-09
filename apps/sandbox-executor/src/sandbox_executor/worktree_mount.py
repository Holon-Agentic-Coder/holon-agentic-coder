"""Host-provisioned worktree bind mounts for role containers.

The host owns the worktree: it provisions (or validates) the target worktree, resolves the repository's common git
directory and turns both into ``docker run`` bind-mount arguments. The container owns nothing but the mountpoint: the
worktree appears at :data:`~sandbox_executor.agent_runner.SANDBOX_WORKSPACE_MOUNT` and the common git directory at its
own host path, so a linked worktree's ``.git`` pointer file (``gitdir: <common>/worktrees/<name>``) and the relative
``commondir`` inside it resolve without a clone or network access.

The mount set is deliberately minimal (docs/safety.md, container sandbox): exactly the worktree top level plus, for a
linked worktree, its common git directory. Nothing here decides *whether* a stage mounts a worktree; callers opt in
by passing the resolved :class:`WorkspaceMount` to the dispatcher.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.git import run_git
from sandbox_executor.git_flow import ensure_worktree, is_bare_repository, is_working_tree, target_worktree_problem

__all__ = [
    "WorkspaceMount",
    "WorkspaceMountError",
    "resolve_workspace_mount",
    "workspace_mount_args",
    "workspace_mount_env",
]


class WorkspaceMountError(RuntimeError):
    """Raised when a target worktree cannot be bind-mounted into a role container."""


@dataclass(frozen=True)
class WorkspaceMount:
    """An immutable, validated description of one host worktree bind mount.

    Attributes:
        host_worktree: Absolute real path of the worktree's top level on the host.
        git_common_dir: Absolute real path of the repository's common git directory on the host.
        container_workspace: Container-side mountpoint of the worktree (the sandbox workspace path).
    """

    host_worktree: str
    git_common_dir: str
    container_workspace: str = SANDBOX_WORKSPACE_MOUNT


def _is_within(path: str, root: str) -> bool:
    """Return True when the real path ``path`` is ``root`` or lies beneath it."""
    return os.path.commonpath([path, root]) == root


def _rev_parse_path(worktree: str, flag: str) -> str:
    """Return the absolute real path ``git rev-parse <flag>`` reports for ``worktree``.

    ``--git-dir`` and ``--git-common-dir`` may print a path relative to the working directory, so the output is
    resolved against ``worktree`` before ``realpath`` normalises symlinks (e.g. ``/var`` vs ``/private/var``).
    """
    res = run_git(["rev-parse", flag], cwd=worktree, check=False)
    output = res.stdout.strip()
    if res.returncode != 0 or not output:
        raise WorkspaceMountError(f"'git rev-parse {flag}' failed in '{worktree}': {res.stderr.strip()}")
    return os.path.realpath(os.path.join(worktree, output))


def _provisioned_worktree(target_worktree: str, slug: str | None, base_branch: str) -> str:
    """Return ``target_worktree`` when it is a working tree, provisioning it through ``ensure_worktree`` for a slug."""
    if is_working_tree(target_worktree):
        return target_worktree
    if not slug:
        problem = target_worktree_problem(target_worktree)
        raise WorkspaceMountError(problem or f"Target worktree '{target_worktree}' is not a git working tree")
    try:
        return ensure_worktree(target_worktree, slug=slug, target_branch=base_branch)
    except (RuntimeError, ValueError) as exc:
        raise WorkspaceMountError(f"Could not provision a worktree for slug '{slug}': {exc}") from exc


def _refuse_unsafe_toplevel(toplevel: str) -> None:
    """Refuse a top level that is a bare repository, the filesystem root or the user's home directory."""
    if is_bare_repository(toplevel):
        raise WorkspaceMountError(f"Refusing to mount bare repository '{toplevel}': it has no working tree")
    home = os.path.realpath(os.path.expanduser("~"))
    if toplevel in (os.path.realpath(os.sep), home):
        raise WorkspaceMountError(f"Refusing to bind-mount '{toplevel}' into a role container: it is too broad")


def _checked_common_dir(toplevel: str) -> str:
    """Return the worktree's common git directory, checking it is readable and covers the worktree's git dir."""
    common_dir = _rev_parse_path(toplevel, "--git-common-dir")
    if not os.path.isdir(common_dir) or not os.access(common_dir, os.R_OK | os.X_OK):
        raise WorkspaceMountError(f"Common git directory '{common_dir}' of '{toplevel}' is missing or unreadable")
    git_dir = _rev_parse_path(toplevel, "--git-dir")
    if not (_is_within(git_dir, common_dir) or _is_within(git_dir, toplevel)):
        raise WorkspaceMountError(
            f"Git directory '{git_dir}' of '{toplevel}' lies outside the mounted paths, so the container "
            "could not resolve it"
        )
    return common_dir


def resolve_workspace_mount(
    target_worktree: str,
    *,
    slug: str | None = None,
    base_branch: str = "origin/main",
) -> WorkspaceMount:
    """Validate (and, for a slug, provision) a target worktree and describe its bind mount.

    Args:
        target_worktree: Host path of the worktree to mount (a subdirectory resolves to its top level). With a
            ``slug``, a bare clone or an absent ``flow-<slug>`` path is provisioned through ``ensure_worktree``.
        slug: Intent slug used to provision a missing worktree; without one, a non-worktree is refused.
        base_branch: Ref a newly provisioned worktree branch starts from (default: ``origin/main``).

    Returns:
        WorkspaceMount: The real paths of the worktree top level and the repository's common git directory.

    Raises:
        WorkspaceMountError: When the path is not (and cannot become) a working tree, is a bare repository, is
            the filesystem root or the home directory, or its common git directory is missing or unreadable.
    """
    worktree = _provisioned_worktree(target_worktree, slug, base_branch)
    toplevel = _rev_parse_path(worktree, "--show-toplevel")
    _refuse_unsafe_toplevel(toplevel)
    return WorkspaceMount(host_worktree=toplevel, git_common_dir=_checked_common_dir(toplevel))


def workspace_mount_args(mount: WorkspaceMount) -> list[str]:
    """Return the ``docker run`` bind-mount arguments for ``mount``.

    The worktree is mounted at the container workspace. A linked worktree's common git directory is mounted at its
    identity path so the worktree's ``.git`` pointer resolves inside the container; a main worktree's common
    directory already lies inside the worktree, so it needs no second bind.
    """
    args = ["-v", f"{mount.host_worktree}:{mount.container_workspace}"]
    if not _is_within(mount.git_common_dir, mount.host_worktree):
        args.extend(["-v", f"{mount.git_common_dir}:{mount.git_common_dir}"])
    return args


def workspace_mount_env(mount: WorkspaceMount) -> dict[str, str]:
    """Return the environment that pins the entrypoint's workspace to the mountpoint.

    ``HOLON_TARGET_WORKTREE`` overrides any forwarded host value, so the in-container ``get_workspace_dir()`` can
    never drift from where the worktree is actually mounted.
    """
    return {"HOLON_TARGET_WORKTREE": mount.container_workspace}
