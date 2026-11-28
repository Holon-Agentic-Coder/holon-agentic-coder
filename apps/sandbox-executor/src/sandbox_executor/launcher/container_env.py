import os
import sys

from sandbox_executor import budgets, claude_credential, local_llm
from sandbox_executor.agent_runner import RETIRED_ENV_VARS, SANDBOX_UV_ENV, SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.config_env import RESOLVED_MARKER
from sandbox_executor.launcher import mounts


def _apply_workspace_pin(env: dict[str, str], pin_workspace: bool) -> None:
    if pin_workspace:
        env["HOLON_TARGET_WORKTREE"] = SANDBOX_WORKSPACE_MOUNT
        env.update(SANDBOX_UV_ENV)


def _default_local_pi_provider(env: dict[str, str], norm_agent_id: str) -> None:
    if local_llm.local_llm_requested() and norm_agent_id == "pi":
        local_provider_name = env.get(local_llm.ENV_LOCAL_PROVIDER, "").strip()
        if "HOLON_AGENT_PROVIDER" not in env and local_provider_name:
            env["HOLON_AGENT_PROVIDER"] = local_provider_name


def _merge_no_proxy_entries(existing_raw: str, exempted: list[str]) -> str:
    existing_no_proxy = [entry.strip() for entry in existing_raw.split(",") if entry.strip()]
    return ",".join(dict.fromkeys(existing_no_proxy + exempted))


def forwarded_env(role: str, agent_id: str, pin_workspace: bool) -> dict[str, str]:
    gh_token = mounts.find_github_token()
    env_to_forward = _host_env()
    env_to_forward[RESOLVED_MARKER] = "1"

    if gh_token:
        env_to_forward["GITHUB_TOKEN"] = gh_token

    claude_credential.pin_claude_credential(env_to_forward, agent_id)
    env_to_forward.update(budgets.stage_budget_handdown_env())
    _apply_workspace_pin(env_to_forward, pin_workspace)

    norm_agent_id = local_llm.normalize_agent_id(agent_id)
    # why: explicit role parameter must override any HOLON_ROLE forwarded from the host environment.
    env_to_forward["HOLON_ROLE"] = role
    env_to_forward["HOLON_AGENT_ID"] = norm_agent_id
    # why: host already ran the readiness probe, so nested container invocations must not repeat it.
    env_to_forward["HOLON_SKIP_PREFLIGHT"] = "1"
    _default_local_pi_provider(env_to_forward, norm_agent_id)
    return env_to_forward


def _host_env() -> dict[str, str]:
    env_to_forward = {}
    for key, value in os.environ.items():
        if (key.startswith("HOLON_") and key not in RETIRED_ENV_VARS) or key == "GITHUB_TOKEN":
            env_to_forward[key] = value
    return env_to_forward


def local_provider(local_config: dict) -> str:
    providers = local_config.get("providers", {})
    if len(providers) == 1:
        return next(iter(providers.keys()))
    if "local" in providers:
        return "local"
    raise local_llm.LocalLLMConfigError(
        f"Multiple host-local providers found ({', '.join(sorted(providers))}). "
        "Please set HOLON_AGENT_PROVIDER or HOLON_LOCAL_PROVIDER to select one."
    )


def exempt_local_hosts(local_config: dict, tr_envs: dict[str, str], proxy_owned: bool) -> None:
    # why: rewritten host-local endpoints point at the gateway and would otherwise be intercepted by the sidecar.
    local_hosts = local_llm.rewritten_local_hosts(local_config)
    if local_hosts and tr_envs.get("NO_PROXY"):
        exempted = list(local_hosts)
        proxy_url = tr_envs.get("HTTP_PROXY", "")
        if proxy_owned and local_llm.GATEWAY_HOST not in proxy_url:
            exempted.insert(0, local_llm.GATEWAY_HOST)
        joined = _merge_no_proxy_entries(tr_envs.get("NO_PROXY", ""), exempted)
        tr_envs["NO_PROXY"] = joined
        tr_envs["no_proxy"] = joined
        print(
            f"Note: host-local endpoint(s) {', '.join(local_hosts)} are exempt from token reduction "
            "(no interception, caching, or wire telemetry for that traffic).",
            file=sys.stderr,
        )
