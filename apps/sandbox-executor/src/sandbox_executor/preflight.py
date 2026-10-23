"""Pre-flight readiness probe for agent runners (``holon doctor``).

The probe sends one minimal prompt through the selected runner inside its normal container, with the same session
mounts, gateway host entries and network isolation as a real run, then classifies the outcome. ``holon plan``,
``holon execute`` and ``holon flow`` run it before they create a branch, push, save a checkpoint or append a ledger
row, so a quota, login or key problem is reported up front instead of after setup work has started.

The probe never runs git, never writes under the repository and never writes to any ledger.
"""

from __future__ import annotations

import enum
import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from sandbox_executor import local_llm
from sandbox_executor.agent_defaults import AGENT_IMAGE_MAPPING, normalise_agent_id

PROBE_PROMPT = "Reply with the single word OK."
DEFAULT_TIMEOUT_SECONDS = 60.0
TIMEOUT_ENV_VAR = "HOLON_PREFLIGHT_TIMEOUT"
SKIP_ENV_VAR = "HOLON_SKIP_PREFLIGHT"
_EXCERPT_CHARS = 800
_TRUTHY_ENV_VALUES = ("1", "true", "yes", "on")
# Variables whose values are masked in a probe excerpt. HOLON_AGENT_SETTINGS can carry CLAUDE_CODE_OAUTH_TOKEN.
_SENSITIVE_ENV_VARS = ("HOLON_AGENT_KEY", "HOLON_AGENT_SETTINGS", "GITHUB_TOKEN", "GH_TOKEN")
# Bare Anthropic API keys and OAuth tokens (sk-ant-...), which the generic sweep does not recognise on their own.
_ANTHROPIC_TOKEN_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]+")


class PreflightStatus(enum.StrEnum):
    """Classified outcome of a readiness probe."""

    OK = "ok"
    QUOTA_EXHAUSTED = "quota_exhausted"
    NOT_LOGGED_IN = "not_logged_in"
    EXPIRED_KEY = "expired_key"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PreflightResult:
    """Result of one readiness probe.

    Attributes:
        status: Classified outcome.
        agent: Runner name that was probed.
        model: Model name that was probed.
        image: Container image used.
        exit_code: Container exit code, or None when the probe did not run to completion.
        output_excerpt: Redacted tail of the combined stdout/stderr.
        hint: Remediation advice for the operator.
        skipped: True when the probe was deliberately not run (the status is then ``ok``).
    """

    status: PreflightStatus
    agent: str
    model: str
    image: str
    exit_code: int | None
    output_excerpt: str
    hint: str
    skipped: bool = False

    @property
    def ok(self) -> bool:
        """True when the runner is ready (or the probe was skipped)."""
        return self.status is PreflightStatus.OK

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        data = asdict(self)
        data["status"] = self.status.value
        return data

    def format_report(self) -> str:
        """Return a multi-line human-readable diagnosis."""
        lines = [
            f"Pre-flight {self.agent} ({self.model}) via {self.image}: {self.status.value.upper()}",
        ]
        if self.exit_code is not None:
            lines.append(f"  exit code: {self.exit_code}")
        if self.hint:
            lines.append(f"  hint: {self.hint}")
        if self.output_excerpt and not self.ok:
            lines.append("  output (redacted):")
            lines.extend(f"    {line}" for line in self.output_excerpt.splitlines())
        return "\n".join(lines)


# Ordered classification table: the first matching status wins.
_PATTERNS: tuple[tuple[PreflightStatus, re.Pattern[str]], ...] = (
    (
        PreflightStatus.QUOTA_EXHAUSTED,
        re.compile(
            r"\b429\b|RESOURCE_EXHAUSTED|quota\s+(?:exceeded|exhausted)|exceeded\s+(?:your|the)\s+(?:current\s+)?quota"
            r"|rate[\s_-]?limit(?:ed)?|too\s+many\s+requests|usage\s+limit",
            re.IGNORECASE,
        ),
    ),
    (
        PreflightStatus.NOT_LOGGED_IN,
        re.compile(
            r"not\s+logged\s+in|please\s+(?:log\s*in|login|sign\s*in|authenticate)|run\s+\S+\s+login"
            r"|missing\s+(?:required\s+)?(?:api\s+)?credentials|no\s+(?:api\s+key|credentials)\s+(?:found|provided)"
            r"|session\s+(?:directory\s+)?(?:not\s+found|missing|does\s+not\s+exist)|authentication\s+required"
            r"|(?:api\s+key|token)\s+(?:is\s+)?(?:not\s+set|missing|required)",
            re.IGNORECASE,
        ),
    ),
    (
        PreflightStatus.EXPIRED_KEY,
        re.compile(
            r"expired|invalid[\s_-]+(?:api[\s_-]+)?(?:key|token|x-api-key|credentials)|\b401\b|UNAUTHENTICATED"
            r"|unauthori[sz]ed|authentication[\s_]+(?:failed|error)",
            re.IGNORECASE,
        ),
    ),
)

