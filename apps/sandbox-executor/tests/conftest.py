"""Suite-wide fixtures."""

import os

import pytest
from sandbox_executor import cli


@pytest.fixture(autouse=True)
def isolated_env_file_dir(tmp_path_factory, monkeypatch):
    """Keep credential env-files written by run_docker_container in a per-test temporary directory.

    Any test that reaches `run_docker_container` with a token in the environment (including the
    developer's own GITHUB_TOKEN) would otherwise write it under the real ~/.holon. The directory
    sits outside `tmp_path`, so tests that inspect their own `tmp_path` never see it.
    """
    env_dir = tmp_path_factory.mktemp("env-files")
    os.chmod(env_dir, 0o700)
    monkeypatch.setattr(cli, "env_file_dir", lambda: str(env_dir))
    return env_dir
