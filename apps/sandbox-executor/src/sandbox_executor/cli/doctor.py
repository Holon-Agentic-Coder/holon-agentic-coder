"""Readiness diagnostics implementation for ``holon doctor``."""

import argparse
import sys
from typing import Any

from sandbox_executor import config_env, local_llm
from sandbox_executor.agent_defaults import (
    MissingAgentRunnerError,
    MissingModelError,
    UnmappedAgentRunnerError,
    get_available_models_for_runner,
    normalise_agent_id,
    resolve_agent_runner,
    resolve_model,
)
from sandbox_executor.cli.doctor_env import _doctor_section_failed, report_budgets_and_envelope
from sandbox_executor.launcher.messages import print_encodable, redact_cli_error
from sandbox_executor.naming import MissingTickerError, ticker_for
from sandbox_executor.preflight import resolve_timeout, run_preflight_probe


def _configuration_section(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    """``holon doctor`` configuration section: the ``--json`` payload and the text lines of the layered configuration.

    The resolution ``main`` remembered is reported (a fresh one when none is held, as when a test calls
    ``run_doctor`` directly); an explicit ``--agent`` or ``--model`` is reported as the ``cli`` layer.
    """
    from sandbox_executor import config_env_report

    config = config_env.current() or config_env.load_effective_config()
    flags = {"HOLON_AGENT": getattr(args, "agent", None), "HOLON_MODEL": getattr(args, "model", None)}
    config = config_env.with_cli_overrides(config, {key: value for key, value in flags.items() if value})
    return config_env_report.configuration_payload(config), config_env_report.format_config_report(config)


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
