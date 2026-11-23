#!/usr/bin/env python3
"""Host wrapper CLI for running Holon Docker sandbox roles (intent, plan, execute)."""

import argparse
import contextlib
import importlib.metadata
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from typing import Any, TextIO

from sandbox_executor import git_flow, local_llm
from sandbox_executor.agent_defaults import (
    AGENT_IMAGE_MAPPING as AGENT_IMAGE_MAPPING,  # re-exported: the mapping moved to agent_defaults
)
from sandbox_executor.agent_defaults import (
    FALLBACK_AGENT,
    MissingAgentRunnerError,
    MissingModelError,
    Resolution,
    UnmappedAgentRunnerError,
    get_available_models_for_runner,
    image_for_agent,
    normalise_agent_id,
    resolve_agent_runner,
    resolve_model,
)
from sandbox_executor.agent_runner import (
    HOST_WORKTREE_ROLES,
    INTENT_CREATOR_ROLE,
    RETIRED_ENV_VARS,
    SANDBOX_UV_ENV,
    SANDBOX_WORKSPACE_MOUNT,
)
from sandbox_executor.budgets import stage_budget_handdown_env
from sandbox_executor.calibration import run_calibrate
from sandbox_executor.claude_credential import CLAUDE_CREDENTIAL_ENV_NAMES, resolve_claude_credential
from sandbox_executor.entrypoint.executor import OVER_BUDGET_EXIT_CODE
from sandbox_executor.flow import (
    SKIP_PUSH_DISPATCH_ERROR,
    FlowContext,
    FlowStage,
    format_decomposition_summary,
    load_checkpoint,
    run_flow_pipeline,
)
from sandbox_executor.knowledge import (
    ActionIndexDatabase,
    ConsultEngine,
    TelemetryCollector,
    append_action_event,
    lift_veto,
    list_vetoes,
    promote_pending_candidates,
    reconcile_records,
)
from sandbox_executor.knowledge.consult import format_consult_results_table
from sandbox_executor.knowledge.stats import format_stats_dashboard
from sandbox_executor.launcher.coherence import (
    _EXPLICIT_OPT_OUT_VALUES as _EXPLICIT_OPT_OUT_VALUES,
)
from sandbox_executor.launcher.coherence import (
    _TRUTHY_ENV_VALUES as _TRUTHY_ENV_VALUES,
)
from sandbox_executor.launcher.coherence import (
    _token_reduce_opt_in as _token_reduce_opt_in,
)
from sandbox_executor.launcher.env_files import (
    _ENV_FILE_STALE_SECONDS as _ENV_FILE_STALE_SECONDS,
)
from sandbox_executor.launcher.env_files import (
    ENV_FILE_PREFIX as ENV_FILE_PREFIX,
)
from sandbox_executor.launcher.env_files import (
    env_file_dir,
    is_secret_env_item,
    sweep_stale_env_files,
    write_env_file,
)
from sandbox_executor.launcher.messages import (
    _URL_DELIMITER_SPLIT_RE as _URL_DELIMITER_SPLIT_RE,
)
from sandbox_executor.launcher.messages import (
    _URL_ENV_NAME_RE as _URL_ENV_NAME_RE,
)
from sandbox_executor.launcher.messages import (
    _url_env_credential_parts as _url_env_credential_parts,
)
from sandbox_executor.launcher.messages import print_encodable, redact_cli_error
from sandbox_executor.launcher.mounts import (
    BLOCKED_METADATA_HOSTS,
    build_network_isolation_args,
    find_github_token,
    get_agent_session_mounts,
)
from sandbox_executor.launcher.mounts import (
    SINKHOLE_IP as SINKHOLE_IP,
)
from sandbox_executor.launcher.mounts import (
    antigravity_session_mounts as antigravity_session_mounts,
)
from sandbox_executor.launcher.mounts import (
    get_pi_mounts as get_pi_mounts,
)
from sandbox_executor.launcher.proxy_attach import _attach_external_proxy
from sandbox_executor.launcher.proxy_paths import (
    CONTAINER_CA_BUNDLE_PATH as CONTAINER_CA_BUNDLE_PATH,
)
from sandbox_executor.launcher.proxy_paths import (
    CONTAINER_CA_DIR as CONTAINER_CA_DIR,
)
from sandbox_executor.launcher.proxy_paths import (
    MITM_PROXY_CA_DIR as MITM_PROXY_CA_DIR,
)
from sandbox_executor.launcher.proxy_paths import (
    NO_PROXY_HOSTS as NO_PROXY_HOSTS,
)
from sandbox_executor.launcher.proxy_paths import (
    PROXY_ATTACH_TIMEOUT_SECONDS as PROXY_ATTACH_TIMEOUT_SECONDS,
)
from sandbox_executor.launcher.proxy_paths import (
    PROXY_CONNECT_TIMEOUT_SECONDS as PROXY_CONNECT_TIMEOUT_SECONDS,
)
from sandbox_executor.launcher.proxy_paths import (
    PROXY_LISTEN_PORT as PROXY_LISTEN_PORT,
)
from sandbox_executor.launcher.proxy_paths import (
    _build_proxy_envs as _build_proxy_envs,
)
from sandbox_executor.launcher.proxy_paths import (
    _ca_mount_args as _ca_mount_args,
)
from sandbox_executor.launcher.proxy_paths import (
    _container_ca_path as _container_ca_path,
)
from sandbox_executor.launcher.proxy_paths import (
    _ensure_network as _ensure_network,
)
from sandbox_executor.launcher.proxy_paths import (
    _find_git_root,
    _gateway_host_args,
    _proxy_host_port,
)
from sandbox_executor.launcher.proxy_paths import (
    _mitm_proxy_ca_paths as _mitm_proxy_ca_paths,
)
from sandbox_executor.launcher.proxy_paths import (
    _proxy_gateway_url as _proxy_gateway_url,
)
from sandbox_executor.launcher.proxy_paths import (
    _published_loopback_port as _published_loopback_port,
)
from sandbox_executor.launcher.proxy_paths import (
    _run_docker as _run_docker,
)
from sandbox_executor.launcher.sidecar import (
    _sidecar_state,
    detect_holon_coherence,
    get_host_proxy_envs,
    get_token_reduction_mounts_and_envs,
    setup_token_reduction_proxy,
    teardown_token_reduction_proxy,
)
from sandbox_executor.launcher.sidecar import (
    _SidecarState as _SidecarState,
)
from sandbox_executor.ledger import format_query_results_table, query_ledger
from sandbox_executor.naming import MissingTickerError, ticker_for
from sandbox_executor.preflight import (
    PreflightResult,
    preflight_skipped_by_env,
    resolve_timeout,
    run_preflight_probe,
)
from sandbox_executor.redaction import (
    COMMAND_ECHO_ENV_NAMES as COMMAND_ECHO_ENV_NAMES,
)
from sandbox_executor.redaction import (
    carries_url_password as carries_url_password,
)
from sandbox_executor.redaction import (
    is_secret_env_name as is_secret_env_name,
)
from sandbox_executor.redaction import (
    redact_agent_secrets as redact_agent_secrets,
)
from sandbox_executor.redaction import (
    redact_command,
    url_userinfo_end,
)
from sandbox_executor.redaction import (
    redact_env_literals as redact_env_literals,
)
from sandbox_executor.redaction import (
    starts_with_url_authority as starts_with_url_authority,
)
from sandbox_executor.redaction import (
    trusted_url_authority as trusted_url_authority,
)
from sandbox_executor.review_ensemble import DEFAULT_ENSEMBLE_SIZE, ENSEMBLE_SIZE_HELP
from sandbox_executor.scaffold import init_project
from sandbox_executor.token_reduction import generate_root_ca as generate_root_ca
from sandbox_executor.token_reduction import proxy_readiness
from sandbox_executor.token_reduction.proxy_readiness import PROXY_READY_TIMEOUT_SECONDS as PROXY_READY_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

