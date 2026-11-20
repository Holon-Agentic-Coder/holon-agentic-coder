"""Tests for the shared agent runner and model resolver (sandbox_executor.agent_defaults)."""

import logging

import pytest
from sandbox_executor import agent_defaults
from sandbox_executor.agent_defaults import (
    AGENT_IMAGE_MAPPING,
    DEFAULT_MODELS,
    LEGACY_DEFAULT_AGENT,
    LEGACY_DEFAULT_MODEL,
    RUNNER_CAPABILITIES,
    MissingAgentRunnerError,
    MissingModelError,
    UnmappedAgentRunnerError,
    canonical_agent_name,
    detect_harness_agent,
    image_for_agent,
    normalise_agent_id,
    normalise_model_and_effort,
    resolve_agent_runner,
    resolve_model,
)
from sandbox_executor.agent_runner import get_runner


@pytest.mark.parametrize(
    ("explicit", "environ", "expected", "source"),
    [
        ("pi-agent", {"HOLON_AGENT": "codex-agent", "CLAUDECODE": "1"}, "pi-agent", "flag"),
        (None, {"HOLON_AGENT": "codex-agent", "CLAUDECODE": "1"}, "codex-agent", "env"),
        ("", {"HOLON_AGENT": "codex", "CLAUDECODE": "1"}, "codex-agent", "env"),
        (None, {"CLAUDECODE": "1"}, "claude-agent", "harness"),
    ],
)
def test_agent_precedence(explicit, environ, expected, source):
    res = resolve_agent_runner(explicit, environ)
    assert (res.value, res.source) == (expected, source)


def test_missing_agent_raises_error():
    with pytest.raises(MissingAgentRunnerError, match="mandatory"):
        resolve_agent_runner(None, {})


def test_missing_agent_error_mentions_how_to_fix():
    with pytest.raises(MissingAgentRunnerError) as exc_info:
        resolve_agent_runner(None, {})
    msg = str(exc_info.value)
    assert "--agent" in msg or "HOLON_AGENT" in msg


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"PI_CODING_AGENT": "true"}, "pi-agent"),
        ({"CLAUDECODE": "1"}, "claude-agent"),
        ({"CLAUDE_CODE_ENTRYPOINT": "cli"}, "claude-agent"),
        ({"CODEX_SANDBOX": "seatbelt"}, "codex-agent"),
        ({"OPENCODE": "1"}, "opencode-agent"),
        ({"OPENCODE_SESSION": "abc"}, "opencode-agent"),
        ({"AGY_SESSION_ID": "abc"}, "antigravity-agent"),
        ({"ANTIGRAVITY_AGENT": "1"}, "antigravity-agent"),
    ],
)
def test_each_harness_marker_maps_to_its_runner(environ, expected):
    detected = detect_harness_agent(environ)
    assert detected is not None
    assert detected[0] == expected


def test_multiple_markers_use_documented_order_and_log_ignored(caplog):
    environ = {"CLAUDECODE": "1", "PI_CODING_AGENT": "true", "CODEX_SANDBOX": "x"}
    with caplog.at_level(logging.INFO, logger=agent_defaults.__name__):
        res = resolve_agent_runner(None, environ)
    assert res.value == "pi-agent"
    assert "PI_CODING_AGENT" in res.reason
    ignored = " ".join(r.getMessage() for r in caplog.records)
    assert "CLAUDECODE" in ignored
    assert "CODEX_SANDBOX" in ignored


@pytest.mark.parametrize("guard", [{"HOLON_ROLE": "executor"}, {"HOLON_IN_SANDBOX": "1"}])
def test_sandbox_guard_disables_detection(guard):
    environ = {"CLAUDECODE": "1", **guard}
    assert detect_harness_agent(environ) is None
    with pytest.raises(MissingAgentRunnerError) as exc_info:
        resolve_agent_runner(None, environ)
    assert "sandbox" in str(exc_info.value)


def test_falsy_sandbox_guard_does_not_disable_detection():
    assert detect_harness_agent({"CLAUDECODE": "1", "HOLON_IN_SANDBOX": "0"}) == ("claude-agent", "CLAUDECODE")


@pytest.mark.parametrize(
    "environ",
    [
        {"AGY_USER_TOKEN": "secret"},
        {"GOOGLE_API_KEY": "secret"},
        {"AGY_USER_TOKEN": "secret", "GOOGLE_API_KEY": "secret"},
        {"CODEX_API_KEY": "secret", "CODEX_HOME": "/home/x/.codex"},
        {"OPENCODE_API_KEY": "secret"},
        {"CLAUDECODE": "0"},
        {"CLAUDECODE": ""},
    ],
)
def test_credentials_and_falsy_values_are_not_markers(environ):
    assert detect_harness_agent(environ) is None


@pytest.mark.parametrize(
    ("name", "normalised", "canonical"),
    [
        ("antigravity", "antigravity", "antigravity-agent"),
        ("antigravity-agent", "antigravity", "antigravity-agent"),
        ("agent-pi", "pi", "pi-agent"),
        ("Claude-Agent", "claude", "claude-agent"),
    ],
)
def test_canonical_form(name, normalised, canonical):
    assert normalise_agent_id(name) == normalised
    assert canonical_agent_name(name) == canonical
    assert resolve_agent_runner(name, {}).value == canonical


def test_unmapped_runner_from_env_names_the_variable():
    with pytest.raises(UnmappedAgentRunnerError) as exc_info:
        resolve_agent_runner(None, {"HOLON_AGENT": "foo-agent"})
    message = str(exc_info.value)
    assert "HOLON_AGENT" in message
    assert "foo-agent" in message
    for runner_id in AGENT_IMAGE_MAPPING:
        assert f"{runner_id}-agent" in message


