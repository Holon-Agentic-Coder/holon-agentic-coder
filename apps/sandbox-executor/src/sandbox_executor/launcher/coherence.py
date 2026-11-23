"""Detection of the holon-coherence proxy and the proxy variables handed to host-side processes."""

import logging
import os
import shutil
import socket
from typing import NamedTuple

from sandbox_executor.launcher import proxy_image, proxy_paths, root_ca
from sandbox_executor.launcher.proxy_paths import PROXY_LISTEN_PORT

logger = logging.getLogger(__name__)

_TRUTHY_ENV_VALUES = ("1", "true", "yes", "on")
_EXPLICIT_OPT_OUT_VALUES = {"0", "false", "no", "off"}


def detect_holon_coherence(token_reduce: bool = False) -> bool:
    """Detect whether holon-coherence proxy is available or explicitly requested.

    Evaluation hierarchy:
    - Tier 1 (Explicit Opt-Out): If HOLON_TOKEN_REDUCE is in ("0", "false", "no", "off"),
      return False immediately.
    - Tier 2 (Explicit Opt-In): If token_reduce is True, or HOLON_TOKEN_REDUCE /
      HOLON_PASSIVE_MONITORING is truthy, return True.
    - Tier 3 (TCP Port Probe): Determine target proxy host and port from HOLON_PROXY_URL,
      or 127.0.0.1 and HOLON_PROXY_PORT (default 8080). Attempt a quick non-blocking TCP socket
      connection with a 0.2-second timeout. If successful, return True.
    - Tier 4 (Docker Image Probe): If docker CLI exists on host PATH, probe if either
      'holon-coherence:latest' or 'ghcr.io/holon-agentic-coder/holon-coherence:latest' is present
      locally via docker image inspect. If found, return True. A plain mitmproxy image does not count: the
      sidecar runs the addon the holon-coherence image ships.
    - Tier 5 (CLI Binary Probe): Check if 'holon-coherence' executable exists on PATH.
      If found, return True.
    - If all tiers evaluate negative, return False.

    The per-stage ``review-loop`` command uses this rule; ``holon flow`` uses `resolve_flow_proxy_choice`, which never
    runs tiers 4 and 5.
    """
    explicit = _explicit_coherence_choice(token_reduce)
    if explicit is not None:
        return explicit
    return _proxy_port_reachable() or _coherence_image_present() or _coherence_binary_present()


class FlowProxyChoice(NamedTuple):
    """How a ``holon flow`` run routes its role containers through a token-reduction proxy."""

    spawn_sidecar: bool
    """This run starts its own mitmproxy sidecar; only an explicit ``--token-reduce`` or ``--mitm-web`` sets it."""
    attach_running_proxy: bool
    """Role containers attach to a proxy the user already runs; this never starts anything."""


def resolve_flow_proxy_choice(token_reduce: bool = False) -> FlowProxyChoice:
    """Resolve the flow's proxy routing from the explicit opt-out, the explicit flag and the TCP port probe only.

    - ``HOLON_TOKEN_REDUCE`` in ("0", "false", "no", "off") returns (False, False) before any probe runs.
    - ``token_reduce`` (the flow's ``--token-reduce`` or ``--mitm-web``) returns (True, False), as on the per-stage
      commands.
    - Otherwise a proxy accepting connections on the probed port is attached to: (False, True).

    The Docker image and CLI binary probes never run here: a present but unused image must not change routing. An
    env-only opt-in (``HOLON_TOKEN_REDUCE=1``, ``HOLON_PASSIVE_MONITORING=1``) needs no flow-side value, because the
    launcher reads those variables itself, exactly as on the per-stage path.
    """
    if _token_reduce_opted_out():
        return FlowProxyChoice(spawn_sidecar=False, attach_running_proxy=False)
    if token_reduce:
        return FlowProxyChoice(spawn_sidecar=True, attach_running_proxy=False)
    return FlowProxyChoice(spawn_sidecar=False, attach_running_proxy=_proxy_port_reachable())


def _token_reduce_opted_out() -> bool:
    """True when ``HOLON_TOKEN_REDUCE`` is an explicit opt-out value."""
    return os.getenv("HOLON_TOKEN_REDUCE", "").strip().lower() in _EXPLICIT_OPT_OUT_VALUES


