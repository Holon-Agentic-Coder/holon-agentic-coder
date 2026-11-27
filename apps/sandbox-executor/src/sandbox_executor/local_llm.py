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
from typing import Any, NotRequired, TypedDict
from urllib.parse import urlsplit, urlunsplit

from sandbox_executor.redaction import starts_with_url_authority, trusted_url_authority, url_userinfo_end

logger = logging.getLogger(__name__)

ENV_LOCAL_MODE = "HOLON_LOCAL_LLM"
ENV_LOCAL_BASE_URL = "HOLON_LOCAL_BASE_URL"
ENV_LOCAL_PROVIDER = "HOLON_LOCAL_PROVIDER"
ENV_LOCAL_MODELS = "HOLON_LOCAL_MODELS"
ENV_AGENT_PROVIDER = "HOLON_AGENT_PROVIDER"
ENV_HOST_LOCAL_HOSTS = "HOLON_HOST_LOCAL_HOSTS"
ENV_PI_AGENT_DIR = "PI_CODING_AGENT_DIR"
ENV_REASONING_MODE = "HOLON_REASONING_MODE"
ENV_CONTEXT_RESERVE_TOKENS = "HOLON_CONTEXT_RESERVE_TOKENS"
ENV_LOCAL_CONTEXT_WINDOW = "HOLON_LOCAL_CONTEXT_WINDOW"
DEFAULT_CONTEXT_RESERVE_TOKENS = 4096
MAX_SYNTHESISED_OUTPUT_TOKENS = 16384
REASONING_MODES = ("off", "minimal", "low", "medium", "high")
DEFAULT_REASONING_MODE = "off"
TEXT_SETTINGS = (
    ENV_LOCAL_BASE_URL,
    ENV_LOCAL_PROVIDER,
    ENV_AGENT_PROVIDER,
    ENV_LOCAL_MODELS,
    ENV_HOST_LOCAL_HOSTS,
    ENV_REASONING_MODE,
    ENV_CONTEXT_RESERVE_TOKENS,
    ENV_LOCAL_CONTEXT_WINDOW,
)

GATEWAY_HOST = "host.docker.internal"
CONTAINER_AGENT_DIR = "/home/holon/.holon-pi-agent"

_TRUTHY_ENV_VALUES = ("1", "true", "yes", "on")
_LOCAL_HOST_NAMES = frozenset({"localhost", "localhost.", "localhost.localdomain"})
_PI_AGENT_DIR_CANDIDATES = (".pi/agent", ".config/pi")
_SYNTHESISED_API_PROTOCOL = "openai-completions"
_SYNTHESISED_API_KEY = "holon-local"
_SYNTHESISED_OUTPUT_WINDOW_DIVISOR = 4
_AGENT_DIR_MODE = 0o777
_SHARED_JSON_MODE = 0o644
_PROVIDER_STRING_FIELDS = ("baseUrl", "apiKey", "api")
_MODEL_TOKEN_FIELDS = ("contextWindow", "maxTokens")
_MEMORY_KEY_TOKENS = ("mem", "bytes", "mb", "gb")
_HTTP_SCHEME_PREFIX_RE = re.compile(r"^https?://")
_QUERY_OR_FRAGMENT_RE = re.compile(r"[?#]")


class LocalLLMConfigError(ValueError):
    pass


