"""Shape-matrix fuzz: no message built from a URL-valued setting echoes any part of its credential.

The rule under test: a warning, error, exception, log line or `--json` field may name the variable
and a host that was validated after the userinfo was removed with the shared last-`@` rule
(`redaction.trusted_url_authority`), and nothing else taken from the value. Every entry point that
turns a configured URL into a message is driven through the full shape matrix below, and the
captured output (logs, stdout, stderr, exception text, and the top-level `redact_cli_error`
rendering) must not hold any 4-character fragment of the credential. The #94 sites are driven too:
`holon doctor` (environment section, text and `--json`), the gateway probe (probe URL, cache line,
fetch errors, `redact_url`) and the model envelope lines.

Credentials are built at runtime from letters that occur in no message built by the code (`FGJQVZ`)
and digits, and compared case-insensitively, so a 4-character check cannot match ordinary output.
The one incidental random text in the checked output, the exact `--env-file` path that `mkstemp`
creates, is masked before the fragment check, so a random temp path cannot cause a false leak.
Failure messages name the case id only, never the value.
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
    assert not failures, (
        f"{len(failures)}/{MATRIX_SIZE} cases leaked a credential fragment, e.g. {failures[:5]} ALL={failures}"
    )


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


def _patch_docker_host(monkeypatch) -> None:
    """Stand in a docker binary and empty mount helpers, so `run_docker_container` reaches the echo."""
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr(cli, "get_agent_session_mounts", lambda agent_id: [])
    monkeypatch.setattr(cli, "get_ssh_auth_mounts", lambda: ([], {}))
    monkeypatch.setattr(cli, "get_token_reduction_mounts_and_envs", lambda **kwargs: ([], {}))


def _delimiter_texts(
    case_id: str, value: str, argv: list[str], probe_argv: list[str], echo: list[str]
) -> dict[str, list[str]]:
    """Return the checked texts of one delimiter case, keyed by site name."""
    from sandbox_executor.redaction import redact_command, redact_text

    return {
        "main-argv": [" ".join(argv)],
        "probe-argv": [" ".join(probe_argv)],
        "echo": echo,
        "env-item-echo": [redact_command(["-e", f"HOLON_PROXY_URL={value}"])],
        # Free text marks a schemeless value as a URL only by its `<NAME>_URL=` assignment.
        "free-text": [redact_text(f"proxy {'HOLON_PROXY_URL=' if case_id.startswith('schemeless') else ''}{value} x")],
    }


ENV_FILE_PLACEHOLDER = "<env-file>"


def _without_env_file_path(texts: list[str], argv: list[str]) -> list[str]:
    """Replace the exact `--env-file` path of `argv` in `texts` with a placeholder, and nothing else.

    The path comes from `mkstemp` in a fixture-owned directory, so its random characters can share a
    4-letter run with a password part without any part having leaked.
    """
    if "--env-file" not in argv:
        return list(texts)
    path = argv[argv.index("--env-file") + 1]
    return [text.replace(path, ENV_FILE_PLACEHOLDER) for text in texts]


def _leak_sites(texts: dict[str, list[str]], argv: list[str], parts: list[str]) -> list[str]:
    """Name each checked text that holds a fragment of any password part once the env-file path is masked."""
    return [
        where
        for where, text in texts.items()
        if any(_leaks(_without_env_file_path(text, argv), part) for part in parts)
    ]


def _run_delimiter_case(value: str) -> tuple[int, _DockerRecorder, list[str], dict[str, str], list[str]]:
    """Run the main container and the probe for one value; return `(rc, docker, probe_argv, probe_secret, echo)`."""
    from sandbox_executor import preflight

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
    return rc, docker, probe_argv, probe_secret, echo


def _delimiter_case_failures(case_id: str, value: str, parts: list[str]) -> list[str]:
    """Run the main container and the probe for one case and name every failing site."""
    rc, docker, probe_argv, probe_secret, echo = _run_delimiter_case(value)
    failures = _leak_sites(_delimiter_texts(case_id, value, docker.argv, probe_argv, echo), docker.argv, parts)
    if rc != 0 or not echo:
        failures.append("no-run")
    if docker.env_file.get("HOLON_PROXY_URL") != value or probe_secret.get("HOLON_PROXY_URL") != value:
        failures.append("not-env-file")
    return failures


def test_every_delimiter_in_a_password_stays_off_argv_and_echo(monkeypatch):
    """Main-run argv, probe argv and the `Executing:` echo hold no password part; the env-file does."""
    _patch_docker_host(monkeypatch)
    failures = [
        f"{case_id}:{where}"
        for case_id, value, parts in _delimiter_cases()
        for where in _delimiter_case_failures(case_id, value, parts)
    ]
    assert not failures, f"{len(failures)} delimiter cases failed, e.g. {failures[:40]}"


def _colliding_parts() -> list[str]:
    """Return two fixed password parts built from `_LETTERS`; the first repeats one 4-letter run."""
    run = "".join(_LETTERS[i] for i in (5, 3, 4, 2))
    return [run * 3, _LETTERS * 2]


def test_env_file_path_sharing_a_part_run_is_not_a_leak(monkeypatch, tmp_path_factory):
    """A random env-file path that happens to share a 4-letter run with a part is not reported as a leak."""
    _patch_docker_host(monkeypatch)
    parts = _colliding_parts()
    env_dir = tmp_path_factory.mktemp(f"env-{parts[0][:FRAGMENT_LEN].lower()}")
    os.chmod(env_dir, 0o700)
    monkeypatch.setattr(cli, "env_file_dir", lambda: str(env_dir))
    value = DELIMITER_FORMS["http"].format(c="[".join(parts))
    _, docker, _, _, echo = _run_delimiter_case(value)
    _assert_collision_forced(docker.argv, echo, str(env_dir), parts[0])
    assert _delimiter_case_failures("http/'['", value, parts) == []


def _assert_collision_forced(argv: list[str], echo: list[str], env_dir: str, part: str) -> None:
    """Fail unless the real env-file path sits in `env_dir` and, unmasked, reads as a leak of `part`."""
    assert "--env-file" in argv, "the main run must route the value to an env-file"
    assert os.path.dirname(argv[argv.index("--env-file") + 1]) == env_dir, "the env-file must live in env_dir"
    assert _leaks([" ".join(argv)], part), "the unmasked main argv must share a run with the part"
    assert _leaks(echo, part), "the unmasked echo must share a run with the part"


@pytest.mark.parametrize(
    ("site", "template"),
    [
        ("main-argv", "docker run --env-file {path} -e X={fragment} img"),
        ("echo", "Executing: docker run --env-file {path} -e X={fragment} img"),
        ("main-argv", "docker run --env-file {path}{fragment} img"),
        ("echo", "Executing: docker run --env-file {path}{fragment} img"),
    ],
    ids=["main-argv", "echo", "main-argv-beside-path", "echo-beside-path"],
)
def test_fragment_beside_env_file_path_is_still_a_leak(site, template, tmp_path_factory):
    """A 4-character fragment of a part in the argv or the echo is reported, even right after the path."""
    parts = _colliding_parts()
    path = str(tmp_path_factory.mktemp(f"env-{parts[0][:FRAGMENT_LEN].lower()}") / "holon-env-x")
    argv = ["docker", "run", "--env-file", path, "img"]
    texts = {"main-argv": [" ".join(argv)], "echo": [f"Executing: {' '.join(argv)}"]}
    texts[site] = [template.format(path=path, fragment=parts[1][:FRAGMENT_LEN])]
    assert _leak_sites(texts, argv, parts) == [site]


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


# C17-N1: a credential-bearing value placed in a non-URL envelope or budget setting by mistake. The
# invalid-value messages name the variable (or the model and field) and the expected type only.
INVALID_VALUE_ENV = (
    "HOLON_CONTEXT_RESERVE_TOKENS",
    "HOLON_LOCAL_CONTEXT_WINDOW",
    "HOLON_REASONING_MODE",
    "HOLON_TURN_TIMEOUT_SECONDS",
    "HOLON_STAGE_BUDGET_SECONDS",
)
INVALID_VALUE_HOST_FIELDS = ("contextWindow", "maxTokens", "reasoning")
_LOCAL_ENV = {
    "HOLON_LOCAL_LLM": "1",
    "HOLON_LOCAL_BASE_URL": "http://localhost:8081/v1",
    "HOLON_LOCAL_MODELS": "m1",
    "HOLON_LOCAL_CONTEXT_WINDOW": "65536",
    "HOLON_REPO_URL": "https://github.com/myorg/myrepo.git",
}


def _invalid_value_cases() -> Iterator[tuple[str, dict[str, str], dict | None, str]]:
    """Yield `(case_id, env, host_config, credential)`: every credential shape in every invalid-value slot."""
    for case_id, value, credential in matrix():
        if not case_id.endswith("/ipv4/path"):
            continue
        for name in INVALID_VALUE_ENV:
            yield f"env:{name}/{case_id}", {**_LOCAL_ENV, name: value}, None, credential
        for field in INVALID_VALUE_HOST_FIELDS:
            entry = {"id": "m", "contextWindow": 65536, field: value}
            host_config = {"providers": {"vmlx": {"baseUrl": "http://localhost:8081/v1", "models": [entry]}}}
            yield f"host:{field}/{case_id}", dict(_LOCAL_ENV), host_config, credential


def test_invalid_envelope_value_never_echoed_by_doctor_or_run_path(monkeypatch, tmp_path):
    """C17-N1: doctor (environment section, text and `--json`) and the run path withhold an invalid value."""
    from sandbox_executor.preflight import PreflightResult, PreflightStatus

    probe = PreflightResult(
        status=PreflightStatus.OK, agent="pi-agent", model="m", image="img", exit_code=0, output_excerpt="", hint=""
    )
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr(cli, "get_agent_session_mounts", lambda agent_id: [])
    monkeypatch.setattr(cli, "get_ssh_auth_mounts", lambda: ([], {}))
    monkeypatch.setattr(cli, "get_token_reduction_mounts_and_envs", lambda **kwargs: ([], {}))
    failures: list[str] = []
    cases = list(_invalid_value_cases())
    for case_id, env, host_config, credential in cases:
        # A budget float falls back to its default, so the run goes on to forward the host `HOLON_*` variables to
        # the container unchanged (pre-existing behaviour, outside this rule); only its doctor output is checked.
        rejected = "TIMEOUT" not in case_id and "BUDGET" not in case_id
        docker = _DockerRecorder()
        with (
            patch.object(local_llm, "host_models_json", return_value=host_config),
            patch.object(cli, "run_preflight_probe", return_value=probe),
            patch.object(cli.subprocess, "run", docker),
            _captured({**env, "HOME": str(tmp_path)}) as out,
        ):
            env_out = io.StringIO()
            codes = [cli.report_budgets_and_envelope(fetch=lambda url, timeout: b"{}", out=env_out)]
            out.append(env_out.getvalue())
            for as_json in (False, True):
                codes.append(
                    cli.run_doctor(
                        argparse.Namespace(agent=None, model=None, json=as_json, timeout=None),
                        fetch=lambda url, timeout: b"{}",
                    )
                )
            if rejected:
                codes.append(cli.run_docker_container("executor", "img", [], agent_id="pi"))
        if _leaks(out, credential):
            failures.append(case_id)
        if rejected and (codes != [1, 1, 1, 1] or docker.argv):
            failures.append(f"{case_id}:not-rejected")
    assert len(cases) == (len(INVALID_VALUE_ENV) + len(INVALID_VALUE_HOST_FIELDS)) * MATRIX_SIZE // (
        len(HOSTS) * len(SUFFIXES)
    )
    assert not failures, f"{len(failures)}/{len(cases)} invalid-value cases failed, e.g. {failures[:5]}"


# C19-N1: base URLs `urlsplit` (or its lazy port check) rejects, or that have no usable `scheme://host`.
UNPARSEABLE_FORMS = {
    "bad-ipv6-bracket-port": "http://{u}127.0.0.1:9[/v1",
    "bad-ipv6-bracket-host": "http://{u}[::1/v1",
    "port-out-of-range": "http://{u}localhost:99999/v1",
    "port-not-digits": "http://{u}localhost:x1/v1",
    "empty-host": "http://{u}/v1",
    "schemeless-garbage": "{u}not a url [",
}
_GOOD_HOST_URL = "http://127.0.0.1:8000/v1"
_GOOD_LOCAL_URL = "http://127.0.0.1:8001/v1"


def _unparseable_cases() -> Iterator[tuple[str, str, str, bool]]:
    """Yield `(case_id, value, credential, plain)`: every form with no userinfo and with each credential shape.

    `plain` marks values whose userinfo boundary is unambiguous, so the parse failure is the only problem and the
    doctor must name the source. With a delimiter inside the userinfo `urlsplit` may read part of the credential as
    the host; such a value is withheld as ambiguous instead, which the leak check covers.
    """
    for f_id, form in UNPARSEABLE_FORMS.items():
        yield f"{f_id}/no-userinfo", form.format(u=""), "", True
        for c_id, make in _credential_shapes().items():
            credential = make()
            plain = c_id in ("plain", "percent-encoded")
            yield f"{f_id}/{c_id}", form.format(u=f"alice:{credential}@"), credential, plain


@pytest.mark.parametrize("slot", ["HOLON_LOCAL_BASE_URL", "host-baseUrl"])
def test_doctor_survives_unparseable_base_url(slot, monkeypatch):  # noqa: C901, PLR0915
    """C19-N1: an unparseable host-side base URL is skipped by the probe, never crashes `holon doctor`.

    Text and `--json` both exit 0 (the run path handles the same config), `--json` stays valid JSON with the problem
    under `environment.warnings`, the warning names only the source, and no part of the value is echoed.
    """
    from sandbox_executor.preflight import PreflightResult, PreflightStatus

    monkeypatch.setattr(cli.shutil, "which", lambda *_args, **_kwargs: None)
    probe = PreflightResult(
        status=PreflightStatus.OK, agent="pi-agent", model="m", image="img", exit_code=0, output_excerpt="", hint=""
    )
    source = "HOLON_LOCAL_BASE_URL" if slot == "HOLON_LOCAL_BASE_URL" else "host models.json provider 'vmlx' baseUrl"
    expected = f"WARNING: {source} is not a parseable URL, so its server is not probed (value withheld)"
    failures: list[str] = []
    cases = list(_unparseable_cases())
    for case_id, value, credential, plain in cases:
        host_url, local_url = (_GOOD_HOST_URL, value) if slot == "HOLON_LOCAL_BASE_URL" else (value, _GOOD_LOCAL_URL)
        env = {
            "HOLON_LOCAL_LLM": "1",
            "HOLON_LOCAL_BASE_URL": local_url,
            "HOLON_LOCAL_MODELS": "m1",
            "HOLON_LOCAL_CONTEXT_WINDOW": "65536",
        }
        host_config = {"providers": {"vmlx": {"baseUrl": host_url, "models": [{"id": "m", "contextWindow": 65536}]}}}
        probed: list[str] = []
        texts: list[str] = []
        with (
            patch.object(local_llm, "host_models_json", return_value=host_config),
            patch.object(cli, "run_preflight_probe", return_value=probe),
            _captured(env) as out,
        ):
            try:
                assert local_llm.health_url(value) is None

                def fetch(url: str, timeout: float, probed: list[str] = probed) -> bytes:
                    probed.append(url)
                    return b"{}"

                results = {}
                for as_json in (False, True):
                    stdout = io.StringIO()
                    with contextlib.redirect_stdout(stdout):
                        code = cli.run_doctor(
                            argparse.Namespace(agent=None, model=None, json=as_json, timeout=None), fetch=fetch
                        )
                    results[as_json] = (code, stdout.getvalue())
                    out.append(stdout.getvalue())
            except Exception as exc:  # any escape is the C19-N1 crash; report the case id only
                failures.append(f"{case_id}:crash:{type(exc).__name__}")
                continue
        texts.extend(out)
        texts.extend(probed)
        text_code, text_out = results[False]
        json_code, json_out = results[True]
        try:
            environment = json.loads(json_out)["environment"]
        except (ValueError, KeyError):
            failures.append(f"{case_id}:invalid-json")
            continue
        if set(environment) != {"ok", "lines", "warnings"}:
            failures.append(f"{case_id}:json-shape")
            continue
        if (text_code, json_code) != (0, 0) or environment["ok"] is not True:
            failures.append(f"{case_id}:exit")
        if not set(environment["warnings"]) <= set(environment["lines"]):
            failures.append(f"{case_id}:json-shape")
        if plain and (expected not in environment["warnings"] or expected not in text_out):
            failures.append(f"{case_id}:not-reported")
        if any(line != expected for line in environment["warnings"]):
            failures.append(f"{case_id}:unexpected-warning")
        if credential and _leaks(texts, credential):
            failures.append(f"{case_id}:leak")
        if not credential and any(value in text for text in texts):
            failures.append(f"{case_id}:value-echoed")
    assert len(cases) == len(UNPARSEABLE_FORMS) * (1 + len(_credential_shapes()))
    assert not failures, f"{len(failures)}/{len(cases)} unparseable-URL cases failed, e.g. {failures[:5]}"
