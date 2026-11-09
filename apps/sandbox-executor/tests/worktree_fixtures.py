"""Fixture working trees on a non-default branch, so CLI tests behave the same whatever branch the checkout is on.

``holon flow`` redirects a target worktree that has the default branch checked out, and ``holon review`` and
``holon calibrate`` refuse one. Tests that exercise something else and rely on the default ``--target-worktree .``
run from one of these instead of the real repository.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

FEATURE_BRANCH = "feature-x"


def init_feature_worktree(path: str | os.PathLike[str], branch: str = FEATURE_BRANCH) -> str:
    """Create an empty git repository at path with branch (unborn) checked out and return its path."""
    Path(path).mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True, capture_output=True)
    return str(path)