def is_utf8_text(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def printable(text: str) -> str:
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _not_text_error(name: str) -> LocalLLMConfigError:
    return LocalLLMConfigError(
        f"{name} is not valid UTF-8 text (a lone surrogate or a non-UTF-8 byte); value withheld."
    )


def check_text_settings(env: Mapping[str, str] | None = None) -> None:
    env = os.environ if env is None else env
    for name in TEXT_SETTINGS:
        if not is_utf8_text(env.get(name, "")):
            raise _not_text_error(name)


def normalize_agent_id(agent_id: str) -> str:
    from sandbox_executor.agent_defaults import normalise_agent_id

    return normalise_agent_id(agent_id)


def local_llm_requested() -> bool:
    return os.getenv(ENV_LOCAL_MODE, "").strip().lower() in _TRUTHY_ENV_VALUES


def host_local_allow_list() -> set[str]:
    raw = os.getenv(ENV_HOST_LOCAL_HOSTS, "")
    if not is_utf8_text(raw):
        raise _not_text_error(ENV_HOST_LOCAL_HOSTS)
    allow = set()
    for entry in raw.split(","):
        cleaned = entry.strip().lower()
        if not cleaned:
            continue
        cleaned = _HTTP_SCHEME_PREFIX_RE.sub("", cleaned)
        cleaned = cleaned.split("/")[0].strip()
        if cleaned:
            allow.add(cleaned)
    return allow


def _is_local_host(host: str) -> bool:
    if host in _LOCAL_HOST_NAMES:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


_USERINFO_MARKER = "***@"
_WITHHELD_HOST = "***"
_URL_DELIMITERS_RE = re.compile(r"[/?#]")


def shown_authority(url: str) -> str | None:
    shown = trusted_url_authority(url)
    if shown is None:
        return None
    stripped = url.strip()
    rest = stripped[len(_lead(stripped)) :]
    userinfo_end = url_userinfo_end(rest)
    if userinfo_end is not None and _URL_DELIMITERS_RE.search(rest[:userinfo_end]):
        return None
    return shown


def _lead(url: str) -> str:
    if not starts_with_url_authority(url):
        return ""
    return url[: url.index("//") + 2]


def redact_url(url: str) -> str:
    stripped = url.strip()
    lead = _lead(stripped)
    if shown_authority(stripped) is None:
        return lead + _WITHHELD_HOST
    rest = stripped[len(lead) :]
    userinfo_end = url_userinfo_end(rest)
    marker, tail = (_USERINFO_MARKER, rest[userinfo_end + 1 :]) if userinfo_end is not None else ("", rest)
    return lead + marker + _QUERY_OR_FRAGMENT_RE.split(tail, maxsplit=1)[0]


def _authority(url: str) -> tuple[str, int | None] | None:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if not parsed.scheme or not host:
            return None
        return host, parsed.port
    except ValueError:
        logger.warning("Ignoring a base URL with an invalid port or host (value withheld).")
        return None


def _is_rewritable_authority(host: str, port: int | None, allowed: set[str]) -> bool:
    return _is_local_host(host) or host in allowed or (port is not None and f"{host}:{port}" in allowed)


def rewrite_authority(url: str, allow_list: Iterable[str] = ()) -> str:
    allowed = {entry.lower() for entry in allow_list}
    authority = _authority(url)
    if authority is None:
        logger.warning("Ignoring an unparseable base URL during host-local rewrite (value withheld).")
        return url

    host, port = authority
    if host == GATEWAY_HOST:
        return url
    if _is_rewritable_authority(host, port, allowed):
        netloc = GATEWAY_HOST if port is None else f"{GATEWAY_HOST}:{port}"
        try:
            parsed = urlsplit(url)
            return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
        except ValueError:
            return url
    return url


def pi_agent_dirs() -> list[str]:
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


MODELS_JSON_MAX_BYTES = 1 << 20
MAX_JSON_DEPTH = 64
MAX_JSON_INT_DIGITS = 19
MAX_TOKEN_COUNT = 2**31 - 1


class MalformedJSONError(ValueError):
    pass


def _bounded_int(text: str) -> int:
    if len(text.lstrip("-")) > MAX_JSON_INT_DIGITS:
        raise MalformedJSONError(f"an integer has more than {MAX_JSON_INT_DIGITS} digits")
    return int(text)


def _finite_float(text: str) -> float:
    value = float(text)
    if value != value or value in (float("inf"), float("-inf")):
        raise MalformedJSONError("a number is out of the finite range")
    return value


def _reject_constant(_name: str) -> Any:
    raise MalformedJSONError("NaN and Infinity are not JSON")


def _json_depth_exceeds(value: Any, limit: int) -> bool:
    stack = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, dict | list):
            if depth > limit:
                return True
            children = node.values() if isinstance(node, dict) else node
            stack.extend((child, depth + 1) for child in children if isinstance(child, dict | list))
    return False


def _has_non_text_string(value: Any) -> bool:
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            if not is_utf8_text(node):
                return True
        elif isinstance(node, dict):
            stack.extend(node.keys())
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return False