def test_unmapped_runner_from_flag_names_the_flag():
    with pytest.raises(UnmappedAgentRunnerError, match="--agent"):
        resolve_agent_runner("foo", {})
    with pytest.raises(ValueError):
        image_for_agent("foo-agent")


def test_every_mapped_runner_has_a_default_model_and_image():
    assert set(DEFAULT_MODELS) == set(AGENT_IMAGE_MAPPING)
    assert image_for_agent("claude-agent") == "holon/agent-claude"


@pytest.mark.parametrize(
    ("explicit", "environ", "expected", "source"),
    [
        ("m-flag", {"HOLON_MODEL": "m-env"}, "m-flag", "flag"),
        (None, {"HOLON_MODEL": "m-env"}, "m-env", "env"),
    ],
)
def test_model_precedence(explicit, environ, expected, source):
    res = resolve_model("claude-agent", explicit, environ)
    assert (res.value, res.source) == (expected, source)


def test_missing_model_raises_error():
    with pytest.raises(MissingModelError, match="mandatory"):
        resolve_model("claude-agent", None, {})


def test_missing_model_error_mentions_how_to_fix():
    with pytest.raises(MissingModelError) as exc_info:
        resolve_model("antigravity-agent", None, {})
    msg = str(exc_info.value)
    assert "--model" in msg or "HOLON_MODEL" in msg
    assert "gemini-3.8-flash-medium" in msg


def test_antigravity_default_model_table_entry_is_accepted_by_agy():
    assert DEFAULT_MODELS["antigravity"] != "gemini-3.5-flash"
    # The label carried by successful agy runs in the ledger, so calibration series stay continuous.
    assert DEFAULT_MODELS["antigravity"] == "gemini-3.8-flash-medium"


def test_legacy_constants_are_fixed():
    assert (LEGACY_DEFAULT_AGENT, LEGACY_DEFAULT_MODEL) == ("antigravity-agent", "gemini-3.8-flash-medium")


def test_cli_reexports_image_mapping():
    from sandbox_executor import cli

    assert cli.AGENT_IMAGE_MAPPING is AGENT_IMAGE_MAPPING


def test_claude_api_key_detection_uses_the_secrets_file_not_holon_agent_key(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    bundle = tmp_path / "bundle.json"
    bundle.write_text("{}", encoding="utf-8")
    env = {"HOLON_AGENT_KEY": "fake-generic", "HOLON_SECRET_BUNDLE_PATH": str(bundle)}
    assert agent_defaults._detect_api_key_present("claude", env) is False
    assert agent_defaults._detect_api_key_present("pi", env) is True

    secrets = tmp_path / ".holon" / "secrets" / "claude-oauth-token"
    secrets.parent.mkdir(parents=True)
    secrets.write_text("  \n", encoding="utf-8")
    assert agent_defaults._detect_api_key_present("claude", {}) is False
    secrets.write_text("fake-claude-value\n", encoding="utf-8")
    secrets.chmod(0o600)
    assert agent_defaults._detect_api_key_present("claude", {}) is True


@pytest.mark.parametrize("gemini_runner", ["gemini", "gemini-agent"])
def test_gemini_runner_is_rejected_via_unmapped_runner_error(gemini_runner: str):
    with pytest.raises(UnmappedAgentRunnerError) as exc_info:
        resolve_agent_runner(gemini_runner, {})
    msg = str(exc_info.value)
    assert f"Agent runner '{gemini_runner}' (from --agent) has no container image." in msg
    assert "Supported runners:" in msg
    assert "antigravity-agent" in msg
    assert "claude-agent" in msg
    assert "codex-agent" in msg
    assert "opencode-agent" in msg
    assert "pi-agent" in msg


@pytest.mark.parametrize("gemini_runner", ["gemini", "gemini-agent"])
def test_get_runner_rejects_gemini_with_unsupported_agent_message(
    gemini_runner: str, capsys: pytest.CaptureFixture[str]
):
    with pytest.raises(SystemExit) as exc_info:
        get_runner(gemini_runner)
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert f"Error: Unsupported agent '{gemini_runner}'." in output
    assert "Supported agents are: antigravity, claude, codex, opencode, pi" in output


def test_antigravity_gemini_models_and_local_model_preserved_in_capabilities():
    caps = {cap.canonical_model for cap in RUNNER_CAPABILITIES if cap.runner == "antigravity-agent"}
    assert "gemini-3.8-flash" in caps
    assert "gemini-3.8-flash-medium" in caps
    assert "gemini-3.8-flash-high" in caps

    local_caps = [cap for cap in RUNNER_CAPABILITIES if cap.runner == "local-model"]
    assert len(local_caps) == 1
    assert local_caps[0].canonical_model == "local-model"


@pytest.mark.parametrize(
    ("raw_model", "effort", "expected_model", "expected_effort"),
    [
        ("gemini-3.8-flash", "low", "gemini-3.8-flash-low", "low"),
        ("gemini-3.8-flash-medium", "medium", "gemini-3.8-flash-medium", "medium"),
        ("gemini-3.8-flash", "high", "gemini-3.8-flash-high", "high"),
        ("gemini-3.8-flash-medium", None, "gemini-3.8-flash-medium", "medium"),
        ("gemini-3.8-flash", None, "gemini-3.8-flash", None),
    ],
)
def test_antigravity_gemini_effort_coordination(
    raw_model: str, effort: str | None, expected_model: str, expected_effort: str | None
):
    canonical, coordinated_effort = normalise_model_and_effort("antigravity", raw_model, effort)
    assert canonical == expected_model
    assert coordinated_effort == expected_effort
