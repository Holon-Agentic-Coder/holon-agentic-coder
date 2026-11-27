"""Suite-wide isolation: no ambient credentials, agent-harness markers, real pre-flight probes or ``gh`` writes."""

import os
import socket
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from sandbox_executor import config_env
from sandbox_executor.agent_defaults import detected_harness_markers
from sandbox_executor.entrypoint import resolver_test_failure
from sandbox_executor.flow import process_reaper, run_lock
from sandbox_executor.flow.admission import AdmissionDecision
from sandbox_executor.flow.github import token_scopes
from sandbox_executor.launcher import env_files, mounts

from tests import fake_keychain as fake_keychain_helper
from tests import fast_tmp_dirs, network_guard
from tests.credential_env import CREDENTIAL_ENV_NAMES
from tests.gh_write_guard import guarded_popen
from tests.hermetic_fixtures import (
    REPO_ROOT,
    describe_index_changes,
    knowledge_index_snapshot,
)
from tests.holon_dir_guard import fail_on_holon_dir_changes, guarded_roots, snapshot_roots
from tests.worktree_fixtures import init_feature_worktree

# Ambient variables that can carry a live credential or change which code path a test exercises.
_AMBIENT_CREDENTIAL_NAMES = ("GITHUB_TOKEN", "GH_TOKEN", *CREDENTIAL_ENV_NAMES)
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


def pytest_configure(config: pytest.Config) -> None:
    """Make `tmp_path` and `tmp_path_factory.mktemp` constant-time for the session (see `tests.fast_tmp_dirs`)."""
    config.add_cleanup(fast_tmp_dirs.install_fast_numbered_dirs())


#: The first collector seen for each directory path in this session.
_FIRST_DIRECTORY_NODES = pytest.StashKey[dict[Path, pytest.Directory]]()


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_make_collect_report(collector: pytest.Collector) -> Iterator[None]:
    """Give a directory's conftest fixtures to every collector of that directory, not only the first.

    pytest 9.1 binds a conftest's fixtures to the first collector of its directory, and a later file argument in an
    already-collected directory makes pytest collect that directory again (`handle_dupes`) into new nodes. Parsing
    the conftest onto those nodes lets any file selection, in any order, see what the full run sees
    (`tests/test_fixture_discovery_subset.py`).
    """
    report = yield
    if not isinstance(collector, pytest.Directory):
        return report
    first_nodes = collector.config.stash.setdefault(_FIRST_DIRECTORY_NODES, {})
    first = first_nodes.setdefault(collector.path, collector)
    conftest = collector.config.pluginmanager.get_plugin(str(collector.path / "conftest.py"))
    if first is not collector and conftest is not None:
        collector.session._fixturemanager.parsefactories(holder=conftest, node=collector)
    return report


@pytest.fixture
def _private_test_root(tmp_path_factory) -> Path:
    """One new, empty, private directory for this test under the worker base temp's `isolated/` directory.

    The autouse fixtures that only need an empty directory each create their own child in it, so a test adds no
    entry to the base temp itself and the cost does not grow with the tests already run.
    """
    return fast_tmp_dirs.new_private_test_dir(tmp_path_factory.getbasetemp())


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


def _no_github_api(url, headers, timeout):
    raise OSError("the GitHub API is not reachable from the test suite")


@pytest.fixture(autouse=True)
def no_ambient_credentials(monkeypatch):
    """Run every test without the developer's or the sandbox's own credentials.

    Clears `GITHUB_TOKEN`, `GH_TOKEN`, every name in `tests.credential_env.CREDENTIAL_ENV_NAMES` (the Claude
    and other runner credentials) and every `HOLON_*` variable except the harness's own, and
    limits `find_github_token` to the (now cleared) environment so no test can reach the real
    `gh auth token`. A test that needs a token sets one explicitly, through `monkeypatch.setenv` or
    `patch.dict`.
    """
    for name in list(os.environ):
        if name in _HARNESS_NAMES:
            continue
        if name in _AMBIENT_CREDENTIAL_NAMES or name.startswith(_AMBIENT_PREFIX):
            monkeypatch.delenv(name, raising=False)
    # The token scope report (holon doctor, the Stage 3 pre-flight) never runs the real `gh` nor calls the GitHub API.
    monkeypatch.setattr(mounts, "_run_gh", lambda argv: subprocess.CompletedProcess(argv, 1, "", "gh disabled"))
    monkeypatch.setattr(token_scopes, "_urllib_fetch", _no_github_api)

    def env_token() -> str | None:
        return os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")

    monkeypatch.setattr(mounts, "find_github_token", env_token)


