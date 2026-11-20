"""Hermetic unit tests for CheckpointStore protocol and FileCheckpointStore adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.flow.checkpoint import FlowCheckpoint, load_checkpoint, save_checkpoint
from sandbox_executor.flow.checkpoint_store import CheckpointStore, FileCheckpointStore
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus


class InMemoryCheckpointStore:
    """In-memory mock store implementing CheckpointStore protocol."""

    def __init__(self) -> None:
        self.storage: dict[str, dict[str, Any]] = {}

    def save(self, checkpoint: FlowCheckpoint, path: str | None = None) -> str:
        resolved_path = path or f"memory://{checkpoint.intent_slug}.json"
        self.storage[resolved_path] = checkpoint.to_dict()
        return resolved_path

    def load(self, path: str) -> dict[str, Any]:
        if path not in self.storage:
            raise FileNotFoundError(f"Key '{path}' not found in memory store.")
        return dict(self.storage[path])

    def default_path(self, target_worktree: str, slug: str) -> str:
        return f"{target_worktree}/memory/{slug}.json"


def test_checkpoint_store_protocol_conformance():
    """Verify both custom and builtin stores conform to CheckpointStore protocol."""
    in_memory = InMemoryCheckpointStore()
    file_store = FileCheckpointStore()
    assert isinstance(in_memory, CheckpointStore)
    assert isinstance(file_store, CheckpointStore)


def test_flow_checkpoint_save_and_load_with_custom_store():
    """FlowCheckpoint save and load delegate directly to the custom store."""
    store = InMemoryCheckpointStore()
    ctx = FlowContext(intent_data={"slug": "custom-store-intent"})
    cp = FlowCheckpoint(
        intent_slug="custom-store-intent",
        current_stage=FlowStage.PLAN,
        completed_stages=["intent"],
        context=ctx,
    )

    saved_path = cp.save(store=store)
    assert saved_path == "memory://custom-store-intent.json"
    assert saved_path in store.storage

    loaded_cp = FlowCheckpoint.load(saved_path, store=store)
    assert loaded_cp.intent_slug == "custom-store-intent"
    assert loaded_cp.current_stage == FlowStage.PLAN
    assert loaded_cp.completed_stages == ["intent"]


def test_file_checkpoint_store_roundtrip(tmp_path: Path):
    """FileCheckpointStore correctly saves and loads checkpoint on disk."""
    store = FileCheckpointStore()
    ctx = FlowContext(
        target_worktree=str(tmp_path),
        intent_data={"slug": "file-store-test"},
    )
    cp = FlowCheckpoint(
        intent_slug="file-store-test",
        current_stage=FlowStage.EXECUTE,
        completed_stages=["intent", "plan"],
        context=ctx,
    )

    custom_path = str(tmp_path / "custom_checkpoint.json")
    saved_path = store.save(cp, path=custom_path)
    assert saved_path == custom_path
    assert Path(custom_path).is_file()

    raw_data = store.load(custom_path)
    assert raw_data["intent_slug"] == "file-store-test"
    assert raw_data["current_stage"] == "execute"

    loaded_cp = FlowCheckpoint.load(custom_path, store=store)
    assert loaded_cp.intent_slug == "file-store-test"
    assert loaded_cp.current_stage == FlowStage.EXECUTE


def test_file_checkpoint_store_default_path(tmp_path: Path):
    """FileCheckpointStore uses default path when path is None."""
    store = FileCheckpointStore()
    ctx = FlowContext(
        target_worktree=str(tmp_path),
        intent_data={"slug": "default-path-slug"},
    )
    cp = FlowCheckpoint(
        intent_slug="default-path-slug",
        current_stage=FlowStage.INTENT,
        context=ctx,
    )

    saved_path = store.save(cp)
    expected_path = store.default_path(str(tmp_path), "default-path-slug")
    assert saved_path == expected_path
    assert Path(saved_path).is_file()


def test_file_checkpoint_store_missing_file_raises():
    """FileCheckpointStore.load raises FileNotFoundError for missing path."""
    store = FileCheckpointStore()
    with pytest.raises(FileNotFoundError, match="does not exist"):
        store.load("/nonexistent/checkpoint.json")


def test_flow_checkpoint_classmethod_default_path():
    """FlowCheckpoint.default_path delegates to get_default_checkpoint_path."""
    path = FlowCheckpoint.default_path("/tmp/repo", "my-slug")
    assert path.endswith(".holon/flow/checkpoint-my-slug.json")


def test_flow_checkpoint_migrate():
    """FlowCheckpoint.migrate returns a new migrated checkpoint instance."""
    legacy_dict = {
        "schema_version": 0,
        "intent_slug": "old-slug",
        "current_stage": "intent",
        "completed_stages": [],
        "context": {},
    }
    cp = FlowCheckpoint.from_dict(legacy_dict)
    assert cp.schema_version == 1

    migrated = cp.migrate(target_version=1)
    assert migrated.schema_version == 1
    assert migrated.intent_slug == "old-slug"


def test_save_and_load_checkpoint_backward_compatibility(tmp_path: Path):
    """save_checkpoint and load_checkpoint preserve backward compatibility with optional store."""
    ctx = FlowContext(
        target_worktree=str(tmp_path),
        intent_data={"slug": "compat-test"},
    )
    ctx.stage_results["intent"] = StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)

    # Test default file store
    path = save_checkpoint(ctx)
    loaded = load_checkpoint(path)
    assert loaded.intent_slug == "compat-test"
    assert loaded.current_stage == FlowStage.PLAN
    assert loaded.completed_stages == ["intent"]

    # Test custom store via module functions
    in_memory = InMemoryCheckpointStore()
    mem_path = save_checkpoint(ctx, filepath="memory://compat.json", store=in_memory)
    mem_loaded = load_checkpoint(mem_path, store=in_memory)
    assert mem_loaded.intent_slug == "compat-test"
