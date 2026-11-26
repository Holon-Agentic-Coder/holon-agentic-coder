"""``tests/fast_home.py``: a fresh, unique, removed isolated HOME, memory-backed only when the host offers it."""

import os

import pytest

from tests.fast_home import MEMORY_ROOT, fast_home, memory_backed


def _mount_table(tmp_path, *entries: str):
    path = tmp_path / "mounts"
    path.write_text("".join(f"{entry}\n" for entry in entries), encoding="utf-8")
    return str(path)


def test_home_is_fresh_empty_unique_and_removed(tmp_path):
    with fast_home(str(tmp_path)) as first, fast_home(str(tmp_path)) as second:
        assert os.path.isdir(first) and os.path.isdir(second)
        assert os.listdir(first) == [] and os.listdir(second) == []
        assert first != second
        os.makedirs(os.path.join(first, ".holon", "tmp"))
    assert not os.path.exists(first)
    assert not os.path.exists(second)


def test_home_is_removed_when_the_body_raises(tmp_path):
    with pytest.raises(RuntimeError), fast_home(str(tmp_path)) as home:
        raise RuntimeError("case failed")
    assert not os.path.exists(home)


def test_falls_back_when_the_memory_root_is_missing(tmp_path):
    missing = str(tmp_path / "no-such-root")
    mounts = _mount_table(tmp_path, f"shm {missing} tmpfs rw 0 0")
    with fast_home(str(tmp_path), root=missing, mounts=mounts) as home:
        assert os.path.dirname(home) == str(tmp_path)


def test_falls_back_when_the_root_is_not_tmpfs(tmp_path):
    root = tmp_path / "disk"
    root.mkdir()
    mounts = _mount_table(tmp_path, f"overlay {root} overlay rw 0 0")
    assert not memory_backed(str(root), mounts)
    with fast_home(str(tmp_path), root=str(root), mounts=mounts) as home:
        assert os.path.dirname(home) == str(tmp_path)


def test_falls_back_when_the_root_is_not_writable(tmp_path):
    root = tmp_path / "read-only"
    root.mkdir()
    mounts = _mount_table(tmp_path, f"shm {root} tmpfs rw 0 0")
    root.chmod(0o555)
    try:
        if os.access(str(root), os.W_OK):
            pytest.skip("running as a user that can write to a read-only directory")
        assert not memory_backed(str(root), mounts)
    finally:
        root.chmod(0o755)


def test_falls_back_without_a_mount_table(tmp_path):
    assert not memory_backed(MEMORY_ROOT, str(tmp_path / "no-mount-table"))
    with fast_home(str(tmp_path), mounts=str(tmp_path / "no-mount-table")) as home:
        assert os.path.dirname(home) == str(tmp_path)


def test_uses_a_listed_writable_tmpfs_root(tmp_path):
    root = tmp_path / "memory"
    root.mkdir()
    mounts = _mount_table(tmp_path, "overlay / overlay rw 0 0", f"shm {root} tmpfs rw,nosuid 0 0")
    with fast_home(str(tmp_path / "unused"), root=str(root), mounts=mounts) as home:
        assert os.path.dirname(home) == str(root)
