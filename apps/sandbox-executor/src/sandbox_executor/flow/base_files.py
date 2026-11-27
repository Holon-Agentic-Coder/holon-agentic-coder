from __future__ import annotations

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.in_flight import AdmissionUnavailable, GitRunner, command_failure, host_git
from sandbox_executor.git import is_metadata_path
from sandbox_executor.touch_set import normalise_touch_path


def base_branch_name(context: FlowContext) -> str:
    # why: must match flow.plan_refresh._target_branch, the branch Stage 3 refreshes the plan onto
    return str(context.intent_data.get("target_branch") or "main")


def _resolve_base_ref(repo_dir: str, branch: str, git: GitRunner) -> str | None:
    for ref in (f"refs/remotes/origin/{branch}", f"refs/heads/{branch}"):
        if git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], repo_dir).returncode == 0:
            return ref
    return None


def list_base_files(
    repo_dir: str, branch: str, run_git: GitRunner | None = None
) -> frozenset[str] | AdmissionUnavailable:
    git = run_git or host_git
    ref = _resolve_base_ref(repo_dir, branch, git)
    if ref is None:
        return AdmissionUnavailable(f"the base branch {branch} could not be resolved")
    result = git(["ls-tree", "-r", "--name-only", "-z", ref], repo_dir)
    if result.returncode != 0:
        return command_failure(f"git ls-tree {ref}", result)
    paths = (normalise_touch_path(path) for path in (result.stdout or "").split("\0"))
    return frozenset(path for path in paths if path and not is_metadata_path(path))
