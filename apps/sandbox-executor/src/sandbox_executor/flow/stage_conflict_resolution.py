from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from sandbox_executor.flow.conflict_classes import ConflictClass, _is_ledger_path, classify_path
from sandbox_executor.flow.conflict_classes_resolve import resolve_path
from sandbox_executor.flow.ledger_reconcile import _resolve_conflicting_ledger

if TYPE_CHECKING:
    from sandbox_executor.flow.stage_conflicts import ConflictDetection


# why: kept for the stage callers; the mechanical members mirror ConflictClass by value
class ConflictKind(Enum):
    CODE = ConflictClass.CODE.value
    LEDGER = ConflictClass.LEDGER.value
    KB_TIMESTAMP_ONLY = ConflictClass.KB_TIMESTAMP_ONLY.value
    DELETE_VS_TOUCH = ConflictClass.DELETE_VS_TOUCH.value
    APPENDED_LIST = ConflictClass.APPENDED_LIST.value
    DEFERRED_LEDGER = "deferred_ledger"


def _classify_conflict(path: str, detection: ConflictDetection, repo_dir: str) -> tuple[str, ConflictKind]:
    normalised = path.replace("\\", "/")
    if not _is_ledger_path(normalised):
        if not repo_dir or path in detection.probed_files:
            # why: a probed-only path is not unmerged in this checkout, so it has no index stages to classify
            return path, ConflictKind.CODE
        return path, ConflictKind(classify_path(repo_dir, path).value)
    if detection.probe_branch != "HEAD" and path in detection.probed_files:
        # why: the probed branch is not checked out, so the working-tree copy is not its ledger; append-only
        # why: ledgers reconcile at merge time and must not be rewritten in the operator's checkout here
        return path, ConflictKind.DEFERRED_LEDGER
    return normalised, ConflictKind.LEDGER


def classify_conflicts(detection: ConflictDetection, repo_dir: str = "") -> list[tuple[str, ConflictKind]]:
    return [_classify_conflict(path, detection, repo_dir) for path in detection.conflicting_files]


def _resolve_one(repo_dir: str, path: str, kind: ConflictKind) -> bool:
    if kind is ConflictKind.LEDGER:
        return _resolve_conflicting_ledger(repo_dir, path)
    return resolve_path(repo_dir, path, ConflictClass(kind.value))


def resolve_classified_conflicts(
    repo_dir: str, classified: list[tuple[str, ConflictKind]]
) -> tuple[list[str], list[str]]:
    resolved: list[str] = []
    unresolved: list[str] = []
    for path, kind in classified:
        if kind is ConflictKind.CODE:
            unresolved.append(path)
        elif kind is not ConflictKind.DEFERRED_LEDGER:
            # why: a mechanical path whose resolver fails counts as unresolved so the caller fails closed on it
            (resolved if _resolve_one(repo_dir, path, kind) else unresolved).append(path)
    return resolved, unresolved
