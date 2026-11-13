import json
from pathlib import Path

import pytest
from sandbox_executor.agent_attribution import ATTRIBUTION_OFF, attribution_off_settings
from sandbox_executor.agent_runner import get_runner


@pytest.fixture
def claude_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("HOLON_AGENT_KEY", "dummy")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    monkeypatch.delenv("HOLON_AGENT_SETTINGS", raising=False)
    monkeypatch.delenv("HOLON_AGENT_SKIP_PERMISSIONS", raising=False)
    monkeypatch.setenv("HOLON_SECRET_BUNDLE_PATH", "/nonexistent/holon_auth.json")
    return monkeypatch


def _settings_of(cmd: list[str]) -> dict:
    assert cmd.count("--settings") == 1
    return json.loads(cmd[cmd.index("--settings") + 1])


def test_unset_settings_pass_attribution_off(claude_env: pytest.MonkeyPatch) -> None:
    settings = _settings_of(get_runner("claude").build_cmd("m", None, None, "p"))
    assert settings["includeCoAuthoredBy"] is False
    assert settings["attribution"] == {"commit": "", "pr": ""}


def test_inline_settings_keys_survive(claude_env: pytest.MonkeyPatch) -> None:
    claude_env.setenv("HOLON_AGENT_SETTINGS", '{"env": {"A": "1"}, "includeCoAuthoredBy": true}')
    settings = _settings_of(get_runner("claude").build_cmd("m", None, None, "p"))
    assert settings["env"] == {"A": "1"}
    assert settings["includeCoAuthoredBy"] is False


def test_file_settings_are_merged_and_left_unchanged(claude_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = '{"model": "x"}'
    path.write_text(original, encoding="utf-8")
    claude_env.setenv("HOLON_AGENT_SETTINGS", str(path))
    settings = _settings_of(get_runner("claude").build_cmd("m", None, None, "p"))
    assert settings["model"] == "x"
    assert settings["attribution"] == ATTRIBUTION_OFF["attribution"]
    assert path.read_text(encoding="utf-8") == original


def test_invalid_settings_fall_back_without_leaking(caplog: pytest.LogCaptureFixture) -> None:
    secret = "{not-json-sk-secret"
    assert json.loads(attribution_off_settings("not-a-file-sk-secret")) == ATTRIBUTION_OFF
    assert json.loads(attribution_off_settings(secret)) == ATTRIBUTION_OFF
    assert "sk-secret" not in caplog.text


def test_bypass_flags_are_unchanged(claude_env: pytest.MonkeyPatch) -> None:
    cmd = get_runner("claude").build_cmd("m", None, None, "p")
    assert cmd.count("--permission-mode") == 1
    assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"
