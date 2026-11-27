from __future__ import annotations

import glob
import logging
import os
from dataclasses import dataclass

from sandbox_executor.flow.admission_state import AdmissionRecord
from sandbox_executor.flow.checkpoint import load_checkpoint
from sandbox_executor.flow.context import FlowStage

logger = logging.getLogger(__name__)

_IN_FLIGHT_STAGES = frozenset({FlowStage.EXECUTE, FlowStage.REVIEW})


@dataclass(frozen=True)
class WorktreeCheckpoint:
    plan_branch: str
    worktree: str
    in_flight_stage: bool
    record: AdmissionRecord | None
    execution_branch: str | None = None


def read_worktree_checkpoint(path: str, worktree: str) -> WorktreeCheckpoint | None:
    try:
        checkpoint = load_checkpoint(path)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        # why: a stale or foreign checkpoint must never block admission
        logger.debug("Skipping unreadable checkpoint %s: %s", path, exc)
        return None
    if not checkpoint.context.plan_branch:
        return None
    in_flight_stage = checkpoint.current_stage in _IN_FLIGHT_STAGES
    record = AdmissionRecord.from_dict(checkpoint.context.admission)
    execution_branch = str(checkpoint.context.execution_branch) if checkpoint.context.execution_branch else None
    return WorktreeCheckpoint(str(checkpoint.context.plan_branch), worktree, in_flight_stage, record, execution_branch)


def worktree_checkpoints(worktree: str) -> list[WorktreeCheckpoint]:
    paths = sorted(glob.glob(os.path.join(worktree, ".holon", "flow", "checkpoint-*.json")))
    return [checkpoint for path in paths if (checkpoint := read_worktree_checkpoint(path, worktree)) is not None]
