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
from typing import Any, NotRequired, TypedDict
from urllib.parse import urlsplit, urlunsplit

from sandbox_executor.redaction import starts_with_url_authority, trusted_url_authority, url_userinfo_end

logger = logging.getLogger(__name__)

# Opt-in switch. Never inferred from the presence of a base URL in a config file.
ENV_LOCAL_MODE = "HOLON_LOCAL_LLM"
# Base URL handed to the agent when there is no host models.json to copy from.
ENV_LOCAL_BASE_URL = "HOLON_LOCAL_BASE_URL"
# Provider name and model ids used to synthesize a config when no host config exists.
ENV_LOCAL_PROVIDER = "HOLON_LOCAL_PROVIDER"
ENV_LOCAL_MODELS = "HOLON_LOCAL_MODELS"
# Provider selected for the runner; also the synthesised provider's name when HOLON_LOCAL_PROVIDER is unset.
ENV_AGENT_PROVIDER = "HOLON_AGENT_PROVIDER"
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
# Every string setting local-LLM mode reads; each must be encodable as UTF-8 (:func:`check_text_settings`).
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


def is_utf8_text(text: str) -> bool:
    """True when ``text`` can be encoded as UTF-8: no lone surrogate (a JSON ``\\ud800`` escape) and no non-UTF-8
    byte decoded by ``surrogateescape`` (how ``os.environ`` and file names carry one). A terminal cannot print
    anything else."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def printable(text: str) -> str:
    """``text`` with every character UTF-8 cannot encode backslash-escaped, for messages that name a path."""
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _not_text_error(name: str) -> LocalLLMConfigError:
    # Name the variable only, never the value (#96).
    return LocalLLMConfigError(
        f"{name} is not valid UTF-8 text (a lone surrogate or a non-UTF-8 byte); value withheld."
    )


def check_text_settings(env: Mapping[str, str] | None = None) -> None:
    """Raise :class:`LocalLLMConfigError` naming the first :data:`TEXT_SETTINGS` variable that is not UTF-8 text."""
    env = os.environ if env is None else env
    for name in TEXT_SETTINGS:
        if not is_utf8_text(env.get(name, "")):
            raise _not_text_error(name)


def normalize_agent_id(agent_id: str) -> str:
    """Normalize agent identifier (e.g. 'pi-agent', 'agent-pi', 'Pi' -> 'pi')."""
    return agent_id.lower().replace("-agent", "").replace("agent-", "")


def local_llm_requested() -> bool:
    """True only when the operator explicitly opts into a host-local model."""
    return os.getenv(ENV_LOCAL_MODE, "").strip().lower() in _TRUTHY_ENV_VALUES


def host_local_allow_list() -> set[str]:
    """Authorities explicitly declared rewritable, lower-cased and empty entries dropped."""
    raw = os.getenv(ENV_HOST_LOCAL_HOSTS, "")
    if not is_utf8_text(raw):
        raise _not_text_error(ENV_HOST_LOCAL_HOSTS)
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


# "scheme://" or "//" kept in front of the userinfo marker; nothing before the userinfo boundary is ever shown.
_USERINFO_MARKER = "***@"
_WITHHELD_HOST = "***"
_URL_DELIMITERS_RE = re.compile(r"[/?#]")


def shown_authority(url: str) -> str | None:
    """The ``host[:port]`` a message may show for a configured base URL, or None when it must be withheld.

    The #96 message rule (:func:`sandbox_executor.redaction.trusted_url_authority`: the userinfo is removed with the
    shared last-``@`` rule and the remaining authority is validated) plus the #94 C11-N1 guard: when the removed
    userinfo itself holds a raw ``/``, ``?`` or ``#``, the text after it may be a path or query rather than the host,
    so no host is shown at all. Every local-LLM message about a base URL goes through here or :func:`redact_url`.
    """
    shown = trusted_url_authority(url)
    if shown is None:
        return None
    stripped = url.strip()
    rest = stripped[len(_lead(stripped)) :]
    at = url_userinfo_end(rest)
    if at is not None and _URL_DELIMITERS_RE.search(rest[:at]):
        return None
    return shown


def _lead(url: str) -> str:
    """The ``scheme://`` or ``//`` prefix of ``url`` (empty when schemeless), as the redaction module reads it."""
    if not starts_with_url_authority(url):
        return ""
    return url[: url.index("//") + 2]


