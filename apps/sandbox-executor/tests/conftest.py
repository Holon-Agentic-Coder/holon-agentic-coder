"""Suite-wide fixtures."""

import os

import pytest
from sandbox_executor import cli

# Ambient variables that can carry a live credential or change which code path a test exercises.
_AMBIENT_CREDENTIAL_NAMES = ("GITHUB_TOKEN", "GH_TOKEN")
_AMBIENT_PREFIX = "HOLON_"

# Set by the test harness itself, never by a user: the child-suite recursion sentinels and the
# simulated container role that `test_sandbox_hermetic_guard` runs the whole suite under. Clearing
# them would let a child suite recurse into the guard and would void the simulation it checks.
_HARNESS_NAMES = frozenset(
    {
        "HOLON_HERMETIC_GUARD_CHILD",
        "HOLON_STRESS_CHILD",
        "HOLON_CHILD_SUITE_ACTIVE",
        "HOLON_ROLE",
        "HOLON_IN_SANDBOX",
    }
)


@pytest.fixture(autouse=True)
def no_ambient_credentials(monkeypatch):
    """Run every test without the developer's or the sandbox's own credentials.

    Clears `GITHUB_TOKEN`, `GH_TOKEN` and every `HOLON_*` variable except the harness's own, and
    limits `find_github_token` to the (now cleared) environment so no test can reach the real
    `gh auth token`. A test that needs a token sets one explicitly, through `monkeypatch.setenv` or
    `patch.dict`.
    """
    for name in list(os.environ):
        if name in _HARNESS_NAMES:
            continue
        if name in _AMBIENT_CREDENTIAL_NAMES or name.startswith(_AMBIENT_PREFIX):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli, "find_github_token", lambda: os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN"))


@pytest.fixture(autouse=True)
def isolated_env_file_dir(tmp_path_factory, monkeypatch):
    """Keep credential env-files written by run_docker_container in a per-test temporary directory.

    Any test that reaches `run_docker_container` with a token in the environment would otherwise
    write it under the real ~/.holon. The directory sits outside `tmp_path`, so tests that inspect
    their own `tmp_path` never see it.
    """
    env_dir = tmp_path_factory.mktemp("env-files")
    os.chmod(env_dir, 0o700)
    monkeypatch.setattr(cli, "env_file_dir", lambda: str(env_dir))
    return env_dir
