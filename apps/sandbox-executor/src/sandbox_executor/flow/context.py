from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sandbox_executor.agent_defaults import (
    DEFAULT_MODELS,
    FALLBACK_AGENT,
    LEGACY_DEFAULT_AGENT,
    LEGACY_DEFAULT_MODEL,
    normalise_agent_id,
)
from sandbox_executor.budgets import format_stage_summary
from sandbox_executor.flow.constants import CURRENT_CONTEXT_SCHEMA_VERSION
from sandbox_executor.flow.stage_status import (
    REVIEW_BOUNDARY_STATUSES,
    SUMMARY_STATUS_WORDS,
    StageStatus,
)
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.review_ensemble import DEFAULT_ENSEMBLE_SIZE

logger = logging.getLogger(__name__)

# why: kept for backward compatibility with tests and callers that read or patch _SUMMARY_STATUS_WORDS.
_SUMMARY_STATUS_WORDS = SUMMARY_STATUS_WORDS

__all__ = [
    "REVIEW_BOUNDARY_STATUSES",
    "SUMMARY_STATUS_WORDS",
    "_SUMMARY_STATUS_WORDS",
    "FlowContext",
    "FlowStage",
    "StageResult",
    "StageStatus",
    "_support_legacy_repo_dir",
]


class FlowStage(StrEnum):
    INTENT = "intent"
    PLAN = "plan"
    EXECUTE = "execute"
    REVIEW = "review"
    CALIBRATE = "calibrate"
    COMPLETED = "completed"

    @classmethod
    def from_str(cls, val: str | FlowStage) -> FlowStage:
        if isinstance(val, FlowStage):
            return val
        clean = str(val).strip().lower()
        for stage in cls:
            if stage.value == clean:
                return stage
        raise ValueError(f"Unknown FlowStage '{val}'. Valid stages: {[s.value for s in cls]}")


@dataclass
class StageResult:
    stage: FlowStage
    status: StageStatus = StageStatus.PENDING
    start_time: str | None = None
    end_time: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    elapsed_seconds: float | None = None
    budget_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "status": self.status.value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "payload": self.payload,
            "error": self.error,
            "elapsed_seconds": self.elapsed_seconds,
            "budget_seconds": self.budget_seconds,
        }

    def summary_line(self) -> str:
        word = _SUMMARY_STATUS_WORDS.get(self.status, self.status.value)
        if self.elapsed_seconds is None:
            return f"{self.stage.value}: {word}"
        return format_stage_summary(self.stage.value, word, self.elapsed_seconds, self.budget_seconds)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StageResult:
        return cls(
            stage=FlowStage.from_str(data["stage"]),
            status=StageStatus.from_str(data.get("status", "pending")),
            start_time=data.get("start_time"),
            end_time=data.get("end_time"),
            payload=data.get("payload", {}),
            error=data.get("error"),
            elapsed_seconds=data.get("elapsed_seconds"),
            budget_seconds=data.get("budget_seconds"),
        )


def _support_legacy_repo_dir(cls: type) -> type:
    orig_init = cls.__init__

    def __init__(self: Any, *args: Any, repo_dir: str | None = None, **kwargs: Any) -> None:
        if repo_dir is not None and "target_worktree" not in kwargs and not args:
            kwargs["target_worktree"] = repo_dir
        orig_init(self, *args, **kwargs)

    cls.__init__ = __init__
    return cls


