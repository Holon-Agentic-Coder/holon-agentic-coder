# why: the one canonical pattern set for echoes, git diagnostics and the ledger; a second, divergent set elsewhere
# why: is a regression, so a credential shape recognised in one place is recognised everywhere.
import ipaddress
import os
import re
from collections.abc import Sequence
from urllib.parse import urlsplit

REDACTION_MARKER: str = "*******"

_MAX_REDACT_INPUT_LEN: int = 100_000

# why: redact_text anchors on a key name, so a bare provider token (`token was: sk_live_...`) needs this sweep.
# why: a closed list of literal prefixes: a generic high-entropy rule would shred base64 and minified-JSON diagnostics.
_PROVIDER_TOKEN_RE = re.compile(
    r"(?:"
    r"(?:sk|pk)_(?:live|test)_[A-Za-z0-9_-]{8,}"
    r"|(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_-]{8,}"
    r"|github_pat_[A-Za-z0-9_-]{8,}"
    r"|sk-ant-[A-Za-z0-9_-]{8,}"
    r"|glpat-[A-Za-z0-9_-]{8,}"
    r"|xox[baprs]-[A-Za-z0-9_-]{8,}"
    r"|AIza[0-9A-Za-z_-]{30,}"
    r"|AKIA[0-9A-Z]{16}"
    r")"
)

_URL_DELIMITERS = "/?#"

_URL_AUTHORITY_START_RE = re.compile(r"(?<![A-Za-z0-9+.-])(?:([A-Za-z][A-Za-z0-9+.-]*):)?//")

# why: a `<NAME>_URL=` or `<NAME>_PROXY=` prefix is the only way a schemeless `user:pass@host` value reads as a URL.
_URL_VAR_PREFIX_RE = re.compile(r"\b(?:[A-Za-z0-9_]*_)?(?:URL|URI|PROXY)[ \t]*=[ \t]*[\"']?", re.IGNORECASE)

# why: length-bounded because a match can start at every `//` or `<NAME>_URL=` (an unbounded scan is quadratic).
# why: a quote does not end the token: `'` is an RFC 3986 sub-delim and may appear raw in a password.
_URL_TOKEN_RE = re.compile(r"\S{0,2048}")

_QUOTES = "'\""

_ASSIGNMENT_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", re.DOTALL)

_URL_VAR_NAME_RE = re.compile(r"(?:^|_)(?:URL|URI|PROXY)$", re.IGNORECASE)


# why: anchored on the start, so a `//` later in a path or password never makes a schemeless value an authority.
def starts_with_url_authority(value: str) -> bool:
    return _URL_AUTHORITY_START_RE.match(value) is not None


def _first_delimiter(text: str, start: int = 0) -> int:
    hits = [i for i in (text.find(c, start) for c in _URL_DELIMITERS) if i >= 0]
    return min(hits, default=len(text))


# why: a password may hold a raw `/`, `?` or `#`, which ends the authority early for urlsplit.
def url_userinfo_end(rest: str) -> int | None:
    authority = rest[: _first_delimiter(rest)]
    if "@" in authority:
        return authority.rindex("@")
    _, colon, tail = authority.partition(":")
    if not colon or not tail or tail.isdigit() or authority.startswith("["):
        return None
    at = rest.find("@", len(authority))
    if at < 0:
        return None
    while (nxt := rest.find("@", at + 1, _first_delimiter(rest, at + 1))) >= 0:
        at = nxt
    if _first_delimiter(rest, at + 1) == at + 1:
        return None
    return at


def _is_credential_userinfo(scheme: str | None, userinfo: str) -> bool:
    return (scheme or "").lower() in ("http", "https") or ":" in userinfo


# why: decided on the whole value, never a free-text scan, so no sub-delim in the password can hide the userinfo.
def url_value_userinfo(value: str) -> tuple[int, int] | None:
    stripped = value.strip()
    lead = len(value) - len(value.lstrip())
    url = stripped if starts_with_url_authority(stripped) else f"//{stripped}"
    m = _URL_AUTHORITY_START_RE.match(url)
    rest = url[m.end() :]
    at = url_userinfo_end(rest)
    if at is None:
        return None
    userinfo = rest[:at]
    if any(c.isspace() for c in userinfo) or not _is_credential_userinfo(m.group(1), userinfo):
        return None
    start = lead + m.end() - (len(url) - len(stripped))
    return start, start + at


