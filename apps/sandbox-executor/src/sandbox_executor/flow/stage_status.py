"""Pipeline stage execution statuses and summary representations."""

from __future__ import annotations

from enum import StrEnum


class StageStatus(StrEnum):
    """Execution status of an individual pipeline stage."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    HALTED_FOR_HUMAN = "halted_for_human"
    APPROVED = "approved"
    SKIPPED = "skipped"

    @classmethod
    def from_str(cls, val: str | StageStatus) -> StageStatus:
        """Parse string or enum instance to StageStatus case-insensitively."""
        if isinstance(val, StageStatus):
            return val
        clean = str(val).strip().lower()
        for status in cls:
            if status.value == clean:
                return status
        raise ValueError(f"Unknown StageStatus '{val}'. Valid statuses: {[s.value for s in cls]}")


SUMMARY_STATUS_WORDS: dict[StageStatus, str] = {
    StageStatus.SUCCESS: "ok",
    StageStatus.FAILED: "failed",
    StageStatus.HALTED_FOR_HUMAN: "halted",
    StageStatus.APPROVED: "approved",
    StageStatus.SKIPPED: "skipped",
    StageStatus.PENDING: "pending",
    StageStatus.RUNNING: "running",
}
REVIEW_BOUNDARY_STATUSES: frozenset[StageStatus] = frozenset({StageStatus.APPROVED, StageStatus.HALTED_FOR_HUMAN})
"""Stage statuses that mark the consensus boundary where Stage 4 ended.

StageStatus.HALTED_FOR_HUMAN is the legacy value kept for backward compatibility with older
checkpoints and ledger rows; StageStatus.APPROVED is the active value.
"""
