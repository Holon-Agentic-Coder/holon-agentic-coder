"""The token-reduction proxy environment of Stage 4, resolved once before the review stage builds its GitHub adapter."""

from __future__ import annotations

from sandbox_executor.flow.context import FlowContext


def review_proxy_env(context: FlowContext) -> dict[str, str]:
    """The host proxy environment when ``token_reduce`` or ``proxy_attach`` is set on the context, else empty."""
    if not (context.token_reduce or getattr(context, "proxy_attach", False)):
        return {}
    try:
        from sandbox_executor.launcher import sidecar

        proxy_env = sidecar.get_host_proxy_envs()
        if proxy_env:
            context.log("Token reduction proxy active for review loop.")
        return proxy_env
    except Exception as exc:
        context.log(f"Warning: Failed to configure token reduction proxy in review loop: {exc}.")
        return {}
