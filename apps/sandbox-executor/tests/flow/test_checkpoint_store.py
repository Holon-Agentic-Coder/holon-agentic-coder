from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor.flow.checkpoint import (
    CURRENT_CHECKPOINT_SCHEMA_VERSION,
    FlowCheckpoint,
    load_checkpoint,
    save_checkpoint,
)
from sandbox_executor.flow.checkpoint_store import CheckpointStore, FileCheckpointStore
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self.storage: dict[str, dict[str, Any]] = {}

    def load(self, path: Path | str) -> dict[str, Any]:
        str_path = str(path)
        if str_path not in self.storage:
            raise FileNotFoundError(f"Key not found: {str_path}")
        return json.loads(json.dumps(self.storage[str_path]))

    def save(self, path: Path | str, data: dict[str, Any]) -> None:
        self.storage[str(path)] = json.loads(json.dumps(data))

    def default_path(self, repo_dir: Path | str, key: str) -> Path:
        return Path(repo_dir) / f"{key}.json"


def test_checkpoint_store_protocol_compliance() -> None:
    store = InMemoryCheckpointStore()
    assert isinstance(store, CheckpointStore)
    file_store = FileCheckpointStore()
    assert isinstance(file_store, CheckpointStore)


def test_file_checkpoint_store_atomic_save_and_load(tmp_path: Path) -> None:
    store = FileCheckpointStore()
    target_path = tmp_path / "nested" / "dir" / "checkpoint.json"

    data = {"hello": "world", "count": 42}
    store.save(target_path, data)

    assert target_path.is_file()
    loaded = store.load(target_path)
    assert loaded == data

    with pytest.raises(FileNotFoundError):
        store.load(tmp_path / "nonexistent.json")


def test_file_checkpoint_store_default_path(tmp_path: Path) -> None:
    store = FileCheckpointStore()
    derived = store.default_path(tmp_path, "test-intent")
    assert derived == tmp_path / ".holon" / "flow" / "checkpoint-test-intent.json"


def test_flow_checkpoint_persistence_with_store(tmp_path: Path) -> None:
    ctx = FlowContext(
        target_worktree=str(tmp_path),
        intent_data={"slug": "custom-store-test"},
    )
    ctx.stage_results["intent"] = StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)

    checkpoint = FlowCheckpoint(
        intent_slug="custom-store-test",
        current_stage=FlowStage.PLAN,
        completed_stages=["intent"],
        context=ctx,
    )

    store = InMemoryCheckpointStore()
    custom_path = Path("/in-memory/custom-store-test.json")

    saved_path = checkpoint.save(custom_path, store=store)
    assert saved_path == custom_path
    assert str(custom_path) in store.storage

    loaded_checkpoint = FlowCheckpoint.load(custom_path, store=store)
    assert loaded_checkpoint.intent_slug == "custom-store-test"
    assert loaded_checkpoint.current_stage == FlowStage.PLAN
    assert loaded_checkpoint.completed_stages == ["intent"]


def test_flow_checkpoint_default_path(tmp_path: Path) -> None:
    default_p = FlowCheckpoint.default_path(tmp_path, "sample-slug")
    assert default_p == tmp_path / ".holon" / "flow" / "checkpoint-sample-slug.json"


def test_flow_checkpoint_migrate_v0_to_v1() -> None:
    legacy_data = {
        "intent_slug": "legacy-flow",
        "current_stage": "intent",
        "completed_stages": [],
        "context": {"target_worktree": "/tmp"},
    }
    migrated = FlowCheckpoint.migrate(legacy_data)
    assert migrated["schema_version"] == CURRENT_CHECKPOINT_SCHEMA_VERSION
    assert migrated["intent_slug"] == "legacy-flow"
    assert migrated["context"]["schema_version"] == 1


def test_backward_compatible_save_and_load(tmp_path: Path) -> None:
    ctx = FlowContext(
        target_worktree=str(tmp_path),
        intent_data={"slug": "compat-test"},
    )
    ctx.stage_results["intent"] = StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)

    path_str = save_checkpoint(ctx)
    assert Path(path_str).is_file()

    loaded = load_checkpoint(path_str)
    assert loaded.intent_slug == "compat-test"
    assert loaded.current_stage == FlowStage.PLAN
    assert loaded.completed_stages == ["intent"]
