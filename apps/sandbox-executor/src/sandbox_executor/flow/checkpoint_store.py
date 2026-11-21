"""CheckpointStore protocol and FileCheckpointStore adapter for FlowCheckpoint persistence."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from sandbox_executor.flow.checkpoint_discovery import get_default_checkpoint_path


@runtime_checkable
class CheckpointStore(Protocol):
    """Protocol defining persistence and path derivation for flow checkpoints."""

    def load(self, path: Path | str) -> dict[str, Any]:
        """Load raw checkpoint data dictionary from path."""
        ...

    def save(self, path: Path | str, data: dict[str, Any]) -> None:
        """Persist checkpoint data dictionary to path."""
        ...

    def default_path(self, repo_dir: Path | str, key: str) -> Path:
        """Derive the canonical checkpoint file path for a checkpoint key (sub-intent branch or intent slug)."""
        ...


class FileCheckpointStore:
    """File-system backed implementation of CheckpointStore with atomic writing."""

    def load(self, path: Path | str) -> dict[str, Any]:
        """Load raw checkpoint data dictionary from the file system."""
        str_path = str(path)
        if not os.path.exists(str_path):
            raise FileNotFoundError(f"Checkpoint file '{str_path}' does not exist.")
        with open(str_path, encoding="utf-8") as f:
            return json.load(f)

    def save(self, path: Path | str, data: dict[str, Any]) -> None:
        """Persist checkpoint data dictionary to the file system using atomic file replacement."""
        str_path = str(path)
        target_dir = os.path.dirname(os.path.abspath(str_path))
        os.makedirs(target_dir, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=target_dir, delete=False, encoding="utf-8") as tf:
            json.dump(data, tf, indent=2)
            temp_name = tf.name
        os.replace(temp_name, str_path)

    def default_path(self, repo_dir: Path | str, key: str) -> Path:
        """Derive the canonical checkpoint file path for a checkpoint key (sub-intent branch or intent slug)."""
        return Path(get_default_checkpoint_path(str(repo_dir), key))
