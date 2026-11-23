"""Stage 5 patch-size diff range: which qualified revisions the calibration diff compares, and how it is labelled.

The patch is measured from the merge-base of the execution head with the intent's target branch (``origin/<target>``
first, then the local ``<target>``), labelled ``target_merge_base``. The caller's three-dot diff (``base...head``)
compares against ``git merge-base base head``, which is derived from the current head on every run, so files that
arrived only through a merge of the target branch into the execution branch are never counted. The plan-branch base
below is used when no target branch resolves, when the target already contains the execution head, or when the plan
branch (or, when it is gone, the first resolvable fallback branch) already contains the target merge-base: its base is
then the newer one.

A decomposed flow calibrates nested branches such as ``I-.../P-.../E-.../_``. Every revision handed to git here is a
fully qualified ref or a SHA, followed by ``--`` where git accepts one, so a plan branch the host cannot resolve never
reaches ``git diff`` as a bare name (git would report exit 128 "ambiguous argument"). An unresolvable plan branch
degrades the measurement to the merge-base with a fallback branch (the intent branch, then ``main``), labelled
``merge_base_fallback``, and only when none resolves to the execution head's first parent, labelled
``first_parent_fallback``, instead of failing Stage 5.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sandbox_executor.git import run_git
from sandbox_executor.git_revisions import qualify_branch_revision, revision_args

logger = logging.getLogger(__name__)

INTENTS_LEDGER_REL = "holon-knowledge/ledger/intents.jsonl"
DEFAULT_TARGET_BRANCH = "main"

DIFF_BASE_TARGET_MERGE_BASE = "target_merge_base"
DIFF_BASE_PLAN = "plan"
DIFF_BASE_MERGE_BASE = "merge_base_fallback"
DIFF_BASE_FIRST_PARENT = "first_parent_fallback"


def intent_target_branch(intent_branch: str, head_rev: str, target_worktree: str) -> str:
    """The ``target_branch`` of the intent's latest row in the intents ledger at ``head_rev``, else ``main``.

    A decomposed child intent targets its parent's plan branch, so its patch is measured against that branch.
    """
    intent = intent_branch.removesuffix("/_").strip()
    res = run_git(["show", f"{head_rev}:{INTENTS_LEDGER_REL}"], cwd=target_worktree, check=False, timeout=30)
    if not intent or res.returncode != 0:
        return DEFAULT_TARGET_BRANCH
    target = ""
    for line in res.stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and str(row.get("branch") or "").removesuffix("/_") == intent:
            target = str(row.get("target_branch") or "") or target
    return target or DEFAULT_TARGET_BRANCH


def target_branch_refs(target_branch: str) -> tuple[str, ...]:
    """The qualified refs to try for the target branch: the origin tracking ref first, then the local branch."""
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
    """The first target ref to measure from, or None to keep the plan-branch base.

    A ref is skipped when it does not verify, already contains the execution head (a merged PR) or shares no history
    with it (no merge-base). The plan branch is
    kept when it already contains the target merge-base: its base is then the newer one (a decomposed child whose plan
    branch carries its parent's integrated work, or an execution branch that never merged the target).
    """
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
    """Choose the qualified revisions the patch-size diff compares, and label how the size is measured.

    Both refs are qualified through ``qualify_branch_revision`` so that a nested ``.../_`` branch reaches git as
    ``refs/heads/...``, ``refs/remotes/origin/...`` or a SHA. A target that cannot be qualified (``HEAD``, a PR ref)
    is passed through unchanged, so an unknown head still fails the diff loudly. An unresolvable base degrades, with a
    warning naming it, to the first of ``fallback_refs`` that resolves to a revision other than the target (the diff
    then runs from their merge-base, so a ledger-only tip commit does not stand in for the patch); a base that is
    empty or equal to the target, or with no usable fallback, degrades to the target's first parent.

    Args:
        base_ref: Plan branch (or resolved plan ref); empty when there is none to compare against.
        target_ref: Execution branch, resolved ref or SHA.
        target_worktree: Git worktree to resolve in.
        fallback_refs: Branches to measure from, in order, when ``base_ref`` cannot be resolved.
        target_refs: Qualified refs of the intent's target branch (``target_branch_refs``), tried first.

    Returns:
        ``(base revision, target revision, label)``: the label is ``DIFF_BASE_TARGET_MERGE_BASE``, ``DIFF_BASE_PLAN``
        or ``DIFF_BASE_MERGE_BASE`` with a qualified base, or ``DIFF_BASE_FIRST_PARENT`` with an empty base revision.
    """
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
