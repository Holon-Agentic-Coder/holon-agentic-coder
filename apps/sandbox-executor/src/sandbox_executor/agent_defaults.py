"""Single source of truth for the default agent runner and model.

Holon used to hard-code ``antigravity-agent`` and a model literal at every call site, and the literals drifted apart.
This module resolves both with a documented precedence and logs every decision together with its source:

* Agent runner: explicit ``--agent``, then ``HOLON_AGENT``, then the harness agent detected from the runtime
  environment; raises :class:`MissingAgentRunnerError` if none is supplied.
* Model: explicit ``--model``, then ``HOLON_MODEL``; raises :class:`MissingModelError` if none is supplied.

Harness detection is disabled inside a Holon sandbox (``HOLON_ROLE`` or ``HOLON_IN_SANDBOX`` set), because there it
would pick up the container's own agent rather than the operator's harness. It also ignores credential variables such
as ``AGY_USER_TOKEN`` and ``GOOGLE_API_KEY``, which the container dispatcher exports and which say nothing about which
agent process is running.

The module is side-effect free apart from logging; ``environ`` is injectable so tests never touch ``os.environ``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Historical fallback identifier preserved for backfilling and default model lookup.
FALLBACK_AGENT = "antigravity-agent"

# Fixed values for back-filling provenance on historical records (old checkpoints, ledger rows without agent/model).
# These describe what produced those records, so they must never follow runtime detection or the current defaults.
LEGACY_DEFAULT_AGENT = "antigravity-agent"
LEGACY_DEFAULT_MODEL = "gemini-3.8-flash-medium"

AGENT_IMAGE_MAPPING: dict[str, str] = {
    "antigravity": "holon/agent-antigravity",
    "claude": "holon/agent-claude",
    "pi": "holon/agent-pi",
    "codex": "holon/agent-codex",
    "gemini": "holon/agent-gemini",
    "opencode": "holon/agent-opencode",
}

# Default model per normalised runner id. The antigravity entry is the label carried by every successful agy run in the
# ledger (agy accepts it together with ``--effort``); the old ``gemini-3.5-flash`` default is rejected by current agy.
# The other entries are best-effort and can be overridden with ``--model`` or ``HOLON_MODEL``; ``holon doctor``
# reports a rejected model before any work starts.
DEFAULT_MODELS: dict[str, str] = {
    "antigravity": "gemini-3.8-flash-medium",
    "claude": "claude-opus-5-5",
    "pi": "claude-opus-5-5",
    "codex": "gpt-5-codex",
    "gemini": "gemini-3.8-flash",
    "opencode": "anthropic/claude-opus-5-5",
}

# Variables whose presence means "running inside a Holon sandbox"; harness detection is off when any is set.
SANDBOX_GUARD_VARS: tuple[str, ...] = ("HOLON_ROLE", "HOLON_IN_SANDBOX")

# Ordered harness markers: (runner id, variable name or prefix, match kind). The tuple order is the tie-break when
# several harnesses are detected at once.
HARNESS_MARKERS: tuple[tuple[str, str, str], ...] = (
    ("pi", "PI_CODING_AGENT", "exact"),
    ("claude", "CLAUDECODE", "exact"),
    ("claude", "CLAUDE_CODE_ENTRYPOINT", "exact"),
    ("codex", "CODEX_", "prefix"),
    ("opencode", "OPENCODE", "exact"),
    ("opencode", "OPENCODE_", "prefix"),
    ("antigravity", "AGY_", "prefix"),
    ("antigravity", "ANTIGRAVITY_", "prefix"),
)

# Variables that never count as harness markers even when they match a prefix: they are credentials or user
# configuration, which can be exported in a shell profile without the agent running.
NON_MARKER_VARS: frozenset[str] = frozenset(
    {
        "AGY_USER_TOKEN",
        "GOOGLE_API_KEY",
        "CODEX_API_KEY",
        "CODEX_HOME",
        "OPENCODE_API_KEY",
        "OPENCODE_CONFIG",
        "OPENCODE_CONFIG_DIR",
    }
)
_CREDENTIAL_SUFFIXES: tuple[str, ...] = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")
_FALSY_VALUES = frozenset({"", "0", "false", "no", "off"})


class UnmappedAgentRunnerError(ValueError):
    """Raised when the resolved agent runner has no container image in :data:`AGENT_IMAGE_MAPPING`."""


class MissingAgentRunnerError(ValueError):
    """Raised when no agent runner could be resolved and no fallback default is in effect.

    The caller must supply ``--agent`` or set ``HOLON_AGENT``, or run from a recognised agent harness.
    """


class MissingModelError(ValueError):
    """Raised when no model could be resolved and no fallback default is in effect.

    The caller must supply ``--model`` or set ``HOLON_MODEL``.
    """


@dataclass(frozen=True)
class Resolution:
    """Outcome of a default resolution.

    Attributes:
        value: The resolved value (canonical ``{id}-agent`` for runners, the model name for models).
        source: Which tier supplied it: ``flag``, ``env``, ``harness``, ``fallback`` or ``table``; ``holon flow``
            also uses ``checkpoint`` for a value kept from a resumed checkpoint.
        reason: Human-readable explanation suitable for logs, for example the matching marker.
    """

    value: str
    source: str
    reason: str

    def describe(self, kind: str) -> str:
        """Return a one-line log message such as ``Agent runner resolved to x (source: env, HOLON_AGENT)``."""
        return f"{kind} resolved to {self.value} (source: {self.source}, {self.reason})"


def normalise_agent_id(name: str) -> str:
    """Normalise a runner name to its registry id (``pi-agent``, ``agent-pi`` and ``Pi`` all give ``pi``)."""
    return name.strip().lower().replace("-agent", "").replace("agent-", "")


def canonical_agent_name(name: str) -> str:
    """Return the canonical ``{id}-agent`` form recorded in ledger rows and plan ids."""
    return f"{normalise_agent_id(name)}-agent"


def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _is_set(value: str | None) -> bool:
    return value is not None and value.strip().lower() not in _FALSY_VALUES


def _is_marker_name(name: str) -> bool:
    return name not in NON_MARKER_VARS and not name.endswith(_CREDENTIAL_SUFFIXES)


def harness_detection_disabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return True when running inside a Holon sandbox, where harness detection must not apply."""
    env = _env(environ)
    return any(_is_set(env.get(var)) for var in SANDBOX_GUARD_VARS)