@pytest.fixture(autouse=True)
def isolated_claude_home(_private_test_root: Path, monkeypatch):
    """Keep every test off the real home, where a retired plaintext claude token file could still sit.

    Points HOME at an empty `claude-home` directory in the test's private root and makes the legacy token path (the
    one-time migration's input) follow the test's own HOME when it sets one (and this directory when it clears the
    environment, where `~` would otherwise fall back to the passwd home).
    """
    from sandbox_executor import claude_token_migration

    fake_home = str(_private_test_root / "claude-home")
    os.mkdir(fake_home, 0o700)
    monkeypatch.setenv("HOME", fake_home)

    def legacy_token_file_path() -> str:
        return os.path.join(os.environ.get("HOME") or fake_home, ".holon", "secrets", "claude-oauth-token")

    monkeypatch.setattr(claude_token_migration, "legacy_token_file_path", legacy_token_file_path)


@pytest.fixture(autouse=True)
def fake_keychain(monkeypatch) -> fake_keychain_helper.FakeKeychain:
    """Replace the OS secret store with an empty in-memory fake, so no test starts `security` or `secret-tool`.

    Returns the fake; a test seeds `entry` or flips a failure switch on it.
    """
    return fake_keychain_helper.install(monkeypatch)


@pytest.fixture(autouse=True)
def no_resolver_failure_diagnosis(monkeypatch):
    """Keep a scripted failing resolver test run from starting real diagnosis re-runs; a test reinstalls a loop."""
    monkeypatch.setattr(resolver_test_failure, "resolver_failure_loop", lambda _repo_dir: None)


@pytest.fixture(autouse=True)
def isolated_env_file_dir(_private_test_root: Path, monkeypatch):
    """Keep credential env-files written by run_docker_container in a per-test temporary directory.

    Any test that reaches `run_docker_container` with a token in the environment would otherwise
    write it under the real ~/.holon. The directory is `env-files` in the test's private root, outside
    `tmp_path`, so tests that inspect their own `tmp_path` never see it.
    """
    env_dir = _private_test_root / "env-files"
    env_dir.mkdir(mode=0o700)
    os.chmod(env_dir, 0o700)
    monkeypatch.setattr(env_files, "env_file_dir", lambda: str(env_dir))
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


def _layered_config_env() -> dict[str, str]:
    """The `os.environ` keys a layered configuration can fill: every `HOLON_*` variable and `GITHUB_TOKEN`."""
    return {key: value for key, value in os.environ.items() if key.startswith(_AMBIENT_PREFIX) or key == "GITHUB_TOKEN"}


