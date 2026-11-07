"""Tests for the holon models command and registry capabilities."""

from __future__ import annotations

import json

import pytest
from sandbox_executor import cli
from sandbox_executor.agent_defaults import (
    RUNNER_CAPABILITIES,
    detect_runner_auth_status,
    get_available_models_for_runner,
    get_model_capabilities,
    normalise_model_and_effort,
)


def test_models_command_table_display(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify that holon models executes real CLI dispatch and prints a formatted table."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["models"])
    assert exc_info.value.code == 0

    captured = capsys.readouterr()
    out = captured.out

    assert "Runner" in out
    assert "Canonical Model" in out
    assert "Aliases" in out
    assert "Supported Effort Levels" in out
    assert "Fallback Tier" in out
    assert "Auth / Session Detection" in out

    assert "antigravity-agent" in out
    assert "gemini-3.8-flash" in out
    assert "claude-agent" in out
    assert "claude-opus-5-5" in out
    assert "pi-agent" in out
    assert "codex-agent" in out
    assert "gemini-agent" in out
    assert "opencode-agent" in out
    assert "local-model" in out


def test_list_models_alias(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify that holon list-models alias works identically to holon models."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["list-models"])
    assert exc_info.value.code == 0

    captured = capsys.readouterr()
    out = captured.out

    assert "Runner" in out
    assert "antigravity-agent" in out
    assert "claude-agent" in out


def test_models_agent_filtering_antigravity(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify --agent antigravity filters output strictly to antigravity models."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["models", "--agent", "antigravity"])
    assert exc_info.value.code == 0

    captured = capsys.readouterr()
    out = captured.out

    assert "gemini-3.8-flash" in out
    assert "gemini-3.8-flash-medium" in out
    assert "gemini-3.8-flash-high" in out
    assert "low, medium, high" in out

    assert "claude-agent" not in out
    assert "pi-agent" not in out
    assert "codex-agent" not in out


def test_models_agent_filtering_claude(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify --agent claude filters output strictly to claude models and aliases."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["models", "--agent", "claude"])
    assert exc_info.value.code == 0

    captured = capsys.readouterr()
    out = captured.out

    assert "claude-opus-5-5" in out
    assert "claude-sonnet-4-6" in out
    assert "claude-haiku-4-5" in out
    assert "opus" in out
    assert "sonnet" in out
    assert "haiku" in out
    assert "fable" in out

    assert "antigravity-agent" not in out
    assert "gemini-3.8-flash" not in out


def test_models_agent_filtering_case_insensitivity(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify --agent is case-insensitive and normalises runner names."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["models", "--agent", "Claude"])
    assert exc_info.value.code == 0
    captured_claude = capsys.readouterr().out
    assert "claude-opus-5-5" in captured_claude

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["models", "--agent", "ANTIGRAVITY-AGENT"])
    assert exc_info.value.code == 0
    captured_agy = capsys.readouterr().out
    assert "gemini-3.8-flash" in captured_agy


def test_models_agent_filtering_pi(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify --agent pi displays Pi runner models."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["models", "--agent", "pi"])
    assert exc_info.value.code == 0

    captured = capsys.readouterr()
    out = captured.out

    assert "pi-agent" in out
    assert "claude-opus-5-5" in out
    assert "gpt-5-codex" in out


def test_models_json_schema(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify that holon models --json outputs valid JSON conforming to expected schema."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["models", "--json"])
    assert exc_info.value.code == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert isinstance(data, list)
    assert len(data) == len(RUNNER_CAPABILITIES)

    for entry in data:
        assert isinstance(entry["runner"], str)
        assert isinstance(entry["canonical_model"], str)
        assert isinstance(entry["aliases"], list)
        assert isinstance(entry["effort_levels"], list)
        assert isinstance(entry["fallback_tier"], str)
        assert isinstance(entry["auth_status"], str)


def test_models_json_filtered_agent(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify that holon models --agent <name> --json returns filtered JSON."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["models", "--agent", "antigravity", "--json"])
    assert exc_info.value.code == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert len(data) == 3
    for entry in data:
        assert entry["runner"] == "antigravity-agent"
        assert entry["effort_levels"] == ["low", "medium", "high"]


def test_leak_free_auth_status_detection(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that secret tokens in environment are never leaked in output."""
    secret_key = "super-secret-agent-key-98765"
    anthropic_secret = "sk-ant-secret-key-12345"
    monkeypatch.setenv("HOLON_AGENT_KEY", secret_key)
    monkeypatch.setenv("ANTHROPIC_API_KEY", anthropic_secret)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["models", "--json"])
    assert exc_info.value.code == 0

    captured = capsys.readouterr()
    raw_output = captured.out

    assert secret_key not in raw_output
    assert anthropic_secret not in raw_output

    data = json.loads(raw_output)
    for entry in data:
        assert entry["auth_status"] == "Detected (API key)"


def test_unknown_agent_error_handling(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify that invalid runner in --agent returns non-zero exit code and error message."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["models", "--agent", "unknown-runner"])
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    assert "Error: Unknown agent runner 'unknown-runner'" in captured.err
    assert "Supported runners:" in captured.err


def test_doctor_missing_model_suggests_holon_models(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that holon doctor suggests running holon models when model resolution fails."""
    monkeypatch.delenv("HOLON_MODEL", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["doctor", "--agent", "antigravity"])
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    assert "Model is mandatory: no --model or HOLON_MODEL provided" in captured.err
    assert "Run 'holon models --agent antigravity' to inspect supported models and effort levels." in captured.err


def test_doctor_invalid_model_suggests_holon_models(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify that holon doctor rejects invalid model with available choices and holon models hint."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["doctor", "--agent", "claude", "--model", "unsupported-model-xyz"])
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    assert "Unrecognised model 'unsupported-model-xyz' for claude-agent" in captured.err
    assert "Supported models for claude-agent: claude-opus-5-5, claude-sonnet-4-6, claude-haiku-4-5." in captured.err
    assert "Run 'holon models --agent claude' to inspect supported models and effort levels." in captured.err


def test_doctor_json_error_payload(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify that holon doctor --json includes available models and recommendation in error payload."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["doctor", "--agent", "antigravity", "--model", "invalid-model", "--json"])
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert "error" in payload
    assert "available_models" in payload
    assert "hint" in payload
    assert "Run 'holon models --agent antigravity'" in payload["hint"]
    assert "gemini-3.8-flash-medium" in payload["available_models"]


def test_normalise_model_and_effort_coordination() -> None:
    """Verify suffix and effort parameter coordination without collisions."""
    model, effort = normalise_model_and_effort("antigravity", "flash", "high")
    assert model == "gemini-3.8-flash-high"
    assert effort == "high"

    model, effort = normalise_model_and_effort("antigravity", "gemini-3.8-flash-medium", None)
    assert model == "gemini-3.8-flash-medium"
    assert effort == "medium"

    model, effort = normalise_model_and_effort("antigravity", "gemini-3.8-flash-medium", "low")
    assert model == "gemini-3.8-flash-low"
    assert effort == "low"

    model, effort = normalise_model_and_effort("claude", "opus", None)
    assert model == "claude-opus-5-5"
    assert effort is None

    model, effort = normalise_model_and_effort("claude", "fable", None)
    assert model == "claude-opus-5-5"
    assert effort is None


def test_detect_runner_auth_status_safe_and_opaque() -> None:
    """Verify detect_runner_auth_status produces only known opaque status strings."""
    valid_statuses = {
        "Detected (session directory)",
        "Detected (API key)",
        "Detected (harness marker)",
        "Detected (local gateway)",
        "Missing / Not detected",
    }
    for cap in get_model_capabilities():
        status = detect_runner_auth_status(cap.runner)
        assert status in valid_statuses
    assert len(get_available_models_for_runner("antigravity")) == 3
