from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

from sandbox_executor.runners import RUNNER_SPECS, runner_spec_for
from sandbox_executor.runners.spec import _VALID_EFFORT_LEVELS
from sandbox_executor.runners.spec import _ModelCapability as ModelCapability

logger = logging.getLogger(__name__)

FALLBACK_AGENT = "antigravity-agent"

LEGACY_DEFAULT_AGENT = "antigravity-agent"
LEGACY_DEFAULT_MODEL = "gemini-3.8-flash-medium"

AGENT_IMAGE_MAPPING: dict[str, str] = {agent_id: spec.image for agent_id, spec in RUNNER_SPECS.items()}

AGENT_TICKERS: dict[str, str] = {
    agent_id: spec.ticker for agent_id, spec in RUNNER_SPECS.items() if spec.ticker is not None
}

DEFAULT_MODELS: dict[str, str] = {agent_id: spec.default_model for agent_id, spec in RUNNER_SPECS.items()}

_DEFAULT_SECRET_BUNDLE_PATH = "/run/secrets/holon_auth.json"
_LOCAL_LLM_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})

_LOCAL_MODEL_CAPABILITY = ModelCapability(
    runner="local-model",
    canonical_model="local-model",
    aliases=["local"],
    effort_levels=[],
    fallback_tier="Local Fallback",
)

RUNNER_CAPABILITIES: tuple[ModelCapability, ...] = (
    *(cap for spec in RUNNER_SPECS.values() for cap in spec.model_capabilities),
    _LOCAL_MODEL_CAPABILITY,
)


def _capability_matches_model(capability: ModelCapability, model_lower: str) -> bool:
    return model_lower == capability.canonical_model.lower() or model_lower in (
        alias.lower() for alias in capability.aliases
    )


def get_model_capabilities(agent: str | None = None) -> list[ModelCapability]:
    if agent is None:
        return list(RUNNER_CAPABILITIES)
    norm = normalise_agent_id(agent)
    return [
        cap
        for cap in RUNNER_CAPABILITIES
        if normalise_agent_id(cap.runner) == norm or (norm in ("local", "local-model") and cap.runner == "local-model")
    ]


def get_available_models_for_runner(agent: str) -> list[str]:
    seen: set[str] = set()
    models: list[str] = []
    for cap in get_model_capabilities(agent):
        if cap.canonical_model not in seen:
            seen.add(cap.canonical_model)
            models.append(cap.canonical_model)
    return models


def is_valid_model_for_runner(agent: str, model: str) -> bool:
    spec = runner_spec_for(agent)
    matching_caps = get_model_capabilities(agent)
    if not matching_caps:
        return True
    m_lower = model.strip().lower()
    for cap in matching_caps:
        if _capability_matches_model(cap, m_lower):
            return True
        if (
            spec is not None
            and spec.supports_effort_suffix
            and any(m_lower == f"{cap.canonical_model.lower()}-{eff}" for eff in _VALID_EFFORT_LEVELS)
        ):
            return True
    return False


def normalise_model_and_effort(agent: str, model: str | None, effort: str | None = None) -> tuple[str, str | None]:
    raw_model = (model or "").strip() or default_model_for(agent)
    raw_lower = raw_model.lower()
    canonical = raw_model
    for cap in get_model_capabilities(agent):
        if _capability_matches_model(cap, raw_lower):
            canonical = cap.canonical_model
            break

    spec = runner_spec_for(agent)
    if spec is not None:
        return spec.normalise_model(canonical, effort)

    return canonical, None


def _detect_api_key_present(norm: str, env: Mapping[str, str]) -> bool:
    spec = RUNNER_SPECS.get(norm)
    if spec is not None and spec.api_key_detector is not None:
        return spec.api_key_detector(env)

    bundle_path = env.get("HOLON_SECRET_BUNDLE_PATH") or _DEFAULT_SECRET_BUNDLE_PATH
    if (spec is None or spec.uses_holon_agent_key) and (
        os.path.isfile(bundle_path) or _is_set(env.get("HOLON_AGENT_KEY"))
    ):
        return True

    if spec is not None:
        return any(_is_set(env.get(k)) for k in spec.host_api_key_env_vars)

    return False


