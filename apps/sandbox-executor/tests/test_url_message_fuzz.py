"""Shape-matrix fuzz: no message built from a URL-valued setting echoes any part of its credential.

The rule under test: a warning, error, exception, log line or `--json` field may name the variable
and a host that was validated after the userinfo was removed with the shared last-`@` rule
(`redaction.trusted_url_authority`), and nothing else taken from the value. Every entry point that
turns a configured URL into a message is driven through the full shape matrix below, and the
captured output (logs, stdout, stderr, exception text, and the top-level `redact_cli_error`
rendering) must not hold any 4-character fragment of the credential.

Credentials are built at runtime from letters that occur in no message (`FGJQVZ`) and digits, and
compared case-insensitively, so a 4-character check cannot match ordinary output. Failure messages
name the case id only, never the value.
"""

import contextlib
import io
import logging
import os
import secrets
from collections.abc import Callable, Iterator
from unittest.mock import patch

import pytest
from sandbox_executor import cli, local_llm

FRAGMENT_LEN = 4
_LETTERS = "FGJQVZ"
_DIGITS = "2347"

PREFIXES = {"schemeless": "", "scheme-relative": "//", "http": "http://", "https": "https://", "socks5": "socks5://"}
HOSTS = {
    "loopback-port": "localhost:8081",
    "ipv4": "127.0.0.1",
    "ipv6": "[::1]:8081",
    "empty-host": "",
    "port-out-of-range": "localhost:99999",
    "port-not-digits": "localhost:x1",
}
SUFFIXES = {"none": "", "path": "/v1", "double-slash-path": "//v1"}
USERINFO = {"user-password": "alice:{c}", "token-only": "{c}"}


def _letters(n: int = 12) -> str:
    return "".join(secrets.choice(_LETTERS) for _ in range(n))


def _digit_head(n: int) -> str:
    # The first digit keeps every head of 5 digits or fewer inside the valid port range.
    return secrets.choice("234") + "".join(secrets.choice(_DIGITS) for _ in range(n - 1))


def _credential_shapes() -> dict[str, Callable[[], str]]:
    shapes: dict[str, Callable[[], str]] = {
        "plain": _letters,
        "double-slash": lambda: _letters(6) + "//" + _letters(6),
        "percent-encoded": lambda: _letters(6) + "%2F" + _letters(6),
    }
    for n in range(1, 8):
        for name, delim in (("slash", "/"), ("query", "?"), ("fragment", "#"), ("colon", ":")):
            shapes[f"digits{n}-{name}"] = lambda n=n, d=delim: _digit_head(n) + d + _letters()
    return shapes


def matrix() -> Iterator[tuple[str, str, str]]:
    """Yield `(case_id, value, credential)` for every prefix x userinfo x credential x host x suffix."""
    for p_id, prefix in PREFIXES.items():
        for u_id, userinfo in USERINFO.items():
            for c_id, make in _credential_shapes().items():
                for h_id, host in HOSTS.items():
                    for s_id, suffix in SUFFIXES.items():
                        credential = make()
                        value = f"{prefix}{userinfo.format(c=credential)}@{host}{suffix}"
                        yield f"{p_id}/{u_id}/{c_id}/{h_id}/{s_id}", value, credential


