"""Shared fakes that read the docker `--env-file` a `run_docker_container` call hands over.

The env-file is removed as soon as the run returns, so a fake `subprocess.run` has to read it while docker "runs".
"""

import os
from itertools import pairwise
from unittest.mock import MagicMock

ENV_FILE_PLACEHOLDER = "<env-file>"


def read_env_file(docker_argv: list[str]) -> dict[str, str]:
    """Parse the `--env-file` a docker argv names (only valid while the run is in progress); `{}` without one."""
    if "--env-file" not in docker_argv:
        return {}
    with open(docker_argv[docker_argv.index("--env-file") + 1], encoding="utf-8") as fh:
        return dict(line.rstrip("\n").split("=", 1) for line in fh)


class DockerEnvFileRecorder:
    """A `subprocess.run` fake recording each docker argv and the variables of the env-file it names.

    The random env-file path in a recorded argv is replaced by `ENV_FILE_PLACEHOLDER` so two runs compare byte for
    byte. `passthrough` holds, per run, the bare `-e NAME` items with their values from the docker client env.
    """

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.argvs: list[list[str]] = []
        self.env_files: list[dict[str, str]] = []
        self.passthrough: list[dict[str, str]] = []

    def __call__(self, cmd: list[str], *args: object, **kwargs: object) -> MagicMock:
        path = cmd[cmd.index("--env-file") + 1] if "--env-file" in cmd else None
        self.env_files.append(read_env_file(cmd))
        self.argvs.append([ENV_FILE_PLACEHOLDER if arg == path else arg for arg in cmd])
        self.passthrough.append(bare_env_items(cmd, kwargs.get("env")))
        return MagicMock(returncode=self.returncode)


def bare_env_items(docker_argv: list[str], client_env: object) -> dict[str, str]:
    """Return each bare `-e NAME` item of a docker argv with the value docker copies from its client env."""
    env = client_env if isinstance(client_env, dict) else os.environ
    return {item: env[item] for flag, item in pairwise(docker_argv) if flag == "-e" and "=" not in item and item in env}
