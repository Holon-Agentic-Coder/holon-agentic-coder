"""Any subset of test files, in any argument order, sees the same conftest fixtures as the full run.

pytest 9.1 binds a conftest's fixtures to the first collector of its directory, and a later file argument in an
already-collected directory makes pytest collect that directory again into new nodes. These cases run pytest in a
subprocess over file lists that trigger the duplicate nodes and guard the hook in `tests/conftest.py` that gives those
nodes the conftest fixtures.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
REPO_ROOT = TESTS_DIR.parents[2]

_FIXTURE_NOT_FOUND = re.compile(r"fixture '[^']+' not found")

FILE_LISTS = {
    "no_origin_reconcile": (
        "flow/stages/test_children.py",
        "flow/test_git_refs.py",
        "flow/stages/test_replan_request.py",
    ),
    "flow_repos": ("flow/stages/test_children.py", "test_naming.py", "flow/test_git_refs.py"),
}


def _child_env() -> dict[str, str]:
    """Copy the environment without the outer xdist worker identity or the running test's name."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("PYTEST_XDIST_")}
    env.pop("PYTEST_CURRENT_TEST", None)
    return env


@pytest.mark.parametrize("files", FILE_LISTS.values(), ids=FILE_LISTS.keys())
def test_file_subset_sees_conftest_fixtures(files: tuple[str, ...]) -> None:
    command = [sys.executable, "-m", "pytest", "-p", "no:xdist", "-p", "no:cacheprovider", "-q", "--setup-plan"]
    command += [str(TESTS_DIR / name) for name in files]
    result = subprocess.run(
        command, cwd=REPO_ROOT, env=_child_env(), capture_output=True, text=True, timeout=120, check=False
    )
    output = result.stdout + result.stderr
    missing = [line for line in output.splitlines() if _FIXTURE_NOT_FOUND.search(line)]
    assert not missing, "\n".join(missing)
    assert result.returncode == 0, output
    unplanned = [name for name in files if f"{name}::" not in output]
    assert not unplanned, f"no tests planned for {unplanned}:\n{output}"
