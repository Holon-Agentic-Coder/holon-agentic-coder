"""Stage 5 patch-size diff range: which qualified revisions the calibration diff compares, and how it is labelled.

A decomposed flow calibrates nested branches such as ``I-.../P-.../E-.../_``. Every revision handed to git here is a
fully qualified ref or a SHA, followed by ``--`` where git accepts one, so a plan branch the host cannot resolve never
reaches ``git diff`` as a bare name (git would report exit 128 "ambiguous argument"). An unresolvable plan branch
degrades the measurement to the execution head's first parent, labelled ``first_parent_fallback``, instead of failing
Stage 5.
"""

from __future__ import annotations

import logging
from typing import Any

from sandbox_executor.git import run_git
from sandbox_executor.git_revisions import qualify_branch_revision, revision_args

logger = logging.getLogger(__name__)

DIFF_BASE_PLAN = "plan"
DIFF_BASE_FIRST_PARENT = "first_parent_fallback"


def patch_diff_range(base_ref: str, target_ref: str, target_worktree: str) -> tuple[str, str, str]:
    """Choose the qualified revisions the patch-size diff compares, and label how the size is measured.

    Both refs are qualified through ``qualify_branch_revision`` so that a nested ``.../_`` branch reaches git as
    ``refs/heads/...``, ``refs/remotes/origin/...`` or a SHA. A target that cannot be qualified (``HEAD``, a PR ref)
    is passed through unchanged, so an unknown head still fails the diff loudly. A base that is empty, unresolvable
    or equal to the target degrades to the target's first parent, with a warning naming the unresolved base.

    Args:
        base_ref: Plan branch (or resolved plan ref); empty when there is none to compare against.
        target_ref: Execution branch, resolved ref or SHA.
        target_worktree: Git worktree to resolve in.

    Returns:
        ``(base revision, target revision, label)``: the label is ``DIFF_BASE_PLAN`` with a qualified base, or
        ``DIFF_BASE_FIRST_PARENT`` with an empty base revision.
    """
    target_rev = qualify_branch_revision(target_worktree, target_ref) or target_ref
    base_rev = qualify_branch_revision(target_worktree, base_ref) if base_ref else None
    if base_rev and base_rev != target_rev:
        return base_rev, target_rev, DIFF_BASE_PLAN
    if base_ref and not base_rev:
        logger.warning(
            "plan branch '%s' could not be resolved; patch size measured against the execution head's first parent",
            base_ref,
        )
    return "", target_rev, DIFF_BASE_FIRST_PARENT


def merge_history(base_rev: str, target_rev: str, target_worktree: str) -> dict[str, Any]:
    """Return the base SHA, merge-base SHA and merged commit count between two qualified revisions.

    Raises:
        RuntimeError: If ``rev-parse``, ``merge-base`` or ``rev-list`` exits non-zero.
    """
    res_base = run_git(
        ["rev-parse", "--verify", f"{base_rev}^{{commit}}"], cwd=target_worktree, check=False, timeout=30
    )
    if res_base.returncode != 0:
        raise RuntimeError(
            f"Git rev-parse failed for '{base_rev}' with exit code {res_base.returncode}: {res_base.stderr.strip()}"
        )
    # merge-base accepts no '--'; qualified refs or SHAs already keep it unambiguous.
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
