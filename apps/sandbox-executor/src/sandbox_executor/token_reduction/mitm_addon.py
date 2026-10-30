"""MITM proxy interceptor & addon for LLM API request optimisation and response caching (Phase 3)."""

import atexit
import concurrent.futures
import contextlib
import copy
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

try:
    from mitmproxy import ctx, http
except ImportError:
    ctx = None
    http = None

from sandbox_executor.token_reduction.hybrid_cache import HybridCacheStore
from sandbox_executor.token_reduction.payload_cleaner import CleaningResult, JSONContextCleaner

logger = logging.getLogger(__name__)

WIRE_LOG_DIR = os.getenv("WIRE_LOG_DIR", "todo/mitm_wire_logs")
CACHE_DIR = os.getenv("CACHE_DIR", os.path.expanduser("~/.holon/cache"))

_MAX_SSE_BUFFER_BYTES = 50 * 1024 * 1024

_SECRET_HEADER_NAMES = {
    "authorization",
    "x-api-key",
    "api-key",
    "x-anthropic-api-key",
    "x-goog-api-key",
    "holon-agent-key",
    "proxy-authorization",
    "x-amz-security-token",
    "cookie",
    "set-cookie",
    "x-auth-token",
    "openai-api-key",
    "x-session-token",
}

_URL_QUERY_SECRET_PATTERN = re.compile(
    r'(?i)([?&](?:key|api_key|apiKey|api-key|apikey|token|access_token|auth_token|secret)=)[^&\s"\'`<>#]+'
)

_BODY_SECRET_PATTERNS = [
    # Anthropic
    re.compile(r"\bsk-ant-[a-zA-Z0-9_\-]{20,}\b"),
    # OpenAI
    re.compile(r"\bsk-(?:proj-|admin-|svcacct-)?[a-zA-Z0-9_\-]{20,}\b"),
    # Google Cloud / Vertex AI
    re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
    # GitHub tokens
    re.compile(r"\bgh[pousr]_[a-zA-Z0-9]{36}\b"),
    re.compile(r"\bgithub_pat_[a-zA-Z0-9_]{22,}\b"),
    # AWS access keys
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bASIA[0-9A-Z]{16}\b"),
    # AWS secret access key
    re.compile(
        r'(?i)\b(?:aws_secret_access_key|aws_secret_key|secret_access_key)\s*[:=]\s*["\']?(?:[A-Za-z0-9/+=]{40})["\']?'
    ),
    # Hugging Face
    re.compile(r"\bhf_[a-zA-Z0-9]{34,}\b"),
    # JWT Bearer tokens
    re.compile(r"\beyJ[a-zA-Z0-9_\-]{20,}\.[a-zA-Z0-9_\-]{20,}\.[a-zA-Z0-9_\-]{20,}\b"),
    # PEM & PGP Private Key Blocks (including PKCS#8)
    re.compile(
        r"-----BEGIN (?:[A-Z\s]+ )?PRIVATE KEY(?: BLOCK)?-----"
        r"[\s\S]*?-----END (?:[A-Z\s]+ )?PRIVATE KEY(?: BLOCK)?-----"
    ),
]

_SECRET_DICT_KEY_PATTERN = re.compile(
    r"(?i)^(?:password|passwd|api[-_]?key|apikey|api[-_]?token|auth[-_]?token|access[-_]?token|refresh[-_]?token|id[-_]?token|secret[-_]?key|secret|private[-_]?key|client[-_]?secret|session[-_]?token)$"
)


def scrub_string(text: str) -> str:
    """Scrubs sensitive credentials from string payloads or URLs."""
    if not isinstance(text, str) or not text:
        return text
    # 1. URL query parameters
    text = _URL_QUERY_SECRET_PATTERN.sub(r"\1[REDACTED]", text)
    # 2. Body secret patterns
    for pattern in _BODY_SECRET_PATTERNS:
        text = pattern.sub("[REDACTED_SECRET]", text)
    return text


def scrub_headers(headers: Any) -> dict[str, str]:
    """Scrubs sensitive credentials and normalizes header keys."""
    cleaned: dict[str, str] = {}
    if hasattr(headers, "items"):
        items = headers.items()
    elif isinstance(headers, (list, tuple)):
        items = headers
    elif isinstance(headers, dict):
        items = headers.items()
    else:
        return {}

    for k, v in items:
        k_str = str(k).lower()
        if (
            k_str in _SECRET_HEADER_NAMES
            or _SECRET_DICT_KEY_PATTERN.match(k_str)
            or "token" in k_str
            or "secret" in k_str
        ):
            cleaned[k_str] = "[REDACTED]"
        else:
            cleaned[k_str] = scrub_string(str(v))
    return cleaned


def scrub_payload(data: Any, max_depth: int = 50) -> Any:
    """Recursively scrubs sensitive keys and credentials in JSON-serialisable payloads."""
    if max_depth <= 0:
        return data
    if isinstance(data, dict):
        cleaned: dict[str, Any] = {}
        for k, v in data.items():
            k_str = str(k).lower()
            if _SECRET_DICT_KEY_PATTERN.match(k_str):
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = scrub_payload(v, max_depth=max_depth - 1)
        return cleaned
    elif isinstance(data, (list, tuple)):
        scrubbed_seq = [scrub_payload(elem, max_depth=max_depth - 1) for elem in data]
        return tuple(scrubbed_seq) if isinstance(data, tuple) else scrubbed_seq
    elif isinstance(data, str):
        return scrub_string(data)
    else:
        return data


def is_wire_logging_enabled() -> bool:
    """Checks whether wire logging is enabled via environment configuration."""
    flag = os.getenv("ENABLE_WIRE_LOGGING", "1").lower().strip()
    return flag not in ("0", "false", "no", "off")


def is_passive_monitoring() -> bool:
    """Checks whether proxy operates in passive monitoring mode (logging only, no reduction/caching)."""
    flag = os.getenv("HOLON_PASSIVE_MONITORING", "0").lower().strip()
    return flag in ("1", "true", "yes", "on")


_wire_log_executor: concurrent.futures.ThreadPoolExecutor | None = None
_pending_wire_log_futures: set[concurrent.futures.Future] = set()
_wire_log_lock = threading.Lock()


def _get_wire_log_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _wire_log_executor
    with _wire_log_lock:
        if _wire_log_executor is None or getattr(_wire_log_executor, "_shutdown", False):
            _wire_log_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="mitm_wire_logger"
            )
        return _wire_log_executor


