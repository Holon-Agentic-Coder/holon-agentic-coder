#!/usr/bin/env python3
"""Host wrapper CLI for running Holon Docker sandbox roles (intent, plan, execute)."""

import argparse
import importlib.metadata
import logging
import os
import sys
from collections.abc import Mapping
from typing import Any, TextIO

from sandbox_executor import config_env, local_llm
from sandbox_executor import git_flow as git_flow
from sandbox_executor.agent_defaults import (
    AGENT_IMAGE_MAPPING as AGENT_IMAGE_MAPPING,  # re-exported: the mapping moved to agent_defaults
)
from sandbox_executor.agent_defaults import (
    FALLBACK_AGENT as FALLBACK_AGENT,
)
from sandbox_executor.agent_defaults import (
    MissingAgentRunnerError,
    MissingModelError,
    Resolution,
    UnmappedAgentRunnerError,
    get_available_models_for_runner,
    normalise_agent_id,
    resolve_agent_runner,
    resolve_model,
)
from sandbox_executor.agent_defaults import (
    image_for_agent as image_for_agent,
)
from sandbox_executor.agent_runner import (
    HOST_WORKTREE_ROLES as HOST_WORKTREE_ROLES,
)
from sandbox_executor.agent_runner import (
    INTENT_CREATOR_ROLE as INTENT_CREATOR_ROLE,
)
from sandbox_executor.agent_runner import (
    RETIRED_ENV_VARS as RETIRED_ENV_VARS,
)
from sandbox_executor.agent_runner import (
    SANDBOX_UV_ENV as SANDBOX_UV_ENV,
)
from sandbox_executor.agent_runner import (
    SANDBOX_WORKSPACE_MOUNT as SANDBOX_WORKSPACE_MOUNT,
)
from sandbox_executor.budgets import stage_budget_handdown_env as stage_budget_handdown_env
from sandbox_executor.calibration import run_calibrate
from sandbox_executor.claude_credential import CLAUDE_CREDENTIAL_ENV_NAMES as CLAUDE_CREDENTIAL_ENV_NAMES
from sandbox_executor.claude_credential import resolve_claude_credential as resolve_claude_credential
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
from sandbox_executor.launcher import proxy_readiness