# Kept for import compatibility; the launcher calls `proxy_readiness.wait_for_proxy` itself.
_wait_for_proxy = proxy_readiness.wait_for_proxy

# Root of Holon's own logger hierarchy; the CLI routes its INFO records to stderr (see configure_cli_logging).
PACKAGE_LOGGER_NAME = "sandbox_executor"


class _CurrentStderrHandler(logging.StreamHandler):
    """Stream handler that always writes to the current ``sys.stderr`` rather than the one bound at creation."""

    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, _value) -> None:
        pass


def configure_cli_logging() -> None:
    """Send Holon's own INFO-and-above log records to stderr as plain messages.

    Only the ``sandbox_executor`` logger hierarchy is configured, so third-party libraries keep their own levels and
    stdout stays untouched. Idempotent: a second call adds no further handler.
    """
    package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    if any(isinstance(handler, _CurrentStderrHandler) for handler in package_logger.handlers):
        return
    handler = _CurrentStderrHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    package_logger.addHandler(handler)
    if package_logger.level == logging.NOTSET:
        package_logger.setLevel(logging.INFO)


from sandbox_executor.cli.options import (  # noqa: E402
    _AGENT_HELP,
    _MODEL_HELP,
    _SKIP_PREFLIGHT_HELP,
    _TOKEN_REDUCE_HELP,
    add_preflight_option,
    add_runner_options,
    add_target_worktree_option,
    add_token_reduction_options,
)

