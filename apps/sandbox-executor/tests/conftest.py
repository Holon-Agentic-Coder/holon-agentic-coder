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


#: Test-only global git config. Each value either stops git spawning work no test asks for (automatic
#: gc and maintenance, commit-graph writes, fsmonitor) or replaces a value the developer's own config
#: would otherwise supply (hooks, default branch, identity, signing).
_TEST_GIT_CONFIG = """\
[gc]
\tauto = 0
[maintenance]
\tauto = false
[fetch]
\twriteCommitGraph = false
[core]
\thooksPath = {hooks_dir}
\tfsmonitor = false
[init]
\tdefaultBranch = main
[user]
\tname = Holon Test
\temail = test@holon.invalid
[commit]
\tgpgsign = false
[tag]
\tgpgsign = false
[advice]
\tdetachedHead = false
"""


#: Environment-injected git config, which git applies on top of every config file.
_GIT_ENV_CONFIG_NAMES = frozenset({"GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"})
_GIT_ENV_CONFIG_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")


@pytest.fixture(scope="session", autouse=True)
def hermetic_git_config(tmp_path_factory) -> Iterator[str]:
    """Point every git process in the session at a test-only global config, never the developer's.

    Clears environment-injected config (`GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_COUNT`,
    `GIT_CONFIG_KEY_<n>`, `GIT_CONFIG_VALUE_<n>`) and sets `GIT_CONFIG_GLOBAL` to a temporary file
    and `GIT_CONFIG_NOSYSTEM=1` in `os.environ` for the whole session, so child processes that copy
    the environment (the stress canary's child suite, xdist workers) inherit the result.
    `tmp_path_factory` is per xdist worker, so each worker owns its file. A repository's own
    `.git/config` still overrides these values. Yields the config path.
    """
    config_dir = tmp_path_factory.mktemp("git-config")
    hooks_dir = config_dir / "hooks"
    hooks_dir.mkdir()
    config_file = config_dir / "gitconfig"
    config_file.write_text(_TEST_GIT_CONFIG.format(hooks_dir=hooks_dir.as_posix()), encoding="utf-8")
    with pytest.MonkeyPatch.context() as mp:
        for name in list(os.environ):
            if name in _GIT_ENV_CONFIG_NAMES or name.startswith(_GIT_ENV_CONFIG_PREFIXES):
                mp.delenv(name, raising=False)
        mp.setenv("GIT_CONFIG_GLOBAL", str(config_file))
        mp.setenv("GIT_CONFIG_NOSYSTEM", "1")
        yield str(config_file)


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
