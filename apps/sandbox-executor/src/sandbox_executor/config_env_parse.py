from __future__ import annotations

import re

from sandbox_executor.config_env_model import ParsedLayer

_UTF8_BOM = "\ufeff"
_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_INLINE_COMMENT_RE = re.compile(r"\s#")


def _unquote(raw: str) -> str:
    quote_char = raw[:1]
    if quote_char in ("'", '"'):
        closing_index = raw.find(quote_char, 1)
        if closing_index == -1:
            raise ValueError("unmatched quote")
        trailing_text = raw[closing_index + 1 :].strip()
        if trailing_text and not trailing_text.startswith("#"):
            raise ValueError("text after the closing quote")
        return raw[1:closing_index]
    inline_comment = _INLINE_COMMENT_RE.search(raw)
    return (raw[: inline_comment.start()] if inline_comment else raw).rstrip()


def _parse_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        raise ValueError("an export prefix is not supported")
    key, sep, raw = stripped.partition("=")
    key = key.strip()
    if not sep:
        raise ValueError("expected KEY=VALUE")
    if not _KEY_RE.match(key):
        raise ValueError("invalid key name")
    return key, _unquote(raw.strip())


def parse_env_text(text: str, origin: str) -> ParsedLayer:
    values: dict[str, str] = {}
    warnings: list[str] = []
    for number, line in enumerate(text.removeprefix(_UTF8_BOM).splitlines(), start=1):
        try:
            parsed = _parse_line(line)
        except ValueError as exc:
            warnings.append(f"Warning: {origin}:{number}: line ignored ({exc}).")
            continue
        if parsed is None:
            continue
        if parsed[0] in values:
            warnings.append(f"Warning: {origin}: duplicate key {parsed[0]}; the last value wins.")
        values[parsed[0]] = parsed[1]
    return ParsedLayer(values, warnings)
