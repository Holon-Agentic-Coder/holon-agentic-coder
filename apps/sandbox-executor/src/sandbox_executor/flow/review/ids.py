"""Review summary identifiers."""

from __future__ import annotations

import secrets
import time


def build_review_id(agent: str) -> str:
    """Build a review summary id ``REV-<ts>-<hex>-<agent>`` unique even within one second.

    Older ``REV-<ts>-<agent>`` ids remain valid; nothing parses the id shape.
    """
    return f"REV-{int(time.time())}-{secrets.token_hex(4)}-{agent}"
