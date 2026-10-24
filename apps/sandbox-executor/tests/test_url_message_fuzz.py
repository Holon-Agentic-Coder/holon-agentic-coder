"""Shape-matrix fuzz: no message built from a URL-valued setting echoes any part of its credential.

The rule under test: a warning, error, exception, log line or `--json` field may name the variable
and a host that was validated after the userinfo was removed with the shared last-`@` rule
(`redaction.trusted_url_authority`), and nothing else taken from the value. Every entry point that
turns a configured URL into a message is driven through the full shape matrix below, and the
captured output (logs, stdout, stderr, exception text, and the top-level `redact_cli_error`
rendering) must not hold any 4-character fragment of the credential. The #94 sites are driven too:
`holon doctor` (environment section, text and `--json`), the gateway probe (probe URL, cache line,
fetch errors, `redact_url`) and the model envelope lines.

Credentials are built at runtime from letters that occur in no message (`FGJQVZ`) and digits, and
compared case-insensitively, so a 4-character check cannot match ordinary output. Failure messages
name the case id only, never the value.
"""

import argparse
import contextlib
import io
import json
import logging
import os
import secrets
import urllib.error
from collections.abc import Callable, Iterator
from unittest.mock import patch
from urllib.parse import urlsplit

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


# A health response that drives every #94 doctor line: the cache state, memory figures, and both envelope warnings.
_HEALTH_TIGHT = {
    "prefill_cache": {"enabled": True, "entries": 1, "memory_bytes": 5},
    "memory": {"used_bytes": 1},
    "effective_defaults": {"max_output_tokens": 1, "max_prompt_tokens": 1},
}


def _host_config(value: str) -> dict:
    return {"providers": {"vmlx": {"baseUrl": value, "models": [{"id": "m", "contextWindow": 65536}]}}}


def _fetchers(seen: list[str]) -> tuple[Callable[[str, float], bytes], ...]:
    """A responding and a failing fetcher; both record the probe URL they were handed, which is shown too."""

    def ok(url: str, timeout: float) -> bytes:
        seen.append(url)
        return json.dumps(_HEALTH_TIGHT).encode()

    def fail(url: str, timeout: float) -> bytes:
        seen.append(url)
        raise OSError(f"connection refused by {url}")

    return ok, fail


def _doctor(value: str) -> list[str]:
    """#94 `holon doctor`: the environment section, then the unified report as text and as `--json`."""
    from sandbox_executor.preflight import PreflightResult, PreflightStatus

    probe = PreflightResult(
        status=PreflightStatus.OK, agent="pi-agent", model="m", image="img", exit_code=0, output_excerpt="", hint=""
    )
    env = {"HOLON_LOCAL_LLM": "1", "HOLON_LOCAL_BASE_URL": value, "HOLON_LOCAL_MODELS": "m1"}
    texts: list[str] = []
    for host_config in (None, _host_config(value)):
        for fetch in _fetchers(texts):
            with (
                patch.object(local_llm, "host_models_json", return_value=host_config),
                patch.object(cli, "run_preflight_probe", return_value=probe),
                _captured(env) as out,
            ):
                cli.report_budgets_and_envelope(fetch=fetch)
                for as_json in (False, True):
                    cli.run_doctor(argparse.Namespace(agent=None, model=None, json=as_json, timeout=None), fetch=fetch)
            texts.extend(out)
    return texts


class _RefusingOpener:
    def open(self, url: str, timeout: float):
        raise urllib.error.URLError(f"refused {url}")


def _gateway_probe(value: str) -> list[str]:
    """#94 gateway probe: the probe URL, its cache line and error (injected and default fetchers), `redact_url`."""
    texts: list[str] = []
    with (
        patch.object(local_llm.urllib.request, "build_opener", lambda *_handlers: _RefusingOpener()),
        _captured({}) as out,
    ):
        for fetch in (*_fetchers(texts), None):
            health = local_llm.probe_gateway_health(value, fetch=fetch)
            if health is not None:
                out.extend((health.url, health.cache_line(), repr(health)))
        out.extend((repr(local_llm.health_url(value)), local_llm.redact_url(value)))
        try:
            scheme = urlsplit(value).scheme.lower()
        except ValueError:
            scheme = ""
        if scheme not in local_llm.HEALTH_SCHEMES:
            try:
                local_llm._default_fetch(value, 0.01)
            except (OSError, ValueError) as exc:
                out.append(str(exc))
    texts.extend(out)
    return texts


def _envelope(value: str) -> list[str]:
    """#94 model envelope: the per-model envelope and reasoning lines and any config error, host and synthesised."""
    env = {"HOLON_LOCAL_LLM": "1", "HOLON_LOCAL_BASE_URL": value, "HOLON_LOCAL_MODELS": "m1"}
    texts: list[str] = []
    for host_config in (None, _host_config(value)):
        with _captured(env) as out:
            try:
                _config, envelopes = local_llm.build_envelope_config(host_config, (), env)
                for envelope in envelopes:
                    out.extend((envelope.envelope_line(), envelope.reasoning_line()))
            except local_llm.LocalLLMConfigError as exc:
                out.append(str(exc))
        texts.extend(out)
    return texts


