"""Host-local LLM support for containerized Holon agents.

A sandboxed agent cannot reach an inference server that listens on the host, because loopback and the host's own LAN
address are not routable inside the container network namespace. The Docker gateway name (``host.docker.internal``) is
the only address that works, and nothing in the host CLI ever told the agent about it (Bean 0049).

This module is the client-side counterpart of the ``server_connect`` rewrite Bean 0027 added to the
``holon-coherence`` proxy: here the *authority in the configured base URL* is rewritten, so the agent connects to the
gateway directly instead of having its request dropped by an unreachable upstream.

Safety invariant inherited from Bean 0027: only loopback and explicitly allow-listed authorities are rewritten.
RFC1918 ranges are never blanket-rewritten, because a genuinely remote inference box on the same LAN would otherwise be
silently redirected onto the host machine.
"""

import copy
import json
import logging
import os
import stat
from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

# Opt-in switch. Never inferred from the presence of a base URL in a config file.
ENV_LOCAL_MODE = "HOLON_LOCAL_LLM"
# Base URL handed to the agent when there is no host models.json to copy from.
ENV_LOCAL_BASE_URL = "HOLON_LOCAL_BASE_URL"
# Provider name and model ids used to synthesize a config when no host config exists.
ENV_LOCAL_PROVIDER = "HOLON_LOCAL_PROVIDER"
ENV_LOCAL_MODELS = "HOLON_LOCAL_MODELS"
# Explicit, comma-separated allow list of host authorities that may be rewritten (Bean 0027 parity).
ENV_HOST_LOCAL_HOSTS = "HOLON_HOST_LOCAL_HOSTS"
# Pi resolves its agent directory (and therefore models.json) from this variable.
ENV_PI_AGENT_DIR = "PI_CODING_AGENT_DIR"

# The only address a container can use to reach the host on Docker Desktop and, with --add-host, on Linux alike.
GATEWAY_HOST = "host.docker.internal"
# Container-side location of the generated, rewritten agent directory.
CONTAINER_AGENT_DIR = "/home/holon/.holon-pi-agent"

_TRUTHY_ENV_VALUES = ("1", "true", "yes", "on")
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"})
# Candidate locations of the pi agent directory, newest layout first.
_PI_AGENT_DIR_CANDIDATES = (".pi/agent", ".config/pi")


class LocalLLMConfigError(ValueError):
    """Raised when local mode is requested but cannot be satisfied; never silently degraded."""


def local_llm_requested() -> bool:
    """True only when the operator explicitly opts into a host-local model."""
    return os.getenv(ENV_LOCAL_MODE, "").strip().lower() in _TRUTHY_ENV_VALUES


def host_local_allow_list() -> set[str]:
    """Authorities explicitly declared rewritable, lower-cased and empty entries dropped."""
    raw = os.getenv(ENV_HOST_LOCAL_HOSTS, "")
    return {entry.strip().lower() for entry in raw.split(",") if entry.strip()}


def rewrite_authority(url: str, allow_list: Iterable[str] = ()) -> str:
    """Replace a loopback or allow-listed authority with the Docker gateway host.

    Scheme, port, path and query are preserved. Anything else -- a public host, or a LAN host that was not explicitly
    declared -- is returned untouched, so a remote inference server is never redirected onto the host.
    """
    allowed = {entry.lower() for entry in allow_list}
    try:
        parsed = urlsplit(url)
    except ValueError:
        logger.warning("Ignoring unparseable base URL %r during host-local rewrite", url)
        return url

    host = (parsed.hostname or "").lower()
    if not parsed.scheme or not host:
        logger.warning("Ignoring base URL %r without scheme or host during host-local rewrite", url)
        return url
    if host == GATEWAY_HOST:
        return url
    if host in _LOOPBACK_HOSTS or host in allowed:
        netloc = GATEWAY_HOST if parsed.port is None else f"{GATEWAY_HOST}:{parsed.port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, ""))
    return url


def pi_agent_dirs() -> list[str]:
    """Host-side pi agent directories, most recent layout first."""
    home = os.path.expanduser("~")
    return [os.path.join(home, *rel.split("/")) for rel in _PI_AGENT_DIR_CANDIDATES]