__all__ = [
    "AGENT_IMAGE_MAPPING",
    "BLOCKED_METADATA_HOSTS",
    "DEFAULT_ENSEMBLE_SIZE",
    "ENSEMBLE_SIZE_HELP",
    "OVER_BUDGET_EXIT_CODE",
    "PACKAGE_LOGGER_NAME",
    "SKIP_PUSH_DISPATCH_ERROR",
    "_AGENT_HELP",
    "_MODEL_HELP",
    "_SKIP_PREFLIGHT_HELP",
    "_TOKEN_REDUCE_HELP",
    "ActionIndexDatabase",
    "ConsultEngine",
    "FlowContext",
    "TelemetryCollector",
    "_CurrentStderrHandler",
    "_attach_external_proxy",
    "_find_git_root",
    "_proxy_host_port",
    "_sidecar_state",
    "add_preflight_option",
    "add_runner_options",
    "add_target_worktree_option",
    "add_token_reduction_options",
    "append_action_event",
    "build_parser",
    "configure_cli_logging",
    "detect_holon_coherence",
    "dispatch_role_agent",
    "env_file_dir",
    "find_github_token",
    "format_consult_results_table",
    "format_decomposition_summary",
    "format_query_results_table",
    "format_stats_dashboard",
    "get_agent_session_mounts",
    "get_host_proxy_envs",
    "get_token_reduction_mounts_and_envs",
    "init_project",
    "is_secret_env_item",
    "lift_veto",
    "list_vetoes",
    "load_checkpoint",
    "main",
    "normalise_agent_id",
    "preflight_gate",
    "print_encodable",
    "promote_pending_candidates",
    "query_ledger",
    "reconcile_records",
    "redact_cli_error",
    "report_budgets_and_envelope",
    "resolve_role_image",
    "run_calibrate",
    "run_docker_container",
    "run_doctor",
    "run_flow_pipeline",
    "run_preflight_probe",
    "setup_token_reduction_proxy",
    "sweep_stale_env_files",
    "teardown_token_reduction_proxy",
    "url_userinfo_end",
    "write_env_file",
]