def _write_transaction_sync(record: dict[str, Any], wire_log_dir: str) -> None:
    try:
        os.makedirs(wire_log_dir, exist_ok=True)
        turn_id = record.get("turn_id", 0)
        flow_id = record.get("flow_id", "flow")
        safe_turn_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(turn_id))
        safe_flow_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(flow_id))
        filename = f"turn_{safe_turn_id}_{safe_flow_id}.json"
        filepath = os.path.join(wire_log_dir, filename)

        tmp_filepath = f"{filepath}.{uuid.uuid4().hex[:6]}.tmp"
        try:
            with open(tmp_filepath, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2, ensure_ascii=False, default=str)
            os.replace(tmp_filepath, filepath)
        except Exception:
            if os.path.exists(tmp_filepath):
                with contextlib.suppress(OSError):
                    os.unlink(tmp_filepath)
            raise

        jsonl_path = os.path.join(wire_log_dir, "transactions.jsonl")
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as exc:
        logger.warning("Failed to persist wire log transaction: %s", exc)


def dump_wire_transaction(record: dict[str, Any], wire_log_dir: str | None = None) -> concurrent.futures.Future:
    """Asynchronously writes turn-scoped JSON dump and atomic JSONL entry."""
    target_dir = wire_log_dir or os.getenv("WIRE_LOG_DIR", WIRE_LOG_DIR)
    if not is_wire_logging_enabled() or not target_dir:
        dummy: concurrent.futures.Future = concurrent.futures.Future()
        dummy.set_result(None)
        return dummy

    future = _get_wire_log_executor().submit(_write_transaction_sync, record, target_dir)
    with _wire_log_lock:
        _pending_wire_log_futures.add(future)
    future.add_done_callback(_discard_pending_future)
    return future


def _discard_pending_future(future: concurrent.futures.Future) -> None:
    with _wire_log_lock:
        _pending_wire_log_futures.discard(future)


def flush_wire_logs(timeout: float = 5.0) -> None:
    """Blocks until all queued background wire log writes complete."""
    with _wire_log_lock:
        pending = list(_pending_wire_log_futures)
    if pending:
        concurrent.futures.wait(pending, timeout=timeout)


def shutdown_wire_logs(wait: bool = True, timeout: float = 5.0) -> None:
    """Blocks until pending wire log writes finish and cleanly shuts down the ThreadPoolExecutor."""
    global _wire_log_executor
    flush_wire_logs(timeout=timeout)
    with _wire_log_lock:
        if _wire_log_executor is not None and not getattr(_wire_log_executor, "_shutdown", False):
            try:
                _wire_log_executor.shutdown(wait=wait)
            except Exception as exc:
                logger.debug("Error shutting down wire log executor: %s", exc)
            _wire_log_executor = None


atexit.register(shutdown_wire_logs)


def get_header_case_insensitive(headers: Any, target_header: str) -> str | None:
    """Extracts header value matching target_header case-insensitively."""
    if headers:
        if hasattr(headers, "items"):
            target_lower = target_header.lower()
            for k, v in headers.items():
                if str(k).lower() == target_lower and v:
                    return str(v)
        elif hasattr(headers, "get"):
            val = headers.get(target_header)
            if val:
                return str(val)
    return None


def derive_turn_id(
    headers: Any,
    payload: dict[str, Any] | None,
    fallback_id: int = 1,
) -> int | str:
    """Derives conversation turn ID using 3-tier precedence:
    1. X-Holon-Turn-Id header
    2. Assistant completion count in message tree (+ 1)
    3. Sequential fallback counter
    """
    if headers:
        val = get_header_case_insensitive(headers, "x-holon-turn-id")
        if val is not None:
            try:
                return int(val)
            except ValueError:
                return val

    if isinstance(payload, dict):
        messages = payload.get("messages")
        if isinstance(messages, list):
            assistant_count = len([m for m in messages if isinstance(m, dict) and m.get("role") == "assistant"])
            return assistant_count + 1
        contents = payload.get("contents")
        if isinstance(contents, list):
            model_count = len([c for c in contents if isinstance(c, dict) and c.get("role") in ("model", "assistant")])
            return model_count + 1

    return fallback_id