_HINTS: dict[PreflightStatus, str] = {
    PreflightStatus.OK: "",
    PreflightStatus.QUOTA_EXHAUSTED: (
        "The provider reports exhausted quota or a rate limit. Wait for the quota to reset, or choose another runner "
        "with --agent or HOLON_AGENT."
    ),
    PreflightStatus.NOT_LOGGED_IN: (
        "No usable credential reached the container. Set HOLON_AGENT_KEY, or initialise and mount the runner's session "
        "(see docs/executor/agent_credentials_requirements.md), or choose another runner with --agent or HOLON_AGENT."
    ),
    PreflightStatus.EXPIRED_KEY: (
        "The provider rejected the credential as invalid or expired. Renew HOLON_AGENT_KEY or re-authorise the "
        "mounted session."
    ),
    PreflightStatus.UNKNOWN: (
        "The probe failed for an unrecognised reason; check the output below. Check that the model name is valid "
        "for this runner (--model or HOLON_MODEL) and that the image exists (make build). Use --skip-preflight to "
        "bypass this check."
    ),
}


def classify_probe_output(exit_code: int | None, output: str) -> PreflightStatus:
    """Classify a probe's exit code and combined output.

    Args:
        exit_code: Container exit code (None when the probe did not complete).
        output: Combined stdout and stderr.

    Returns:
        The first matching failure status in table order; ``ok`` for exit code 0 with non-empty output and no failure
        pattern; ``unknown`` otherwise.
    """
    text = output or ""
    for status, pattern in _PATTERNS:
        if pattern.search(text):
            return status
    if exit_code == 0 and text.strip():
        return PreflightStatus.OK
    return PreflightStatus.UNKNOWN


def preflight_skipped_by_env(environ: Mapping[str, str] | None = None) -> bool:
    """Return True when ``HOLON_SKIP_PREFLIGHT`` is truthy."""
    env = os.environ if environ is None else environ
    return str(env.get(SKIP_ENV_VAR, "")).strip().lower() in _TRUTHY_ENV_VALUES


def resolve_timeout(explicit: float | None = None, environ: Mapping[str, str] | None = None) -> float:
    """Return the probe timeout: explicit value, then ``HOLON_PREFLIGHT_TIMEOUT``, then 60 seconds."""
    if explicit is not None and explicit > 0:
        return float(explicit)
    env = os.environ if environ is None else environ
    try:
        value = float(env.get(TIMEOUT_ENV_VAR, ""))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


def redact_output(text: str) -> str:
    """Mask credential values and token-shaped strings in probe output."""
    from sandbox_executor.entrypoint.executor import redact_text

    redacted = text or ""
    for var in _SENSITIVE_ENV_VARS:
        value = os.getenv(var)
        if value and len(value) >= 4:
            redacted = redacted.replace(value, "***REDACTED***")
    redacted = _ANTHROPIC_TOKEN_RE.sub("***REDACTED***", redacted)
    return redact_text(redacted)


def _has_host_credential(agent_id: str, session_mounts: list[str]) -> bool:
    """Return True when some credential could reach the container, mirroring the in-container validators."""
    if os.getenv("HOLON_AGENT_KEY") or session_mounts:
        return True
    return agent_id == "codex" and os.getenv("HOLON_AGENT_OSS_MODE", "").lower() in ("true", "1")


def build_probe_docker_cmd(
    agent: str, model: str, image: str, container_name: str, session_mounts: list[str]
) -> list[str]:
    """Build the ``docker run`` argv for the probe.

    The container keeps its normal entrypoint with no ``HOLON_ROLE``, so the dispatcher maps ``HOLON_AGENT_KEY`` onto
    the vendor variables and then executes the runner binary directly. The mounts, gateway host entries and network
    isolation come from the same helpers ``run_docker_container`` uses.

    Args:
        agent: Runner name.
        model: Model name.
        image: Container image.
        container_name: Name given to the container so a timed-out probe can be removed.
        session_mounts: Output of ``get_agent_session_mounts`` for this runner.

    Returns:
        The docker argv. Credential values are passed through ``-e`` exactly as for a real run.
    """
    from sandbox_executor.agent_runner import runners
    from sandbox_executor.cli import _gateway_host_args, build_network_isolation_args

    agent_id = normalise_agent_id(agent)
    cmd = ["docker", "run", "--rm", "-i", "--name", container_name]
    env: dict[str, str] = {
        key: value for key, value in os.environ.items() if key.startswith("HOLON_") and key != "HOLON_ROLE"
    }
    env["HOLON_AGENT_ID"] = agent_id
    env[SKIP_ENV_VAR] = "1"
    for key, value in sorted(env.items()):
        cmd.extend(["-e", f"{key}={value}"])
    cmd.extend(_gateway_host_args())
    cmd.extend(session_mounts)
    cmd.extend(build_network_isolation_args())
    cmd.append(image)
    cmd.extend(runners[agent_id].compose_cmd(model, PROBE_PROMPT))
    return cmd


