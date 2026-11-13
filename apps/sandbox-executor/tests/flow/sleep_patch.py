"""Patch a module's own ``time`` name so only that module sees a mocked ``sleep``.

A dotted patch such as ``sandbox_executor.flow.github.ci.time.sleep`` resolves ``time`` to the real ``time`` module and
replaces its ``sleep`` attribute for the whole process. Then ``subprocess``, ``threading`` and the ledger lock all see
the mock, and any real child that outlives its first poll records calls. The helper here swaps the module's ``time``
reference for a stand-in instead, leaving the real clock untouched everywhere else.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch


class _ScopedTime:
    """Stand-in for the ``time`` module: ``sleep`` is the mock, every other attribute reads the real module."""

    def __init__(self, sleep: Any) -> None:
        self.sleep = sleep

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)


@contextmanager
def scoped_sleep_patch(module_path: str, sleep: Any = None) -> Iterator[Any]:
    """Patch ``<module_path>.time`` with a stand-in whose ``sleep`` is ``sleep`` (a new ``MagicMock`` by default)."""
    mock = MagicMock() if sleep is None else sleep
    with patch(f"{module_path}.time", _ScopedTime(mock)):
        yield mock
