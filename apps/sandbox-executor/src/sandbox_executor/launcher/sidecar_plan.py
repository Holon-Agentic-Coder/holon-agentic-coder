import logging
import os
import socket
from dataclasses import dataclass

from sandbox_executor.launcher import proxy_paths
from sandbox_executor.launcher.coherence import _TRUTHY_ENV_VALUES
from sandbox_executor.launcher.proxy_paths import MITM_PROXY_CA_DIR, PROXY_CONNECT_TIMEOUT_SECONDS, PROXY_LISTEN_PORT

logger = logging.getLogger(__name__)

# why: dashboard password env var is forwarded value-less (-e NAME) so the secret never appears in docker run argv
COHERENCE_WEB_PASSWORD_ENV = "HOLON_COHERENCE_WEB_PASSWORD"
_DEFAULT_MITM_WEB_PORT = 8081
_SIDECAR_WRITABLE_DIR_MODE = 0o775
_LOOPBACK_HOST = "127.0.0.1"


@dataclass(frozen=True)
class _SidecarPlan:
    container_name: str
    network_name: str
    image: str
    proxy_cache_dir: str
    mitm_ca_combined: str
    mitm_ca_cert: str
    host_wire_log_dir: str
    host_cache_dir: str
    web_port: int
    mitm_web: bool


def _resolve_host_dir(env_var: str, default_dir: str) -> str:
    env_dir = os.getenv(env_var)
    if not env_dir:
        return default_dir
    return env_dir if os.path.isabs(env_dir) else os.path.abspath(env_dir)


def _ensure_writable_host_dirs(directories: tuple[str, ...]) -> None:
    for directory in directories:
        try:
            # why: 0o775 lets non-root container UID 1000 write on Linux hosts without permission errors
            os.chmod(directory, _SIDECAR_WRITABLE_DIR_MODE)
        except OSError as exc:
            logger.debug("Could not chmod 0o775 on %s: %s", directory, exc)


def _proxy_host_dirs(ca_cert_path: str, ca_key_path: str) -> tuple[str, str, str, str, str]:
    # why: mount only narrow proxy-cache read-only, never ~/.holon (holds Root CA private key and auth sessions)
    proxy_cache_dir = os.path.join(proxy_paths._user_state_dir(), "proxy-cache")
    os.makedirs(proxy_cache_dir, exist_ok=True)

    mitm_ca_combined, mitm_ca_cert = proxy_paths._mitm_proxy_ca_paths(ca_cert_path, ca_key_path)

    host_wire_log_dir = _resolve_host_dir("WIRE_LOG_DIR", proxy_paths._default_wire_log_dir())
    os.makedirs(host_wire_log_dir, exist_ok=True)

    host_cache_dir = _resolve_host_dir("CACHE_DIR", proxy_paths._default_cache_dir())
    os.makedirs(host_cache_dir, exist_ok=True)

    _ensure_writable_host_dirs((host_wire_log_dir, host_cache_dir))

    return proxy_cache_dir, mitm_ca_combined, mitm_ca_cert, host_wire_log_dir, host_cache_dir


def _dashboard_port(mitm_web: bool) -> int:
    try:
        web_port = int(os.getenv("HOLON_MITM_WEB_PORT", str(_DEFAULT_MITM_WEB_PORT)))
    except ValueError:
        logger.warning("Invalid HOLON_MITM_WEB_PORT; falling back to %s.", _DEFAULT_MITM_WEB_PORT)
        web_port = _DEFAULT_MITM_WEB_PORT

    if mitm_web:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(PROXY_CONNECT_TIMEOUT_SECONDS)
            if s.connect_ex((_LOOPBACK_HOST, web_port)) == 0:
                raise RuntimeError(
                    f"Port {web_port} is already in use on 127.0.0.1. "
                    "Specify a different dashboard port using HOLON_MITM_WEB_PORT=<port>."
                )
    return web_port


def _dashboard_password(plan: _SidecarPlan) -> str | None:
    if not plan.mitm_web:
        return None
    return os.getenv("HOLON_MITM_WEB_PASSWORD") or None


def _sidecar_spawn_env(plan: _SidecarPlan) -> dict[str, str] | None:
    password = _dashboard_password(plan)
    if password is None:
        return None
    return {**os.environ, COHERENCE_WEB_PASSWORD_ENV: password}


def _base_docker_run_args(plan: _SidecarPlan) -> list[str]:
    args = [
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
        "-p",
        f"{_LOOPBACK_HOST}::{PROXY_LISTEN_PORT}",
    ]
    mitm_user = os.getenv("HOLON_MITM_USER")
    if mitm_user:
        args.extend(["--user", mitm_user])
    if plan.mitm_web:
        args.extend(["-p", f"{_LOOPBACK_HOST}:{plan.web_port}:{plan.web_port}"])
    return args


def _sidecar_env_args(plan: _SidecarPlan) -> list[str]:
    passive_val = "1" if os.getenv("HOLON_PASSIVE_MONITORING", "").strip().lower() in _TRUTHY_ENV_VALUES else "0"
    args = [
        "-e",
        "HOLON_IN_CONTAINER=1",
        "-e",
        "WIRE_LOG_DIR=/tmp/wire_logs",
        "-e",
        "CACHE_DIR=/tmp/cache",
        "-e",
        f"HOLON_PASSIVE_MONITORING={passive_val}",
    ]
    if _dashboard_password(plan) is not None:
        args.extend(["-e", COHERENCE_WEB_PASSWORD_ENV])
    return args


def _sidecar_mount_args(plan: _SidecarPlan) -> list[str]:
    return [
        "-v",
        f"{plan.proxy_cache_dir}:/home/mitmproxy/.holon/proxy-cache:ro",
        "-v",
        f"{plan.host_cache_dir}:/tmp/cache",
        "-v",
        f"{plan.mitm_ca_combined}:{MITM_PROXY_CA_DIR}/mitmproxy-ca.pem:ro",
        "-v",
        f"{plan.mitm_ca_cert}:{MITM_PROXY_CA_DIR}/mitmproxy-ca-cert.pem:ro",
        "-v",
        f"{plan.host_wire_log_dir}:/tmp/wire_logs",
    ]


def _coherence_start_args(plan: _SidecarPlan) -> list[str]:
    args = ["start", "--port", str(PROXY_LISTEN_PORT)]
    if plan.mitm_web:
        args.extend(["--web", "--web-port", str(plan.web_port)])
    return args


def _sidecar_command(plan: _SidecarPlan) -> list[str]:
    return [
        *_base_docker_run_args(plan),
        *_sidecar_env_args(plan),
        *_sidecar_mount_args(plan),
        plan.image,
        *_coherence_start_args(plan),
    ]
