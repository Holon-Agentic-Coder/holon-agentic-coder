"""The target probe: did the target branch move past the flow branch, and which paths would a merge conflict on?

The probe is agent-free and cheap. It fetches the target through the host remote helper, resolves both SHAs, and runs
``git merge-tree --write-tree --name-only -z`` only when the (head SHA, target SHA) pair differs from the last probe
the caller cached. ``merge-tree`` writes loose objects only, never a ref, so probing never changes a branch. Any fetch
or ``merge-tree`` failure raises :class:`TargetProbeError`; a failed probe is never reported as a clean target.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from sandbox_executor.flow.host_remote import HostRemoteError, fetch_base
from sandbox_executor.git import run_git


class TargetProbeError(RuntimeError):
    """The target could not be fetched, resolved or merge-probed; callers fail closed on it."""


@dataclass(frozen=True)
class TargetProbe:
    """One probe of the flow branch (``HEAD``) against ``origin/<target>``.

    ``moved`` is False when the target SHA is already an ancestor of ``HEAD`` (nothing to merge); ``conflicted_paths``
    are the paths a merge would conflict on, in ``merge-tree`` order; ``cached`` marks a probe reused unchanged.
    """

    target_ref: str
    target_sha: str
    head_sha: str
    merge_base: str
    moved: bool
    conflicted_paths: tuple[str, ...] = ()
    cached: bool = False


def _rev_parse(worktree: str, ref: str) -> str:
    res = run_git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=worktree, check=False)
    sha = (res.stdout or "").strip() if isinstance(res.stdout, str) else ""
    if res.returncode != 0 or not sha:
        raise TargetProbeError(f"cannot resolve '{ref}' to a commit")
    return sha


def parse_merge_tree_paths(output: str) -> tuple[str, ...]:
    """The conflicted paths of ``git merge-tree --write-tree --name-only -z`` output, in order, without duplicates.

    The first NUL field is the tree OID; the conflicted paths follow up to the first empty field; the informational
    messages after it are ignored, so a message naming a file is never mistaken for a conflicted path.
    """
    fields = output.split("\0")[1:]
    paths: list[str] = []
    for field in fields:
        if not field:
            break
        if field not in paths:
            paths.append(field)
    return tuple(paths)


def merge_tree_conflicts(worktree: str, head_ref: str, target_ref: str) -> tuple[str, ...]:
    """The paths merging ``target_ref`` into ``head_ref`` would conflict on (empty for a clean merge).

    Raises:
        TargetProbeError: When ``merge-tree`` exits with anything other than 0 (clean) or 1 (conflicts).
    """
    res = run_git(["merge-tree", "--write-tree", "--name-only", "-z", head_ref, target_ref], cwd=worktree, check=False)
    if res.returncode == 0:
        return ()
    if res.returncode != 1:
        detail = (res.stderr or "").strip() if isinstance(res.stderr, str) else ""
        raise TargetProbeError(f"git merge-tree exited with code {res.returncode}: {detail}")
    return parse_merge_tree_paths(res.stdout if isinstance(res.stdout, str) else "")


def _is_ancestor(worktree: str, ancestor: str, descendant: str) -> bool:
    res = run_git(["merge-base", "--is-ancestor", ancestor, descendant], cwd=worktree, check=False)
    return res.returncode == 0


def _merge_base(worktree: str, head_sha: str, target_sha: str) -> str:
    res = run_git(["merge-base", head_sha, target_sha], cwd=worktree, check=False)
    return (res.stdout or "").strip() if res.returncode == 0 and isinstance(res.stdout, str) else ""


def _rev_parse_pair(worktree: str, first_ref: str, second_ref: str) -> tuple[str, str]:
    res = run_git(
        ["rev-parse", "--quiet", f"{first_ref}^{{commit}}", f"{second_ref}^{{commit}}"], cwd=worktree, check=False
    )
    lines = (res.stdout or "").splitlines() if res.returncode == 0 and isinstance(res.stdout, str) else []
    if len(lines) == 2 and lines[0] and lines[1]:
        return lines[0], lines[1]
    return _rev_parse(worktree, first_ref), _rev_parse(worktree, second_ref)


def probe_target(
    worktree: str,
    target_branch: str,
    last_probe: TargetProbe | None = None,
    *,
    fetch: bool = True,
    head_ref: str = "HEAD",
) -> TargetProbe:
    """Probe ``head_ref`` of ``worktree`` against ``origin/<target_branch>``, reusing ``last_probe`` when unchanged.

    Args:
        worktree: The flow worktree.
        target_branch: The branch the flow merges into.
        last_probe: The caller's cached probe; returned with ``cached=True`` when both SHAs are unchanged.
        fetch: Fetch the target first (False inside role containers, which never reach the remote).
        head_ref: Revision to compare against ``origin/<target_branch>`` (defaults to ``HEAD``).

    Raises:
        TargetProbeError: When the fetch, a ``rev-parse`` or ``merge-tree`` fails.
    """
    if fetch:
        try:
            fetch_base(worktree, target_branch)
        except HostRemoteError as exc:
            raise TargetProbeError(str(exc)) from exc
    target_ref = f"origin/{target_branch}"
    target_sha, head_sha = _rev_parse_pair(worktree, target_ref, head_ref)
    if last_probe is not None and (last_probe.head_sha, last_probe.target_sha) == (head_sha, target_sha):
        return replace(last_probe, cached=True)
    if target_sha == head_sha or _is_ancestor(worktree, target_sha, head_sha):
        return TargetProbe(target_ref, target_sha, head_sha, target_sha, moved=False)
    paths = merge_tree_conflicts(worktree, head_sha, target_sha)
    return TargetProbe(target_ref, target_sha, head_sha, _merge_base(worktree, head_sha, target_sha), True, paths)
