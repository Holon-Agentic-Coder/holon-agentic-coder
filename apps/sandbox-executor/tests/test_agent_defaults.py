"""Tests for the shared agent runner and model resolver (sandbox_executor.agent_defaults)."""

import logging

import pytest
from sandbox_executor import agent_defaults
from sandbox_executor.agent_defaults import (
    AGENT_IMAGE_MAPPING,
    DEFAULT_MODELS,
    LEGACY_DEFAULT_AGENT,
    LEGACY_DEFAULT_MODEL,
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


def test_claude_api_key_detection_uses_the_keychain_not_holon_agent_key(tmp_path, monkeypatch, fake_keychain):
    monkeypatch.setenv("HOME", str(tmp_path))
    bundle = tmp_path / "bundle.json"
    bundle.write_text("{}", encoding="utf-8")
    env = {"HOLON_AGENT_KEY": "fake-generic", "HOLON_SECRET_BUNDLE_PATH": str(bundle)}
    assert agent_defaults._detect_api_key_present("claude", env) is False
    assert agent_defaults._detect_api_key_present("pi", env) is True

    fake_keychain.lookup_override = (0, "  \n", "")
    assert agent_defaults._detect_api_key_present("claude", {}) is False
    fake_keychain.lookup_override = None
    fake_keychain.entry = "fake-claude-value"
    assert agent_defaults._detect_api_key_present("claude", {}) is True


def test_claude_api_key_detection_accepts_the_layered_oauth_token(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert agent_defaults._detect_api_key_present("claude", {"HOLON_AGENT_OAUTH_TOKEN": "sk-ant-oat01-fake"}) is True


@pytest.mark.parametrize(
    ("alias", "expected_canonical"),
    [
        ("fable", "claude-fable-5-2"),
        ("claude-fable", "claude-fable-5-2"),
        ("claude-fable-5-2", "claude-fable-5-2"),
        ("opus", "claude-opus-5-5"),
        ("claude-opus", "claude-opus-5-5"),
        ("claude-opus-5-5", "claude-opus-5-5"),
    ],
)
def test_claude_fable_and_opus_normalisation(alias, expected_canonical):
    assert normalise_model_and_effort("claude-agent", alias) == (expected_canonical, None)


def test_resolve_model_canonicalises_alias_and_logs_both_alias_and_canonical_id(caplog):
    with caplog.at_level(logging.INFO, logger=agent_defaults.__name__):
        res_flag = resolve_model("claude-agent", "fable", {})
    assert res_flag.value == "claude-fable-5-2"
    assert res_flag.source == "flag"
    assert "fable" in res_flag.reason
    assert "claude-fable-5-2" in res_flag.describe("Model")
    assert "fable" in res_flag.describe("Model")
    assert any("claude-fable-5-2" in r.getMessage() and "fable" in r.getMessage() for r in caplog.records)

    caplog.clear()
    with caplog.at_level(logging.INFO, logger=agent_defaults.__name__):
        res_env = resolve_model("claude-agent", None, {"HOLON_MODEL": "claude-fable"})
    assert res_env.value == "claude-fable-5-2"
    assert res_env.source == "env"
    assert "claude-fable" in res_env.reason
    assert "claude-fable-5-2" in res_env.describe("Model")
    assert "claude-fable" in res_env.describe("Model")
    assert any("claude-fable-5-2" in r.getMessage() and "claude-fable" in r.getMessage() for r in caplog.records)


def test_default_models_per_runner_unchanged():
    assert DEFAULT_MODELS == {
        "antigravity": "gemini-3.8-flash-medium",
        "claude": "claude-opus-5-5",
        "pi": "claude-opus-5-5",
        "codex": "gpt-5-codex",
        "opencode": "anthropic/claude-opus-5-5",
    }


def test_agent_tables_pinned_and_derived_from_runner_specs():
    from sandbox_executor.agent_defaults import AGENT_TICKERS
    from sandbox_executor.runners import RUNNER_SPECS

    assert AGENT_IMAGE_MAPPING == {
        "antigravity": "holon/agent-antigravity",
        "claude": "holon/agent-claude",
        "pi": "holon/agent-pi",
        "codex": "holon/agent-codex",
        "opencode": "holon/agent-opencode",
    }
    assert AGENT_TICKERS == {"claude": "CLA", "antigravity": "ANA", "pi": "PIA"}
    assert DEFAULT_MODELS == {
        "antigravity": "gemini-3.8-flash-medium",
        "claude": "claude-opus-5-5",
        "pi": "claude-opus-5-5",
        "codex": "gpt-5-codex",
        "opencode": "anthropic/claude-opus-5-5",
    }
    assert {agent_id: spec.image for agent_id, spec in RUNNER_SPECS.items()} == AGENT_IMAGE_MAPPING
    assert {agent_id: spec.default_model for agent_id, spec in RUNNER_SPECS.items()} == DEFAULT_MODELS
    assert {agent_id: spec.ticker for agent_id, spec in RUNNER_SPECS.items() if spec.ticker} == AGENT_TICKERS


def test_capabilities_markers_and_non_marker_vars_derived_from_runner_specs():
    from sandbox_executor.agent_defaults import HARNESS_MARKERS, NON_MARKER_VARS, RUNNER_CAPABILITIES
    from sandbox_executor.runners import RUNNER_SPECS

    expected_caps = tuple(cap for spec in RUNNER_SPECS.values() for cap in spec.model_capabilities)
    assert RUNNER_CAPABILITIES[:-1] == expected_caps
    assert RUNNER_CAPABILITIES[-1].runner == "local-model"

    expected_markers = tuple(
        (spec.agent_id, marker, kind)
        for spec in sorted(RUNNER_SPECS.values(), key=lambda s: s.harness_marker_priority)
        for marker, kind in spec.harness_markers
    )
    assert HARNESS_MARKERS == expected_markers

    expected_non_markers = frozenset(var for spec in RUNNER_SPECS.values() for var in spec.non_marker_vars)
    assert NON_MARKER_VARS == expected_non_markers


def test_model_validation_and_normalisation_honour_runner_specs():
    from sandbox_executor.agent_defaults import is_valid_model_for_runner

    assert is_valid_model_for_runner("antigravity", "gemini-3.8-flash-low") is True
    assert is_valid_model_for_runner("claude", "claude-opus-5-5-low") is False
    assert normalise_model_and_effort("antigravity", "flash", "high") == ("gemini-3.8-flash-high", "high")
    assert normalise_model_and_effort("pi", "pi-default", "high") == ("claude-opus-5-5", None)
    assert normalise_model_and_effort("codex", "codex", "medium") == ("gpt-5-codex", None)
    assert normalise_model_and_effort("opencode", "opencode", "low") == ("anthropic/claude-opus-5-5", None)


def test_detect_runner_auth_status_honours_runner_specs(tmp_path, monkeypatch, fake_keychain):
    import os

    from sandbox_executor.agent_defaults import detect_runner_auth_status
    from sandbox_executor.runners import RUNNER_SPECS

    present: set[str] = set()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(os.path, "exists", lambda p: p in present)
    monkeypatch.setattr(os.path, "isfile", lambda _p: False)

    for agent_id, spec in RUNNER_SPECS.items():
        if spec.host_api_key_env_vars:
            assert detect_runner_auth_status(agent_id, {spec.host_api_key_env_vars[0]: "k"}) == "Detected (API key)"

    for agent_id in ("antigravity", "claude", "codex", "pi"):
        present.clear()
        present.add(os.path.expanduser(RUNNER_SPECS[agent_id].session_detect_paths[0]))
        assert detect_runner_auth_status(agent_id, {}) == "Detected (session directory)"

    present.clear()
    assert detect_runner_auth_status("pi", {"HOLON_LOCAL_LLM": "1"}) == "Detected (local gateway)"
    assert detect_runner_auth_status("codex", {"HOLON_LOCAL_LLM": "1"}) == "Missing / Not detected"
    assert detect_runner_auth_status("local-model", {}) == "Detected (local gateway)"


def test_agent_defaults_has_no_duplicated_per_runner_tables():
    import ast
    from pathlib import Path

    source = Path(agent_defaults.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    assigned_names: set[str] = set()
    function_names: set[str] = set()
    runner_specs_refs: dict[str, bool] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            function_names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned_names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assigned_names.add(node.target.id)
            if node.target.id in ("RUNNER_CAPABILITIES", "HARNESS_MARKERS", "NON_MARKER_VARS") and node.value:
                runner_specs_refs[node.target.id] = any(
                    isinstance(sub, ast.Name) and sub.id == "RUNNER_SPECS" for sub in ast.walk(node.value)
                )

    assert "_RUNNER_API_KEY_ENV_VARS" not in assigned_names
    assert "runner_session_paths" not in assigned_names
    assert "_coordinate_gemini_effort" not in function_names
    assert runner_specs_refs == {
        "RUNNER_CAPABILITIES": True,
        "HARNESS_MARKERS": True,
        "NON_MARKER_VARS": True,
    }
