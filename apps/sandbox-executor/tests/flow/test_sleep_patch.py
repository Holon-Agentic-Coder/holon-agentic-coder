"""Tests for the scoped sleep patch helper."""

from __future__ import annotations

import subprocess
import sys
import time
from unittest.mock import MagicMock

from sandbox_executor.flow.github import ci

from tests.flow.sleep_patch import scoped_sleep_patch


def test_module_sleep_is_the_mock_and_other_attributes_stay_real():
    sleep = MagicMock()
    with scoped_sleep_patch("sandbox_executor.flow.github.ci", sleep) as mock:
        assert mock is sleep
        ci.time.sleep(5)
        assert isinstance(ci.time.monotonic(), float)
    sleep.assert_called_once_with(5)


def test_real_time_module_is_untouched_and_restored():
    real_sleep = time.sleep
    original = ci.time
    with scoped_sleep_patch("sandbox_executor.flow.github.ci"):
        assert time.sleep is real_sleep
    assert ci.time is original


def test_real_child_does_not_reach_the_mock():
    code = "import time; time.sleep(0.2)"
    with scoped_sleep_patch("sandbox_executor.flow.github.ci") as mock:
        subprocess.run([sys.executable, "-c", code], check=True)
    mock.assert_not_called()
