from __future__ import annotations

import re
from typing import Any, ClassVar

from sandbox_executor.redaction import redact_credential_shapes

_OPENAI_API_KEY_PATTERN = re.compile(r"\b(sk-[a-zA-Z0-9_\-]{20,})\b")
_ANTHROPIC_API_KEY_PATTERN = re.compile(r"\b(sk-ant-[a-zA-Z0-9_\-]{20,})\b")
_GOOGLE_AI_API_KEY_PATTERN = re.compile(r"\b(AIza[0-9A-Za-z\-_]{30,40})\b")
_GITHUB_PAT_PATTERN = re.compile(r"\b(ghp_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]{82})\b")
_BEARER_TOKEN_PATTERN = re.compile(r"(?i)\b(Bearer\s+)[a-zA-Z0-9_\-\.]{15,}")
_PRIVATE_KEY_BLOCK_PATTERN = re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----")
_SENSITIVE_URL_PARAM_PATTERN = re.compile(
    r"([?&](?:token|api_key|access_token|secret|password|auth|bearer)[^=]*=)[^\s&]+",
    re.IGNORECASE,
)
_GENERIC_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r'(["\']?(?:api_key|token|access_token|secret|password)["\']?\s*[:=]\s*["\'])([^"\'\s]{8,})(["\'])',
    re.IGNORECASE,
)


class CredentialRedactor:
    _PATTERNS: ClassVar[list[tuple[re.Pattern, str]]] = [
        (_OPENAI_API_KEY_PATTERN, "[REDACTED_SECRET]"),
        (_ANTHROPIC_API_KEY_PATTERN, "[REDACTED_SECRET]"),
        (_GOOGLE_AI_API_KEY_PATTERN, "[REDACTED_SECRET]"),
        (_GITHUB_PAT_PATTERN, "[REDACTED_SECRET]"),
        (_BEARER_TOKEN_PATTERN, r"\1[REDACTED_SECRET]"),
        (_PRIVATE_KEY_BLOCK_PATTERN, "[REDACTED_PRIVATE_KEY]"),
        (_SENSITIVE_URL_PARAM_PATTERN, r"\1[REDACTED_SECRET]"),
        (_GENERIC_SECRET_ASSIGNMENT_PATTERN, r"\1[REDACTED_SECRET]\3"),
    ]

    @classmethod
    def redact_text(cls, text: str) -> str:
        if not text:
            return text
        result = text
        for pattern, replacement in cls._PATTERNS:
            result = pattern.sub(replacement, result)
        return redact_credential_shapes(result)

    @classmethod
    def redact_structure(cls, data: Any) -> Any:
        if isinstance(data, str):
            return cls.redact_text(data)
        if isinstance(data, dict):
            return {cls.redact_text(str(k)): cls.redact_structure(v) for k, v in data.items()}
        if isinstance(data, list):
            return [cls.redact_structure(item) for item in data]
        return data
