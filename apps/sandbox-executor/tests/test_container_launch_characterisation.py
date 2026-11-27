"""Golden characterisation of the role-container launch, pinned before the launcher left the CLI package.

Every scenario drives a public entry point re-exported by `sandbox_executor.cli` and patches only stdlib module
attributes (`subprocess.run`, `shutil.which`, `socket.create_connection`, `tempfile.mkstemp`, `sys.stdin`,
`sys.platform`) or domain modules (`git_flow`, `launcher.root_ca`, `launcher.run_tracking`), never a `cli.<function>`
name, so the same assertions hold wherever the launch code lives. Each launch pins the docker argv (the redacted
`Executing:` line included), the sorted `--env-file` lines and the exit code, after run-specific paths are swapped for
placeholders.
"""

import io
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sandbox_executor import cli, git_flow
from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.budgets import StageTimer, active_stage_timer
from sandbox_executor.launcher import root_ca, run_tracking

# Synthetic credentials only: a fixed filler after each real-looking prefix.
GITHUB_TOKEN = "ghp_" + "A1b2C3d4" * 5
URL_PASSWORD = "s3cretPassw0rd"
CLAUDE_API_KEY = "sk-ant-api03-" + "x" * 40
WORKTREE = "/work/flow-x"


@dataclass
class Launch:
    """What one launch produced, with run-specific values replaced by placeholders."""

    code: int
    docker_runs: list[list[str]]
    env_file_lines: list[str]
    stdout: str
    stderr: str


class _ProxyConnection:
    """A proxy connection that answers the readiness request with an HTTP status line."""

    def __enter__(self) -> "_ProxyConnection":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def sendall(self, data: bytes) -> None:
        del data

    def recv(self, size: int) -> bytes:
        del size
        return b"HTTP/1.1 502 Bad Gateway\r\n"

    def close(self) -> None:
        return None