def _detect_session_dir_present(norm: str) -> bool:
    spec = RUNNER_SPECS.get(norm)
    return spec.detect_session_dir() if spec is not None else False


def _detect_harness_marker_present(norm: str, env: Mapping[str, str]) -> bool:
    markers = detected_harness_markers(env)
    return any(normalise_agent_id(m_runner) == norm for m_runner, _m_var in markers)


def _detect_local_gateway_present(norm: str, env: Mapping[str, str]) -> bool:
    if norm in ("local", "local-model"):
        return True
    spec = RUNNER_SPECS.get(norm)
    return bool(
        spec and spec.supports_local_llm and env.get("HOLON_LOCAL_LLM", "").strip().lower() in _LOCAL_LLM_TRUTHY_VALUES
    )


def detect_runner_auth_status(agent: str, environ: Mapping[str, str] | None = None) -> str:
    env = _env(environ)
    norm = normalise_agent_id(agent)

    if _detect_api_key_present(norm, env):
        return "Detected (API key)"
    if _detect_session_dir_present(norm):
        return "Detected (session directory)"
    if _detect_harness_marker_present(norm, env):
        return "Detected (harness marker)"
    if _detect_local_gateway_present(norm, env):
        return "Detected (local gateway)"

    return "Missing / Not detected"


SANDBOX_GUARD_VARS: tuple[str, ...] = ("HOLON_ROLE", "HOLON_IN_SANDBOX")

HARNESS_MARKERS: tuple[tuple[str, str, str], ...] = tuple(
    (spec.agent_id, marker, kind)
    for spec in sorted(RUNNER_SPECS.values(), key=lambda spec: spec.harness_marker_priority)
    for marker, kind in spec.harness_markers
)

NON_MARKER_VARS: frozenset[str] = frozenset(var for spec in RUNNER_SPECS.values() for var in spec.non_marker_vars)
_CREDENTIAL_SUFFIXES: tuple[str, ...] = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")
_FALSY_VALUES = frozenset({"", "0", "false", "no", "off"})


class UnmappedAgentRunnerError(ValueError):
    pass


class MissingAgentRunnerError(ValueError):
    pass


class MissingModelError(ValueError):
    pass


@dataclass(frozen=True)
class Resolution:
    value: str
    source: str
    reason: str

    def describe(self, kind: str) -> str:
        return f"{kind} resolved to {self.value} (source: {self.source}, {self.reason})"


def normalise_agent_id(name: str) -> str:
    return name.strip().lower().replace("-agent", "").replace("agent-", "")


def canonical_agent_name(name: str) -> str:
    return f"{normalise_agent_id(name)}-agent"


def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _is_set(value: str | None) -> bool:
    return value is not None and value.strip().lower() not in _FALSY_VALUES


def _is_marker_name(name: str) -> bool:
    return name not in NON_MARKER_VARS and not name.endswith(_CREDENTIAL_SUFFIXES)


def _matching_marker_names(env: Mapping[str, str], marker: str, match_kind: str) -> list[str]:
    if match_kind == "exact":
        return [marker] if marker in env else []
    return sorted(name for name in env if name.startswith(marker))


def harness_detection_disabled(environ: Mapping[str, str] | None = None) -> bool:
    env = _env(environ)
    return any(_is_set(env.get(var)) for var in SANDBOX_GUARD_VARS)


def detected_harness_markers(environ: Mapping[str, str] | None = None) -> list[tuple[str, str]]:
    env = _env(environ)
    found: list[tuple[str, str]] = []
    for runner_id, marker, kind in HARNESS_MARKERS:
        for name in _matching_marker_names(env, marker, kind):
            if _is_marker_name(name) and _is_set(env.get(name)) and (runner_id, name) not in found:
                found.append((runner_id, name))
    return found


def detect_harness_agent(environ: Mapping[str, str] | None = None) -> tuple[str, str] | None:
    if harness_detection_disabled(environ):
        return None
    markers = detected_harness_markers(environ)
    if not markers:
        return None
    winner_id, winner_marker = markers[0]
    others = [f"{name} ({rid})" for rid, name in markers[1:] if rid != winner_id]
    if others:
        logger.info(
            "Several agent harnesses detected; using %s from %s and ignoring %s",
            winner_id,
            winner_marker,
            ", ".join(others),
        )
    return canonical_agent_name(winner_id), winner_marker


