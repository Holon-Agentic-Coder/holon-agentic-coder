from __future__ import annotations

import secrets
import time


def build_review_id(agent: str) -> str:
    # why: the hex keeps ids unique within one second; older REV-<ts>-<agent> ids stay valid as nothing parses them.
    return f"REV-{int(time.time())}-{secrets.token_hex(4)}-{agent}"
