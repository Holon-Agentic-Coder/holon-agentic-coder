"""Shared credential redaction for the Holon CLI, the sandbox entrypoints and the ledger.

This is the one canonical pattern set. Every command echo, environment echo, git diagnostic and
ledger record is sanitised through the functions here, so a credential shape that is recognised in
one place is recognised everywhere. Adding a second, divergent set of patterns elsewhere is a
regression: extend this module instead.

Every redactor replaces a secret with the same fixed marker, `REDACTION_MARKER`.
"""

import os
import re
from collections.abc import Sequence

REDACTION_MARKER: str = "*******"

_MAX_REDACT_INPUT_LEN: int = 100_000

# Well-known provider credential prefixes, swept as whole tokens with no key-name anchor required.
# `redact_text` anchors on a key NAME, so a bare provider token (`token was: sk_live_...`) matches
# nothing there. Deliberately a closed list of literal prefixes: a generic high-entropy rule would
# shred legitimate base64 and minified-JSON diagnostics, which is a coverage regression.
_PROVIDER_TOKEN_RE = re.compile(
    r"(?:"
    r"(?:sk|pk)_(?:live|test)_[A-Za-z0-9_-]{8,}"
    r"|(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_-]{8,}"
    r"|github_pat_[A-Za-z0-9_-]{8,}"
    r"|glpat-[A-Za-z0-9_-]{8,}"
    r"|xox[baprs]-[A-Za-z0-9_-]{8,}"
    r"|AIza[0-9A-Za-z_-]{30,}"
    r"|AKIA[0-9A-Z]{16}"
    r")"
)

# Explicit list of flags whose next argument must be masked.
# Note: Suffixes like "-token", "_token", "-secret", "_secret", "-key", and "_key"
# are checked dynamically in _is_secret_flag.
# Any custom command line parameter that needs redaction must be added to this list.
SECRET_FLAGS = {
    "--password",
    "--passwd",
    "--auth",
}

# Environment variables whose literal values `redact_env_literals` sweeps by default (the ledger and
# agent-output path).
DEFAULT_ENV_LITERAL_NAMES: tuple[str, ...] = ("GITHUB_TOKEN", "GH_TOKEN", "HOLON_AGENT_KEY")

# Variable NAMES, not patterns, whose literal values are also swept from echoed commands. The mitmweb
# dashboard password is forwarded to the container under a name the key regex cannot recognise.
COMMAND_ECHO_ENV_NAMES: tuple[str, ...] = (*DEFAULT_ENV_LITERAL_NAMES, "HOLON_MITM_WEB_PASSWORD")


def redact_text(text: str) -> str:
    """Mask credentials and sensitive tokens in text with asterisks.

    Recognises standard secret keys (token, secret, password, pwd, passwd, api_key,
    credentials, client_secret, db_password, bare key), provider token prefixes,
    HTTP authorization headers, and URL query parameters while preserving non-secret
    diagnostic keys (such as sort_key and cache_key). Enforces line scoping on
    separators and URL anchors to prevent multiline bleeding or nested structure corruption.

    Args:
        text: The string content to redact.

    Returns:
        The sanitised string with matched secret values masked as '*******'.
    """
    if not text:
        return text
    # Guard against abnormally large inputs to prevent regex performance degradation on
    # pathological strings (ReDoS prevention). 100,000 chars is well above any realistic log
    # line length. Inputs exceeding this limit are truncated before applying redaction.
    if len(text) > _MAX_REDACT_INPUT_LEN:
        half_len = _MAX_REDACT_INPUT_LEN // 2
        # Align truncation split to line boundary if a newline exists close to the split point
        # to avoid severing tokens/secrets. Otherwise, truncate exactly at half_len.
        head_end = text.rfind("\n", 0, half_len)
        head = text[:half_len] if head_end == -1 or (half_len - head_end) > 1000 else text[:head_end]
        tail_start = text.find("\n", len(text) - half_len)
        tail = (
            text[-half_len:]
            if tail_start == -1 or (tail_start - (len(text) - half_len)) > 1000
            else text[tail_start + 1 :]
        )
        text = head + "\n... (truncated) ...\n" + tail
    s = re.sub(r"(https?://)[^@/]+@", r"\1*******@", text)
    # Redact sensitive URL query parameters including auth_code and code
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
        r"|\b[a-zA-Z0-9_-]*(?:token|access_token|secret|password|passwd|pwd|client_secret|db_password|credential|credentials|auth|bearer|_pat|-pat|\bpat|auth_token|auth_code)\b"
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


