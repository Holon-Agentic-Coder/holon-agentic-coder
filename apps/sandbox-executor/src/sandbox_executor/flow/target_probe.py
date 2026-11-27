from __future__ import annotations

from dataclasses import dataclass, replace

from sandbox_executor.flow.host_remote import HostRemoteError, fetch_base
from sandbox_executor.git import run_git


# why: callers fail closed on this error; a failed probe is never reported as a clean target
class TargetProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class TargetProbe:
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
    fields = output.split("\0")[1:]
    paths: list[str] = []
    for field in fields:
        if not field:
            # why: informational messages follow the first empty field and may name a file that is not conflicted
            break
        if field not in paths:
            paths.append(field)
    return tuple(paths)


def merge_tree_conflicts(worktree: str, head_ref: str, target_ref: str) -> tuple[str, ...]:
    res = run_git(["merge-tree", "--write-tree", "--name-only", "-z", head_ref, target_ref], cwd=worktree, check=False)
    if res.returncode == 0:
        return ()
    # why: merge-tree exits 1 for a merge with conflicts; only other codes are failures
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
    # why: role containers never reach the remote, so they probe with fetch=False
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
