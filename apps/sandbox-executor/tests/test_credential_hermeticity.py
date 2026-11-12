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
    "test_cli.py::test_claude_container_gets_the_discovered_secrets_file_value_by_type",
    "test_cli.py::test_secrets_travel_in_an_owner_only_env_file_that_is_removed",
    "test_cli.py::test_line_break_in_a_secret_aborts_before_docker_runs",
)


def test_probe_sees_no_ambient_credential():
    """Runs only in the child suite: the fixture must have removed the planted variable."""
    name = os.environ.get(PROBE_NAME_VAR)
    if not name:
        pytest.skip("only meaningful inside the guard's child pytest")
    assert name not in os.environ, f"{name} leaked into the test environment"


def test_fixture_clears_every_credential_name_the_code_reads():
    assert set(CLAUDE_CREDENTIAL_ENV_NAMES) <= set(CREDENTIAL_ENV_NAMES)
    missing = set(CREDENTIAL_ENV_NAMES) - set(conftest._AMBIENT_CREDENTIAL_NAMES)
    assert not missing, f"add {sorted(missing)} to _AMBIENT_CREDENTIAL_NAMES in tests/conftest.py"


@pytest.mark.skipif(os.environ.get(CHILD_SUITE_SENTINEL) == "1", reason="child suite must not recurse")
@pytest.mark.parametrize("name", CREDENTIAL_ENV_NAMES)
def test_credential_tests_pass_with_variable_set_in_real_environment(name, tmp_path):
    env = {**os.environ, name: DUMMY_VALUE, PROBE_NAME_VAR: name, CHILD_SUITE_SENTINEL: "1"}
    selection = [str(_TESTS_DIR / node) for node in _CREDENTIAL_TESTS]
    selection.append(f"{__file__}::test_probe_sees_no_ambient_credential")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:xdist", "-p", "no:cacheprovider",
         f"--basetemp={tmp_path / 'child'}", *selection],
        env=env, cwd=REPO_ROOT, capture_output=True, text=True, timeout=CHILD_TIMEOUT_SECONDS, check=False,
    )  # fmt: skip
    assert result.returncode == 0, (
        f"{name} set in the environment broke the credential tests; clear it in "
        f"`no_ambient_credentials` (tests/conftest.py, list in tests/credential_env.py).\n"
        f"{result.stdout[-2000:]}"
    )
