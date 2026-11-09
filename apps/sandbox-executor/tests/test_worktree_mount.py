"""Hermetic tests for host worktree bind mounts: local git only, no Docker and no network."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.worktree_mount import (
    WorkspaceMount,
    WorkspaceMountError,
    guarded_run,
    resolve_workspace_mount,
    snapshot_git_metadata,
    verify_git_metadata,
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


def _bind(source: str, target: str, readonly: bool = False) -> list[str]:
    return ["--mount", f"type=bind,source={source},target={target}" + (",readonly" if readonly else "")]


def test_linked_worktree_args_bind_the_common_dir_with_read_only_hooks_and_config(linked_worktree):
    mount = resolve_workspace_mount(str(linked_worktree))
    common = mount.git_common_dir

    assert workspace_mount_args(mount) == [
        *_bind(mount.host_worktree, SANDBOX_WORKSPACE_MOUNT),
        *_bind(common, common),
        *_bind(f"{common}/hooks", f"{common}/hooks", readonly=True),
        *_bind(f"{common}/config", f"{common}/config", readonly=True),
    ]


def test_main_worktree_needs_a_single_bind_plus_read_only_in_tree_hooks_and_config(main_repo):
    mount = resolve_workspace_mount(str(main_repo))

    assert mount.git_common_dir == os.path.realpath(main_repo / ".git")
    assert workspace_mount_args(mount) == [
        *_bind(mount.host_worktree, SANDBOX_WORKSPACE_MOUNT),
        *_bind(f"{mount.git_common_dir}/hooks", f"{SANDBOX_WORKSPACE_MOUNT}/.git/hooks", readonly=True),
        *_bind(f"{mount.git_common_dir}/config", f"{SANDBOX_WORKSPACE_MOUNT}/.git/config", readonly=True),
    ]


def test_missing_hooks_dir_is_created_so_the_read_only_overlay_has_a_source(main_repo):
    hooks = main_repo / ".git" / "hooks"
    shutil.rmtree(hooks, ignore_errors=True)

    resolve_workspace_mount(str(main_repo))

    assert hooks.is_dir()


def test_paths_with_colons_are_kept_whole_and_commas_are_refused():
    colon = WorkspaceMount(host_worktree="/host/a:b", git_common_dir="/host/a:b/.git")
    assert workspace_mount_args(colon)[1] == f"type=bind,source=/host/a:b,target={SANDBOX_WORKSPACE_MOUNT}"

    with pytest.raises(WorkspaceMountError, match="','"):
        workspace_mount_args(WorkspaceMount(host_worktree="/host/a,b", git_common_dir="/host/a,b/.git"))


def test_absent_flow_worktree_is_provisioned_from_a_bare_clone_and_mounted(main_repo, tmp_path):
    bare = tmp_path / "repo.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(main_repo), str(bare)], check=True, capture_output=True)
    assert not (tmp_path / "flow-demo").exists()

    mount = resolve_workspace_mount(str(bare), slug="demo", base_branch="main")

    assert mount.host_worktree == os.path.realpath(tmp_path / "flow-demo")
    assert mount.git_common_dir == os.path.realpath(bare)
    assert _git(tmp_path / "flow-demo", "branch", "--show-current") == "flow-demo"
    assert len(workspace_mount_args(mount)) == 8


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


def _gitdir(worktree: Path) -> Path:
    return Path((worktree / ".git").read_text(encoding="utf-8").strip().removeprefix("gitdir: "))


@pytest.mark.parametrize(
    "tamper",
    [
        "rewrite_pointer",
        "rewrite_commondir",
        "plant_config_worktree",
        "plant_common_config_worktree",
        "plant_hook",
        "plant_fifo_config_worktree",
        "swap_common_dir",
    ],
)
def test_container_tampering_with_git_metadata_is_refused(main_repo, linked_worktree, tamper):
    mount = resolve_workspace_mount(str(linked_worktree))
    baseline = snapshot_git_metadata(mount)
    common = Path(mount.git_common_dir)
    evil = "[core]\n\tfsmonitor = touch /tmp/pwned\n"

    if tamper == "rewrite_pointer":
        (linked_worktree / ".git").write_text(f"gitdir: {linked_worktree}/.evil\n", encoding="utf-8")
    elif tamper == "rewrite_commondir":
        (_gitdir(linked_worktree) / "commondir").write_text("../../../evil\n", encoding="utf-8")
    elif tamper == "plant_config_worktree":
        (_gitdir(linked_worktree) / "config.worktree").write_text(evil, encoding="utf-8")
    elif tamper == "plant_common_config_worktree":
        (common / "config.worktree").write_text(evil, encoding="utf-8")
    elif tamper == "plant_hook":
        (common / "hooks" / "post-checkout").write_text("#!/bin/sh\n", encoding="utf-8")
    elif tamper == "plant_fifo_config_worktree":
        # A FIFO must be recorded by its type, never opened for reading (that would hang the host).
        os.mkfifo(_gitdir(linked_worktree) / "config.worktree")
    else:
        shutil.copytree(common, common.with_name(".git-new"))
        common.rename(common.with_name(".git-old"))
        common.with_name(".git-new").rename(common)

    with pytest.raises(WorkspaceMountError, match="changed git metadata"):
        verify_git_metadata(mount, baseline)


def test_ordinary_git_work_in_the_container_passes_verification(main_repo, linked_worktree):
    mount = resolve_workspace_mount(str(linked_worktree))
    baseline = snapshot_git_metadata(mount)

    (linked_worktree / "new.txt").write_text("x\n", encoding="utf-8")
    _git(linked_worktree, "add", "new.txt")
    _git(linked_worktree, "commit", "-q", "-m", "work")

    verify_git_metadata(mount, baseline)


def test_ordinary_commits_in_a_linked_worktree_named_hooks_pass_verification(main_repo):
    hooks_named = main_repo.parent / "hooks"
    _git(main_repo, "worktree", "add", "-q", "-b", "feature-hooks", str(hooks_named))
    mount = resolve_workspace_mount(str(hooks_named))
    baseline = snapshot_git_metadata(mount)

    (hooks_named / "new.txt").write_text("x\n", encoding="utf-8")
    _git(hooks_named, "add", "new.txt")
    _git(hooks_named, "commit", "-q", "-m", "work")

    verify_git_metadata(mount, baseline)


@pytest.mark.parametrize("name", ["logs", "refs", "objects", "lfs"])
def test_tampering_in_a_linked_worktree_named_like_bulk_data_is_refused(main_repo, name):
    worktree = main_repo.parent / name
    _git(main_repo, "worktree", "add", "-q", "-b", f"feature-{name}", str(worktree))
    mount = resolve_workspace_mount(str(worktree))
    baseline = snapshot_git_metadata(mount)

    (_gitdir(worktree) / "commondir").write_text("../../../evil\n", encoding="utf-8")

    with pytest.raises(WorkspaceMountError, match="changed git metadata"):
        verify_git_metadata(mount, baseline)


def test_linked_worktree_whose_common_dir_overlaps_the_workspace_mount_is_refused(main_repo, monkeypatch):
    linked = main_repo.parent / "linked"
    _git(main_repo, "worktree", "add", "-q", "-b", "feature-x", str(linked))
    monkeypatch.setattr("sandbox_executor.worktree_mount.SANDBOX_WORKSPACE_MOUNT", str(main_repo.resolve()))

    with pytest.raises(WorkspaceMountError, match="overlaps the container workspace"):
        resolve_workspace_mount(str(linked))


def test_guarded_run_returns_the_run_exit_code_or_fails_on_tampering(linked_worktree, capsys):
    mount = resolve_workspace_mount(str(linked_worktree))
    hook = Path(mount.git_common_dir) / "hooks" / "post-checkout"

    assert guarded_run(None, lambda: 7) == 7
    assert guarded_run(mount, lambda: 3) == 3
    assert guarded_run(mount, lambda: hook.write_text("#!/bin/sh\n", encoding="utf-8") and 0) == 1
    assert "changed git metadata" in capsys.readouterr().err
