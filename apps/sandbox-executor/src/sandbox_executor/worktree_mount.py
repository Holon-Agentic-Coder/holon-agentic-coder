"""Host-provisioned worktree bind mounts for role containers.

The host owns the worktree: it provisions (or validates) the target worktree, resolves the repository's common git
directory and turns both into ``docker run`` bind-mount arguments. The container owns nothing but the mountpoint: the
worktree appears at :data:`~sandbox_executor.agent_runner.SANDBOX_WORKSPACE_MOUNT` and the common git directory at its
own host path, so a linked worktree's ``.git`` pointer file (``gitdir: <common>/worktrees/<name>``) and the relative
``commondir`` inside it resolve without a clone or network access.

The mount set is deliberately minimal (docs/safety.md, container sandbox): exactly the worktree top level plus, for a
linked worktree, its common git directory, with the common directory's ``hooks`` and ``config`` overlaid read-only.
The overlay alone cannot stop a container from redirecting the host's git (rewriting the ``.git`` pointer or a
``commondir``, planting a ``config.worktree``, or swapping the git directory out from under the overlay), so the host
fingerprints the git metadata it later trusts before the run (:func:`snapshot_git_metadata`) and refuses to continue
when it changed (:func:`verify_git_metadata`). Nothing here decides *whether* a stage mounts a worktree; callers
opt in by passing the resolved :class:`WorkspaceMount` to the dispatcher.
"""

from __future__ import annotations

import hashlib
import os
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass

from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.git import run_git
from sandbox_executor.git_flow import ensure_worktree, is_bare_repository, is_working_tree, target_worktree_problem

