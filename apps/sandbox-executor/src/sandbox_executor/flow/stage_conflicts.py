"""Pre-push conflict check: detect unmerged files, probe mergeability, append-merge ledgers, raise on code conflicts."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow.git_refs import _resolve_branch_ref
from sandbox_executor.flow.github.pr import check_pr_mergeability
from sandbox_executor.flow.ledger_reconcile import ConflictResolutionError, _ledgers_with_conflict_markers
from sandbox_executor.flow.stage_conflict_resolution import classify_conflicts, resolve_classified_conflicts
from sandbox_executor.git import run_git

logger = logging.getLogger(__name__)

UNMERGED_PREFIXES = ("UU", "AA", "UD", "DU", "DD", "AU", "UA")


def _unmerged_path(line: str) -> str | None:
    """The path of one ``git status --porcelain`` line when it reports an unmerged file (rename arrows followed)."""
    line_str = line.strip()
    if len(line_str) < 3:
        return None
    prefix = line_str[:2]
    if not (prefix in UNMERGED_PREFIXES or prefix.startswith("U") or prefix.endswith("U")):
        return None
    file_path = line_str[2:].strip().strip("\"'")
    if " -> " in file_path:
        file_path = file_path.split(" -> ")[1].strip().strip("\"'")
    return file_path


def _parse_porcelain_unmerged(stdout: str) -> list[str]:
    """The unmerged paths ``git status --porcelain`` output reports, in order and without duplicates."""
    conflicting_files: list[str] = []
    for line in stdout.splitlines():
        file_path = _unmerged_path(line)
        if file_path is not None and file_path not in conflicting_files:
            conflicting_files.append(file_path)
    return conflicting_files


def _append_new(paths: list[str], extra: list[str]) -> None:
    for path in extra:
        if path not in paths:
            paths.append(path)


def _stdout_text(proc: Any) -> str:
    """The process's stdout when it is a string (a mocked process may carry anything else), else ``""``."""
    raw = getattr(proc, "stdout", "")
    return raw if isinstance(raw, str) else ""


def _upstream_conflicting(merge_diag: dict[str, Any]) -> bool:
    return bool(merge_diag.get("is_dirty")) or merge_diag.get("mergeable") == "CONFLICTING"


def _probe_target_ref(repo_dir: str, target_branch: str) -> str:
    """``origin/<target>`` when that ref verifies, else the bare target branch name."""
    target_ref = f"origin/{target_branch}"
    ref_chk = run_git(["rev-parse", "--verify", target_ref], cwd=repo_dir, check=False)
    return target_ref if ref_chk.returncode == 0 else target_branch


def _probe_branch_ref(repo_dir: str, branch: str) -> str:
    """``HEAD`` as is, else the branch's resolved local ref, else its name."""
    if branch == "HEAD":
        return branch
    return _resolve_branch_ref(repo_dir, branch, prefer_remote=False) or branch


def _conflict_line_paths(line: str) -> list[str]:
    """The path-like words of one ``git merge-tree`` conflict line."""
    words = (word.strip(",:\"'") for word in line.split())
    return [word for word in words if os.sep in word or "." in word]


def _merge_tree_conflict_paths(output: str) -> list[str]:
    """The paths ``git merge-tree`` output names on its conflict lines, in order and without duplicates."""
    probed: list[str] = []
    for line in output.splitlines():
        if "CONFLICT" in line or line.startswith("changed in both"):
            _append_new(probed, _conflict_line_paths(line))
    return probed


def _probe_upstream_conflicts(repo_dir: str, branch: str, target_branch: str) -> list[str]:
    """The paths ``git merge-tree`` reports conflicting when ``branch`` is not mergeable into ``target_branch``.

    The probe is non-fatal: any failure is logged at DEBUG level and yields no paths.
    """
    try:
        merge_diag = check_pr_mergeability(repo_dir, branch=branch, target_branch=target_branch)
        if not _upstream_conflicting(merge_diag):
            return []
        target_ref = _probe_target_ref(repo_dir, target_branch)
        branch_ref = _probe_branch_ref(repo_dir, branch)
        mt_proc = run_git(["merge-tree", branch_ref, target_ref], cwd=repo_dir, check=False)
        return _merge_tree_conflict_paths(_stdout_text(mt_proc))
    except Exception as exc:
        logger.debug("Non-fatal upstream mergeability probe in check_and_resolve_stage_conflicts: %s", exc)
        return []


@dataclass(frozen=True)
class ConflictDetection:
    """What ``detect_stage_conflicts`` found: the conflicting paths in order, and which of them the probe reported."""

    conflicting_files: list[str]
    probed_files: frozenset[str]
    probe_branch: str


def detect_stage_conflicts(repo_dir: str, target_branch: str, branch: str | None) -> ConflictDetection:
    """Collect the conflicting paths: unmerged files, then the upstream probe, then conflict-marked ledgers.

    The upstream mergeability probe of ``branch`` against ``target_branch`` runs only when nothing is unmerged and
    ``branch`` is non-empty (``None`` skips it: Stage 1's branch does not exist yet).
    """
    conflicting_files = _parse_porcelain_unmerged(
        _stdout_text(run_git(["status", "--porcelain"], cwd=repo_dir, check=False))
    )
    probe_branch = (branch or "").strip()
    probed_files: list[str] = []
    if not conflicting_files and probe_branch:
        probed_files = _probe_upstream_conflicts(repo_dir, probe_branch, target_branch)
        _append_new(conflicting_files, probed_files)
    # Also count ledgers whose working-tree copy holds line-anchored conflict markers
    _append_new(conflicting_files, _ledgers_with_conflict_markers(repo_dir))
    return ConflictDetection(conflicting_files, frozenset(probed_files), probe_branch)


def check_and_resolve_stage_conflicts(
    repo_dir: str,
    target_branch: str = "main",
    stage_name: str = "",
    branch: str | None = "HEAD",
) -> dict[str, Any]:
    """Inspect and resolve stage merge conflicts proactively before stage execution.

    Three steps: ``detect_stage_conflicts`` collects the conflicting paths, ``classify_conflicts`` labels each one
    (code conflict, ledger to resolve, deferred ledger) and ``resolve_classified_conflicts`` append-merges the ledgers.

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
    detection = detect_stage_conflicts(repo_dir, target_branch, branch)
    resolved_ledgers, unresolved = resolve_classified_conflicts(repo_dir, classify_conflicts(detection))
    if unresolved:
        err_msg = f"Unresolvable merge conflicts detected in stage '{stage_name or 'unknown'}': {', '.join(unresolved)}"
        raise ConflictResolutionError(err_msg)

    return {
        "status": "resolved" if resolved_ledgers else "clean",
        "conflicts_detected": bool(detection.conflicting_files),
        "resolved": not unresolved,
        "conflicting_files": detection.conflicting_files,
        "resolved_ledgers": resolved_ledgers,
        "stage": stage_name,
    }