def redact_url(url: str) -> str:
    """``url`` safe to print or log: no part of a credential, and a host only when it was validated.

    Built on the shared redaction helpers so there is one userinfo rule (``redaction.url_userinfo_end``, the last
    ``@`` before the next delimiter) and one host-trust rule (:func:`shown_authority`). Display contract:

    - When the host may be shown: ``scheme://[***@]host[:port]/path``. Everything up to the userinfo boundary is
      replaced by ``***@``; the path after the validated authority is kept; a query or fragment (``?key=``) is
      dropped. A credential-free URL keeps its original spelling up to the query.
    - Otherwise (unparseable value, an ``@`` left after the userinfo, a raw ``/``, ``?`` or ``#`` inside the userinfo,
      a bad port or a non-host authority): ``scheme://***`` (``//***`` or ``***`` without a scheme). Nothing from the
      value but its scheme is shown, and no believable wrong host is printed.
    """
    stripped = url.strip()
    lead = _lead(stripped)
    if shown_authority(stripped) is None:
        return lead + _WITHHELD_HOST
    rest = stripped[len(lead) :]
    at = url_userinfo_end(rest)
    marker, tail = (_USERINFO_MARKER, rest[at + 1 :]) if at is not None else ("", rest)
    # The tail holds no "@" (shown_authority guarantees it); a query or fragment may still carry "?key=".
    return lead + marker + re.split(r"[?#]", tail, maxsplit=1)[0]


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
        logger.warning("Ignoring a base URL with an invalid port or host (value withheld).")
        return None


def rewrite_authority(url: str, allow_list: Iterable[str] = ()) -> str:
    """Replace a loopback or allow-listed authority with the Docker gateway host.

    Scheme, port, path and query are preserved. Anything else -- a public host, or a LAN host that was not explicitly
    declared -- is returned untouched, so a remote inference server is never redirected onto the host.
    """
    allowed = {entry.lower() for entry in allow_list}
    authority = _authority(url)
    if authority is None:
        logger.warning("Ignoring an unparseable base URL during host-local rewrite (value withheld).")
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


# Bounds of the shared JSON reader (:func:`parse_bounded_json`). A models.json is a few KiB; these exist so that a
# corrupt or hostile file (or health response) is rejected with a clean error instead of an uncaught exception.
MODELS_JSON_MAX_BYTES = 1 << 20
MAX_JSON_DEPTH = 64
MAX_JSON_INT_DIGITS = 19
# Upper bound for every token count (contextWindow, maxTokens, the reserve and fallback window): the int32 range that
# pi and the inference servers use; anything larger is a typo or a misplaced value.
MAX_TOKEN_COUNT = 2**31 - 1


class MalformedJSONError(ValueError):
    """A document :func:`parse_bounded_json` refused; the message names the reason, never document content."""


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
    """True when ``value`` nests containers deeper than ``limit`` (iterative, so it cannot itself recurse too deep)."""
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
    """True when a string or object key anywhere in ``value`` is not UTF-8 text (iterative, like the depth check)."""
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
    """Parse ``raw`` as strict UTF-8 JSON with bounded integers, finite floats and at most ``MAX_JSON_DEPTH`` levels.

    Every failure (bad encoding, syntax, a string or key holding a lone surrogate escape such as ``\\ud800``, an
    oversized integer, NaN/Infinity, a float that overflows, nesting deep enough to exhaust the interpreter stack)
    raises :class:`MalformedJSONError` whose message carries the failure
    class only: ``UnicodeDecodeError`` and some parse errors would otherwise quote document bytes.
    """
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        data = json.loads(text, parse_int=_bounded_int, parse_float=_finite_float, parse_constant=_reject_constant)
    except MalformedJSONError:
        raise
    except UnicodeDecodeError:
        raise MalformedJSONError("not valid UTF-8") from None
    except json.JSONDecodeError as exc:
        # Line and column only: exc.msg is a fixed phrase, and the document itself is never quoted.
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
    """One validated ``models`` entry; only the fields Holon reads are typed, every other field is kept as is."""

    id: str
    contextWindow: NotRequired[int]
    maxTokens: NotRequired[int]
    reasoning: NotRequired[bool | str]


