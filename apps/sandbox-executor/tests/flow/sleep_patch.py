from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch


# why: a dotted <module>.time.sleep patch leaks the mock to subprocess, threading and the ledger lock process-wide.
class _ScopedTime:
    def __init__(self, sleep: Any) -> None:
        self.sleep = sleep

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)


@contextmanager
def scoped_sleep_patch(module_path: str, sleep: Any = None) -> Iterator[Any]:
    mock = MagicMock() if sleep is None else sleep
    with patch(f"{module_path}.time", _ScopedTime(mock)):
        yield mock