ENTRY_POINTS: dict[str, Callable[[str], list[str]]] = {
    "detect_holon_coherence": _proxy_detect,
    "attach_external_proxy": _proxy_attach,
    "local_base_url": _local_base_url,
    "host_config_base_url": _host_config_base_url,
    "remote_url": _remote_url,
    "doctor": _doctor,
    "gateway_probe": _gateway_probe,
    "envelope": _envelope,
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


# Every RFC 3986 gen-delim and sub-delim, plus `"` (not RFC-valid raw, but seen in real values). A
# password holding any of them, raw, must still be recognised on the whole value: kept off the docker
# argv of the main run and of the pre-flight probe, routed to the env-file, and masked in the echo.
# A raw `@` stays an accepted residual only where it is ambiguous with a path `@` (see
# `redaction.url_userinfo_end`); placed before the host, as here, it is unambiguous.
URL_DELIMITERS = "!$&'()*+,;=:/?#[]@\""
DELIMITER_FORMS = {
    "schemeless": "alice:{c}@proxy.local:8080",
    "scheme-relative": "//alice:{c}@proxy.local:8080",
    "http": "http://alice:{c}@proxy.local:8080",
    "https-token": "https://{c}@proxy.local:3128/v1",
    "socks5": "socks5://alice:{c}@proxy.local:1080",
}


def _delimiter_cases() -> Iterator[tuple[str, str, list[str]]]:
    """Yield `(case_id, value, parts)`: each delimiter between two runtime-built password parts."""
    for f_id, form in DELIMITER_FORMS.items():
        for delim in URL_DELIMITERS:
            if "alice:" not in form and delim in "/?#":
                # A bare user with a raw `/`, `?` or `#` reads as host + path (`https://host/p@x`).
                continue
            parts = [_letters(), _letters()]
            yield f"{f_id}/{delim!r}", form.format(c=delim.join(parts)), parts


class _DockerRecorder:
    def __init__(self) -> None:
        self.argv: list[str] = []
        self.env_file: dict[str, str] = {}

    def __call__(self, cmd, *_args, **_kwargs):
        self.argv = list(cmd)
        if "--env-file" in cmd:
            with open(cmd[cmd.index("--env-file") + 1], encoding="utf-8") as fh:
                self.env_file = dict(line.rstrip("\n").split("=", 1) for line in fh)
        from unittest.mock import MagicMock

        return MagicMock(returncode=0)


def test_every_delimiter_in_a_password_stays_off_argv_and_echo(monkeypatch):
    """Main-run argv, probe argv and the `Executing:` echo hold no password part; the env-file does."""
    from sandbox_executor import preflight
    from sandbox_executor.redaction import redact_command, redact_text

    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr(cli, "get_agent_session_mounts", lambda agent_id: [])
    monkeypatch.setattr(cli, "get_ssh_auth_mounts", lambda: ([], {}))
    monkeypatch.setattr(cli, "get_token_reduction_mounts_and_envs", lambda **kwargs: ([], {}))
    failures: list[str] = []
    for case_id, value, parts in _delimiter_cases():
        docker = _DockerRecorder()
        env = {"HOLON_REPO_URL": "https://github.com/myorg/myrepo.git", "HOLON_PROXY_URL": value}
        stdout = io.StringIO()
        with (
            patch.dict(os.environ, env),
            patch.object(cli.subprocess, "run", docker),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            rc = cli.run_docker_container("executor", "img", [], agent_id="claude-agent")
            probe_argv, probe_secret = preflight.build_probe_docker_cmd(
                "claude-agent", "m", "img", "probe", session_mounts=[]
            )
        echo = [line for line in stdout.getvalue().splitlines() if line.startswith("Executing:")]
        texts = {
            "main-argv": [" ".join(docker.argv)],
            "probe-argv": [" ".join(probe_argv)],
            "echo": echo,
            "env-item-echo": [redact_command(["-e", f"HOLON_PROXY_URL={value}"])],
            # Free text marks a schemeless value as a URL only by its `<NAME>_URL=` assignment.
            "free-text": [
                redact_text(f"proxy {'HOLON_PROXY_URL=' if case_id.startswith('schemeless') else ''}{value} x")
            ],
        }
        leaked = [where for where, text in texts.items() if any(_leaks(text, part) for part in parts)]
        if rc != 0 or not echo:
            leaked.append("no-run")
        if docker.env_file.get("HOLON_PROXY_URL") != value or probe_secret.get("HOLON_PROXY_URL") != value:
            leaked.append("not-env-file")
        failures.extend(f"{case_id}:{where}" for where in leaked)
    assert not failures, f"{len(failures)} delimiter cases failed, e.g. {failures[:40]}"


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ('{"url":"http://proxy.corp:8080","owner":"ops@corp.example"}', '"owner":"ops@corp.example"'),
        ("('http://proxy.corp:8080', 'ops@corp.example')", "'ops@corp.example'"),
        ("it's at http://proxy.corp:8080/ and ops@corp.example", "ops@corp.example"),
    ],
    ids=["compact-json", "python-repr", "prose-apostrophe"],
)
def test_quoted_url_ends_at_its_closing_quote(text, kept):
    """A quote no longer ends a URL token, except the one that closes a quote-opened URL."""
    from sandbox_executor.redaction import redact_text

    assert redact_text(text) == text
    assert kept in redact_text(text)
