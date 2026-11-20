"""The ``.holon/`` write guard: snapshots, guarded roots, and a session that fails when a test writes a checkpoint."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.hermetic_fixtures import REPO_ROOT
from tests.holon_dir_guard import (
    fail_on_holon_dir_changes,
    guarded_roots,
    holon_dir_failure,
    holon_dir_snapshot,
    snapshot_roots,
)

_SUITE_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: A child session whose only guarded root is its own directory, never the checkout's ``.holon/``.
_CHILD_CONFTEST = """\
import os

import pytest

from tests.holon_dir_guard import fail_on_holon_dir_changes, snapshot_roots

ROOTS = (os.path.dirname(os.path.abspath(__file__)),)


@pytest.fixture(scope="session", autouse=True)
def holon_dir_session_guard():
    before = snapshot_roots(ROOTS)
    yield
    fail_on_holon_dir_changes(ROOTS, before, "The test session")
"""

_CHILD_TEST = """\
from pathlib import Path


def test_writes():
    target = Path(__file__).parent / {relative!r}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{{}}")
"""


def _write(path: Path, text: str = "{}") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_snapshot_of_a_missing_holon_dir_is_empty(tmp_path: Path) -> None:
    assert holon_dir_snapshot(str(tmp_path)) == {}


def test_snapshot_lists_only_files_under_holon_dir(tmp_path: Path) -> None:
    _write(tmp_path / ".holon" / "flow" / "checkpoint-a.json")
    _write(tmp_path / "elsewhere.json")

    assert list(holon_dir_snapshot(str(tmp_path))) == [".holon/flow/checkpoint-a.json"]


def test_created_modified_and_deleted_files_are_each_reported(tmp_path: Path) -> None:
    root = (str(tmp_path),)
    _write(tmp_path / ".holon" / "flow" / "kept.json")
    _write(tmp_path / ".holon" / "flow" / "changed.json")
    _write(tmp_path / ".holon" / "flow" / "removed.json")
    before = snapshot_roots(root)

    _write(tmp_path / ".holon" / "flow" / "changed.json", '{"stage": "plan"}')
    (tmp_path / ".holon" / "flow" / "removed.json").unlink()
    _write(tmp_path / ".holon" / "flow" / "checkpoint-parent.json")

    with pytest.raises(pytest.fail.Exception) as failure:
        fail_on_holon_dir_changes(root, before, "Test")
    assert str(failure.value) == holon_dir_failure(
        [
            "modified .holon/flow/changed.json",
            "created .holon/flow/checkpoint-parent.json",
            "deleted .holon/flow/removed.json",
        ],
        "Test",
    )


def test_pre_existing_unchanged_files_never_fail(tmp_path: Path) -> None:
    root = (str(tmp_path),)
    _write(tmp_path / ".holon" / "flow" / "checkpoint-live.json")
    before = snapshot_roots(root)

    fail_on_holon_dir_changes(root, before, "Test")


def test_failure_message_names_the_paths_and_the_fix() -> None:
    message = holon_dir_failure(["created .holon/flow/checkpoint-parent.json"], "Test")

    assert "created .holon/flow/checkpoint-parent.json" in message
    assert "pin repo_dir/target_worktree or the working directory to tmp_path" in message


def test_guarded_roots_add_an_invocation_directory_inside_the_repository() -> None:
    repo = os.path.realpath(REPO_ROOT)
    suite = os.path.join(repo, "apps", "sandbox-executor")

    assert guarded_roots(REPO_ROOT, suite) == (repo, suite)
    assert guarded_roots(REPO_ROOT, REPO_ROOT) == (repo,)


def test_guarded_roots_ignore_an_invocation_directory_outside_the_repository(tmp_path: Path) -> None:
    assert guarded_roots(REPO_ROOT, str(tmp_path)) == (os.path.realpath(REPO_ROOT),)


def test_snapshot_roots_key_paths_relative_to_the_first_root(tmp_path: Path) -> None:
    _write(tmp_path / ".holon" / "a.json")
    _write(tmp_path / "sub" / ".holon" / "b.json")

    assert sorted(snapshot_roots((str(tmp_path), str(tmp_path / "sub")))) == [".holon/a.json", "sub/.holon/b.json"]


def _run_child_session(root: Path, relative: str) -> subprocess.CompletedProcess[str]:
    """Run a one-test pytest session in ``root`` whose session guard watches ``root/.holon/`` only."""
    (root / "pytest.ini").write_text("[pytest]\n")
    (root / "conftest.py").write_text(_CHILD_CONFTEST)
    (root / "test_child.py").write_text(_CHILD_TEST.format(relative=relative))
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [_SUITE_PARENT, os.environ.get("PYTHONPATH")]))}
    command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:xdist", "-q"]
    return subprocess.run(
        [*command, f"--rootdir={root}", "-c", str(root / "pytest.ini"), str(root)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_session_guard_fails_a_session_that_writes_under_holon_dir(tmp_path: Path) -> None:
    result = _run_child_session(tmp_path, ".holon/flow/checkpoint-x.json")

    assert result.returncode != 0, result.stdout + result.stderr
    assert "The test session wrote the checkout's .holon/: created .holon/flow/checkpoint-x.json" in result.stdout


def test_session_guard_passes_a_session_that_writes_elsewhere(tmp_path: Path) -> None:
    result = _run_child_session(tmp_path, "scratch/checkpoint-x.json")

    assert result.returncode == 0, result.stdout + result.stderr
