"""Classify and resolve the conflicts ``detect_stage_conflicts`` found, before a stage pushes.

Classification is pure and keeps the detection order; resolution walks that order, so the unresolved paths a stage
reports stay in the order git reported them. Conflicted ledgers are resolved by pure append and never staged.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from sandbox_executor.flow.ledger_reconcile import _resolve_conflicting_ledger

if TYPE_CHECKING:
    from sandbox_executor.flow.stage_conflicts import ConflictDetection


class ConflictKind(Enum):
    """How a conflicting path is handled: a code conflict fails the stage, a ledger is append-merged or deferred."""

    CODE = "code"
    LEDGER = "ledger"
    DEFERRED_LEDGER = "deferred_ledger"


def _is_ledger_path(path: str) -> bool:
    return "holon-knowledge/ledger" in path and path.endswith(".jsonl")


def _classify_conflict(path: str, detection: ConflictDetection) -> tuple[str, ConflictKind]:
    normalised = path.replace("\\", "/")
    if not _is_ledger_path(normalised):
        return path, ConflictKind.CODE
    if detection.probe_branch != "HEAD" and path in detection.probed_files:
        # The probed branch is not checked out, so the working-tree copy is not its ledger; append-only
        # ledgers reconcile at merge time and must not be rewritten in the operator's checkout here.
        return path, ConflictKind.DEFERRED_LEDGER
    return normalised, ConflictKind.LEDGER


def classify_conflicts(detection: ConflictDetection) -> list[tuple[str, ConflictKind]]:
    """Label each conflicting path, in detection order.

    A code conflict keeps its raw path; a ledger to resolve gets its ``/``-normalised path; a ledger probed on a branch
    other than ``HEAD`` is deferred.
    """
    return [_classify_conflict(path, detection) for path in detection.conflicting_files]


def resolve_classified_conflicts(
    repo_dir: str, classified: list[tuple[str, ConflictKind]]
) -> tuple[list[str], list[str]]:
    """Resolve the ledgers by pure append and return ``(resolved_ledgers, unresolved_paths)``, both in order.

    Code conflicts are unresolved; a ledger that cannot be resolved also counts as unresolved, so the caller fails
    closed on it; deferred ledgers are skipped.
    """
    resolved_ledgers: list[str] = []
    unresolved: list[str] = []
    for path, kind in classified:
        if kind is ConflictKind.CODE:
            unresolved.append(path)
        elif kind is ConflictKind.LEDGER:
            (resolved_ledgers if _resolve_conflicting_ledger(repo_dir, path) else unresolved).append(path)
    return resolved_ledgers, unresolved
