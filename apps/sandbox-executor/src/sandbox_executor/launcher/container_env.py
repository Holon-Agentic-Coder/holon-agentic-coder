"""The environment a role container receives, assembled in an order where each later assignment deliberately wins.

The host ``HOLON_*`` and ``GITHUB_TOKEN`` values are forwarded first (they include the layered configuration that
``cli.main`` folded into the host environment, see ``config_env``); the inheritance marker, the chosen GitHub token,
the claude credential, the stage budget, the workspace pin, the role and the preflight skip flag are set after them,
so a stale host value can never displace one of them.
"""

import os
import sys

from sandbox_executor import budgets, claude_credential, local_llm
from sandbox_executor.agent_runner import RETIRED_ENV_VARS, SANDBOX_UV_ENV, SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.config_env import RESOLVED_MARKER
from sandbox_executor.launcher import mounts


def forwarded_env(role: str, agent_id: str, pin_workspace: bool) -> dict[str, str]:
    """Return every variable the role container receives, before it is split into argv and env-file values.

    Args:
        role: The role the container runs; it overrides any host ``HOLON_ROLE``.
        agent_id: The runner or agent id the container hosts.
        pin_workspace: True when a host worktree is mounted at the sandbox workspace path.

    Returns:
        The variables by name.
    """
    # Forward all host environment variables prefixed with HOLON_ and GITHUB_TOKEN
    gh_token = mounts.find_github_token()
    env_to_forward = _host_env()
    # The configuration is resolved once per invocation: a nested holon in the container reads no layer file.
    env_to_forward[RESOLVED_MARKER] = "1"

    # Set after the host forwarding loop so a host GITHUB_TOKEN cannot displace the chosen token.
    if gh_token:
        env_to_forward["GITHUB_TOKEN"] = gh_token

    # Set after the host forwarding loop so a stale host value cannot displace the resolved credential.
    claude_credential.pin_claude_credential(env_to_forward, agent_id)

    # Inside a timed host stage (holon flow), hand the container the time this stage has left so its own ceiling fires
    # first; this replaces any forwarded host HOLON_STAGE_BUDGET_SECONDS. Every role-container dispatch goes
    # through here, so no caller has to pass it.
    env_to_forward.update(budgets.stage_budget_handdown_env())

    # Pin the container workspace to the host worktree mountpoint, and keep 'uv run' off the host's mounted .venv.
    if pin_workspace:
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
    return env_to_forward


def _host_env() -> dict[str, str]:
    """The host ``HOLON_*`` variables (retired ones excluded) and the host ``GITHUB_TOKEN``."""
    env_to_forward = {}
    for key, value in os.environ.items():
        # A retired variable is ignored: no container needs a repository URL, as the host does every fetch and push.
        if (key.startswith("HOLON_") and key not in RETIRED_ENV_VARS) or key == "GITHUB_TOKEN":
            env_to_forward[key] = value
    return env_to_forward


def local_provider(local_config: dict) -> str:
    """The provider a host-local pi run selects when ``HOLON_AGENT_PROVIDER`` is unset.

    Raises:
        local_llm.LocalLLMConfigError: If several providers exist and none of them is named ``local``.
    """
    # If provider was unset, determine whether to use the single distinct provider or "local"
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
    """Add the rewritten host-local endpoints to the proxy's ``NO_PROXY`` list in ``tr_envs``.

    Args:
        local_config: The generated pi configuration.
        tr_envs: The token-reduction env vars, updated in place.
        proxy_owned: True when this run started the sidecar (addressed by container name, not the gateway).
    """
    # That endpoint now points at the gateway, so it would otherwise be intercepted by the sidecar.
    local_hosts = local_llm.rewritten_local_hosts(local_config)
    if local_hosts and tr_envs.get("NO_PROXY"):
        exempted = list(local_hosts)
        # Some NO_PROXY implementations compare only the hostname, so a port-qualified entry can miss. Adding
        # the bare gateway name is safe when the sidecar is addressed by container name, but not when an
        # external proxy is reached through host.docker.internal itself.
        proxy_url = tr_envs.get("HTTP_PROXY", "")
        if proxy_owned and local_llm.GATEWAY_HOST not in proxy_url:
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
