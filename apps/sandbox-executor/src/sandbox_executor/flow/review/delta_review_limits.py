"""Operator limits of scoped delta review: the delta size that still allows it, and the per-file context cap.

Both are read on the host when the review mode is decided; unsafe or invalid values keep the safe default.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

logger = logging.getLogger(__name__)

ENV_SCOPED_REVIEW_MAX_LINES = "HOLON_SCOPED_REVIEW_MAX_LINES"
ENV_SCOPED_REVIEW_CONTEXT_BYTES = "HOLON_SCOPED_REVIEW_CONTEXT_BYTES"
DEFAULT_SCOPED_REVIEW_MAX_LINES = 400
DEFAULT_SCOPED_REVIEW_CONTEXT_BYTES = 40000
MIN_SCOPED_REVIEW_CONTEXT_BYTES = 1000


def _read_int(env: Mapping[str, str] | None, name: str, default: int, minimum: int) -> int:
    """The integer value of ``name``, or ``default`` with a warning naming the variable when it is below ``minimum``."""
    raw = (os.environ if env is None else env).get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        value = None
    if value is None or value < minimum:
        logger.warning("Ignoring %s: '%s' is not an integer of at least %d; keeping %d.", name, raw, minimum, default)
        return default
    return value


def resolve_scoped_review_max_lines(env: Mapping[str, str] | None = None) -> int:
    """The largest reviewable delta, in changed lines, a scoped pass may review; ``0`` disables scoped review."""
    return _read_int(env, ENV_SCOPED_REVIEW_MAX_LINES, DEFAULT_SCOPED_REVIEW_MAX_LINES, 0)


def resolve_scoped_review_context_bytes(env: Mapping[str, str] | None = None) -> int:
    """Bytes of one modified file's post-change content shown to a scoped pass; at least 1000."""
    return _read_int(
        env, ENV_SCOPED_REVIEW_CONTEXT_BYTES, DEFAULT_SCOPED_REVIEW_CONTEXT_BYTES, MIN_SCOPED_REVIEW_CONTEXT_BYTES
    )
