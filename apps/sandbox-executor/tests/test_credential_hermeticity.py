"""Guard: the suite stays hermetic when a credential variable is really set in the process environment."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sandbox_executor.claude_credential import CLAUDE_CREDENTIAL_ENV_NAMES

from tests import conftest
from tests.credential_env import CREDENTIAL_ENV_NAMES
from tests.hermetic_fixtures import CHILD_SUITE_SENTINEL, REPO_ROOT

PROBE_NAME_VAR = "CREDENTIAL_GUARD_PROBE_NAME"
DUMMY_VALUE = "sk-ant-oat-dummy-not-real"
CHILD_TIMEOUT_SECONDS = 120
_TESTS_DIR = Path(__file__).parent
_CREDENTIAL_TESTS = (
    "test_cli.py::test_claude_container_gets_the_discovered_keychain_value_by_type",
    "test_cli.py::test_secrets_travel_in_an_owner_only_env_file_that_is_removed",
    "test_cli.py::test_line_break_in_a_secret_aborts_before_docker_runs",
)


def test_probe_sees_no_ambient_credential():
    """Runs only in the child suite: the fixture must have removed every planted variable."""
    names = [name for name in os.environ.get(PROBE_NAME_VAR, "").split(",") if name]
    if not names:
        pytest.skip("only meaningful inside the guard's child pytest")
    leaked = [name for name in names if name in os.environ]
    assert not leaked, f"{leaked} leaked into the test environment"


def test_fixture_clears_every_credential_name_the_code_reads():
    assert set(CLAUDE_CREDENTIAL_ENV_NAMES) <= set(CREDENTIAL_ENV_NAMES)
    missing = set(CREDENTIAL_ENV_NAMES) - set(conftest._AMBIENT_CREDENTIAL_NAMES)
    assert not missing, f"add {sorted(missing)} to _AMBIENT_CREDENTIAL_NAMES in tests/conftest.py"


@pytest.mark.skipif(os.environ.get(CHILD_SUITE_SENTINEL) == "1", reason="child suite must not recurse")
def test_credential_tests_pass_with_variable_set_in_real_environment(tmp_path):
    planted = dict.fromkeys(CREDENTIAL_ENV_NAMES, DUMMY_VALUE)
    env = {
        **os.environ,
        **planted,
        PROBE_NAME_VAR: ",".join(CREDENTIAL_ENV_NAMES),
        CHILD_SUITE_SENTINEL: "1",
    }
    selection = [str(_TESTS_DIR / node) for node in _CREDENTIAL_TESTS]
    selection.append(f"{__file__}::test_probe_sees_no_ambient_credential")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:xdist", "-p", "no:cacheprovider",
         f"--basetemp={tmp_path / 'child'}", *selection],
        env=env, cwd=REPO_ROOT, capture_output=True, text=True, timeout=CHILD_TIMEOUT_SECONDS, check=False,
    )  # fmt: skip
    assert result.returncode == 0, (
        f"Credential variables {list(CREDENTIAL_ENV_NAMES)} set in the environment broke the credential tests; "
        f"clear them in `no_ambient_credentials` (tests/conftest.py, list in tests/credential_env.py).\n"
        f"{result.stdout[-2000:]}"
    )
