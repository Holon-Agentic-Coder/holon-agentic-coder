"""The layered configuration end to end: `cli.main`, role-container env-files, nested runs, the doctor report and
the image's role dispatcher.

Temporary files and a fake docker only; the fake credential never reaches the argv, an echo or the doctor output.
"""

import argparse
import importlib
import json
import os
import pathlib
import shutil
import subprocess
from unittest.mock import patch

import pytest
from sandbox_executor import cli, config_env
from sandbox_executor.cli import doctor as cli_doctor
from sandbox_executor.launcher import container_env
from sandbox_executor.preflight import PreflightResult, PreflightStatus

from tests.env_file_capture import DockerEnvFileRecorder
from tests.leak_assertions import assert_no_fragment
from tests.test_config_env import fake_token, write

DISPATCHER = pathlib.Path(__file__).resolve().parent.parent / "entrypoint" / "role_dispatcher.sh"
ROLES = ("intent-creator", "planner", "executor", "review-loop")


@pytest.fixture
def token_layers(tmp_path, monkeypatch):
    """A user layer referencing a 0600 token file and a project layer overriding one user key; returns the token."""
    token = fake_token()
    token_file = write(tmp_path / "secrets" / "claude-oauth-token", token)
    user = write(tmp_path / "home" / ".holon" / ".env", f"HOLON_X=1\nHOLON_AGENT_OAUTH_TOKEN_FILE={token_file}\n")
    project = write(tmp_path / "repo" / ".env", "HOLON_X=3\nHOLON_LAYERED=project-value\n")
    monkeypatch.setattr(config_env, "user_env_path", lambda: user)
    monkeypatch.setattr(config_env, "project_env_path", lambda start: project)
    return token


def _probe() -> PreflightResult:
    return PreflightResult(
        status=PreflightStatus.OK,
        agent="pi-agent",
        model="m",
        image="holon/agent-pi",
        exit_code=0,
        output_excerpt="",
        hint="",
    )


# --- cli.main and the role containers -------------------------------------------------------------------------------


def test_main_resolves_once_and_fills_the_process_environment(token_layers, capsys):
    cli_main_mod = importlib.import_module("sandbox_executor.cli.main")
    with patch.object(cli_main_mod, "build_parser", side_effect=SystemExit(0)), pytest.raises(SystemExit):
        cli.main(["doctor"])
    assert os.environ["HOLON_X"] == "3"
    assert os.environ["HOLON_AGENT_OAUTH_TOKEN"] == token_layers
    assert "HOLON_CONFIG_RESOLVED" not in os.environ
    held = config_env.current()
    assert held is not None and held.settings["HOLON_X"].source == "project"
    captured = capsys.readouterr()
    assert_no_fragment(captured.out + captured.err, token_layers)


@pytest.mark.parametrize("role", ROLES)
def test_every_role_container_inherits_the_layers_through_the_env_file(token_layers, role, tmp_path, capsys):
    cli.resolve_layered_configuration()
    intent_file = write(tmp_path / "intent.json", "{}")
    with (
        patch("sandbox_executor.cli.git_flow.sandbox_worktree_mounts", return_value=["-v", "/work:/workspace"]),
        patch("sandbox_executor.launcher.mounts.get_agent_session_mounts", return_value=[]),
        patch("subprocess.run") as mock_run,
        patch("shutil.which", return_value="/usr/bin/docker"),
    ):
        docker = mock_run.side_effect = DockerEnvFileRecorder()
        options = {"intent_file": str(intent_file)} if role == "intent-creator" else {}
        cli.run_docker_container(role, "holon/agent-claude", ["arg"], agent_id="claude", **options)
    env_file = docker.env_files[-1]
    assert env_file["HOLON_X"] == "3"
    assert env_file["HOLON_LAYERED"] == "project-value"
    assert env_file["HOLON_CONFIG_RESOLVED"] == "1"
    assert env_file["HOLON_AGENT_OAUTH_TOKEN"] == token_layers
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env_file
    argv = " ".join(docker.argvs[-1])
    assert "HOLON_AGENT_OAUTH_TOKEN=" not in argv and "HOLON_X=" not in argv
    captured = capsys.readouterr()
    for text in (argv, captured.out, captured.err):
        assert_no_fragment(text, token_layers)


