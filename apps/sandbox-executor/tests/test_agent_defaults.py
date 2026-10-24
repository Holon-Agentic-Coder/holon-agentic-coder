"""Tests for the shared agent runner and model resolver (sandbox_executor.agent_defaults)."""

import logging

import pytest
from sandbox_executor import agent_defaults
from sandbox_executor.agent_defaults import (
    AGENT_IMAGE_MAPPING,
    DEFAULT_MODELS,
    FALLBACK_AGENT,
    LEGACY_DEFAULT_AGENT,
    LEGACY_DEFAULT_MODEL,
    UnmappedAgentRunnerError,
    canonical_agent_name,
    detect_harness_agent,
    image_for_agent,
    normalise_agent_id,
    resolve_agent_runner,
    resolve_model,
)


@pytest.mark.parametrize(
    ("explicit", "environ", "expected", "source"),
    [
        ("pi-agent", {"HOLON_AGENT": "codex-agent", "CLAUDECODE": "1"}, "pi-agent", "flag"),
        (None, {"HOLON_AGENT": "codex-agent", "CLAUDECODE": "1"}, "codex-agent", "env"),
        ("", {"HOLON_AGENT": "codex", "CLAUDECODE": "1"}, "codex-agent", "env"),
        (None, {"CLAUDECODE": "1"}, "claude-agent", "harness"),
        (None, {}, FALLBACK_AGENT, "fallback"),
    ],
)
def test_agent_precedence(explicit, environ, expected, source):
    res = resolve_agent_runner(explicit, environ)
    assert (res.value, res.source) == (expected, source)


def test_fallback_is_logged_at_info(caplog):
    with caplog.at_level(logging.INFO, logger=agent_defaults.__name__):
        res = resolve_agent_runner(None, {})
    assert res.source == "fallback"
    assert any(
        r.levelno == logging.INFO and "antigravity-agent" in r.getMessage() and "source: fallback" in r.getMessage()
        for r in caplog.records
    )


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
    res = resolve_agent_runner(None, environ)
    assert res.source == "fallback"
    assert "sandbox" in res.reason


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
        (None, {}, DEFAULT_MODELS["claude"], "table"),
    ],
)
def test_model_precedence(explicit, environ, expected, source):
    res = resolve_model("claude-agent", explicit, environ)
    assert (res.value, res.source) == (expected, source)


def test_antigravity_default_model_is_accepted_by_agy():
    default = resolve_model("antigravity-agent", None, {}).value
    assert default == DEFAULT_MODELS["antigravity"]
    assert default != "gemini-3.5-flash"
    # The label carried by successful agy runs in the ledger, so calibration series stay continuous.
    assert default == "gemini-3.8-flash-medium"


def test_legacy_constants_are_fixed():
    assert (LEGACY_DEFAULT_AGENT, LEGACY_DEFAULT_MODEL) == ("antigravity-agent", "gemini-3.8-flash-medium")


def test_cli_reexports_image_mapping():
    from sandbox_executor import cli

    assert cli.AGENT_IMAGE_MAPPING is AGENT_IMAGE_MAPPING