def detected_harness_markers(environ: Mapping[str, str] | None = None) -> list[tuple[str, str]]:
    """List every ``(runner id, variable)`` harness marker present, in tie-break order.

    Does not apply the sandbox guard; use :func:`detect_harness_agent` for the decision.
    """
    env = _env(environ)
    found: list[tuple[str, str]] = []
    for runner_id, marker, kind in HARNESS_MARKERS:
        if kind == "exact":
            names = [marker] if marker in env else []
        else:
            names = sorted(name for name in env if name.startswith(marker))
        for name in names:
            if _is_marker_name(name) and _is_set(env.get(name)) and (runner_id, name) not in found:
                found.append((runner_id, name))
    return found


def detect_harness_agent(environ: Mapping[str, str] | None = None) -> tuple[str, str] | None:
    """Detect the agent harness Holon was launched from.

    Args:
        environ: Environment mapping to inspect (defaults to ``os.environ``).

    Returns:
        ``(canonical runner name, marker variable)`` for the first match in :data:`HARNESS_MARKERS` order, or None when
        no marker is present or detection is disabled by the sandbox guard.

    Side effects:
        Logs the ignored markers when several harnesses are detected.
    """
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
    """Resolve the agent runner: explicit flag, then ``HOLON_AGENT``, then harness detection.

    Args:
        explicit: Value of ``--agent`` (None or empty when not given).
        environ: Environment mapping to inspect (defaults to ``os.environ``).
        log: False when the caller reports the decision itself (``holon doctor``), so it is not also logged.

    Returns:
        A :class:`Resolution` whose value is the canonical ``{id}-agent`` name.

    Raises:
        MissingAgentRunnerError: If no agent runner was provided, configured, or detected.
        UnmappedAgentRunnerError: If the selected runner has no entry in :data:`AGENT_IMAGE_MAPPING`; the message lists
            the supported runners and names the flag or variable that supplied the value.

    Side effects:
        Logs the decision at INFO level unless ``log`` is False.
    """
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
    """Return the table default model for a runner name, or the antigravity default for an unknown runner."""
    return DEFAULT_MODELS.get(normalise_agent_id(agent), DEFAULT_MODELS[normalise_agent_id(FALLBACK_AGENT)])


def resolve_model(
    agent: str, explicit: str | None, environ: Mapping[str, str] | None = None, *, log: bool = True
) -> Resolution:
    """Resolve the model: explicit flag, then ``HOLON_MODEL``; raises :class:`MissingModelError` if omitted.

    Args:
        agent: Resolved runner name (any form accepted by :func:`normalise_agent_id`).
        explicit: Value of ``--model`` (None or empty when not given).
        environ: Environment mapping to inspect (defaults to ``os.environ``).
        log: False when the caller reports the decision itself (``holon doctor``), so it is not also logged.

    Returns:
        A :class:`Resolution` holding the model name.

    Raises:
        MissingModelError: If neither ``--model`` nor ``HOLON_MODEL`` was provided.

    Side effects:
        Logs the decision at INFO level unless ``log`` is False.
    """
    env = _env(environ)
    if explicit and explicit.strip():
        resolution = Resolution(explicit.strip(), "flag", "--model")
    elif (env.get("HOLON_MODEL") or "").strip():
        resolution = Resolution(env["HOLON_MODEL"].strip(), "env", "HOLON_MODEL")
    else:
        raise MissingModelError(
            "Model is mandatory: no --model or HOLON_MODEL provided. "
            f"Provide --model or set HOLON_MODEL (e.g. {default_model_for(agent)} for {canonical_agent_name(agent)})."
        )
    if log:
        logger.info(resolution.describe("Model"))
    return resolution


def image_for_agent(agent: str) -> str:
    """Return the container image for a runner name.

    Raises:
        UnmappedAgentRunnerError: If the runner has no mapped image.
    """
    _require_mapped(agent, "the agent runner")
    return AGENT_IMAGE_MAPPING[normalise_agent_id(agent)]
