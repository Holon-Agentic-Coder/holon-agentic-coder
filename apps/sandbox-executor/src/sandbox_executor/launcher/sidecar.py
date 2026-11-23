"""The per-run mitmproxy token reduction sidecar: setup, teardown and the token-reduction mounts and env vars.

``detect_holon_coherence`` and ``get_host_proxy_envs`` live in ``launcher.coherence`` and are re-exported here, so
``sandbox_executor.launcher.sidecar`` is the one import path for the proxy surface the flow engine uses.
"""

import logging
import os
import socket
import subprocess
import uuid
from dataclasses import dataclass

from sandbox_executor import token_reduction
from sandbox_executor.launcher import coherence, messages, proxy_attach, proxy_paths
from sandbox_executor.launcher.coherence import _TRUTHY_ENV_VALUES
from sandbox_executor.launcher.coherence import detect_holon_coherence as detect_holon_coherence
from sandbox_executor.launcher.coherence import get_host_proxy_envs as get_host_proxy_envs
from sandbox_executor.launcher.proxy_paths import MITM_PROXY_CA_DIR, PROXY_CONNECT_TIMEOUT_SECONDS, PROXY_LISTEN_PORT
from sandbox_executor.redaction import COMMAND_ECHO_ENV_NAMES, redact_agent_secrets, redact_env_literals
from sandbox_executor.token_reduction import proxy_readiness
from sandbox_executor.token_reduction.proxy_readiness import PROXY_READY_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


@dataclass
class _SidecarState:
    """Tracks the proxy resources created by THIS run so teardown never touches foreign ones."""

    container_name: str | None = None
    network_name: str | None = None
    network_created: bool = False


_sidecar_state = _SidecarState()


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


def setup_token_reduction_proxy(mitm_web: bool = False) -> tuple[list[str], dict[str, str]]:
    """Start this run's mitmproxy sidecar and return the sandbox mounts and env vars.

    Resources are named per run (pid + uuid suffix) and recorded in ``_sidecar_state`` so teardown
    only ever removes what this run created.

    Raises:
        FileNotFoundError: If the mitmproxy addon script is missing.
        RuntimeError: If Docker networking, the sidecar spawn, or the readiness probe fails.
    """
    container_name, network_name = _sidecar_names()
    addon_path = _mitm_addon_path()

    ca_cert_path, ca_key_path = token_reduction.generate_root_ca()

    proxy_cache_dir, mitm_ca_combined, mitm_ca_cert, host_wire_log_dir, host_cache_dir = _proxy_host_dirs(
        ca_cert_path, ca_key_path
    )
    web_port = _dashboard_port(mitm_web)
    plan = _SidecarPlan(
        container_name=container_name,
        network_name=network_name,
        addon_path=addon_path,
        proxy_cache_dir=proxy_cache_dir,
        mitm_ca_combined=mitm_ca_combined,
        mitm_ca_cert=mitm_ca_cert,
        host_wire_log_dir=host_wire_log_dir,
        host_cache_dir=host_cache_dir,
        web_port=web_port,
        mitm_web=mitm_web,
    )
    _spawn_sidecar(plan)
    _await_sidecar_ready(container_name)

    if mitm_web:
        logger.info("🌐 mitmweb dashboard active at http://127.0.0.1:%s", web_port)

    return _sidecar_mounts_and_envs(container_name, network_name, ca_cert_path)


def _sidecar_names() -> tuple[str, str]:
    """The per-run ``(container name, network name)``, suffixed with the pid and a short uuid."""
    run_suffix = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    container_name = f"holon-proxy-{run_suffix}"
    network_name = f"holon-net-{run_suffix}"
    return container_name, network_name


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


def _spawn_sidecar(plan: _SidecarPlan) -> None:
    """Record this run's resources, create its network and start the sidecar; tears down and raises on failure."""
    _sidecar_state.network_name = plan.network_name
    _sidecar_state.network_created = proxy_paths._ensure_network(plan.network_name)
    _sidecar_state.container_name = plan.container_name

    docker_run_proxy = _sidecar_command(plan)

    logger.info(
        "Starting mitmproxy sidecar '%s'; the first run has to pull the 'mitmproxy/mitmproxy:12.2.3' "
        "image, which can take a while before the container appears.",
        plan.container_name,
    )
    proxy_spawn = subprocess.run(docker_run_proxy, capture_output=True, text=True, check=False)
    if proxy_spawn.returncode != 0:
        # The sidecar's output can echo the mitmweb `?token=` login URL or the `web_password=` setting.
        stderr = redact_env_literals(
            redact_agent_secrets((proxy_spawn.stderr or "").strip() or (proxy_spawn.stdout or "").strip()),
            names=COMMAND_ECHO_ENV_NAMES,
        )
        teardown_token_reduction_proxy()
        raise RuntimeError(
            f"mitmproxy sidecar '{plan.container_name}' failed to start: {stderr or 'unknown docker error'}. "
            "Re-run without --token-reduce to execute with direct egress."
        )


