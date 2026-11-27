from __future__ import annotations

import logging
import subprocess
from typing import Any

from sandbox_executor.git import run_git
from sandbox_executor.git_revisions import qualify_branch_revision, revision_args
from sandbox_executor.ledger import read_merged_rows_at_ref

logger = logging.getLogger(__name__)

DEFAULT_TARGET_BRANCH = "main"

# why: measuring from the target merge-base never counts files that arrived only through merging the target branch.
DIFF_BASE_TARGET_MERGE_BASE = "target_merge_base"
# why: a re-run after a merge-commit merge falls back here and recounts target files; calibrate before merge.
DIFF_BASE_PLAN = "plan"
DIFF_BASE_MERGE_BASE = "merge_base_fallback"
DIFF_BASE_FIRST_PARENT = "first_parent_fallback"


# why: a decomposed child intent targets its parent's plan branch, so its patch is measured against that branch.
def intent_target_branch(intent_branch: str, head_rev: str, target_worktree: str) -> str:
    intent = intent_branch.removesuffix("/_").strip()
    if not intent:
        return DEFAULT_TARGET_BRANCH
    target = ""
    for row in read_merged_rows_at_ref(target_worktree, head_rev, "intents.jsonl", git=run_git):
        if str(row.get("branch") or "").removesuffix("/_") == intent:
            target = str(row.get("target_branch") or "") or target
    return target or DEFAULT_TARGET_BRANCH


def target_branch_refs(target_branch: str) -> tuple[str, ...]:
    bare = target_branch.strip().removeprefix("refs/heads/").removeprefix("origin/")
    return (f"refs/remotes/origin/{bare}", f"refs/heads/{bare}") if bare else ()


def _verified(target_worktree: str, revision: str) -> bool:
    res = run_git(["rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"], cwd=target_worktree, check=False)
    return res.returncode == 0


def _is_ancestor(target_worktree: str, ancestor: str, descendant: str) -> bool:
    res = run_git(["merge-base", "--is-ancestor", ancestor, descendant], cwd=target_worktree, check=False)
    return res.returncode == 0


def _target_merge_base_ref(
    target_rev: str, target_worktree: str, target_refs: tuple[str, ...], plan_rev: str | None
) -> str | None:
    for ref in target_refs:
        if not _verified(target_worktree, ref) or _is_ancestor(target_worktree, target_rev, ref):
            continue
        res = run_git(["merge-base", ref, target_rev], cwd=target_worktree, check=False, timeout=30)
        merge_base = res.stdout.strip() if res.returncode == 0 else ""
        if not merge_base:
            continue
        if plan_rev and _is_ancestor(target_worktree, merge_base, plan_rev):
            return None
        return ref
    return None


def patch_diff_range(
    base_ref: str,
    target_ref: str,
    target_worktree: str,
    fallback_refs: tuple[str, ...] = (),
    target_refs: tuple[str, ...] = (),
) -> tuple[str, str, str]:
    # why: a bare nested ``.../_`` name makes git exit 128 "ambiguous argument"; an unqualifiable head fails loudly.
    target_rev = qualify_branch_revision(target_worktree, target_ref) or target_ref
    base_rev = qualify_branch_revision(target_worktree, base_ref) if base_ref else None
    anchor_rev = base_rev or next(
        (rev for ref in fallback_refs if ref and (rev := qualify_branch_revision(target_worktree, ref))), None
    )
    target_base = _target_merge_base_ref(target_rev, target_worktree, target_refs, anchor_rev)
    if target_base:
        return target_base, target_rev, DIFF_BASE_TARGET_MERGE_BASE
    if base_rev and base_rev != target_rev:
        return base_rev, target_rev, DIFF_BASE_PLAN
    if base_ref and not base_rev:
        for ref in fallback_refs:
            fallback_rev = qualify_branch_revision(target_worktree, ref) if ref else None
            if fallback_rev and fallback_rev != target_rev:
                logger.warning(
                    "plan branch '%s' could not be resolved; patch size measured from the merge-base with '%s'",
                    base_ref,
                    ref,
                )
                return fallback_rev, target_rev, DIFF_BASE_MERGE_BASE
        logger.warning(
            "plan branch '%s' could not be resolved; patch size measured against the execution head's first parent",
            base_ref,
        )
    return "", target_rev, DIFF_BASE_FIRST_PARENT


