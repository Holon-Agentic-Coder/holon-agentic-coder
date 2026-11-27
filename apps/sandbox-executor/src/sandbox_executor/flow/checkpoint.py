from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sandbox_executor.agent_defaults import LEGACY_DEFAULT_AGENT, LEGACY_DEFAULT_MODEL
from sandbox_executor.flow.checkpoint_discovery import _checkpoint_names
from sandbox_executor.flow.checkpoint_store import CheckpointStore, FileCheckpointStore
from sandbox_executor.flow.constants import CURRENT_CONTEXT_SCHEMA_VERSION
from sandbox_executor.flow.context import FlowContext, FlowStage, StageStatus
from sandbox_executor.flow.stage_status import REVIEW_BOUNDARY_STATUSES

CURRENT_CHECKPOINT_SCHEMA_VERSION: int = 1


MigrationTransform = Callable[[dict[str, Any]], dict[str, Any]]


CHECKPOINT_MIGRATION_REGISTRY: dict[tuple[int, int], MigrationTransform] = {}


def _migrate_v0_to_v1(data: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(data)
    migrated["schema_version"] = 1
    migrated["intent_slug"] = str(migrated.get("intent_slug") or "unknown")
    migrated["current_stage"] = str(migrated.get("current_stage") or "intent")
    migrated["completed_stages"] = list(migrated.get("completed_stages") or [])
    migrated["timestamp"] = str(migrated.get("timestamp") or "")

    raw_context = migrated.get("context")
    context = dict(raw_context) if isinstance(raw_context, dict) else {}
    context.setdefault("schema_version", CURRENT_CONTEXT_SCHEMA_VERSION)
    context.setdefault("target_worktree", ".")
    context.setdefault("intent_data", {})
    context.setdefault("intent_branch", None)
    context.setdefault("plan_branch", None)
    context.setdefault("execution_branch", None)
    context.setdefault("calibrated_branch", None)
    # why: a checkpoint without attribution was produced under the legacy default and must resume with it
    context.setdefault("agent", LEGACY_DEFAULT_AGENT)
    context.setdefault("model", LEGACY_DEFAULT_MODEL)
    context.setdefault("dry_run", False)
    context.setdefault("auto_calibrate", True)
    context.setdefault("token_reduce", False)
    context.setdefault("skip_push", False)
    context.setdefault("skip_calibrate", False)
    context.setdefault("max_review_iterations", 25)
    context.setdefault("mitm_web", False)
    context.setdefault("stage_results", {})
    context.setdefault("logs", [])
    migrated["context"] = context
    return migrated


CHECKPOINT_MIGRATION_REGISTRY[(0, 1)] = _migrate_v0_to_v1


def migrate_checkpoint_data(
    data: dict[str, Any],
    target_version: int = CURRENT_CHECKPOINT_SCHEMA_VERSION,
) -> dict[str, Any]:
    current_version = int(data.get("schema_version") or 0)
    if current_version > target_version:
        raise ValueError(
            f"Unsupported checkpoint schema_version {current_version}. Supported version is {target_version}."
        )

    current_data = dict(data)
    v = current_version
    while v < target_version:
        step = (v, v + 1)
        if step not in CHECKPOINT_MIGRATION_REGISTRY:
            raise ValueError(f"No migration registered for transition {step}")
        transform = CHECKPOINT_MIGRATION_REGISTRY[step]
        current_data = transform(current_data)
        next_v = int(current_data.get("schema_version") or (v + 1))
        if next_v <= v:
            raise ValueError(f"Migration step {step} failed to advance schema version from {v}")
        v = next_v

    return current_data


@dataclass
class FlowCheckpoint:
    intent_slug: str
    current_stage: FlowStage
    completed_stages: list[str] = field(default_factory=list)
    context: FlowContext = field(default_factory=FlowContext)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")
    schema_version: int = CURRENT_CHECKPOINT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        data = {
            "schema_version": self.schema_version,
            "intent_slug": self.intent_slug,
            "current_stage": self.current_stage.value,
            "completed_stages": self.completed_stages,
            "context": self.context.to_dict(),
            "timestamp": self.timestamp,
        }
        if self.context.admission is not None:
            data["admission"] = self.context.admission
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FlowCheckpoint:
        raw_version = data.get("schema_version")
        schema_version = int(raw_version or 0)
        if schema_version > CURRENT_CHECKPOINT_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported checkpoint schema_version {raw_version}. "
                f"Supported version is {CURRENT_CHECKPOINT_SCHEMA_VERSION}."
            )
        if schema_version < CURRENT_CHECKPOINT_SCHEMA_VERSION:
            data = cls.migrate(data, target_version=CURRENT_CHECKPOINT_SCHEMA_VERSION)

        context = FlowContext.from_dict(data.get("context", {}))
        if context.admission is None:
            # why: admission is optional and additive, because older checkpoints have neither
            context.admission = data.get("admission")
        return cls(
            schema_version=data.get("schema_version", CURRENT_CHECKPOINT_SCHEMA_VERSION),
            intent_slug=data.get("intent_slug", "unknown"),
            current_stage=FlowStage.from_str(data.get("current_stage", "intent")),
            completed_stages=data.get("completed_stages", []),
            context=context,
            timestamp=data.get("timestamp", ""),
        )

    def save(self, path: Path | str | None = None, store: CheckpointStore | None = None) -> Path:
        s = store if store is not None else FileCheckpointStore()
        if path is None:
            _, key = _checkpoint_names(self.context.intent_data)
            target = s.default_path(self.context.target_worktree, key)
        else:
            target = Path(path)
        s.save(target, self.to_dict())
        self.context.log(f"Checkpoint saved to {target}")
        return target

    @classmethod
    def load(cls, path: Path | str, store: CheckpointStore | None = None) -> FlowCheckpoint:
        s = store if store is not None else FileCheckpointStore()
        data = s.load(path)
        return cls.from_dict(data)

    @classmethod
    def default_path(cls, repo_dir: Path | str, key: str, store: CheckpointStore | None = None) -> Path:
        s = store if store is not None else FileCheckpointStore()
        return s.default_path(repo_dir, key)

    @staticmethod
    def migrate(data: dict[str, Any], target_version: int = CURRENT_CHECKPOINT_SCHEMA_VERSION) -> dict[str, Any]:
        return migrate_checkpoint_data(data, target_version)