def run_docker_container(  # noqa: C901, PLR0911, PLR0912, PLR0913, PLR0915
    role: str,
    image_name: str,
    container_args: list[str],
    agent_id: str,
    intent_file: str | None = None,
    token_reduce: bool = False,
    mitm_web: bool = False,
    target_worktree: str | None = None,
    result_file: str | None = None,
) -> int:
    """Constructs docker run command with auto-discovered credentials and executes it.

    Every role (intent creator, planner, executor and review loop) works in place on the host worktree, so its
    ``target_worktree`` is required, validated and bind-mounted at the sandbox workspace path (see
    ``git_flow.sandbox_worktree_mounts``) with ``HOLON_TARGET_WORKTREE`` pinned to the mountpoint. No container
    reaches the remote: the host fetches the base before the container starts and pushes after it returns.
    """
    if not shutil.which("docker"):
        print("Error: 'docker' CLI command not found. Please install Docker.", file=sys.stderr)
        return 1

    workspace_mounts: list[str] = []
    if role in HOST_WORKTREE_ROLES:
        try:
            workspace_mounts = git_flow.sandbox_worktree_mounts(target_worktree, SANDBOX_WORKSPACE_MOUNT, role)
        except RuntimeError as exc:
            print(f"Error: {redact_cli_error(exc)}", file=sys.stderr)
            return 1

    tty_flag = ["-it"] if sys.stdin.isatty() else ["-i"]
    docker_cmd = ["docker", "run", "--rm", *tty_flag]
    docker_cmd.extend(["-e", "DOCKER_CLI_HINTS=false"])

    # Credentials never go on the docker command line, where `ps` and /proc/<pid>/cmdline can read
    # them: they are collected here and handed over in a 0600 --env-file just before the run. That
    # includes any value with a URL password (a proxy URL in any scheme, or none), whatever its name.
    secret_env: dict[str, str] = {}

    def add_env(key: str, value: str) -> None:
        if is_secret_env_item(key, value):
            secret_env[key] = value
        else:
            docker_cmd.extend(["-e", f"{key}={value}"])

    # Forward all host environment variables prefixed with HOLON_ and GITHUB_TOKEN
    env_to_forward = {}
    gh_token = find_github_token()

    for key, value in os.environ.items():
        # A retired variable is ignored: no container needs a repository URL, as the host does every fetch and push.
        if (key.startswith("HOLON_") and key not in RETIRED_ENV_VARS) or key == "GITHUB_TOKEN":
            env_to_forward[key] = value

    # Set after the host forwarding loop so a host GITHUB_TOKEN cannot displace the chosen token.
    if gh_token:
        env_to_forward["GITHUB_TOKEN"] = gh_token

    # The claude runner never takes HOLON_AGENT_KEY: its credential is an explicit claude variable or the secrets
    # file, exported under the name its format decides. Set after the loop so a stale host value cannot displace it.
    if local_llm.normalize_agent_id(agent_id) == "claude":
        env_to_forward.pop("HOLON_AGENT_KEY", None)
        claude_credential = resolve_claude_credential()
        if claude_credential:
            for name in CLAUDE_CREDENTIAL_ENV_NAMES:
                env_to_forward.pop(name, None)
            env_to_forward[claude_credential[0]] = claude_credential[1]

    # Inside a timed host stage (holon flow), hand the container the time this stage has left so its own ceiling fires
    # first; this replaces any forwarded host HOLON_STAGE_BUDGET_SECONDS. Every role-container dispatch goes
    # through here, so no caller has to pass it.
    env_to_forward.update(stage_budget_handdown_env())

    # Pin the container workspace to the host worktree mountpoint, and keep 'uv run' off the host's mounted .venv.
    if workspace_mounts:
        env_to_forward["HOLON_TARGET_WORKTREE"] = SANDBOX_WORKSPACE_MOUNT
        env_to_forward.update(SANDBOX_UV_ENV)

    norm_agent_id = local_llm.normalize_agent_id(agent_id)

    # Ensure explicit role parameter takes strict precedence over host environment
    env_to_forward["HOLON_ROLE"] = role
    # The host already ran the readiness probe; nested invocations inside the container must not repeat it.
    env_to_forward["HOLON_SKIP_PREFLIGHT"] = "1"

    # Coherence for local mode: default HOLON_AGENT_PROVIDER from HOLON_LOCAL_PROVIDER if unset (pi runner only)
    if local_llm.local_llm_requested() and norm_agent_id == "pi":
        local_provider = env_to_forward.get(local_llm.ENV_LOCAL_PROVIDER, "").strip()
        if "HOLON_AGENT_PROVIDER" not in env_to_forward and local_provider:
            env_to_forward["HOLON_AGENT_PROVIDER"] = local_provider

    for key, value in sorted(env_to_forward.items()):
        add_env(key, value)

    # Containers reach the host only through the gateway name; make it resolvable on Linux as it already is on
    # Docker Desktop, so agent base URLs rewritten to host.docker.internal work on every platform.
    docker_cmd.extend(_gateway_host_args())

    # Token Reduction Proxy & CA Mounts. From this point on the sidecar (and its network) may exist,
    # so every remaining exit path — early returns included — must run teardown, not just the final
    # subprocess.run.
    local_agent_dir: str | None = None
    env_file: str | None = None
    tr_mounts, tr_envs = get_token_reduction_mounts_and_envs(token_reduce=token_reduce, mitm_web=mitm_web)
    try:
        docker_cmd.extend(tr_mounts)

        # Host-local inference servers: loopback and the host's own LAN address are unreachable from the
        # container namespace, so hand the agent a generated config whose authorities resolve via the gateway. The
        # artifact is a pi agent directory, so it is produced for pi only; other runners still need real credentials.
        if local_llm.local_llm_requested() and norm_agent_id != "pi" and role in ("planner", "executor"):
            print(
                f"Warning: host-local model mode applies to the pi runner only; ignoring it for '{agent_id}'.",
                file=sys.stderr,
            )
        if local_llm.local_llm_requested() and norm_agent_id == "pi":
            try:
                base_tmp = os.path.expanduser("~/.holon/tmp")
                os.makedirs(base_tmp, mode=0o755, exist_ok=True)
                local_agent_dir = tempfile.mkdtemp(prefix="holon-pi-agent-", dir=base_tmp)
                local_config = local_llm.prepare_agent_dir(
                    local_agent_dir,
                    host_config=local_llm.host_models_json(),
                    allow_list=local_llm.host_local_allow_list(),
                )
                # If provider was unset, determine whether to use the single distinct provider or "local"
                if "HOLON_AGENT_PROVIDER" not in env_to_forward:
                    providers = local_config.get("providers", {})
                    if len(providers) == 1:
                        chosen = next(iter(providers.keys()))
                    elif "local" in providers:
                        chosen = "local"
                    else:
                        raise local_llm.LocalLLMConfigError(
                            f"Multiple host-local providers found ({', '.join(sorted(providers))}). "
                            "Please set HOLON_AGENT_PROVIDER or HOLON_LOCAL_PROVIDER to select one."
                        )
                    add_env("HOLON_AGENT_PROVIDER", chosen)
            except local_llm.LocalLLMConfigError as exc:
                print_encodable(f"Error: {redact_cli_error(exc)}", file=sys.stderr)
                return 1
            docker_cmd.extend(local_llm.container_mount_args(local_agent_dir))
            for key, value in local_llm.container_env(local_agent_dir).items():
                add_env(key, value)
            # That endpoint now points at the gateway, so it would otherwise be intercepted by the sidecar.
            local_hosts = local_llm.rewritten_local_hosts(local_config)
            if local_hosts and tr_envs.get("NO_PROXY"):
                exempted = list(local_hosts)
                # Some NO_PROXY implementations compare only the hostname, so a port-qualified entry can miss. Adding
                # the bare gateway name is safe when the sidecar is addressed by container name, but not when an
                # external proxy is reached through host.docker.internal itself.
                proxy_url = tr_envs.get("HTTP_PROXY", "")
                if (
                    token_reduce or mitm_web or bool(_sidecar_state.container_name)
                ) and local_llm.GATEWAY_HOST not in proxy_url:
                    exempted.insert(0, local_llm.GATEWAY_HOST)
                existing_no_proxy = [e.strip() for e in tr_envs.get("NO_PROXY", "").split(",") if e.strip()]
                joined = ",".join(dict.fromkeys(existing_no_proxy + exempted))
                tr_envs["NO_PROXY"] = joined
                tr_envs["no_proxy"] = joined
                print(
                    f"Note: host-local endpoint(s) {', '.join(local_hosts)} are exempt from token reduction "
                    "(no interception, caching, or wire telemetry for that traffic).",
                    file=sys.stderr,
                )

        for k, v in tr_envs.items():
            add_env(k, v)

        # Intent file mount for intent-creator role
        if role == "intent-creator":
            if not intent_file:
                print("Error: Intent file is mandatory for intent creation.", file=sys.stderr)
                return 1
            abs_intent = os.path.abspath(intent_file)
            if not os.path.exists(abs_intent):
                print(f"Error: Intent file '{intent_file}' does not exist.", file=sys.stderr)
                return 1
            docker_cmd.extend(["-v", f"{abs_intent}:/tmp/intent.json"])

        # Result file mount for role containers (e.g. review-loop result transport)
        if result_file:
            abs_result = os.path.abspath(result_file)
            docker_cmd.extend(["-v", f"{abs_result}:/tmp/review_result.json"])

        # The validated host worktree, its git directories and the read-only git-config overlays.
        docker_cmd.extend(workspace_mounts)

        # Auto-detect Session Mounts
        session_mounts = get_agent_session_mounts(agent_id)
        docker_cmd.extend(session_mounts)

        # Network isolation and egress controls
        network_isolation_args = build_network_isolation_args(token_reduce=token_reduce)
        docker_cmd.extend(network_isolation_args)

        # Image and args
        docker_cmd.append(image_name)
        docker_cmd.extend(container_args)

        if secret_env:
            try:
                sweep_stale_env_files(env_file_dir())
                env_file = write_env_file(secret_env)
            except ValueError as exc:
                print(f"Error: {redact_cli_error(exc)}.", file=sys.stderr)
                return 1
            except OSError as exc:
                # An unwritable or unreadable ~/.holon/tmp/env-files: fail closed with a clear message
                # rather than a traceback, and never fall back to passing the credentials on the argv.
                print(f"Error: could not write the credential env-file: {redact_cli_error(exc)}.", file=sys.stderr)
                return 1
            # Right after `docker run`. Docker reads --env-file on the client side, so the host path
            # needs no bind mount.
            docker_cmd[2:2] = ["--env-file", env_file]

        # A forwarded HOLON_* value may hold a non-UTF-8 byte (surrogateescape); it must not crash the run here.
        print_encodable(f"Executing: {redact_command(docker_cmd)}")
        result = subprocess.run(docker_cmd, env={**os.environ, "DOCKER_CLI_HINTS": "false"})
        return result.returncode
    finally:
        if env_file:
            with contextlib.suppress(FileNotFoundError):
                os.remove(env_file)
        teardown_token_reduction_proxy()
        if local_agent_dir:
            shutil.rmtree(local_agent_dir, ignore_errors=True)
        try:
            from sandbox_executor.agent_runner import get_runner

            runner = get_runner(agent_id)
            if hasattr(runner, "cleanup_ephemeral_credentials"):
                runner.cleanup_ephemeral_credentials()
        except Exception:
            pass


