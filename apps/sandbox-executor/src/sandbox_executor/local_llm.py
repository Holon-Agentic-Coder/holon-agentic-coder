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
import http.client
import ipaddress
import json
import logging
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any
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

# Model envelope (Bean 0140). Reasoning mode for the generated model entries; validated against REASONING_MODES.
ENV_REASONING_MODE = "HOLON_REASONING_MODE"
# Tokens kept free for the prompt when bounding maxTokens: maxTokens may not exceed contextWindow minus this reserve.
ENV_CONTEXT_RESERVE_TOKENS = "HOLON_CONTEXT_RESERVE_TOKENS"
# Context window used for model entries that do not declare contextWindow; synthesis never guesses one.
ENV_LOCAL_CONTEXT_WINDOW = "HOLON_LOCAL_CONTEXT_WINDOW"
DEFAULT_CONTEXT_RESERVE_TOKENS = 4096
# Upper bound for a synthesised maxTokens: min(MAX_SYNTHESISED_OUTPUT_TOKENS, contextWindow // 4).
MAX_SYNTHESISED_OUTPUT_TOKENS = 16384
# Least-thinking first. "off" is the default for authoring stages, whose long outputs are what runs out of tokens.
REASONING_MODES = ("off", "minimal", "low", "medium", "high")
DEFAULT_REASONING_MODE = "off"

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


@dataclass(frozen=True)
class ModelEnvelope:
    """Effective output envelope of one generated model entry, with where each value came from."""

    provider: str
    model_id: str
    context_window: int
    max_tokens: int
    max_tokens_source: str  # "host" or "synthesised"
    reasoning: str
    reasoning_source: str  # "env", "host" or "default"

    def envelope_line(self) -> str:
        """``model=<id> context=<n> max_tokens=<n> source=host|synthesised``."""
        return (
            f"model={self.model_id} context={self.context_window} max_tokens={self.max_tokens} "
            f"source={self.max_tokens_source}"
        )

    def reasoning_line(self) -> str:
        """``reasoning=<mode> source=env|host|default``."""
        return f"reasoning={self.reasoning} source={self.reasoning_source}"


