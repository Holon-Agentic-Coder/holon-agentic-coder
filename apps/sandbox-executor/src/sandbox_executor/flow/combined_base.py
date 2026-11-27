from __future__ import annotations

from typing import Any

from sandbox_executor.git import empty_diff_summary, net_changed_paths, run_git
from sandbox_executor.redaction import redact_agent_secrets


def _integrated_plan_recorded(rows: list[dict[str, Any]], plan_branch: str) -> bool:
    return any(
        row.get("entry_type") == "summary"
        and row.get("integrated") is True
        and row.get("plan_branch") == plan_branch
        and isinstance(row.get("children"), list)
        and bool(row.get("children"))
        for row in rows
    )


def _commit_ref(repo_dir: str, ref: str) -> bool:
    return run_git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=repo_dir, check=False).returncode == 0


def _target_ref(repo_dir: str, target_branch: str, mounted: bool | None) -> tuple[str | None, str]:
    if mounted is None:
        local_ref = f"refs/heads/{target_branch}"
        if _commit_ref(repo_dir, local_ref):
            return local_ref, ""
        return None, f"no 'origin' remote and cannot resolve '{target_branch}' locally"
    # why: deferred because git_refs imports this module.
    from sandbox_executor.flow.git_refs import _fetch_refspecs_error, _unfetched_branch_message

    remote_ref = f"refs/remotes/origin/{target_branch}"
    if not mounted and (fetch_error := _fetch_refspecs_error(repo_dir, [f"+refs/heads/{target_branch}:{remote_ref}"])):
        return None, f"fetching '{target_branch}' from origin failed: {fetch_error}"
    if unfetched := _unfetched_branch_message(repo_dir, [target_branch]):
        return None, unfetched
    return remote_ref, ""


def _combined_diff_verdict(
    repo_dir: str, branches: tuple[str, str], target_branch: str, mounted: bool | None, source: str
) -> tuple[bool | None, str]:
    exec_branch, exec_ref = branches
    target_ref, problem = _target_ref(repo_dir, target_branch, mounted)
    if target_ref is None:
        return None, problem
    res = run_git(["merge-base", target_ref, exec_ref], cwd=repo_dir, check=False)
    base_sha = res.stdout.strip() if res.returncode == 0 and isinstance(res.stdout, str) else ""
    if not base_sha:
        return None, f"no merge base between '{target_branch}' and '{exec_branch}'"
    try:
        # why: a merge-inclusive diff, because the children's work reaches the execution branch only through merges.
        changed = net_changed_paths(repo_dir, base_sha, exec_ref)
    except (RuntimeError, TimeoutError) as exc:
        return None, f"diff against {target_branch}@{base_sha[:12]} failed: {redact_agent_secrets(str(exc))}"
    label = f"{target_branch}@{base_sha[:12]} (merge base of '{exec_branch}' with {target_branch})"
    if not changed:
        return False, f"{empty_diff_summary(label)} (compared on {source})"
    return True, f"{len(changed)} changed path(s) against {label} on {source}"