def _doctor_section_failed(section: str, exc: BaseException) -> str:
    """The warning line for a doctor section that raised unexpectedly: section and exception class only, no values."""
    return f"WARNING: doctor {section} check failed unexpectedly ({type(exc).__name__}); details withheld"


def report_budgets_and_envelope(fetch: local_llm.Fetcher | None = None, out: TextIO | None = None) -> int:
    """``holon doctor`` environment section: print the effective budgets and the local-LLM model envelope, and probe
    the local gateway.

    Prints ``model=<id> context=<n> max_tokens=<n> source=host|synthesised`` and
    ``reasoning=<mode> source=env|host|default`` per model, then ``prefill_cache=off|cold|warm`` with memory figures
    once per distinct host-local server, warning when a model's emitted ``maxTokens`` or ``contextWindow`` exceeds
    the ``effective_defaults.max_output_tokens`` or ``max_prompt_tokens`` of the server that serves it. Nothing is
    written; only loopback or allow-listed endpoints are probed.

    Each part (budgets, envelope, base-URL check, gateway probe) is isolated: an invalid configuration prints an
    ``Error:`` line, and any unexpected exception becomes a value-free ``WARNING: doctor <part> check failed
    unexpectedly (<ExceptionClass>)`` line; either makes the result 1, and the doctor never raises from here.
    """
    env = os.environ
    out = out or sys.stdout
    code = 0
    try:
        _report_budgets(env, out)
    except Exception as exc:  # The doctor reports; it never crashes.
        print(_doctor_section_failed("budgets", exc), file=out)
        code = 1

    if env.get(local_llm.ENV_LOCAL_MODE, "").strip().lower() not in ("1", "true", "yes", "on"):
        print(f"local_llm=disabled (set {local_llm.ENV_LOCAL_MODE}=1 to inspect the model envelope)", file=out)
        return code

    try:
        allow_list = local_llm.host_local_allow_list()
        # One validated loader (local_llm.load_models_json): a malformed file is a LocalLLMConfigError naming it.
        host_config = local_llm.host_models_json()
        config, envelopes = local_llm.build_envelope_config(host_config, allow_list, env)
    except local_llm.LocalLLMConfigError as exc:
        # The same top-level redaction as the run path (#96); these lines are also the `--json` environment section.
        print(f"Error: {redact_cli_error(exc)}", file=out)
        return 1
    except Exception as exc:
        print(_doctor_section_failed("model envelope", exc), file=out)
        return 1

    for envelope in envelopes:
        print(envelope.envelope_line(), file=out)
        print(envelope.reasoning_line(), file=out)

    try:
        for source in local_llm.unparseable_base_url_sources(host_config, env):
            # Named by its source only (#96 message rule); like any non-probeable endpoint it is skipped, not fatal.
            print(f"WARNING: {source} is not a parseable URL, so its server is not probed (value withheld)", file=out)
    except Exception as exc:
        print(_doctor_section_failed("base URL", exc), file=out)
        code = 1

    try:
        _report_gateways(host_config, config, envelopes, env, allow_list, fetch, out)
    except Exception as exc:
        print(_doctor_section_failed("gateway probe", exc), file=out)
        code = 1
    return code