MATRIX_SIZE = len(PREFIXES) * len(USERINFO) * len(_credential_shapes()) * len(HOSTS) * len(SUFFIXES)


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@contextlib.contextmanager
def _captured(env: dict[str, str]) -> Iterator[list[str]]:
    """Run with exactly `env`, collecting every log line, stdout and stderr into the yielded list."""
    out: list[str] = []
    handler = _Capture()
    root = logging.getLogger("sandbox_executor")
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    stdout, stderr = io.StringIO(), io.StringIO()
    try:
        with (
            patch.dict(os.environ, env, clear=True),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            yield out
            # The top-level CLI handlers render exceptions through redact_cli_error with this env set.
            out[:] = [cli.redact_cli_error(text) for text in out] + out
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    out.extend(handler.lines)
    out.extend((stdout.getvalue(), stderr.getvalue()))


def _proxy_detect(value: str) -> list[str]:
    with _captured({"HOLON_PROXY_URL": value}) as out:
        cli.detect_holon_coherence(token_reduce=False)
    return out


def _proxy_attach(value: str) -> list[str]:
    texts: list[str] = []
    for outcome in (False, OSError("probe refused")):

        def wait(*_args, outcome=outcome, **_kwargs):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with patch.object(cli, "_wait_for_proxy", wait), _captured({"HOLON_PROXY_URL": value}) as out:
            cli._attach_external_proxy()
        texts.extend(out)
    return texts


def _local_base_url(value: str) -> list[str]:
    env = {"HOLON_LOCAL_LLM": "1", "HOLON_LOCAL_BASE_URL": value, "HOLON_LOCAL_MODELS": "m1"}
    with _captured(env) as out:
        try:
            config = local_llm.build_container_config(None)
            out.append(repr(local_llm.rewritten_local_hosts(config)))
        except local_llm.LocalLLMConfigError as exc:
            out.append(str(exc))
    return out


def _host_config_base_url(value: str) -> list[str]:
    host_config = {"providers": {"vmlx": {"baseUrl": value, "models": [{"id": "m"}]}}}
    env = {"HOLON_LOCAL_LLM": "1", "HOLON_LOCAL_BASE_URL": "http://localhost:8081/v1", "HOLON_LOCAL_MODELS": "m1"}
    with _captured(env) as out:
        try:
            local_llm.build_container_config(host_config)
        except local_llm.LocalLLMConfigError as exc:
            out.append(str(exc))
    return out


def _remote_url(value: str) -> list[str]:
    with _captured({"HOLON_REPO_URL": value}) as out:
        try:
            cli.resolve_target_repo()
        except RuntimeError as exc:
            out.append(str(exc))
        try:
            cli.normalize_repo_remote(value)
        except ValueError as exc:
            out.append(str(exc))
    return out


ENTRY_POINTS: dict[str, Callable[[str], list[str]]] = {
    "detect_holon_coherence": _proxy_detect,
    "attach_external_proxy": _proxy_attach,
    "local_base_url": _local_base_url,
    "host_config_base_url": _host_config_base_url,
    "remote_url": _remote_url,
}


def _leaks(texts: list[str], credential: str) -> bool:
    haystack = "\n".join(texts).lower()
    needle = credential.lower()
    return any(needle[i : i + FRAGMENT_LEN] in haystack for i in range(len(needle) - FRAGMENT_LEN + 1))


@pytest.mark.parametrize("entry_point", sorted(ENTRY_POINTS))
def test_no_credential_fragment_in_any_message(entry_point, monkeypatch):
    """Every matrix case leaves no credential fragment in logs, stdout, stderr or exception text."""
    monkeypatch.setattr(cli.shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.socket, "create_connection", _refuse)
    monkeypatch.setattr(cli, "generate_root_ca", lambda: pytest.fail("must not generate a CA"))
    run = ENTRY_POINTS[entry_point]
    failures = [case_id for case_id, value, credential in matrix() if _leaks(run(value), credential)]
    assert not failures, f"{len(failures)}/{MATRIX_SIZE} cases leaked a credential fragment, e.g. {failures[:5]}"


def _refuse(*_args, **_kwargs):
    raise OSError("connection refused")


def test_matrix_covers_every_shape():
    """The matrix is the full cross product and its ids are unique."""
    ids = [case_id for case_id, _, _ in matrix()]
    assert len(ids) == MATRIX_SIZE == len(set(ids))


@pytest.mark.parametrize(
    ("form", "expected"),
    [
        ("http://alice:{c}@192.168.2.13:8081/v1", "192.168.2.13:8081"),
        ("https://{c}@proxy.corp:3128//v1", "proxy.corp:3128"),
        ("alice:{c}@proxy.corp:8080", "proxy.corp:8080"),
        ("socks5://alice:{c}@[::1]:1080", "[::1]:1080"),
        ("http://localhost:8081/v1", "localhost:8081"),
    ],
)
def test_unambiguous_host_is_still_shown(form, expected):
    """A value whose userinfo boundary is unambiguous keeps its validated host in messages."""
    from sandbox_executor.redaction import trusted_url_authority

    assert trusted_url_authority(form.format(c=_letters())) == expected


@pytest.mark.parametrize(
    "form",
    [
        "http://alice:{d}/{c}@localhost:8081/v1",
        "//{d}?{c}@proxy:3128",
        "http://localhost:8081/v1?q={c}@x",
        "http://alice:{c}@localhost:99999",
        "http://alice:{c}@local%68ost:8081",
        "http://alice:{c}@:8081",
        # urlsplit reads `alice` as the host here, so the two readings disagree.
        "http://alice:{c}//x@proxy.corp",
    ],
)
def test_ambiguous_or_invalid_host_is_withheld(form):
    """An `@` left after the userinfo, a bad port or a non-host authority shows nothing."""
    from sandbox_executor.redaction import trusted_url_authority

    assert trusted_url_authority(form.format(c=_letters(), d=_digit_head(4))) is None
