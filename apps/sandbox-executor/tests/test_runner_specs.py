import dataclasses
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from sandbox_executor import claude_keychain
from sandbox_executor.runners import RUNNER_SPECS, runner_spec_for

APP_ROOT = Path(__file__).resolve().parent.parent

GENERIC_TAIL = (
    "Please set 'HOLON_AGENT_KEY', mount session credentials, or select a host-local model with "
    "'HOLON_LOCAL_LLM=1' plus a reachable endpoint."
)

EXPECTED_MESSAGES = {
    "antigravity": (
        "Error: Missing required API credentials for agent 'antigravity'.\n"
        "Please set 'HOLON_AGENT_KEY' or mount active session to '/home/holon/.gemini'."
    ),
    "claude": (
        "Error: Missing required credentials for agent 'claude'.\n"
        "Please store a token or API key from 'claude setup-token' in the host's OS keychain "
        f"({claude_keychain.store_commands_text()}), set 'CLAUDE_CODE_OAUTH_TOKEN' (sk-ant-oat...) or "
        "'ANTHROPIC_API_KEY' (sk-ant-api...), or mount a session. 'HOLON_AGENT_KEY' is not used for claude."
    ),
    "pi": "Error: Missing required API credentials for agent 'pi'.\n" + GENERIC_TAIL,
    "codex": (
        "Error: Missing required credentials for agent 'codex'.\n"
        "Please set 'HOLON_AGENT_KEY', set 'HOLON_AGENT_OSS_MODE=true', "
        "or mount active credentials to '/home/holon/.codex'."
    ),
    "opencode": "Error: Missing required API credentials for agent 'opencode'.\n" + GENERIC_TAIL,
}

SESSION_PATHS = [
    ("antigravity", "/home/holon/.gemini/antigravity-cli"),
    ("antigravity", "~/.gemini/antigravity-cli"),
    ("antigravity", "/home/holon/.gemini"),
    ("antigravity", "~/.gemini"),
    ("antigravity", "/home/holon/.config/gcloud"),
    ("antigravity", "~/.config/gcloud"),
    ("antigravity", "~/.holon/sessions/antigravity"),
    ("claude", "/home/holon/.config/claude"),
    ("claude", "~/.config/claude"),
    ("pi", "/home/holon/.pi/agent/models.json"),
    ("pi", "~/.pi/agent/models.json"),
    ("pi", "/home/holon/.config/pi/models.json"),
    ("pi", "~/.config/pi/models.json"),
    ("codex", "/home/holon/.codex"),
    ("codex", "~/.codex"),
]


@pytest.fixture
def existing_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> set[str]:
    for name in list(os.environ):
        monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", str(tmp_path))
    paths: set[str] = set()
    monkeypatch.setattr(os.path, "exists", lambda p: p in paths)
    return paths


def test_registry_keys_match_agent_ids() -> None:
    assert list(RUNNER_SPECS) == ["antigravity", "claude", "pi", "codex", "opencode"]
    for agent_id, spec in RUNNER_SPECS.items():
        assert spec.agent_id == agent_id


def test_runner_spec_for_normalises_agent_names() -> None:
    assert runner_spec_for("claude-agent") is RUNNER_SPECS["claude"]
    assert runner_spec_for("Agent-Pi") is RUNNER_SPECS["pi"]
    assert runner_spec_for("test-agent") is None


def test_specs_are_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        RUNNER_SPECS["claude"].image = "other"  # type: ignore[misc]


def test_every_spec_image_is_built_by_docker_bake() -> None:
    bake = (APP_ROOT / "docker-bake.hcl").read_text()
    tags = set(re.findall(r'tags\s*=\s*\["([^"]+)"\]', bake))
    missing = sorted(spec.image for spec in RUNNER_SPECS.values() if spec.image not in tags)
    assert not missing, f"images missing from docker-bake.hcl: {missing}"