def _report_budgets(env: Mapping[str, str], out: TextIO) -> None:
    from sandbox_executor.budgets import describe_budgets, load_budgets

    for line in describe_budgets(load_budgets(env)):
        print(line, file=out)


def _report_gateways(  # noqa: PLR0913
    host_config: dict | None,
    config: dict,
    envelopes: list[local_llm.ModelEnvelope],
    env: Mapping[str, str],
    allow_list: set[str],
    fetch: local_llm.Fetcher | None,
    out: TextIO,
) -> None:
    probed = False
    # One probe and one line per distinct server; limits are compared only with the models that server serves.
    for base_url, providers in local_llm.host_gateways(host_config, config, env, allow_list):
        health = local_llm.probe_gateway_health(base_url, fetch=fetch, allow_list=allow_list)
        if health is None:
            continue
        probed = True
        print(f"gateway={health.url} {health.cache_line()}", file=out)
        served = [envelope for envelope in envelopes if envelope.provider in providers]
        if health.max_output_tokens is not None:
            for envelope in served:
                if envelope.max_tokens > health.max_output_tokens:
                    print(
                        f"WARNING: model={envelope.model_id} max_tokens={envelope.max_tokens} exceeds the server's "
                        f"effective_defaults.max_output_tokens={health.max_output_tokens}",
                        file=out,
                    )
        if health.max_prompt_tokens is not None:
            for envelope in served:
                if envelope.context_window > health.max_prompt_tokens:
                    print(
                        f"WARNING: model={envelope.model_id} context={envelope.context_window} exceeds the server's "
                        f"max_prompt_tokens={health.max_prompt_tokens}",
                        file=out,
                    )
    if not probed:
        print("prefill_cache=unknown (no loopback or allow-listed endpoint to probe)", file=out)


INTENT_CREATOR_IMAGE: str = "holon/orchestrator"
# The intent-creator entrypoint never invokes a runner, so its container keeps the historical antigravity
# mount profile whatever runner the caller resolved.
INTENT_CREATOR_AGENT_ID: str = "antigravity"


