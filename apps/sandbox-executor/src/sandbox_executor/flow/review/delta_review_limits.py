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
    raw = (os.environ if env is None else env).get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        value = None
    # why: an invalid or unsafe operator value falls back to the safe default instead of failing the review.
    if value is None or value < minimum:
        logger.warning("Ignoring %s: '%s' is not an integer of at least %d; keeping %d.", name, raw, minimum, default)
        return default
    return value


def resolve_scoped_review_max_lines(env: Mapping[str, str] | None = None) -> int:
    return _read_int(env, ENV_SCOPED_REVIEW_MAX_LINES, DEFAULT_SCOPED_REVIEW_MAX_LINES, 0)


def resolve_scoped_review_context_bytes(env: Mapping[str, str] | None = None) -> int:
    return _read_int(
        env, ENV_SCOPED_REVIEW_CONTEXT_BYTES, DEFAULT_SCOPED_REVIEW_CONTEXT_BYTES, MIN_SCOPED_REVIEW_CONTEXT_BYTES
    )