# why: other names are left to the free-text scan, so an image reference `name:tag@sha256:...` stays readable.
def _mask_assignment_url(arg: str) -> str:
    m = _ASSIGNMENT_RE.fullmatch(arg)
    if m is None:
        return arg
    name, value = m.groups()
    quote = value[:1] if len(value) > 1 and value[:1] in _QUOTES and value.endswith(value[:1]) else ""
    inner = value[1:-1] if quote else value
    if not (starts_with_url_authority(inner.strip()) or _URL_VAR_NAME_RE.search(name)):
        return arg
    span = url_value_userinfo(inner)
    if span is None:
        return arg
    return f"{name}={quote}{inner[: span[0]]}{REDACTION_MARKER}{inner[span[1] :]}{quote}"


def _url_token(text: str, start: int, opener: str) -> str:
    token = _URL_TOKEN_RE.match(text, start).group(0)
    return token.partition(opener)[0] if opener and opener in _QUOTES else token


_HOST_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOST_NAME_RE = re.compile(rf"{_HOST_LABEL}(?:\.{_HOST_LABEL})*\.?")


# why: a message may show the authority only when no `@` is left and urlsplit agrees, so no credential boundary is
# why: ambiguous; otherwise the caller names the variable only.
def trusted_url_authority(value: str) -> str | None:  # noqa: PLR0911
    url = value.strip()
    url = url if starts_with_url_authority(url) else f"//{url}"
    head, sep, rest = url.partition("//")
    at = url_userinfo_end(rest)
    tail = rest[at + 1 :] if at is not None else rest
    if "@" in tail:
        return None
    authority = tail[: _first_delimiter(tail)].lower()
    if authority.startswith("["):
        host, bracket, port = authority[1:].partition("]")
        if not bracket or (port and not port.startswith(":")):
            return None
        port = port[1:]
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            return None
    else:
        host, _, port = authority.partition(":")
        if not host or len(host) > 253 or not _HOST_NAME_RE.fullmatch(host):
            return None
    if port and not (port.isdigit() and 0 < int(port) <= 65535):
        return None
    try:
        readings = {(parts.hostname, parts.port) for parts in (urlsplit(head + sep + tail), urlsplit(url))}
    except ValueError:
        return None
    if readings != {(host, int(port) if port else None)}:
        return None
    return authority


# why: a bare http(s) user name may be a token; elsewhere only a password is masked, so `ssh://git@host` stays readable.
def _mask_url_userinfo(text: str) -> str:
    if "@" not in text:
        return text
    for start_re in (_URL_AUTHORITY_START_RE, _URL_VAR_PREFIX_RE):
        out: list[str] = []
        pos = 0
        for m in start_re.finditer(text):
            if m.start() < pos:
                continue
            is_authority = start_re is _URL_AUTHORITY_START_RE
            opener = (text[m.start() - 1] if m.start() else "") if is_authority else m.group(0)[-1:]
            token = _url_token(text, m.end(), opener)
            if not is_authority and starts_with_url_authority(token):
                continue
            at = url_userinfo_end(token)
            if at is None:
                continue
            if not _is_credential_userinfo(m.group(1) if is_authority else None, token[:at]):
                continue
            out.extend((text[pos : m.end()], REDACTION_MARKER))
            pos = m.end() + at
        out.append(text[pos:])
        text = "".join(out)
    return text


SECRET_FLAGS = {
    "--password",
    "--passwd",
    "--auth",
}

_CREDENTIAL_ENV_NAMES: tuple[str, ...] = (
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "HOLON_AGENT_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "HOLON_AGENT_OAUTH_TOKEN",
)

# why: HOLON_AGENT_SETTINGS can carry CLAUDE_CODE_OAUTH_TOKEN in its JSON, so its value is swept, but it is not a
# why: secret name (a plain settings path is not secret); redact_command masks a `--settings` value on its own.
DEFAULT_ENV_LITERAL_NAMES: tuple[str, ...] = (*_CREDENTIAL_ENV_NAMES, "HOLON_AGENT_SETTINGS")