class MITMProxyInterceptor:
    """Interceptor for LLM requests that performs context cleaning,
    deduplication, prompt cache optimisation, and local response caching.
    """

    def __init__(self, cache_dir: str | None = None, enable_caching: bool = True):
        self.cleaner = JSONContextCleaner()
        self.cache_dir = cache_dir or os.getenv("CACHE_DIR", os.path.expanduser("~/.holon/cache"))
        self.enable_caching = enable_caching
        self._cache_store: HybridCacheStore | None = None

    @property
    def cache_store(self) -> HybridCacheStore:
        if self._cache_store is None:
            self._cache_store = HybridCacheStore(cache_dir=self.cache_dir)
        return self._cache_store

    def detect_provider(self, url_or_path: str, payload: dict[str, Any] | None = None) -> str:  # noqa: PLR0911
        """Determines the provider protocol family (anthropic, openai, gemini)
        based on target URL, path, or payload structure.
        """
        url_lower = url_or_path.lower()
        parse_target = url_lower if "://" in url_lower else f"https://{url_lower}"
        parsed = urlparse(parse_target)
        hostname = parsed.hostname or url_lower

        if "anthropic" in url_lower or "v1/messages" in url_lower:
            return "anthropic"
        if (
            hostname == "generativelanguage.googleapis.com"
            or hostname.endswith(".generativelanguage.googleapis.com")
            or "gemini" in url_lower
            or "generatecontent" in url_lower
            or "streamgeneratecontent" in url_lower
        ):
            return "gemini"

        openai_providers = [
            "openai",
            "openrouter",
            "deepseek",
            "groq",
            "together",
            "mistral",
            "fireworks",
            "ollama",
            "vllm",
            "lmstudio",
            "chat/completions",
            "v1/completions",
        ]
        if any(p in url_lower for p in openai_providers):
            return "openai"

        if payload and isinstance(payload, dict):
            if "anthropic-version" in payload or "anthropic_version" in payload:
                return "anthropic"
            if "contents" in payload:
                return "gemini"
            if "messages" in payload or "prompt" in payload:
                return "openai"
            if "system" in payload:
                return "anthropic"

        return "unknown"

    def intercept_request_with_stats(
        self, endpoint: str, request_json: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any] | None, CleaningResult | None]:
        """Intercepts and optimises an outgoing JSON API request payload, returning per-request stats cleanly.

        Tuple Return Design:
            1. First Element (cleaned_request_json): The cleaned request body (with deduplicated tool
               outputs and cache control breakpoints).
            2. Second Element (cached_response_or_none): The pre-cached LLM response payload served from the
               local SQLite database (llm_cache.db), or None on a cache miss.
            3. Third Element (cleaner_result_or_none): The CleaningResult dataclass capturing cleaner metrics,
               or None if provider is unknown.

        Args:
            endpoint: The API endpoint URL or path.
            request_json: Incoming JSON body from agent.

        Returns:
            tuple[dict[str, Any], dict[str, Any] | None, CleaningResult | None]:
                (cleaned_request_json, cached_response_or_none, cleaner_result_or_none)
        """
        # Backward compatibility if intercept_request was overridden on class or monkeypatched on instance
        custom_intercept = getattr(self, "intercept_request", None)
        if custom_intercept is not None:
            func = getattr(custom_intercept, "__func__", custom_intercept)
            if func is not MITMProxyInterceptor.intercept_request:
                cleaned_req, cached_resp = self.intercept_request(endpoint, request_json)
                return cleaned_req, cached_resp, None

        provider = self.detect_provider(endpoint, request_json)
        if provider == "unknown":
            logger.warning("Unknown LLM provider for endpoint: %s. Bypassing payload cleaning.", endpoint)
            return request_json, None, None

        # Step 1: Clean and optimise request payload
        clean_res = self.cleaner.process_payload_with_stats(request_json, provider=provider)
        cleaned_request = clean_res.payload

        # Step 2: Check local cache if enabled (bypassed for streaming requests)
        is_streaming = (
            request_json.get("stream") is True
            or "streamgeneratecontent" in endpoint.lower()
            or "alt=sse" in endpoint.lower()
        )
        if self.enable_caching and not is_streaming:
            try:
                cached_response = self.cache_store.get(cleaned_request, provider=provider)
                if cached_response is not None:
                    logger.info("Serving response from local cache for endpoint %s", endpoint)
                    return cleaned_request, cached_response, clean_res
            except Exception:
                logger.exception("Cache lookup failed for endpoint %s; bypassing cache.", endpoint)

        return cleaned_request, None, clean_res

    def intercept_request(
        self, endpoint: str, request_json: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Intercepts and optimises an outgoing JSON API request payload (backward-compatible 2-tuple)."""
        cleaned_request, cached_response, _ = self.intercept_request_with_stats(endpoint, request_json)
        return cleaned_request, cached_response

    def intercept_response(
        self,
        endpoint: str,
        request_json: dict[str, Any],
        response_json: dict[str, Any],
        status_code: int = 200,
    ) -> None:
        """Records an LLM response into the local cache.

        Args:
            endpoint: The API endpoint URL or path.
            request_json: Cleaned request JSON payload.
            response_json: Received response JSON from provider API.
            status_code: HTTP status code of the response.
        """
        if not self.enable_caching:
            return

        if (
            request_json.get("stream") is True
            or "streamgeneratecontent" in endpoint.lower()
            or "alt=sse" in endpoint.lower()
        ):
            return

        if status_code != 200:
            logger.warning("Skipping cache put for non-200 HTTP status code (%d) on %s", status_code, endpoint)
            return

        if isinstance(response_json, dict) and ("error" in response_json or response_json.get("type") == "error"):
            logger.warning("Skipping cache put for API error response payload on %s", endpoint)
            return

        provider = self.detect_provider(endpoint, request_json)
        if provider != "unknown":
            try:
                self.cache_store.put(request_json, response_json, provider=provider)
            except Exception:
                logger.exception("Cache store put failed for endpoint %s.", endpoint)


def find_nested_key(data: Any, target_keys: tuple[str, ...] | str, max_depth: int = 10) -> Any:  # noqa: C901, PLR0912
    """Recursively searches nested dicts/lists to find the first occurrence of any key in target_keys.

    Evaluates target keys on the immediate node before recursively traversing child dictionaries
    or lists in depth-first order.
    """
    if not isinstance(data, (dict, list)):
        return None
    if max_depth <= 0 or not data:
        return None
    if isinstance(target_keys, str):
        target_keys = (target_keys,)

    if isinstance(data, dict):
        for key in target_keys:
            if key in data and data[key] is not None:
                return data[key]
        for val in data.values():
            if isinstance(val, (dict, list)):
                res = find_nested_key(val, target_keys, max_depth - 1)
                if res is not None:
                    return res
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                res = find_nested_key(item, target_keys, max_depth - 1)
                if res is not None:
                    return res
    return None


def log_telemetry(msg: str) -> None:
    """Logs telemetry message to mitmproxy console context if available, falling back to standard logger."""
    if ctx and hasattr(ctx, "log") and hasattr(ctx.log, "info"):
        try:
            ctx.log.info(msg)
            return
        except Exception:
            # mitmproxy context is unavailable or inactive in this execution context
            pass

    logger.info(msg)


def estimate_chars(data: Any, max_depth: int = 10) -> int:
    """Recursively estimates prompt/response characters from nested request/response structures.

    Returns 0 for unsupported primitive types or when max_depth is reached.
    """
    if max_depth <= 0:
        return 0
    if isinstance(data, str):
        return len(data)
    if isinstance(data, list):
        return sum(estimate_chars(x, max_depth - 1) for x in data)
    if isinstance(data, dict):
        total = 0
        found_target = False
        if "system" in data:
            total += estimate_chars(data["system"], max_depth - 1)
            found_target = True
        for key in ("messages", "message", "prompt", "contents", "parts", "choices", "candidates", "content", "text"):
            if key in data:
                total += estimate_chars(data[key], max_depth - 1)
                found_target = True
        if found_target:
            return total
        return sum(estimate_chars(v, max_depth - 1) for v in data.values())
    return 0


def safe_int(val: Any, default: int = 0) -> int:
    """Safely converts val to int, returning default on None or ValueError/TypeError."""
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


class TokenCounts(tuple):
    """3-tuple compatible with (input_tokens, output_tokens, cache_read_tokens) with extra metadata."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    reasoning_tokens: int

    def __new__(
        cls,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_creation_tokens: int = 0,
        reasoning_tokens: int = 0,
    ):
        inst = super().__new__(cls, (input_tokens, output_tokens, cache_read_tokens))
        inst.input_tokens = input_tokens
        inst.output_tokens = output_tokens
        inst.cache_read_tokens = cache_read_tokens
        inst.cache_creation_tokens = cache_creation_tokens
        inst.reasoning_tokens = reasoning_tokens
        return inst


def extract_sse_content(resp_text: str, provider: str) -> str:  # noqa: C901, PLR0912, PLR0915
    """Extracts aggregated completion text from SSE lines."""
    accumulated_content: list[str] = []
    lines = resp_text.splitlines()
    in_anthropic_tool = False
    in_openai_tool = False
    for line in lines:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        json_str = line[5:].strip()
        if not json_str or json_str == "[DONE]":
            continue
        try:
            chunk = json.loads(json_str)
        except Exception:
            continue
        if not isinstance(chunk, dict):
            continue

        if provider == "anthropic":
            content_block = chunk.get("content_block") or {}
            if content_block.get("type") == "tool_use":
                in_anthropic_tool = True
                name = content_block.get("name", "")
                if name:
                    accumulated_content.append(f"{name}(")

            delta = find_nested_key(chunk, ("delta",)) or {}
            text = delta.get("text", "")
            if text:
                accumulated_content.append(text)
            thinking = delta.get("thinking", "")
            if thinking:
                accumulated_content.append(thinking)
            partial_json = delta.get("partial_json", "")
            if partial_json:
                accumulated_content.append(partial_json)

            if in_anthropic_tool and chunk.get("type") == "content_block_stop":
                accumulated_content.append(")")
                in_anthropic_tool = False
        elif provider == "openai":
            choices = find_nested_key(chunk, ("choices",))
            if isinstance(choices, list):
                for choice in choices:
                    if isinstance(choice, dict):
                        delta = choice.get("delta")
                        if isinstance(delta, dict):
                            content = delta.get("content")
                            if isinstance(content, str):
                                if in_openai_tool:
                                    accumulated_content.append(")")
                                    in_openai_tool = False
                                accumulated_content.append(content)
                            tool_calls = delta.get("tool_calls")
                            if isinstance(tool_calls, list):
                                for tc in tool_calls:
                                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                                    fn_name = fn.get("name", "")
                                    args = fn.get("arguments", "")
                                    if fn_name:
                                        if in_openai_tool:
                                            accumulated_content.append(")")
                                        accumulated_content.append(f"{fn_name}(")
                                        in_openai_tool = True
                                    if args:
                                        accumulated_content.append(args)
                        if choice.get("finish_reason") in ("tool_calls", "stop") and in_openai_tool:
                            accumulated_content.append(")")
                            in_openai_tool = False
        elif provider == "gemini":
            candidates = find_nested_key(chunk, ("candidates",))
            if isinstance(candidates, list):
                for candidate in candidates:
                    if isinstance(candidate, dict):
                        content = candidate.get("content")
                        if isinstance(content, dict):
                            parts = content.get("parts")
                            if isinstance(parts, list):
                                for part in parts:
                                    if isinstance(part, dict):
                                        if "text" in part:
                                            accumulated_content.append(part["text"])
                                        func_call = part.get("functionCall")
                                        if isinstance(func_call, dict):
                                            accumulated_content.append(json.dumps(func_call))

    if in_openai_tool:
        accumulated_content.append(")")
    if in_anthropic_tool:
        accumulated_content.append(")")

    return "".join(accumulated_content)


def extract_detailed_token_counts(
    req_data: dict[str, Any], resp_data: dict[str, Any] | str, provider: str
) -> dict[str, int]:
    """Extracts normalized token counts and cache breakdown from request and response."""
    if isinstance(resp_data, str):
        counts = extract_sse_token_counts(resp_data, req_data, provider)
    else:
        counts = extract_token_counts(req_data, resp_data, provider)
    return {
        "input_tokens": counts[0],
        "output_tokens": counts[1],
        "cache_read_input_tokens": counts[2],
        "cache_creation_input_tokens": getattr(counts, "cache_creation_tokens", 0),
        "reasoning_tokens": getattr(counts, "reasoning_tokens", 0),
    }


def extract_sse_cache_read_tokens(resp_text: str) -> int:
    """Extracts cache_read_input_tokens from Anthropic SSE text if present.

    Delegates to extract_sse_token_counts to ensure single-pass SSE stream line parsing.
    """
    _, _, cache_read_tokens = extract_sse_token_counts(resp_text, {}, "anthropic")
    return cache_read_tokens


def extract_sse_token_counts(resp_text: str, req_data: dict[str, Any], provider: str) -> tuple[int, int, int]:  # noqa: C901, PLR0912, PLR0915
    """Extracts (input_tokens, output_tokens, cache_read_tokens) from SSE stream text.

    Args:
        resp_text: Full raw SSE text accumulated from stream chunks.
        req_data: Original request payload dictionary used for input token fallback estimation.
        provider: Detected model provider ("anthropic", "openai", "gemini").

    Returns:
        tuple[int, int, int]: A 3-tuple containing (input_tokens, output_tokens, cache_read_tokens).

    Note:
        When explicit usage metadata is absent from the SSE stream chunks, fallback token estimation
        derives counts from accumulated character counts using an integer division heuristic of
        1 token per 4 characters (i.e. chars // 4).
    """
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0

    # Track parsed metrics
    input_tokens_parsed = None
    output_tokens_parsed = None
    cache_read_tokens_parsed = None
    cache_creation_tokens_parsed = None
    reasoning_tokens_parsed = None
    accumulated_content_len = 0
    accumulated_thinking_len = 0

    # We split the stream by lines.
    lines = resp_text.splitlines()
    for line in lines:
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue

        # Strip "data:" prefix
        json_str = line[5:].strip()

        if not json_str or json_str == "[DONE]":
            continue

        try:
            chunk = json.loads(json_str)
        except Exception:
            continue

        if not isinstance(chunk, dict):
            continue

        if provider == "anthropic":
            # Anthropic SSE format
            event_type = find_nested_key(chunk, ("type",))
            if event_type == "message_start":
                message = find_nested_key(chunk, ("message",)) or {}
                usage = find_nested_key(message, ("usage",))
                if isinstance(usage, dict):
                    if "cache_read_input_tokens" in usage:
                        cache_read_tokens_parsed = safe_int(usage.get("cache_read_input_tokens"))
                    if "cache_creation_input_tokens" in usage:
                        cache_creation_tokens_parsed = safe_int(usage.get("cache_creation_input_tokens"))
                    if "input_tokens" in usage:
                        input_tokens_parsed = (
                            safe_int(usage.get("input_tokens"))
                            + safe_int(usage.get("cache_read_input_tokens"))
                            + safe_int(usage.get("cache_creation_input_tokens"))
                        )
            elif event_type == "message_delta":
                usage = find_nested_key(chunk, ("usage",))
                if isinstance(usage, dict):
                    if "output_tokens" in usage:
                        output_tokens_parsed = safe_int(usage.get("output_tokens"))
                    if "thinking_tokens" in usage:
                        reasoning_tokens_parsed = safe_int(usage.get("thinking_tokens"))
            elif event_type == "content_block_delta":
                delta = find_nested_key(chunk, ("delta",)) or {}
                text = delta.get("text", "")
                if text:
                    accumulated_content_len += len(text)
                partial_json = delta.get("partial_json", "")
                if partial_json:
                    accumulated_content_len += len(partial_json)
                thinking = delta.get("thinking", "")
                if thinking:
                    accumulated_content_len += len(thinking)
                    accumulated_thinking_len += len(thinking)

        elif provider == "openai":
            # OpenAI SSE format
            usage = find_nested_key(chunk, ("usage",))
            if usage and isinstance(usage, dict):
                if "prompt_tokens" in usage:
                    input_tokens_parsed = safe_int(usage.get("prompt_tokens"))
                if "completion_tokens" in usage:
                    output_tokens_parsed = safe_int(usage.get("completion_tokens"))
                prompt_tokens_details = usage.get("prompt_tokens_details")
                if isinstance(prompt_tokens_details, dict) and "cached_tokens" in prompt_tokens_details:
                    cache_read_tokens_parsed = safe_int(prompt_tokens_details.get("cached_tokens"))
                elif "cached_tokens" in usage:
                    cache_read_tokens_parsed = safe_int(usage.get("cached_tokens"))
                comp_details = usage.get("completion_tokens_details")
                if isinstance(comp_details, dict) and "reasoning_tokens" in comp_details:
                    reasoning_tokens_parsed = safe_int(comp_details.get("reasoning_tokens"))

            choices = find_nested_key(chunk, ("choices",))
            if choices and isinstance(choices, list):
                for choice in choices:
                    if isinstance(choice, dict):
                        delta = choice.get("delta")
                        if isinstance(delta, dict):
                            content = delta.get("content", "")
                            if isinstance(content, str) and content:
                                accumulated_content_len += len(content)
                            tool_calls = delta.get("tool_calls")
                            if isinstance(tool_calls, list):
                                for tc in tool_calls:
                                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                                    fn_name = fn.get("name", "")
                                    if isinstance(fn_name, str) and fn_name:
                                        accumulated_content_len += len(fn_name)
                                    args = fn.get("arguments", "")
                                    if isinstance(args, str) and args:
                                        accumulated_content_len += len(args)

        elif provider == "gemini":
            # Gemini / Cloud Code PA format
            usage = find_nested_key(chunk, ("usageMetadata", "usage"))
            if usage and isinstance(usage, dict):
                if "promptTokenCount" in usage:
                    input_tokens_parsed = safe_int(usage.get("promptTokenCount"))
                if "candidatesTokenCount" in usage:
                    output_tokens_parsed = safe_int(usage.get("candidatesTokenCount"))
                if "cachedContentTokenCount" in usage:
                    cache_read_tokens_parsed = safe_int(usage.get("cachedContentTokenCount"))
                if "reasoningTokenCount" in usage:
                    reasoning_tokens_parsed = safe_int(usage.get("reasoningTokenCount"))

            candidates = find_nested_key(chunk, ("candidates", "choices", "contents"))
            if candidates and isinstance(candidates, list):
                for candidate in candidates:
                    if isinstance(candidate, dict):
                        content = candidate.get("content")
                        if isinstance(content, dict):
                            parts = content.get("parts")
                            if isinstance(parts, list):
                                for part in parts:
                                    if isinstance(part, dict):
                                        text = part.get("text", "")
                                        if isinstance(text, str) and text:
                                            accumulated_content_len += len(text)
                                        func_call = part.get("functionCall")
                                        if isinstance(func_call, dict):
                                            fn_name = func_call.get("name", "")
                                            if isinstance(fn_name, str) and fn_name:
                                                accumulated_content_len += len(fn_name)
                                            fn_args = func_call.get("args")
                                            if isinstance(fn_args, dict):
                                                accumulated_content_len += len(json.dumps(fn_args))
                                            elif isinstance(fn_args, str) and fn_args:
                                                accumulated_content_len += len(fn_args)
                                    elif isinstance(part, str) and part:
                                        accumulated_content_len += len(part)
                        elif isinstance(content, str) and content:
                            accumulated_content_len += len(content)
                    elif isinstance(candidate, str) and candidate:
                        accumulated_content_len += len(candidate)

    # Fallback/estimate calculations
    if input_tokens_parsed is not None:
        input_tokens = input_tokens_parsed
    else:
        input_chars = estimate_chars(req_data)
        input_tokens = max(1, input_chars // 4) if input_chars > 0 else 0

    if output_tokens_parsed is not None:
        output_tokens = output_tokens_parsed
    else:
        output_tokens = max(1, accumulated_content_len // 4) if accumulated_content_len > 0 else 0

    if cache_read_tokens_parsed is not None:
        cache_read_tokens = cache_read_tokens_parsed

    reasoning_tokens = reasoning_tokens_parsed or (
        max(1, accumulated_thinking_len // 4) if accumulated_thinking_len > 0 else 0
    )

    return TokenCounts(
        input_tokens,
        output_tokens,
        cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens_parsed or 0,
        reasoning_tokens=reasoning_tokens,
    )


def extract_token_counts(  # noqa: C901, PLR0912, PLR0915
    req_data: dict[str, Any], resp_data: dict[str, Any] | str, provider: str
) -> tuple[int, int, int]:
    """Extracts (input_tokens, output_tokens, cache_read_tokens) from request/response data.

    Args:
        req_data: Request payload dictionary used for input token fallback estimation.
        resp_data: Response payload dictionary or raw SSE stream text string.
        provider: Detected model provider ("anthropic", "openai", "gemini").

    Returns:
        tuple[int, int, int]: A 3-tuple containing (input_tokens, output_tokens, cache_read_tokens).
            For Anthropic prompt caching, input_tokens reflects total input tokens (prompt tokens,
            cache read tokens, and cache creation tokens), while cache_read_tokens tracks ephemeral
            prompt cache read hits separately.

    Note:
        If provider usage metadata is missing or unparseable, fallback token counts are estimated
        using character count integer division (chars // 4 heuristic, ~4 characters per token).
    """
    if isinstance(resp_data, str):
        return extract_sse_token_counts(resp_data, req_data, provider)

    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_creation_tokens = 0
    reasoning_tokens = 0
    parsed = False

    try:
        if isinstance(resp_data, dict):
            if provider == "anthropic":
                usage = resp_data.get("usage")
                if isinstance(usage, dict) and "input_tokens" in usage and "output_tokens" in usage:
                    cache_read_tokens = safe_int(usage.get("cache_read_input_tokens"))
                    cache_creation_tokens = safe_int(usage.get("cache_creation_input_tokens"))
                    input_tokens = safe_int(usage.get("input_tokens")) + cache_read_tokens + cache_creation_tokens
                    output_tokens = safe_int(usage.get("output_tokens"))
                    reasoning_tokens = safe_int(usage.get("reasoning_tokens")) or safe_int(usage.get("thinking_tokens"))
                    parsed = True
            elif provider == "openai":
                usage = resp_data.get("usage")
                if isinstance(usage, dict) and "prompt_tokens" in usage and "completion_tokens" in usage:
                    input_tokens = safe_int(usage.get("prompt_tokens"))
                    output_tokens = safe_int(usage.get("completion_tokens"))
                    prompt_tokens_details = usage.get("prompt_tokens_details")
                    if isinstance(prompt_tokens_details, dict) and "cached_tokens" in prompt_tokens_details:
                        cache_read_tokens = safe_int(prompt_tokens_details.get("cached_tokens"))
                    elif "cached_tokens" in usage:
                        cache_read_tokens = safe_int(usage.get("cached_tokens"))
                    comp_details = usage.get("completion_tokens_details")
                    if isinstance(comp_details, dict) and "reasoning_tokens" in comp_details:
                        reasoning_tokens = safe_int(comp_details.get("reasoning_tokens"))
                    parsed = True
            elif provider == "gemini":
                usage = resp_data.get("usageMetadata") or resp_data.get("usage")
                if isinstance(usage, dict) and "promptTokenCount" in usage and "candidatesTokenCount" in usage:
                    input_tokens = safe_int(usage.get("promptTokenCount"))
                    output_tokens = safe_int(usage.get("candidatesTokenCount"))
                    if "cachedContentTokenCount" in usage:
                        cache_read_tokens = safe_int(usage.get("cachedContentTokenCount"))
                    if "reasoningTokenCount" in usage:
                        reasoning_tokens = safe_int(usage.get("reasoningTokenCount"))
                    parsed = True
    except Exception:
        logger.debug("Failed to parse token counts for provider %s from response; using estimation fallback", provider)

    if not parsed:
        logger.debug(
            "Provider usage dictionary missing or invalid for %s; estimating token counts from payload characters",
            provider,
        )
        input_chars = estimate_chars(req_data)
        output_chars = estimate_chars(resp_data)
        input_tokens = max(1, input_chars // 4) if input_chars > 0 else 0
        output_tokens = max(1, output_chars // 4) if output_chars > 0 else 0

    return TokenCounts(
        input_tokens,
        output_tokens,
        cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        reasoning_tokens=reasoning_tokens,
    )


# mitmproxy addon entrypoint compatible function
class MitmproxyAddon:
    """Addon for mitmproxy command line tool."""

    def __init__(self, cache_dir: str | None = None, wire_log_dir: str | None = None):
        self.interceptor = MITMProxyInterceptor(cache_dir=cache_dir)
        self.wire_log_dir = wire_log_dir or os.getenv("WIRE_LOG_DIR", WIRE_LOG_DIR)
        self.total_requests = 0
        self.cache_hits = 0

    def done(self) -> None:
        """Called when mitmproxy is shutting down to flush in-flight logs."""
        shutdown_wire_logs(wait=True, timeout=5.0)

    def _dump_flow_transaction(  # noqa: PLR0913
        self,
        flow: Any,
        resp_data: Any,
        status_code: int = 200,
        is_hit: bool = False,
        is_sse: bool = False,
        ttft: float | None = None,
        total_time: float | None = None,
    ) -> None:
        if not is_wire_logging_enabled():
            return
        try:
            url = getattr(flow.request, "pretty_url", "")
            flow_headers = getattr(flow.request, "headers", {})
            provider = getattr(flow, "provider", "unknown")

            # IDs and Role
            flow_id = getattr(flow, "id", None) or f"flow_{uuid.uuid4().hex[:8]}"
            agent_id = get_header_case_insensitive(flow_headers, "x-holon-agent-id") or os.getenv(
                "HOLON_AGENT_ID", "antigravity"
            )
            agent_role = get_header_case_insensitive(flow_headers, "x-holon-agent-role") or os.getenv(
                "HOLON_ROLE", "executor"
            )

            raw_req = getattr(flow, "raw_request_data", None) or getattr(flow, "req_data", None) or {}
            cleaned_req = getattr(flow, "req_data", None) or raw_req
            turn_id = derive_turn_id(flow_headers, raw_req, self.total_requests)

            clean_res = getattr(flow, "cleaner_result", None)
            try:
                raw_chars = len(json.dumps(raw_req)) if raw_req else 0
                cleaned_chars = len(json.dumps(cleaned_req)) if cleaned_req else 0
            except Exception:
                raw_chars = 0
                cleaned_chars = 0
            chars_saved = max(0, raw_chars - cleaned_chars)
            delta = {
                "raw_chars": raw_chars,
                "cleaned_chars": cleaned_chars,
                "chars_saved": clean_res.chars_saved if clean_res else chars_saved,
                "tool_outputs_omitted": clean_res.tool_outputs_omitted if clean_res else 0,
                "turns_summarized": clean_res.turns_summarized if clean_res else 0,
                "cache_control_injected": clean_res.cache_control_injected if clean_res else 0,
            }

            # Token metrics extraction
            if is_hit:
                detailed_tokens = extract_detailed_token_counts(raw_req, resp_data, provider)
                detailed_tokens["cache_read_input_tokens"] = detailed_tokens["input_tokens"]
            else:
                detailed_tokens = extract_detailed_token_counts(cleaned_req, resp_data, provider)

            # Content extraction
            if is_sse:
                content_val = extract_sse_content(resp_data, provider)
            elif isinstance(resp_data, dict):
                content_val = resp_data
            else:
                content_val = str(resp_data)

            record = {
                "turn_id": turn_id,
                "flow_id": flow_id,
                "agent_id": agent_id,
                "agent_role": agent_role,
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "provider": provider,
                "endpoint": scrub_string(url),
                "headers": scrub_headers(flow_headers),
                "raw_request": scrub_payload(raw_req),
                "cleaned_request": scrub_payload(cleaned_req),
                "delta": delta,
                "cache_action": "HIT" if is_hit else "MISS",
                "response": {
                    "status": status_code,
                    "usage": {
                        "input_tokens": detailed_tokens["input_tokens"],
                        "cache_creation_input_tokens": detailed_tokens["cache_creation_input_tokens"],
                        "cache_read_input_tokens": detailed_tokens["cache_read_input_tokens"],
                        "output_tokens": detailed_tokens["output_tokens"],
                        "reasoning_tokens": detailed_tokens["reasoning_tokens"],
                    },
                    "content": scrub_payload(content_val),
                },
                "timing": {
                    "ttft_ms": round(ttft * 1000, 2) if ttft is not None else (0.0 if is_hit else None),
                    "total_ms": round(total_time * 1000, 2) if total_time is not None else 0.0,
                },
            }

            dump_wire_transaction(record, self.wire_log_dir)
        except Exception as exc:
            logger.debug("Failed to record flow wire transaction: %s", exc)

    def request(self, flow: Any) -> None:  # noqa: C901, PLR0912, PLR0915
        """Mitmproxy request callback."""
        if getattr(flow, "request", None) is None:
            return
        url = getattr(flow.request, "pretty_url", "")
        data = None
        try:
            content = flow.request.get_text()
            if content:
                data = json.loads(content)
        except Exception as exc:
            # Request body is non-JSON or unparseable; proceed with URL-based provider detection
            logger.debug("Non-JSON or unparseable request body for endpoint %s: %s", url, exc)

        provider = self.interceptor.detect_provider(url, data)
        if provider != "unknown":
            flow.provider = provider
            flow.request_start_time = time.perf_counter()  # float: timestamp from time.perf_counter()
            self.total_requests += 1

            try:
                if data is not None:
                    flow.raw_request_data = copy.deepcopy(data)
                    if is_passive_monitoring():
                        # Passive mode: bypass cleaning and cache lookup, preserving raw payload for baseline logging
                        flow.cleaner_result = None
                        flow.req_data = data
                    else:
                        if hasattr(self.interceptor, "intercept_request_with_stats"):
                            cleaned_data, cached_resp, clean_res = self.interceptor.intercept_request_with_stats(
                                url, data
                            )
                        else:
                            cleaned_data, cached_resp = self.interceptor.intercept_request(url, data)
                            clean_res = None
                        flow.cleaner_result = clean_res
                        flow.req_data = cleaned_data
                        flow.request.set_text(json.dumps(cleaned_data))

                        if clean_res and (
                            clean_res.chars_saved > 0
                            or clean_res.tool_outputs_omitted > 0
                            or clean_res.turns_summarized > 0
                            or clean_res.cache_control_injected > 0
                        ):
                            cleaner_log = (
                                f"🧹 [CLEANER_METRICS] Chars Saved: {clean_res.chars_saved} | "
                                f"Tool Outputs Omitted: {clean_res.tool_outputs_omitted} | "
                                f"Turns Summarized: {clean_res.turns_summarized} | "
                                f"Cache Control Injected: {clean_res.cache_control_injected}"
                            )
                            log_telemetry(cleaner_log)

                        if cached_resp:
                            self.cache_hits += 1
                            flow.is_cached = True

                            headers = {"Content-Type": "application/json"}
                            response_cls = (
                                getattr(http, "Response", None)
                                or getattr(flow, "Response", None)
                                or globals().get("Response")
                            )
                            if response_cls and hasattr(response_cls, "make"):
                                flow.response = response_cls.make(200, json.dumps(cached_resp).encode("utf-8"), headers)

                            # Inject telemetry headers on cache hit
                            hit_rate = self.cache_hits / self.total_requests if self.total_requests > 0 else 0.0
                            if (
                                getattr(flow, "response", None) is not None
                                and hasattr(flow.response, "headers")
                                and flow.response.headers is not None
                                and hasattr(flow.response.headers, "__setitem__")
                            ):
                                flow.response.headers["X-Holon-Cache-Hit-Rate"] = f"{hit_rate:.4f}"
                                flow.response.headers["X-Holon-TTFT-Ms"] = "0.00"
                                flow.response.headers["X-Holon-Prefill-TPS"] = "0.0000"
                                flow.response.headers["X-Holon-Tail-Prefill-TPS"] = "0.0000"
                                flow.response.headers["X-Holon-Decode-Time-Sec"] = "0.000"
                                flow.response.headers["X-Holon-Output-TPS"] = "0.0000"
                                flow.response.headers["X-Holon-Total-Time-Ms"] = "0.00"

                            log_msg = (
                                f"📊 [TELEMETRY] Provider: {provider.upper()} | "
                                f"Cache: HIT (Hit Rate: {hit_rate * 100:.1f}%) | "
                                f"TTFT: 0.00ms | Prefill: 0.00 t/s | Output: 0.00 t/s | "
                                f"Total: 0.00ms"
                            )
                            log_telemetry(log_msg)

                            # Dump wire transaction for cache hit
                            self._dump_flow_transaction(flow, resp_data=cached_resp, status_code=200, is_hit=True)
            except json.JSONDecodeError as exc:
                logger.debug("Non-JSON request body for endpoint %s: %s", url, exc)
            except Exception:
                logger.exception("Mitmproxy request intercept error for endpoint: %s", url)

    def responseheaders(self, flow: Any) -> None:  # noqa: C901
        """Mitmproxy response headers callback."""
        if getattr(flow, "provider", "unknown") != "unknown":
            flow.response_headers_time = time.perf_counter()  # float: timestamp from time.perf_counter()
            flow.first_chunk_time = None
            response = getattr(flow, "response", None)
            if response is not None:
                headers = getattr(response, "headers", None)
                content_type = ""
                if headers:
                    if hasattr(headers, "get"):
                        content_type = headers.get("Content-Type") or headers.get("content-type") or ""
                    elif isinstance(headers, dict):
                        for k, v in headers.items():
                            if k.lower() == "content-type":
                                content_type = v
                                break
                if "text/event-stream" in content_type.lower():
                    flow.sse_chunks = []
                    flow.sse_bytes = 0
                    existing_stream = getattr(response, "stream", None)

                    def sse_stream_wrapper(chunk: bytes) -> bytes:
                        if chunk:
                            if getattr(flow, "first_chunk_time", None) is None:
                                flow.first_chunk_time = time.perf_counter()
                            flow.sse_bytes = getattr(flow, "sse_bytes", 0) + len(chunk)
                            if flow.sse_bytes <= _MAX_SSE_BUFFER_BYTES:
                                flow.sse_chunks.append(chunk)
                        if callable(existing_stream):
                            return existing_stream(chunk)
                        return chunk

                    response.stream = sse_stream_wrapper

    def response(self, flow: Any) -> None:  # noqa: C901, PLR0912, PLR0915
        """Mitmproxy response callback."""
        if getattr(flow, "request", None) is None or getattr(flow, "response", None) is None:
            return

        url = getattr(flow.request, "pretty_url", "")
        status_code = getattr(flow.response, "status_code", 200)

        provider = getattr(flow, "provider", "unknown")
        if provider == "unknown" or getattr(flow, "is_cached", False):
            return

        if status_code == 200:
            try:
                req_data = getattr(flow, "req_data", None)
                if req_data is None:
                    req_text = flow.request.get_text()
                    if req_text:
                        req_data = json.loads(req_text)

                sse_chunks = getattr(flow, "sse_chunks", None)
                is_sse = sse_chunks is not None

                if is_sse:
                    resp_data = b"".join(sse_chunks).decode("utf-8", errors="ignore")
                else:
                    resp_text = flow.response.get_text()
                    resp_data = json.loads(resp_text) if resp_text else None

                if req_data is not None and resp_data:
                    # Note: Response caching is explicitly bypassed for SSE streams (is_sse is True)
                    # because streaming responses cannot be served statically from cache, but telemetry
                    # metrics (token counts, TTFT, TPS) are still calculated and logged.
                    if not is_sse and not is_passive_monitoring():
                        self.interceptor.intercept_response(url, req_data, resp_data, status_code=status_code)

                    # Extract token counts and cache read tokens in a single pass
                    input_tokens, output_tokens, cache_read_tokens = extract_token_counts(req_data, resp_data, provider)

                    # Compute timing metrics
                    req_start = getattr(flow, "request_start_time", None)
                    resp_headers_time = getattr(flow, "response_headers_time", None)
                    first_chunk_time = getattr(flow, "first_chunk_time", None)

                    now = time.perf_counter()
                    if req_start is None:
                        req_start = now
                    if resp_headers_time is None:
                        resp_headers_time = now

                    ttfb = max(0.0, resp_headers_time - req_start)
                    ttft = max(0.0, first_chunk_time - req_start) if first_chunk_time is not None else ttfb

                    total_time = max(0.0, now - req_start)
                    generation_time = max(0.0, total_time - ttft)

                    uncached_input_tokens = max(0, input_tokens - cache_read_tokens)
                    prefill_tps = input_tokens / ttft if ttft > 0 else 0.0
                    tail_prefill_tps = uncached_input_tokens / ttft if ttft > 0 else 0.0
                    output_tps = output_tokens / generation_time if generation_time > 0 else 0.0
                    hit_rate = self.cache_hits / self.total_requests if self.total_requests > 0 else 0.0

                    # Inject telemetry headers on cache miss
                    if (
                        getattr(flow, "response", None) is not None
                        and hasattr(flow.response, "headers")
                        and flow.response.headers is not None
                        and hasattr(flow.response.headers, "__setitem__")
                    ):
                        flow.response.headers["X-Holon-Cache-Hit-Rate"] = f"{hit_rate:.4f}"
                        flow.response.headers["X-Holon-TTFT-Ms"] = f"{ttft * 1000:.2f}"
                        flow.response.headers["X-Holon-Prefill-TPS"] = f"{prefill_tps:.4f}"
                        flow.response.headers["X-Holon-Tail-Prefill-TPS"] = f"{tail_prefill_tps:.4f}"
                        flow.response.headers["X-Holon-Decode-Time-Sec"] = f"{generation_time:.3f}"
                        flow.response.headers["X-Holon-Output-TPS"] = f"{output_tps:.4f}"
                        flow.response.headers["X-Holon-Total-Time-Ms"] = f"{total_time * 1000:.2f}"

                    log_msg = (
                        f"📊 [TELEMETRY] Provider: {provider.upper()} | "
                        f"Cache: MISS (Hit Rate: {hit_rate * 100:.1f}%) | "
                        f"TTFT: {ttft * 1000:.1f}ms | "
                        f"Prefill: {prefill_tps:.2f} t/s ({input_tokens} tok) | "
                        f"Output: {output_tps:.2f} t/s ({output_tokens} tok in {generation_time:.2f}s) | "
                        f"Total: {total_time * 1000:.1f}ms"
                    )
                    log_telemetry(log_msg)

                    # Persist wire transaction dump
                    self._dump_flow_transaction(
                        flow,
                        resp_data=resp_data,
                        status_code=status_code,
                        is_hit=False,
                        is_sse=is_sse,
                        ttft=ttft,
                        total_time=total_time,
                    )
            except json.JSONDecodeError as exc:
                logger.debug("Non-JSON request/response body for endpoint %s: %s", url, exc)
            except Exception:
                logger.exception("Mitmproxy response intercept error for endpoint: %s", url)
        else:
            logger.warning("Skipping caching response with HTTP status code %d for %s", status_code, url)
            start_t = getattr(flow, "request_start_time", None) or time.perf_counter()
            elapsed_ms = (time.perf_counter() - start_t) * 1000
            log_msg = f"⚠️ [TELEMETRY] Provider: {provider.upper()} | Status: {status_code} | Total: {elapsed_ms:.1f}ms"
            log_telemetry(log_msg)

            try:
                if (
                    getattr(flow, "req_data", None) is None
                    and getattr(flow, "request", None)
                    and hasattr(flow.request, "get_text")
                ):
                    req_text = flow.request.get_text()
                    if req_text:
                        try:
                            flow.req_data = json.loads(req_text)
                        except Exception:
                            flow.req_data = req_text

                resp_data = None
                if getattr(flow, "response", None) and hasattr(flow.response, "get_text"):
                    resp_text = flow.response.get_text()
                    if resp_text:
                        try:
                            resp_data = json.loads(resp_text)
                        except Exception:
                            resp_data = resp_text

                self._dump_flow_transaction(
                    flow,
                    resp_data=resp_data,
                    status_code=status_code,
                    is_hit=False,
                    is_sse=False,
                    ttft=None,
                    total_time=(elapsed_ms / 1000.0),
                )
            except Exception as exc:
                logger.debug("Failed to record non-200 wire transaction: %s", exc)


addons = [MitmproxyAddon()]