def merge_history(base_rev: str, target_rev: str, target_worktree: str) -> dict[str, Any]:
    res_base = run_git(
        ["rev-parse", "--verify", f"{base_rev}^{{commit}}"], cwd=target_worktree, check=False, timeout=30
    )
    if res_base.returncode != 0:
        raise RuntimeError(
            f"Git rev-parse failed for '{base_rev}' with exit code {res_base.returncode}: {res_base.stderr.strip()}"
        )
    # why: merge-base accepts no '--'; qualified refs or SHAs keep it unambiguous.
    res_mb = run_git(["merge-base", base_rev, target_rev], cwd=target_worktree, check=False, timeout=30)
    if res_mb.returncode != 0:
        raise RuntimeError(
            f"Git merge-base failed between '{base_rev}' and '{target_rev}' "
            f"with exit code {res_mb.returncode}: {res_mb.stderr.strip()}"
        )
    res_merges = run_git(
        ["rev-list", "--count", "--merges", *revision_args(f"{base_rev}..{target_rev}")],
        cwd=target_worktree,
        check=False,
        timeout=30,
    )
    if res_merges.returncode != 0:
        raise RuntimeError(
            f"Git rev-list failed between '{base_rev}' and '{target_rev}' "
            f"with exit code {res_merges.returncode}: {res_merges.stderr.strip()}"
        )
    try:
        merged_commit_count = int(res_merges.stdout.strip())
    except ValueError:
        merged_commit_count = 0
    return {
        "base_sha": res_base.stdout.strip(),
        "merge_base_sha": res_mb.stdout.strip(),
        "merged_commit_count": merged_commit_count,
    }


def execution_patch_numstat(
    plan_branch: str, execution_branch: str, target_ref: str, target_worktree: str, intent_branch: str
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    fallback_refs = (intent_branch, DEFAULT_TARGET_BRANCH) if plan_branch else ()
    target_refs = (
        target_branch_refs(intent_target_branch(intent_branch, target_ref, target_worktree)) if execution_branch else ()
    )
    base_rev, target_rev, diff_base = patch_diff_range(
        plan_branch if execution_branch else "", target_ref, target_worktree, fallback_refs, target_refs
    )
    patch_meta: dict[str, Any] = {"diff_base": diff_base}
    diff_range = f"{base_rev}...{target_rev}" if base_rev else f"{target_rev}~1..{target_rev}"
    res = run_git(["diff", "--numstat", diff_range, "--"], cwd=target_worktree, check=False, timeout=30)
    if res.returncode != 0:
        raise RuntimeError(
            f"Git diff failed between '{base_rev or f'{target_rev}~1'}' and '{target_rev}' "
            f"with exit code {res.returncode}: {res.stderr.strip()}"
        )
    if diff_base in (DIFF_BASE_PLAN, DIFF_BASE_TARGET_MERGE_BASE):
        patch_meta.update(merge_history(base_rev, target_rev, target_worktree))
    return res, patch_meta


def patch_size_base_line(report_data: dict[str, Any]) -> str:
    label = report_data["diff_base"]
    line = f"- **Patch Size Base:** `{label}`"
    if label == DIFF_BASE_TARGET_MERGE_BASE and report_data.get("merge_base_sha"):
        line += f" (merge-base `{report_data['merge_base_sha']}` with the target branch)"
    elif label in (DIFF_BASE_MERGE_BASE, DIFF_BASE_FIRST_PARENT):
        line += " (not measured against the plan branch)"
    return line