class ProviderEntry(TypedDict, total=False):
    """One validated provider: every field optional, and present ones have the types below."""

    baseUrl: str
    apiKey: str
    api: str
    models: list[ModelEntry]


class ModelsConfig(TypedDict, total=False):
    """A validated pi ``models.json`` (host or generated): ``providers`` maps provider names to entries."""

    providers: dict[str, ProviderEntry]


def _token_count_ok(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and 0 < value <= MAX_TOKEN_COUNT


def validate_models_config(data: Any, source: str = "models.json") -> ModelsConfig:  # noqa: C901, PLR0912
    """Check the shape of a pi ``models.json`` document and return it typed as :class:`ModelsConfig`.

    The single shape rule for every consumer (:func:`host_models_json`, :func:`build_container_config`,
    :func:`apply_model_envelope`, :func:`host_base_urls`, :func:`host_gateways`, the doctor): a JSON object whose
    optional ``providers`` is an object of provider objects; a provider's ``baseUrl``, ``apiKey`` and ``api`` are
    strings and its ``models`` a list of model objects; a model has a non-empty string ``id``, positive integer
    ``contextWindow`` and ``maxTokens`` up to ``MAX_TOKEN_COUNT``, and a boolean or string ``reasoning``. Provider
    names and every string field read must be UTF-8 text (:func:`is_utf8_text`). Fields Holon does not read are not
    checked and are preserved. Raises :class:`LocalLLMConfigError` naming ``source`` and the
    field (provider by name, model by id, or by position when it has no valid id), never a value.
    """
    if not isinstance(data, dict):
        raise LocalLLMConfigError(f"{source}: the top level must be a JSON object.")
    providers = data.get("providers")
    if providers is None:
        return data  # type: ignore[return-value]
    if not isinstance(providers, dict):
        raise LocalLLMConfigError(f"{source}: 'providers' must be an object of provider entries.")
    for name, provider in providers.items():
        if not is_utf8_text(name):
            raise LocalLLMConfigError(f"{source}: a provider name is not valid UTF-8 text.")
        where = f"{source}: provider {name!r}"
        if not isinstance(provider, dict):
            raise LocalLLMConfigError(f"{where} must be an object.")
        for key in ("baseUrl", "apiKey", "api"):
            if key in provider and not isinstance(provider[key], str):
                raise LocalLLMConfigError(f"{where} field {key!r} must be a string.")
            if key in provider and not is_utf8_text(provider[key]):
                raise LocalLLMConfigError(f"{where} field {key!r} is not valid UTF-8 text.")
        models = provider.get("models")
        if models is None:
            continue
        if not isinstance(models, list):
            raise LocalLLMConfigError(f"{where} field 'models' must be a list.")
        for index, entry in enumerate(models):
            at = f"{where} models[{index}]"
            if not isinstance(entry, dict):
                raise LocalLLMConfigError(f"{at} must be an object.")
            if not isinstance(entry.get("id"), str) or not entry["id"].strip():
                raise LocalLLMConfigError(f"{at} field 'id' must be a non-empty string.")
            if not is_utf8_text(entry["id"]):
                raise LocalLLMConfigError(f"{at} field 'id' is not valid UTF-8 text.")
            # Model ids are already shown in envelope lines and messages; naming one here is no new disclosure.
            at = f"{where} model {entry['id']!r}"
            for key in ("contextWindow", "maxTokens"):
                if key in entry and not _token_count_ok(entry[key]):
                    raise LocalLLMConfigError(
                        f"{at} field {key!r} must be a positive integer of at most {MAX_TOKEN_COUNT}."
                    )
            if "reasoning" in entry and not isinstance(entry["reasoning"], bool | str):
                raise LocalLLMConfigError(f"{at} field 'reasoning' must be a boolean or a string.")
            if isinstance(entry.get("reasoning"), str) and not is_utf8_text(entry["reasoning"]):
                raise LocalLLMConfigError(f"{at} field 'reasoning' is not valid UTF-8 text.")
    return data  # type: ignore[return-value]


def load_models_json(path: str) -> ModelsConfig:
    """Read, parse (:func:`parse_bounded_json`) and validate (:func:`validate_models_config`) one ``models.json``.

    Raises :class:`LocalLLMConfigError` naming the file and the reason (an unreadable file, an oversized one, malformed
    JSON or a wrong shape); never any of its content. The path is shown with non-UTF-8 bytes escaped
    (:func:`printable`).
    """
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
    """The validated host pi ``models.json`` (providers with their natural base URLs), or None when there is none.

    The first existing candidate (:func:`pi_agent_dirs`) is used. A present but invalid file raises
    :class:`LocalLLMConfigError` (:func:`load_models_json`) instead of being skipped: local mode never silently runs
    on a configuration other than the one the operator wrote.
    """
    for agent_dir in pi_agent_dirs():
        path = os.path.join(agent_dir, "models.json")
        if os.path.isfile(path):
            return load_models_json(path)
    return None


def _providers(config: Mapping[str, Any] | None, source: str = "models.json") -> dict[str, ProviderEntry]:
    """The validated ``providers`` map of ``config`` (empty when absent); raises on a malformed shape."""
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
        # Never echo an unparseable value: it has no reliable userinfo boundary to mask.
        raise LocalLLMConfigError(f"The configured {ENV_LOCAL_BASE_URL} is not a valid URL; ignoring it.")
    if auth[0] != GATEWAY_HOST:
        # Show only a host validated after the userinfo is removed; an ambiguous value is named, not echoed.
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
                "api": "openai-completions",
                # Local servers ignore the key; pi resolves credentials from this placeholder.
                "apiKey": "holon-local",
                "models": [{"id": model_id} for model_id in model_ids],
            },
        },
    }