def _explicit_coherence_choice(token_reduce: bool) -> bool | None:
    """Tiers 1 and 2: False on an explicit opt-out, True on an explicit opt-in, None when neither was given."""
    env_reduce = os.getenv("HOLON_TOKEN_REDUCE", "").strip().lower()
    if env_reduce in _EXPLICIT_OPT_OUT_VALUES:
        return False

    if token_reduce:
        return True
    if env_reduce in _TRUTHY_ENV_VALUES:
        return True
    if os.getenv("HOLON_PASSIVE_MONITORING", "").strip().lower() in _TRUTHY_ENV_VALUES:
        return True
    return None


def _proxy_port_reachable() -> bool:
    """Tier 3: True when the configured (or default loopback) proxy port accepts a TCP connection."""
    proxy_url = os.getenv("HOLON_PROXY_URL")
    if proxy_url:
        host_port = proxy_paths._proxy_host_port(proxy_url)
        if host_port is None:
            # Never echo the value: an unparseable URL has no reliable userinfo boundary to mask.
            logger.warning("HOLON_PROXY_URL is not a valid proxy URL; skipping the proxy port probe.")
    else:
        try:
            port = int(os.getenv("HOLON_PROXY_PORT", str(PROXY_LISTEN_PORT)))
        except ValueError:
            port = PROXY_LISTEN_PORT
        host_port = ("127.0.0.1", port)

    if host_port is not None:
        try:
            sock = socket.create_connection(host_port, timeout=0.2)
            if hasattr(sock, "close"):
                sock.close()
            return True
        except (OSError, TimeoutError):
            pass
    return False


def _coherence_image_present() -> bool:
    """Tier 4: True when docker is on PATH and a holon-coherence proxy image is present locally."""
    return proxy_image.local_coherence_image() is not None


def _coherence_binary_present() -> bool:
    """Tier 5: True when the `holon-coherence` executable is on PATH."""
    return bool(shutil.which("holon-coherence"))


def _token_reduce_opt_in(token_reduce: bool = False) -> bool:
    """True only on explicit opt-in; respects opt-out."""
    return _explicit_coherence_choice(token_reduce) is True


def get_host_proxy_envs() -> dict[str, str]:
    """Return environment variables for host-side processes to route through an active coherence proxy.

    Only returns proxy env vars if a proxy is actively listening on localhost or HOLON_PROXY_URL,
    and the root CA certificate exists on the host. Otherwise returns empty dict (direct egress).
    """
    env_reduce = os.getenv("HOLON_TOKEN_REDUCE", "").strip().lower()
    if env_reduce in _EXPLICIT_OPT_OUT_VALUES:
        return {}

    proxy_url = os.getenv("HOLON_PROXY_URL")
    if proxy_url:
        host_port = proxy_paths._proxy_host_port(proxy_url)
    else:
        try:
            port = int(os.getenv("HOLON_PROXY_PORT", str(PROXY_LISTEN_PORT)))
        except ValueError:
            port = PROXY_LISTEN_PORT
        host_port = ("127.0.0.1", port)
        proxy_url = f"http://127.0.0.1:{port}"

    if host_port is None:
        return {}

    # Probe if proxy is actively accepting connections
    try:
        with socket.create_connection(host_port, timeout=0.2):
            pass
    except (OSError, TimeoutError):
        return {}

    # Check for host CA cert
    ca_dir = os.path.join(os.path.expanduser(os.path.join("~", ".holon")), "proxy-ca")
    ca_cert_path = os.path.join(ca_dir, "mitmproxy-ca-cert.pem")
    if not os.path.exists(ca_cert_path):
        try:
            ca_cert_path, _ = root_ca.generate_root_ca()
        except Exception:
            return {}

    envs = {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
    }
    if os.path.exists(ca_cert_path):
        envs["NODE_EXTRA_CA_CERTS"] = ca_cert_path
        envs["SSL_CERT_FILE"] = ca_cert_path
        envs["REQUESTS_CA_BUNDLE"] = ca_cert_path

    return envs
