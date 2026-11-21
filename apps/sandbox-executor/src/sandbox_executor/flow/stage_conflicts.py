"""Pre-push conflict check: detect unmerged files, probe mergeability, append-merge ledgers, raise on code conflicts."""

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

UNMERGED_PREFIXES = ("UU", "AA", "UD", "DU", "DD", "AU", "UA")


def _parse_porcelain_unmerged(stdout: str) -> list[str]:
    """The unmerged paths ``git status --porcelain`` output reports, in order and without duplicates."""
    conflicting_files: list[str] = []
    for line in stdout.splitlines():
        line_str = line.strip()
        if not line_str or len(line_str) < 3:
            continue
        prefix = line_str[:2]
        if prefix in UNMERGED_PREFIXES or prefix.startswith("U") or prefix.endswith("U"):
            file_path = line_str[2:].strip().strip("\"'")
            if " -> " in file_path:
                file_path = file_path.split(" -> ")[1].strip().strip("\"'")
            if file_path not in conflicting_files:
                conflicting_files.append(file_path)
    return conflicting_files


def _probe_upstream_conflicts(repo_dir: str, branch: str, target_branch: str) -> list[str]:
    """The paths ``git merge-tree`` reports conflicting when ``branch`` is not mergeable into ``target_branch``.

    The probe is non-fatal: any failure is logged at DEBUG level and yields no paths.
    """
    probed: list[str] = []
    try:
        merge_diag = check_pr_mergeability(repo_dir, branch=branch, target_branch=target_branch)
        if merge_diag.get("is_dirty") or merge_diag.get("mergeable") == "CONFLICTING":
            target_ref = f"origin/{target_branch}"
            ref_chk = run_git(["rev-parse", "--verify", target_ref], cwd=repo_dir, check=False)
            if ref_chk.returncode != 0:
                target_ref = target_branch

            branch_ref = (
                branch if branch == "HEAD" else (_resolve_branch_ref(repo_dir, branch, prefer_remote=False) or branch)
            )
            mt_proc = run_git(["merge-tree", branch_ref, target_ref], cwd=repo_dir, check=False)
            mt_raw = getattr(mt_proc, "stdout", "")
            mt_out = mt_raw if isinstance(mt_raw, str) else ""
            if mt_out:
                for line in mt_out.splitlines():
                    if "CONFLICT" in line or line.startswith("changed in both"):
                        for p in line.split():
                            p_clean = p.strip(",:\"'")
                            if (os.sep in p_clean or "." in p_clean) and p_clean not in probed:
                                probed.append(p_clean)
    except Exception as exc:
        logger.debug("Non-fatal upstream mergeability probe in check_and_resolve_stage_conflicts: %s", exc)
    return probed


def _partition_and_resolve_conflicts(
    repo_dir: str,
    conflicting_files: list[str],
    probe_branch: str,
    probed_files: set[str],
) -> tuple[list[str], list[str]]:
    """Resolve the conflicted ledgers by pure append and return ``(resolved_ledgers, non_ledger_conflicts)``.

    A ledger that cannot be resolved counts as a non-ledger conflict, so the caller fails closed on it.
    """
    resolved_ledgers: list[str] = []
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
    return resolved_ledgers, non_ledger_conflicts


def check_and_resolve_stage_conflicts(
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
    # 1. Inspect git status --porcelain with isinstance guard for MagicMock test compatibility
    status_proc = run_git(["status", "--porcelain"], cwd=repo_dir, check=False)
    raw_out = getattr(status_proc, "stdout", "")
    stdout = raw_out if isinstance(raw_out, str) else ""
    conflicting_files = _parse_porcelain_unmerged(stdout)

    # 2. Check mergeability against target_branch if clean so far
    probe_branch = (branch or "").strip()
    probed_files: list[str] = []
    if not conflicting_files and probe_branch:
        probed_files = _probe_upstream_conflicts(repo_dir, probe_branch, target_branch)
        for pf in probed_files:
            if pf not in conflicting_files:
                conflicting_files.append(pf)

    # Also count ledgers whose working-tree copy holds line-anchored conflict markers
    for rel_path in _ledgers_with_conflict_markers(repo_dir):
        if rel_path not in conflicting_files:
            conflicting_files.append(rel_path)

    # 3. Resolve ledger conflicts by pure append; never stage them here
    resolved_ledgers, non_ledger_conflicts = _partition_and_resolve_conflicts(
        repo_dir, conflicting_files, probe_branch, set(probed_files)
    )

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
