"""Hermetic unit tests for Claude Code experimental code mode default and ``HOLON_CLAUDE_CODE_MODE``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sandbox_executor import config_env, config_env_report, env_registry
from sandbox_executor.agent_attribution import ATTRIBUTION_OFF
from sandbox_executor.agent_runner import (
    CLAUDE_EXPERIMENTAL_CODE_MODE_ENV,
    ENV_CLAUDE_CODE_MODE,
    claude_code_mode_enabled,
    get_runner,
    runners,
)
from sandbox_executor.launcher import container_env

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parents[1]


@pytest.fixture
def hermetic_runner_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Hermetic environment with dummy credentials and no ambient runner flags."""
    monkeypatch.setenv("HOLON_AGENT_KEY", "dummy")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    monkeypatch.setenv("HOLON_SECRET_BUNDLE_PATH", "/nonexistent/holon_auth.json")
    for var in (
        "CLAUDE_CODE_OAUTH_TOKEN",
        "HOLON_AGENT_SETTINGS",
        "HOLON_AGENT_EFFORT",
        "HOLON_AGENT_SKIP_PERMISSIONS",
        "HOLON_AGENT_PROVIDER",
        "HOLON_AGENT_MODE",
        "HOLON_AGENT_OSS_MODE",
        "HOLON_AGENT_LOCAL_PROVIDER",
        "HOLON_AGENT_CONFIG",
        "HOLON_LOCAL_LLM",
        ENV_CLAUDE_CODE_MODE,
    ):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def _settings_payload(cmd: list[str]) -> dict:
    assert cmd.count("--settings") == 1
    raw = cmd[cmd.index("--settings") + 1]
    text = Path(raw).read_text(encoding="utf-8") if not raw.startswith("{") else raw
    return json.loads(text)


def test_default_unset_enables_code_mode_alongside_attribution(
    hermetic_runner_env: pytest.MonkeyPatch,
) -> None:
    assert claude_code_mode_enabled() is True
    cmd = get_runner("claude").build_cmd("claude-sonnet-4-6", "/tmp/p.md", "/tmp/i.json", "prompt")
    settings = _settings_payload(cmd)
    assert settings["includeCoAuthoredBy"] is False
    assert settings["attribution"] == ATTRIBUTION_OFF["attribution"]
    assert settings["env"] == {CLAUDE_EXPERIMENTAL_CODE_MODE_ENV: "1"}


def test_default_merges_with_inline_operator_settings(
    hermetic_runner_env: pytest.MonkeyPatch,
) -> None:
    hermetic_runner_env.setenv(
        "HOLON_AGENT_SETTINGS",
        json.dumps({"model": "custom", "permissions": {"allow": ["Bash"]}, "env": {"FOO": "bar"}}),
    )
    cmd = get_runner("claude").build_cmd("claude-sonnet-4-6", None, None, "prompt")
    settings = _settings_payload(cmd)
    assert settings["model"] == "custom"
    assert settings["permissions"] == {"allow": ["Bash"]}
    assert settings["includeCoAuthoredBy"] is False
    assert settings["attribution"] == ATTRIBUTION_OFF["attribution"]
    assert settings["env"] == {"FOO": "bar", CLAUDE_EXPERIMENTAL_CODE_MODE_ENV: "1"}


