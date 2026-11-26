"""Classify and resolve the conflicts ``detect_stage_conflicts`` found, before a stage pushes.

Classification keeps the detection order; resolution walks that order, so the unresolved paths a stage reports stay in
the order git reported them. Conflicted ledgers are resolved by inserting incoming rows at their ``created_at`` slot
with the kept rows unmoved, and never staged. Every other unmerged path is classified by
:mod:`sandbox_executor.flow.conflict_classes` (the one classifier of the codebase), so a container stage resolves the
same mechanical classes as the host target sync without an agent.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from sandbox_executor.flow.conflict_classes import ConflictClass, _is_ledger_path, classify_path
from sandbox_executor.flow.conflict_classes_resolve import resolve_path
from sandbox_executor.flow.ledger_reconcile import _resolve_conflicting_ledger

if TYPE_CHECKING:
    from sandbox_executor.flow.stage_conflicts import ConflictDetection


class ConflictKind(Enum):
    """How a conflicting path is handled: the ``ConflictClass`` values, plus a ledger deferred to merge time.

    Kept for the stage callers; the mechanical members mirror :class:`ConflictClass` by value.
    """

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
            # A probed-only path is not unmerged in this checkout, so it has no index stages to classify.
            return path, ConflictKind.CODE
        return path, ConflictKind(classify_path(repo_dir, path).value)
    if detection.probe_branch != "HEAD" and path in detection.probed_files:
        # The probed branch is not checked out, so the working-tree copy is not its ledger; append-only
        # ledgers reconcile at merge time and must not be rewritten in the operator's checkout here.
        return path, ConflictKind.DEFERRED_LEDGER
    return normalised, ConflictKind.LEDGER


def classify_conflicts(detection: ConflictDetection, repo_dir: str = "") -> list[tuple[str, ConflictKind]]:
    """Label each conflicting path, in detection order.

    A ledger to resolve gets its ``/``-normalised path; a ledger probed on a branch other than ``HEAD`` is deferred.
    With ``repo_dir``, an unmerged non-ledger path gets its ``conflict_classes`` class; otherwise it is ``CODE``.
    """
    return [_classify_conflict(path, detection, repo_dir) for path in detection.conflicting_files]


def _resolve_one(repo_dir: str, path: str, kind: ConflictKind) -> bool:
    if kind is ConflictKind.LEDGER:
        return _resolve_conflicting_ledger(repo_dir, path)
    return resolve_path(repo_dir, path, ConflictClass(kind.value))


def resolve_classified_conflicts(
    repo_dir: str, classified: list[tuple[str, ConflictKind]]
) -> tuple[list[str], list[str]]:
    """Resolve every mechanical path and return ``(resolved_paths, unresolved_paths)``, both in order.

    Code conflicts are unresolved; a mechanical path whose resolver fails also counts as unresolved, so the caller
    fails closed on it; deferred ledgers are skipped.
    """
    resolved: list[str] = []
    unresolved: list[str] = []
    for path, kind in classified:
        if kind is ConflictKind.CODE:
            unresolved.append(path)
        elif kind is not ConflictKind.DEFERRED_LEDGER:
            (resolved if _resolve_one(repo_dir, path, kind) else unresolved).append(path)
    return resolved, unresolved
