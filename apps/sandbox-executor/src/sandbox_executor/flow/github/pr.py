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


def check_pr_mergeability(  # noqa: C901, PLR0912, PLR0915
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
        gh_env = {**os.environ, **(proxy_env or {}), "DOCKER_CLI_HINTS": "false"}
        for attempt in range(3):
            try:
                gh_proc = subprocess.run(
                    [
                        "gh",
                        "pr",
                        "view",
                        clean_branch,
                        "--json",
                        "number,mergeable,mergeStateStatus,statusCheckRollup",
                    ],
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=60,
                    env=gh_env,
                )
                if gh_proc.returncode != 0:
                    break
                if gh_proc.returncode == 0 and gh_proc.stdout.strip():
                    data = json.loads(gh_proc.stdout)
                    mergeable = str(data.get("mergeable") or "UNKNOWN").upper()
                    merge_state_status = str(data.get("mergeStateStatus") or "UNKNOWN").upper()

                    # Retry briefly if GitHub is still computing mergeability (UNKNOWN)
                    if mergeable == "UNKNOWN" and attempt < 2:
                        time.sleep(2)
                        continue

                    is_dirty = (merge_state_status == "DIRTY") or (mergeable == "CONFLICTING")
                    return {
                        "mergeable": mergeable,
                        "merge_state_status": merge_state_status,
                        "is_dirty": is_dirty,
                        "source": "github_cli",
                        "pr_number": data.get("number"),
                        "status_check_rollup": data.get("statusCheckRollup"),
                        "diagnostics": {},
                    }
            except Exception as e:
                logger.debug(f"gh pr view check failed: {e}")
                break

    # 2. Fallback to local git inspection
    is_dirty = False
    mergeable = "UNKNOWN"
    merge_state_status = "UNKNOWN"
    diagnostics: dict[str, Any] = {}

    try:
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

        # Gate: both refs must resolve to commits before a merge is simulated. merge-tree exits 1 for an
        # unresolvable ref as well as for a real conflict, so passing it through would report a false DIRTY.
        resolved: dict[str, str] = {}
        for role, ref, candidates in (
            ("branch", branch_ref, branch_candidates),
            ("target", target_ref, target_candidates),
        ):
            sha, rp_stderr = _resolve_commit_ref(repo_dir, ref)
            if sha is None:
                # Outside a repository every ref fails to resolve; that is UNKNOWN, not a missing ref.
                git_dir = run_git(["rev-parse", "--git-dir"], cwd=repo_dir, check=False)
                if git_dir.returncode != 0:
                    return {
                        "mergeable": "UNKNOWN",
                        "merge_state_status": "UNKNOWN",
                        "is_dirty": False,
                        "source": "git_local",
                        "pr_number": None,
                        "status_check_rollup": None,
                        "diagnostics": {"error": f"not a git repository: {repo_dir}", "stderr": rp_stderr},
                    }
                # Report the name that was actually checked; candidates_tried lists every substitution.
                missing_ref = ref
                logger.info(
                    "Cannot evaluate mergeability for ref %s (%s): ref could not be resolved to a commit.",
                    missing_ref,
                    role,
                )
                return {
                    "mergeable": "UNKNOWN",
                    "merge_state_status": "MISSING_REF",
                    "is_dirty": False,
                    "source": "git_local",
                    "pr_number": None,
                    "status_check_rollup": None,
                    "diagnostics": {
                        "missing_ref": missing_ref,
                        "ref_role": role,
                        "candidates_tried": candidates,
                        "stderr": rp_stderr,
                        "message": f"cannot evaluate mergeability for ref {missing_ref}",
                    },
                }
            resolved[role] = sha

        mt_proc = run_git(
            ["merge-tree", "--write-tree", resolved["branch"], resolved["target"]], cwd=repo_dir, check=False
        )
        if mt_proc.returncode == 0:
            mergeable = "MERGEABLE"
            merge_state_status = "CLEAN"
        elif mt_proc.returncode == 1:
            is_dirty = True
            mergeable = "CONFLICTING"
            merge_state_status = "DIRTY"
        else:
            # Unrelated histories, a git too old for --write-tree, or any other failure: an honest UNKNOWN.
            diagnostics = {
                "merge_tree_exit_code": mt_proc.returncode,
                "stderr": mt_proc.stderr.strip() if isinstance(mt_proc.stderr, str) else "",
            }
    except Exception as exc:
        logger.debug(f"Local git mergeability check encountered error: {exc}")
        is_dirty = False
        mergeable = "UNKNOWN"
        merge_state_status = "UNKNOWN"
        diagnostics = {"error": str(exc)}

    return {
        "mergeable": mergeable,
        "merge_state_status": merge_state_status,
        "is_dirty": is_dirty,
        "source": "git_local",
        "pr_number": None,
        "status_check_rollup": None,
        "diagnostics": diagnostics,
    }


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


def generate_semantic_pr_metadata(  # noqa: C901, PLR0912, PLR0915
    intent_data: dict[str, Any],
    plan_metadata: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Generate a conventional commit PR title and structured markdown description.

    Transforms intent data and optional plan metadata into Conventional Commits
    title format (e.g. 'feat(scope): title') and a structured markdown PR body
    containing Summary, Key Changes, References, and Plan & Validation Metadata.

    Args:
        intent_data: Dictionary containing intent fields (category, slug, title, description, goal, references, labels).
        plan_metadata: Optional dictionary containing plan fields (plan_id, ev, agent, model).

    Returns:
        tuple[str, str]: (pr_title, pr_body)
    """
    category_map: dict[str, str] = {
        "feature": "feat",
        "feat": "feat",
        "fix": "fix",
        "bugfix": "fix",
        "bug": "fix",
        "refactor": "refactor",
        "refactoring": "refactor",
        "documentation": "docs",
        "doc": "docs",
        "docs": "docs",
        "chore": "chore",
        "test": "test",
        "tests": "test",
        "testing": "test",
        "build": "build",
        "ci": "ci",
        "perf": "perf",
    }
    raw_cat = str(intent_data.get("category") or "feat").strip().lower()
    conv_prefix = category_map.get(raw_cat, "feat")

    # Scope extraction: primary label or first slug component
    scope = ""
    labels = intent_data.get("labels")
    if isinstance(labels, list) and labels:
        first_lbl = str(labels[0]).strip().lower()
        if first_lbl:
            scope = first_lbl
    elif isinstance(labels, str) and labels.strip():
        scope = labels.split(",")[0].strip().lower()

    if not scope:
        raw_slug = str(intent_data.get("slug") or "").strip()
        if raw_slug:
            scope = raw_slug.split("-")[0].strip().lower()

    if not scope:
        scope = "core"

    # Sanitise scope
    scope = re.sub(r"[^a-zA-Z0-9_-]+", "", scope) or "core"

    # Title extraction
    raw_title = str(
        intent_data.get("title") or intent_data.get("description") or intent_data.get("goal") or "Update components"
    ).strip()
    # Strip any multiline text
    raw_title = raw_title.splitlines()[0].strip()
    # Strip existing conventional commit prefixes to avoid duplicate prefixes
    raw_title = re.sub(
        r"^(feat|fix|refactor|docs|chore|test|build|ci|perf)(\([^)]+\))?:\s*",
        "",
        raw_title,
        flags=re.IGNORECASE,
    ).strip()

    full_title = f"{conv_prefix}({scope}): {raw_title}"
    if len(full_title) > 100:
        full_title = full_title[:97].rstrip() + "..."

    # Description generation
    body_lines: list[str] = []

    # Summary section
    body_lines.append("## Summary")
    desc = intent_data.get("description")
    goal = intent_data.get("goal")
    if desc:
        body_lines.append(str(desc).strip())
    if goal and goal != desc:
        if desc:
            body_lines.append("")
        body_lines.append(f"**Goal:**\n{str(goal).strip()}")
    if not desc and not goal:
        body_lines.append("No description provided.")
    body_lines.append("")

    # Key Changes section
    body_lines.append("## Key Changes")
    changes_listed = False
    if goal:
        goal_text = str(goal).strip()
        bullet_candidates = [
            line.strip().lstrip("-* ").strip()
            for line in goal_text.splitlines()
            if line.strip().startswith(("-", "*", "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9."))
            or bool(re.match(r"^\d+[\.\)]", line.strip()))
        ]
        if bullet_candidates:
            for cand in bullet_candidates:
                clean_cand = re.sub(r"^\d+[\.\)]\s*", "", cand).strip()
                if clean_cand:
                    body_lines.append(f"- {clean_cand}")
            changes_listed = True
    if not changes_listed:
        body_lines.append(f"- Implement {raw_title}")
    body_lines.append("")

    # References section
    body_lines.append("## References")
    references = intent_data.get("references")
    if references:
        if isinstance(references, list):
            reference_list = [str(r).strip() for r in references if str(r).strip()]
        elif isinstance(references, str):
            reference_list = [r.strip() for r in references.split(",") if r.strip()]
        else:
            reference_list = []
        if reference_list:
            body_lines.append(f"References: {', '.join(reference_list)}")
        else:
            body_lines.append("None")
    else:
        body_lines.append("None")
    body_lines.append("")

    # Plan & Validation Metadata section
    body_lines.append("## Plan & Validation Metadata")
    plan_meta = plan_metadata or {}
    plan_id = plan_meta.get("plan_id") or "N/A"
    ev = plan_meta.get("ev")
    if ev is None and isinstance(plan_meta.get("metrics"), dict):
        ev = plan_meta["metrics"].get("ev")
    ev_str = f"{ev:.2f}" if isinstance(ev, (int, float)) else str(ev or "N/A")
    agent = plan_meta.get("agent") or intent_data.get("agent", "N/A")
    model = plan_meta.get("model") or intent_data.get("model", "N/A")
    body_lines.append(f"- **Plan ID:** `{plan_id}`")
    body_lines.append(f"- **Expected Value (EV):** `{ev_str}`")
    body_lines.append(f"- **Agent:** `{agent}`")
    body_lines.append(f"- **Model:** `{model}`")
    body_lines.append("")

    return full_title, "\n".join(body_lines)


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
