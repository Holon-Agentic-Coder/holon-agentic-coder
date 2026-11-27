from __future__ import annotations

import os
import stat
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from sandbox_executor import cli
from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT

from tests.env_file_capture import bare_env_items, read_env_file

# why: captured at import, before the suite-wide isolated_env_file_dir fixture in conftest.py replaces it
_REAL_ENV_FILE_DIR = cli.env_file_dir

_WORKTREE_MOUNTS = ["-v", f"/work/flow-x:{SANDBOX_WORKSPACE_MOUNT}"]


def _stub_host_publish(testcase: unittest.TestCase) -> dict[str, MagicMock]:
    mocks: dict[str, MagicMock] = {}
    for name, target, kwargs in (
        ("intent_prepare", "intent.prepare_worktree", {"return_value": "/work/flow-x"}),
        ("intent_push", "intent.push_checked_out_branch", {"return_value": "I-1-x/_"}),
        ("plan_prepare", "plan.prepare_worktree", {"return_value": "/work/flow-x"}),
        ("plan_push", "plan.push_checked_out_branch", {"return_value": "I-1-x/P-1/_"}),
        ("execute_worktree", "execute.execution_worktree", {"return_value": "/work/flow-x"}),
        ("execute_push", "execute.push_checked_out_branch", {"return_value": "I-1-x/P-1/E-2/_"}),
    ):
        patcher = patch(f"sandbox_executor.cli.commands.{target}", **kwargs)
        mocks[name] = patcher.start()
        testcase.addCleanup(patcher.stop)
    return mocks


class _RecordingDocker:
    def __init__(self, outcome=None):
        self.outcome = outcome if outcome is not None else MagicMock(returncode=0)
        self.argv: list[str] = []
        self.env_file: str | None = None
        self.env_file_mode: int | None = None
        self.env_dir_mode: int | None = None
        self.env_file_contents: dict[str, str] = {}
        self.passthrough_env: dict[str, str] = {}
        self._real_run = subprocess.run

    @property
    def argv_env(self) -> dict[str, str]:
        pairs = zip(self.argv, self.argv[1:], strict=False)
        return dict(item.split("=", 1) for flag, item in pairs if flag == "-e" and "=" in item)

    @property
    def container_env(self) -> dict[str, str]:
        return {**self.argv_env, **self.env_file_contents}

    def __call__(self, cmd, *args, **kwargs):
        if cmd[0] != "docker":
            return self._real_run(cmd, *args, **kwargs)
        self.argv = list(cmd)
        self.passthrough_env = bare_env_items(cmd, kwargs.get("env"))
        if "--env-file" in cmd:
            self.env_file = cmd[cmd.index("--env-file") + 1]
            self.env_file_mode = stat.S_IMODE(os.stat(self.env_file).st_mode)
            self.env_dir_mode = stat.S_IMODE(os.stat(os.path.dirname(self.env_file)).st_mode)
            self.env_file_contents = read_env_file(cmd)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome
