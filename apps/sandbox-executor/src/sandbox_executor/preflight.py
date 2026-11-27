from __future__ import annotations

import contextlib
import enum
import functools
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from sandbox_executor import claude_token_migration, local_llm, preflight_cooldown
from sandbox_executor.agent_defaults import AGENT_IMAGE_MAPPING, normalise_agent_id
from sandbox_executor.claude_credential import pin_claude_credential, resolve_claude_credential_with_lookup
from sandbox_executor.preflight_model_error import is_model_error
from sandbox_executor.preflight_secret_args import reference_secret_args

logger = logging.getLogger(__name__)

PROBE_PROMPT = "Reply with the single word OK."
_EXPECTED_REPLY_RE = re.compile(r"^\s*OK[.!]?\s*$", re.MULTILINE | re.IGNORECASE)
DEFAULT_TIMEOUT_SECONDS = 60.0
TIMEOUT_ENV_VAR = "HOLON_PREFLIGHT_TIMEOUT"
CLEANUP_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_DELAY_SECONDS = 2.0
SKIP_ENV_VAR = "HOLON_SKIP_PREFLIGHT"
_EXCERPT_CHARS = 800
_TRUTHY_ENV_VALUES = ("1", "true", "yes", "on")
_OSS_MODE_TRUTHY_VALUES = ("true", "1")
_MIN_REDACTED_SECRET_LENGTH = 4
_REDACTION_PLACEHOLDER = "***REDACTED***"
_DOCKER_RUN_ENV_FILE_INSERT_INDEX = 2
_PROBE_CONTAINER_PREFIX = "holon-preflight-"
_PROBE_CONTAINER_ID_LENGTH = 12
_SENSITIVE_ENV_VARS = (
    "HOLON_AGENT_KEY",
    "HOLON_AGENT_SETTINGS",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "HOLON_AGENT_OAUTH_TOKEN",
)
_ANTHROPIC_TOKEN_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]+")


class PreflightStatus(enum.StrEnum):
    OK = "ok"
    QUOTA_EXHAUSTED = "quota_exhausted"
    NOT_LOGGED_IN = "not_logged_in"
    EXPIRED_KEY = "expired_key"
    UNKNOWN = "unknown"


_DECISIVE_TIMEOUT_STATUSES = (
    PreflightStatus.QUOTA_EXHAUSTED,
    PreflightStatus.NOT_LOGGED_IN,
    PreflightStatus.EXPIRED_KEY,
)


@dataclass(frozen=True)
class PreflightResult:
    status: PreflightStatus
    agent: str
    model: str
    image: str
    exit_code: int | None
    output_excerpt: str
    hint: str
    skipped: bool = False
    short_circuited: bool = False

    @property
    def ok(self) -> bool:
        return self.status is PreflightStatus.OK

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    def format_report(self) -> str:
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


