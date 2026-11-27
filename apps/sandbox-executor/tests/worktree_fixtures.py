from __future__ import annotations

import os
import subprocess
from pathlib import Path

# why: flow redirects and review/calibrate refuse a default-branch worktree, so CLI tests run from a feature branch
FEATURE_BRANCH = "feature-x"


def init_feature_worktree(path: str | os.PathLike[str], branch: str = FEATURE_BRANCH) -> str:
    Path(path).mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True, capture_output=True)
    return str(path)