@pytest.fixture(autouse=True)
def isolated_layered_config(
    _isolate_agent_defaults, _private_test_root: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Keep every test off the developer's `~/.holon/.env` and the checkout's `.env`, and undo what `cli.main` applies.

    Both layer paths point into an empty `layered-config` directory in the test's private root. `cli.main` folds the
    layered configuration into `os.environ` directly, which no monkeypatch would undo, so the `HOLON_*` and
    `GITHUB_TOKEN` keys are restored after the test and any key added during it is removed. The remembered resolution
    is reset on both sides.
    """
    layer_dir = _private_test_root / "layered-config"
    layer_dir.mkdir(mode=0o700)
    monkeypatch.setattr(config_env, "user_env_path", lambda: layer_dir / "user.env")
    monkeypatch.setattr(config_env, "project_env_path", lambda start: layer_dir / "project.env")
    config_env.remember(None)
    before = _layered_config_env()
    yield
    config_env.remember(None)
    for key, value in _layered_config_env().items():
        if key not in before:
            del os.environ[key]
        elif before[key] != value:
            os.environ[key] = before[key]
    for key, value in before.items():
        os.environ.setdefault(key, value)


@pytest.fixture(autouse=True)
def checkout_knowledge_index_guard() -> Iterator[None]:
    """Fail any test that creates, modifies or deletes a file under the checkout's knowledge index.

    Takes a snapshot of ``holon-knowledge/.index/`` before and after each test.  On any difference
    it calls ``pytest.fail`` with a message naming the offending paths and a remediation hint.

    Fail-closed rule: if the snapshot itself raises (e.g. a permission error), the test fails with
    that error and is never skipped.  A developer's pre-existing index is not counted as a failure
    -- only changes made during the test are.

    Note: under xdist, a change detected here may originate from a concurrent worker rather than
    the test being torn down.  If you see unexpected failures, re-run the offending test in
    isolation to confirm attribution.
    """
    before = knowledge_index_snapshot(REPO_ROOT)
    yield
    after = knowledge_index_snapshot(REPO_ROOT)
    changes = describe_index_changes(before, after)
    if changes:
        pytest.fail(
            "Test wrote the checkout's holon-knowledge/.index/: "
            + ", ".join(changes)
            + " -- pin repo_dir and the working directory to tmp_path to fix this.",
            pytrace=False,
        )


@pytest.fixture(scope="session", autouse=True)
def holon_dir_session_guard() -> Iterator[tuple[str, ...]]:
    """Fail the session if anything under the checkout's ``.holon/`` was created, modified or deleted during it.

    Guards the repository root and, when pytest started in a subdirectory of it, that directory too; the starting
    directory is read before any test changes it. Files that existed before the session (a live flow's checkpoint)
    only fail it if they change. Under xdist each worker runs this fixture, so the worker that wrote the file reports
    it. Yields the guarded roots.
    """
    roots = guarded_roots(REPO_ROOT, os.getcwd())
    before = snapshot_roots(roots)
    yield roots
    fail_on_holon_dir_changes(roots, before, "The test session")


@pytest.fixture(autouse=True)
def checkout_holon_dir_guard(holon_dir_session_guard: tuple[str, ...]) -> Iterator[None]:
    """Fail any test that creates, modifies or deletes a file under the checkout's ``.holon/`` (a flow checkpoint).

    A test that saves a flow checkpoint must pin ``repo_dir``/``target_worktree`` or the working directory to
    ``tmp_path``. Under xdist, or while a ``holon flow`` writes the same checkout, a write by another worker or process
    can fail an unrelated test; re-run it in isolation to confirm attribution.
    """
    before = snapshot_roots(holon_dir_session_guard)
    yield
    fail_on_holon_dir_changes(holon_dir_session_guard, before, "Test")


_CHECKOUT_LEDGERS = ("intents.jsonl", "plans.jsonl", "executions.jsonl")


@pytest.fixture(autouse=True)
def checkout_ledger_guard() -> Iterator[None]:
    """Fail any test that appends to, rewrites or deletes a checkout ledger under ``holon-knowledge/ledger/``.

    A test that runs the executor or a stage must pin its worktree to ``tmp_path``; one that does not leaks its
    fixture rows into the real append-only ledgers. The guard compares each ledger's size and modification time
    before and after the test. Under xdist, or while a ``holon flow`` or calibration writes the same checkout, a write
    by another worker or process can fail an unrelated test; re-run it in isolation to confirm attribution.
    """

    def snapshot() -> dict[str, tuple[int, int] | None]:
        result: dict[str, tuple[int, int] | None] = {}
        for name in _CHECKOUT_LEDGERS:
            path = os.path.join(REPO_ROOT, "holon-knowledge", "ledger", name)
            try:
                stat = os.stat(path)
                result[name] = (stat.st_size, stat.st_mtime_ns)
            except OSError:
                result[name] = None
        return result

    before = snapshot()
    yield
    changed = sorted(name for name, state in snapshot().items() if state != before[name])
    if changed:
        pytest.fail(
            "Test wrote the checkout's holon-knowledge/ledger/: "
            + ", ".join(changed)
            + " -- pin repo_dir and the working directory to tmp_path to fix this.",
            pytrace=False,
        )


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


@pytest.fixture(autouse=True)
def network_guard_fixture(monkeypatch: pytest.MonkeyPatch, gh_write_guard: list[str]) -> Iterator[list[str]]:
    """Fail any test that runs a git network command against a real remote or opens an inet connection.

    ``git fetch``, ``pull``, ``push`` and ``ls-remote`` against a filesystem path or ``file://`` remote, loopback
    connections and ``AF_UNIX`` sockets stay allowed. The operation is refused before it starts and the test fails at
    teardown, even when the code under test swallows the refusal. Yields the list of blocked operations; a test that
    expects a block clears it. Depends on ``gh_write_guard`` so its ``Popen`` wrapper is chained, not replaced.
    """
    blocked: list[str] = []
    monkeypatch.setattr(subprocess, "Popen", network_guard.guarded_popen(blocked))
    replacements = network_guard.guarded_socket_calls(blocked)
    monkeypatch.setattr(socket.socket, "connect", replacements["socket.connect"])
    monkeypatch.setattr(socket.socket, "connect_ex", replacements["socket.connect_ex"])
    monkeypatch.setattr(socket, "getaddrinfo", replacements["getaddrinfo"])
    yield blocked
    if blocked:
        pytest.fail("Network access ran in this test: " + "; ".join(blocked), pytrace=False)


#: Tests (path under tests/, then a class, function or ``Class.method`` qualname) that run from `feature_worktree_cwd`.
#: Listed here rather than marked in place so the long CLI test modules do not grow.
_FEATURE_WORKTREE_CWD_TESTS = frozenset(
    {
        ("cli/test_flow_cli.py", "TestHolonFlowCLI"),
        ("cli/test_flow_cli.py", "test_review_loop_handler_masks_a_quoted_password_head"),
        ("cli/test_flow_cli.py", "test_calibrate_handler_masks_a_quoted_password_head"),
        ("test_calibration.py", "TestCLICalibrateCommand"),
        ("test_review_loop.py", "TestCLIDispatchReviewLoop"),
        ("flow/test_pipeline.py", "TestFlowCLI.test_cli_flow_dry_run_dispatch"),
        ("flow/test_pipeline_touch_set.py", "TestAllowOverlapCLI"),
    }
)


@pytest.fixture(autouse=True)
def _listed_feature_worktree_cwd(request: pytest.FixtureRequest) -> None:
    """Apply `feature_worktree_cwd` to the tests in `_FEATURE_WORKTREE_CWD_TESTS`."""
    try:
        rel_path = request.node.path.relative_to(os.path.dirname(__file__)).as_posix()
    except ValueError:
        return
    name = request.node.originalname
    cls = request.node.cls
    if {(rel_path, name), (rel_path, cls and cls.__name__), (rel_path, cls and f"{cls.__name__}.{name}")} & (
        _FEATURE_WORKTREE_CWD_TESTS
    ):
        request.getfixturevalue("feature_worktree_cwd")


@pytest.fixture
def feature_worktree_cwd(tmp_path_factory, monkeypatch) -> str:
    """Run the test from a fixture worktree on a non-default branch instead of the real repository.

    The default ``--target-worktree .`` then means the same thing whichever branch the developer's or CI's checkout is
    on: ``holon flow`` does not redirect it and ``holon review`` and ``holon calibrate`` do not refuse it.
    """
    path = init_feature_worktree(tmp_path_factory.mktemp("feature-worktree"))
    monkeypatch.chdir(path)
    return path


@pytest.fixture(autouse=True)
def stub_stage3_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admit every Stage 3, so no test reaches the host ``gh`` or another worktree through touch-set admission.

    The admission tests (``tests/flow/test_admission.py`` and ``tests/flow/stages/test_execute_touch_set.py``) put the
    real ``check_admission`` back with injected ``gh`` and ``git`` runners.
    """
    monkeypatch.setattr("sandbox_executor.flow.admission.check_admission", lambda *_a, **_k: AdmissionDecision(True))


@pytest.fixture(autouse=True)
def stub_executor_test_measurement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep executor tests from running the real ``uv run task test``; a test that needs a measurement patches it."""
    monkeypatch.setattr("sandbox_executor.entrypoint.executor.measure_executor_tests", lambda *_args, **_kw: {})


@pytest.fixture(autouse=True)
def stub_review_local_test_measurement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep review-loop tests from running the real ``uv run task test``: the stub is an unrunnable (unmeasured) run."""
    from sandbox_executor.entrypoint.test_measurement import TestMeasurement

    unmeasured = TestMeasurement(None, None, None, None, 127, "test task not run in this test", "")
    monkeypatch.setattr("sandbox_executor.flow.review.local_tests.run_measurement", lambda _workspace: unmeasured)


@pytest.fixture(autouse=True)
def stub_review_workspace_head(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read the review workspace HEAD as ``stage3-head``, the commit test Stage 3 payloads record as tested.

    A test that moves the head patches ``sandbox_executor.flow.review.local_tests.run_git`` itself.
    """
    from unittest.mock import MagicMock

    head = MagicMock(returncode=0, stdout="stage3-head\n")
    monkeypatch.setattr("sandbox_executor.flow.review.local_tests.run_git", lambda *_a, **_k: head)


@pytest.fixture(autouse=True)
def release_flow_run_state() -> Iterator[None]:
    """Release the flow run locks a test took in-process and end its reaper run, restoring the signal handlers.

    ``holon flow`` holds its run lock until the process exits; the test process outlives each in-process run, so
    without this a later test in the same worker would re-enter a stale run, or a lock left on a test repository.
    A test that takes a run lock in the real checkout's git directory fails: list it in ``_FEATURE_WORKTREE_CWD_TESTS``
    or give it a temporary ``--target-worktree``.
    """
    yield
    process_reaper.reset_reaper()
    held = run_lock.held_run_lock_paths()
    real_git_dir = os.path.dirname(run_lock.run_lock_path(REPO_ROOT, "probe") or "/") if held else None
    in_checkout = [path for path in held if os.path.dirname(path) == real_git_dir]
    run_lock.release_flow_run_lock()
    assert not in_checkout, f"a test took a flow run lock in the real repository: {in_checkout}"