def test_a_line_break_value_stays_off_the_argv_as_a_bare_name(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLON_AGENT_OAUTH_TOKEN", "line\nbreak")
    with (
        patch("sandbox_executor.cli.git_flow.sandbox_worktree_mounts", return_value=["-v", "/work:/workspace"]),
        patch("sandbox_executor.launcher.mounts.get_agent_session_mounts", return_value=[]),
        patch("subprocess.run") as mock_run,
        patch("shutil.which", return_value="/usr/bin/docker"),
    ):
        docker = mock_run.side_effect = DockerEnvFileRecorder()
        cli.run_docker_container("planner", "holon/agent-claude", [], agent_id="claude")
    assert "HOLON_AGENT_OAUTH_TOKEN" in docker.argvs[-1]
    assert not any("break" in arg for arg in docker.argvs[-1])


def test_nested_invocation_inherits_and_forwards_the_same_values(token_layers, monkeypatch):
    cli.resolve_layered_configuration()
    outer = container_env.forwarded_env("planner", "claude", pin_workspace=False)
    for key in [key for key in os.environ if key.startswith("HOLON_")]:
        monkeypatch.delenv(key)
    for key, value in outer.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(config_env, "user_env_path", lambda: pytest.fail("a nested run must not read a layer file"))
    nested = config_env.load_effective_config()
    assert nested.inherited
    assert (nested.settings["HOLON_X"].value, nested.settings["HOLON_X"].source) == ("3", "process")
    inner = container_env.forwarded_env("planner", "claude", pin_workspace=False)
    for key in ("HOLON_X", "HOLON_LAYERED", "HOLON_AGENT_OAUTH_TOKEN", "HOLON_CONFIG_RESOLVED"):
        assert inner[key] == outer[key], key


def test_probe_uses_the_same_env_file_route(token_layers):
    from sandbox_executor import preflight

    cli.resolve_layered_configuration()
    with patch("sandbox_executor.launcher.mounts.build_network_isolation_args", return_value=[]):
        cmd, secret_env = preflight.build_probe_docker_cmd("claude", "m", "holon/agent-claude", "probe", [])
    assert secret_env["HOLON_AGENT_OAUTH_TOKEN"] == token_layers
    assert secret_env["HOLON_CONFIG_RESOLVED"] == "1"
    assert_no_fragment(" ".join(cmd), token_layers)


# --- holon doctor ---------------------------------------------------------------------------------------------------


def _doctor(as_json: bool, capsys) -> tuple[int, str]:
    args = argparse.Namespace(agent="pi", model="claude-opus-5-5", json=as_json, timeout=None)
    with patch.object(cli_doctor, "run_preflight_probe", return_value=_probe()):
        code = cli.run_doctor(args)
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_doctor_text_reports_sources_and_masks_the_secret(token_layers, capsys):
    cli.resolve_layered_configuration()
    capsys.readouterr()
    code, output = _doctor(False, capsys)
    assert code == 0
    assert "Configuration (HOLON_* settings):" in output
    assert "HOLON_X = 3   [project; shadows user]" in output
    assert "HOLON_AGENT_OAUTH_TOKEN = set (user)   [user]" in output
    assert "HOLON_AGENT = pi   [cli]" in output
    assert "HOLON_MODEL = claude-opus-5-5   [cli]" in output
    assert_no_fragment(output, token_layers)


def test_doctor_json_reports_rows_and_masks_the_secret(token_layers, capsys):
    cli.resolve_layered_configuration()
    capsys.readouterr()
    code, output = _doctor(True, capsys)
    assert code == 0
    rows = {row["key"]: row for row in json.loads(output)["configuration"]["settings"]}
    assert (rows["HOLON_X"]["source"], rows["HOLON_X"]["shadowed"]) == ("project", ["user"])
    secret = rows["HOLON_AGENT_OAUTH_TOKEN"]
    assert (secret["value"], secret["display"], secret["secret"]) == (None, "set (user)", True)
    assert_no_fragment(output, token_layers)


def test_doctor_without_a_held_resolution_loads_one(token_layers, capsys):
    code, output = _doctor(True, capsys)
    assert code == 0
    assert json.loads(output)["configuration"]["settings"]
    assert_no_fragment(output, token_layers)


def test_doctor_masks_a_url_password_and_shows_warnings(tmp_path, monkeypatch, capsys):
    user = write(tmp_path / ".env", "HOLON_PROXY_URL=http://u:pw-probe-value@proxy:3128\nPATH=/x\n", mode=0o644)
    monkeypatch.setattr(config_env, "user_env_path", lambda: user)
    cli.resolve_layered_configuration()
    capsys.readouterr()
    _code, output = _doctor(False, capsys)
    assert "HOLON_PROXY_URL = set (user; value withheld)" in output and "pw-probe-value" not in output
    assert f"WARNING: {user} has mode 0644" in output
    assert f"WARNING: {user}: ignoring PATH" in output


# --- the image's role dispatcher ------------------------------------------------------------------------------------


def _dispatch(tmp_path: pathlib.Path, **env: str) -> subprocess.CompletedProcess[str]:
    base = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "HOLON_ROOT_CA_PATH": str(tmp_path / "absent-ca.crt"),
        "HOLON_SECRET_BUNDLE_PATH": str(tmp_path / "absent-bundle.json"),
    }
    return subprocess.run(
        ["bash", str(DISPATCHER), "env"], env={**base, **env}, capture_output=True, text=True, check=True
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not available")
def test_dispatcher_maps_the_layered_token_for_claude_only(tmp_path):
    token = fake_token()
    claude = _dispatch(tmp_path, HOLON_AGENT_ID="claude", HOLON_AGENT_OAUTH_TOKEN=token)
    assert f"CLAUDE_CODE_OAUTH_TOKEN={token}" in claude.stdout.splitlines()
    assert_no_fragment(claude.stderr, token)
    pi = _dispatch(tmp_path, HOLON_AGENT_ID="pi", HOLON_AGENT_OAUTH_TOKEN=token)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in pi.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not available")
def test_dispatcher_keeps_an_explicit_claude_credential(tmp_path):
    preset, layered = fake_token(), fake_token()
    kept = _dispatch(
        tmp_path, HOLON_AGENT_ID="claude-agent", HOLON_AGENT_OAUTH_TOKEN=layered, CLAUDE_CODE_OAUTH_TOKEN=preset
    )
    assert f"CLAUDE_CODE_OAUTH_TOKEN={preset}" in kept.stdout.splitlines()
    api = _dispatch(tmp_path, HOLON_AGENT_ID="claude", HOLON_AGENT_OAUTH_TOKEN=layered, ANTHROPIC_API_KEY="api-probe")
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in api.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not available")
def test_dispatcher_maps_the_layered_token_from_a_real_planner_env_file(token_layers, tmp_path):
    cli.resolve_layered_configuration()
    with (
        patch("sandbox_executor.cli.git_flow.sandbox_worktree_mounts", return_value=["-v", "/work:/workspace"]),
        patch("sandbox_executor.launcher.mounts.get_agent_session_mounts", return_value=[]),
        patch("subprocess.run") as mock_run,
        patch("shutil.which", return_value="/usr/bin/docker"),
    ):
        docker = mock_run.side_effect = DockerEnvFileRecorder()
        cli.run_docker_container("planner", "holon/agent-claude", [], agent_id="Claude-Agent")
    env_file = docker.env_files[-1]
    assert env_file["HOLON_AGENT_ID"] == "claude"
    # Without HOLON_ROLE the dispatcher runs its argv ('env') rather than the planner, after the same mappings.
    dispatched = _dispatch(tmp_path, **{key: value for key, value in env_file.items() if key != "HOLON_ROLE"})
    assert f"CLAUDE_CODE_OAUTH_TOKEN={token_layers}" in dispatched.stdout.splitlines()
    assert_no_fragment(dispatched.stderr, token_layers)