def parse_bounded_json(raw: bytes | str) -> Any:
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        data = json.loads(text, parse_int=_bounded_int, parse_float=_finite_float, parse_constant=_reject_constant)
    except MalformedJSONError:
        raise
    except UnicodeDecodeError:
        raise MalformedJSONError("not valid UTF-8") from None
    except json.JSONDecodeError as exc:
        raise MalformedJSONError(f"invalid JSON at line {exc.lineno} column {exc.colno}") from None
    except RecursionError:
        raise MalformedJSONError(f"nested deeper than {MAX_JSON_DEPTH} levels") from None
    except (ValueError, OverflowError) as exc:
        raise MalformedJSONError(f"unreadable JSON ({type(exc).__name__})") from None
    if _json_depth_exceeds(data, MAX_JSON_DEPTH):
        raise MalformedJSONError(f"nested deeper than {MAX_JSON_DEPTH} levels")
    if _has_non_text_string(data):
        raise MalformedJSONError("a string is not valid Unicode (a lone surrogate escape)")
    return data


class ModelEntry(TypedDict):
    id: str
    contextWindow: NotRequired[int]
    maxTokens: NotRequired[int]
    reasoning: NotRequired[bool | str]


class ProviderEntry(TypedDict, total=False):
    baseUrl: str
    apiKey: str
    api: str
    models: list[ModelEntry]


class ModelsConfig(TypedDict, total=False):
    providers: dict[str, ProviderEntry]


def _token_count_ok(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and 0 < value <= MAX_TOKEN_COUNT


def _validate_model_entry(provider_location: str, index: int, entry: Any) -> None:
    location = f"{provider_location} models[{index}]"
    if not isinstance(entry, dict):
        raise LocalLLMConfigError(f"{location} must be an object.")
    model_id = entry.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        raise LocalLLMConfigError(f"{location} field 'id' must be a non-empty string.")
    if not is_utf8_text(model_id):
        raise LocalLLMConfigError(f"{location} field 'id' is not valid UTF-8 text.")
    model_location = f"{provider_location} model {model_id!r}"
    for key in _MODEL_TOKEN_FIELDS:
        if key in entry and not _token_count_ok(entry[key]):
            raise LocalLLMConfigError(
                f"{model_location} field {key!r} must be a positive integer of at most {MAX_TOKEN_COUNT}."
            )
    if "reasoning" in entry and not isinstance(entry["reasoning"], bool | str):
        raise LocalLLMConfigError(f"{model_location} field 'reasoning' must be a boolean or a string.")
    if isinstance(entry.get("reasoning"), str) and not is_utf8_text(entry["reasoning"]):
        raise LocalLLMConfigError(f"{model_location} field 'reasoning' is not valid UTF-8 text.")


def _validate_provider_entry(source: str, name: str, provider: Any) -> None:
    if not is_utf8_text(name):
        raise LocalLLMConfigError(f"{source}: a provider name is not valid UTF-8 text.")
    provider_location = f"{source}: provider {name!r}"
    if not isinstance(provider, dict):
        raise LocalLLMConfigError(f"{provider_location} must be an object.")
    for key in _PROVIDER_STRING_FIELDS:
        if key in provider and not isinstance(provider[key], str):
            raise LocalLLMConfigError(f"{provider_location} field {key!r} must be a string.")
        if key in provider and not is_utf8_text(provider[key]):
            raise LocalLLMConfigError(f"{provider_location} field {key!r} is not valid UTF-8 text.")
    models = provider.get("models")
    if models is None:
        return
    if not isinstance(models, list):
        raise LocalLLMConfigError(f"{provider_location} field 'models' must be a list.")
    for index, entry in enumerate(models):
        _validate_model_entry(provider_location, index, entry)


def validate_models_config(data: Any, source: str = "models.json") -> ModelsConfig:
    if not isinstance(data, dict):
        raise LocalLLMConfigError(f"{source}: the top level must be a JSON object.")
    providers = data.get("providers")
    if providers is None:
        return data  # type: ignore[return-value]
    if not isinstance(providers, dict):
        raise LocalLLMConfigError(f"{source}: 'providers' must be an object of provider entries.")
    for name, provider in providers.items():
        _validate_provider_entry(source, name, provider)
    return data  # type: ignore[return-value]


def load_models_json(path: str) -> ModelsConfig:
    shown = printable(path)
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MODELS_JSON_MAX_BYTES + 1)
    except OSError as exc:
        raise LocalLLMConfigError(f"Could not read {shown} ({type(exc).__name__}).") from None
    if len(raw) > MODELS_JSON_MAX_BYTES:
        raise LocalLLMConfigError(f"{shown} exceeds {MODELS_JSON_MAX_BYTES} bytes.")
    try:
        data = parse_bounded_json(raw)
    except MalformedJSONError as exc:
        raise LocalLLMConfigError(f"{shown} is not valid JSON: {exc}.") from None
    return validate_models_config(data, shown)


