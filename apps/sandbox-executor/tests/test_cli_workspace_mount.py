"""run_docker_container and dispatch_role_agent plumbing for host-provisioned worktree bind mounts."""

import os
from unittest.mock import MagicMock, patch

from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.cli import dispatch_role_agent, run_docker_container
from sandbox_executor.worktree_mount import WorkspaceMount, workspace_mount_args


def _remove_argv_pair(argv: list[str], pair: list[str]) -> list[str]:
    """Return ``argv`` with the first adjacent occurrence of ``pair`` removed; fail if it is absent."""
    for idx in range(len(argv) - 1):
        if argv[idx : idx + 2] == pair:
            return argv[:idx] + argv[idx + 2 :]
    raise AssertionError(f"expected pair {pair} in docker argv: {argv}")


_LINKED_MOUNT_KWARGS = {"host_worktree": "/host/flow-x", "git_common_dir": "/host/repo/.git"}


def test_workspace_mount_bind_mounts_worktree_and_git_dir_and_pins_the_workspace():
    """A mount adds exactly its binds and the pinned workspace env, over a forwarded host value; nothing else."""

    mount = WorkspaceMount(**_LINKED_MOUNT_KWARGS)
    captured: list[list[str]] = []

    def record_run(cmd, *args, **kwargs):
        captured.append(list(cmd))
        return MagicMock(returncode=0)

    with (
        patch("sandbox_executor.cli.resolve_target_repo", return_value=("git@github.com:test/repo.git", None)),
        patch("sandbox_executor.cli.get_agent_session_mounts", return_value=["-v", "/mock:/mock"]),
        patch("subprocess.run", side_effect=record_run),
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": "/home/dev/own-checkout"}, clear=True),
    ):
        args = ["branch", "fake-agent", "fake-model"]
        assert run_docker_container("review-loop", "holon/agent-claude", list(args), agent_id="claude") == 0
        assert (
            run_docker_container(
                "review-loop", "holon/agent-claude", list(args), agent_id="claude", workspace_mount=mount
            )
            == 0
        )

    baseline, mounted = captured
    binds = workspace_mount_args(mount)
    assert binds == [
        "--mount",
        f"type=bind,source=/host/flow-x,target={SANDBOX_WORKSPACE_MOUNT}",
        "--mount",
        "type=bind,source=/host/repo/.git,target=/host/repo/.git",
        "--mount",
        "type=bind,source=/host/repo/.git/hooks,target=/host/repo/.git/hooks,readonly",
        "--mount",
        "type=bind,source=/host/repo/.git/config,target=/host/repo/.git/config,readonly",
    ]
    # Guard every isolation flag: removing the binds and swapping the pinned worktree variable back restores the
    # baseline argv byte for byte, so nothing else (``--rm``, ``--cap-drop=ALL``, metadata sinkholes, env) changed.
    reduced = mounted
    for idx in range(0, len(binds), 2):
        reduced = _remove_argv_pair(reduced, binds[idx : idx + 2])
    pinned = f"HOLON_TARGET_WORKTREE={SANDBOX_WORKSPACE_MOUNT}"
    assert [a for a in mounted if a.startswith("HOLON_TARGET_WORKTREE=")] == [pinned]
    assert [a if a != pinned else "HOLON_TARGET_WORKTREE=/home/dev/own-checkout" for a in reduced] == baseline
    assert "--rm" in mounted
    assert "--cap-drop=ALL" in mounted
    assert "--security-opt=no-new-privileges" in mounted
    # No named volume: no `docker volume` subcommand ran and no bind source is a bare volume name.
    assert all(cmd[:2] != ["docker", "volume"] for cmd in captured)
    sources = [mounted[i + 1].split(":", 1)[0] for i, a in enumerate(mounted) if a == "-v"]
    sources += [mounted[i + 1].split(",")[1].removeprefix("source=") for i, a in enumerate(mounted) if a == "--mount"]
    assert all(os.path.isabs(source) for source in sources), sources


def test_workspace_mount_run_fails_when_the_container_changes_trusted_git_metadata(tmp_path, capsys):
    """A config.worktree planted during the run fails it before the host's git can honour it."""

    common = tmp_path / "repo" / ".git"
    (common / "hooks").mkdir(parents=True)
    (common / "config").write_text("[core]\n", encoding="utf-8")
    (tmp_path / "wt").mkdir()
    mount = WorkspaceMount(host_worktree=str(tmp_path / "wt"), git_common_dir=str(common))

    def tampering_run(cmd, *args, **kwargs):
        (common / "config.worktree").write_text("[core]\n\tfsmonitor = touch /tmp/pwned\n", encoding="utf-8")
        return MagicMock(returncode=0)

    with (
        patch("sandbox_executor.cli.resolve_target_repo", return_value=("git@github.com:test/repo.git", None)),
        patch("sandbox_executor.cli.get_agent_session_mounts", return_value=[]),
        patch("subprocess.run", side_effect=tampering_run),
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch.dict(os.environ, {}, clear=True),
    ):
        args = ["branch", "fake-agent", "fake-model"]
        assert (
            run_docker_container("review-loop", "holon/agent-claude", args, agent_id="claude", workspace_mount=mount)
            == 1
        )

    assert "config.worktree" in capsys.readouterr().err


@patch("sandbox_executor.cli.run_docker_container", return_value=0)
def test_dispatch_role_agent_forwards_the_workspace_mount(mock_run_container):
    """dispatch_role_agent hands the mount to run_docker_container unchanged and never derives one itself."""

    mount = WorkspaceMount(**_LINKED_MOUNT_KWARGS)
    positional = ["I-1-x/P-1/E-1/_", "claude-agent", "claude-opus-5-5"]

    assert dispatch_role_agent("review-loop", "claude-agent", None, positional, workspace_mount=mount) == 0
    assert dispatch_role_agent("review-loop", "claude-agent", None, positional, target_worktree="/repo") == 0

    with_mount, without_mount = mock_run_container.call_args_list
    assert with_mount.kwargs["workspace_mount"] is mount
    assert "workspace_mount" not in without_mount.kwargs
