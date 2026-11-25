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
_LOOPBACK_HOST = "127.0.0.1"
_PORT_PROBE_TIMEOUT_SECONDS = 0.2
_PROXY_CA_SUBDIR = "proxy-ca"
_MITMPROXY_CA_CERT_FILENAME = "mitmproxy-ca-cert.pem"


def detect_holon_coherence(token_reduce: bool = False) -> bool:
    explicit = _explicit_coherence_choice(token_reduce)
    if explicit is not None:
        return explicit
    return _proxy_port_reachable() or _coherence_image_present() or _coherence_binary_present()


class FlowProxyChoice(NamedTuple):
    spawn_sidecar: bool
    attach_running_proxy: bool


def resolve_flow_proxy_choice(token_reduce: bool = False) -> FlowProxyChoice:
    if _token_reduce_opted_out():
        return FlowProxyChoice(spawn_sidecar=False, attach_running_proxy=False)
    if token_reduce:
        return FlowProxyChoice(spawn_sidecar=True, attach_running_proxy=False)
    if _explicit_coherence_choice(False) is True:
        return FlowProxyChoice(spawn_sidecar=False, attach_running_proxy=True)
    return FlowProxyChoice(spawn_sidecar=False, attach_running_proxy=_proxy_port_reachable())


def _token_reduce_opted_out() -> bool:
    return os.getenv("HOLON_TOKEN_REDUCE", "").strip().lower() in _EXPLICIT_OPT_OUT_VALUES


def _explicit_coherence_choice(token_reduce: bool) -> bool | None:
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


def _configured_proxy_port() -> int:
    try:
        return int(os.getenv("HOLON_PROXY_PORT", str(PROXY_LISTEN_PORT)))
    except ValueError:
        return PROXY_LISTEN_PORT


def _proxy_port_reachable() -> bool:
    proxy_url = os.getenv("HOLON_PROXY_URL")
    if proxy_url:
        host_port = proxy_paths._proxy_host_port(proxy_url)
        if host_port is None:
            logger.warning("HOLON_PROXY_URL is not a valid proxy URL; skipping the proxy port probe.")
    else:
        host_port = (_LOOPBACK_HOST, _configured_proxy_port())

    if host_port is not None:
        try:
            sock = socket.create_connection(host_port, timeout=_PORT_PROBE_TIMEOUT_SECONDS)
            if hasattr(sock, "close"):
                sock.close()
            return True
        except (OSError, TimeoutError):
            pass
    return False


def _coherence_image_present() -> bool:
    return proxy_image.local_coherence_image() is not None


def _coherence_binary_present() -> bool:
    return bool(shutil.which("holon-coherence"))


def _token_reduce_opt_in(token_reduce: bool = False) -> bool:
    return _explicit_coherence_choice(token_reduce) is True


def _ensure_host_ca_cert_path() -> str | None:
    ca_dir = os.path.join(os.path.expanduser(os.path.join("~", ".holon")), _PROXY_CA_SUBDIR)
    ca_cert_path = os.path.join(ca_dir, _MITMPROXY_CA_CERT_FILENAME)
    if os.path.exists(ca_cert_path):
        return ca_cert_path
    try:
        generated_cert_path, _ = root_ca.generate_root_ca()
    except Exception:
        return None
    return generated_cert_path


def get_host_proxy_envs() -> dict[str, str]:
    if _token_reduce_opted_out():
        return {}

    proxy_url = os.getenv("HOLON_PROXY_URL")
    if proxy_url:
        host_port = proxy_paths._proxy_host_port(proxy_url)
    else:
        port = _configured_proxy_port()
        host_port = (_LOOPBACK_HOST, port)
        proxy_url = f"http://{_LOOPBACK_HOST}:{port}"

    if host_port is None:
        return {}

    try:
        with socket.create_connection(host_port, timeout=_PORT_PROBE_TIMEOUT_SECONDS):
            pass
    except (OSError, TimeoutError):
        return {}

    ca_cert_path = _ensure_host_ca_cert_path()
    if ca_cert_path is None:
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
