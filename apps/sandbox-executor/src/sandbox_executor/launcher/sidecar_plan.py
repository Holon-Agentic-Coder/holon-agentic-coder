"""What one mitmproxy sidecar run needs: its addon, host directories, dashboard port and `docker run` argv."""

import logging
import os
import socket
from dataclasses import dataclass

from sandbox_executor.launcher import proxy_paths
from sandbox_executor.launcher.coherence import _TRUTHY_ENV_VALUES
from sandbox_executor.launcher.proxy_paths import MITM_PROXY_CA_DIR, PROXY_CONNECT_TIMEOUT_SECONDS, PROXY_LISTEN_PORT

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _SidecarPlan:
    """Everything the sidecar `docker run` needs, resolved before any Docker resource exists."""

    container_name: str
    network_name: str
    addon_path: str
    proxy_cache_dir: str
    mitm_ca_combined: str
    mitm_ca_cert: str
    host_wire_log_dir: str
    host_cache_dir: str
    web_port: int
    mitm_web: bool


def _package_dir() -> str:
    """The ``sandbox_executor`` package directory (this module sits one level below it)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mitm_addon_path() -> str:
    """The mitmproxy addon script the sidecar loads; raises FileNotFoundError when it is missing."""
    addon_path = os.path.join(_package_dir(), "token_reduction", "mitm_addon.py")
    if not os.path.isfile(addon_path):
        raise FileNotFoundError(
            f"mitmproxy addon script not found at '{addon_path}'. Token reduction cannot start its proxy "
            "without it; re-run without --token-reduce to execute with direct egress."
        )
    return addon_path


def _proxy_host_dirs(ca_cert_path: str, ca_key_path: str) -> tuple[str, str, str, str, str]:
    """Create the host directories and CA files the sidecar mounts.

    Returns:
        ``(proxy cache dir, mitmproxy combined CA, mitmproxy CA certificate, wire-log dir, cache dir)``.
    """
    # Share ONLY a narrow proxy cache dir, read-only. Never mount ~/.holon: that subtree holds the
    # Root CA private key (~/.holon/certs) and the agent auth session stores (~/.holon/sessions).
    proxy_cache_dir = os.path.join(os.path.expanduser(os.path.join("~", ".holon")), "proxy-cache")
    os.makedirs(proxy_cache_dir, exist_ok=True)

    # mitmproxy needs the CA private key to sign leaves (see _mitm_proxy_ca_paths); hand it exactly
    # the two files it expects, read-only, instead of the whole certificate directory.
    mitm_ca_combined, mitm_ca_cert = proxy_paths._mitm_proxy_ca_paths(ca_cert_path, ca_key_path)

    repo_root = proxy_paths._find_git_root()
    wire_dir_env = os.getenv("WIRE_LOG_DIR")
    if wire_dir_env:
        host_wire_log_dir = (
            wire_dir_env if os.path.isabs(wire_dir_env) else os.path.abspath(os.path.join(repo_root, wire_dir_env))
        )
    else:
        host_wire_log_dir = os.path.abspath(os.path.join(repo_root, "todo", "mitm_wire_logs"))
    os.makedirs(host_wire_log_dir, exist_ok=True)

    cache_dir_env = os.getenv("CACHE_DIR")
    if cache_dir_env:
        host_cache_dir = (
            cache_dir_env if os.path.isabs(cache_dir_env) else os.path.abspath(os.path.join(repo_root, cache_dir_env))
        )
    else:
        host_cache_dir = os.path.abspath(os.path.join(repo_root, "todo", "cache"))
    os.makedirs(host_cache_dir, exist_ok=True)

    # Ensure non-root container UID 1000 can write without permission errors on Linux hosts.
    # Note: On shared multi-user hosts or environments where host UID/GID != 1000, setting
    # HOLON_MITM_USER="$(id -u):$(id -g)" is strongly recommended, allowing the host
    # directories to use restrictive 0o700 or 0o750 permissions rather than 0o775.
    for d in (host_wire_log_dir, host_cache_dir):
        try:
            os.chmod(d, 0o775)
        except OSError as e:
            logger.debug("Could not chmod 0o775 on %s: %s", d, e)

    return proxy_cache_dir, mitm_ca_combined, mitm_ca_cert, host_wire_log_dir, host_cache_dir


def _dashboard_port(mitm_web: bool) -> int:
    """The mitmweb dashboard port; with ``mitm_web`` set, raises RuntimeError when it is already in use."""
    try:
        web_port = int(os.getenv("HOLON_MITM_WEB_PORT", "8081"))
    except ValueError:
        logger.warning("Invalid HOLON_MITM_WEB_PORT; falling back to 8081.")
        web_port = 8081

    if mitm_web:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(PROXY_CONNECT_TIMEOUT_SECONDS)
            if s.connect_ex(("127.0.0.1", web_port)) == 0:
                raise RuntimeError(
                    f"Port {web_port} is already in use on 127.0.0.1. "
                    "Specify a different dashboard port using HOLON_MITM_WEB_PORT=<port>."
                )
    return web_port


def _sidecar_command(plan: _SidecarPlan) -> list[str]:
    """The `docker run` argv that starts the mitmproxy sidecar described by ``plan``."""
    src_dir = os.path.abspath(os.path.join(_package_dir(), ".."))

    docker_run_proxy = [
        "docker",
        "run",
        "-d",
        "--name",
        plan.container_name,
        "--network",
        plan.network_name,
        "--memory=256m",
        "--cpus=0.5",
        "--log-opt",
        "max-size=5m",
        "--log-opt",
        "max-file=2",
        "--restart=no",
        # Loopback-only publish so the host can run a real TCP readiness probe.
        "-p",
        f"127.0.0.1::{PROXY_LISTEN_PORT}",
    ]
    mitm_user = os.getenv("HOLON_MITM_USER")
    if mitm_user:
        docker_run_proxy.extend(["--user", mitm_user])
    if plan.mitm_web:
        docker_run_proxy.extend(["-p", f"127.0.0.1:{plan.web_port}:{plan.web_port}"])

    passive_val = "1" if os.getenv("HOLON_PASSIVE_MONITORING", "").strip().lower() in _TRUTHY_ENV_VALUES else "0"
    docker_run_proxy.extend(
        [
            "-e",
            "PYTHONPATH=/tmp/src",
            "-e",
            "WIRE_LOG_DIR=/tmp/wire_logs",
            "-e",
            "CACHE_DIR=/tmp/cache",
            "-e",
            f"HOLON_PASSIVE_MONITORING={passive_val}",
            "-v",
            f"{src_dir}:/tmp/src:ro",
            "-v",
            f"{plan.proxy_cache_dir}:/home/mitmproxy/.holon/proxy-cache:ro",
            "-v",
            f"{plan.host_cache_dir}:/tmp/cache",
            "-v",
            f"{plan.mitm_ca_combined}:{MITM_PROXY_CA_DIR}/mitmproxy-ca.pem:ro",
            "-v",
            f"{plan.mitm_ca_cert}:{MITM_PROXY_CA_DIR}/mitmproxy-ca-cert.pem:ro",
            "-v",
            f"{plan.addon_path}:/tmp/mitm_addon.py:ro",
            "-v",
            f"{plan.host_wire_log_dir}:/tmp/wire_logs",
            "mitmproxy/mitmproxy:12.2.3",
            "mitmweb" if plan.mitm_web else "mitmdump",
            "-s",
            "/tmp/mitm_addon.py",
            "--listen-port",
            str(PROXY_LISTEN_PORT),
            "--set",
            "stream_large_bodies=1m",
        ]
    )
    if plan.mitm_web:
        docker_run_proxy.extend(["--web-host", "0.0.0.0", "--web-port", str(plan.web_port)])
        web_password = os.getenv("HOLON_MITM_WEB_PASSWORD")
        if web_password:
            docker_run_proxy.extend(["--set", f"web_password={web_password}"])
    return docker_run_proxy