@_support_legacy_repo_dir
@dataclass
class FlowContext:
    target_worktree: str = "."
    intent_data: dict[str, Any] = field(default_factory=dict)
    intent_branch: str | None = None
    plan_branch: str | None = None
    execution_branch: str | None = None
    calibrated_branch: str | None = None
    # why: Stage 4 or 5 moves HEAD, so the branch or detached SHA it was on is recorded to return to.
    starting_branch: str | None = None
    starting_detached: bool = False
    # why: the CLI always passes resolved values (see agent_defaults); these only apply to direct construction.
    agent: str = FALLBACK_AGENT
    model: str = DEFAULT_MODELS[normalise_agent_id(FALLBACK_AGENT)]
    dry_run: bool = False
    auto_calibrate: bool = True
    # why: only an explicit flag spawns this run's sidecar.
    token_reduce: bool | None = None
    # why: attaching to a running user-owned proxy never spawns one.
    proxy_attach: bool = False
    skip_push: bool = False
    skip_calibrate: bool = False
    max_review_iterations: int = 25
    review_ensemble_size: int = DEFAULT_ENSEMBLE_SIZE
    mitm_web: bool = False
    allow_overlap: bool = False
    admission: dict[str, Any] | None = None
    stage_results: dict[str, StageResult] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)
    schema_version: int = CURRENT_CONTEXT_SCHEMA_VERSION
    # why: bounded by budgets.MAX_REPLAN_ATTEMPTS so an over-budget plan cannot replan forever.
    replan_attempts: int = 0
    # why: checkpointed so a resume continues the engine-owned Stage 4 pass count.
    review_iterations_done: int = 0
    # why: a halted pass's undisposed findings go to the resolver first on resume.
    carried_review_findings: list[dict[str, Any]] = field(default_factory=list)
    # why: an agent-resolved target merge is re-reviewed only on these paths; empty means the full PR diff.
    review_scope_paths: list[str] = field(default_factory=list)
    review_state: dict[str, Any] = field(default_factory=dict)
    # why: never checkpointed; False once merged-head CI restarts of Stage 4 hit their cap.
    merged_head_restart_allowed: bool = field(default=True, repr=False, compare=False)
    # why: never checkpointed; None means launcher.role_dispatch.dispatch_role_agent.
    dispatcher: Callable[..., int] | None = field(default=None, repr=False, compare=False)
    # why: never checkpointed.
    checkpoint_path: str | None = field(default=None, repr=False)

    @property
    def repo_dir(self) -> str:
        return self.target_worktree

    @repo_dir.setter
    def repo_dir(self, value: str) -> None:
        self.target_worktree = value

    def __post_init__(self) -> None:
        if self.token_reduce is None:
            from sandbox_executor.launcher import coherence

            self.token_reduce, self.proxy_attach = coherence.resolve_flow_proxy_choice()

    def log(self, message: str) -> None:
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        sanitized = redact_agent_secrets(str(message))
        formatted = f"[{timestamp}] {sanitized}"
        self.logs.append(formatted)
        logger.info(formatted)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "target_worktree": self.target_worktree,
            "intent_data": self.intent_data,
            "intent_branch": self.intent_branch,
            "plan_branch": self.plan_branch,
            "execution_branch": self.execution_branch,
            "calibrated_branch": self.calibrated_branch,
            "agent": self.agent,
            "model": self.model,
            "dry_run": self.dry_run,
            "auto_calibrate": self.auto_calibrate,
            "token_reduce": self.token_reduce,
            "proxy_attach": self.proxy_attach,
            "skip_push": self.skip_push,
            "skip_calibrate": self.skip_calibrate,
            "max_review_iterations": self.max_review_iterations,
            "review_ensemble_size": self.review_ensemble_size,
            "mitm_web": self.mitm_web,
            "allow_overlap": self.allow_overlap,
            "admission": self.admission,
            "replan_attempts": self.replan_attempts,
            "review_iterations_done": self.review_iterations_done,
            "carried_review_findings": self.carried_review_findings,
            "review_scope_paths": self.review_scope_paths,
            "review_state": self.review_state,
            "stage_results": {k: v.to_dict() for k, v in self.stage_results.items()},
            "logs": self.logs,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FlowContext:
        raw_results = data.get("stage_results", {})
        parsed_results = {k: StageResult.from_dict(v) if isinstance(v, dict) else v for k, v in raw_results.items()}
        raw_version = data.get("schema_version")
        schema_version = CURRENT_CONTEXT_SCHEMA_VERSION if raw_version is None or raw_version == 0 else int(raw_version)
        if schema_version > CURRENT_CONTEXT_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported context schema_version {schema_version}. "
                f"Supported version is {CURRENT_CONTEXT_SCHEMA_VERSION}."
            )
        target_worktree = data.get("target_worktree") or data.get("repo_dir", ".")
        return cls(
            schema_version=schema_version,
            target_worktree=target_worktree,
            intent_data=data.get("intent_data", {}),
            intent_branch=data.get("intent_branch"),
            plan_branch=data.get("plan_branch"),
            execution_branch=data.get("execution_branch"),
            calibrated_branch=data.get("calibrated_branch"),
            agent=data.get("agent", LEGACY_DEFAULT_AGENT),
            model=data.get("model", LEGACY_DEFAULT_MODEL),
            dry_run=data.get("dry_run", False),
            auto_calibrate=data.get("auto_calibrate", True),
            token_reduce=data.get("token_reduce", False),
            proxy_attach=bool(data.get("proxy_attach", False)),
            skip_push=data.get("skip_push", False),
            skip_calibrate=data.get("skip_calibrate", False),
            max_review_iterations=data.get("max_review_iterations", 25),
            review_ensemble_size=int(data.get("review_ensemble_size") or DEFAULT_ENSEMBLE_SIZE),
            mitm_web=data.get("mitm_web", False),
            allow_overlap=bool(data.get("allow_overlap", False)),
            admission=data.get("admission"),
            replan_attempts=int(data.get("replan_attempts") or 0),
            review_iterations_done=int(data.get("review_iterations_done") or 0),
            carried_review_findings=list(data.get("carried_review_findings") or []),
            review_scope_paths=list(data.get("review_scope_paths") or []),
            review_state=dict(data.get("review_state") or {}),
            stage_results=parsed_results,
            logs=data.get("logs", []),
        )