def host_models_json() -> dict | None:
    """The host pi ``models.json`` (providers with their natural base URLs), or None when absent/invalid."""
    for agent_dir in pi_agent_dirs():
        path = os.path.join(agent_dir, "models.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read %s: %s", path, exc)
            continue
        if isinstance(data, dict):
            return data
        logger.warning("Ignoring %s: expected a JSON object at the top level", path)
    return None


def _synthesized_config(allow_list: Iterable[str]) -> dict:
    base_url = os.getenv(ENV_LOCAL_BASE_URL, "").strip()
    if not base_url:
        raise LocalLLMConfigError(
            f"{ENV_LOCAL_MODE} is enabled but no endpoint is known. Either keep a models.json in the host pi agent "
            f"directory (~/.pi/agent/models.json), or set {ENV_LOCAL_BASE_URL} (for example "
            f"http://localhost:11434/v1) together with {ENV_LOCAL_MODELS} (comma-separated model ids)."
        )
    model_ids = [entry.strip() for entry in os.getenv(ENV_LOCAL_MODELS, "").split(",") if entry.strip()]
    if not model_ids:
        raise LocalLLMConfigError(
            f"{ENV_LOCAL_BASE_URL} is set but no model ids were declared; set {ENV_LOCAL_MODELS} with at least one "
            "model id served by that endpoint."
        )
    provider = os.getenv(ENV_LOCAL_PROVIDER, "").strip() or os.getenv("HOLON_AGENT_PROVIDER", "").strip() or "local"
    rewritten = rewrite_authority(base_url, allow_list)
    return {
        "providers": {
            provider: {
                "baseUrl": rewritten,
                "api": "openai-completions",
                # Local servers ignore the key; pi resolves credentials from this placeholder.
                "apiKey": "holon-local",
                "models": [{"id": model_id} for model_id in model_ids],
            },
        },
    }


def build_container_config(host_config: dict | None, allow_list: Iterable[str] = ()) -> dict:
    """Config to present to the container: every provider baseUrl rewritten, all other fields preserved."""
    if not host_config:
        return _synthesized_config(allow_list)

    config = copy.deepcopy(host_config)
    providers = config.get("providers")
    if not isinstance(providers, dict) or not providers:
        return _synthesized_config(allow_list)
    for name, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        base_url = provider.get("baseUrl")
        if isinstance(base_url, str) and base_url:
            rewritten = rewrite_authority(base_url, allow_list)
            if rewritten != base_url:
                logger.info("Rewrote provider %r baseUrl for sandbox reachability: %s -> %s", name, base_url, rewritten)
            provider["baseUrl"] = rewritten
    return config


def prepare_agent_dir(dest_dir: str, host_config: dict | None = None, allow_list: Iterable[str] = ()) -> dict:
    """Materialize a writable container-side pi agent directory and return the config it contains.

    The directory holds a ``models.json`` whose authorities are reachable from the container. It is written mode 0700
    with the file at 0600 and mounted read-write, because pi persists sessions and settings next to ``models.json``.
    """
    config = build_container_config(host_config, allow_list)
    os.makedirs(dest_dir, mode=0o700, exist_ok=True)
    path = os.path.join(dest_dir, "models.json")
    with open(path, "w") as handle:
        json.dump(config, handle, indent=2)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return config


def rewritten_local_hosts(config: dict) -> list[str]:
    """``host:port`` authorities in the generated config that now point at the gateway (for NO_PROXY scoping)."""
    hosts = []
    for provider in (config.get("providers") or {}).values():
        base_url = provider.get("baseUrl") if isinstance(provider, dict) else None
        if not isinstance(base_url, str):
            continue
        parsed = urlsplit(base_url)
        if parsed.hostname == GATEWAY_HOST:
            hosts.append(f"{GATEWAY_HOST}:{parsed.port}" if parsed.port else GATEWAY_HOST)
    return sorted(set(hosts))


def container_mount_args(dest_dir: str) -> list[str]:
    """Docker args exposing the generated agent directory, and where pi must find it."""
    return ["-v", f"{dest_dir}:{CONTAINER_AGENT_DIR}:rw"]


def container_env(dest_dir: str) -> dict[str, str]:
    """Environment that points pi at the generated agent directory."""
    return {ENV_PI_AGENT_DIR: CONTAINER_AGENT_DIR}
