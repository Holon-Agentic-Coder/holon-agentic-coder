#!/usr/bin/env python3

from sandbox_executor import config_env as config_env
from sandbox_executor import git_flow as git_flow
from sandbox_executor import local_llm as local_llm
from sandbox_executor.agent_defaults import (
    AGENT_IMAGE_MAPPING as AGENT_IMAGE_MAPPING,
)
from sandbox_executor.agent_defaults import (
    FALLBACK_AGENT as FALLBACK_AGENT,
)
from sandbox_executor.agent_defaults import (
    MissingAgentRunnerError as MissingAgentRunnerError,
)
from sandbox_executor.agent_defaults import (
    MissingModelError as MissingModelError,
)
from sandbox_executor.agent_defaults import (
    Resolution as Resolution,
)
from sandbox_executor.agent_defaults import (
    UnmappedAgentRunnerError as UnmappedAgentRunnerError,
)
from sandbox_executor.agent_defaults import (
    get_available_models_for_runner as get_available_models_for_runner,
)
from sandbox_executor.agent_defaults import (
    image_for_agent as image_for_agent,
)
from sandbox_executor.agent_defaults import (
    normalise_agent_id,
)
from sandbox_executor.agent_defaults import (
    resolve_agent_runner as resolve_agent_runner,
)
from sandbox_executor.agent_defaults import (
    resolve_model as resolve_model,
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
from sandbox_executor.cli.doctor import _configuration_section as _configuration_section
from sandbox_executor.cli.doctor import run_doctor
from sandbox_executor.cli.doctor_env import _doctor_section_failed as _doctor_section_failed
from sandbox_executor.cli.doctor_env import _report_budgets as _report_budgets
from sandbox_executor.cli.doctor_env import _report_gateways as _report_gateways
from sandbox_executor.cli.doctor_env import report_budgets_and_envelope
from sandbox_executor.cli.logging import (
    PACKAGE_LOGGER_NAME,
    _CurrentStderrHandler,
    configure_cli_logging,
)
from sandbox_executor.cli.main import (
    build_parser,
    main,
    resolve_layered_configuration,
)
from sandbox_executor.cli.options import (
    _AGENT_HELP,
    _MODEL_HELP,
    _SKIP_PREFLIGHT_HELP,
    _TOKEN_REDUCE_HELP,
    add_preflight_option,
    add_runner_options,
    add_target_worktree_option,
    add_token_reduction_options,
)
from sandbox_executor.cli.preflight import _AGENT_FLOW_STAGES as _AGENT_FLOW_STAGES
from sandbox_executor.cli.preflight import flow_dispatches_agent as flow_dispatches_agent
from sandbox_executor.cli.preflight import preflight_gate
from sandbox_executor.cli.preflight import resolve_runner_and_model as resolve_runner_and_model
from sandbox_executor.entrypoint.executor import OVER_BUDGET_EXIT_CODE
from sandbox_executor.flow import (
    SKIP_PUSH_DISPATCH_ERROR,
    FlowContext,
    format_decomposition_summary,
    load_checkpoint,
    run_flow_pipeline,
)
from sandbox_executor.flow import FlowStage as FlowStage
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

# why: names moved to sandbox_executor.launcher stay importable here for existing patch targets and callers.
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
from sandbox_executor.launcher.proxy_readiness import wait_for_proxy as _wait_for_proxy
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
from sandbox_executor.naming import MissingTickerError as MissingTickerError
from sandbox_executor.naming import ticker_for as ticker_for
from sandbox_executor.preflight import PreflightResult as PreflightResult
from sandbox_executor.preflight import preflight_skipped_by_env as preflight_skipped_by_env
from sandbox_executor.preflight import resolve_timeout as resolve_timeout
from sandbox_executor.preflight import run_preflight_probe
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
    "_wait_for_proxy",
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