def _await_sidecar_ready(container_name: str) -> None:
    """Wait until the sidecar answers an HTTP proxy request on its published port; tears down and raises if not."""
    host_port = proxy_paths._published_loopback_port(container_name)
    if host_port is None:
        teardown_token_reduction_proxy()
        raise RuntimeError(
            f"mitmproxy sidecar '{container_name}' published no host loopback port, so its readiness cannot be "
            "verified. Re-run without --token-reduce to execute with direct egress."
        )
    if not proxy_readiness.wait_for_proxy("127.0.0.1", host_port):
        teardown_token_reduction_proxy()
        raise RuntimeError(
            f"mitmproxy sidecar '{container_name}' never answered an HTTP proxy request on "
            f"127.0.0.1:{host_port} within {PROXY_READY_TIMEOUT_SECONDS}s (the addon likely crashed on startup). "
            "Re-run without --token-reduce to execute with direct egress."
        )


def _sidecar_mounts_and_envs(
    container_name: str, network_name: str, ca_cert_path: str
) -> tuple[list[str], dict[str, str]]:
    """The agent container's network and CA mounts, and the env vars that route it through the sidecar."""
    mounts = ["--network", network_name, *proxy_paths._ca_mount_args(ca_cert_path)]
    return mounts, proxy_paths._build_proxy_envs(ca_cert_path, f"http://{container_name}:{PROXY_LISTEN_PORT}")


def teardown_token_reduction_proxy() -> None:
    """Remove only the sidecar container and network THIS run created (no-op otherwise)."""
    if _sidecar_state.container_name:
        result = proxy_paths._run_docker("rm", "-f", _sidecar_state.container_name)
        if result.returncode != 0:
            logger.debug(
                "docker rm -f %s exited %s: %s",
                _sidecar_state.container_name,
                result.returncode,
                (result.stderr or "").strip(),
            )
        _sidecar_state.container_name = None

    if _sidecar_state.network_created and _sidecar_state.network_name:
        result = proxy_paths._run_docker("network", "rm", _sidecar_state.network_name)
        if result.returncode != 0:
            logger.debug(
                "docker network rm %s exited %s: %s",
                _sidecar_state.network_name,
                result.returncode,
                (result.stderr or "").strip(),
            )

    _sidecar_state.network_name = None
    _sidecar_state.network_created = False


def get_token_reduction_mounts_and_envs(
    token_reduce: bool = False,
    mitm_web: bool = False,
) -> tuple[list[str], dict[str, str]]:
    """Build token-reduction mounts/env vars for an explicitly opted-in or auto-detected run.

    Opt-in is strictly ``--token-reduce``, ``--mitm-web``, ``HOLON_TOKEN_REDUCE`` in ``("1", "true", "yes",
    "on")``, or auto-detected holon-coherence proxy. Host ``HTTP_PROXY``/``HTTPS_PROXY`` alone never
    change sandbox networking. Any failure degrades to direct egress (empty mounts/envs) with an
    actionable warning log.
    """
    if not coherence._token_reduce_opt_in(token_reduce or mitm_web):
        return [], {}

    try:
        is_passive = os.getenv("HOLON_PASSIVE_MONITORING", "").strip().lower() in _TRUTHY_ENV_VALUES
        if token_reduce or mitm_web or is_passive:
            if mitm_web:
                return setup_token_reduction_proxy(mitm_web=True)
            return setup_token_reduction_proxy()
        return proxy_attach._attach_external_proxy()
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        logger.warning(
            "holon-coherence proxy configuration failed (%s: %s). Proceeding with DIRECT egress fallback.",
            type(exc).__name__,
            messages.redact_cli_error(exc),
        )
        logger.error(
            "Token reduction is enabled but could not be configured (%s: %s). This run continues with DIRECT "
            "egress (no TLS interception, no token reduction).",
            type(exc).__name__,
            messages.redact_cli_error(exc),
        )
        return [], {}