@pytest.fixture
def launch_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Hermetic launch surroundings: fake docker, a temp home, a fixed platform and a recording `subprocess.run`."""
    home = tmp_path / "home"
    home.mkdir()
    # The suite-wide isolation keeps the harness's own HOLON_* names (a simulated sandbox sets HOLON_ROLE and
    # HOLON_IN_SANDBOX); the launch forwards every HOLON_* variable, so each scenario starts from none.
    for name in list(os.environ):
        if name.startswith("HOLON_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    # An earlier test may have run `configure_cli_logging`, whose handler would add INFO lines to stderr.
    monkeypatch.setattr(logging.getLogger("sandbox_executor"), "handlers", [])
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}" if name == "docker" else None)
    state: dict[str, Any] = {"runs": [], "env_files": [], "tmp": str(tmp_path), "worktree_calls": []}

    def fake_run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        state["runs"].append(list(cmd))
        if "--env-file" in cmd:
            env_file = cmd[cmd.index("--env-file") + 1]
            state["env_files"].append(env_file)
            state["env_file_lines"] = Path(env_file).read_text(encoding="utf-8").splitlines()
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def fake_worktree_mounts(target_worktree: str | None, mountpoint: str, role: str = "review-loop") -> list[str]:
        state["worktree_calls"].append((target_worktree, mountpoint, role))
        if target_worktree is None:
            raise RuntimeError(f"the {role} role needs a host worktree; pass --target-worktree")
        return ["-v", f"{target_worktree}:{mountpoint}"]

    def fake_ca(cert_dir: str | None = None) -> tuple[str, str, bool]:
        del cert_dir
        return str(home / ".holon" / "certs" / "ca.crt"), str(home / ".holon" / "certs" / "ca.key"), False

    monkeypatch.setattr(subprocess, "run", fake_run)
    # The role container's client is tracked through `run_tracked`; route it to the recording `subprocess.run`.
    monkeypatch.setattr(run_tracking, "run_tracked", lambda cmd, env=None: subprocess.run(cmd, env=env).returncode)
    monkeypatch.setattr(git_flow, "sandbox_worktree_mounts", fake_worktree_mounts)
    monkeypatch.setattr(root_ca, "_ensure_root_ca", fake_ca)
    return state


def _normalise(text: str, state: dict[str, Any]) -> str:
    """Swap the env-file path, the temp root and generated directory suffixes for stable placeholders."""
    for env_file in state["env_files"]:
        text = text.replace(env_file, "<ENV_FILE>")
    text = text.replace(state["tmp"], "<TMP>")
    return re.sub(r"holon-pi-agent-[A-Za-z0-9_]+", "holon-pi-agent-<ID>", text)


def _launch(state: dict[str, Any], capsys: pytest.CaptureFixture[str], call: Any) -> Launch:
    """Run `call` under a fixed 600-second stage timer and return its normalised record."""
    timer = StageTimer("plan", 600.0, clock=lambda: 1000.0)
    with active_stage_timer(timer):
        code = call()
    out, err = capsys.readouterr()
    docker_runs = [[_normalise(arg, state) for arg in run] for run in state["runs"] if run[:2] == ["docker", "run"]]
    env_lines = sorted(_normalise(line, state) for line in state.get("env_file_lines", []))
    return Launch(code, docker_runs, env_lines, _normalise(out, state), _normalise(err, state))


def _assert_no_secret_on_argv(launch: Launch, *secrets: str) -> None:
    """No credential value may appear on any recorded argv or on stdout."""
    for secret in secrets:
        assert all(secret not in arg for run in launch.docker_runs for arg in run)
        assert secret not in launch.stdout


def _check(name: str, launch: Launch) -> None:
    """Compare a launch record with its pinned literal."""
    assert launch == EXPECTED[name]


def _argv(command: str) -> list[str]:
    """Split a pinned command line; no pinned argv element contains a space."""
    return command.split(" ")


def _executing(command: str) -> str:
    """The stdout line printed just before `docker run`; none of the pinned argvs carries a redactable value."""
    return f"Executing: {command}\n"


_PLANNER_CLAUDE_ARGV = (
    "docker run --env-file <ENV_FILE> --rm -i -e DOCKER_CLI_HINTS=false -e UV_FROZEN=1 -e "
    "UV_PROJECT_ENVIRONMENT=/home/holon/.cache/holon-venv --add-host host.docker.internal:host-gateway -v "
    "/work/flow-x:/home/holon/.holon-sandbox/workspace --cap-drop=ALL --security-opt=no-new-privileges "
    "--add-host 169.254.169.254:127.0.0.1 --add-host metadata.google.internal:127.0.0.1 --add-host "
    "instance-data:127.0.0.1 holon/agent-claude plan-branch claude-agent opus"
)

_EXECUTOR_RESULT_FILE_ARGV = (
    "docker run --env-file <ENV_FILE> --rm -i -e DOCKER_CLI_HINTS=false -e UV_FROZEN=1 -e "
    "UV_PROJECT_ENVIRONMENT=/home/holon/.cache/holon-venv --add-host host.docker.internal:host-gateway -v "
    "<TMP>/result.json:/tmp/review_result.json -v /work/flow-x:/home/holon/.holon-sandbox/workspace "
    "--cap-drop=ALL --security-opt=no-new-privileges --add-host 169.254.169.254:127.0.0.1 --add-host "
    "metadata.google.internal:127.0.0.1 --add-host instance-data:127.0.0.1 holon/agent-codex exec-branch "
    "codex-agent gpt"
)

_INTENT_CREATOR_ARGV = (
    "docker run --env-file <ENV_FILE> --rm -i -e DOCKER_CLI_HINTS=false -e UV_FROZEN=1 -e "
    "UV_PROJECT_ENVIRONMENT=/home/holon/.cache/holon-venv --add-host host.docker.internal:host-gateway -v "
    "<TMP>/intent.json:/tmp/intent.json:ro -v /work/flow-x:/home/holon/.holon-sandbox/workspace "
    "--cap-drop=ALL --security-opt=no-new-privileges --add-host 169.254.169.254:127.0.0.1 --add-host "
    "metadata.google.internal:127.0.0.1 --add-host instance-data:127.0.0.1 holon/orchestrator --slug x"
)

_PI_LOCAL_LLM_ARGV = (
    "docker run --env-file <ENV_FILE> --rm -i -e DOCKER_CLI_HINTS=false -e UV_FROZEN=1 -e "
    "UV_PROJECT_ENVIRONMENT=/home/holon/.cache/holon-venv --add-host host.docker.internal:host-gateway -v "
    "<TMP>/home/.holon/tmp/holon-pi-agent-<ID>:/home/holon/.holon-pi-agent:rw -e "
    "PI_CODING_AGENT_DIR=/home/holon/.holon-pi-agent -v /work/flow-x:/home/holon/.holon-sandbox/workspace "
    "--cap-drop=ALL --security-opt=no-new-privileges --add-host 169.254.169.254:127.0.0.1 --add-host "
    "metadata.google.internal:127.0.0.1 --add-host instance-data:127.0.0.1 holon/agent-pi exec-branch "
    "pi-agent qwen3:test"
)

_TOKEN_REDUCE_ATTACH_ARGV = (
    "docker run --env-file <ENV_FILE> --rm -i -e DOCKER_CLI_HINTS=false -e UV_FROZEN=1 -e "
    "UV_PROJECT_ENVIRONMENT=/home/holon/.cache/holon-venv --add-host host.docker.internal:host-gateway -v "
    "<TMP>/home/.holon/certs/ca.crt:/usr/local/share/ca-certificates/ca.crt:ro -e "
    "NO_PROXY=localhost,127.0.0.1,::1,169.254.169.254 -e no_proxy=localhost,127.0.0.1,::1,169.254.169.254 "
    "-e NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/ca.crt -e "
    "SSL_CERT_FILE=/home/holon/.holon-ca-bundle.crt -e "
    "REQUESTS_CA_BUNDLE=/home/holon/.holon-ca-bundle.crt -e "
    "CURL_CA_BUNDLE=/home/holon/.holon-ca-bundle.crt -v /work/flow-x:/home/holon/.holon-sandbox/workspace "
    "--cap-drop=ALL --security-opt=no-new-privileges --add-host 169.254.169.254:127.0.0.1 --add-host "
    "metadata.google.internal:127.0.0.1 --add-host instance-data:127.0.0.1 holon/agent-codex b"
)

EXPECTED: dict[str, Launch] = {
    "planner_claude": Launch(
        code=0,
        docker_runs=[_argv(_PLANNER_CLAUDE_ARGV)],
        env_file_lines=[
            f"ANTHROPIC_API_KEY={CLAUDE_API_KEY}",
            f"GITHUB_TOKEN={GITHUB_TOKEN}",
            "HOLON_AGENT_ID=claude",
            "HOLON_CONFIG_RESOLVED=1",
            f"HOLON_MIRROR_URL=https://holon:{URL_PASSWORD}@mirror.example/repo",
            "HOLON_ROLE=planner",
            "HOLON_SKIP_PREFLIGHT=1",
            "HOLON_STAGE_BUDGET_SECONDS=540",
            "HOLON_TARGET_WORKTREE=/home/holon/.holon-sandbox/workspace",
            "HOLON_TRACE=1",
        ],
        stdout=_executing(_PLANNER_CLAUDE_ARGV),
        stderr="",
    ),
    "executor_result_file": Launch(
        code=0,
        docker_runs=[_argv(_EXECUTOR_RESULT_FILE_ARGV)],
        env_file_lines=[
            f"GITHUB_TOKEN={GITHUB_TOKEN}",
            "HOLON_AGENT_ID=codex",
            "HOLON_CONFIG_RESOLVED=1",
            "HOLON_ROLE=executor",
            "HOLON_SKIP_PREFLIGHT=1",
            "HOLON_STAGE_BUDGET_SECONDS=540",
            "HOLON_TARGET_WORKTREE=/home/holon/.holon-sandbox/workspace",
        ],
        stdout=_executing(_EXECUTOR_RESULT_FILE_ARGV),
        stderr="",
    ),
    "intent_creator": Launch(
        code=0,
        docker_runs=[_argv(_INTENT_CREATOR_ARGV)],
        env_file_lines=[
            "HOLON_AGENT_ID=antigravity",
            "HOLON_CONFIG_RESOLVED=1",
            "HOLON_ROLE=intent-creator",
            "HOLON_SKIP_PREFLIGHT=1",
            "HOLON_STAGE_BUDGET_SECONDS=540",
            "HOLON_TARGET_WORKTREE=/home/holon/.holon-sandbox/workspace",
        ],
        stdout=_executing(_INTENT_CREATOR_ARGV),
        stderr="",
    ),
    "intent_creator_missing_file": Launch(
        code=1,
        docker_runs=[],
        env_file_lines=[],
        stdout="",
        stderr="Error: Intent file '<TMP>/missing.json' does not exist.\n",
    ),
    "intent_creator_no_file": Launch(
        code=1,
        docker_runs=[],
        env_file_lines=[],
        stdout="",
        stderr="Error: Intent file is mandatory for intent creation.\n",
    ),
    "planner_no_worktree": Launch(
        code=1,
        docker_runs=[],
        env_file_lines=[],
        stdout="",
        stderr="Error: the planner role needs a host worktree; pass --target-worktree\n",
    ),
    "docker_missing": Launch(
        code=1,
        docker_runs=[],
        env_file_lines=[],
        stdout="",
        stderr="Error: 'docker' CLI command not found. Please install Docker.\n",
    ),
    "pi_local_llm": Launch(
        code=0,
        docker_runs=[_argv(_PI_LOCAL_LLM_ARGV)],
        env_file_lines=[
            f"GITHUB_TOKEN={GITHUB_TOKEN}",
            "HOLON_AGENT_ID=pi",
            "HOLON_AGENT_PROVIDER=vmlx",
            "HOLON_CONFIG_RESOLVED=1",
            "HOLON_LOCAL_BASE_URL=http://localhost:8081/v1",
            "HOLON_LOCAL_CONTEXT_WINDOW=32768",
            "HOLON_LOCAL_LLM=1",
            "HOLON_LOCAL_MODELS=qwen3:test",
            "HOLON_LOCAL_PROVIDER=vmlx",
            "HOLON_ROLE=executor",
            "HOLON_SKIP_PREFLIGHT=1",
            "HOLON_STAGE_BUDGET_SECONDS=540",
            "HOLON_TARGET_WORKTREE=/home/holon/.holon-sandbox/workspace",
        ],
        stdout=_executing(_PI_LOCAL_LLM_ARGV),
        stderr="",
    ),
    "env_file_failure": Launch(
        code=1,
        docker_runs=[],
        env_file_lines=[],
        stdout="",
        stderr="Error: could not write the credential env-file: [Errno 13] Permission denied: '/denied/env-files'.\n",
    ),
    "env_file_line_break": Launch(
        code=1,
        docker_runs=[],
        env_file_lines=[],
        stdout="",
        stderr="Error: the value of GITHUB_TOKEN contains a line break and cannot be passed to the container.\n",
    ),
    "token_reduce_attach": Launch(
        code=0,
        docker_runs=[_argv(_TOKEN_REDUCE_ATTACH_ARGV)],
        env_file_lines=[
            "HOLON_AGENT_ID=codex",
            "HOLON_CONFIG_RESOLVED=1",
            f"HOLON_PROXY_URL=http://proxy:{URL_PASSWORD}@proxy.example:3128",
            "HOLON_ROLE=executor",
            "HOLON_SKIP_PREFLIGHT=1",
            "HOLON_STAGE_BUDGET_SECONDS=540",
            "HOLON_TARGET_WORKTREE=/home/holon/.holon-sandbox/workspace",
            "HOLON_TOKEN_REDUCE=1",
            f"HTTPS_PROXY=http://proxy:{URL_PASSWORD}@proxy.example:3128",
            f"HTTP_PROXY=http://proxy:{URL_PASSWORD}@proxy.example:3128",
            f"http_proxy=http://proxy:{URL_PASSWORD}@proxy.example:3128",
            f"https_proxy=http://proxy:{URL_PASSWORD}@proxy.example:3128",
        ],
        stdout=_executing(_TOKEN_REDUCE_ATTACH_ARGV),
        stderr="",
    ),
}


def test_planner_claude_launch(launch_env, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", GITHUB_TOKEN)
    monkeypatch.setenv("HOLON_MIRROR_URL", f"https://holon:{URL_PASSWORD}@mirror.example/repo")
    monkeypatch.setenv("HOLON_TRACE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", CLAUDE_API_KEY)
    monkeypatch.setenv("HOLON_AGENT_KEY", "stale-agent-key")
    launch = _launch(
        launch_env,
        capsys,
        lambda: cli.dispatch_role_agent(
            "planner", "claude-agent", "opus", ["plan-branch", "claude-agent", "opus"], target_worktree=WORKTREE
        ),
    )
    _check("planner_claude", launch)
    assert launch_env["worktree_calls"] == [(WORKTREE, SANDBOX_WORKSPACE_MOUNT, "planner")]
    _assert_no_secret_on_argv(launch, GITHUB_TOKEN, URL_PASSWORD, CLAUDE_API_KEY, "stale-agent-key")
    assert not os.path.exists(launch_env["env_files"][0])


def test_executor_with_result_file(launch_env, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", GITHUB_TOKEN)
    result_file = tmp_path / "result.json"
    launch = _launch(
        launch_env,
        capsys,
        lambda: cli.run_docker_container(
            "executor",
            "holon/agent-codex",
            ["exec-branch", "codex-agent", "gpt"],
            agent_id="codex",
            target_worktree=WORKTREE,
            result_file=str(result_file),
        ),
    )
    _check("executor_result_file", launch)
    _assert_no_secret_on_argv(launch, GITHUB_TOKEN)


def test_a_launch_during_a_flow_run_is_named_and_labelled(launch_env, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", GITHUB_TOKEN)
    run_tracking.start_run("x", "abc123")
    try:
        launch = _launch(
            launch_env,
            capsys,
            lambda: cli.run_docker_container(
                "executor",
                "holon/agent-codex",
                ["exec-branch", "codex-agent", "gpt"],
                agent_id="codex",
                target_worktree=WORKTREE,
                result_file=str(tmp_path / "result.json"),
            ),
        )
        run = run_tracking.active_run()
        assert run is not None and run.container_names == ["holon-x-executor-abc123-1"]
    finally:
        run_tracking.end_run()
    identity = "--name holon-x-executor-abc123-1 --label holon.flow.run=abc123"
    expected = _EXECUTOR_RESULT_FILE_ARGV.replace("--rm -i ", f"--rm -i {identity} ")
    assert launch.docker_runs == [_argv(expected)]
    assert launch.docker_runs[0][2] == "--env-file"
    assert launch.stdout == _executing(expected)


def test_intent_creator_with_intent_file(launch_env, capsys, tmp_path):
    intent_file = tmp_path / "intent.json"
    intent_file.write_text("{}", encoding="utf-8")
    launch = _launch(
        launch_env,
        capsys,
        lambda: cli.dispatch_role_agent(
            "intent-creator", None, None, ["--slug", "x"], target_worktree=WORKTREE, intent_file=str(intent_file)
        ),
    )
    _check("intent_creator", launch)


def test_intent_creator_mounts_the_intent_file_read_only(launch_env, capsys, tmp_path):
    intent_file = tmp_path / "intent.json"
    intent_file.write_text("{}", encoding="utf-8")
    launch = _launch(
        launch_env,
        capsys,
        lambda: cli.dispatch_role_agent(
            "intent-creator", None, None, ["--slug", "x"], target_worktree=WORKTREE, intent_file=str(intent_file)
        ),
    )
    [docker_run] = launch.docker_runs
    volumes = [docker_run[index + 1] for index, arg in enumerate(docker_run[:-1]) if arg == "-v"]
    intent_volumes = [volume for volume in volumes if ":/tmp/intent.json" in volume]
    assert intent_volumes == ["<TMP>/intent.json:/tmp/intent.json:ro"]
    assert "<TMP>/intent.json:/tmp/intent.json" not in docker_run

def test_intent_creator_with_missing_intent_file(launch_env, capsys, tmp_path):
    missing = tmp_path / "missing.json"
    launch = _launch(
        launch_env,
        capsys,
        lambda: cli.dispatch_role_agent(
            "intent-creator", None, None, ["--slug", "x"], target_worktree=WORKTREE, intent_file=str(missing)
        ),
    )
    _check("intent_creator_missing_file", launch)


def test_intent_creator_without_intent_file(launch_env, capsys):
    launch = _launch(
        launch_env,
        capsys,
        lambda: cli.run_docker_container(
            "intent-creator", "holon/orchestrator", [], agent_id="antigravity", target_worktree=WORKTREE
        ),
    )
    _check("intent_creator_no_file", launch)


def test_planner_without_worktree_fails_closed(launch_env, capsys, monkeypatch):
    monkeypatch.setenv("HOLON_MIRROR_URL", f"https://holon:{URL_PASSWORD}@mirror.example/repo")
    launch = _launch(
        launch_env,
        capsys,
        lambda: cli.run_docker_container("planner", "holon/agent-claude", [], agent_id="claude"),
    )
    _check("planner_no_worktree", launch)


def test_docker_missing(launch_env, capsys, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    launch = _launch(
        launch_env,
        capsys,
        lambda: cli.run_docker_container(
            "planner", "holon/agent-claude", [], agent_id="claude", target_worktree=WORKTREE
        ),
    )
    _check("docker_missing", launch)
    assert launch_env["worktree_calls"] == []


def test_pi_runner_with_local_llm(launch_env, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", GITHUB_TOKEN)
    monkeypatch.setenv("HOLON_LOCAL_LLM", "1")
    monkeypatch.setenv("HOLON_LOCAL_BASE_URL", "http://localhost:8081/v1")
    monkeypatch.setenv("HOLON_LOCAL_MODELS", "qwen3:test")
    monkeypatch.setenv("HOLON_LOCAL_PROVIDER", "vmlx")
    monkeypatch.setenv("HOLON_LOCAL_CONTEXT_WINDOW", "32768")
    launch = _launch(
        launch_env,
        capsys,
        lambda: cli.dispatch_role_agent(
            "executor", "pi-agent", "qwen3:test", ["exec-branch", "pi-agent", "qwen3:test"], target_worktree=WORKTREE
        ),
    )
    _check("pi_local_llm", launch)
    # The generated agent directory is removed once the container returns.
    home = Path(os.environ["HOME"])
    assert list((home / ".holon" / "tmp").glob("holon-pi-agent-*")) == []


def test_env_file_write_failure_fails_closed(launch_env, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", GITHUB_TOKEN)

    def refuse(*args: Any, **kwargs: Any) -> tuple[int, str]:
        del args, kwargs
        raise PermissionError(13, "Permission denied", "/denied/env-files")

    monkeypatch.setattr(tempfile, "mkstemp", refuse)
    launch = _launch(
        launch_env,
        capsys,
        lambda: cli.run_docker_container(
            "planner", "holon/agent-claude", [], agent_id="claude", target_worktree=WORKTREE
        ),
    )
    _check("env_file_failure", launch)
    _assert_no_secret_on_argv(launch, GITHUB_TOKEN)
    assert GITHUB_TOKEN not in launch.stderr


def test_env_file_line_break_fails_closed(launch_env, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", GITHUB_TOKEN + "\nINJECTED=1")
    launch = _launch(
        launch_env,
        capsys,
        lambda: cli.run_docker_container(
            "planner", "holon/agent-claude", [], agent_id="claude", target_worktree=WORKTREE
        ),
    )
    _check("env_file_line_break", launch)


def test_token_reduce_attaches_to_an_external_proxy(launch_env, capsys, monkeypatch):
    monkeypatch.setenv("HOLON_TOKEN_REDUCE", "1")
    monkeypatch.setenv("HOLON_PROXY_URL", f"http://proxy:{URL_PASSWORD}@proxy.example:3128")
    probes: list[tuple[Any, ...]] = []

    def fake_connect(address: tuple[str, int], timeout: float | None = None, *args: Any) -> _ProxyConnection:
        probes.append((address, timeout))
        return _ProxyConnection()

    monkeypatch.setattr(socket, "create_connection", fake_connect)
    launch = _launch(
        launch_env,
        capsys,
        lambda: cli.run_docker_container(
            "executor", "holon/agent-codex", ["b"], agent_id="codex", target_worktree=WORKTREE
        ),
    )
    _check("token_reduce_attach", launch)
    assert probes == [(("proxy.example", 3128), 2.0)]
    # Only docker run was spawned: no sidecar, no network.
    assert [run[:2] for run in launch_env["runs"]] == [["docker", "run"]]
    _assert_no_secret_on_argv(launch, URL_PASSWORD)


def test_host_proxy_envs_and_coherence(launch_env, monkeypatch):
    del launch_env
    monkeypatch.setenv("HOLON_PROXY_URL", "http://127.0.0.1:9090")
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: _ProxyConnection())
    # The fake CA generator's paths do not exist, so no CA variables are added.
    assert cli.get_host_proxy_envs() == {
        "HTTP_PROXY": "http://127.0.0.1:9090",
        "HTTPS_PROXY": "http://127.0.0.1:9090",
        "http_proxy": "http://127.0.0.1:9090",
        "https_proxy": "http://127.0.0.1:9090",
    }
    assert cli.detect_holon_coherence() is True
    monkeypatch.setenv("HOLON_TOKEN_REDUCE", "off")
    assert cli.get_host_proxy_envs() == {}
    assert cli.detect_holon_coherence(token_reduce=True) is False


def test_coherence_probe_order_when_nothing_answers(launch_env, monkeypatch):
    calls: list[str] = []

    def refuse(address: tuple[str, int], timeout: float | None = None) -> None:
        calls.append(f"connect {address[0]}:{address[1]} {timeout}")
        raise OSError("refused")

    monkeypatch.setenv("HOLON_PROXY_PORT", "not-a-port")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: calls.append(" ".join(cmd)) or subprocess.CompletedProcess(cmd, 1, "", ""),
    )
    assert cli.detect_holon_coherence() is False
    assert calls == [
        "connect 127.0.0.1:8080 0.2",
        "docker image inspect holon-coherence:latest",
        "docker image inspect ghcr.io/holon-agentic-coder/holon-coherence:latest",
    ]
    assert launch_env["runs"] == []


def test_redact_cli_error_masks_url_password_pieces(monkeypatch):
    monkeypatch.setenv("HOLON_PROXY_URL", "http://user:ab/cd?efgh@proxy.example:3128")
    message = "Port could not be cast to integer value as 'ab' and user:ab/cd?efgh and efgh"
    assert cli.redact_cli_error(ValueError(message)) == (
        "Port could not be cast to integer value as 'ab' and ******* and *******"
    )
