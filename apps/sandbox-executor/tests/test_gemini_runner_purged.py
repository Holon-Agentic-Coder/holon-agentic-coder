"""Hermetic verification tests confirming gemini-agent runner has been completely purged."""

from __future__ import annotations

from pathlib import Path

import pytest
from sandbox_executor import cli
from sandbox_executor.agent_defaults import (
    AGENT_IMAGE_MAPPING,
    DEFAULT_MODELS,
    RUNNER_CAPABILITIES,
    get_available_models_for_runner,
    get_model_capabilities,
    is_valid_model_for_runner,
    normalise_model_and_effort,
)
from sandbox_executor.agent_runner import get_runner, runners


def test_get_runner_rejects_gemini_via_generic_unsupported_path(capsys: pytest.CaptureFixture[str]) -> None:
    """Verifies get_runner rejects gemini and gemini-agent via standard generic error path."""
    for runner_name in ("gemini", "gemini-agent"):
        with pytest.raises(SystemExit) as exc_info:
            get_runner(runner_name)
        assert exc_info.value.code == 1
        out, _ = capsys.readouterr()
        assert f"Error: Unsupported agent '{runner_name}'." in out
        assert "Supported agents are: antigravity, claude, codex, opencode, pi" in out


def test_cli_models_command_rejects_gemini(capsys: pytest.CaptureFixture[str]) -> None:
    """Verifies 'holon models --agent gemini[-agent]' rejects with exit code 1."""
    for runner_name in ("gemini", "gemini-agent"):
        with pytest.raises(SystemExit) as exc_info:
            cli.main(["models", "--agent", runner_name])
        assert exc_info.value.code == 1
        _, err = capsys.readouterr()
        assert f"Error: Unknown agent runner '{runner_name}'." in err
        assert "Supported runners:" in err


def test_gemini_runner_absent_from_defaults_and_registry() -> None:
    """Verifies gemini runner keys do not exist in runner tables and capabilities."""
    assert "gemini" not in runners
    assert "gemini" not in AGENT_IMAGE_MAPPING
    assert "gemini-agent" not in AGENT_IMAGE_MAPPING
    assert "gemini" not in DEFAULT_MODELS
    assert "gemini-agent" not in DEFAULT_MODELS

    for cap in RUNNER_CAPABILITIES:
        assert cap.runner != "gemini"
        assert cap.runner != "gemini-agent"


def test_antigravity_gemini_models_and_effort_levels_preserved() -> None:
    """Verifies Gemini models under antigravity-agent remain valid with effort levels."""
    agy_models = get_available_models_for_runner("antigravity-agent")
    expected_models = ["gemini-3.8-flash", "gemini-3.8-flash-medium", "gemini-3.8-flash-high"]
    for model in expected_models:
        assert model in agy_models
        assert is_valid_model_for_runner("antigravity-agent", model)
        for effort in ("low", "medium", "high"):
            assert is_valid_model_for_runner("antigravity-agent", f"{model}-{effort}")

    # Test model and effort normalization for antigravity
    canonical, effort = normalise_model_and_effort("antigravity", "gemini-3.8-flash-medium", "high")
    assert canonical == "gemini-3.8-flash-high"
    assert effort == "high"

    canonical_default, effort_default = normalise_model_and_effort("antigravity", "gemini-3.8-flash")
    assert canonical_default == "gemini-3.8-flash"
    assert effort_default is None

    # Test capabilities count and effort levels
    caps = get_model_capabilities("antigravity-agent")
    assert len(caps) == 3
    for cap in caps:
        assert cap.effort_levels == ["low", "medium", "high"]


def test_local_model_preserved_in_runner_capabilities() -> None:
    """Verifies local-model entry in RUNNER_CAPABILITIES remains intact."""
    local_caps = [cap for cap in RUNNER_CAPABILITIES if cap.runner == "local-model"]
    assert len(local_caps) == 1
    assert local_caps[0].canonical_model == "local-model"
    assert local_caps[0].aliases == ["local"]
    assert local_caps[0].fallback_tier == "Local Fallback"


def test_no_special_case_gemini_checks_in_source() -> None:
    """Verifies no special-case gemini branches exist in runner or default modules."""
    executor_src = Path(__file__).resolve().parent.parent / "src" / "sandbox_executor"
    runner_src = (executor_src / "agent_runner.py").read_text()
    defaults_src = (executor_src / "agent_defaults.py").read_text()

    assert 'custom_validator == "gemini"' not in runner_src
    assert 'agent_id == "gemini"' not in runner_src
    assert 'norm == "gemini"' not in runner_src
    assert 'norm_runner == "gemini"' not in runner_src
    assert '"gemini"' not in runner_src
    assert "'gemini'" not in runner_src

    assert 'agent_id == "gemini"' not in defaults_src
    assert 'norm == "gemini"' not in defaults_src
    assert 'norm_runner == "gemini"' not in defaults_src
    assert '("antigravity", "gemini")' not in defaults_src
