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
import ipaddress
import json
import logging
import os
import re
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
# Names that denote the local machine without being parseable as an address. Address literals are classified with
# ipaddress so that the whole 127.0.0.0/8 range and IPv6 ::1 are covered, not just 127.0.0.1.
_LOCAL_HOST_NAMES = frozenset({"localhost", "localhost.", "localhost.localdomain"})
# Candidate locations of the pi agent directory, newest layout first.
_PI_AGENT_DIR_CANDIDATES = (".pi/agent", ".config/pi")


class LocalLLMConfigError(ValueError):
    """Raised when local mode is requested but cannot be satisfied; never silently degraded."""


def normalize_agent_id(agent_id: str) -> str:
    """Normalize agent identifier (e.g. 'pi-agent', 'agent-pi', 'Pi' -> 'pi')."""
    return agent_id.lower().replace("-agent", "").replace("agent-", "")


def local_llm_requested() -> bool:
    """True only when the operator explicitly opts into a host-local model."""
    return os.getenv(ENV_LOCAL_MODE, "").strip().lower() in _TRUTHY_ENV_VALUES


def host_local_allow_list() -> set[str]:
    """Authorities explicitly declared rewritable, lower-cased and empty entries dropped."""
    raw = os.getenv(ENV_HOST_LOCAL_HOSTS, "")
    allow = set()
    for entry in raw.split(","):
        cleaned = entry.strip().lower()
        if not cleaned:
            continue
        cleaned = re.sub(r"^https?://", "", cleaned)
        cleaned = cleaned.split("/")[0].strip()
        if cleaned:
            allow.add(cleaned)
    return allow


def _is_local_host(host: str) -> bool:
    """True for names and addresses that mean 'this machine' (loopback range, ::1, and unspecified)."""
    if host in _LOCAL_HOST_NAMES:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    # Unspecified (0.0.0.0 / ::) is how local servers are commonly advertised; as a client destination it can only
    # resolve to the local machine, so rewriting it is safe.
    return address.is_loopback or address.is_unspecified


def _authority(url: str) -> tuple[str, int | None] | None:
    """Return ``(host, port)`` or None when the URL is unusable.

    ``SplitResult.port`` validates lazily on access and raises for a malformed port, so every port read goes through
    here; a bad ``baseUrl`` in a user config must degrade to "untouched", not crash the launcher.
    """
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if not parsed.scheme or not host:
            return None
        return host, parsed.port
    except ValueError:
        logger.warning("Ignoring base URL with an invalid port or host: %r", url)
        return None


def rewrite_authority(url: str, allow_list: Iterable[str] = ()) -> str:
    """Replace a loopback or allow-listed authority with the Docker gateway host.

    Scheme, port, path and query are preserved. Anything else -- a public host, or a LAN host that was not explicitly
    declared -- is returned untouched, so a remote inference server is never redirected onto the host.
    """
    allowed = {entry.lower() for entry in allow_list}
    authority = _authority(url)
    if authority is None:
        logger.warning("Ignoring unparseable base URL %r during host-local rewrite", url)
        return url

    host, port = authority
    if host == GATEWAY_HOST:
        return url
    if _is_local_host(host) or host in allowed or (port is not None and f"{host}:{port}" in allowed):
        netloc = GATEWAY_HOST if port is None else f"{GATEWAY_HOST}:{port}"
        try:
            parsed = urlsplit(url)
            return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
        except ValueError:
            return url
    return url


def pi_agent_dirs() -> list[str]:
    """Host-side pi agent directories, most recent layout first."""
    candidates = []
    env_dir = os.getenv(ENV_PI_AGENT_DIR, "").strip()
    if env_dir:
        candidates.append(os.path.expanduser(env_dir))
    home = os.path.expanduser("~")
    for rel in _PI_AGENT_DIR_CANDIDATES:
        path = os.path.join(home, *rel.split("/"))
        if path not in candidates:
            candidates.append(path)
    return candidates