def host_models_json() -> ModelsConfig | None:
    for agent_dir in pi_agent_dirs():
        path = os.path.join(agent_dir, "models.json")
        if os.path.isfile(path):
            return load_models_json(path)
    return None


def _providers(config: Mapping[str, Any] | None, source: str = "models.json") -> dict[str, ProviderEntry]:
    if not config:
        return {}
    return validate_models_config(config, source).get("providers") or {}


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
    provider = os.getenv(ENV_LOCAL_PROVIDER, "").strip() or os.getenv(ENV_AGENT_PROVIDER, "").strip() or "local"
    rewritten = rewrite_authority(base_url, allow_list)
    auth = _authority(rewritten)
    if auth is None:
        raise LocalLLMConfigError(f"The configured {ENV_LOCAL_BASE_URL} is not a valid URL; ignoring it.")
    if auth[0] != GATEWAY_HOST:
        shown = shown_authority(base_url)
        target = f"host {shown}" if shown else "host withheld: no unambiguous userinfo boundary"
        raise LocalLLMConfigError(
            f"The configured {ENV_LOCAL_BASE_URL} ({target}) is neither a loopback address nor in "
            f"{ENV_HOST_LOCAL_HOSTS}."
        )
    return {
        "providers": {
            provider: {
                "baseUrl": rewritten,
                "api": _SYNTHESISED_API_PROTOCOL,
                "apiKey": _SYNTHESISED_API_KEY,
                "models": [{"id": model_id} for model_id in model_ids],
            },
        },
    }


def build_container_config(host_config: dict | None, allow_list: Iterable[str] = ()) -> dict:
    if not _providers(host_config):
        return _synthesized_config(allow_list)

    config = copy.deepcopy(host_config)
    providers = _providers(config)

    kept: dict[str, dict] = {}
    dropped: list[str] = []
    for name, provider in providers.items():
        base_url = provider.get("baseUrl")
        if not base_url:
            dropped.append(name)
            continue
        rewritten = rewrite_authority(base_url, allow_list)
        authority = _authority(rewritten)
        is_gateway_reachable = authority is not None and authority[0] == GATEWAY_HOST
        if not is_gateway_reachable:
            dropped.append(name)
            continue
        if rewritten != base_url:
            logger.info(
                "Rewrote provider %r baseUrl host for sandbox reachability: %s -> %s",
                name,
                shown_authority(base_url) or "<withheld>",
                shown_authority(rewritten) or "<withheld>",
            )
        provider["baseUrl"] = rewritten
        if not provider.get("apiKey"):
            provider["apiKey"] = _SYNTHESISED_API_KEY
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
    provider: str
    model_id: str
    context_window: int
    max_tokens: int
    max_tokens_source: str
    reasoning: str
    reasoning_source: str

    def envelope_line(self) -> str:
        return (
            f"model={self.model_id} context={self.context_window} max_tokens={self.max_tokens} "
            f"source={self.max_tokens_source}"
        )

    def reasoning_line(self) -> str:
        return f"reasoning={self.reasoning} source={self.reasoning_source}"


def _strict_int(value: Any) -> int | None:
    return None if isinstance(value, bool) or not isinstance(value, int) else value


