from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from sandbox_executor.flow.github.adapter import GitHubAdapter
from sandbox_executor.flow.github.mergeability_policy import (
    gh_view_verdict,
    local_error_result,
    merge_tree_verdict,
    missing_ref_result,
    not_a_repository_result,
)
from sandbox_executor.flow.github.pr_metadata import generate_semantic_pr_metadata as generate_semantic_pr_metadata
from sandbox_executor.git import run_git
from sandbox_executor.redaction import redact_agent_secrets

logger = logging.getLogger(__name__)

_GH_MERGEABILITY_MAX_ATTEMPTS = 3
_GH_MERGEABILITY_RETRY_DELAY_SECONDS = 2
_PR_URL_PATTERN = re.compile(r"https://\S+")


def _resolve_commit_ref(repo_dir: str, ref: str) -> tuple[str | None, str]:
    proc = run_git(["rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=repo_dir, check=False)
    stderr = proc.stderr.strip() if isinstance(proc.stderr, str) else ""
    if proc.returncode != 0:
        return None, stderr
    stdout = proc.stdout.strip() if isinstance(proc.stdout, str) else ""
    return (stdout or ref), stderr


def check_pr_mergeability(
    repo_dir: str,
    branch: str,
    target_branch: str = "main",
    proxy_env: dict[str, str] | None = None,
    *,
    github: GitHubAdapter | None = None,
) -> dict[str, Any]:
    clean_branch = branch.strip() if branch else ""
    github = github or GitHubAdapter(repo_dir, proxy_env)

    if clean_branch and github.available:
        gh_result = _gh_mergeability(github, clean_branch)
        if gh_result is not None:
            return gh_result

    return _local_mergeability(repo_dir, clean_branch, target_branch)


def _gh_mergeability(github: GitHubAdapter, clean_branch: str) -> dict[str, Any] | None:
    for attempt in range(_GH_MERGEABILITY_MAX_ATTEMPTS):
        try:
            gh_proc = github.mergeability(clean_branch)
            if not gh_proc.ok:
                return None
            if not gh_proc.stdout.strip():
                continue
            result, still_computing = gh_view_verdict(json.loads(gh_proc.stdout))
            if still_computing and attempt < _GH_MERGEABILITY_MAX_ATTEMPTS - 1:
                time.sleep(_GH_MERGEABILITY_RETRY_DELAY_SECONDS)
                continue
            return result
        except Exception as e:
            logger.debug(f"gh pr view check failed: {e}")
            return None
    return None


def _local_candidate_refs(repo_dir: str, clean_branch: str, target_branch: str) -> list[tuple[str, str, list[str]]]:
    target_candidates = [f"origin/{target_branch}"]
    target_ref = target_candidates[0]
    ref_check = run_git(["rev-parse", "--verify", target_ref], cwd=repo_dir, check=False)
    if ref_check.returncode != 0:
        target_ref = target_branch
        target_candidates.append(target_ref)

    branch_ref = clean_branch or "HEAD"
    branch_candidates = [branch_ref]
    if clean_branch:
        b_check = run_git(["rev-parse", "--verify", clean_branch], cwd=repo_dir, check=False)
        if b_check.returncode != 0:
            remote_b_ref = f"origin/{clean_branch}"
            branch_candidates.append(remote_b_ref)
            rb_check = run_git(["rev-parse", "--verify", remote_b_ref], cwd=repo_dir, check=False)
            if rb_check.returncode == 0:
                branch_ref = remote_b_ref
    return [("branch", branch_ref, branch_candidates), ("target", target_ref, target_candidates)]


def _is_git_repository(repo_dir: str) -> bool:
    return run_git(["rev-parse", "--git-dir"], cwd=repo_dir, check=False).returncode == 0


def _unresolved_ref_result(repo_dir: str, role: str, ref: str, candidates: list[str], stderr: str) -> dict[str, Any]:
    if not _is_git_repository(repo_dir):
        return not_a_repository_result(repo_dir, stderr)
    logger.info("Cannot evaluate mergeability for ref %s (%s): ref could not be resolved to a commit.", ref, role)
    return missing_ref_result(ref, role, candidates, stderr)


def _local_mergeability(repo_dir: str, clean_branch: str, target_branch: str) -> dict[str, Any]:
    try:
        resolved_commits: dict[str, str] = {}
        for role, ref, candidates in _local_candidate_refs(repo_dir, clean_branch, target_branch):
            sha, rp_stderr = _resolve_commit_ref(repo_dir, ref)
            if sha is None:
                return _unresolved_ref_result(repo_dir, role, ref, candidates, rp_stderr)
            resolved_commits[role] = sha

        mt_proc = run_git(
            ["merge-tree", "--write-tree", resolved_commits["branch"], resolved_commits["target"]],
            cwd=repo_dir,
            check=False,
        )
        return merge_tree_verdict(mt_proc.returncode, mt_proc.stderr)
    except Exception as exc:
        logger.debug(f"Local git mergeability check encountered error: {exc}")
        return local_error_result(exc)


def format_review_git_trailers(
    action_origin: str = "pr_review",
    iteration: int = 1,
    finding_ids: list[str] | None = None,
    severity: str = "clean",
) -> str:
    fids = ", ".join(finding_ids) if finding_ids else "none"
    lines = [
        f"Action-Origin: {action_origin}",
        f"Review-Iteration: {iteration}",
        f"Review-Finding-Ids: {fids}",
        f"Review-Severity: {severity}",
    ]
    return "\n".join(lines)


def create_or_update_pr(  # noqa: PLR0913
    repo_dir: str,
    branch: str,
    target_branch: str = "main",
    title: str = "",
    body: str = "",
    proxy_env: dict[str, str] | None = None,
    *,
    github: GitHubAdapter | None = None,
) -> dict[str, Any]:
    clean_branch = branch.strip() if branch else ""
    github = github or GitHubAdapter(repo_dir, proxy_env)
    if not clean_branch or not github.available:
        return {"attempted": False, "success": False, "action": "none", "pr_number": None, "output": "", "pr_url": None}

    try:
        action, pr_num, result = github.open_or_update(clean_branch, target_branch, title, body)
    except Exception as exc:
        return _pr_error(redact_agent_secrets(str(exc)))
    if result.error:
        return _pr_error(result.error)
    return {
        "attempted": True,
        "success": result.ok,
        "action": action,
        "pr_number": pr_num,
        "output": result.output,
        "pr_url": _pr_url_from_stdout(result.stdout),
    }


def _pr_url_from_stdout(stdout: str) -> str | None:
    for line in stdout.splitlines() if isinstance(stdout, str) else []:
        match = _PR_URL_PATTERN.search(line.strip())
        if match:
            return redact_agent_secrets(match.group(0))
    return None


def _pr_error(output: str) -> dict[str, Any]:
    return {"attempted": True, "success": False, "action": "error", "pr_number": None, "output": output, "pr_url": None}
