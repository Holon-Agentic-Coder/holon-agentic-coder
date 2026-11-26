"""Constant-time temporary directories: cost independent of the base temp's size, never-reused names, isolation."""

import os
from pathlib import Path

import _pytest.tmpdir
import pytest
from sandbox_executor import config_env
from sandbox_executor.launcher import env_files

from tests import fast_tmp_dirs
from tests.fast_tmp_dirs import NumberedDirAllocator, PrivateTestDirs


@pytest.fixture
def scandir_calls(monkeypatch) -> list[str]:
    """Count every ``os.scandir`` call during the test; returns the scanned paths."""
    calls: list[str] = []
    real_scandir = os.scandir

    def counting_scandir(path="."):
        calls.append(os.fspath(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", counting_scandir)
    return calls


def test_allocator_scans_a_root_only_once_while_names_are_free(tmp_path, scandir_calls):
    """Only the first call for a ``(root, prefix)`` pair lists the root; later calls just count up."""
    allocate = NumberedDirAllocator()
    names = [allocate(tmp_path, "case").name for _ in range(200)]
    assert names == [f"case{n}" for n in range(200)]
    assert scandir_calls == [str(tmp_path)]


def test_allocator_scans_a_root_once_across_many_prefixes(tmp_path, scandir_calls):
    """``tmp_path`` uses each test's name as the prefix: new prefixes reuse the root's one listing, not a rescan."""
    (tmp_path / "test_0_0").mkdir()
    allocate = NumberedDirAllocator()
    names = [allocate(tmp_path, f"test_{n}_").name for n in range(500)]
    assert names == ["test_0_1"] + [f"test_{n}_0" for n in range(1, 500)]
    assert allocate(tmp_path, "test_0_").name == "test_0_2"
    assert scandir_calls == [str(tmp_path)]


def test_private_dirs_scan_only_once(tmp_path, scandir_calls):
    """Per-test private directories list their ``isolated`` root once per process, not once per test."""
    private_dirs = PrivateTestDirs()
    for _ in range(200):
        private_dirs.new(tmp_path)
    assert scandir_calls == [str(tmp_path / fast_tmp_dirs.PRIVATE_ROOT_NAME)]


def test_allocator_never_reuses_an_existing_or_foreign_name(tmp_path):
    """Existing names, and a name a foreign writer adds after the counter was seeded, are skipped, never reused."""
    for n in range(5):
        (tmp_path / f"prefix{n}").mkdir()
    allocate = NumberedDirAllocator()
    assert allocate(tmp_path, "prefix").name == "prefix5"
    (tmp_path / "prefix7").mkdir()
    (tmp_path / "prefix7" / "foreign").touch()
    created = [allocate(tmp_path, "prefix") for _ in range(1000)]
    assert len(set(created)) == len(created)
    assert created[:2] == [tmp_path / "prefix6", tmp_path / "prefix8"]
    assert all(not any(path.iterdir()) for path in created)


def test_allocator_gives_up_after_bounded_retries(tmp_path, monkeypatch):
    """A name that keeps existing fails loudly after ``MAX_ATTEMPTS`` tries instead of looping or reusing it."""

    def always_exists(path, mode=0o777):
        raise FileExistsError(path)

    monkeypatch.setattr(os, "mkdir", always_exists)
    with pytest.raises(OSError, match=f"after {fast_tmp_dirs.MAX_ATTEMPTS} tries") as raised:
        NumberedDirAllocator()(tmp_path, "stuck")
    assert type(raised.value) is OSError


def test_private_dirs_skip_a_stale_directory_from_an_earlier_run(tmp_path):
    """A reused base temp's leftover ``isolated/t0`` is skipped: each call returns a new, empty, private directory."""
    stale = tmp_path / fast_tmp_dirs.PRIVATE_ROOT_NAME / "t0"
    stale.mkdir(parents=True)
    (stale / "left-over").touch()
    first, second = PrivateTestDirs().new(tmp_path), fast_tmp_dirs.new_private_test_dir(tmp_path)
    assert stale not in (first, second)
    assert first != second
    for path in (first, second):
        assert path.parent == stale.parent
        assert not any(path.iterdir())
        assert path.stat().st_mode & 0o777 == 0o700


def test_the_suite_runs_on_the_constant_time_allocator():
    """pytest's numbered-directory hook point is the suite's allocator; a pytest upgrade that renames it fails here."""
    assert isinstance(_pytest.tmpdir.make_numbered_dir, NumberedDirAllocator)


def test_install_and_restore_round_trip():
    """Installing puts a fresh allocator in place and the returned function restores what was there before."""
    before = _pytest.tmpdir.make_numbered_dir
    restore = fast_tmp_dirs.install_fast_numbered_dirs()
    try:
        assert isinstance(_pytest.tmpdir.make_numbered_dir, NumberedDirAllocator)
        assert _pytest.tmpdir.make_numbered_dir is not before
    finally:
        restore()
    assert _pytest.tmpdir.make_numbered_dir is before


def _isolated_dirs(tmp_path: Path) -> dict[str, Path]:
    """The directories the autouse fixtures gave the running test, and its ``tmp_path``."""
    return {
        "home": Path(os.environ["HOME"]),
        "env-files": Path(env_files.env_file_dir()),
        "layered-config": config_env.user_env_path().parent,
        "tmp_path": tmp_path,
    }


def test_isolated_directories_are_distinct_empty_private_and_under_the_base_temp(tmp_path, tmp_path_factory):
    """HOME, the env-file directory and the layered-config directory are separate, empty and under the base temp."""
    dirs = _isolated_dirs(tmp_path)
    base = tmp_path_factory.getbasetemp()
    assert len(set(dirs.values())) == len(dirs)
    for path in dirs.values():
        assert path.is_dir()
        assert not any(path.iterdir())
        assert path.is_relative_to(base)
    assert dirs["env-files"].stat().st_mode & 0o777 == 0o700


#: Directories handed to earlier tests of this module in this process (each xdist worker has its own base temp).
_SEEN_DIRS: set[Path] = set()


@pytest.mark.parametrize("run", range(3))
def test_consecutive_tests_never_get_the_same_directory(run, tmp_path):
    """No test gets a HOME, env-file, layered-config or ``tmp_path`` directory an earlier test of this process got.

    The check is per process: when xdist spreads the cases over workers, each compares only against the cases that ran
    in its own worker (and its own base temp).
    """
    dirs = set(_isolated_dirs(tmp_path).values())
    assert not dirs & _SEEN_DIRS
    _SEEN_DIRS.update(dirs)