def test_every_spec_has_a_role_dispatcher_case() -> None:
    dispatcher = (APP_ROOT / "entrypoint" / "role_dispatcher.sh").read_text()
    labels = set(re.findall(r"^\s*([a-z0-9_-]+)\)\s*$", dispatcher, flags=re.MULTILINE))
    missing = sorted(agent_id for agent_id in RUNNER_SPECS if agent_id not in labels)
    assert not missing, f"agents missing from role_dispatcher.sh: {missing}"


def test_importing_runners_does_not_import_defaults_or_runner() -> None:
    code = (
        "import sys, sandbox_executor.runners\n"
        "forbidden = (\n"
        "    'sandbox_executor.agent_defaults',\n"
        "    'sandbox_executor.agent_runner',\n"
        "    'sandbox_executor.launcher',\n"
        "    'sandbox_executor.launcher.mounts',\n"
        "    'sandbox_executor.launcher.antigravity_mounts',\n"
        "    'sandbox_executor.launcher.pi_mounts',\n"
        ")\n"
        "leaked = [m for m in forbidden if m in sys.modules]\n"
        "assert not leaked, leaked\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.parametrize("agent_id", list(EXPECTED_MESSAGES))
def test_missing_credentials_message(agent_id: str, existing_paths: set[str]) -> None:
    assert RUNNER_SPECS[agent_id].check_credentials() == EXPECTED_MESSAGES[agent_id]


@pytest.mark.parametrize("agent_id", ["antigravity", "pi", "codex", "opencode"])
def test_holon_agent_key_passes(agent_id: str, existing_paths: set[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOLON_AGENT_KEY", "k")
    assert RUNNER_SPECS[agent_id].check_credentials() is None


@pytest.mark.parametrize("variable", ["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"])
def test_claude_accepts_its_own_credentials(
    variable: str, existing_paths: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(variable, "k")
    assert RUNNER_SPECS["claude"].check_credentials() is None


def test_claude_ignores_holon_agent_key(existing_paths: set[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOLON_AGENT_KEY", "k")
    assert RUNNER_SPECS["claude"].check_credentials() == EXPECTED_MESSAGES["claude"]


def test_empty_key_does_not_count(existing_paths: set[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOLON_AGENT_KEY", "")
    assert RUNNER_SPECS["opencode"].check_credentials() == EXPECTED_MESSAGES["opencode"]


@pytest.mark.parametrize(("agent_id", "path"), SESSION_PATHS)
def test_session_path_passes(agent_id: str, path: str, existing_paths: set[str]) -> None:
    existing_paths.add(os.path.expanduser(path))
    assert RUNNER_SPECS[agent_id].check_credentials() is None


def test_opencode_has_no_session_directory(existing_paths: set[str]) -> None:
    existing_paths.update(os.path.expanduser(p) for _, p in SESSION_PATHS)
    assert RUNNER_SPECS["opencode"].check_credentials() == EXPECTED_MESSAGES["opencode"]


@pytest.mark.parametrize(("value", "passes"), [("true", True), ("1", True), ("TRUE", False), ("yes", False)])
def test_codex_oss_mode_exemption(
    value: str, passes: bool, existing_paths: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOLON_AGENT_OSS_MODE", value)
    expected = None if passes else EXPECTED_MESSAGES["codex"]
    assert RUNNER_SPECS["codex"].check_credentials() == expected


@pytest.mark.parametrize(
    ("env", "passes"),
    [
        ({"HOLON_LOCAL_LLM": "1", "HOLON_LOCAL_BASE_URL": "http://host.docker.internal:11434/v1"}, True),
        ({"HOLON_LOCAL_LLM": "1", "PI_CODING_AGENT_DIR": "/tmp/pi-agent"}, True),
        ({"HOLON_LOCAL_LLM": "1"}, False),
        ({"HOLON_LOCAL_BASE_URL": "http://host.docker.internal:11434/v1"}, False),
    ],
)
def test_pi_local_llm_exemption(
    env: dict[str, str], passes: bool, existing_paths: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    expected = None if passes else EXPECTED_MESSAGES["pi"]
    assert RUNNER_SPECS["pi"].check_credentials() == expected


def test_local_llm_exemption_is_pi_only(existing_paths: set[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOLON_LOCAL_LLM", "1")
    monkeypatch.setenv("HOLON_LOCAL_BASE_URL", "http://host.docker.internal:11434/v1")
    assert RUNNER_SPECS["opencode"].check_credentials() == EXPECTED_MESSAGES["opencode"]


def test_pi_accepts_models_json_in_pi_coding_agent_dir(
    existing_paths: set[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent_dir = tmp_path / "pi-agent"
    monkeypatch.setenv("PI_CODING_AGENT_DIR", f" {agent_dir} ")
    assert RUNNER_SPECS["pi"].check_credentials() == EXPECTED_MESSAGES["pi"]
    existing_paths.add(str(agent_dir / "models.json"))
    assert RUNNER_SPECS["pi"].check_credentials() is None


def test_every_spec_discovers_models_including_default_model() -> None:
    from sandbox_executor.runners import ModelCapability

    for spec in RUNNER_SPECS.values():
        discovered = spec.discover_models()
        assert discovered
        assert all(isinstance(cap, ModelCapability) for cap in discovered)
        assert spec.default_model in {cap.canonical_model for cap in discovered}


def test_model_discovery_hook_overrides_static_capabilities() -> None:
    from sandbox_executor.runners import ModelCapability, RunnerSpec

    dynamic_cap = ModelCapability(
        runner="custom-agent",
        canonical_model="custom-dynamic-1",
        aliases=["dyn"],
        effort_levels=[],
        fallback_tier="Custom",
    )
    spec = RunnerSpec(
        agent_id="custom",
        image="holon/agent-custom",
        default_model="custom-static",
        ticker=None,
        api_key_env_vars=(),
        credential_check=lambda _spec: None,
        model_discovery_hook=lambda _spec: (dynamic_cap,),
    )
    assert spec.discover_models() == (dynamic_cap,)
    assert spec.is_valid_model("dyn") is True
    assert spec.is_valid_model("unknown") is False
    assert spec.normalise_model_and_effort("dyn") == ("custom-dynamic-1", None)


def test_spec_session_mounts_match_launcher_and_reexports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from sandbox_executor.launcher import antigravity_mounts, mounts, pi_mounts
    from sandbox_executor.runners import antigravity as antigravity_runner
    from sandbox_executor.runners import pi as pi_runner

    assert antigravity_mounts.ANTIGRAVITY_WRITABLE_TMPFS is antigravity_runner.ANTIGRAVITY_WRITABLE_TMPFS
    assert antigravity_mounts.antigravity_session_mounts is antigravity_runner.antigravity_session_mounts
    assert pi_mounts.CONTAINER_HOME == pi_runner.CONTAINER_HOME
    assert pi_mounts.OWNED_TMPFS_OPTIONS == pi_runner.OWNED_TMPFS_OPTIONS
    assert pi_mounts.get_pi_mounts is pi_runner.get_pi_mounts

    (tmp_path / ".holon" / "sessions" / "antigravity").mkdir(parents=True)
    (tmp_path / ".config" / "claude").mkdir(parents=True)
    (tmp_path / ".codex").mkdir(parents=True)
    pi_agent_dir = tmp_path / ".pi" / "agent"
    pi_agent_dir.mkdir(parents=True)
    (pi_agent_dir / "models.json").write_text("{}")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HOLON_LOCAL_LLM", raising=False)
    monkeypatch.delenv("HOLON_ANTIGRAVITY_SESSION_DIR", raising=False)

    for agent_id, spec in RUNNER_SPECS.items():
        assert mounts.get_agent_session_mounts(agent_id) == spec.build_session_mounts(str(tmp_path))
    assert RUNNER_SPECS["opencode"].build_session_mounts(str(tmp_path)) == []
