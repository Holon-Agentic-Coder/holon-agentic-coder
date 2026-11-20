"""Checkpoint store protocol and file-backed adapter."""

from __future__ import annotations

import json
import os
import tempfile
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from sandbox_executor.flow.checkpoint_discovery import _checkpoint_names, get_default_checkpoint_path

if TYPE_CHECKING:
    from sandbox_executor.flow.checkpoint import FlowCheckpoint


@runtime_checkable
class CheckpointStore(Protocol):
    """Protocol for checkpoint persistence backends."""

    def save(self, checkpoint: FlowCheckpoint, path: str | None = None) -> str:
        """Persist a checkpoint and return the saved path."""
        ...

    def load(self, path: str) -> dict[str, Any]:
        """Load raw checkpoint data dictionary from storage."""
        ...

    def default_path(self, target_worktree: str, slug: str) -> str:
        """Derive the canonical checkpoint path for a target worktree and slug."""
        ...


class FileCheckpointStore:
    """Atomic filesystem adapter for checkpoint storage."""

    def default_path(self, target_worktree: str, slug: str) -> str:
        """Derive the canonical checkpoint file path for an intent slug."""
        return get_default_checkpoint_path(target_worktree, slug)

    def save(self, checkpoint: FlowCheckpoint, path: str | None = None) -> str:
        """Atomically save checkpoint to disk using temporary file replacement."""
        if path is None:
            if checkpoint.context and checkpoint.context.intent_data:
                _, key = _checkpoint_names(checkpoint.context.intent_data)
            else:
                key = checkpoint.intent_slug or "default"
            target_worktree = checkpoint.context.target_worktree if checkpoint.context else "."
            path = self.default_path(target_worktree, key)

        target_dir = os.path.dirname(os.path.abspath(path))
        os.makedirs(target_dir, exist_ok=True)

        data = checkpoint.to_dict()
        with tempfile.NamedTemporaryFile("w", dir=target_dir, delete=False, encoding="utf-8") as tf:
            json.dump(data, tf, indent=2)
            temp_name = tf.name

        os.replace(temp_name, path)
        if checkpoint.context:
            checkpoint.context.log(f"Checkpoint saved to {path}")
        return path

    def load(self, path: str) -> dict[str, Any]:
        """Load raw checkpoint data dictionary from disk."""
        if not os.path.exists(path):
            raise FileNotFoundError(f"Checkpoint file '{path}' does not exist.")
        with open(path, encoding="utf-8") as f:
            return json.load(f)
