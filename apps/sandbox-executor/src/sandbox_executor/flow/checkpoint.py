"""Flow checkpoints: schema migration, the on-disk checkpoint record and its save and load helpers.

A checkpoint lets ``holon flow --resume`` continue from the last completed stage.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.agent_defaults import LEGACY_DEFAULT_AGENT, LEGACY_DEFAULT_MODEL
from sandbox_executor.flow.constants import CURRENT_CONTEXT_SCHEMA_VERSION
from sandbox_executor.flow.context import FlowContext, FlowStage, StageStatus

CURRENT_CHECKPOINT_SCHEMA_VERSION: int = 1


MigrationTransform = Callable[[dict[str, Any]], dict[str, Any]]


CHECKPOINT_MIGRATION_REGISTRY: dict[tuple[int, int], MigrationTransform] = {}


def _migrate_v0_to_v1(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate legacy v0 checkpoint dictionary to v1 schema."""
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
    # A checkpoint without attribution was produced under the legacy default and must resume with it.
    context.setdefault("agent", LEGACY_DEFAULT_AGENT)
    context.setdefault("model", LEGACY_DEFAULT_MODEL)
    context.setdefault("dry_run", False)
    context.setdefault("auto_calibrate", True)
    context.setdefault("token_reduce", False)
    context.setdefault("skip_push", False)
    context.setdefault("skip_review", False)
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
    """Migrate checkpoint data dictionary to target schema version using registered transforms.

    Args:
        data: Raw checkpoint dictionary.
        target_version: Desired target schema version (default: CURRENT_CHECKPOINT_SCHEMA_VERSION).

    Returns:
        Migrated checkpoint dictionary matching target schema version.

    Raises:
        ValueError: If current schema version exceeds target version, or if an intermediate migration
            pathway is missing in CHECKPOINT_MIGRATION_REGISTRY.
    """
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
    """Represents a persisted snapshot of the pipeline state for resumption."""

    intent_slug: str
    current_stage: FlowStage
    completed_stages: list[str] = field(default_factory=list)
    context: FlowContext = field(default_factory=FlowContext)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")
    schema_version: int = CURRENT_CHECKPOINT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Convert FlowCheckpoint to dictionary."""
        return {
            "schema_version": self.schema_version,
            "intent_slug": self.intent_slug,
            "current_stage": self.current_stage.value,
            "completed_stages": self.completed_stages,
            "context": self.context.to_dict(),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FlowCheckpoint:
        """Construct FlowCheckpoint from dictionary."""
        raw_version = data.get("schema_version")
        schema_version = int(raw_version or 0)
        if schema_version > CURRENT_CHECKPOINT_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported checkpoint schema_version {raw_version}. "
                f"Supported version is {CURRENT_CHECKPOINT_SCHEMA_VERSION}."
            )
        if schema_version < CURRENT_CHECKPOINT_SCHEMA_VERSION:
            data = migrate_checkpoint_data(data, target_version=CURRENT_CHECKPOINT_SCHEMA_VERSION)

        return cls(
            schema_version=data.get("schema_version", CURRENT_CHECKPOINT_SCHEMA_VERSION),
            intent_slug=data.get("intent_slug", "unknown"),
            current_stage=FlowStage.from_str(data.get("current_stage", "intent")),
            completed_stages=data.get("completed_stages", []),
            context=FlowContext.from_dict(data.get("context", {})),
            timestamp=data.get("timestamp", ""),
        )


def get_default_checkpoint_path(target_worktree: str, slug: str) -> str:
    """Derive the canonical checkpoint file path for an intent slug."""
    clean_slug = re.sub(r"[^a-zA-Z0-9_-]", "-", slug).strip("-") or "default"
    return os.path.join(target_worktree, ".holon", "flow", f"checkpoint-{clean_slug}.json")


def save_checkpoint(context: FlowContext, filepath: str | None = None) -> str:
    """Atomically save flow context checkpoint to disk using temporary file replacement."""
    slug = context.intent_data.get("slug")
    if not slug and context.intent_data.get("branch"):
        slug = context.intent_data["branch"].replace("I-", "").split("-", 2)[-1]
    if not slug:
        slug = "default"
    # A sub-intent's slug can repeat its parent's or a sibling's, so its checkpoint is keyed on its unique branch.
    key = (
        str(context.intent_data["branch"]).removesuffix("/_")
        if context.intent_data.get("parent_intent_id") and context.intent_data.get("branch")
        else slug
    )
    target_path = filepath or get_default_checkpoint_path(context.target_worktree, key)
    target_dir = os.path.dirname(os.path.abspath(target_path))
    os.makedirs(target_dir, exist_ok=True)

    completed = [
        stage_name
        for stage_name, res in context.stage_results.items()
        if res.status in (StageStatus.SUCCESS, StageStatus.HALTED_FOR_HUMAN)
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

    data = checkpoint.to_dict()
    # Atomic write pattern: write to NamedTemporaryFile in same dir, then replace
    with tempfile.NamedTemporaryFile("w", dir=target_dir, delete=False, encoding="utf-8") as tf:
        json.dump(data, tf, indent=2)
        temp_name = tf.name

    os.replace(temp_name, target_path)
    context.log(f"Checkpoint saved to {target_path}")
    return target_path


def load_checkpoint(filepath: str) -> FlowCheckpoint:
    """Load a flow checkpoint from disk."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Checkpoint file '{filepath}' does not exist.")
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    return FlowCheckpoint.from_dict(data)
