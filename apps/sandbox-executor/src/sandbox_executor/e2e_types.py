"""Shared data types, exceptions, and constants for the ``holon flow`` E2E harness."""

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
    """Classification of an E2E attempt failure into retryable model output vs non-retryable machinery."""

    MODEL_OUTPUT = "model_output"
    MACHINERY = "machinery"


class E2EError(RuntimeError):
    """Base exception for E2E flow harness failures."""


class E2EModelOutputError(E2EError):
    """Retryable failure caused by model output or generated code assertions failing."""


class E2EMachineryError(E2EError):
    """Non-retryable failure in flow machinery, configuration, git topology, or safety guards."""


class PrefixScopingError(E2EMachineryError):
    """Raised when an intent prefix is malformed or a candidate ref lies outside the intent prefix."""


class ZeroPRGuardViolationError(E2EMachineryError):
    """Raised when a stray pull request is detected for the disposable E2E intent or ``--skip-review`` is passed."""


@dataclass(frozen=True)
class TeardownReport:
    """Enumeration of every worktree and local/remote branch deleted during an E2E teardown."""

    intent_prefix: str
    removed_worktrees: tuple[str, ...] = ()
    deleted_local_branches: tuple[str, ...] = ()
    deleted_remote_branches: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def summary_line(self) -> str:
        """Render a concise one-line summary enumerating all deleted worktrees and branches."""
        wt = ", ".join(self.removed_worktrees) or "none"
        loc = ", ".join(self.deleted_local_branches) or "none"
        rem = ", ".join(self.deleted_remote_branches) or "none"
        return f"Teardown[{self.intent_prefix}]: worktrees=[{wt}] local_branches=[{loc}] remote_branches=[{rem}]"


@dataclass(frozen=True)
class ZeroPRCheckResult:
    """Outcome of verifying that zero pull requests exist for the disposable E2E intent prefix."""

    intent_prefix: str
    passed: bool
    matching_prs: tuple[int, ...] = ()
    closed_prs: tuple[int, ...] = ()


@dataclass(frozen=True)
class E2EAttemptOutcome:
    """Outcome of a single E2E flow attempt including its teardown and zero-PR check records."""

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
    """Aggregated outcome of an E2E flow test run across up to ``max_attempts`` attempts."""

    succeeded: bool
    attempts_run: int
    intent_prefix: str
    stage_results: dict[str, Any] = field(default_factory=dict)
    teardown_reports: tuple[TeardownReport, ...] = ()
    zero_pr_results: tuple[ZeroPRCheckResult, ...] = ()
    attempts: tuple[E2EAttemptOutcome, ...] = ()
    failure_category: FailureCategory | None = None
    error_message: str | None = None
