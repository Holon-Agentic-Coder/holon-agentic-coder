from __future__ import annotations

from enum import StrEnum


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    HALTED_FOR_HUMAN = "halted_for_human"
    APPROVED = "approved"
    SKIPPED = "skipped"

    @classmethod
    def from_str(cls, val: str | StageStatus) -> StageStatus:
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
# why: HALTED_FOR_HUMAN is the legacy boundary value still found in older checkpoints and ledger rows
REVIEW_BOUNDARY_STATUSES: frozenset[StageStatus] = frozenset({StageStatus.APPROVED, StageStatus.HALTED_FOR_HUMAN})
