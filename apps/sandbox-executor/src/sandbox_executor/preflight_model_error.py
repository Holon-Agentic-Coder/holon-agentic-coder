from __future__ import annotations

import re

MODEL_ERROR_RE = re.compile(
    r"invalid[\s_-]+model|unknown[\s_-]+model|unsupported[\s_-]+model|no\s+such\s+model"
    r"|model[\s_-]+not[\s_-]+found|model\b[^\n]{0,80}\b(?:not\s+found|does\s+not\s+exist)",
    re.IGNORECASE,
)


def is_model_error(text: str) -> bool:
    return MODEL_ERROR_RE.search(text) is not None