def _require_mapped(name: str, origin: str) -> None:
    if normalise_agent_id(name) not in AGENT_IMAGE_MAPPING:
        supported = ", ".join(f"{rid}-agent" for rid in sorted(AGENT_IMAGE_MAPPING))
        raise UnmappedAgentRunnerError(
            f"Agent runner '{name}' (from {origin}) has no container image. Supported runners: {supported}. "
            f"Fix {origin} or pass --agent with one of the supported runners."
        )


def resolve_agent_runner(
    explicit: str | None, environ: Mapping[str, str] | None = None, *, log: bool = True
) -> Resolution:
    env = _env(environ)
    if explicit and explicit.strip():
        _require_mapped(explicit, "--agent")
        resolution = Resolution(canonical_agent_name(explicit), "flag", "--agent")
    elif (env.get("HOLON_AGENT") or "").strip():
        value = env["HOLON_AGENT"]
        _require_mapped(value, "HOLON_AGENT")
        resolution = Resolution(canonical_agent_name(value), "env", "HOLON_AGENT")
    elif (detected := detect_harness_agent(env)) is not None:
        name, marker = detected
        resolution = Resolution(name, "harness", f"harness detection, marker {marker}")
    else:
        reason = "no --agent, HOLON_AGENT or harness marker"
        if harness_detection_disabled(env):
            reason += "; harness detection is off inside the sandbox"
        raise MissingAgentRunnerError(
            f"Agent runner is mandatory ({reason}). "
            "Provide --agent, set HOLON_AGENT, or run from a recognised agent harness."
        )
    if log:
        logger.info(resolution.describe("Agent runner"))
    return resolution


def default_model_for(agent: str) -> str:
    return DEFAULT_MODELS.get(normalise_agent_id(agent), DEFAULT_MODELS[normalise_agent_id(FALLBACK_AGENT)])


def _format_available_models_hint(agent: str) -> str:
    norm_agent = normalise_agent_id(agent)
    available_models = get_available_models_for_runner(agent)
    models_list = ", ".join(available_models)
    available_str = f" Supported models for {canonical_agent_name(agent)}: {models_list}." if available_models else ""
    hint_str = f" Run 'holon models --agent {norm_agent}' to inspect supported models and effort levels."
    return f"{available_str}{hint_str}"


def _build_explicit_model_resolution(
    agent: str,
    model_name: str,
    source: str,
    source_label: str,
    *,
    validate: bool,
) -> Resolution:
    if validate and not is_valid_model_for_runner(agent, model_name):
        hint = _format_available_models_hint(agent)
        raise MissingModelError(f"Unrecognised model '{model_name}' for {canonical_agent_name(agent)}.{hint}")
    canonical, _ = normalise_model_and_effort(agent, model_name)
    reason = f"{source_label} (alias '{model_name}')" if canonical != model_name else source_label
    return Resolution(canonical, source, reason)


def resolve_model(
    agent: str,
    explicit: str | None,
    environ: Mapping[str, str] | None = None,
    *,
    log: bool = True,
    validate: bool = False,
) -> Resolution:
    env = _env(environ)
    if explicit and explicit.strip():
        resolution = _build_explicit_model_resolution(agent, explicit.strip(), "flag", "--model", validate=validate)
    elif (env.get("HOLON_MODEL") or "").strip():
        resolution = _build_explicit_model_resolution(
            agent, env["HOLON_MODEL"].strip(), "env", "HOLON_MODEL", validate=validate
        )
    else:
        hint = _format_available_models_hint(agent)
        raise MissingModelError(
            "Model is mandatory: no --model or HOLON_MODEL provided. "
            f"Provide --model or set HOLON_MODEL (e.g. {default_model_for(agent)} for {canonical_agent_name(agent)})."
            f"{hint}"
        )
    if log:
        logger.info(resolution.describe("Model"))
    return resolution


def image_for_agent(agent: str) -> str:
    _require_mapped(agent, "the agent runner")
    return AGENT_IMAGE_MAPPING[normalise_agent_id(agent)]