_PATTERNS: tuple[tuple[PreflightStatus, re.Pattern[str]], ...] = (
    (
        PreflightStatus.QUOTA_EXHAUSTED,
        re.compile(
            r"(?:HTTP|status|code|error)[\s:=]*429\b|\b429\b\W*(?:too\s+many|quota|rate|resource)|RESOURCE_EXHAUSTED"
            r"|quota\s+(?:exceeded|exhausted)|exceeded\s+(?:your|the)\s+(?:current\s+)?quota"
            r"|rate[\s_-]?limit(?:ed)?|too\s+many\s+requests|usage\s+limit"
            r"|spend(?:ing)?\s+limit|credit\s+balance\s+is\s+too\s+low|insufficient_quota|billing\s+hard\s+limit",
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
            r"(?:key|token|credential|session|login)s?\s+(?:has\s+|have\s+|is\s+)?expired"
            r"|invalid[\s_-]+(?:api[\s_-]+)?(?:key|token|x-api-key|credentials)"
            r"|(?:HTTP|status|code|error)[\s:=]*401\b|\b401\s+Unauthori[sz]ed|UNAUTHENTICATED"
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
        "for this runner (--model or HOLON_MODEL; run 'holon models' to inspect supported models) and that the image "
        "exists (make build). Use --skip-preflight to bypass this check."
    ),
}


def classify_probe_output(exit_code: int | None, output: str) -> PreflightStatus:
    text = output or ""
    if exit_code == 0 and _EXPECTED_REPLY_RE.search(text):
        return PreflightStatus.OK
    for status, pattern in _PATTERNS:
        if pattern.search(text):
            return status
    if exit_code == 0 and text.strip():
        return PreflightStatus.OK
    return PreflightStatus.UNKNOWN


def matching_statuses(text: str) -> set[PreflightStatus]:
    return {status for status, pattern in _PATTERNS if pattern.search(text)}


def preflight_skipped_by_env(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get(SKIP_ENV_VAR, "")).strip().lower() in _TRUTHY_ENV_VALUES


def resolve_timeout(explicit: float | None = None, environ: Mapping[str, str] | None = None) -> float:
    if explicit is not None and explicit > 0:
        return float(explicit)
    env = os.environ if environ is None else environ
    try:
        value = float(env.get(TIMEOUT_ENV_VAR, ""))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


def redact_output(text: str) -> str:
    from sandbox_executor.redaction import redact_agent_secrets

    redacted = text or ""
    for var in _SENSITIVE_ENV_VARS:
        value = os.getenv(var)
        if value and len(value) >= _MIN_REDACTED_SECRET_LENGTH:
            redacted = redacted.replace(value, _REDACTION_PLACEHOLDER)
    redacted = _ANTHROPIC_TOKEN_RE.sub(_REDACTION_PLACEHOLDER, redacted)
    return redact_agent_secrets(redacted)


def _import_legacy_claude_token(agent_id: str) -> None:
    if agent_id == "claude":
        claude_token_migration.migrate_legacy_token_file()


def _missing_credential_output(agent: str, agent_id: str, session_mounts: list[str]) -> str | None:
    if session_mounts:
        return None
    if agent_id == "claude":
        resolved, lookup = resolve_claude_credential_with_lookup()
        return None if resolved else claude_token_migration.claude_missing_credential_message(lookup)
    if os.getenv("HOLON_AGENT_KEY"):
        return None
    if agent_id == "codex" and os.getenv("HOLON_AGENT_OSS_MODE", "").lower() in _OSS_MODE_TRUTHY_VALUES:
        return None
    return f"No HOLON_AGENT_KEY is set and no session credentials for '{agent}' were found to mount."


def _new_probe_container_name() -> str:
    return f"{_PROBE_CONTAINER_PREFIX}{uuid.uuid4().hex[:_PROBE_CONTAINER_ID_LENGTH]}"


def _collect_forwarded_holon_env(agent_id: str) -> dict[str, str]:
    from sandbox_executor.agent_runner import RETIRED_ENV_VARS
    from sandbox_executor.config_env import RESOLVED_MARKER

    forwarded: dict[str, str] = {
        key: value
        for key, value in os.environ.items()
        if key.startswith("HOLON_") and key != "HOLON_ROLE" and key not in RETIRED_ENV_VARS
    }
    pin_claude_credential(forwarded, agent_id)
    forwarded["HOLON_AGENT_ID"] = agent_id
    forwarded[RESOLVED_MARKER] = "1"
    forwarded[SKIP_ENV_VAR] = "1"
    return forwarded


def _append_docker_env_args(cmd: list[str], env: Mapping[str, str]) -> dict[str, str]:
    from sandbox_executor.launcher import env_files

    secret_env: dict[str, str] = {}
    for key, value in sorted(env.items()):
        if env_files.is_passthrough_env_item(key, value):
            secret_env[key] = value
            cmd.extend(["-e", key])
        elif env_files.is_env_file_item(key, value):
            secret_env[key] = value
        else:
            cmd.extend(["-e", f"{key}={value}"])
    return secret_env


def _split_secret_env(secret_env: Mapping[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    from sandbox_executor.launcher import env_files

    passthrough_env = {key: value for key, value in secret_env.items() if env_files.is_passthrough_env_item(key, value)}
    file_env = {key: value for key, value in secret_env.items() if key not in passthrough_env}
    return passthrough_env, file_env


def build_probe_docker_cmd(
    agent: str, model: str, image: str, container_name: str, session_mounts: list[str]
) -> tuple[list[str], dict[str, str]]:
    from sandbox_executor.agent_runner import runners
    from sandbox_executor.launcher import mounts, proxy_paths

    agent_id = normalise_agent_id(agent)
    cmd = ["docker", "run", "--rm", "-i", "--name", container_name]
    env = _collect_forwarded_holon_env(agent_id)
    secret_env = _append_docker_env_args(cmd, env)
    cmd.extend(proxy_paths._gateway_host_args())
    cmd.extend(session_mounts)
    cmd.extend(mounts.build_network_isolation_args())
    cmd.append(image)
    cmd.extend(reference_secret_args(runners[agent_id].compose_cmd(model, PROBE_PROMPT), secret_env))
    return cmd, secret_env


def _result(  # noqa: PLR0913
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


def _early_probe_result(agent: str, agent_id: str, model: str, image: str, has_runner: bool) -> PreflightResult | None:
    from sandbox_executor.agent_runner import runners

    if agent_id not in runners or agent_id not in AGENT_IMAGE_MAPPING:
        return _result(PreflightStatus.UNKNOWN, agent, model, image, hint=f"Unsupported agent runner '{agent}'.")
    if agent_id == "pi" and local_llm.local_llm_requested():
        hint = "Probe skipped: host-local model mode has no provider quota or login to check."
        return _result(PreflightStatus.OK, agent, model, image, hint=hint, skipped=True)
    if cached := preflight_cooldown.short_circuit_result(agent, model, image):
        return cached
    if not has_runner and not shutil.which("docker"):
        hint = "'docker' CLI command not found. Please install Docker."
        return _result(PreflightStatus.UNKNOWN, agent, model, image, hint=hint)
    return None


def run_preflight_probe(
    agent: str,
    model: str,
    timeout: float | None = None,
    runner_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> PreflightResult:
    run = runner_fn or subprocess.run
    agent_id = normalise_agent_id(agent)
    image = AGENT_IMAGE_MAPPING.get(agent_id, f"holon/agent-{agent_id}")
    limit = resolve_timeout(timeout)

    if early := _early_probe_result(agent, agent_id, model, image, runner_fn is not None):
        return early

    from sandbox_executor.launcher import env_files, mounts

    try:
        session_mounts = mounts.get_agent_session_mounts(agent)
    except SystemExit:
        return _result(
            PreflightStatus.NOT_LOGGED_IN,
            agent,
            model,
            image,
            output="The runner's session directory is missing (see the message above).",
        )
    _import_legacy_claude_token(agent_id)
    missing_credential = _missing_credential_output(agent, agent_id, session_mounts)
    if missing_credential is not None:
        return _result(
            PreflightStatus.NOT_LOGGED_IN,
            agent,
            model,
            image,
            output=missing_credential,
            hint=claude_token_migration.claude_not_logged_in_hint() if agent_id == "claude" else None,
        )

    container_name = _new_probe_container_name()
    docker_cmd, secret_env = build_probe_docker_cmd(agent, model, image, container_name, session_mounts)

    passthrough_env, file_env = _split_secret_env(secret_env)
    if passthrough_env:
        run = functools.partial(run, env={**os.environ, **passthrough_env})
    env_file: str | None = None
    if file_env:
        try:
            env_files.sweep_stale_env_files(env_files.env_file_dir())
            env_file = env_files.write_env_file(file_env)
        except (ValueError, OSError) as exc:
            return _result(PreflightStatus.UNKNOWN, agent, model, image, output=str(exc))
        docker_cmd[_DOCKER_RUN_ENV_FILE_INSERT_INDEX:_DOCKER_RUN_ENV_FILE_INSERT_INDEX] = ["--env-file", env_file]
    target = _ProbeTarget(agent=agent, model=model, image=image, limit=limit)
    try:
        result = _run_probe_with_retries(run, docker_cmd, target, max_attempts)
        preflight_cooldown.sync_probe_result(result)
        return result
    finally:
        if env_file:
            with contextlib.suppress(FileNotFoundError):
                os.remove(env_file)


@dataclass(frozen=True)
class _ProbeTarget:
    agent: str
    model: str
    image: str
    limit: float


def _is_retryable_unknown(result: PreflightResult) -> bool:
    return result.status is PreflightStatus.UNKNOWN and not is_model_error(result.output_excerpt)


def _with_container_name(docker_cmd: list[str], container_name: str) -> list[str]:
    updated = list(docker_cmd)
    name_index = updated.index("--name") + 1
    updated[name_index] = container_name
    return updated


def _run_probe_with_retries(
    run: Callable[..., subprocess.CompletedProcess[str]],
    docker_cmd: list[str],
    target: _ProbeTarget,
    max_attempts: int,
) -> PreflightResult:
    attempts = max(1, max_attempts)
    for attempt in range(1, attempts):
        container_name = _new_probe_container_name()
        attempt_cmd = _with_container_name(docker_cmd, container_name)
        result = _run_probe(run, attempt_cmd, container_name, target)
        if not _is_retryable_unknown(result):
            return result
        logger.warning(
            "Pre-flight probe for %s (%s) returned transient UNKNOWN on attempt %d/%d; retrying in %gs.",
            target.agent,
            target.model,
            attempt,
            attempts,
            DEFAULT_RETRY_DELAY_SECONDS,
        )
        if DEFAULT_RETRY_DELAY_SECONDS > 0:
            time.sleep(DEFAULT_RETRY_DELAY_SECONDS)
    container_name = _new_probe_container_name()
    return _run_probe(run, _with_container_name(docker_cmd, container_name), container_name, target)


def _remove_timed_out_container(
    run: Callable[..., subprocess.CompletedProcess[str]],
    container_name: str,
) -> None:
    try:
        run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            text=True,
            timeout=CLEANUP_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as cleanup_exc:
        logger.warning("Could not remove pre-flight container %s: %s", container_name, cleanup_exc)


def _handle_probe_timeout(
    run: Callable[..., subprocess.CompletedProcess[str]],
    container_name: str,
    target: _ProbeTarget,
    exc: subprocess.TimeoutExpired,
) -> PreflightResult:
    _remove_timed_out_container(run, container_name)

    partial = _combine_streams(exc.stdout, exc.stderr)
    if _EXPECTED_REPLY_RE.search(partial):
        logger.warning(
            "Pre-flight probe for %s (%s) received the expected OK reply, but container exit timed out after %gs "
            "(slow exit).",
            target.agent,
            target.model,
            target.limit,
        )
        return _result(
            PreflightStatus.OK,
            target.agent,
            target.model,
            target.image,
            exit_code=None,
            output=partial,
            hint=(f"Expected OK reply was received, but container exit timed out after {target.limit:g}s (slow exit)."),
        )

    classified = classify_probe_output(None, partial)
    if classified in _DECISIVE_TIMEOUT_STATUSES:
        return _result(classified, target.agent, target.model, target.image, exit_code=None, output=partial)

    if partial.strip():
        return _result(
            PreflightStatus.UNKNOWN,
            target.agent,
            target.model,
            target.image,
            exit_code=None,
            output=partial,
            hint=(
                f"The probe timed out after {target.limit:g}s after capturing output. "
                f"Inspect the output below or raise {TIMEOUT_ENV_VAR}."
            ),
        )

    return _result(
        PreflightStatus.UNKNOWN,
        target.agent,
        target.model,
        target.image,
        exit_code=None,
        output=partial,
        hint=(
            f"The probe did not finish within {target.limit:g}s; the runner may be waiting on an interactive login. "
            f"Initialise the session interactively or raise {TIMEOUT_ENV_VAR}."
        ),
    )


def _run_probe(
    run: Callable[..., subprocess.CompletedProcess[str]],
    docker_cmd: list[str],
    container_name: str,
    target: _ProbeTarget,
) -> PreflightResult:
    try:
        proc = run(
            docker_cmd, capture_output=True, text=True, timeout=target.limit, check=False, stdin=subprocess.DEVNULL
        )
    except subprocess.TimeoutExpired as exc:
        return _handle_probe_timeout(run, container_name, target, exc)
    except OSError as exc:
        return _result(PreflightStatus.UNKNOWN, target.agent, target.model, target.image, output=str(exc))

    output = _combine_streams(proc.stdout, proc.stderr)
    status = classify_probe_output(proc.returncode, output)
    return _result(status, target.agent, target.model, target.image, exit_code=proc.returncode, output=output)


def _combine_streams(stdout: Any, stderr: Any) -> str:
    out = _decode(stdout)
    err = _decode(stderr)
    if out and err and not out.endswith("\n"):
        return f"{out}\n{err}"
    return out + err


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
