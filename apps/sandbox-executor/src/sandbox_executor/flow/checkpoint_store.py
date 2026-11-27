from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from sandbox_executor.flow.checkpoint_discovery import get_default_checkpoint_path


@runtime_checkable
class CheckpointStore(Protocol):
    def load(self, path: Path | str) -> dict[str, Any]: ...

    def save(self, path: Path | str, data: dict[str, Any]) -> None: ...

    def default_path(self, repo_dir: Path | str, key: str) -> Path: ...


class FileCheckpointStore:
    def load(self, path: Path | str) -> dict[str, Any]:
        str_path = str(path)
        if not os.path.exists(str_path):
            raise FileNotFoundError(f"Checkpoint file '{str_path}' does not exist.")
        with open(str_path, encoding="utf-8") as f:
            return json.load(f)

    def save(self, path: Path | str, data: dict[str, Any]) -> None:
        str_path = str(path)
        target_dir = os.path.dirname(os.path.abspath(str_path))
        os.makedirs(target_dir, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=target_dir, delete=False, encoding="utf-8") as tf:
            json.dump(data, tf, indent=2)
            temp_name = tf.name
        os.replace(temp_name, str_path)

    def default_path(self, repo_dir: Path | str, key: str) -> Path:
        return Path(get_default_checkpoint_path(str(repo_dir), key))
