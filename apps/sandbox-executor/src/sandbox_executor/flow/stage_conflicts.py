"""Proactive conflict check run before a stage pushes: rebase onto the target and reconcile ledgers."""

from __future__ import annotations

import logging
import os
from typing import Any

from sandbox_executor.flow.git_refs import _resolve_branch_ref
from sandbox_executor.flow.github.pr import check_pr_mergeability
from sandbox_executor.flow.ledger_reconcile import (
    ConflictResolutionError,
    _ledgers_with_conflict_markers,
    _resolve_conflicting_ledger,
)
from sandbox_executor.git import run_git

logger = logging.getLogger(__name__)


def check_and_resolve_stage_conflicts(  # noqa: C901, PLR0912, PLR0915
    repo_dir: str,
    target_branch: str = "main",
    stage_name: str = "",
    branch: str | None = "HEAD",
) -> dict[str, Any]:
    """Inspect and resolve stage merge conflicts proactively before stage execution.

    - Runs git status --porcelain to detect conflicting files, with isinstance(..., str) guards
      on stdout for test mock safety.
    - Inspects upstream mergeability of 'branch' against target_branch. The flow no longer checks its
      branches out, so each stage names the branch it works from; None skips this probe (Stage 1, whose
      branch does not exist yet).
    - A ledger counts as conflicted only when git reports it unmerged or a line starts with a conflict
      marker. Conflicted ledgers are resolved by pure append (``reconcile_ledger_file``) and never staged.
    - If non-ledger code conflicts exist and cannot be cleanly resolved, raises ConflictResolutionError.

    Args:
        repo_dir: Path to git repository.
        target_branch: Target base branch name (defaults to 'main').
        stage_name: Optional pipeline stage name for telemetry.
        branch: Branch or ref whose mergeability is probed (defaults to the checkout's 'HEAD').

    Returns:
        dict[str, Any]: Conflict check outcome with keys:
            - status: str ('clean', 'resolved', 'conflict')
            - conflicting_files: list[str]
            - resolved_ledgers: list[str]
            - stage: str

    Raises:
        ConflictResolutionError: When unresolvable non-ledger conflicts exist.
    """
    conflicting_files: list[str] = []
    resolved_ledgers: list[str] = []

    # 1. Inspect git status --porcelain with isinstance guard for MagicMock test compatibility
    status_proc = run_git(["status", "--porcelain"], cwd=repo_dir, check=False)
    raw_out = getattr(status_proc, "stdout", "")
    stdout = raw_out if isinstance(raw_out, str) else ""

    unmerged_prefixes = ("UU", "AA", "UD", "DU", "DD", "AU", "UA")
    for line in stdout.splitlines():
        line_str = line.strip()
        if not line_str or len(line_str) < 3:
            continue
        prefix = line_str[:2]
        if prefix in unmerged_prefixes or prefix.startswith("U") or prefix.endswith("U"):
            file_path = line_str[2:].strip().strip("\"'")
            if " -> " in file_path:
                file_path = file_path.split(" -> ")[1].strip().strip("\"'")
            if file_path not in conflicting_files:
                conflicting_files.append(file_path)

    # 2. Check mergeability against target_branch if clean so far
    probe_branch = (branch or "").strip()
    probed_files: list[str] = []
    if not conflicting_files and probe_branch:
        try:
            merge_diag = check_pr_mergeability(repo_dir, branch=probe_branch, target_branch=target_branch)
            if merge_diag.get("is_dirty") or merge_diag.get("mergeable") == "CONFLICTING":
                target_ref = f"origin/{target_branch}"
                ref_chk = run_git(["rev-parse", "--verify", target_ref], cwd=repo_dir, check=False)
                if ref_chk.returncode != 0:
                    target_ref = target_branch

                branch_ref = (
                    probe_branch
                    if probe_branch == "HEAD"
                    else (_resolve_branch_ref(repo_dir, probe_branch, prefer_remote=False) or probe_branch)
                )
                mt_proc = run_git(["merge-tree", branch_ref, target_ref], cwd=repo_dir, check=False)
                mt_raw = getattr(mt_proc, "stdout", "")
                mt_out = mt_raw if isinstance(mt_raw, str) else ""
                if mt_out:
                    for line in mt_out.splitlines():
                        if "CONFLICT" in line or line.startswith("changed in both"):
                            parts = line.split()
                            for p in parts:
                                p_clean = p.strip(",:\"'")
                                if (os.sep in p_clean or "." in p_clean) and p_clean not in conflicting_files:
                                    conflicting_files.append(p_clean)
                                    probed_files.append(p_clean)
        except Exception as exc:
            logger.debug("Non-fatal upstream mergeability probe in check_and_resolve_stage_conflicts: %s", exc)

    # Also count ledgers whose working-tree copy holds line-anchored conflict markers
    for rel_path in _ledgers_with_conflict_markers(repo_dir):
        if rel_path not in conflicting_files:
            conflicting_files.append(rel_path)

    # 3. Resolve ledger conflicts by pure append; never stage them here
    non_ledger_conflicts: list[str] = []
    for f in conflicting_files:
        norm_f = f.replace("\\", "/")
        if not ("holon-knowledge/ledger" in norm_f and norm_f.endswith(".jsonl")):
            non_ledger_conflicts.append(f)
        elif probe_branch != "HEAD" and f in probed_files:
            # The probed branch is not checked out, so the working-tree copy is not its ledger; append-only
            # ledgers reconcile at merge time and must not be rewritten in the operator's checkout here.
            continue
        elif _resolve_conflicting_ledger(repo_dir, norm_f):
            resolved_ledgers.append(norm_f)
        else:
            non_ledger_conflicts.append(norm_f)

    if non_ledger_conflicts:
        err_msg = (
            f"Unresolvable merge conflicts detected in stage '{stage_name or 'unknown'}': "
            f"{', '.join(non_ledger_conflicts)}"
        )
        raise ConflictResolutionError(err_msg)

    status_str = "resolved" if resolved_ledgers else "clean"
    return {
        "status": status_str,
        "conflicts_detected": bool(conflicting_files),
        "resolved": not bool(non_ledger_conflicts),
        "conflicting_files": conflicting_files,
        "resolved_ledgers": resolved_ledgers,
        "stage": stage_name,
    }
