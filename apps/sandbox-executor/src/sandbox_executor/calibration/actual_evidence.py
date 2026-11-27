from __future__ import annotations

import contextlib
import os
import re
from dataclasses import dataclass
from typing import Any

from sandbox_executor.calibration.branches import extract_branch_components
from sandbox_executor.calibration.predicted import PredictedMetrics
from sandbox_executor.calibration.refs import read_git_file
from sandbox_executor.calibration_diff import execution_patch_numstat
from sandbox_executor.calibration_ev import is_matching_record, parse_summary_records
from sandbox_executor.calibration_evaluated_sha import LEDGER_EXECUTIONS_REL
from sandbox_executor.calibration_outcome import latest_summary_row
from sandbox_executor.calibration_rows import is_execution_row
from sandbox_executor.git import is_metadata_path
from sandbox_executor.ledger import read_merged_text, with_ref_fragments


@dataclass(frozen=True)
class ExecutionEvidence:
    execution_id: str
    plan_branch: str
    predicted: PredictedMetrics
    target_worktree: str
    records: list[dict[str, Any]]
    best_record: dict[str, Any] | None
    numstat_stdout: str
    patch_meta: dict[str, Any]


def _summary_records(
    execution_id: str, plan_branch: str, target_ref: str, target_worktree: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    canonical_content = read_git_file(target_ref, LEDGER_EXECUTIONS_REL, target_worktree)
    jsonl_content = with_ref_fragments(canonical_content, target_worktree, target_ref, "executions.jsonl")
    records = parse_summary_records(jsonl_content) if jsonl_content else []
    matching = [r for r in records if is_matching_record(r, execution_id, plan_branch)]
    if not matching and (disk_content := read_merged_text(os.path.join(target_worktree, LEDGER_EXECUTIONS_REL))):
        with contextlib.suppress(Exception):
            disk_records = parse_summary_records(disk_content)
            disk_matching = [r for r in disk_records if is_matching_record(r, execution_id, plan_branch)]
            if disk_matching:
                records = disk_records
                matching = disk_matching
    return records, matching


def _patch_numstat(
    plan_branch: str, execution_branch: str, target_ref: str, target_worktree: str
) -> tuple[str, dict[str, Any]]:
    intent_branch = extract_branch_components(plan_branch)["intent_branch"] if plan_branch else ""
    res, patch_meta = execution_patch_numstat(plan_branch, execution_branch, target_ref, target_worktree, intent_branch)
    return res.stdout, patch_meta


def gather_execution_evidence(
    execution_id: str,
    plan_branch: str,
    predicted: PredictedMetrics,
    execution_branch: str = "",
    target_worktree: str = ".",
) -> ExecutionEvidence:
    target_ref = execution_branch if execution_branch else "HEAD"
    records, matching = _summary_records(execution_id, plan_branch, target_ref, target_worktree)
    # why: Stage 4 and Stage 5 rows describe later stages, so only the Stage 3 row sets exit_code and p_success
    best_record = latest_summary_row([r for r in matching if is_execution_row(r, execution_id)])
    numstat_stdout, patch_meta = _patch_numstat(plan_branch, execution_branch, target_ref, target_worktree)
    return ExecutionEvidence(
        execution_id=execution_id,
        plan_branch=plan_branch,
        predicted=predicted,
        target_worktree=target_worktree,
        records=records,
        best_record=best_record,
        numstat_stdout=numstat_stdout,
        patch_meta=patch_meta,
    )


def parse_numstat(text: str) -> tuple[list[str], int, int, int]:
    changed_paths: list[str] = []
    insertions = deletions = 0
    for line in text.splitlines():
        parts = line.strip().split("\t")
        if len(parts) < 3 or is_metadata_path(parts[2]):
            continue
        changed_paths.append(parts[2].strip())
        if parts[0] != "-":
            insertions += int(parts[0])
        if parts[1] != "-":
            deletions += int(parts[1])
    return changed_paths, len(changed_paths), insertions, deletions


def parse_shortstat(text: str) -> tuple[int, int, int]:
    fc_m = re.search(r"(\d+)\s+file", text)
    ins_m = re.search(r"(\d+)\s+insertion", text)
    del_m = re.search(r"(\d+)\s+deletion", text)
    return (
        int(fc_m.group(1)) if fc_m else 0,
        int(ins_m.group(1)) if ins_m else 0,
        int(del_m.group(1)) if del_m else 0,
    )
