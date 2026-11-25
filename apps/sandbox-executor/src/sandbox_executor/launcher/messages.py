import os
import re
import sys
from typing import TextIO

from sandbox_executor.redaction import redact_agent_secrets, starts_with_url_authority, url_userinfo_end

_URL_ENV_NAME_RE = re.compile(r"(?:URL|URI|PROXY)$", re.IGNORECASE)
_URL_DELIMITER_SPLIT_RE = re.compile(r"[/?#]")
_MIN_CREDENTIAL_PIECE_LENGTH = 4
_CREDENTIAL_MASK = "*******"


def _url_env_credential_parts() -> set[str]:
    parts: set[str] = set()
    for name, value in os.environ.items():
        if not value or not _URL_ENV_NAME_RE.search(name):
            continue
        url = value if starts_with_url_authority(value) else f"//{value}"
        rest = url.partition("//")[2]
        at = url_userinfo_end(rest)
        if at is None:
            continue
        userinfo = rest[:at]
        user, colon, password = userinfo.partition(":")
        credential = password if colon else user
        for piece in (userinfo, credential, *_URL_DELIMITER_SPLIT_RE.split(credential)):
            if len(piece) >= _MIN_CREDENTIAL_PIECE_LENGTH:
                parts.add(piece)
    return parts


def redact_cli_error(message: object) -> str:
    text = redact_agent_secrets(str(message))
    for part in sorted(_url_env_credential_parts(), key=len, reverse=True):
        text = text.replace(part, _CREDENTIAL_MASK)
    return text


def print_encodable(text: str, file: TextIO | None = None) -> None:
    stream = sys.stdout if file is None else file
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        text = text.encode(encoding, "backslashreplace").decode(encoding)
    except LookupError:
        text = text.encode("ascii", "backslashreplace").decode("ascii")
    print(text, file=stream)