def _result(
    status: PreflightStatus,
    agent: str,
    model: str,
    image: str,
    exit_code: int | None = None,
    output: str = "",
    hint: str | None = None,
    skipped: bool = False,
) -> PreflightResult:
    excerpt = redact_output(output)[-_EXCERPT_CHARS:].strip()
    return PreflightResult(
        status=status,
        agent=agent,
        model=model,
        image=image,
        exit_code=exit_code,
        output_excerpt=excerpt,
        hint=_HINTS[status] if hint is None else hint,
        skipped=skipped,
    )


def run_preflight_probe(
    agent: str,
    model: str,
    timeout: float | None = None,
    runner_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> PreflightResult:
    """Probe a runner with one minimal prompt inside its container.

    Args:
        agent: Resolved runner name (for example ``antigravity-agent``).
        model: Resolved model name.
        timeout: Seconds before the probe is abandoned (defaults via :func:`resolve_timeout`).
        runner_fn: Replacement for ``subprocess.run``, for tests.

    Returns:
        A :class:`PreflightResult`; never raises for probe failures.

    Side effects:
        Runs one ``docker run`` (one real model call). On timeout the probe container is removed with ``docker rm -f``.
        Nothing is written to the repository or the ledgers.
    """
    run = runner_fn or subprocess.run
    agent_id = normalise_agent_id(agent)
    image = AGENT_IMAGE_MAPPING.get(agent_id, f"holon/agent-{agent_id}")
    limit = resolve_timeout(timeout)

    from sandbox_executor.agent_runner import runners

    if agent_id not in runners or agent_id not in AGENT_IMAGE_MAPPING:
        return _result(PreflightStatus.UNKNOWN, agent, model, image, hint=f"Unsupported agent runner '{agent}'.")

    if agent_id == "pi" and local_llm.local_llm_requested():
        return _result(
            PreflightStatus.OK,
            agent,
            model,
            image,
            hint="Probe skipped: host-local model mode has no provider quota or login to check.",
            skipped=True,
        )

    if runner_fn is None and not shutil.which("docker"):
        return _result(
            PreflightStatus.UNKNOWN, agent, model, image, hint="'docker' CLI command not found. Please install Docker."
        )

    from sandbox_executor.cli import get_agent_session_mounts

    try:
        session_mounts = get_agent_session_mounts(agent)
    except SystemExit:
        return _result(
            PreflightStatus.NOT_LOGGED_IN,
            agent,
            model,
            image,
            output="The runner's session directory is missing (see the message above).",
        )
    if not _has_host_credential(agent_id, session_mounts):
        return _result(
            PreflightStatus.NOT_LOGGED_IN,
            agent,
            model,
            image,
            output=f"No HOLON_AGENT_KEY is set and no session credentials for '{agent}' were found to mount.",
        )

    container_name = f"holon-preflight-{uuid.uuid4().hex[:12]}"
    docker_cmd = build_probe_docker_cmd(agent, model, image, container_name, session_mounts)

    try:
        proc = run(docker_cmd, capture_output=True, text=True, timeout=limit, check=False, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        run(["docker", "rm", "-f", container_name], capture_output=True, text=True, check=False)
        partial = _decode(exc.stdout) + _decode(exc.stderr)
        return _result(
            PreflightStatus.UNKNOWN,
            agent,
            model,
            image,
            output=partial,
            hint=(
                f"The probe did not finish within {limit:g}s; the runner may be waiting on an interactive login. "
                f"Initialise the session interactively or raise {TIMEOUT_ENV_VAR}."
            ),
        )
    except OSError as exc:
        return _result(PreflightStatus.UNKNOWN, agent, model, image, output=str(exc))

    output = (proc.stdout or "") + (proc.stderr or "")
    status = classify_probe_output(proc.returncode, output)
    return _result(status, agent, model, image, exit_code=proc.returncode, output=output)


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
