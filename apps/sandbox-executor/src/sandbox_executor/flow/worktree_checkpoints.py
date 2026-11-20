"""The flow checkpoints a worktree holds (``.holon/flow/checkpoint-*.json``), read for Stage 3 admission discovery.

Each readable checkpoint with a plan branch yields its plan branch, whether its current stage is execute or review, and
its parsed Stage 3 admission record (``None`` for an older checkpoint without one). An unreadable or foreign file is
skipped, never an error, so a stale checkpoint never blocks admission.
"""

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
    """One flow checkpoint in a worktree: its plan branch, stage and Stage 3 admission record."""

    plan_branch: str
    worktree: str
    in_flight_stage: bool  # the current stage is execute or review
    record: AdmissionRecord | None


def read_worktree_checkpoint(path: str, worktree: str) -> WorktreeCheckpoint | None:
    """A checkpoint's plan branch, stage and admission record; ``None`` for a bad file or one without a plan branch."""
    try:
        checkpoint = load_checkpoint(path)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        logger.debug("Skipping unreadable checkpoint %s: %s", path, exc)  # stale or foreign: never blocks
        return None
    if not checkpoint.context.plan_branch:
        return None
    in_flight_stage = checkpoint.current_stage in _IN_FLIGHT_STAGES
    record = AdmissionRecord.from_dict(checkpoint.context.admission)
    return WorktreeCheckpoint(str(checkpoint.context.plan_branch), worktree, in_flight_stage, record)


def worktree_checkpoints(worktree: str) -> list[WorktreeCheckpoint]:
    """Every readable flow checkpoint in ``worktree``, in file-name order."""
    paths = sorted(glob.glob(os.path.join(worktree, ".holon", "flow", "checkpoint-*.json")))
    return [checkpoint for path in paths if (checkpoint := read_worktree_checkpoint(path, worktree)) is not None]