# why: the mitmweb dashboard password is forwarded under a name the key regex cannot recognise.
COMMAND_ECHO_ENV_NAMES: tuple[str, ...] = (*_CREDENTIAL_ENV_NAMES, "HOLON_MITM_WEB_PASSWORD")

TRUNCATION_MARKER = "..."


def redact_text(text: str) -> str:
    if not text:
        return text
    # why: ReDoS guard; the cap is well above any realistic log line, so only pathological input is truncated.
    if len(text) > _MAX_REDACT_INPUT_LEN:
        half_len = _MAX_REDACT_INPUT_LEN // 2
        # why: split on a nearby line boundary so the cut does not sever a secret into an unrecognisable fragment.
        head_end = text.rfind("\n", 0, half_len)
        head = text[:half_len] if head_end == -1 or (half_len - head_end) > 1000 else text[:head_end]
        tail_start = text.find("\n", len(text) - half_len)
        tail = (
            text[-half_len:]
            if tail_start == -1 or (tail_start - (len(text) - half_len)) > 1000
            else text[tail_start + 1 :]
        )
        text = head + "\n... (truncated) ...\n" + tail
    s = _mask_url_userinfo(text)
    s = re.sub(
        r"([?&](?:token|api_key|access_token|secret|password|auth|bearer|auth_code|code|client_secret|db_password|credential|credentials|key(?=[=_-])|pwd|passwd)[^=\s&]*=)[^\s&]+",
        r"\1*******",
        s,
        flags=re.IGNORECASE,
    )
    pattern = (
        r'(["\']?)('
        r"(?<![a-zA-Z0-9_-])key\b"
        r"|\b[a-zA-Z0-9_-]*(?:api|secret|private|public|signing|encryption|auth|access|session|consumer|client|master|token)[_-]key\b"
        r"|\b[a-zA-Z0-9_-]*(?:token|access_token|secret|password|passwd|pwd|client_secret|db_password|credential|credentials|auth|bearer|_pat|-pat|\bpat|auth_token|auth_code|apikey)\b"
        r")\1"
        r"(?:"
        r"([ \t]*(:[ \t]*|=)[ \t]*)"
        r'(?:(["\'])(.*?)\5|([^&\s\'"{}\[\],]+))'
        r"|"
        r"([ \t]*:[ \t]*\n\s*)"
        r'(?:(?:(["\'])([^"\'\r\n]*)\9|([^&\s\'"{}\[\],:]+))(?=[ \t]*(?:,|\n|$)))'
        r")"
    )

    def _replace_secret(match: re.Match) -> str:
        q_key = match.group(1) or ""
        key = match.group(2)
        if match.group(3) is not None:
            sep = match.group(3)
            q_val = match.group(5) or ""
        else:
            sep = match.group(8)
            q_val = match.group(9) or ""
        return f"{q_key}{key}{q_key}{sep}{q_val}*******{q_val}"

    s = re.sub(pattern, _replace_secret, s, flags=re.IGNORECASE)
    s = re.sub(r"(Bearer\s+)[^\s]+", r"\1*******", s, flags=re.IGNORECASE)
    return s


_FLAG_TOKEN_RE = re.compile(r"^-{1,2}[a-z0-9_-]+$")


def _is_secret_flag(flag: str) -> bool:
    flag_lowered = flag.lower()
    return flag_lowered in SECRET_FLAGS or (
        bool(_FLAG_TOKEN_RE.match(flag_lowered))
        and any(flag_lowered.endswith(sfx) for sfx in ("-token", "_token", "-secret", "_secret", "-key", "_key"))
    )


