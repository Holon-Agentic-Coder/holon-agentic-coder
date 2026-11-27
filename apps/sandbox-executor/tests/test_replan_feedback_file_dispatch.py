from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sandbox_executor import git_flow
from sandbox_executor.budgets import (
    ENV_REPLAN_FEEDBACK,
    ENV_REPLAN_FEEDBACK_FILE,
    read_replan_feedback,
)
from sandbox_executor.entrypoint import planner
from sandbox_executor.entrypoint.executor.run import ExecutionRun
from sandbox_executor.flow.stages.plan_support import replan_feedback_env
from sandbox_executor.launcher import containers, env_files, root_ca, run_tracking, sidecar


def _large_feedback_payload(min_bytes: int = 180 * 1024) -> str:
    header = "Previous plan exceeded the entropy budget.\n\n## Target branch merged\n"
    line = "changed/path/with/long/history/segment_0123456789abcdef.py | 42 ++++++++++++++++----------------\n"
    repeats = (min_bytes // len(line.encode("utf-8"))) + 10
    return header + (line * repeats)


@pytest.fixture
def isolated_launch_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    home = tmp_path / "home"
    home.mkdir()
    for name in list(os.environ):
        if name.startswith("HOLON_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    monkeypatch.setattr(logging.getLogger("sandbox_executor"), "handlers", [])
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}" if name == "docker" else None)

    state: dict[str, Any] = {"runs": [], "env_file_lines": [], "child_env": None, "mounted_feedback_text": None}

    def fake_run_tracked(cmd: list[str], env: dict[str, str] | None = None) -> int:
        state["runs"].append(list(cmd))
        state["child_env"] = dict(env) if env is not None else None
        if "--env-file" in cmd:
            env_file = cmd[cmd.index("--env-file") + 1]
            state["env_file_lines"] = Path(env_file).read_text(encoding="utf-8").splitlines()
        for idx, arg in enumerate(cmd):
            if arg == "-v" and idx + 1 < len(cmd):
                mount_spec = cmd[idx + 1]
                suffix = f":{containers._REPLAN_FEEDBACK_CONTAINER_PATH}:ro"
                if mount_spec.endswith(suffix):
                    host_path = mount_spec[: -len(suffix)]
                    state["mounted_feedback_host_path"] = host_path
                    state["mounted_feedback_text"] = Path(host_path).read_text(encoding="utf-8")
        return 0

    def fake_worktree_mounts(target_worktree: str | None, mountpoint: str, role: str = "review-loop") -> list[str]:
        del role
        assert target_worktree is not None
        return ["-v", f"{target_worktree}:{mountpoint}"]

    def fake_ca(cert_dir: str | None = None) -> tuple[str, str, bool]:
        del cert_dir
        return str(home / ".holon" / "certs" / "ca.crt"), str(home / ".holon" / "certs" / "ca.key"), False

    monkeypatch.setattr(run_tracking, "run_tracked", fake_run_tracked)
    monkeypatch.setattr(git_flow, "sandbox_worktree_mounts", fake_worktree_mounts)
    monkeypatch.setattr(root_ca, "_ensure_root_ca", fake_ca)
    return state


def test_replan_feedback_env_spawns_child_process_with_over_150kib_feedback_without_e2big(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(ENV_REPLAN_FEEDBACK, raising=False)
    monkeypatch.delenv(ENV_REPLAN_FEEDBACK_FILE, raising=False)

    payload = _large_feedback_payload(180 * 1024)
    expected_bytes = len(payload.encode("utf-8"))
    assert expected_bytes > 150 * 1024

    with replan_feedback_env("", payload):
        assert ENV_REPLAN_FEEDBACK not in os.environ
        feedback_path = os.environ[ENV_REPLAN_FEEDBACK_FILE]
        assert os.stat(feedback_path).st_mode & 0o777 == 0o600
        assert Path(feedback_path).read_text(encoding="utf-8") == payload
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "import os, pathlib; p = os.environ['HOLON_REPLAN_FEEDBACK_FILE']; "
                "print(len(pathlib.Path(p).read_text(encoding='utf-8').encode('utf-8')))",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert int(child.stdout.strip()) == expected_bytes

    assert not os.path.exists(feedback_path)
    assert ENV_REPLAN_FEEDBACK_FILE not in os.environ

    errored_path = ""
    with pytest.raises(RuntimeError, match="boom"), replan_feedback_env("", payload):
        errored_path = os.environ[ENV_REPLAN_FEEDBACK_FILE]
        raise RuntimeError("boom")
    assert errored_path and not os.path.exists(errored_path)


@pytest.mark.parametrize("role", ["planner", "executor"])
@pytest.mark.parametrize("via_file", [True, False])
def test_container_dispatch_mounts_over_150kib_feedback_read_only_for_planner_and_executor(
    role: str,
    via_file: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_launch_env: dict[str, Any],
) -> None:
    payload = _large_feedback_payload(180 * 1024)
    assert len(payload.encode("utf-8")) > 150 * 1024
    caller_file = tmp_path / "caller_feedback.txt"
    if via_file:
        caller_file.write_text(payload, encoding="utf-8")
        monkeypatch.setenv(ENV_REPLAN_FEEDBACK_FILE, str(caller_file))
    else:
        monkeypatch.setenv(ENV_REPLAN_FEEDBACK, payload)

    exit_code = containers.run_docker_container(
        role,
        "holon:test",
        ["--help"],
        "claude",
        target_worktree=str(tmp_path / "worktree"),
    )

    assert exit_code == 0
    assert len(isolated_launch_env["runs"]) == 1
    cmd = isolated_launch_env["runs"][0]
    host_path = isolated_launch_env["mounted_feedback_host_path"]
    assert f"{host_path}:{containers._REPLAN_FEEDBACK_CONTAINER_PATH}:ro" in cmd
    expected_env_line = f"{ENV_REPLAN_FEEDBACK_FILE}={containers._REPLAN_FEEDBACK_CONTAINER_PATH}"
    assert expected_env_line in isolated_launch_env["env_file_lines"]
    assert not any(line.startswith(f"{ENV_REPLAN_FEEDBACK}=") for line in isolated_launch_env["env_file_lines"])
    assert ENV_REPLAN_FEEDBACK not in cmd
    assert ENV_REPLAN_FEEDBACK not in isolated_launch_env["child_env"]
    assert isolated_launch_env["mounted_feedback_text"] == payload
    if via_file:
        assert caller_file.exists()
    else:
        assert not os.path.exists(host_path)


def test_planner_and_executor_read_over_150kib_feedback_from_mounted_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _large_feedback_payload(180 * 1024)
    mounted = tmp_path / "replan_feedback.txt"
    mounted.write_text(payload, encoding="utf-8")

    assert read_replan_feedback({ENV_REPLAN_FEEDBACK_FILE: str(mounted)}) == payload.strip()
    assert read_replan_feedback({ENV_REPLAN_FEEDBACK: "small inline"}) == "small inline"
    assert (
        read_replan_feedback(
            {
                ENV_REPLAN_FEEDBACK_FILE: str(tmp_path / "missing.txt"),
                ENV_REPLAN_FEEDBACK: "fallback inline",
            }
        )
        == "fallback inline"
    )

    captured: dict[str, str] = {}
    run = ExecutionRun.__new__(ExecutionRun)
    run.repo_dir = str(tmp_path)
    run.plan_branch = "I-1/P-1/_"
    run.exec_id = "E-1"
    run.args = SimpleNamespace(agent_name="claude", partial_branch=None)
    run.partial_head = None
    run.plan_data = {"plan_id": "P-1"}
    run.plan_content = "# Plan"
    run.intent_data = {"slug": "test", "goal": "test"}
    monkeypatch.setattr(run, "_supervise_authoring", lambda base, full, turn: captured.update(base=base, full=full))
    monkeypatch.setattr(run, "_record_budget_failure", lambda: None)
    monkeypatch.setattr(run, "_resolve_action_provenance", lambda: None)
    monkeypatch.setattr(run, "_record_agent_action", lambda: None)
    monkeypatch.setattr(run, "_capture_agent_output", lambda: None)
    monkeypatch.setenv(ENV_REPLAN_FEEDBACK_FILE, str(mounted))
    monkeypatch.setenv(ENV_REPLAN_FEEDBACK, "stale inline")

    run.run_turns()

    assert payload.strip() in captured["base"]
    assert ENV_REPLAN_FEEDBACK_FILE not in os.environ
    assert ENV_REPLAN_FEEDBACK not in os.environ

    assert planner.read_replan_feedback is read_replan_feedback
    monkeypatch.setenv(ENV_REPLAN_FEEDBACK_FILE, str(mounted))
    raw_feedback = planner.read_replan_feedback(os.environ)
    head_part, tail_part = raw_feedback.split("## Target branch merged", 1)
    assert head_part.strip() == "Previous plan exceeded the entropy budget."
    assert len(f"## Target branch merged{tail_part}".encode()) > 150 * 1024


def test_oversized_env_var_rejected_before_docker_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    isolated_launch_env: dict[str, Any],
) -> None:
    secret_marker = "TOP_SECRET_VALUE_MUST_NOT_LEAK"
    oversized_multiline = f"{secret_marker}\n" + ("x" * (env_files.MAX_ENV_VAR_BYTES + 1024))
    expected_size = env_files.env_var_byte_size("HOLON_OVERSIZED_MULTILINE", oversized_multiline)
    monkeypatch.setenv("HOLON_OVERSIZED_MULTILINE", oversized_multiline)

    exit_code = containers.run_docker_container(
        "planner",
        "holon:test",
        ["--help"],
        "claude",
        target_worktree=str(tmp_path / "worktree"),
    )
    err = capsys.readouterr().err
    assert exit_code == 1
    assert isolated_launch_env["runs"] == []
    assert "HOLON_OVERSIZED_MULTILINE" in err
    assert str(expected_size) in err
    assert secret_marker not in err

    monkeypatch.delenv("HOLON_OVERSIZED_MULTILINE")
    oversized_inline = f"{secret_marker}_" + ("y" * (env_files.MAX_ENV_VAR_BYTES + 2048))
    expected_inline_size = env_files.env_var_byte_size("CUSTOM_INLINE_VAR", oversized_inline)
    monkeypatch.setattr(
        sidecar,
        "get_token_reduction_mounts_and_envs",
        lambda **kwargs: ([], {"CUSTOM_INLINE_VAR": oversized_inline}),
    )

    exit_code_inline = containers.run_docker_container(
        "executor",
        "holon:test",
        ["--help"],
        "claude",
        target_worktree=str(tmp_path / "worktree"),
    )
    err_inline = capsys.readouterr().err
    assert exit_code_inline == 1
    assert isolated_launch_env["runs"] == []
    assert "CUSTOM_INLINE_VAR" in err_inline
    assert str(expected_inline_size) in err_inline
    assert secret_marker not in err_inline
