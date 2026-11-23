"""Message helpers the launcher shares with the CLI: error redaction and encoding-safe printing."""

import os
import re
import sys
from typing import TextIO

from sandbox_executor.redaction import redact_agent_secrets, starts_with_url_authority, url_userinfo_end

_URL_ENV_NAME_RE = re.compile(r"(?:URL|URI|PROXY)$", re.IGNORECASE)
_URL_DELIMITER_SPLIT_RE = re.compile(r"[/?#]")


def _url_env_credential_parts() -> set[str]:
    """Return the userinfo credential of every URL-valued environment variable, whole and in pieces.

    A parse error can quote only the part of a password before its first raw `/`, `?` or `#`, which
    no shape-based redactor recognises, so each delimiter-separated piece of at least 4 characters
    is returned alongside the whole credential.
    """
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
            if len(piece) >= 4:
                parts.add(piece)
    return parts


def redact_cli_error(message: object) -> str:
    """Redact an exception message before a top-level CLI handler prints or logs it.

    Applies `redact_agent_secrets`, then masks the userinfo credential of every URL-valued
    environment variable (and each piece of it between raw URL delimiters), so a parse error that
    quotes part of a configured password, such as `urlparse`'s "Port could not be cast" message,
    cannot echo it.

    Args:
        message: The exception (or any object) whose string form is printed.

    Returns:
        The redacted message.
    """
    text = redact_agent_secrets(str(message))
    for part in sorted(_url_env_credential_parts(), key=len, reverse=True):
        text = text.replace(part, "*******")
    return text


def print_encodable(text: str, file: TextIO | None = None) -> None:
    """``print(text, file=file)`` that cannot raise ``UnicodeEncodeError``.

    Characters the stream's encoding cannot represent (a lone surrogate, a non-UTF-8 byte that ``os.environ`` decoded
    with ``surrogateescape``, or non-ASCII text on an ASCII terminal) are written backslash-escaped; text the stream
    can encode is written unchanged.
    """
    stream = sys.stdout if file is None else file
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        text = text.encode(encoding, "backslashreplace").decode(encoding)
    except LookupError:
        text = text.encode("ascii", "backslashreplace").decode("ascii")
    print(text, file=stream)
