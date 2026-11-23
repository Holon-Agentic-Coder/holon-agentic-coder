"""Pull request helpers: mergeability checks, review git trailers, semantic PR metadata and PR creation."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from typing import Any

from sandbox_executor.flow.github.mergeability_policy import (
    gh_view_verdict,
    local_error_result,
    merge_tree_verdict,
    missing_ref_result,
    not_a_repository_result,
)
from sandbox_executor.flow.github.pr_metadata import generate_semantic_pr_metadata as generate_semantic_pr_metadata
from sandbox_executor.git import run_git

logger = logging.getLogger(__name__)


def _resolve_commit_ref(repo_dir: str, ref: str) -> tuple[str | None, str]:
    """Resolve a revision to the commit it names, peeling annotated tags.

    Args:
        repo_dir: Path to git repository.
        ref: Branch name, remote-tracking ref, tag or SHA to resolve.

    Returns:
        tuple[str | None, str]: The commit SHA (or None when the ref does not resolve to a commit) and the
            stripped stderr from 'git rev-parse'. If git reports success with empty stdout, the ref name itself
            is returned so callers can still pass it on to later git commands.

    Raises:
        TimeoutError: Propagated from run_git if the subprocess times out.

    Side effects:
        Runs 'git rev-parse --verify <ref>^{commit}' in repo_dir (read-only).
    """
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
) -> dict[str, Any]:
    """Inspect PR and branch mergeability status against target branch.

    Detects DIRTY or CONFLICTING states using GitHub CLI (gh) when available,
    falling back to local git merge-tree analysis in offline or sandbox environments.

    The local fallback first chooses candidate refs: the target is 'origin/<target>' if it exists, otherwise
    '<target>'; the branch is 'HEAD' when empty, otherwise '<branch>', or 'origin/<branch>' when the local name
    does not exist. Both chosen refs (branch first, then target) must then resolve with
    'git rev-parse --verify <ref>^{commit}'. If either fails, merge-tree is never run and the result is
    MISSING_REF, because an unresolvable ref says nothing about conflicts (when repo_dir is not a git
    repository at all, the result is UNKNOWN instead). Once both refs resolve,
    'git merge-tree --write-tree' exit 0 maps to MERGEABLE/CLEAN, exit 1 maps to CONFLICTING/DIRTY, and any
    other exit code maps to UNKNOWN/UNKNOWN with the exit code and stderr recorded in diagnostics.

    Args:
        repo_dir: Path to git repository.
        branch: Name of branch or PR branch to evaluate.
        target_branch: Target base branch name (defaults to 'main').
        proxy_env: Optional proxy environment variables for network-intercepted runs.

    Returns:
        dict[str, Any]: Mergeability diagnostics dictionary with keys:
            - mergeable: str ("MERGEABLE", "CONFLICTING", "UNKNOWN")
            - merge_state_status: str ("CLEAN", "DIRTY", "BLOCKED", "BEHIND", "UNKNOWN", "MISSING_REF")
            - is_dirty: bool (True only for a genuine conflict)
            - source: str ("github_cli" or "git_local")
            - pr_number: int | None
            - status_check_rollup: list | None
            - diagnostics: dict, empty when there is nothing to report. For MISSING_REF it holds
              missing_ref, ref_role ("branch" or "target"), candidates_tried, stderr and message
              ("cannot evaluate mergeability for ref <X>"). For an unexpected merge-tree exit it holds
              merge_tree_exit_code and stderr. For an exception it holds error.

    Raises:
        None: Gracefully falls back to git_local or UNKNOWN on exceptions.
    """
    clean_branch = branch.strip() if branch else ""

    # 1. Attempt GitHub CLI inspection if gh is installed and available
    if shutil.which("gh") and clean_branch:
        gh_result = _gh_mergeability(repo_dir, clean_branch, proxy_env)
        if gh_result is not None:
            return gh_result

    # 2. Fallback to local git inspection
    return _local_mergeability(repo_dir, clean_branch, target_branch)


def _gh_mergeability(repo_dir: str, clean_branch: str, proxy_env: dict[str, str] | None) -> dict[str, Any] | None:
    """Return the ``gh pr view`` mergeability verdict, retrying while GitHub computes it, or None to fall back."""
    gh_env = {**os.environ, **(proxy_env or {}), "DOCKER_CLI_HINTS": "false"}
    for attempt in range(3):
        try:
            gh_proc = subprocess.run(
                ["gh", "pr", "view", clean_branch, "--json", "number,mergeable,mergeStateStatus,statusCheckRollup"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
                env=gh_env,
            )
            if gh_proc.returncode != 0:
                return None
            if not gh_proc.stdout.strip():
                continue
            result, still_computing = gh_view_verdict(json.loads(gh_proc.stdout))
            # Retry briefly if GitHub is still computing mergeability (UNKNOWN)
            if still_computing and attempt < 2:
                time.sleep(2)
                continue
            return result
        except Exception as e:
            logger.debug(f"gh pr view check failed: {e}")
            return None
    return None


def _local_candidate_refs(repo_dir: str, clean_branch: str, target_branch: str) -> list[tuple[str, str, list[str]]]:
    """Choose the local refs to compare as ``(role, ref, candidates_tried)``, branch first, then target."""
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


def _unresolved_ref_result(repo_dir: str, role: str, ref: str, candidates: list[str], stderr: str) -> dict[str, Any]:
    """Return the verdict for a ref that did not resolve: UNKNOWN outside a repository, otherwise MISSING_REF."""
    # Outside a repository every ref fails to resolve; that is UNKNOWN, not a missing ref.
    git_dir = run_git(["rev-parse", "--git-dir"], cwd=repo_dir, check=False)
    if git_dir.returncode != 0:
        return not_a_repository_result(repo_dir, stderr)
    # Report the name that was actually checked; candidates_tried lists every substitution.
    logger.info("Cannot evaluate mergeability for ref %s (%s): ref could not be resolved to a commit.", ref, role)
    return missing_ref_result(ref, role, candidates, stderr)


def _local_mergeability(repo_dir: str, clean_branch: str, target_branch: str) -> dict[str, Any]:
    """Return the local ``git merge-tree`` mergeability verdict once both refs resolve to commits."""
    try:
        # Gate: both refs must resolve to commits before a merge is simulated. merge-tree exits 1 for an
        # unresolvable ref as well as for a real conflict, so passing it through would report a false DIRTY.
        resolved: dict[str, str] = {}
        for role, ref, candidates in _local_candidate_refs(repo_dir, clean_branch, target_branch):
            sha, rp_stderr = _resolve_commit_ref(repo_dir, ref)
            if sha is None:
                return _unresolved_ref_result(repo_dir, role, ref, candidates, rp_stderr)
            resolved[role] = sha

        mt_proc = run_git(
            ["merge-tree", "--write-tree", resolved["branch"], resolved["target"]], cwd=repo_dir, check=False
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
    """Format RFC 2822 conforming git trailers block for review commits.

    Args:
        action_origin: Origin of the review action (default: 'pr_review').
        iteration: Review loop iteration number (>= 1).
        finding_ids: List of identified issue/finding keys (e.g. ['FIND-001']).
        severity: Review severity level ('critical', 'important', 'nit', or 'clean').

    Returns:
        str: Multiline trailers string.
    """
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
) -> dict[str, Any]:
    """Create a new pull request or update existing PR title and body via GitHub CLI.

    Args:
        repo_dir: Path to git repository.
        branch: Head branch name.
        target_branch: Base target branch (default: 'main').
        title: PR title.
        body: PR markdown description.
        proxy_env: Optional proxy environment variables.

    Returns:
        dict[str, Any]: Outcome dictionary with keys 'attempted', 'success', 'action', 'pr_number', 'output'.
    """
    clean_branch = branch.strip() if branch else ""
    if not shutil.which("gh") or not clean_branch:
        return {"attempted": False, "success": False, "action": "none", "pr_number": None, "output": ""}

    gh_env = {**os.environ, **(proxy_env or {}), "DOCKER_CLI_HINTS": "false"}

    try:
        view_proc = subprocess.run(
            ["gh", "pr", "view", clean_branch, "--json", "number,title"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
            env=gh_env,
            timeout=60,
        )
        if view_proc.returncode == 0 and view_proc.stdout.strip():
            data = json.loads(view_proc.stdout)
            pr_num = data.get("number")
            edit_cmd = ["gh", "pr", "edit", clean_branch]
            if title:
                edit_cmd.extend(["--title", title])
            if body:
                edit_cmd.extend(["--body", body])
            edit_proc = subprocess.run(
                edit_cmd,
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
                env=gh_env,
                timeout=60,
            )
            return {
                "attempted": True,
                "success": edit_proc.returncode == 0,
                "action": "edit",
                "pr_number": pr_num,
                "output": f"{edit_proc.stdout or ''}\n{edit_proc.stderr or ''}".strip(),
            }
        else:
            create_cmd = [
                "gh",
                "pr",
                "create",
                "--base",
                target_branch,
                "--head",
                clean_branch,
                "--title",
                title or clean_branch,
                "--body",
                body or f"Automated PR for branch {clean_branch}",
            ]
            create_proc = subprocess.run(
                create_cmd,
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
                env=gh_env,
                timeout=60,
            )
            out = f"{create_proc.stdout or ''}\n{create_proc.stderr or ''}".strip()
            pr_match = re.search(r"/pull/(\d+)", out)
            pr_num = int(pr_match.group(1)) if pr_match else None
            return {
                "attempted": True,
                "success": create_proc.returncode == 0,
                "action": "create",
                "pr_number": pr_num,
                "output": out,
            }
    except Exception as exc:
        return {"attempted": True, "success": False, "action": "error", "pr_number": None, "output": str(exc)}
