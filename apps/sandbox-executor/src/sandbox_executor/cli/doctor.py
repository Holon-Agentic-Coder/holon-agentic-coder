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
    from sandbox_executor import config_env_report

    config = config_env.current() or config_env.load_effective_config()
    flags = {"HOLON_AGENT": getattr(args, "agent", None), "HOLON_MODEL": getattr(args, "model", None)}
    config = config_env.with_cli_overrides(config, {key: value for key, value in flags.items() if value})
    rows = config_env_report.setting_rows(config)
    return (
        config_env_report.configuration_payload(config, rows=rows),
        config_env_report.format_config_report(config, rows=rows),
    )


def run_doctor(args: argparse.Namespace, fetch: local_llm.Fetcher | None = None) -> int:
    import io
    import json

    target_worktree = getattr(args, "target_worktree", ".")
    # why: accepted for a future worktree-scoped probe; unused today.
    del target_worktree

    env_out = io.StringIO()
    try:
        env_code = report_budgets_and_envelope(fetch=fetch, out=env_out)
    # why: last resort; each section already isolates its own failures, and the doctor must still print a report.
    except Exception as exc:
        print(_doctor_section_failed("environment", exc), file=env_out)
        env_code = 1
    env_lines = env_out.getvalue().splitlines()
    warnings = [line for line in env_lines if line.startswith("WARNING: ")]
    environment = {"ok": env_code == 0, "lines": env_lines, "warnings": warnings}
    configuration, config_lines = _configuration_section(args)

    try:
        # why: the text report prints both decisions, so resolver INFO logs would duplicate them.
        agent_res = resolve_agent_runner(args.agent, log=False)
        # why: a runner without a ticker cannot name a branch; reject it before the probe.
        ticker_for(agent_res.value)
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
        # why: a strict terminal encoding must not crash the report on configuration text.
        print_encodable(agent_res.describe("Agent runner"))
        print_encodable(model_res.describe("Model"))
        print_encodable(result.format_report())
        print_encodable("\n".join([*env_lines, *config_lines]))
    return 0 if result.ok and env_code == 0 else 1
