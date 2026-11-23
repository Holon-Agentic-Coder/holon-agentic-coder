"""The types of the shared target sync: where it runs, what it is asked, how code conflicts are resolved, what it did.

Kept apart from the routine (``flow.target_sync``) so the merge, record and resume helpers share them without an
import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from sandbox_executor.flow.target_probe import TargetProbe


class SyncBoundary(Enum):
    """The flow boundary a sync runs at; at ``TURN`` a clean target move is deferred to the next stage boundary."""

    FLOW_START = "flow_start"
    STAGE_START = "stage_start"
    STAGE_END = "stage_end"
    TURN = "turn"
    PUSH = "push"
    CALIBRATE = "calibrate"
    FLOW_END = "flow_end"


@dataclass(frozen=True)
class CodeResolution:
    """What a code resolver did with the ``CODE`` paths: resolved (and staged) them all, or not."""

    resolved: bool
    tier: str = ""
    attempts: int = 0
    test_result: str = ""


class CodeResolver(Protocol):
    """Resolves the ``CODE`` paths of an in-progress merge in ``worktree`` and stages the results."""

    def __call__(self, worktree: str, code_paths: list[str], probe: TargetProbe) -> CodeResolution:
        """Resolve ``code_paths``; return ``CodeResolution(resolved=True, ...)`` only when every path is staged."""
        ...


def fail_closed_code_resolver(worktree: str, code_paths: list[str], probe: TargetProbe) -> CodeResolution:
    """The default code resolver: resolves nothing, so a code conflict blocks the sync and the merge is aborted."""
    del worktree, code_paths, probe
    return CodeResolution(resolved=False, tier="none")


@dataclass(frozen=True)
class SyncRequest:
    """One call of ``sync_with_target``: the flow worktree and branch, the target, and where the call happens."""

    worktree: str
    branch: str
    target_branch: str
    stage: str
    boundary: SyncBoundary
    ledger_path: str
    execution_id: str
    last_probe: TargetProbe | None = None
    code_resolver: CodeResolver = fail_closed_code_resolver


@dataclass(frozen=True)
class SyncResult:
    """The outcome of one sync: ``unchanged``, ``deferred``, ``merged`` or ``blocked``.

    ``resolutions`` holds one ``(path, class, how)`` triple per conflicted path that was resolved; ``changed_paths``
    are the paths the merge changed (for the next turn's prompt); ``probe`` is for the caller to cache.
    """

    status: str
    head_before: str
    head_after: str
    target_sha: str
    probe: TargetProbe | None = None
    resolutions: tuple[tuple[str, str, str], ...] = ()
    code_paths: tuple[str, ...] = ()
    resolver_tier: str = ""
    attempts: int = 0
    test_result: str = ""
    changed_paths: tuple[str, ...] = ()
    ledger_row_id: str = ""
    error: str = ""
    conflicted_paths: tuple[str, ...] = field(default=())
