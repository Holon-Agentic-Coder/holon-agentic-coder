"""Workspace helpers of the executor entrypoint: the mounted host worktree and the resume of a failed execution.

Each helper takes the caller's GitRepository, so the git commands run through the executor's own runner.
"""

from __future__ import annotations

import os
import re

from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.git import GitRepository

# A resumable execution branch name (validated before it reaches a refspec) and a full commit sha.
_PARTIAL_BRANCH_RE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*/E-\d+-[A-Za-z0-9._-]+/_$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


def workspace_mounted() -> bool:
    """True when the workspace is the host worktree bind-mounted at the sandbox mountpoint."""
    return os.getenv("HOLON_TARGET_WORKTREE") == SANDBOX_WORKSPACE_MOUNT


def validate_mounted_workspace(repo: GitRepository, repo_dir: str, plan_branch: str) -> str:
    """Validate the host worktree mounted at repo_dir and return the plan tip the host fetched into it.

    The container never clones, fetches, resets or cleans the mount. HEAD must be a readable commit, the worktree must
    carry no uncommitted changes (an earlier crashed run's edits stay on the host for the operator, and are never
    swept into this execution), and the plan branch must already resolve locally, because the host fetches it before
    dispatch.

    Raises:
        RuntimeError: If any of these checks fails.
    """
    repo.run(["config", "--global", "--add", "safe.directory", repo_dir], check=False)
    if repo.rev_parse("HEAD^{commit}", verify=True).returncode != 0:
        raise RuntimeError(f"mounted workspace at '{repo_dir}' has no readable HEAD commit")
    status = repo.run(["status", "--porcelain"], check=False)
    if status.returncode != 0 or (status.stdout or "").strip():
        raise RuntimeError(
            f"mounted workspace at '{repo_dir}' has uncommitted changes, which are kept on the host; commit, stash or "
            "discard them in the host worktree before executing"
        )
    for ref in (f"refs/remotes/origin/{plan_branch}", f"refs/heads/{plan_branch}"):
        tip = repo.rev_parse(f"{ref}^{{commit}}", verify=True)
        if tip.returncode == 0 and (tip.stdout or "").strip():
            return tip.stdout.strip()
    raise RuntimeError(
        f"plan branch '{plan_branch}' does not resolve in the mounted workspace; the host fetches it before dispatch"
    )


def _resume_refusal(repo: GitRepository, repo_url: str, partial_branch: str, plan_branch_prefix: str) -> str | None:
    """Fetch a failed execution branch for a resume and return why it cannot be used, or None when it can.

    A mounted host worktree is never fetched into: the host already fetched the branch before dispatch.
    """
    shaped = _PARTIAL_BRANCH_RE.match(partial_branch) and ".." not in partial_branch
    if not shaped or not partial_branch.startswith(f"{plan_branch_prefix}/E-"):
        return f"'{partial_branch}' is not an execution branch of this plan"
    if workspace_mounted():
        return None
    fetch = repo.run(
        ["fetch", "--no-tags", repo_url, f"+refs/heads/{partial_branch}:refs/remotes/origin/{partial_branch}"],
        check=False,
    )
    return "fetch failed" if fetch.returncode != 0 else None


def _descends_from_plan_tip(repo: GitRepository, repo_url: str, partial_branch: str, plan_tip: str, sha: str) -> bool:
    """True when sha descends from the plan tip, deepening a shallow (never a mounted) clone once to find out."""
    if repo.merge_base(plan_tip, sha, is_ancestor=True).returncode == 0:
        return True
    shallow = repo.rev_parse("--is-shallow-repository")
    if (shallow.stdout or "").strip() != "true" or workspace_mounted():
        return False
    repo.fetch(repo_url, f"refs/heads/{partial_branch}", "--deepen=50")
    return repo.merge_base(plan_tip, sha, is_ancestor=True).returncode == 0


def resolve_partial_head(
    repo: GitRepository, repo_url: str, partial_branch: str, plan_tip: str | None, plan_branch_prefix: str
) -> tuple[str | None, str]:
    """Return the partial head to resume from (or None) and the reason, read from the failed branch's own ledger.

    The partial head must be recorded on the branch's latest summary row of a failed execution and must descend
    from the plan tip; otherwise the caller starts from the plan tip and logs the reason.
    """
    if not plan_tip:
        return None, "the plan tip is unknown"
    refusal = _resume_refusal(repo, repo_url, partial_branch, plan_branch_prefix)
    if refusal:
        return None, refusal
    from sandbox_executor.flow import failed_partial_head

    exec_id = partial_branch.removesuffix("/_").rsplit("/", 1)[-1]
    sha = failed_partial_head(repo.repo_dir, f"refs/remotes/origin/{partial_branch}", exec_id)
    if not sha or not _SHA_RE.match(sha):
        return None, "no partial_head"
    if repo.run(["cat-file", "-e", f"{sha}^{{commit}}"], check=False).returncode != 0:
        return None, "the partial head is not on the fetched branch"
    if not _descends_from_plan_tip(repo, repo_url, partial_branch, plan_tip, sha):
        return None, "not a descendant of the plan tip"
    return sha, "resumed"


def continuation_note(partial_branch: str, partial_head: str) -> str:
    """Prompt note telling the agent it continues the partial work of a failed execution."""
    return (
        f"Continuation: the working tree already holds partial work from the failed execution {partial_branch} "
        f"(commit {partial_head}); its execution notes stay on that branch. Review the existing commits and the "
        "plan's completed steps, then continue from where that run stopped rather than redoing them."
    )