def redact_args(args: list[str]) -> list[str]:
    redacted = []
    mask_next = False
    for arg in args:
        s_arg = str(arg)
        if mask_next:
            is_secret = _is_secret_flag(s_arg)
            # why: deliberate trade-off: a flag-like secret after `--token` is not masked, so `--verbose` is not
            # why: over-masked when it follows `--token`.
            if is_secret or re.match(r"^-{1,2}[a-zA-Z0-9_-]+$", s_arg):
                mask_next = False
            else:
                redacted.append("*******")
                mask_next = False
                continue

        parts = s_arg.split("=", 1)
        is_secret_flag = _is_secret_flag(parts[0])
        if is_secret_flag:
            if len(parts) == 2:
                redacted.append(f"{parts[0]}=*******")
            else:
                redacted.append(s_arg)
                mask_next = True
        else:
            masked = redact_text(_mask_assignment_url(s_arg))
            redacted.append(masked)

    return redacted


# why: no length cap, so it is safe before the byte bound, where redact_text's cap would drop content instead.
def redact_env_literals(text: str, *, names: Sequence[str] = DEFAULT_ENV_LITERAL_NAMES) -> str:
    if not text:
        return text

    s = text
    for env_var in names:
        val = os.getenv(env_var)
        if val and len(val) >= 4:
            s = s.replace(val, "*******")
            if val.strip() != val and len(val.strip()) >= 4:
                s = s.replace(val.strip(), "*******")

    return s


def redact_agent_secrets(text: str) -> str:
    if not text:
        return text

    s = redact_text(text)
    s = _PROVIDER_TOKEN_RE.sub("*******", s)
    return redact_env_literals(s)


# why: skips the key-name regex and its input cap, so a patch or whole test log is swept without losing its middle.
def redact_credential_shapes(text: str) -> str:
    if not text:
        return text
    return redact_env_literals(_PROVIDER_TOKEN_RE.sub(REDACTION_MARKER, _mask_url_userinfo(text)))


# why: redact before truncating; a cut first would leave a token prefix too short for the provider-token sweep.
def redact_then_truncate(text: str | None, limit: int) -> str | None:
    if not text:
        return None
    clean = redact_agent_secrets(text)
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - len(TRUNCATION_MARKER))] + TRUNCATION_MARKER


def redact_url_value(value: str) -> str:
    if starts_with_url_authority(value):
        return redact_command([value])
    return redact_command([f"//{value}"])[2:]


def carries_url_password(value: str) -> bool:
    return url_value_userinfo(value) is not None or redact_url_value(value) != value


SETTINGS_REDACTION = "<redacted>"
_SETTINGS_FLAG = "--settings"
_SETTINGS_VALUE_RE = re.compile(r"""(--settings(?:=|\s+))(?:'[^']*'|"(?:\\.|[^"\\])*"|\S+)""")


# why: the settings JSON can carry credentials, so no part of a `--settings` value may reach an echo.
def _redact_settings_values(args: list[str]) -> list[str]:
    out: list[str] = []
    mask_next = False
    for arg in args:
        if mask_next:
            out.append(SETTINGS_REDACTION)
            mask_next = False
        elif arg == _SETTINGS_FLAG:
            out.append(arg)
            mask_next = True
        else:
            out.append(_SETTINGS_VALUE_RE.sub(lambda m: f"{m.group(1)}{SETTINGS_REDACTION}", arg))
    return out


def redact_command_args(argv: Sequence[str]) -> list[str]:
    return [
        redact_env_literals(_PROVIDER_TOKEN_RE.sub(REDACTION_MARKER, arg), names=COMMAND_ECHO_ENV_NAMES)
        for arg in redact_args(_redact_settings_values([str(arg) for arg in argv]))
    ]


def redact_command(argv: Sequence[str]) -> str:
    redacted = redact_command_args(argv)
    if len(redacted) <= 1:
        return redacted[0] if redacted else ""
    s = " ".join(redacted)
    # why: swept again so a value spanning two arguments (`password:` then its value) is caught.
    s = redact_text(s)
    s = _PROVIDER_TOKEN_RE.sub(REDACTION_MARKER, s)
    return redact_env_literals(s, names=COMMAND_ECHO_ENV_NAMES)


# why: reuses the canonical key-name regex in redact_text rather than copying it.
def is_secret_env_name(name: str) -> bool:
    if name in COMMAND_ECHO_ENV_NAMES:
        return True
    probe = f"{name}=x"
    return redact_text(probe) != probe