def host_models_json() -> dict | None:
    """The host pi ``models.json`` (providers with their natural base URLs), or None when absent/invalid."""
    for agent_dir in pi_agent_dirs():
        path = os.path.join(agent_dir, "models.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
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
    auth = _authority(rewritten)
    if auth is None or auth[0] != GATEWAY_HOST:
        raise LocalLLMConfigError(
            f"The configured {ENV_LOCAL_BASE_URL} ({base_url}) is neither a loopback address nor in "
            f"{ENV_HOST_LOCAL_HOSTS}."
        )
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

    kept: dict[str, dict] = {}
    dropped: list[str] = []
    for name, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        base_url = provider.get("baseUrl")
        if not isinstance(base_url, str) or not base_url:
            dropped.append(name)
            continue
        rewritten = rewrite_authority(base_url, allow_list)
        authority = _authority(rewritten)
        # Keep only endpoints the container can actually reach. Cloud providers are dropped on purpose: this file is
        # mounted into the sandbox, and a copied provider entry would carry its literal apiKey across that boundary
        # while offering an endpoint the local-mode run cannot use anyway.
        if authority is None or authority[0] != GATEWAY_HOST:
            dropped.append(name)
            continue
        if rewritten != base_url:
            logger.info("Rewrote provider %r baseUrl for sandbox reachability: %s -> %s", name, base_url, rewritten)
        provider["baseUrl"] = rewritten
        if not provider.get("apiKey"):
            provider["apiKey"] = "holon-local"
        kept[name] = provider

    if not kept:
        logger.warning(
            "No host-local provider found among %s; falling back to the synthesized endpoint",
            ", ".join(sorted(providers)) or "<none>",
        )
        return _synthesized_config(allow_list)

    if dropped:
        logger.info("Excluded non-local provider(s) from the sandbox config: %s", ", ".join(sorted(dropped)))
    config["providers"] = kept
    return config


def prepare_agent_dir(dest_dir: str, host_config: dict | None = None, allow_list: Iterable[str] = ()) -> dict:
    """Materialize a writable container-side pi agent directory and return the config it contains.

    The directory holds a ``models.json`` whose authorities are reachable from the container, and is mounted
    read-write because pi persists sessions and settings next to ``models.json``.

    Modes are chosen for the *container* user, not the host user: the sandbox runs as uid 1000, so a 0700/0600 tree
    owned by the host uid would be unreadable on Linux. The file mode is therefore applied at creation time (never
    tightened afterwards, which would leave a readable-file window in a /tmp path) and the content is deliberately
    limited to host-local endpoint data -- cloud providers are stripped by :func:`build_container_config`.
    """
    config = build_container_config(host_config, allow_list)
    os.makedirs(dest_dir, mode=0o777, exist_ok=True)
    os.chmod(dest_dir, 0o777)
    path = os.path.join(dest_dir, "models.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.fchmod(fd, 0o644)
    except OSError:
        os.chmod(path, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
    return config


def rewritten_local_hosts(config: dict) -> list[str]:
    """``host:port`` authorities in the generated config that now point at the gateway (for NO_PROXY scoping)."""
    hosts = []
    for provider in (config.get("providers") or {}).values():
        base_url = provider.get("baseUrl") if isinstance(provider, dict) else None
        if not isinstance(base_url, str):
            continue
        authority = _authority(base_url)
        if authority is None or authority[0] != GATEWAY_HOST:
            continue
        hosts.append(f"{GATEWAY_HOST}:{authority[1]}" if authority[1] else GATEWAY_HOST)
    return sorted(set(hosts))


def container_mount_args(dest_dir: str) -> list[str]:
    """Docker args exposing the generated agent directory, and where pi must find it."""
    return ["-v", f"{dest_dir}:{CONTAINER_AGENT_DIR}:rw"]


def container_env(dest_dir: str) -> dict[str, str]:
    """Environment that points pi at the generated agent directory."""
    return {ENV_PI_AGENT_DIR: CONTAINER_AGENT_DIR}
