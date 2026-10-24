"""Suite-wide isolation: no ambient credentials, agent-harness markers, real pre-flight probes or ``gh`` writes."""

import os
import subprocess
from collections.abc import Iterator

import pytest
from sandbox_executor import cli
from sandbox_executor.agent_defaults import detected_harness_markers

from tests.gh_write_guard import guarded_popen

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


@pytest.fixture(autouse=True)
def _isolate_agent_defaults(no_ambient_credentials, monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear harness markers and runner overrides, and disable the real readiness probe.

    The suite runs inside Claude Code, pi and other harnesses whose markers would otherwise change the resolved default
    runner. The pre-flight probe makes a real model call through docker, so tests opt back in explicitly by deleting
    HOLON_SKIP_PREFLIGHT and mocking the probe. Depends on `no_ambient_credentials` so it always runs after that
    fixture's `HOLON_*` sweep, which would otherwise delete HOLON_SKIP_PREFLIGHT again.
    """
    for _runner_id, name in detected_harness_markers():
        monkeypatch.delenv(name, raising=False)
    for name in ("HOLON_AGENT", "HOLON_MODEL", "HOLON_PREFLIGHT_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOLON_SKIP_PREFLIGHT", "1")


@pytest.fixture(autouse=True)
def gh_write_guard(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Fail any test that runs an unmocked ``gh`` write command (comment, review, create, edit, merge, ...).

    Read-only ``gh`` calls still run. The write is refused before ``gh`` starts and the test fails at teardown, even
    when the code under test swallows the refusal. Yields the list of blocked commands; a test that expects a block
    clears it.
    """
    blocked: list[str] = []
    monkeypatch.setattr(subprocess, "Popen", guarded_popen(blocked))
    yield blocked
    if blocked:
        pytest.fail("Unmocked gh write command(s) ran in this test: " + "; ".join(blocked), pytrace=False)
