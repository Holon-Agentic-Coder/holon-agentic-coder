from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow.git_refs import _resolve_branch_ref
from sandbox_executor.flow.github.pr import check_pr_mergeability
from sandbox_executor.flow.ledger_reconcile import ConflictResolutionError, _ledgers_with_conflict_markers
from sandbox_executor.flow.stage_conflict_resolution import (
    ConflictKind,
    classify_conflicts,
    resolve_classified_conflicts,
)
from sandbox_executor.flow.target_probe import merge_tree_conflicts
from sandbox_executor.git import run_git

logger = logging.getLogger(__name__)

UNMERGED_PREFIXES = ("UU", "AA", "UD", "DU", "DD", "AU", "UA")


def _unmerged_path(line: str) -> str | None:
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
    # why: a mocked process may carry anything else as stdout
    raw = getattr(proc, "stdout", "")
    return raw if isinstance(raw, str) else ""


def _upstream_conflicting(merge_diag: dict[str, Any]) -> bool:
    return bool(merge_diag.get("is_dirty")) or merge_diag.get("mergeable") == "CONFLICTING"


def _probe_target_ref(repo_dir: str, target_branch: str) -> str:
    target_ref = f"origin/{target_branch}"
    ref_chk = run_git(["rev-parse", "--verify", target_ref], cwd=repo_dir, check=False)
    return target_ref if ref_chk.returncode == 0 else target_branch


def _probe_branch_ref(repo_dir: str, branch: str) -> str:
    if branch == "HEAD":
        return branch
    return _resolve_branch_ref(repo_dir, branch, prefer_remote=False) or branch


def _probe_upstream_conflicts(repo_dir: str, branch: str, target_branch: str) -> list[str]:
    # why: the container never fetches, so the probe is non-fatal and any failure yields no paths
    try:
        merge_diag = check_pr_mergeability(repo_dir, branch=branch, target_branch=target_branch)
        if not _upstream_conflicting(merge_diag):
            return []
        target_ref = _probe_target_ref(repo_dir, target_branch)
        branch_ref = _probe_branch_ref(repo_dir, branch)
        return list(merge_tree_conflicts(repo_dir, branch_ref, target_ref))
    except Exception as exc:
        logger.debug("Non-fatal upstream mergeability probe in check_and_resolve_stage_conflicts: %s", exc)
        return []


@dataclass(frozen=True)
class ConflictDetection:
    conflicting_files: list[str]
    probed_files: frozenset[str]
    probe_branch: str


def detect_stage_conflicts(repo_dir: str, target_branch: str, branch: str | None) -> ConflictDetection:
    conflicting_files = _parse_porcelain_unmerged(
        _stdout_text(run_git(["status", "--porcelain"], cwd=repo_dir, check=False))
    )
    probe_branch = (branch or "").strip()
    probed_files: list[str] = []
    # why: a None branch skips the probe because Stage 1's branch does not exist yet
    if not conflicting_files and probe_branch:
        probed_files = _probe_upstream_conflicts(repo_dir, probe_branch, target_branch)
        _append_new(conflicting_files, probed_files)
    _append_new(conflicting_files, _ledgers_with_conflict_markers(repo_dir))
    return ConflictDetection(conflicting_files, frozenset(probed_files), probe_branch)


def check_and_resolve_stage_conflicts(
    repo_dir: str,
    target_branch: str = "main",
    stage_name: str = "",
    branch: str | None = "HEAD",
) -> dict[str, Any]:
    detection = detect_stage_conflicts(repo_dir, target_branch, branch)
    classified = classify_conflicts(detection, repo_dir)
    resolved, unresolved = resolve_classified_conflicts(repo_dir, classified)
    if unresolved:
        raise ConflictResolutionError(_unresolved_message(classified, unresolved, stage_name))

    kinds = dict(classified)
    return {
        "status": "resolved" if resolved else "clean",
        "conflicts_detected": bool(detection.conflicting_files),
        "resolved": not unresolved,
        "conflicting_files": detection.conflicting_files,
        "resolved_ledgers": [path for path in resolved if kinds[path] is ConflictKind.LEDGER],
        "resolved_paths": [{"path": path, "class": kinds[path].value} for path in resolved],
        "stage": stage_name,
    }


def _unresolved_message(classified: list[tuple[str, ConflictKind]], unresolved: list[str], stage_name: str) -> str:
    all_code = all(kind is ConflictKind.CODE for path, kind in classified if path in unresolved)
    kind_word = "code" if all_code else "merge"
    return f"Unresolvable {kind_word} conflicts detected in stage '{stage_name or 'unknown'}': {', '.join(unresolved)}"