def _strict_int(value: Any) -> int | None:
    """``value`` as an int when it is a genuine integer (bools and floats are rejected), else None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _env_int(env: Mapping[str, str], name: str) -> int | None:
    raw = env.get(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise LocalLLMConfigError(f"{name}={raw!r} is not an integer.") from None
    if value <= 0:
        raise LocalLLMConfigError(f"{name}={raw!r} must be a positive integer.")
    return value


def _resolve_reasoning(env: Mapping[str, str], entry: dict) -> tuple[str, str]:
    raw_env = env.get(ENV_REASONING_MODE, "").strip().lower()
    if raw_env and raw_env not in REASONING_MODES:
        raise LocalLLMConfigError(
            f"{ENV_REASONING_MODE}={raw_env!r} is not a supported reasoning mode; use one of "
            f"{', '.join(REASONING_MODES)}."
        )
    host = entry.get("reasoning")
    # pi models.json uses a boolean "reasoning" capability flag; a host value of false means the model never thinks, so
    # no environment-wide mode may give it a thinking level.
    if host is False:
        if raw_env and raw_env != "off":
            logger.warning(
                "Ignoring %s=%s for model %r: its host entry declares reasoning: false; keeping reasoning off.",
                ENV_REASONING_MODE,
                raw_env,
                entry.get("id"),
            )
        return "off", "host"
    if raw_env:
        return raw_env, "env"
    if isinstance(host, str) and host.strip():
        mode = host.strip().lower()
        if mode not in REASONING_MODES:
            raise LocalLLMConfigError(
                f"Model {entry.get('id')!r} declares reasoning={host!r}; use one of {', '.join(REASONING_MODES)} "
                f"or set {ENV_REASONING_MODE}."
            )
        return mode, "host"
    return DEFAULT_REASONING_MODE, "default"


def apply_model_envelope(config: dict, env: Mapping[str, str] | None = None) -> list[ModelEnvelope]:
    """Give every model entry an explicit integer ``maxTokens`` and reasoning mode; return their provenance.

    - ``maxTokens`` absent: synthesised as ``min(16384, contextWindow // 4)`` and logged with model id, value and
      reason. Present: kept, after rejecting bools, non-integers, zero, negatives and values above
      ``contextWindow - reserve`` (``HOLON_CONTEXT_RESERVE_TOKENS``, default 4096).
    - ``contextWindow`` absent: taken from ``HOLON_LOCAL_CONTEXT_WINDOW``; without it the config is rejected rather
      than guessed.
    - Reasoning mode: ``HOLON_REASONING_MODE``, else the host entry, else ``off``; a host ``reasoning: false`` always
      wins (with a warning when the variable asks for more). pi's ``reasoning`` field is a
      boolean capability flag, so the entry carries ``reasoning: mode != "off"`` and the mode itself is written as the
      agent directory's default thinking level by :func:`prepare_agent_dir`.

    The config is modified in place. Raises :class:`LocalLLMConfigError` for any value it cannot honour.
    """
    env = os.environ if env is None else env
    reserve = _env_int(env, ENV_CONTEXT_RESERVE_TOKENS)
    reserve = DEFAULT_CONTEXT_RESERVE_TOKENS if reserve is None else reserve
    fallback_window = _env_int(env, ENV_LOCAL_CONTEXT_WINDOW)
    envelopes: list[ModelEnvelope] = []
    for provider_name, provider in (config.get("providers") or {}).items():
        if not isinstance(provider, dict):
            continue
        for entry in provider.get("models") or []:
            if not isinstance(entry, dict):
                continue
            model_id = str(entry.get("id", "<unnamed>"))
            if "contextWindow" in entry:
                window = _strict_int(entry["contextWindow"])
                if window is None or window <= 0:
                    raise LocalLLMConfigError(
                        f"Model {model_id!r} has contextWindow={entry['contextWindow']!r}; it must be a positive "
                        "integer."
                    )
            elif fallback_window is not None:
                window = fallback_window
                entry["contextWindow"] = window
            else:
                raise LocalLLMConfigError(
                    f"Model {model_id!r} declares no contextWindow, so maxTokens cannot be checked or synthesised. "
                    f"Add contextWindow to the model entry or set {ENV_LOCAL_CONTEXT_WINDOW}."
                )
            ceiling = window - reserve
            if "maxTokens" in entry:
                max_tokens = _strict_int(entry["maxTokens"])
                if max_tokens is None or max_tokens <= 0:
                    raise LocalLLMConfigError(
                        f"Model {model_id!r} has maxTokens={entry['maxTokens']!r}; it must be a positive integer."
                    )
                source = "host"
            else:
                max_tokens = min(MAX_SYNTHESISED_OUTPUT_TOKENS, window // 4)
                source = "synthesised"
            if max_tokens > ceiling:
                raise LocalLLMConfigError(
                    f"Model {model_id!r} maxTokens={max_tokens} ({source}) exceeds contextWindow {window} minus the "
                    f"{reserve}-token reserve ({ENV_CONTEXT_RESERVE_TOKENS}); lower maxTokens or the reserve."
                )
            if source == "synthesised":
                entry["maxTokens"] = max_tokens
                logger.info(
                    "Model %r: maxTokens=%d synthesised as min(%d, contextWindow %d // 4): absent in host config",
                    model_id,
                    max_tokens,
                    MAX_SYNTHESISED_OUTPUT_TOKENS,
                    window,
                )
            mode, mode_source = _resolve_reasoning(env, entry)
            entry["reasoning"] = mode != "off"
            logger.info("Model %r: reasoning=%s source=%s", model_id, mode, mode_source)
            envelopes.append(
                ModelEnvelope(
                    provider=provider_name,
                    model_id=model_id,
                    context_window=window,
                    max_tokens=max_tokens,
                    max_tokens_source=source,
                    reasoning=mode,
                    reasoning_source=mode_source,
                )
            )
    return envelopes


def build_envelope_config(
    host_config: dict | None, allow_list: Iterable[str] = (), env: Mapping[str, str] | None = None
) -> tuple[dict, list[ModelEnvelope]]:
    """The container config together with its model envelope, without writing anything (used by ``holon doctor``)."""
    config = build_container_config(host_config, allow_list)
    return config, apply_model_envelope(config, env)


def _thinking_level(envelopes: list[ModelEnvelope]) -> str:
    """Single agent-wide thinking level: the least-thinking mode among the models, so no model exceeds its own."""
    if not envelopes:
        return DEFAULT_REASONING_MODE
    return min((envelope.reasoning for envelope in envelopes), key=REASONING_MODES.index)


def prepare_agent_dir(dest_dir: str, host_config: dict | None = None, allow_list: Iterable[str] = ()) -> dict:
    """Materialize a writable container-side pi agent directory and return the config it contains.

    The directory holds a ``models.json`` whose authorities are reachable from the container, and is mounted
    read-write because pi persists sessions and settings next to ``models.json``.

    Modes are chosen for the *container* user, not the host user: the sandbox runs as uid 1000, so a 0700/0600 tree
    owned by the host uid would be unreadable on Linux. The file mode is therefore applied at creation time (never
    tightened afterwards, which would leave a readable-file window in a /tmp path) and the content is deliberately
    limited to host-local endpoint data -- cloud providers are stripped by :func:`build_container_config`.
    """
    config, envelopes = build_envelope_config(host_config, allow_list)
    os.makedirs(dest_dir, mode=0o777, exist_ok=True)
    os.chmod(dest_dir, 0o777)
    _write_shared_json(os.path.join(dest_dir, "models.json"), config)
    # pi reads its default thinking level from settings.json in the agent directory (Bean 0140).
    _write_shared_json(os.path.join(dest_dir, "settings.json"), {"defaultThinkingLevel": _thinking_level(envelopes)})
    return config


def _write_shared_json(path: str, data: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.fchmod(fd, 0o644)
    except OSError:
        os.chmod(path, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


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


# Gateway health probe (Bean 0140). Runs on the host only, against loopback or allow-listed authorities.
HEALTH_PATH = "/health"
HEALTH_TIMEOUT_SECONDS = 2.0
# Only these schemes are probed, and at most this many bytes of the response are read.
HEALTH_SCHEMES = ("http", "https")
HEALTH_MAX_BYTES = 1 << 20
# Key spellings accepted for the cache section and its counters; servers differ in naming.
_CACHE_SECTION_KEYS = ("prefill_cache", "prefix_cache", "prompt_cache", "cache")
_CACHE_COUNT_KEYS = ("entries", "num_entries", "cached_entries", "cached_tokens", "tokens", "size")

Fetcher = Callable[[str, float], bytes | str]


@dataclass(frozen=True)
class GatewayHealth:
    """What the local inference server reports about its prompt cache, memory and output defaults."""

    url: str
    prefill_cache: str  # "off", "cold", "warm" or "unknown"
    memory: dict[str, Any]
    max_output_tokens: int | None
    error: str | None = None
    max_prompt_tokens: int | None = None

    def cache_line(self) -> str:
        """``prefill_cache=<state>`` followed by any memory figures the server reported."""
        figures = " ".join(f"{key}={value}" for key, value in sorted(self.memory.items()))
        suffix = f" ({self.error})" if self.error else ""
        return f"prefill_cache={self.prefill_cache}{' ' + figures if figures else ''}{suffix}"


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Fail on any redirect: the probe may only reach the loopback or allow-listed authority it was given."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused by health probe", headers, fp)


def _default_fetch(url: str, timeout: float) -> bytes:
    # Proxies are bypassed explicitly: the target is the operator's own machine, and an HTTP(S)_PROXY from the
    # environment would otherwise receive the request. Redirects are refused so a loopback responder cannot send the
    # probe to a host that health_url() would not have allowed. The opener still carries urllib's file:// and ftp://
    # handlers, so the scheme is checked here as well as in health_url(), and the read is capped.
    if urlsplit(url).scheme.lower() not in HEALTH_SCHEMES:
        raise ValueError(f"health probe only supports http and https, not {url!r}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RefuseRedirects())
    with opener.open(url, timeout=timeout) as response:
        body = response.read(HEALTH_MAX_BYTES + 1)
    if len(body) > HEALTH_MAX_BYTES:
        raise ValueError(f"health response exceeds {HEALTH_MAX_BYTES} bytes")
    return body


def health_url(base_url: str, allow_list: Iterable[str] = ()) -> str | None:
    """Host-side health URL for a provider base URL, or None when the authority is not one we may probe.

    Only authorities :func:`rewrite_authority` would rewrite (loopback or explicitly allow-listed) are probed, so the
    doctor never reaches out to a remote host. The gateway name itself is container-side and not probed.
    """
    if urlsplit(base_url).scheme.lower() not in HEALTH_SCHEMES:
        return None
    authority = _authority(base_url)
    if authority is None:
        return None
    if authority[0] == GATEWAY_HOST or rewrite_authority(base_url, allow_list) == base_url:
        return None
    parsed = urlsplit(base_url)
    # Rebuilt from host and port only, so userinfo (user:token@) never reaches the probe URL or the doctor output.
    host, port = authority
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, HEALTH_PATH, "", ""))


def _numeric_figures(section: Any, prefix: str = "") -> dict[str, Any]:
    figures: dict[str, Any] = {}
    if not isinstance(section, dict):
        return figures
    for key, value in section.items():
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        lowered = key.lower()
        if any(token in lowered for token in ("mem", "bytes", "mb", "gb")):
            figures[f"{prefix}{key}"] = value
    return figures


def parse_gateway_health(url: str, payload: Any) -> GatewayHealth:
    """Classify a health response into ``off``/``cold``/``warm`` and extract memory and output defaults.

    Cache disabled is ``off``; enabled with zero cached entries or tokens is ``cold``; otherwise ``warm``. Anything
    unrecognised is ``unknown``, never an error.
    """
    if not isinstance(payload, dict):
        return GatewayHealth(url, "unknown", {}, None, "unrecognised health response")
    cache = next((payload[key] for key in _CACHE_SECTION_KEYS if isinstance(payload.get(key), dict)), None)
    state = "unknown"
    memory = _numeric_figures(payload.get("memory"), "memory_")
    if cache is not None:
        memory.update(_numeric_figures(cache, "cache_"))
        enabled = cache.get("enabled")
        counts = [cache[key] for key in _CACHE_COUNT_KEYS if isinstance(cache.get(key), int | float)]
        counts = [count for count in counts if not isinstance(count, bool)]
        if enabled is False:
            state = "off"
        elif enabled is True or counts:
            state = "warm" if any(count > 0 for count in counts) else "cold"
    defaults = payload.get("effective_defaults")
    defaults = defaults if isinstance(defaults, dict) else {}
    max_output = defaults.get("max_output_tokens")
    # Servers report the prompt ceiling inside effective_defaults or at the top level.
    max_prompt = defaults.get("max_prompt_tokens", payload.get("max_prompt_tokens"))
    return GatewayHealth(url, state, memory, _strict_int(max_output), max_prompt_tokens=_strict_int(max_prompt))


def probe_gateway_health(
    base_url: str,
    fetch: Fetcher | None = None,
    allow_list: Iterable[str] = (),
    timeout: float = HEALTH_TIMEOUT_SECONDS,
) -> GatewayHealth | None:
    """Probe the local server behind ``base_url`` (host-side, pre-rewrite URL); None when it may not be probed.

    ``fetch`` is injected in tests; the default uses ``urllib.request`` with a 2 second timeout. Network or parse
    failures are reported as ``prefill_cache=unknown`` and never raise.
    """
    url = health_url(base_url, allow_list)
    if url is None:
        return None
    fetcher = fetch or _default_fetch
    try:
        raw = fetcher(url, timeout)
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        payload = json.loads(text)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        # HTTPException covers malformed responses (BadStatusLine, IncompleteRead), which are not OSErrors.
        return GatewayHealth(url, "unknown", {}, None, f"health probe failed: {exc}")
    return parse_gateway_health(url, payload)


def host_base_urls(host_config: dict | None, env: Mapping[str, str] | None = None) -> list[str]:
    """Host-side (pre-rewrite) base URLs the generated config was built from, for probing from the host."""
    env = os.environ if env is None else env
    urls: list[str] = []
    for provider in ((host_config or {}).get("providers") or {}).values():
        base_url = provider.get("baseUrl") if isinstance(provider, dict) else None
        if isinstance(base_url, str) and base_url:
            urls.append(base_url)
    configured = env.get(ENV_LOCAL_BASE_URL, "").strip()
    if configured:
        urls.append(configured)
    return list(dict.fromkeys(urls))
