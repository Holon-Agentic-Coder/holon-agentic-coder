import logging
import os

from sandbox_executor.launcher import messages, proxy_paths, proxy_readiness, root_ca
from sandbox_executor.launcher.proxy_paths import PROXY_ATTACH_TIMEOUT_SECONDS
from sandbox_executor.redaction import trusted_url_authority

logger = logging.getLogger(__name__)

_LOOPBACK_HOST = "127.0.0.1"
_LOCAL_GATEWAY_HOSTS = ("host.docker.internal", "172.17.0.1", "localhost")
_UNTRUSTED_PROXY_TARGET_LABEL = "the HOLON_PROXY_URL target"


def _probe_host_for_gateway(host: str) -> str:
    return _LOOPBACK_HOST if host in _LOCAL_GATEWAY_HOSTS else host


def _format_redacted_target(host: str, port: int, *, trusted: bool) -> str:
    return f"{host}:{port}" if trusted else _UNTRUSTED_PROXY_TARGET_LABEL


def _attach_external_proxy() -> tuple[list[str], dict[str, str]]:
    proxy_url = os.getenv("HOLON_PROXY_URL") or proxy_paths._proxy_gateway_url()
    host_port = proxy_paths._proxy_host_port(proxy_url)
    if host_port is None:
        # why: never echo the raw value because an unparseable URL has no reliable userinfo boundary to mask.
        logger.warning(
            "holon-coherence proxy probe failed: HOLON_PROXY_URL is not a valid proxy URL; ignoring it. "
            "Proceeding with DIRECT egress fallback."
        )
        logger.error(
            "The token-reduction proxy attach failed: HOLON_PROXY_URL is not a valid proxy URL; ignoring it. "
            "This run continues with DIRECT egress (no token reduction)."
        )
        return [], {}

    # why: probe before generating the CA so an unreachable proxy leaves no fresh CA on a direct-egress host.
    ready = False
    probe_host = _probe_host_for_gateway(host_port[0])
    # why: log host/port only after userinfo validation; otherwise parsed host/port segments may hold credentials.
    trusted = trusted_url_authority(proxy_url) is not None
    probe_target = _format_redacted_target(probe_host, host_port[1], trusted=trusted)
    proxy_target = _format_redacted_target(host_port[0], host_port[1], trusted=trusted)
    try:
        ready = proxy_readiness.wait_for_proxy(probe_host, host_port[1], timeout=PROXY_ATTACH_TIMEOUT_SECONDS)
    except (OSError, TimeoutError) as exc:
        logger.warning(
            "holon-coherence proxy probe failed at %s. Proceeding with DIRECT egress fallback.",
            probe_target,
        )
        logger.error(
            "The token-reduction proxy attach failed: the proxy probe failed at %s (%s: %s). "
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
            "The token-reduction proxy attach failed: no proxy answered an HTTP proxy request at %s. Start the "
            "proxy or point HOLON_PROXY_URL at it; this run continues with DIRECT egress (no token reduction).",
            proxy_target,
        )
        return [], {}

    ca_cert_path, _ = root_ca.generate_root_ca()

    return proxy_paths._ca_mount_args(ca_cert_path), proxy_paths._build_proxy_envs(ca_cert_path, proxy_url)
