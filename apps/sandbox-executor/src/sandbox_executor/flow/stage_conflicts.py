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

_UNMERGED_PREFIXES = ("UU", "AA", "UD", "DU", "DD", "AU", "UA")


def _parse_porcelain_unmerged(stdout: str) -> list[str]:
    """Parse git status --porcelain output for unmerged/conflicting files."""
    conflicting_files: list[str] = []
    for line in stdout.splitlines():
        line_str = line.strip()
        if not line_str or len(line_str) < 3:
            continue
        prefix = line_str[:2]
        if prefix in _UNMERGED_PREFIXES or prefix.startswith("U") or prefix.endswith("U"):
            file_path = line_str[2:].strip().strip("\"'")
            if " -> " in file_path:
                file_path = file_path.split(" -> ")[1].strip().strip("\"'")
            if file_path not in conflicting_files:
                conflicting_files.append(file_path)
    return conflicting_files


def _probe_upstream_conflicts(repo_dir: str, probe_branch: str, target_branch: str) -> list[str]:
    """Probe upstream mergeability using merge-tree to detect prospective conflicts."""
    probed: list[str] = []
    try:
        merge_diag = check_pr_mergeability(repo_dir, branch=probe_branch, target_branch=target_branch)
        if not (merge_diag.get("is_dirty") or merge_diag.get("mergeable") == "CONFLICTING"):
            return probed

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
                    for p in line.split():
                        p_clean = p.strip(",:\"'")
                        if (os.sep in p_clean or "." in p_clean) and p_clean not in probed:
                            probed.append(p_clean)
    except Exception as exc:
        logger.debug("Non-fatal upstream mergeability probe in check_and_resolve_stage_conflicts: %s", exc)
    return probed


def _resolve_detected_ledgers(
    repo_dir: str,
    conflicting_files: list[str],
    probed_files: list[str],
    probe_branch: str,
) -> tuple[list[str], list[str]]:
    """Partition conflicts into ledgers vs code conflicts, resolving ledgers by pure append."""
    resolved_ledgers: list[str] = []
    non_ledger_conflicts: list[str] = []

    for f in conflicting_files:
        norm_f = f.replace("\\", "/")
        if not ("holon-knowledge/ledger" in norm_f and norm_f.endswith(".jsonl")):
            non_ledger_conflicts.append(f)
        elif probe_branch != "HEAD" and f in probed_files:
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
    """Inspect and resolve stage merge conflicts proactively before stage execution."""
    status_proc = run_git(["status", "--porcelain"], cwd=repo_dir, check=False)
    raw_out = getattr(status_proc, "stdout", "")
    stdout = raw_out if isinstance(raw_out, str) else ""
    conflicting_files = _parse_porcelain_unmerged(stdout)

    probe_branch = (branch or "").strip()
    probed_files: list[str] = []
    if not conflicting_files and probe_branch:
        probed_files = _probe_upstream_conflicts(repo_dir, probe_branch, target_branch)
        for pf in probed_files:
            if pf not in conflicting_files:
                conflicting_files.append(pf)

    for rel_path in _ledgers_with_conflict_markers(repo_dir):
        if rel_path not in conflicting_files:
            conflicting_files.append(rel_path)

    resolved_ledgers, non_ledger_conflicts = _resolve_detected_ledgers(
        repo_dir, conflicting_files, probed_files, probe_branch
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