def save_checkpoint(context: FlowContext, filepath: str | None = None) -> str:
    slug, _ = _checkpoint_names(context.intent_data)
    completed = [
        stage_name
        for stage_name, res in context.stage_results.items()
        if res.status == StageStatus.SUCCESS or res.status in REVIEW_BOUNDARY_STATUSES
    ]
    current = FlowStage.COMPLETED if len(completed) >= 5 else FlowStage.INTENT
    for candidate in [FlowStage.INTENT, FlowStage.PLAN, FlowStage.EXECUTE, FlowStage.REVIEW, FlowStage.CALIBRATE]:
        if candidate.value not in completed:
            current = candidate
            break

    checkpoint = FlowCheckpoint(
        intent_slug=slug,
        current_stage=current,
        completed_stages=completed,
        context=context,
    )
    saved = checkpoint.save(filepath or None)
    return str(saved)


def save_admission(context: FlowContext, admission: dict[str, Any] | None) -> str | None:
    # why: a failed save keeps the previous admission, so the in-memory context never claims what disk lacks
    previous, context.admission = context.admission, admission
    try:
        save_checkpoint(context, context.checkpoint_path)
    except OSError as exc:
        context.admission = previous
        return str(exc)
    return None


def load_checkpoint(filepath: str) -> FlowCheckpoint:
    return FlowCheckpoint.load(filepath)
