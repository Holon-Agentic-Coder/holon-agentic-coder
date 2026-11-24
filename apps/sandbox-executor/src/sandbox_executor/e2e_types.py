from __future__ import annotations

import enum
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

DEFAULT_E2E_MAX_ATTEMPTS = 3
MAX_E2E_ATTEMPTS_CAP = 5

GhRunnerCallable = Callable[..., subprocess.CompletedProcess[str]]


class FailureCategory(enum.StrEnum):
    MODEL_OUTPUT = "model_output"
    MACHINERY = "machinery"


class E2EError(RuntimeError):
    pass


class E2EModelOutputError(E2EError):
    pass


class E2EMachineryError(E2EError):
    pass


class PrefixScopingError(E2EMachineryError):
    pass


class ZeroPRGuardViolationError(E2EMachineryError):
    pass


@dataclass(frozen=True)
class TeardownReport:
    intent_prefix: str
    removed_worktrees: tuple[str, ...] = ()
    deleted_local_branches: tuple[str, ...] = ()
    deleted_remote_branches: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def summary_line(self) -> str:
        worktrees = ", ".join(self.removed_worktrees) or "none"
        local_branches = ", ".join(self.deleted_local_branches) or "none"
        remote_branches = ", ".join(self.deleted_remote_branches) or "none"
        return (
            f"Teardown[{self.intent_prefix}]: worktrees=[{worktrees}] "
            f"local_branches=[{local_branches}] remote_branches=[{remote_branches}]"
        )


@dataclass(frozen=True)
class ZeroPRCheckResult:
    intent_prefix: str
    passed: bool
    matching_prs: tuple[int, ...] = ()
    closed_prs: tuple[int, ...] = ()


@dataclass(frozen=True)
class E2EAttemptOutcome:
    attempt: int
    intent_prefix: str
    succeeded: bool
    stage_results: dict[str, Any] = field(default_factory=dict)
    teardown_report: TeardownReport | None = None
    zero_pr_result: ZeroPRCheckResult | None = None
    failure_category: FailureCategory | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class E2ERunOutcome:
    succeeded: bool
    attempts_run: int
    intent_prefix: str
    stage_results: dict[str, Any] = field(default_factory=dict)
    teardown_reports: tuple[TeardownReport, ...] = ()
    zero_pr_results: tuple[ZeroPRCheckResult, ...] = ()
    attempts: tuple[E2EAttemptOutcome, ...] = ()
    failure_category: FailureCategory | None = None
    error_message: str | None = None
