from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from sandbox_executor.flow.target_probe import TargetProbe

# why: these types live apart from flow.target_sync so the merge, record and resume helpers share them without a cycle


class SyncBoundary(Enum):
    FLOW_START = "flow_start"
    STAGE_START = "stage_start"
    STAGE_END = "stage_end"
    # why: at TURN a clean target move is deferred to the next stage boundary
    TURN = "turn"
    PUSH = "push"
    CALIBRATE = "calibrate"
    FLOW_END = "flow_end"


@dataclass(frozen=True)
class CodeResolution:
    resolved: bool
    tier: str = ""
    attempts: int = 0
    test_result: str = ""
    test_edits: tuple[str, ...] = ()


class CodeResolver(Protocol):
    def __call__(self, worktree: str, code_paths: list[str], probe: TargetProbe) -> CodeResolution: ...


def fail_closed_code_resolver(worktree: str, code_paths: list[str], probe: TargetProbe) -> CodeResolution:
    # why: resolving nothing makes a code conflict block the sync and abort the merge
    del worktree, code_paths, probe
    return CodeResolution(resolved=False, tier="none")


@dataclass(frozen=True)
class SyncRequest:
    worktree: str
    branch: str
    target_branch: str
    stage: str
    boundary: SyncBoundary
    ledger_path: str
    execution_id: str
    last_probe: TargetProbe | None = None
    code_resolver: CodeResolver = fail_closed_code_resolver
    reentry_round: int = 0


@dataclass(frozen=True)
class SyncResult:
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
    resolver_test_edits: tuple[str, ...] = ()
