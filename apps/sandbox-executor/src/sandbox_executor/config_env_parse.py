"""The strict ``KEY=VALUE`` parser of a layered-configuration file (see ``config_env``).

Comments, blank lines and optional single or double quotes are understood; quoted text is taken literally, an
unquoted value ends at an inline `` #`` comment. A shell ``export`` prefix is not part of the subset and is rejected.
Nothing is expanded and nothing is executed. A malformed line is skipped with a warning naming the file and line
number, never the line text.
"""

from __future__ import annotations

import re

from sandbox_executor.config_env_model import ParsedLayer

_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_INLINE_COMMENT_RE = re.compile(r"\s#")


def _unquote(raw: str) -> str:
    if raw[:1] in ("'", '"'):
        end = raw.find(raw[0], 1)
        if end == -1:
            raise ValueError("unmatched quote")
        rest = raw[end + 1 :].strip()
        if rest and not rest.startswith("#"):
            raise ValueError("text after the closing quote")
        return raw[1:end]
    comment = _INLINE_COMMENT_RE.search(raw)
    return (raw[: comment.start()] if comment else raw).rstrip()


def _parse_line(line: str) -> tuple[str, str] | None:
    """Return ``(key, value)``, or None for a blank or comment line; ValueError carries a value-free reason."""
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
    """Parse a layer file's text; malformed lines are skipped with a warning naming ``origin`` and the line number."""
    values: dict[str, str] = {}
    warnings: list[str] = []
    for number, line in enumerate(text.removeprefix("﻿").splitlines(), start=1):
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
