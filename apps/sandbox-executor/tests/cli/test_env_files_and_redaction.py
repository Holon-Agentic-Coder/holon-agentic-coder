from __future__ import annotations

import os
import shutil
import stat
import subprocess
import time
from unittest.mock import MagicMock

import pytest
from sandbox_executor import cli
from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.cli import run_docker_container
from sandbox_executor.launcher import env_files

from tests.cli._helpers import _REAL_ENV_FILE_DIR, _WORKTREE_MOUNTS, _RecordingDocker
from tests.leak_assertions import (
    CREDENTIAL_FREE_URLS,
    assert_no_fragment,
    assert_no_leak,
    make_gh_probe,
    make_probe,
    make_url_probe,
    url_userinfo_cases,
)

_BRANCH = "I-1792762862-x/_"


@pytest.fixture
def leak_env(tmp_path, monkeypatch):
    probes = {
        "GITHUB_TOKEN": make_gh_probe(),
        "HOLON_MITM_WEB_PASSWORD": make_probe("mitmweb"),
        "HOLON_AGENT_KEY": make_probe("agentkey"),
        "ANTHROPIC_API_KEY": make_probe("anthropickey"),
    }
    for key in list(os.environ):
        if key.startswith(("HOLON_", "GIT_")) or key in ("GITHUB_TOKEN", "GH_TOKEN"):
            monkeypatch.delenv(key, raising=False)
    for key, value in probes.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:myorg/myrepo.git"], check=True)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr("sandbox_executor.launcher.mounts.get_agent_session_mounts", lambda agent_id: [])
    monkeypatch.setattr(cli.git_flow, "sandbox_worktree_mounts", lambda *args, **kwargs: list(_WORKTREE_MOUNTS))
    # why: an external HOLON_PROXY_URL can carry its own credential
    proxy_password_sample = make_probe("proxypw")
    monkeypatch.setattr(
        "sandbox_executor.launcher.sidecar.get_token_reduction_mounts_and_envs",
        lambda **kwargs: ([], {"HTTPS_PROXY": f"http://proxyuser:{proxy_password_sample}@proxy.internal:3128"}),
    )
    probes["HTTPS_PROXY"] = proxy_password_sample
    return {"probes": probes, "repo": str(repo)}


def _run_planner(repo_dir: str) -> int:
    return run_docker_container(
        "planner", "holon/agent-claude", [_BRANCH, "claude-agent", "opus"], agent_id="claude", target_worktree=repo_dir
    )


