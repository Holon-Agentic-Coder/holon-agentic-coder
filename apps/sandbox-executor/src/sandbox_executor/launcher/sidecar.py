"""The per-run holon-coherence token reduction sidecar: setup, teardown and the token-reduction mounts and env vars.

``detect_holon_coherence`` and ``get_host_proxy_envs`` live in ``launcher.coherence`` and are re-exported here, so
``sandbox_executor.launcher.sidecar`` is the one import path for the proxy surface the flow engine uses.
"""

import logging
import os
import subprocess
import uuid
from dataclasses import dataclass

from sandbox_executor.launcher import (
    coherence,
    messages,
    proxy_attach,
    proxy_image,
    proxy_paths,
    proxy_readiness,
    root_ca,
    sidecar_plan,
)
from sandbox_executor.launcher.coherence import _TRUTHY_ENV_VALUES
from sandbox_executor.launcher.coherence import detect_holon_coherence as detect_holon_coherence
from sandbox_executor.launcher.coherence import get_host_proxy_envs as get_host_proxy_envs
from sandbox_executor.launcher.proxy_paths import PROXY_LISTEN_PORT
from sandbox_executor.launcher.proxy_readiness import PROXY_READY_TIMEOUT_SECONDS
from sandbox_executor.redaction import COMMAND_ECHO_ENV_NAMES, redact_agent_secrets, redact_env_literals

logger = logging.getLogger(__name__)


@dataclass
class _SidecarState:
    """Tracks the proxy resources created by THIS run so teardown never touches foreign ones."""

    container_name: str | None = None
    network_name: str | None = None
    network_created: bool = False


_sidecar_state = _SidecarState()


def setup_token_reduction_proxy(mitm_web: bool = False) -> tuple[list[str], dict[str, str]]:
    """Start this run's holon-coherence proxy sidecar and return the sandbox mounts and env vars.

    Resources are named per run (pid + uuid suffix) and recorded in ``_sidecar_state`` so teardown
    only ever removes what this run created. The image comes from ``proxy_image.coherence_proxy_image()``.

    Raises:
        RuntimeError: If Docker networking, the sidecar spawn, or the readiness probe fails.
    """
    container_name, network_name = _sidecar_names()

    ca_cert_path, ca_key_path = root_ca.generate_root_ca()

    proxy_cache_dir, mitm_ca_combined, mitm_ca_cert, host_wire_log_dir, host_cache_dir = sidecar_plan._proxy_host_dirs(
        ca_cert_path, ca_key_path
    )
    web_port = sidecar_plan._dashboard_port(mitm_web)
    plan = sidecar_plan._SidecarPlan(
        container_name=container_name,
        network_name=network_name,
        image=proxy_image.coherence_proxy_image(),
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


def _spawn_sidecar(plan: sidecar_plan._SidecarPlan) -> None:
    """Record this run's resources, create its network and start the sidecar; tears down and raises on failure."""
    _sidecar_state.network_name = plan.network_name
    _sidecar_state.network_created = proxy_paths._ensure_network(plan.network_name)
    _sidecar_state.container_name = plan.container_name

    docker_run_proxy = sidecar_plan._sidecar_command(plan)

    logger.info(
        "Starting holon-coherence proxy sidecar '%s' from image '%s'; a first run may have to pull the image, "
        "which can take a while before the container appears.",
        plan.container_name,
        plan.image,
    )
    proxy_spawn = subprocess.run(
        docker_run_proxy, capture_output=True, text=True, check=False, env=sidecar_plan._sidecar_spawn_env(plan)
    )
    if proxy_spawn.returncode != 0:
        # The sidecar's output can echo the mitmweb `?token=` login URL or the `web_password=` setting.
        stderr = redact_env_literals(
            redact_agent_secrets((proxy_spawn.stderr or "").strip() or (proxy_spawn.stdout or "").strip()),
            names=COMMAND_ECHO_ENV_NAMES,
        )
        teardown_token_reduction_proxy()
        raise RuntimeError(
            f"holon-coherence sidecar '{plan.container_name}' failed to start: {stderr or 'unknown docker error'}. "
            "Re-run without --token-reduce to execute with direct egress."
        )


def _await_sidecar_ready(container_name: str) -> None:
    """Wait until the sidecar answers an HTTP proxy request on its published port; tears down and raises if not."""
    host_port = proxy_paths._published_loopback_port(container_name)
    if host_port is None:
        teardown_token_reduction_proxy()
        raise RuntimeError(
            f"holon-coherence sidecar '{container_name}' published no host loopback port, so its readiness cannot be "
            "verified. Re-run without --token-reduce to execute with direct egress."
        )
    if not proxy_readiness.wait_for_proxy("127.0.0.1", host_port):
        teardown_token_reduction_proxy()
        raise RuntimeError(
            f"holon-coherence sidecar '{container_name}' never answered an HTTP proxy request on "
            f"127.0.0.1:{host_port} within {PROXY_READY_TIMEOUT_SECONDS}s (the holon-coherence image likely failed "
            "to start its proxy; check `docker logs` and the image set by HOLON_COHERENCE_IMAGE). "
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