def resolve_role_image(role: str, agent_id: str) -> str:
    """Resolve the container image that hosts a role agent.

    Args:
        role: Role name ('intent-creator', 'planner' or 'executor').
        agent_id: Runner name or normalised agent id (see agent_defaults.normalise_agent_id).

    Returns:
        str: 'holon/orchestrator' for the intent creator, otherwise the mapped 'holon/agent-<runner>' image.

    Raises:
        UnmappedAgentRunnerError: If a planner or executor runner has no mapped image.
    """
    if role == INTENT_CREATOR_ROLE:
        return INTENT_CREATOR_IMAGE
    return image_for_agent(agent_id)


def dispatch_role_agent(  # noqa: PLR0913
    role: str,
    runner: str | None,
    model: str | None,
    positional_args: list[str],
    *,
    target_worktree: str | None = None,
    intent_file: str | None = None,
    result_file: str | None = None,
    token_reduce: bool = False,
    mitm_web: bool = False,
) -> int:
    """Launch the containerised role agent shared by 'holon intent|plan|execute' and 'holon flow'.

    This is the single dispatch path for the authoring roles: the per-stage commands and the flow
    stages both call it, so the two invocations launch the same image with the same arguments.

    Args:
        role: Role name ('intent-creator', 'planner' or 'executor').
        runner: Resolved runner name (e.g. 'claude-agent'); selects the 'holon/agent-<runner>' image.
        model: Resolved model name. Planner and executor receive it through positional_args; the
            intent creator does not run a model.
        positional_args: Container arguments (e.g. [plan_branch, runner, model] for the executor).
        target_worktree: Host git worktree the role container works in (required).
        intent_file: Intent payload path, mandatory for the intent creator.
        result_file: Structured outcome payload path for container-to-host result transport.
        token_reduce: Whether to route container traffic through the token reduction proxy.
        mitm_web: Whether to start the proxy with the mitmweb interface.

    Returns:
        int: Container exit code (non-zero on failure).
    """
    del model  # Carried in positional_args for the roles that run a model.
    if role == INTENT_CREATOR_ROLE:
        kwargs: dict[str, Any] = {"agent_id": INTENT_CREATOR_AGENT_ID, "intent_file": intent_file}
        if token_reduce:
            kwargs["token_reduce"] = True
    else:
        kwargs = {"agent_id": normalise_agent_id(runner or FALLBACK_AGENT), "token_reduce": token_reduce}
    if mitm_web:
        kwargs["mitm_web"] = True
    if target_worktree:
        kwargs["target_worktree"] = target_worktree
    if result_file:
        kwargs["result_file"] = result_file
    image_name = resolve_role_image(role, kwargs["agent_id"])
    return run_docker_container(role, image_name, list(positional_args), **kwargs)


def resolve_runner_and_model(agent: str | None, model: str | None) -> tuple[Resolution, Resolution]:
    """Resolve the agent runner and model for a command.

    The resolvers log each decision at INFO, which :func:`configure_cli_logging` routes to stderr.

    Args:
        agent: Raw ``--agent`` value (None when omitted).
        model: Raw ``--model`` value (None when omitted).

    Returns:
        ``(agent resolution, model resolution)``.

    Raises:
        MissingAgentRunnerError: If no agent runner was provided, configured, or detected.
        MissingModelError: If no model was provided or configured.
        UnmappedAgentRunnerError: If the selected runner has no container image.
    """
    agent_res = resolve_agent_runner(agent)
    model_res = resolve_model(agent_res.value, model)
    return agent_res, model_res


# Resumption points from which a flow still reaches a stage that needs the agent runner: plan and execute, and review,
# whose loop needs the runner for its reviewer turns (Stage 4 dispatches reviewer turns through _dispatch_role into
# the review-loop role container). A flow resumed at review must preflight the runner too; only calibration (git and
# local checks) has nothing for the probe to protect.
_AGENT_FLOW_STAGES = tuple(s.value for s in FlowStage if s not in (FlowStage.CALIBRATE, FlowStage.COMPLETED))


def flow_dispatches_agent(from_stage: str | None) -> bool:
    """Return True when a flow starting at ``from_stage`` still reaches a stage that dispatches the agent runner.

    Args:
        from_stage: Resumption stage name, or None for a run from the start.

    Returns:
        False only when the flow resumes at a stage after the last agent-dispatching stage.
    """
    if not from_stage:
        return True
    return str(from_stage).strip().lower() in _AGENT_FLOW_STAGES


def preflight_gate(agent: str, model: str, skip: bool = False) -> PreflightResult | None:
    """Run the readiness probe unless skipped and print the diagnosis when it fails.

    Must be called before any branch, checkpoint or ledger write.

    Args:
        agent: Resolved runner name.
        model: Resolved model name.
        skip: True when ``--skip-preflight`` was given.

    Returns:
        The probe result, or None when the probe was skipped by flag or ``HOLON_SKIP_PREFLIGHT``.
    """
    if skip or preflight_skipped_by_env():
        logger.info("Pre-flight probe skipped (--skip-preflight or HOLON_SKIP_PREFLIGHT).")
        return None
    result = run_preflight_probe(agent, model, timeout=resolve_timeout())
    if not result.ok:
        print(result.format_report(), file=sys.stderr)
        print(
            "Aborting before any branch, checkpoint or ledger write. Use --skip-preflight to bypass this check.",
            file=sys.stderr,
        )
    return result