def test_default_merges_with_file_operator_settings_privately(
    hermetic_runner_env: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings_file = tmp_path / "settings.json"
    original = json.dumps({"customKey": True, "env": {"SECRET_TOKEN": "sk-file-secret"}})
    settings_file.write_text(original, encoding="utf-8")
    hermetic_runner_env.setenv("HOLON_AGENT_SETTINGS", str(settings_file))

    cmd = get_runner("claude").build_cmd("claude-sonnet-4-6", None, None, "prompt")
    assert not any("sk-file-secret" in arg for arg in cmd)
    out_path = Path(cmd[cmd.index("--settings") + 1])
    assert (out_path.stat().st_mode & 0o077) == 0
    assert settings_file.read_text(encoding="utf-8") == original

    settings = _settings_payload(cmd)
    assert settings["customKey"] is True
    assert settings["includeCoAuthoredBy"] is False
    assert settings["env"] == {"SECRET_TOKEN": "sk-file-secret", CLAUDE_EXPERIMENTAL_CODE_MODE_ENV: "1"}


@pytest.mark.parametrize("off_value", ["0", "false", "off", "no", "FALSE", "OFF", "No", " 0 "])
def test_opt_out_values_omit_experimental_code_mode(
    hermetic_runner_env: pytest.MonkeyPatch,
    off_value: str,
) -> None:
    hermetic_runner_env.setenv(ENV_CLAUDE_CODE_MODE, off_value)
    assert claude_code_mode_enabled() is False

    cmd = get_runner("claude").build_cmd("claude-sonnet-4-6", None, None, "prompt")
    settings = _settings_payload(cmd)
    assert settings == ATTRIBUTION_OFF
    assert "env" not in settings


@pytest.mark.parametrize("off_value", ["0", "false", "off"])
def test_opt_out_preserves_other_operator_env_keys(
    hermetic_runner_env: pytest.MonkeyPatch,
    off_value: str,
) -> None:
    hermetic_runner_env.setenv(ENV_CLAUDE_CODE_MODE, off_value)
    hermetic_runner_env.setenv("HOLON_AGENT_SETTINGS", json.dumps({"env": {"OTHER_VAR": "kept"}}))

    cmd = get_runner("claude").build_cmd("claude-sonnet-4-6", None, None, "prompt")
    settings = _settings_payload(cmd)
    assert settings["env"] == {"OTHER_VAR": "kept"}
    assert CLAUDE_EXPERIMENTAL_CODE_MODE_ENV not in settings["env"]


@pytest.mark.parametrize("on_value", ["1", "true", "on", "yes", "TRUE", "ON", ""])
def test_explicit_enable_values_turn_on_code_mode(
    hermetic_runner_env: pytest.MonkeyPatch,
    on_value: str,
) -> None:
    hermetic_runner_env.setenv(ENV_CLAUDE_CODE_MODE, on_value)
    assert claude_code_mode_enabled() is True
    cmd = get_runner("claude").build_cmd("claude-sonnet-4-6", None, None, "prompt")
    settings = _settings_payload(cmd)
    assert settings["env"] == {CLAUDE_EXPERIMENTAL_CODE_MODE_ENV: "1"}


@pytest.mark.parametrize("holon_mode", [None, "1", "0", "false"])
@pytest.mark.parametrize("operator_value", ["0", "1", "custom"])
def test_operator_supplied_code_mode_in_settings_env_is_kept(
    hermetic_runner_env: pytest.MonkeyPatch,
    holon_mode: str | None,
    operator_value: str,
) -> None:
    if holon_mode is not None:
        hermetic_runner_env.setenv(ENV_CLAUDE_CODE_MODE, holon_mode)
    hermetic_runner_env.setenv(
        "HOLON_AGENT_SETTINGS",
        json.dumps({"env": {CLAUDE_EXPERIMENTAL_CODE_MODE_ENV: operator_value, " SIBLING": "yes"}}),
    )

    cmd = get_runner("claude").build_cmd("claude-sonnet-4-6", None, None, "prompt")
    settings = _settings_payload(cmd)
    assert settings["env"][CLAUDE_EXPERIMENTAL_CODE_MODE_ENV] == operator_value
    assert settings["env"][" SIBLING"] == "yes"


def test_registry_documentation_forwarding_and_doctor(
    hermetic_runner_env: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # 1. Present in env_registry.ENVIRONMENT_VARIABLES
    entries = [v for v in env_registry.ENVIRONMENT_VARIABLES if v.name == ENV_CLAUDE_CODE_MODE]
    assert len(entries) == 1
    entry = entries[0]
    assert entry.area is env_registry.Area.RUNNER_AND_AGENT
    assert entry.default == "`1`"
    assert entry.forwarding == "env-file"
    assert entry.read_side == "both"
    assert entry.secret is False
    assert entry.internal is False

    # 2. Appears in generated environment-variable documentation and executor README
    doc_text = (REPO / "docs" / "environment_variables.md").read_text(encoding="utf-8")
    readme_text = (PACKAGE / "README.md").read_text(encoding="utf-8")
    assert f"| `{ENV_CLAUDE_CODE_MODE}`" in doc_text
    assert f"| `{ENV_CLAUDE_CODE_MODE}`" in readme_text

    # 3. Forwarded to role containers by container_env.forwarded_env
    hermetic_runner_env.setenv(ENV_CLAUDE_CODE_MODE, "0")
    forwarded = container_env.forwarded_env("executor", "claude-agent", pin_workspace=False)
    assert forwarded.get(ENV_CLAUDE_CODE_MODE) == "0"

    # 4. Shown by holon doctor configuration report (both default and overridden)
    fake_home = tmp_path / "home"
    fake_repo = tmp_path / "repo"
    (fake_repo / ".git").mkdir(parents=True)
    hermetic_runner_env.setenv("HOME", str(fake_home))

    default_cfg = config_env.load_effective_config(environ={}, cwd=str(fake_repo))
    assert ENV_CLAUDE_CODE_MODE in default_cfg.settings
    assert default_cfg.settings[ENV_CLAUDE_CODE_MODE].value == "1"
    assert default_cfg.settings[ENV_CLAUDE_CODE_MODE].source == "default"
    default_report = "\n".join(config_env_report.format_config_report(default_cfg))
    assert f"{ENV_CLAUDE_CODE_MODE} = 1   [default]" in default_report

    override_cfg = config_env.load_effective_config(environ={ENV_CLAUDE_CODE_MODE: "0"}, cwd=str(fake_repo))
    assert override_cfg.settings[ENV_CLAUDE_CODE_MODE].value == "0"
    assert override_cfg.settings[ENV_CLAUDE_CODE_MODE].source == "process"
    override_report = "\n".join(config_env_report.format_config_report(override_cfg))
    assert f"{ENV_CLAUDE_CODE_MODE} = 0   [process; shadows default]" in override_report


@pytest.mark.parametrize("agent_name", ["pi", "opencode", "codex", "antigravity"])
def test_non_claude_runners_are_unaffected(
    hermetic_runner_env: pytest.MonkeyPatch,
    agent_name: str,
) -> None:
    runner = runners[agent_name]
    cmd_unset = runner.build_cmd("model-x", "/tmp/p.md", "/tmp/i.json", "prompt")
    compose_unset = runner.compose_cmd("model-x", "prompt")
    env_unset = {
        k: v
        for k, v in container_env.forwarded_env("executor", agent_name, pin_workspace=False).items()
        if k != ENV_CLAUDE_CODE_MODE
    }

    for val in ("1", "0", "false"):
        hermetic_runner_env.setenv(ENV_CLAUDE_CODE_MODE, val)
        assert runner.build_cmd("model-x", "/tmp/p.md", "/tmp/i.json", "prompt") == cmd_unset
        assert runner.compose_cmd("model-x", "prompt") == compose_unset
        assert not any(CLAUDE_EXPERIMENTAL_CODE_MODE_ENV in part for part in cmd_unset)
        env_now = {
            k: v
            for k, v in container_env.forwarded_env("executor", agent_name, pin_workspace=False).items()
            if k != ENV_CLAUDE_CODE_MODE
        }
        assert env_now == env_unset
