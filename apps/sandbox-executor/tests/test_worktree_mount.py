"""Hermetic tests for host worktree bind mounts: local git only, no Docker and no network."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.worktree_mount import (
    WorkspaceMount,
    WorkspaceMountError,
    resolve_workspace_mount,
    workspace_mount_args,
    workspace_mount_env,
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def main_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A main worktree with one commit on 'main'; cwd is moved off the real repository."""
    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "main"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


@pytest.fixture
def linked_worktree(main_repo: Path) -> Path:
    path = main_repo.parent / "linked"
    _git(main_repo, "worktree", "add", "-q", "-b", "feature-x", str(path))
    return path


def test_linked_worktree_resolves_to_its_toplevel_and_common_git_dir(main_repo, linked_worktree):
    mount = resolve_workspace_mount(str(linked_worktree / "."))

    assert mount.host_worktree == os.path.realpath(linked_worktree)
    assert mount.git_common_dir == os.path.realpath(main_repo / ".git")
    assert mount.container_workspace == SANDBOX_WORKSPACE_MOUNT


def test_linked_worktree_git_pointer_lies_under_the_mounted_common_dir(linked_worktree):
    mount = resolve_workspace_mount(str(linked_worktree))

    pointer = (linked_worktree / ".git").read_text(encoding="utf-8").strip()
    assert pointer.startswith("gitdir: ")
    gitdir = os.path.realpath(pointer.removeprefix("gitdir: "))
    assert os.path.commonpath([gitdir, mount.git_common_dir]) == mount.git_common_dir
    commondir = (Path(gitdir) / "commondir").read_text(encoding="utf-8").strip()
    assert os.path.realpath(os.path.join(gitdir, commondir)) == mount.git_common_dir


def test_linked_worktree_args_are_exactly_two_binds_with_an_identity_common_dir(linked_worktree):
    mount = resolve_workspace_mount(str(linked_worktree))

    assert workspace_mount_args(mount) == [
        "-v",
        f"{mount.host_worktree}:{SANDBOX_WORKSPACE_MOUNT}",
        "-v",
        f"{mount.git_common_dir}:{mount.git_common_dir}",
    ]


def test_main_worktree_needs_a_single_bind(main_repo):
    mount = resolve_workspace_mount(str(main_repo))

    assert mount.git_common_dir == os.path.realpath(main_repo / ".git")
    assert workspace_mount_args(mount) == ["-v", f"{mount.host_worktree}:{SANDBOX_WORKSPACE_MOUNT}"]


def test_absent_flow_worktree_is_provisioned_from_a_bare_clone_and_mounted(main_repo, tmp_path):
    bare = tmp_path / "repo.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(main_repo), str(bare)], check=True, capture_output=True)
    assert not (tmp_path / "flow-demo").exists()

    mount = resolve_workspace_mount(str(bare), slug="demo", base_branch="main")

    assert mount.host_worktree == os.path.realpath(tmp_path / "flow-demo")
    assert mount.git_common_dir == os.path.realpath(bare)
    assert _git(tmp_path / "flow-demo", "branch", "--show-current") == "flow-demo"
    assert len(workspace_mount_args(mount)) == 4


@pytest.mark.parametrize("kind", ["plain", "bare", "root"])
def test_non_worktrees_are_refused(main_repo, tmp_path, kind):
    if kind == "plain":
        target = tmp_path / "plain"
        target.mkdir()
    elif kind == "bare":
        target = tmp_path / "bare.git"
        subprocess.run(["git", "init", "-q", "--bare", str(target)], check=True, capture_output=True)
    else:
        target = Path(os.sep)

    with pytest.raises(WorkspaceMountError):
        resolve_workspace_mount(str(target))


def test_home_directory_worktree_is_refused(main_repo, monkeypatch):
    monkeypatch.setenv("HOME", str(main_repo))

    with pytest.raises(WorkspaceMountError, match="too broad"):
        resolve_workspace_mount(str(main_repo))


def test_mount_env_pins_the_workspace_to_the_mountpoint():
    mount = WorkspaceMount(host_worktree="/host/wt", git_common_dir="/host/repo/.git")

    assert workspace_mount_env(mount) == {"HOLON_TARGET_WORKTREE": SANDBOX_WORKSPACE_MOUNT}
