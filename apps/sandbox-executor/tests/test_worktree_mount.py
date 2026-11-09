"""Host-provisioned worktree bind mounts: validation, provisioning and docker mount arguments (hermetic, no network)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.worktree_mount import (
    WorktreeMount,
    WorktreeMountError,
    docker_mount_args,
    git_config_entries,
    resolve_worktree_mount,
)

_SLUG = "mount-me"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """A local bare origin, a main checkout on 'main' and a linked worktree on 'feature'."""
    root = Path(os.path.realpath(tmp_path))
    seed = root / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-q", "-m", "seed")
    origin = root / "origin.git"
    _git(root, "clone", "-q", "--bare", str(seed), str(origin))
    main = root / "main"
    _git(root, "clone", "-q", str(origin), str(main))
    linked = root / "feature"
    _git(main, "worktree", "add", "-q", "-b", "feature", str(linked))
    monkeypatch.chdir(main)
    return {"root": root, "origin": origin, "main": main, "linked": linked}


def _mount_targets(args: list[str]) -> dict[str, str]:
    """Map each ``--mount`` target to its source."""
    pairs = [args[i + 1] for i in range(0, len(args), 2) if args[i] == "--mount"]
    fields = [dict(part.split("=", 1) for part in pair.split(",")) for pair in pairs]
    assert all(f["type"] == "bind" for f in fields)
    return {f["target"]: f["source"] for f in fields}


def test_existing_linked_worktree_resolves_to_real_paths_and_survives(repo: dict[str, Path]) -> None:
    linked = repo["linked"]
    (linked / "scratch.txt").write_text("uncommitted\n", encoding="utf-8")
    inode = os.stat(linked).st_ino

    mount = resolve_worktree_mount(str(linked))

    assert mount == WorktreeMount(str(linked), str(repo["main"] / ".git"), SANDBOX_WORKSPACE_MOUNT)
    assert os.stat(linked).st_ino == inode
    assert (linked / "scratch.txt").read_text(encoding="utf-8") == "uncommitted\n"


def test_absent_flow_worktree_is_provisioned_then_mounted(repo: dict[str, Path]) -> None:
    flow = repo["root"] / f"flow-{_SLUG}"

    mount = resolve_worktree_mount(str(flow), slug=_SLUG)

    assert mount.host_worktree == str(flow)
    assert _git(flow, "symbolic-ref", "--short", "HEAD") == f"flow-{_SLUG}"


def test_default_branch_worktree_is_swapped_for_its_flow_sibling(repo: dict[str, Path]) -> None:
    mount = resolve_worktree_mount(str(repo["main"]), slug=_SLUG)

    assert mount.host_worktree == str(repo["root"] / f"flow-{_SLUG}")
    assert _git(repo["main"], "symbolic-ref", "--short", "HEAD") == "main"


@pytest.mark.parametrize("which", ["origin", "plain", "main", "missing"])
def test_unusable_paths_are_refused(repo: dict[str, Path], which: str) -> None:
    plain = repo["root"] / "plain"
    plain.mkdir()
    path = {"origin": repo["origin"], "plain": plain, "main": repo["main"], "missing": repo["root"] / "gone"}[which]

    with pytest.raises(WorktreeMountError):
        resolve_worktree_mount(str(path))


def test_detached_and_subdirectory_worktrees_are_refused(repo: dict[str, Path]) -> None:
    sub = repo["linked"] / "sub"
    sub.mkdir()
    with pytest.raises(WorktreeMountError, match="subdirectory"):
        resolve_worktree_mount(str(sub))
    _git(repo["linked"], "checkout", "-q", "--detach")
    with pytest.raises(WorktreeMountError, match="detached"):
        resolve_worktree_mount(str(repo["linked"]))


@pytest.mark.parametrize(
    "path", ["relative/worktree", "/home/holon", "/home/holon/.holon-sandbox/workspace", "/usr/src/x", "/tmp/a,b"]
)
def test_relative_reserved_and_comma_paths_are_refused(path: str) -> None:
    with pytest.raises(WorktreeMountError):
        resolve_worktree_mount(path)


def test_mount_args_bind_the_worktree_twice_and_the_common_dir(repo: dict[str, Path]) -> None:
    mount = resolve_worktree_mount(str(repo["linked"]))

    args = docker_mount_args(mount)
    targets = _mount_targets(args)

    common = str(repo["main"] / ".git")
    assert targets == {
        SANDBOX_WORKSPACE_MOUNT: str(repo["linked"]),
        str(repo["linked"]): str(repo["linked"]),
        common: common,
    }
    gitdir = (repo["linked"] / ".git").read_text(encoding="utf-8").removeprefix("gitdir:").strip()
    assert os.path.realpath(gitdir).startswith(common + "/")
    back_pointer = (Path(gitdir) / "gitdir").read_text(encoding="utf-8").strip()
    assert os.path.dirname(os.path.realpath(back_pointer)) in targets
    assert "-v" not in args
    assert not any("volume" in arg for arg in args)


def test_main_checkout_needs_no_common_dir_mount(repo: dict[str, Path]) -> None:
    _git(repo["main"], "checkout", "-q", "-b", "topic")

    targets = _mount_targets(docker_mount_args(resolve_worktree_mount(str(repo["main"]))))

    assert set(targets) == {SANDBOX_WORKSPACE_MOUNT, str(repo["main"])}


def test_git_config_entries_disable_gc_pruning_and_trust_both_paths(repo: dict[str, Path]) -> None:
    mount = resolve_worktree_mount(str(repo["linked"]))

    entries = git_config_entries(mount)

    assert ("gc.auto", "0") in entries
    assert ("gc.worktreePruneExpire", "never") in entries
    assert {value for key, value in entries if key == "safe.directory"} == {
        SANDBOX_WORKSPACE_MOUNT,
        str(repo["linked"]),
    }
