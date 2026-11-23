"""Attaching a role container to a proxy the user already runs (the ``HOLON_TOKEN_REDUCE`` opt-in path)."""

import logging
import os

from sandbox_executor import token_reduction
from sandbox_executor.launcher import messages, proxy_paths, proxy_readiness
from sandbox_executor.launcher.proxy_paths import PROXY_ATTACH_TIMEOUT_SECONDS
from sandbox_executor.redaction import trusted_url_authority

logger = logging.getLogger(__name__)


def _attach_external_proxy() -> tuple[list[str], dict[str, str]]:
    """Attach the sandbox to an already-running proxy (``HOLON_PROXY_URL`` or host gateway).

    Used for the ``HOLON_TOKEN_REDUCE`` opt-in path: the proxy is owned by the user, so this run
    neither starts nor tears it down. An unreachable proxy degrades to direct egress.
    """
    proxy_url = os.getenv("HOLON_PROXY_URL") or proxy_paths._proxy_gateway_url()
    host_port = proxy_paths._proxy_host_port(proxy_url)
    if host_port is None:
        # Never echo the value: an unparseable URL has no reliable userinfo boundary to mask.
        logger.warning(
            "holon-coherence proxy probe failed: HOLON_PROXY_URL is not a valid proxy URL; ignoring it. "
            "Proceeding with DIRECT egress fallback."
        )
        logger.error(
            "HOLON_TOKEN_REDUCE is enabled but HOLON_PROXY_URL is not a valid proxy URL; ignoring it. "
            "This run continues with DIRECT egress (no token reduction)."
        )
        return [], {}

    # Probe before generating: an unreachable proxy must not leave a fresh CA behind on a host that
    # is about to run with direct egress.
    ready = False
    probe_host = "127.0.0.1" if host_port[0] in ("host.docker.internal", "172.17.0.1", "localhost") else host_port[0]
    # Messages show the target only when its host was validated after the userinfo was removed;
    # otherwise the parsed host and port may be part of a credential, so only the variable is named.
    trusted = trusted_url_authority(proxy_url) is not None
    probe_target = f"{probe_host}:{host_port[1]}" if trusted else "the HOLON_PROXY_URL target"
    proxy_target = f"{host_port[0]}:{host_port[1]}" if trusted else "the HOLON_PROXY_URL target"
    try:
        ready = proxy_readiness.wait_for_proxy(probe_host, host_port[1], timeout=PROXY_ATTACH_TIMEOUT_SECONDS)
    except (OSError, TimeoutError) as exc:
        logger.warning(
            "holon-coherence proxy probe failed at %s. Proceeding with DIRECT egress fallback.",
            probe_target,
        )
        logger.error(
            "HOLON_TOKEN_REDUCE is enabled but proxy probe failed at %s (%s: %s). "
            "This run continues with DIRECT egress (no token reduction).",
            probe_target,
            type(exc).__name__,
            messages.redact_cli_error(exc),
        )
        return [], {}

    if not ready:
        logger.warning(
            "holon-coherence proxy probe failed at %s. Proceeding with DIRECT egress fallback.",
            proxy_target,
        )
        logger.error(
            "HOLON_TOKEN_REDUCE is enabled but no proxy answered an HTTP proxy request at %s. Start the proxy or "
            "point HOLON_PROXY_URL at it; this run continues with DIRECT egress (no token reduction).",
            proxy_target,
        )
        return [], {}

    ca_cert_path, _ = token_reduction.generate_root_ca()

    # No gateway --add-host here: run_docker_container adds it once for every agent container, and emitting it from
    # both places produced a duplicate flag whenever an externally attached proxy was in use.
    return proxy_paths._ca_mount_args(ca_cert_path), proxy_paths._build_proxy_envs(ca_cert_path, proxy_url)