def _is_secret_flag(flag: str) -> bool:
    """Check whether a command-line flag identifies a sensitive credential.

    Args:
        flag: The command-line flag string (e.g. '--password', '--token').

    Returns:
        True if the flag indicates a credential parameter, False otherwise.
    """
    flag_lowered = flag.lower()
    return flag_lowered in SECRET_FLAGS or (
        flag_lowered.startswith("-")
        and any(flag_lowered.endswith(sfx) for sfx in ("-token", "_token", "-secret", "_secret", "-key", "_key"))
    )


def redact_args(args: list[str]) -> list[str]:
    """Redact sensitive credentials from command-line arguments.

    Args:
        args: List of command-line arguments.

    Returns:
        List of sanitised command-line arguments with secrets masked as '*******'.
    """
    redacted = []
    mask_next = False
    for arg in args:
        s_arg = str(arg)
        if mask_next:
            is_secret = _is_secret_flag(s_arg)
            # LIMITATION: If a secret value happens to look like a flag (starts with -),
            # it will NOT be masked. This is a deliberate trade-off to avoid over-masking
            # when a flag like --verbose follows --token in the args list.
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
                mask_next = True  # If trailing (no next arg), the dangling flag is safe — no secret to miss.
        else:
            masked = redact_text(s_arg)
            redacted.append(masked)

    return redacted


def redact_env_literals(text: str, *, names: Sequence[str] = DEFAULT_ENV_LITERAL_NAMES) -> str:
    """Replace the literal values of the environment credential variables, without any length cap.

    Safe on arbitrarily large streams, which is what makes it usable before the byte bound, where
    a value may still be intact. `redact_text`'s input cap would drop middle content instead of
    redacting it, so the regex pass is reserved for text that is already within the cap.

    Args:
        text: Input string potentially containing literal credential values.
        names: Environment variable names whose current values are swept. Defaults to
            `DEFAULT_ENV_LITERAL_NAMES`, which is what the ledger path uses.

    Returns:
        The input with every whole occurrence of the value of each variable in `names` (by default
        `GITHUB_TOKEN`, `GH_TOKEN` and `HOLON_AGENT_KEY`) replaced by asterisks.
    """
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
    """Redact secrets from agent output using redact_text, provider token shapes and literal env values.

    The provider-token sweep is anchored on the token itself rather than on a key name, so it still
    fires on `token was: sk_live_...`, where the key-name regex has nothing to match.

    Args:
        text: Input string potentially containing sensitive tokens or credentials.

    Returns:
        Sanitised string with sensitive tokens masked with asterisks.
    """
    if not text:
        return text

    s = redact_text(text)
    s = _PROVIDER_TOKEN_RE.sub("*******", s)
    return redact_env_literals(s)


def redact_command(argv: Sequence[str]) -> str:
    """Render a command line for display with every credential masked.

    Applies `redact_args` (secret flags, key-anchored values and URL userinfo), then the provider
    token sweep over the joined line, then the literal values of `COMMAND_ECHO_ENV_NAMES`. The URL
    rule only rewrites `scheme://<userinfo>@`, so the repository host, path and branch, and the
    overall command shape, stay readable.

    Args:
        argv: The command and its arguments, exactly as handed to `subprocess`.

    Returns:
        A single space-joined line that is safe to print or log. The input is not modified.
    """
    s = " ".join(redact_args([str(arg) for arg in argv]))
    s = _PROVIDER_TOKEN_RE.sub(REDACTION_MARKER, s)
    return redact_env_literals(s, names=COMMAND_ECHO_ENV_NAMES)


def is_secret_env_name(name: str) -> bool:
    """Decide whether an environment variable name holds a credential.

    Reuses the canonical key-name regex in `redact_text` rather than copying it: a name is secret
    when `redact_text` would mask a value assigned to it. Names in `COMMAND_ECHO_ENV_NAMES` are
    always secret, even where the regex does not recognise them.

    Args:
        name: The environment variable name, for example `GITHUB_TOKEN`.

    Returns:
        True if values of this variable must be kept out of process arguments and echoes.
    """
    if name in COMMAND_ECHO_ENV_NAMES:
        return True
    probe = f"{name}=x"
    return redact_text(probe) != probe