# The container plumbing lives in sandbox_executor.launcher; every name it took from this module stays importable here.
# `name as name` marks a re-export this module no longer uses itself.
from sandbox_executor.launcher.coherence import (
    _EXPLICIT_OPT_OUT_VALUES as _EXPLICIT_OPT_OUT_VALUES,
)
from sandbox_executor.launcher.coherence import (
    _TRUTHY_ENV_VALUES as _TRUTHY_ENV_VALUES,
)
from sandbox_executor.launcher.coherence import (
    _token_reduce_opt_in as _token_reduce_opt_in,
)
from sandbox_executor.launcher.containers import ContainerLaunch as ContainerLaunch
from sandbox_executor.launcher.containers import run_docker_container
from sandbox_executor.launcher.env_files import (
    _ENV_FILE_STALE_SECONDS as _ENV_FILE_STALE_SECONDS,
)
from sandbox_executor.launcher.env_files import (
    ENV_FILE_PREFIX as ENV_FILE_PREFIX,
)
from sandbox_executor.launcher.env_files import (
    env_file_dir,
    is_env_file_item,
    is_passthrough_env_item,
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
    build_network_isolation_args as build_network_isolation_args,
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
    _proxy_host_port,
)
from sandbox_executor.launcher.proxy_paths import (
    _gateway_host_args as _gateway_host_args,
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
from sandbox_executor.launcher.proxy_readiness import PROXY_READY_TIMEOUT_SECONDS as PROXY_READY_TIMEOUT_SECONDS
from sandbox_executor.launcher.role_dispatch import (
    INTENT_CREATOR_AGENT_ID as INTENT_CREATOR_AGENT_ID,
)
from sandbox_executor.launcher.role_dispatch import (
    INTENT_CREATOR_IMAGE as INTENT_CREATOR_IMAGE,
)
from sandbox_executor.launcher.role_dispatch import (
    RoleLaunchOptions as RoleLaunchOptions,
)
from sandbox_executor.launcher.role_dispatch import dispatch_role_agent, resolve_role_image
from sandbox_executor.launcher.root_ca import generate_root_ca as generate_root_ca
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
    redact_command as redact_command,
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
from sandbox_executor.redaction import (
    url_userinfo_end,
)
from sandbox_executor.review_ensemble import DEFAULT_ENSEMBLE_SIZE, ENSEMBLE_SIZE_HELP
from sandbox_executor.scaffold import init_project

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
    "is_env_file_item",
    "is_passthrough_env_item",
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
    "resolve_layered_configuration",
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

    Each part (budgets, GitHub token, envelope, base-URL check, gateway probe) is isolated: an invalid configuration
    prints an ``Error:`` line, and any unexpected exception becomes a value-free ``WARNING: doctor <part> check
    failed unexpectedly (<ExceptionClass>)`` line; either makes the result 1, and the doctor never raises from here.
    """
    env = os.environ
    out = out or sys.stdout
    code = 0
    try:
        _report_budgets(env, out)
    except Exception as exc:  # The doctor reports; it never crashes.
        print(_doctor_section_failed("budgets", exc), file=out)
        code = 1
    try:
        from sandbox_executor.flow.github.token_scopes import doctor_lines

        print("\n".join(doctor_lines()), file=out)
    except Exception as exc:
        print(_doctor_section_failed("github token", exc), file=out)
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


def _configuration_section(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    """``holon doctor`` configuration section: the ``--json`` payload and the text lines of the layered configuration.

    The resolution ``main`` remembered is reported (a fresh one when none is held, as when a test calls
    ``run_doctor`` directly); an explicit ``--agent`` or ``--model`` is reported as the ``cli`` layer.
    """
    from sandbox_executor import config_env_report

    config = config_env.current() or config_env.load_effective_config()
    flags = {"HOLON_AGENT": getattr(args, "agent", None), "HOLON_MODEL": getattr(args, "model", None)}
    config = config_env.with_cli_overrides(config, {key: value for key, value in flags.items() if value})
    rows = config_env_report.setting_rows(config)
    return config_env_report.configuration_payload(config, rows=rows), config_env_report.format_config_report(
        config, rows=rows
    )


def run_doctor(args: argparse.Namespace, fetch: local_llm.Fetcher | None = None) -> int:
    """Implement ``holon doctor``: one readiness report for the runner, the model and the local environment.

    It resolves the agent runner and model and runs the pre-flight probe on them, then reports the effective turn and
    stage budgets and, when local-LLM mode is enabled, the model envelope and the local gateway probe
    (:func:`report_budgets_and_envelope`), then every effective ``HOLON_*`` setting with its source layer and the layers
    it shadowed (secrets shown only as ``set (<layer>)``). With ``--json`` the environment section is carried under
    ``environment`` as its report lines, with its ``WARNING:`` lines also listed under ``warnings``, and the settings
    under ``configuration``.

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
    configuration, config_lines = _configuration_section(args)

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
            error_payload["configuration"] = configuration
            print(json.dumps(error_payload, indent=2))
        else:
            print_encodable(f"Error: {redact_cli_error(exc)}", file=sys.stderr)
            print_encodable("\n".join([*env_lines, *config_lines]))
        return 1

    result = run_preflight_probe(agent_res.value, model_res.value, timeout=resolve_timeout(args.timeout))
    if args.json:
        payload = {
            "agent": {"value": agent_res.value, "source": agent_res.source, "reason": agent_res.reason},
            "model": {"value": model_res.value, "source": model_res.source, "reason": model_res.reason},
            "preflight": result.to_dict(),
            "environment": environment,
            "configuration": configuration,
        }
        print(json.dumps(payload, indent=2))
    else:
        # Encoding-safe: no configuration text can crash the report on a strict terminal.
        print_encodable(agent_res.describe("Agent runner"))
        print_encodable(model_res.describe("Model"))
        print_encodable(result.format_report())
        print_encodable("\n".join([*env_lines, *config_lines]))
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


def resolve_layered_configuration() -> config_env.EffectiveConfig:
    """Resolve the layered configuration once for this invocation and fold it into the host environment.

    Each warning goes to stderr (redacted as defence in depth; it names keys, paths and modes only). The file-sourced
    values fill only the variables the process environment leaves unset, so the process environment and explicit CLI
    flags keep winning, and every role container and the pre-flight probe inherit them through the 0600 env-file.
    The resolution is remembered with its pre-apply provenance for ``holon doctor``.
    """
    config = config_env.load_effective_config()
    for warning in config.warnings:
        print_encodable(redact_cli_error(warning), file=sys.stderr)
    config_env.apply_to_environ(config, os.environ)
    config_env.remember(config)
    return config


def main(argv: list[str] | None = None) -> None:
    """Run the Holon CLI."""
    configure_cli_logging()
    # Before the parser is built: argparse defaults may read the environment.
    resolve_layered_configuration()
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, "_run"):
        ret = args._run(args)
        if ret is not None:
            sys.exit(ret)
        return
    sys.exit(0)
