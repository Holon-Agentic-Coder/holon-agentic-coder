import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TextIO

from sandbox_executor import local_llm
from sandbox_executor.launcher.messages import redact_cli_error


@dataclass(frozen=True)
class _GatewayReportContext:
    host_config: dict | None
    config: dict
    envelopes: list[local_llm.ModelEnvelope]
    env: Mapping[str, str]
    allow_list: set[str]


def _doctor_section_failed(section: str, exc: BaseException) -> str:
    # why: never print exception values; they can carry secrets.
    return f"WARNING: doctor {section} check failed unexpectedly ({type(exc).__name__}); details withheld"


def report_budgets_and_envelope(fetch: local_llm.Fetcher | None = None, out: TextIO | None = None) -> int:
    env = os.environ
    out = out or sys.stdout
    code = 0
    try:
        _report_budgets(env, out)
    # why: the doctor reports every section failure; it never crashes.
    except Exception as exc:
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
        host_config = local_llm.host_models_json()
        config, envelopes = local_llm.build_envelope_config(host_config, allow_list, env)
    except local_llm.LocalLLMConfigError as exc:
        # why: same redaction as the run path (#96); these lines are also the `--json` environment section.
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
            # why: a non-probeable endpoint is named by source only (#96) and skipped, not fatal.
            print(f"WARNING: {source} is not a parseable URL, so its server is not probed (value withheld)", file=out)
    except Exception as exc:
        print(_doctor_section_failed("base URL", exc), file=out)
        code = 1

    try:
        ctx = _GatewayReportContext(host_config, config, envelopes, env, allow_list)
        _report_gateways(ctx, fetch, out)
    except Exception as exc:
        print(_doctor_section_failed("gateway probe", exc), file=out)
        code = 1
    return code


def _report_budgets(env: Mapping[str, str], out: TextIO) -> None:
    from sandbox_executor.budgets import describe_budgets, load_budgets

    for line in describe_budgets(load_budgets(env)):
        print(line, file=out)


def _report_gateways(
    ctx: _GatewayReportContext,
    fetch: local_llm.Fetcher | None,
    out: TextIO,
) -> None:
    probed = False
    # why: one probe per distinct server; limits compare only with models that server serves.
    for base_url, providers in local_llm.host_gateways(ctx.host_config, ctx.config, ctx.env, ctx.allow_list):
        health = local_llm.probe_gateway_health(base_url, fetch=fetch, allow_list=ctx.allow_list)
        if health is None:
            continue
        probed = True
        print(f"gateway={health.url} {health.cache_line()}", file=out)
        served = [envelope for envelope in ctx.envelopes if envelope.provider in providers]
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