def run_doctor(args: argparse.Namespace, fetch: local_llm.Fetcher | None = None) -> int:
    """Implement ``holon doctor``: one readiness report for the runner, the model and the local environment.

    It resolves the agent runner and model and runs the pre-flight probe on them, then reports the effective turn and
    stage budgets and, when local-LLM mode is enabled, the model envelope and the local gateway probe
    (:func:`report_budgets_and_envelope`). With ``--json`` the environment section is carried under ``environment``
    as its report lines, with its ``WARNING:`` lines also listed under ``warnings``.

    Args:
        args: Parsed namespace with ``agent``, ``model``, ``json``, ``timeout`` and ``target_worktree``.
        fetch: Optional HTTP fetcher for the gateway probe (tests).

    Returns:
        0 when the probe reports ``ok`` and the environment section found no configuration error, otherwise 1.

    Side effects:
        One ``docker run`` with a minimal prompt and, in local-LLM mode, a health request to loopback or allow-listed
        gateways. Writes only to stdout/stderr; never to the repository or ledgers.
    """
    import io
    import json

    target_worktree = getattr(args, "target_worktree", ".")
    del target_worktree  # Validated and available for future worktree-scoped environment probes.

    env_out = io.StringIO()
    try:
        env_code = report_budgets_and_envelope(fetch=fetch, out=env_out)
    except Exception as exc:  # Last resort; each part already isolates its own failures.
        print(_doctor_section_failed("environment", exc), file=env_out)
        env_code = 1
    env_lines = env_out.getvalue().splitlines()
    warnings = [line for line in env_lines if line.startswith("WARNING: ")]
    environment = {"ok": env_code == 0, "lines": env_lines, "warnings": warnings}

    try:
        # The text report prints both decisions on stdout, so the resolvers' INFO log would show them twice.
        agent_res = resolve_agent_runner(args.agent, log=False)
        ticker_for(agent_res.value)  # A runner without a ticker may not name a branch; reject it before the probe.
        model_res = resolve_model(agent_res.value, args.model, log=False, validate=True)
    except (UnmappedAgentRunnerError, MissingAgentRunnerError, MissingModelError, MissingTickerError) as exc:
        if args.json:
            error_payload: dict[str, Any] = {"error": redact_cli_error(exc)}
            agent_id = getattr(args, "agent", None) or (agent_res.value if "agent_res" in locals() else None)
            if agent_id:
                norm_a = normalise_agent_id(agent_id)
                avail = get_available_models_for_runner(norm_a)
                if avail:
                    error_payload["available_models"] = avail
                    error_payload["hint"] = (
                        f"Run 'holon models --agent {norm_a}' to inspect supported models and effort levels."
                    )
            error_payload["environment"] = environment
            print(json.dumps(error_payload, indent=2))
        else:
            print_encodable(f"Error: {redact_cli_error(exc)}", file=sys.stderr)
            print_encodable("\n".join(env_lines))
        return 1

    result = run_preflight_probe(agent_res.value, model_res.value, timeout=resolve_timeout(args.timeout))
    if args.json:
        payload = {
            "agent": {"value": agent_res.value, "source": agent_res.source, "reason": agent_res.reason},
            "model": {"value": model_res.value, "source": model_res.source, "reason": model_res.reason},
            "preflight": result.to_dict(),
            "environment": environment,
        }
        print(json.dumps(payload, indent=2))
    else:
        # Encoding-safe: no configuration text can crash the report on a strict terminal.
        print_encodable(agent_res.describe("Agent runner"))
        print_encodable(model_res.describe("Model"))
        print_encodable(result.format_report())
        print_encodable("\n".join(env_lines))
    return 0 if result.ok and env_code == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser and register all discovered subcommands."""
    from sandbox_executor.cli.commands import discover

    try:
        pkg_version = importlib.metadata.version("holon")
    except importlib.metadata.PackageNotFoundError:
        pkg_version = "0.1.0"
    parser = argparse.ArgumentParser(
        prog="holon",
        description="Holon CLI: Host wrapper for running containerized Holon AI agent roles.",
    )
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {pkg_version}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for cmd in discover():
        cmd.register(subparsers)
    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the Holon CLI."""
    configure_cli_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, "_run"):
        ret = args._run(args)
        if ret is not None:
            sys.exit(ret)
        return
    sys.exit(0)