@pytest.mark.parametrize(
    "typed", [("sk-ant-" + "oat", "CLAUDE_CODE_OAUTH_TOKEN"), ("sk-ant-" + "api", "ANTHROPIC_API_KEY")]
)
def test_claude_container_gets_the_discovered_keychain_value_by_type(
    leak_env, monkeypatch, capsys, fake_keychain, typed
):
    prefix, expected = typed
    value = prefix + make_probe("k")
    fake_keychain.entry = value
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    docker = _RecordingDocker()
    monkeypatch.setattr(subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    captured = capsys.readouterr()
    assert docker.env_file_contents[expected] == value
    assert "HOLON_AGENT_KEY" not in docker.container_env
    assert not ({"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"} - {expected}) & set(docker.container_env)
    for text in (captured.out, captured.err, " ".join(docker.argv)):
        assert_no_fragment(text, value)


def test_docker_echo_and_argv_carry_no_secret_fragment(leak_env, monkeypatch, capsys):
    docker = _RecordingDocker()
    monkeypatch.setattr(subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    printed_docker_command = next(
        line for line in capsys.readouterr().out.splitlines() if line.startswith("Executing: docker run")
    )
    docker_argv_text = " ".join(docker.argv)
    for value in leak_env["probes"].values():
        assert_no_fragment(printed_docker_command, value)
        assert_no_fragment(docker_argv_text, value)
    assert _BRANCH in printed_docker_command
    assert _BRANCH in docker.argv
    assert docker.env_file_contents["HOLON_TARGET_WORKTREE"] == SANDBOX_WORKSPACE_MOUNT
    assert docker.env_file_contents["HOLON_ROLE"] == "planner"
    assert not any(name.startswith("HOLON_") for name in docker.argv_env)
    assert "HOLON_" not in printed_docker_command.replace(docker.env_file, "<env-file>")
    assert "--env-file" in printed_docker_command


def test_secrets_travel_in_an_owner_only_env_file_that_is_removed(leak_env, monkeypatch):
    docker = _RecordingDocker()
    monkeypatch.setattr(subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    probes = leak_env["probes"]
    assert docker.env_file_mode == 0o600
    assert docker.env_dir_mode == 0o700
    assert docker.env_file_contents["GITHUB_TOKEN"] == probes["GITHUB_TOKEN"]
    assert docker.env_file_contents["HOLON_MITM_WEB_PASSWORD"] == probes["HOLON_MITM_WEB_PASSWORD"]
    assert docker.env_file_contents["ANTHROPIC_API_KEY"] == probes["ANTHROPIC_API_KEY"]
    assert "HOLON_AGENT_KEY" not in docker.container_env
    assert probes["HTTPS_PROXY"] in docker.env_file_contents["HTTPS_PROXY"]
    assert not os.path.exists(docker.env_file)


@pytest.mark.parametrize(
    "form",
    ["alice:{v}@proxy.corp:8080", "socks5://alice:{v}@proxy:1080", "https://alice:{v}@proxy:3128"],
    ids=["schemeless", "socks5", "https"],
)
def test_proxy_url_password_travels_only_in_the_env_file(leak_env, monkeypatch, capsys, form):
    sample = make_probe()
    proxy_value = form.format(v=sample)
    monkeypatch.setenv("HOLON_PROXY_URL", proxy_value)
    monkeypatch.setattr(
        "sandbox_executor.launcher.sidecar.get_token_reduction_mounts_and_envs",
        lambda **kwargs: ([], {"HTTP_PROXY": proxy_value, "https_proxy": proxy_value, "NO_PROXY": "localhost"}),
    )
    docker = _RecordingDocker()
    monkeypatch.setattr(subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    echo = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("Executing: docker run"))
    assert_no_leak(sample, echo, docker.argv)
    assert docker.env_file_mode == 0o600
    for name in ("HOLON_PROXY_URL", "HTTP_PROXY", "https_proxy"):
        assert docker.env_file_contents[name] == proxy_value
        assert name not in docker.argv_env
    passwordless_proxy_bypass = docker.argv_env["NO_PROXY"]
    assert passwordless_proxy_bypass == "localhost"


_URL_ENV_NAMES = ["HOLON_PROXY_URL", "HOLON_LOCAL_BASE_URL", "HOLON_MIRROR_ENDPOINT"]


@pytest.mark.parametrize(
    ("form", "separators"), [pytest.param(form, seps, id=case_id) for case_id, form, seps in url_userinfo_cases()]
)
@pytest.mark.parametrize("name", _URL_ENV_NAMES)
def test_url_userinfo_travels_only_in_the_env_file_in_every_shape(  # noqa: PLR0913
    leak_env, monkeypatch, capsys, name, form, separators
):
    value, parts = make_url_probe(form, separators)
    monkeypatch.setenv(name, value)
    docker = _RecordingDocker()
    monkeypatch.setattr(subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    echo = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("Executing: docker run"))
    for part in parts:
        assert_no_leak(part, echo, docker.argv)
    assert docker.env_file_contents[name] == value
    assert name not in docker.argv_env


@pytest.mark.parametrize("value", CREDENTIAL_FREE_URLS)
@pytest.mark.parametrize("name", _URL_ENV_NAMES)
def test_credential_free_url_travels_verbatim_in_the_env_file(leak_env, monkeypatch, capsys, name, value):
    monkeypatch.setenv(name, value)
    docker = _RecordingDocker()
    monkeypatch.setattr(subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    echo = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("Executing: docker run"))
    assert f"{name}=" not in echo
    assert name not in docker.argv_env
    assert docker.env_file_contents[name] == value


@pytest.mark.parametrize("failure", ["write", "directory"])
def test_unwritable_env_file_fails_closed_with_a_clear_error(leak_env, monkeypatch, capsys, failure):
    monkeypatch.setattr(subprocess, "run", _RecordingDocker(outcome=AssertionError("docker must not run")))

    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(env_files, "write_env_file" if failure == "write" else "env_file_dir", refuse)

    assert _run_planner(leak_env["repo"]) == 1

    err = capsys.readouterr().err
    assert "could not write the credential env-file" in err
    assert "Permission denied" in err
    for value in leak_env["probes"].values():
        assert_no_fragment(err, value)


@pytest.mark.parametrize(
    "outcome",
    [MagicMock(returncode=3), KeyboardInterrupt(), RuntimeError("docker exploded")],
    ids=["non-zero-exit", "keyboard-interrupt", "exception"],
)
def test_env_file_is_removed_however_the_run_ends(leak_env, monkeypatch, outcome):
    docker = _RecordingDocker(outcome=outcome)
    monkeypatch.setattr(subprocess, "run", docker)

    if isinstance(outcome, BaseException):
        with pytest.raises(type(outcome)):
            _run_planner(leak_env["repo"])
    else:
        assert _run_planner(leak_env["repo"]) == 3

    assert docker.env_file_mode == 0o600
    assert not os.path.exists(docker.env_file)


def test_no_git_credential_or_ssh_agent_environment_reaches_the_container(leak_env, monkeypatch):
    # why: the container never talks to the remote, so it needs no credential helper, SSH agent or repository URL
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/ssh-agent.sock")
    docker = _RecordingDocker()
    monkeypatch.setattr(subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    env = docker.container_env
    assert not [key for key in env if key.startswith("GIT_CONFIG_") or key in ("SSH_AUTH_SOCK", "HOLON_REPO_URL")]
    assert "/tmp/ssh-agent.sock" not in " ".join(docker.argv)
    assert docker.env_file_contents["GITHUB_TOKEN"] == leak_env["probes"]["GITHUB_TOKEN"]
    assert "GITHUB_TOKEN" not in docker.argv_env


def test_agent_settings_carrying_an_api_key_travel_in_the_env_file(leak_env, monkeypatch, capsys):
    provider_sample = "sk-" + "ant-" + make_probe("anthropic")
    settings = '{"apiKey":"' + provider_sample + '"}'
    monkeypatch.setenv("HOLON_AGENT_SETTINGS", settings)
    docker = _RecordingDocker()
    monkeypatch.setattr(subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    captured = capsys.readouterr()
    for text in (captured.out, captured.err, " ".join(docker.argv)):
        assert_no_fragment(text, provider_sample)
    assert docker.env_file_contents["HOLON_AGENT_SETTINGS"] == settings


def test_line_break_in_a_secret_aborts_before_docker_runs(leak_env, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "first-line\nINJECTED=1")
    monkeypatch.setattr(subprocess, "run", _RecordingDocker(outcome=AssertionError("docker must not run")))

    assert _run_planner(leak_env["repo"]) == 1

    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY" in err
    assert "first-line" not in err
    assert os.listdir(cli.env_file_dir()) == []


def test_stale_env_files_are_swept(isolated_env_file_dir):
    stale = isolated_env_file_dir / "holon-env-stale"
    fresh = isolated_env_file_dir / "holon-env-fresh"
    unrelated = isolated_env_file_dir / "keep-me"
    for path in (stale, fresh, unrelated):
        path.write_text("X=1\n")
    old = time.time() - 2 * 24 * 60 * 60
    os.utime(stale, (old, old))
    os.utime(unrelated, (old, old))

    cli.sweep_stale_env_files(str(isolated_env_file_dir))

    assert not stale.exists()
    assert fresh.exists()
    assert unrelated.exists()


def test_env_file_dir_is_tightened_to_owner_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    loose = tmp_path / ".holon" / "tmp" / "env-files"
    loose.mkdir(parents=True, mode=0o755)
    os.chmod(loose, 0o755)

    assert _REAL_ENV_FILE_DIR() == str(loose)
    assert stat.S_IMODE(os.stat(loose).st_mode) == 0o700


def test_oauth_settings_json_travels_in_the_env_file(leak_env, monkeypatch, capsys):
    settings_probe = "sk-" + "ant-" + "oat01-" + make_probe("o")
    settings = '{"env":{"CLAUDE_CODE_OAUTH_TOKEN":"' + settings_probe + '"}}'
    monkeypatch.setenv("HOLON_AGENT_SETTINGS", settings)
    docker = _RecordingDocker()
    monkeypatch.setattr(subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    captured = capsys.readouterr()
    assert_no_leak(settings_probe, captured.out, captured.err, docker.argv)
    assert docker.env_file_contents["HOLON_AGENT_SETTINGS"] == settings


def test_multiline_settings_are_named_bare_on_the_command_line(leak_env, monkeypatch, capsys):
    # why: no env-file line can carry a multi-line value, so it goes as a bare `-e NAME` with its value in docker's env
    sample = make_probe("bare")
    settings = '{\n  "model": "opus",\n  "apiKeyHelper": "' + sample + '"\n}'
    monkeypatch.setenv("HOLON_AGENT_SETTINGS", settings)
    docker = _RecordingDocker()
    monkeypatch.setattr(subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    assert docker.passthrough_env["HOLON_AGENT_SETTINGS"] == settings
    assert "HOLON_AGENT_SETTINGS" not in {**docker.argv_env, **docker.env_file_contents}
    assert_no_leak(sample, capsys.readouterr().out, docker.argv)


def test_mitm_web_password_is_neither_in_argv_nor_echoed(leak_env, monkeypatch, capsys):
    docker = _RecordingDocker()
    monkeypatch.setattr(subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    probe = leak_env["probes"]["HOLON_MITM_WEB_PASSWORD"]
    assert_no_leak(probe, capsys.readouterr().out, docker.argv)
    assert "HOLON_MITM_WEB_PASSWORD" not in docker.argv_env


def test_intent_file_error_path_leaves_no_env_file(leak_env, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", _RecordingDocker(outcome=AssertionError("docker must not run")))

    code = run_docker_container(
        "intent-creator",
        "holon/agent-claude",
        ["claude-agent", "opus"],
        agent_id="claude",
        target_worktree=leak_env["repo"],
        intent_file=os.path.join(leak_env["repo"], "missing-intent.json"),
    )

    assert code == 1
    assert "does not exist" in capsys.readouterr().err
    assert os.listdir(cli.env_file_dir()) == []


def test_redact_command_echo_names_the_env_file_but_not_its_contents(leak_env, monkeypatch, capsys):
    docker = _RecordingDocker()
    monkeypatch.setattr(subprocess, "run", docker)

    assert _run_planner(leak_env["repo"]) == 0

    echo = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("Executing: docker run"))
    assert f"--env-file {docker.env_file}" in echo
    assert_no_fragment(echo, leak_env["probes"]["GITHUB_TOKEN"])
    assert _BRANCH in echo