def build_container_config(host_config: dict | None, allow_list: Iterable[str] = ()) -> dict:
    """Config to present to the container: every provider baseUrl rewritten, all other fields preserved.

    ``host_config`` is validated first (:func:`validate_models_config`); a malformed one raises
    :class:`LocalLLMConfigError`.
    """
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
        # Keep only endpoints the container can actually reach. Cloud providers are dropped on purpose: this file is
        # mounted into the sandbox, and a copied provider entry would carry its literal apiKey across that boundary
        # while offering an endpoint the local-mode run cannot use anyway.
        if authority is None or authority[0] != GATEWAY_HOST:
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
    if len(raw.lstrip("+-")) > MAX_JSON_INT_DIGITS:
        raise LocalLLMConfigError(f"{name} must be a positive integer of at most {MAX_TOKEN_COUNT}.")
    try:
        value = int(raw)
    except ValueError:
        # Name the variable only, never echo the value (#96): a misplaced credential must not reach any message.
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
                f"Model {entry.get('id')!r} declares an unsupported reasoning mode; use one of "
                f"{', '.join(REASONING_MODES)} or set {ENV_REASONING_MODE}."
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

    The config is validated first (:func:`validate_models_config`) and then modified in place. Raises
    :class:`LocalLLMConfigError` for any shape or value it cannot honour.
    """
    env = os.environ if env is None else env
    providers = _providers(config, "generated models.json")
    reserve = _env_int(env, ENV_CONTEXT_RESERVE_TOKENS)
    reserve = DEFAULT_CONTEXT_RESERVE_TOKENS if reserve is None else reserve
    fallback_window = _env_int(env, ENV_LOCAL_CONTEXT_WINDOW)
    envelopes: list[ModelEnvelope] = []
    for provider_name, provider in providers.items():
        for entry in provider.get("models") or []:
            model_id = entry["id"]
            if "contextWindow" in entry:
                window = entry["contextWindow"]
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
                max_tokens = entry["maxTokens"]
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
    """The container config together with its model envelope, without writing anything (used by ``holon doctor``).

    Every :data:`TEXT_SETTINGS` variable is checked first (:func:`check_text_settings`), so the doctor and the run path
    reject the same values with the same message.
    """
    check_text_settings(env)
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

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: PLR0913
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused by health probe", headers, fp)


def _default_fetch(url: str, timeout: float) -> bytes:
    # Proxies are bypassed explicitly: the target is the operator's own machine, and an HTTP(S)_PROXY from the
    # environment would otherwise receive the request. Redirects are refused so a loopback responder cannot send the
    # probe to a host that health_url() would not have allowed. The opener still carries urllib's file:// and ftp://
    # handlers, so the scheme is checked here as well as in health_url(), and the read is capped.
    if urlsplit(url).scheme.lower() not in HEALTH_SCHEMES:
        raise ValueError(f"health probe only supports http and https, not {redact_url(url)!r}")
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
    try:
        scheme = urlsplit(base_url).scheme.lower()
    except ValueError:
        # Unparseable (for example an unbalanced "["): not probeable. The doctor names its source, never the value.
        return None
    if scheme not in HEALTH_SCHEMES:
        return None
    authority = _authority(base_url)
    # The probe URL (and so the doctor's ``gateway=`` line) is shown, so only a validated authority is probed: an
    # ambiguous userinfo boundary could make urlsplit read part of a credential as the host or port.
    if authority is None or shown_authority(base_url) is None:
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
    except (OSError, ValueError, http.client.HTTPException) as exc:
        # HTTPException covers malformed responses (BadStatusLine, IncompleteRead), which are not OSErrors.
        return GatewayHealth(url, "unknown", {}, None, f"health probe failed: {exc}")
    try:
        # The same bounded reader as models.json: deep nesting or a huge number is "unknown", never an exception.
        payload = parse_bounded_json(text)
    except MalformedJSONError as exc:
        return GatewayHealth(url, "unknown", {}, None, f"health probe failed: {exc}")
    return parse_gateway_health(url, payload)


def host_base_urls(host_config: dict | None, env: Mapping[str, str] | None = None) -> list[str]:
    """Host-side (pre-rewrite) base URLs the generated config was built from, for probing from the host."""
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
    """True when ``url`` cannot be read as a ``scheme://host[:port]`` base URL at all.

    That is: ``urlsplit`` or the lazy ``port`` check raises (an unbalanced ``[``, a bad port), the value is schemeless
    (no ``://``), or an ``http``/``https`` URL has an empty host. A well-formed URL with another scheme (``file://``)
    is merely not probeable and is not reported.
    """
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
    """Where each unparseable host-side base URL came from, for the doctor; the value itself is never returned.

    Each entry names the setting only (``HOLON_LOCAL_BASE_URL`` or a host ``models.json`` provider's ``baseUrl``),
    per the #96 message rule. Such a URL has no server to probe, so :func:`host_gateways` skips it.
    """
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
    """One ``(host-side base URL, provider names)`` pair per distinct probeable server, in configuration order.

    Several providers can sit on one server (for example ``/v1`` and ``/anthropic`` on the same port), and the health
    URL is rebuilt from host and port only, so the server, not the base URL, is the unit to probe. ``config`` is the
    container config :func:`build_envelope_config` returned; each of its providers is attributed to the host base URL
    it was rewritten from (the synthesised provider to ``HOLON_LOCAL_BASE_URL``), so a model is only compared with the
    limits of the server that serves it. Endpoints that may not be probed are omitted.
    """
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