def _env_int(env: Mapping[str, str], name: str) -> int | None:
    raw = env.get(name, "").strip()
    if not raw:
        return None
    if len(raw.lstrip("+-")) > MAX_JSON_INT_DIGITS:
        raise LocalLLMConfigError(f"{name} must be a positive integer of at most {MAX_TOKEN_COUNT}.")
    try:
        value = int(raw)
    except ValueError:
        raise LocalLLMConfigError(f"{name} is not an integer; it must be a positive integer.") from None
    if not 0 < value <= MAX_TOKEN_COUNT:
        raise LocalLLMConfigError(f"{name} must be a positive integer of at most {MAX_TOKEN_COUNT}.")
    return value


def _resolve_reasoning(env: Mapping[str, str], entry: dict) -> tuple[str, str]:
    raw_env = env.get(ENV_REASONING_MODE, "").strip().lower()
    if raw_env and raw_env not in REASONING_MODES:
        raise LocalLLMConfigError(
            f"{ENV_REASONING_MODE} is not a supported reasoning mode; use one of {', '.join(REASONING_MODES)}."
        )
    host = entry.get("reasoning")
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
                f"Model {entry.get('id')!r} declares an unsupported reasoning mode; use one of "
                f"{', '.join(REASONING_MODES)} or set {ENV_REASONING_MODE}."
            )
        return mode, "host"
    return DEFAULT_REASONING_MODE, "default"


def _resolve_context_window(entry: dict[str, Any], fallback_window: int | None) -> int:
    if "contextWindow" in entry:
        return entry["contextWindow"]
    if fallback_window is not None:
        entry["contextWindow"] = fallback_window
        return fallback_window
    raise LocalLLMConfigError(
        f"Model {entry['id']!r} declares no contextWindow, so maxTokens cannot be checked or synthesised. "
        f"Add contextWindow to the model entry or set {ENV_LOCAL_CONTEXT_WINDOW}."
    )