__all__ = [
    "WorkspaceMount",
    "WorkspaceMountError",
    "guarded_run",
    "resolve_workspace_mount",
    "snapshot_git_metadata",
    "verify_git_metadata",
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
    """Return the worktree's common git directory, checking it is readable and covers the worktree's git dir.

    A linked worktree's common dir is bind-mounted at its own host path, so it must not overlap the container
    workspace mountpoint or the two binds would collide.
    """
    common_dir = _rev_parse_path(toplevel, "--git-common-dir")
    if not os.path.isdir(common_dir) or not os.access(common_dir, os.R_OK | os.X_OK):
        raise WorkspaceMountError(f"Common git directory '{common_dir}' of '{toplevel}' is missing or unreadable")
    if not _is_within(common_dir, toplevel) and (
        _is_within(common_dir, SANDBOX_WORKSPACE_MOUNT) or _is_within(SANDBOX_WORKSPACE_MOUNT, common_dir)
    ):
        raise WorkspaceMountError(
            f"Common git directory '{common_dir}' of '{toplevel}' overlaps the container workspace "
            f"'{SANDBOX_WORKSPACE_MOUNT}', so it cannot be bind-mounted at its own path"
        )
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
    common_dir = _checked_common_dir(toplevel)
    os.makedirs(os.path.join(common_dir, "hooks"), exist_ok=True)
    return WorkspaceMount(host_worktree=toplevel, git_common_dir=common_dir)


def _bind(source: str, target: str, *, readonly: bool = False) -> list[str]:
    """Return one ``--mount type=bind`` argument pair (``--mount`` never splits a path on ``:`` like ``-v``)."""
    if "," in source or "," in target:
        raise WorkspaceMountError(f"Refusing to bind-mount '{source}': a ',' would break the --mount field list")
    spec = f"type=bind,source={source},target={target}"
    return ["--mount", f"{spec},readonly" if readonly else spec]


def _protected_paths(mount: WorkspaceMount) -> list[tuple[str, str]]:
    """Return ``(host, container)`` pairs of the common git dir's ``hooks`` and ``config``.

    Both are run or read by the host's git after the container exits (hooks, ``core.hooksPath``,
    ``core.fsmonitor``), so they are overlaid read-only. :func:`resolve_workspace_mount` creates a missing ``hooks``
    directory so the overlay always has a source and the container cannot create it under the writable common dir.
    """
    if _is_within(mount.git_common_dir, mount.host_worktree):
        rel = os.path.relpath(mount.git_common_dir, mount.host_worktree)
        container_common = os.path.normpath(os.path.join(mount.container_workspace, rel))
    else:
        container_common = mount.git_common_dir
    return [(os.path.join(mount.git_common_dir, n), os.path.join(container_common, n)) for n in ("hooks", "config")]


def workspace_mount_args(mount: WorkspaceMount) -> list[str]:
    """Return the ``docker run`` bind-mount arguments for ``mount``.

    The worktree is mounted at the container workspace. A linked worktree's common git directory is mounted at its
    identity path so the worktree's ``.git`` pointer resolves inside the container; a main worktree's common
    directory already lies inside the worktree, so it needs no second bind. Either way the common directory's
    ``hooks`` and ``config`` are then overlaid read-only (see :func:`_protected_paths`).
    """
    args = _bind(mount.host_worktree, mount.container_workspace)
    if not _is_within(mount.git_common_dir, mount.host_worktree):
        args.extend(_bind(mount.git_common_dir, mount.git_common_dir))
    for host_path, container_path in _protected_paths(mount):
        args.extend(_bind(host_path, container_path, readonly=True))
    return args


def workspace_mount_env(mount: WorkspaceMount) -> dict[str, str]:
    """Return the environment that pins the entrypoint's workspace to the mountpoint.

    ``HOLON_TARGET_WORKTREE`` overrides any forwarded host value, so the in-container ``get_workspace_dir()`` can
    never drift from where the worktree is actually mounted.
    """
    return {"HOLON_TARGET_WORKTREE": mount.container_workspace}


# Git consults these files to locate its directories and configuration; any of them may name a command
# (``core.fsmonitor``, ``core.hooksPath``, ``include.path``) or redirect git to a directory that does.
_TRUSTED_GIT_FILES = frozenset({"config", "config.worktree", "commondir", "gitdir"})
# Bulk data under a git dir that can name no command, so it is not fingerprinted (only at a real git dir's top
# level: a linked worktree named e.g. ``logs`` lives at ``worktrees/logs`` and must stay fingerprinted).
_UNTRUSTED_BULK_DIRS = frozenset({"objects", "refs", "logs", "lfs"})


def _fingerprint(path: str) -> str:
    """Return an identity for ``path`` that changes when it is rewritten, replaced, re-pointed or removed.

    Only a regular file is read (in bounded chunks, opened non-blocking without following a link), so a planted
    FIFO or device is recorded by its type instead of hanging the host; an unreadable entry gets its own identity
    rather than raising.
    """
    try:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            return f"link:{os.readlink(path)}"
        if stat.S_ISDIR(st.st_mode):
            return f"dir:{st.st_dev}:{st.st_ino}"
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as fh:
            mode = os.fstat(fh.fileno()).st_mode
            if not stat.S_ISREG(mode):
                return f"special:{stat.S_IFMT(mode):o}"
            digest = hashlib.sha256()
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                digest.update(chunk)
            return f"file:{digest.hexdigest()}"
    except FileNotFoundError:
        return "absent"
    except OSError as exc:
        return f"unreadable:{exc.errno}"


def _in_hooks(common_dir: str, root: str) -> bool:
    """Return True when ``root`` is (beneath) a real hooks directory of the common dir or a submodule git dir.

    Only the top-level ``hooks`` and a ``hooks`` directly inside a ``modules/**`` git dir (one holding ``HEAD``)
    count, so a linked worktree or submodule merely *named* ``hooks`` is not treated as one.
    """
    parts = os.path.relpath(root, common_dir).split(os.sep)
    for idx, part in enumerate(parts):
        if part != "hooks":
            continue
        if idx == 0 or (parts[0] == "modules" and os.path.isfile(os.path.join(common_dir, *parts[:idx], "HEAD"))):
            return True
    return False


def _is_git_dir_top(common_dir: str, root: str) -> bool:
    """Return True when ``root`` is the common dir or a submodule git dir (one holding ``HEAD``) in ``modules``."""
    if root == common_dir:
        return True
    parts = os.path.relpath(root, common_dir).split(os.sep)
    return parts[0] == "modules" and os.path.isfile(os.path.join(root, "HEAD"))


def snapshot_git_metadata(mount: WorkspaceMount) -> dict[str, str]:
    """Fingerprint the git metadata the host's git trusts after the container exits.

    Covers the worktree's ``.git`` entry, the common git dir itself, and every ``config``, ``config.worktree``,
    ``commondir`` and ``gitdir`` file and every ``hooks`` entry beneath the common dir (including ``worktrees/*``
    and ``modules/*``), so a new, rewritten or removed one, or a swapped directory, changes the snapshot.
    """
    snapshot = {
        os.path.join(mount.host_worktree, ".git"): _fingerprint(os.path.join(mount.host_worktree, ".git")),
        mount.git_common_dir: _fingerprint(mount.git_common_dir),
    }
    for root, dirs, files in os.walk(mount.git_common_dir):
        if _is_git_dir_top(mount.git_common_dir, root):
            dirs[:] = [d for d in dirs if d not in _UNTRUSTED_BULK_DIRS]
        in_hooks = _in_hooks(mount.git_common_dir, root)
        tracked = [f for f in files if in_hooks or f in _TRUSTED_GIT_FILES]
        tracked += [d for d in dirs if in_hooks or d == "hooks"]
        for name in tracked:
            path = os.path.join(root, name)
            snapshot[path] = _fingerprint(path)
    return snapshot


def verify_git_metadata(mount: WorkspaceMount, baseline: dict[str, str]) -> None:
    """Raise :class:`WorkspaceMountError` when the git metadata differs from ``baseline``.

    Called after the container exits and before the host runs git in the worktree again, so a planted
    ``core.fsmonitor``/``core.hooksPath``, hook or redirected ``.git`` pointer is refused rather than executed.
    """
    current = snapshot_git_metadata(mount)
    changed = sorted(
        p for p in baseline.keys() | current.keys() if baseline.get(p, "absent") != current.get(p, "absent")
    )
    if changed:
        raise WorkspaceMountError(
            f"Refusing to continue: the container changed git metadata the host trusts ({', '.join(changed)}); "
            f"inspect '{mount.host_worktree}' before running git in it"
        )


def guarded_run(mount: WorkspaceMount | None, run: Callable[[], int]) -> int:
    """Return ``run()``'s exit code, or 1 when the container changed the git metadata of ``mount``.

    With a mount, the git metadata is fingerprinted before ``run`` and verified after it, so a planted fsmonitor,
    hook, ``config.worktree`` or re-pointed ``.git`` fails the run (docs/safety.md); without one ``run`` is called
    unguarded.
    """
    if mount is None:
        return run()
    baseline = snapshot_git_metadata(mount)
    returncode = run()
    try:
        verify_git_metadata(mount, baseline)
    except WorkspaceMountError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return returncode