def _resolve_max_tokens(entry: dict[str, Any], context_window: int, reserve: int) -> tuple[int, str]:
    model_id = entry["id"]
    ceiling = context_window - reserve
    if "maxTokens" in entry:
        max_tokens = entry["maxTokens"]
        source = "host"
    else:
        max_tokens = min(MAX_SYNTHESISED_OUTPUT_TOKENS, context_window // _SYNTHESISED_OUTPUT_WINDOW_DIVISOR)
        source = "synthesised"
    if max_tokens > ceiling:
        raise LocalLLMConfigError(
            f"Model {model_id!r} maxTokens={max_tokens} ({source}) exceeds contextWindow {context_window} minus the "
            f"{reserve}-token reserve ({ENV_CONTEXT_RESERVE_TOKENS}); lower maxTokens or the reserve."
        )
    if source == "synthesised":
        entry["maxTokens"] = max_tokens
        logger.info(
            "Model %r: maxTokens=%d synthesised as min(%d, contextWindow %d // 4): absent in host config",
            model_id,
            max_tokens,
            MAX_SYNTHESISED_OUTPUT_TOKENS,
            context_window,
        )
    return max_tokens, source


def apply_model_envelope(config: dict, env: Mapping[str, str] | None = None) -> list[ModelEnvelope]:
    env = os.environ if env is None else env
    providers = _providers(config, "generated models.json")
    configured_reserve = _env_int(env, ENV_CONTEXT_RESERVE_TOKENS)
    reserve = DEFAULT_CONTEXT_RESERVE_TOKENS if configured_reserve is None else configured_reserve
    fallback_window = _env_int(env, ENV_LOCAL_CONTEXT_WINDOW)
    envelopes: list[ModelEnvelope] = []
    for provider_name, provider in providers.items():
        for entry in provider.get("models") or []:
            model_id = entry["id"]
            window = _resolve_context_window(entry, fallback_window)
            max_tokens, source = _resolve_max_tokens(entry, window, reserve)
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
    check_text_settings(env)
    config = build_container_config(host_config, allow_list)
    return config, apply_model_envelope(config, env)


def _thinking_level(envelopes: list[ModelEnvelope]) -> str:
    if not envelopes:
        return DEFAULT_REASONING_MODE
    return min((envelope.reasoning for envelope in envelopes), key=REASONING_MODES.index)


def prepare_agent_dir(dest_dir: str, host_config: dict | None = None, allow_list: Iterable[str] = ()) -> dict:
    config, envelopes = build_envelope_config(host_config, allow_list)
    os.makedirs(dest_dir, mode=_AGENT_DIR_MODE, exist_ok=True)
    os.chmod(dest_dir, _AGENT_DIR_MODE)
    _write_shared_json(os.path.join(dest_dir, "models.json"), config)
    _write_shared_json(os.path.join(dest_dir, "settings.json"), {"defaultThinkingLevel": _thinking_level(envelopes)})
    return config


def _write_shared_json(path: str, data: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _SHARED_JSON_MODE)
    try:
        os.fchmod(fd, _SHARED_JSON_MODE)
    except OSError:
        os.chmod(path, _SHARED_JSON_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


def rewritten_local_hosts(config: dict) -> list[str]:
    hosts = []
    for provider in _providers(config, "generated models.json").values():
        base_url = provider.get("baseUrl")
        if not base_url:
            continue
        authority = _authority(base_url)
        if authority is None or authority[0] != GATEWAY_HOST:
            continue
        hosts.append(f"{GATEWAY_HOST}:{authority[1]}" if authority[1] else GATEWAY_HOST)
    return sorted(set(hosts))


def container_mount_args(dest_dir: str) -> list[str]:
    return ["-v", f"{dest_dir}:{CONTAINER_AGENT_DIR}:rw"]


def container_env(dest_dir: str) -> dict[str, str]:
    return {ENV_PI_AGENT_DIR: CONTAINER_AGENT_DIR}


HEALTH_PATH = "/health"
HEALTH_TIMEOUT_SECONDS = 2.0
HEALTH_SCHEMES = ("http", "https")
HEALTH_MAX_BYTES = 1 << 20
_CACHE_SECTION_KEYS = ("prefill_cache", "prefix_cache", "prompt_cache", "cache")
_CACHE_COUNT_KEYS = ("entries", "num_entries", "cached_entries", "cached_tokens", "tokens", "size")

Fetcher = Callable[[str, float], bytes | str]


@dataclass(frozen=True)
class GatewayHealth:
    url: str
    prefill_cache: str
    memory: dict[str, Any]
    max_output_tokens: int | None
    error: str | None = None
    max_prompt_tokens: int | None = None

    def cache_line(self) -> str:
        figures = " ".join(f"{key}={value}" for key, value in sorted(self.memory.items()))
        suffix = f" ({self.error})" if self.error else ""
        return f"prefill_cache={self.prefill_cache}{' ' + figures if figures else ''}{suffix}"


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: PLR0913
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused by health probe", headers, fp)


def _default_fetch(url: str, timeout: float) -> bytes:
    if urlsplit(url).scheme.lower() not in HEALTH_SCHEMES:
        raise ValueError(f"health probe only supports http and https, not {redact_url(url)!r}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RefuseRedirects())
    with opener.open(url, timeout=timeout) as response:
        body = response.read(HEALTH_MAX_BYTES + 1)
    if len(body) > HEALTH_MAX_BYTES:
        raise ValueError(f"health response exceeds {HEALTH_MAX_BYTES} bytes")
    return body


def health_url(base_url: str, allow_list: Iterable[str] = ()) -> str | None:
    try:
        scheme = urlsplit(base_url).scheme.lower()
    except ValueError:
        return None
    if scheme not in HEALTH_SCHEMES:
        return None
    authority = _authority(base_url)
    if authority is None or shown_authority(base_url) is None:
        return None
    if authority[0] == GATEWAY_HOST or rewrite_authority(base_url, allow_list) == base_url:
        return None
    parsed = urlsplit(base_url)
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
        if any(token in lowered for token in _MEMORY_KEY_TOKENS):
            figures[f"{prefix}{key}"] = value
    return figures


def _classify_cache_state(cache: dict[str, Any]) -> str:
    enabled = cache.get("enabled")
    counts = [
        cache[key]
        for key in _CACHE_COUNT_KEYS
        if isinstance(cache.get(key), int | float) and not isinstance(cache[key], bool)
    ]
    if enabled is False:
        return "off"
    if enabled is True or counts:
        return "warm" if any(count > 0 for count in counts) else "cold"
    return "unknown"


def parse_gateway_health(url: str, payload: Any) -> GatewayHealth:
    if not isinstance(payload, dict):
        return GatewayHealth(url, "unknown", {}, None, "unrecognised health response")
    cache = next((payload[key] for key in _CACHE_SECTION_KEYS if isinstance(payload.get(key), dict)), None)
    state = "unknown"
    memory = _numeric_figures(payload.get("memory"), "memory_")
    if cache is not None:
        memory.update(_numeric_figures(cache, "cache_"))
        state = _classify_cache_state(cache)
    defaults = payload.get("effective_defaults")
    defaults = defaults if isinstance(defaults, dict) else {}
    max_output = defaults.get("max_output_tokens")
    max_prompt = defaults.get("max_prompt_tokens", payload.get("max_prompt_tokens"))
    return GatewayHealth(url, state, memory, _strict_int(max_output), max_prompt_tokens=_strict_int(max_prompt))


def probe_gateway_health(
    base_url: str,
    fetch: Fetcher | None = None,
    allow_list: Iterable[str] = (),
    timeout: float = HEALTH_TIMEOUT_SECONDS,
) -> GatewayHealth | None:
    url = health_url(base_url, allow_list)
    if url is None:
        return None
    fetcher = fetch or _default_fetch
    try:
        raw = fetcher(url, timeout)
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        return GatewayHealth(url, "unknown", {}, None, f"health probe failed: {exc}")
    try:
        payload = parse_bounded_json(text)
    except MalformedJSONError as exc:
        return GatewayHealth(url, "unknown", {}, None, f"health probe failed: {exc}")
    return parse_gateway_health(url, payload)


def host_base_urls(host_config: dict | None, env: Mapping[str, str] | None = None) -> list[str]:
    env = os.environ if env is None else env
    urls: list[str] = []
    for provider in _providers(host_config).values():
        base_url = provider.get("baseUrl")
        if base_url:
            urls.append(base_url)
    configured = env.get(ENV_LOCAL_BASE_URL, "").strip()
    if configured:
        urls.append(configured)
    return list(dict.fromkeys(urls))


def _unparseable_base_url(url: str) -> bool:
    stripped = url.strip()
    try:
        parsed = urlsplit(stripped)
        host = parsed.hostname
        _port = parsed.port
    except ValueError:
        return True
    if "://" not in stripped:
        return True
    return parsed.scheme.lower() in HEALTH_SCHEMES and not host


def unparseable_base_url_sources(host_config: dict | None, env: Mapping[str, str] | None = None) -> list[str]:
    env = os.environ if env is None else env
    sources: list[str] = []
    for name, provider in _providers(host_config).items():
        base_url = provider.get("baseUrl")
        if base_url and _unparseable_base_url(base_url):
            sources.append(f"host models.json provider {name!r} baseUrl")
    configured = env.get(ENV_LOCAL_BASE_URL, "").strip()
    if configured and _unparseable_base_url(configured):
        sources.append(ENV_LOCAL_BASE_URL)
    return sources


def host_gateways(
    host_config: dict | None,
    config: dict,
    env: Mapping[str, str] | None = None,
    allow_list: Iterable[str] = (),
) -> list[tuple[str, frozenset[str]]]:
    env = os.environ if env is None else env
    allow_list = tuple(allow_list)
    host_providers = _providers(host_config)
    configured = env.get(ENV_LOCAL_BASE_URL, "").strip()
    served_by: dict[str, set[str]] = {}
    for name, provider in _providers(config, "generated models.json").items():
        container_url = provider.get("baseUrl")
        host_provider = host_providers.get(name)
        host_url = host_provider.get("baseUrl") if host_provider else None
        if host_url and rewrite_authority(host_url, allow_list) == container_url:
            source = host_url
        elif configured:
            source = configured
        else:
            continue
        served_by.setdefault(source, set()).add(name)

    gateways: dict[str, tuple[str, set[str]]] = {}
    for base_url in host_base_urls(host_config, env):
        url = health_url(base_url, allow_list)
        if url is None:
            continue
        _first, names = gateways.setdefault(url, (base_url, set()))
        names.update(served_by.get(base_url, ()))
    return [(base_url, frozenset(names)) for base_url, names in gateways.values()]
