import contextlib
import io
import itertools
import json
import logging
import os
import random
import re
import shutil
import socket
import subprocess
import tempfile
import urllib.error
from collections.abc import Callable, Iterator
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from sandbox_executor import cli, local_llm, preflight
from sandbox_executor.cli import doctor as cli_doctor
from sandbox_executor.launcher import env_files, proxy_readiness, root_ca

from tests import seeded_secrets
from tests.doctor_replay import replayed_configuration_section, replayed_direct_report, replayed_environment_report
from tests.fast_home import fast_home
from tests.url_redaction_matrix import HEALTH_TIGHT_BYTES, doctor_args, probe_stand_in

FRAGMENT_LEN = 4
# why: no message the code builds holds these letters, so a 4-character match is a real leak
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


def _letters(rng: random.Random, n: int = 12) -> str:
    return "".join(rng.choice(_LETTERS) for _ in range(n))


def _digit_head(rng: random.Random, n: int) -> str:
    # why: the first digit keeps every head of 5 digits or fewer inside the valid port range
    return rng.choice("234") + "".join(rng.choice(_DIGITS) for _ in range(n - 1))


def _credential_shapes() -> dict[str, Callable[[random.Random], str]]:
    shapes: dict[str, Callable[[random.Random], str]] = {
        "plain": _letters,
        "double-slash": lambda rng: _letters(rng, 6) + "//" + _letters(rng, 6),
        "percent-encoded": lambda rng: _letters(rng, 6) + "%2F" + _letters(rng, 6),
    }
    for n in range(1, 8):
        for name, delim in (("slash", "/"), ("query", "?"), ("fragment", "#"), ("colon", ":")):
            shapes[f"digits{n}-{name}"] = lambda rng, n=n, d=delim: _digit_head(rng, n) + d + _letters(rng)
    return shapes


def matrix(rng: random.Random) -> Iterator[tuple[str, str, str]]:
    for p_id, prefix in PREFIXES.items():
        for u_id, userinfo in USERINFO.items():
            for c_id, make in _credential_shapes().items():
                for h_id, host in HOSTS.items():
                    for s_id, suffix in SUFFIXES.items():
                        credential = make(rng)
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
            # why: the top-level CLI handlers render exceptions through redact_cli_error with this env set
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

        with patch.object(proxy_readiness, "wait_for_proxy", wait), _captured({"HOLON_PROXY_URL": value}) as out:
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


def _host_config(value: str) -> dict:
    return {"providers": {"vmlx": {"baseUrl": value, "models": [{"id": "m", "contextWindow": 65536}]}}}


def _fetchers(seen: list[str]) -> tuple[Callable[[str, float], bytes], ...]:
    def ok(url: str, timeout: float) -> bytes:
        seen.append(url)
        # why: `HEALTH_TIGHT_BYTES` drives every #94 doctor line: cache state, memory figures, envelope warnings
        return HEALTH_TIGHT_BYTES

    def fail(url: str, timeout: float) -> bytes:
        seen.append(url)
        raise OSError(f"connection refused by {url}")

    return ok, fail


def _doctor(value: str) -> list[str]:
    env = {"HOLON_LOCAL_LLM": "1", "HOLON_LOCAL_BASE_URL": value, "HOLON_LOCAL_MODELS": "m1"}
    texts: list[str] = []
    # why: the configuration section ignores host config, fetcher and `--json` (test_doctor_replay.py), so it replays
    with (
        replayed_configuration_section(),
        # why: plain stand-ins, not MagicMocks: building a mock per call dominated this matrix's runtime
        patch.object(cli_doctor, "run_preflight_probe", probe_stand_in),
        _captured(env) as out,
    ):
        for host_config in (None, _host_config(value)):
            for fetch in _fetchers(texts):
                with (
                    patch.object(local_llm, "host_models_json", lambda *_a, host_config=host_config, **_k: host_config),
                    # why: one block shares host config and fetcher, so its environment report runs once and replays
                    replayed_environment_report(),
                    replayed_direct_report(),
                ):
                    cli_doctor.report_budgets_and_envelope(fetch=fetch)
                    for as_json in (False, True):
                        cli.run_doctor(doctor_args(as_json), fetch=fetch)
    texts.extend(out)
    return texts


class _RefusingOpener:
    def open(self, url: str, timeout: float):
        raise urllib.error.URLError(f"refused {url}")


_REFUSING_OPENER = _RefusingOpener()


def _refusing_opener(*_handlers) -> _RefusingOpener:
    return _REFUSING_OPENER


def _gateway_probe(value: str) -> list[str]:
    texts: list[str] = []
    with (
        patch.object(local_llm.urllib.request, "build_opener", _refusing_opener),
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
    "doctor": _doctor,
    "gateway_probe": _gateway_probe,
    "envelope": _envelope,
}


def _leaks(texts: list[str], credential: str) -> bool:
    haystack = "\n".join(texts).lower()
    needle = credential.lower()
    return any(needle[i : i + FRAGMENT_LEN] in haystack for i in range(len(needle) - FRAGMENT_LEN + 1))


@pytest.mark.parametrize("prefix", sorted(PREFIXES))
@pytest.mark.parametrize("entry_point", sorted(ENTRY_POINTS))
def test_no_url_credential_fragment_in_any_message(entry_point, prefix, monkeypatch):
    # why: one prefix slice per test lets the parallel runner spread the matrix across workers
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(root_ca, "generate_root_ca", lambda: pytest.fail("must not generate a CA"))
    # why: `_captured` swaps in an exact environment per case; starting from an empty one keeps each swap cheap
    for name in list(os.environ):
        monkeypatch.delenv(name)
    run = ENTRY_POINTS[entry_point]
    rng = seeded_secrets.generator(f"url-redaction:{entry_point}-{prefix}")
    cases = [case for case in matrix(rng) if case[0].startswith(f"{prefix}/")]
    failures = [case_id for case_id, value, credential in cases if _leaks(run(value), credential)]
    assert not failures, (
        f"{len(failures)}/{len(cases)} cases leaked a credential fragment, e.g. {failures[:5]} ALL={failures} "
        f"{seeded_secrets.seed_note()}"
    )


def _refuse(*_args, **_kwargs):
    raise OSError("connection refused")


def test_url_credential_matrix_covers_every_shape():
    ids = [case_id for case_id, _, _ in matrix(seeded_secrets.generator("url-redaction:matrix"))]
    assert len(ids) == MATRIX_SIZE == len(set(ids))
    reached = [set(parts) for parts in zip(*(case_id.split("/") for case_id in ids), strict=True)]
    assert reached == [set(PREFIXES), set(USERINFO), set(_credential_shapes()), set(HOSTS), set(SUFFIXES)]


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
    from sandbox_executor.redaction import trusted_url_authority

    rng = seeded_secrets.generator(f"url-redaction:unambiguous:{form}")
    assert trusted_url_authority(form.format(c=_letters(rng))) == expected, seeded_secrets.seed_note()


@pytest.mark.parametrize(
    "form",
    [
        "http://alice:{d}/{c}@localhost:8081/v1",
        "//{d}?{c}@proxy:3128",
        "http://localhost:8081/v1?q={c}@x",
        "http://alice:{c}@localhost:99999",
        "http://alice:{c}@local%68ost:8081",
        "http://alice:{c}@:8081",
        # why: urlsplit reads `alice` as the host here, so the two readings disagree
        "http://alice:{c}//x@proxy.corp",
    ],
)
def test_ambiguous_or_invalid_host_is_withheld(form):
    from sandbox_executor.redaction import trusted_url_authority

    rng = seeded_secrets.generator(f"url-redaction:ambiguous:{form}")
    assert trusted_url_authority(form.format(c=_letters(rng), d=_digit_head(rng, 4))) is None, (
        seeded_secrets.seed_note()
    )


# why: `"` is not RFC-valid raw but occurs in real values; a raw `@` placed before the host is unambiguous
URL_DELIMITERS = "!$&'()*+,;=:/?#[]@\""
DELIMITER_FORMS = {
    "schemeless": "alice:{c}@proxy.local:8080",
    "scheme-relative": "//alice:{c}@proxy.local:8080",
    "http": "http://alice:{c}@proxy.local:8080",
    "https-token": "https://{c}@proxy.local:3128/v1",
    "socks5": "socks5://alice:{c}@proxy.local:1080",
}


def _delimiter_cases(rng: random.Random) -> Iterator[tuple[str, str, list[str]]]:
    for f_id, form in DELIMITER_FORMS.items():
        for delim in URL_DELIMITERS:
            if "alice:" not in form and delim in "/?#":
                # why: a bare user with a raw `/`, `?` or `#` reads as host + path (`https://host/p@x`)
                continue
            parts = [_letters(rng), _letters(rng)]
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
        return subprocess.CompletedProcess(cmd, 0)


def _patch_docker_host(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr("sandbox_executor.launcher.mounts.get_agent_session_mounts", lambda agent_id: [])
    monkeypatch.setattr(cli.git_flow, "sandbox_worktree_mounts", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "sandbox_executor.launcher.sidecar.get_token_reduction_mounts_and_envs", lambda **kwargs: ([], {})
    )


def _delimiter_texts(
    case_id: str, value: str, argv: list[str], probe_argv: list[str], echo: list[str]
) -> dict[str, list[str]]:
    from sandbox_executor.redaction import redact_command, redact_text

    return {
        "main-argv": [" ".join(argv)],
        "probe-argv": [" ".join(probe_argv)],
        "echo": echo,
        "env-item-echo": [redact_command(["-e", f"HOLON_PROXY_URL={value}"])],
        # why: free text marks a schemeless value as a URL only by its `<NAME>_URL=` assignment
        "free-text": [redact_text(f"proxy {'HOLON_PROXY_URL=' if case_id.startswith('schemeless') else ''}{value} x")],
    }


ENV_FILE_PLACEHOLDER = "<env-file>"
PI_AGENT_DIR_PLACEHOLDER = "<pi-agent-dir>"
_PI_AGENT_MOUNT_SUFFIX = f":{local_llm.CONTAINER_AGENT_DIR}:rw"
# why: `mkdtemp` names the dir with this prefix plus exactly 8 random `[a-z0-9_]` characters
_PI_AGENT_DIR_NAME = re.compile(r"holon-pi-agent-[a-z0-9_]{8}")


def _fixture_paths(argv: list[str], home: str) -> dict[str, str]:
    # why: random temp-path characters can share a 4-letter run with a credential without any of it having leaked
    paths = {}
    if "--env-file" in argv:
        paths[argv[argv.index("--env-file") + 1]] = ENV_FILE_PLACEHOLDER
    base_tmp = os.path.join(home, ".holon", "tmp")
    for arg in argv:
        host = arg.removesuffix(_PI_AGENT_MOUNT_SUFFIX)
        if host != arg and os.path.dirname(host) == base_tmp and _PI_AGENT_DIR_NAME.fullmatch(os.path.basename(host)):
            paths[host] = PI_AGENT_DIR_PLACEHOLDER
    return paths


def _without_fixture_paths(texts: list[str], argv: list[str], home: str | None = None) -> list[str]:
    paths = _fixture_paths(argv, home or os.path.expanduser("~"))
    masked = []
    for text in texts:
        for path in sorted(paths, key=len, reverse=True):
            text = text.replace(path, paths[path])
        masked.append(text)
    return masked


def _leak_sites(texts: dict[str, list[str]], argv: list[str], parts: list[str]) -> list[str]:
    return [
        where
        for where, text in texts.items()
        if any(_leaks(_without_fixture_paths(text, argv), part) for part in parts)
    ]


def _run_delimiter_case(value: str) -> tuple[int, _DockerRecorder, list[str], dict[str, str], list[str]]:
    docker = _DockerRecorder()
    env = {"HOLON_PROXY_URL": value}
    stdout = io.StringIO()
    with (
        patch.dict(os.environ, env),
        patch.object(subprocess, "run", docker),
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        rc = cli.run_docker_container("planner", "img", [], agent_id="claude-agent")
        probe_argv, probe_secret = preflight.build_probe_docker_cmd(
            "claude-agent", "m", "img", "probe", session_mounts=[]
        )
    echo = [line for line in stdout.getvalue().splitlines() if line.startswith("Executing:")]
    return rc, docker, probe_argv, probe_secret, echo


def _delimiter_case_failures(case_id: str, value: str, parts: list[str]) -> list[str]:
    rc, docker, probe_argv, probe_secret, echo = _run_delimiter_case(value)
    failures = _leak_sites(_delimiter_texts(case_id, value, docker.argv, probe_argv, echo), docker.argv, parts)
    if rc != 0 or not echo:
        failures.append("no-run")
    if docker.env_file.get("HOLON_PROXY_URL") != value or probe_secret.get("HOLON_PROXY_URL") != value:
        failures.append("not-env-file")
    return failures


def test_every_delimiter_in_a_password_stays_off_argv_and_echo(monkeypatch):
    _patch_docker_host(monkeypatch)
    failures = [
        f"{case_id}:{where}"
        for case_id, value, parts in _delimiter_cases(seeded_secrets.generator("url-redaction:delimiters"))
        for where in _delimiter_case_failures(case_id, value, parts)
    ]
    assert not failures, f"{len(failures)} delimiter cases failed, e.g. {failures[:40]} {seeded_secrets.seed_note()}"


def _colliding_parts() -> list[str]:
    run = "".join(_LETTERS[i] for i in (5, 3, 4, 2))
    return [run * 3, _LETTERS * 2]


def test_env_file_path_sharing_a_part_run_is_not_a_leak(monkeypatch, tmp_path_factory):
    _patch_docker_host(monkeypatch)
    parts = _colliding_parts()
    env_dir = tmp_path_factory.mktemp(f"env-{parts[0][:FRAGMENT_LEN].lower()}")
    os.chmod(env_dir, 0o700)
    monkeypatch.setattr(env_files, "env_file_dir", lambda: str(env_dir))
    value = DELIMITER_FORMS["http"].format(c="[".join(parts))
    _, docker, _, _, echo = _run_delimiter_case(value)
    _assert_collision_forced(docker.argv, echo, str(env_dir), parts[0])
    assert _delimiter_case_failures("http/'['", value, parts) == []


def _assert_collision_forced(argv: list[str], echo: list[str], env_dir: str, part: str) -> None:
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
    from sandbox_executor.redaction import redact_text

    assert redact_text(text) == text
    assert kept in redact_text(text)


# why: a credential-bearing value may land in a non-URL envelope or budget setting by mistake
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
}


def _invalid_value_cases(rng: random.Random) -> Iterator[tuple[str, dict[str, str], dict | None, str]]:
    for case_id, value, credential in matrix(rng):
        if not case_id.endswith("/ipv4/path"):
            continue
        for name in INVALID_VALUE_ENV:
            yield f"env:{name}/{case_id}", {**_LOCAL_ENV, name: value}, None, credential
        for field in INVALID_VALUE_HOST_FIELDS:
            entry = {"id": "m", "contextWindow": 65536, field: value}
            host_config = {"providers": {"vmlx": {"baseUrl": "http://localhost:8081/v1", "models": [entry]}}}
            yield f"host:{field}/{case_id}", dict(_LOCAL_ENV), host_config, credential


def _empty_health(url: str, timeout: float) -> bytes:
    return b"{}"


def _isolate_invalid_value_process(monkeypatch) -> None:
    _patch_docker_host(monkeypatch)
    for name in list(os.environ):
        monkeypatch.delenv(name)


@contextlib.contextmanager
def _invalid_value_case(
    case_id: str, env: dict[str, str], host_config: dict | None, credential: str, home: str
) -> Iterator[tuple[list[str], _DockerRecorder]]:
    docker = _DockerRecorder()
    # why: each temp name first shares a run with the credential, so masking, not luck, keeps it from reading as a leak
    colliding = _colliding_temp_names(credential, seeded_secrets.generator(f"url-redaction:temp-names:{case_id}"))
    with (
        patch.object(tempfile, "_get_candidate_names", colliding),
        patch.object(local_llm, "host_models_json", lambda *_a, **_k: host_config),
        patch.object(cli_doctor, "run_preflight_probe", probe_stand_in),
        patch.object(subprocess, "run", docker),
        _captured({**env, "HOME": home}) as out,
    ):
        yield out, docker


def _invalid_value_doctor(out: list[str]) -> list[int]:
    # why: all three reports share environment, host config and fetcher, so each replays (test_doctor_replay.py)
    with replayed_configuration_section(), replayed_environment_report():
        env_out = io.StringIO()
        codes = [cli_doctor.report_budgets_and_envelope(fetch=_empty_health, out=env_out)]
        out.append(env_out.getvalue())
        codes.extend(cli.run_doctor(doctor_args(as_json), fetch=_empty_health) for as_json in (False, True))
    return codes


def test_credential_in_invalid_envelope_value_never_echoed(monkeypatch, tmp_path):
    # why: the HOME path and temp name may share a run with the credential, so the whole pi-agent mount path is masked
    _isolate_invalid_value_process(monkeypatch)
    failures: list[str] = []
    collided: list[str] = []
    cases = list(_invalid_value_cases(seeded_secrets.generator("url-redaction:invalid-envelope")))
    with fast_home(str(tmp_path)) as home:
        for case_id, env, host_config, credential in cases:
            # why: a budget float falls back to its default, so the run forwards the value through the env-file
            rejected = "TIMEOUT" not in case_id and "BUDGET" not in case_id
            with _invalid_value_case(case_id, env, host_config, credential, home) as (out, docker):
                codes = _invalid_value_doctor(out)
                codes.append(cli.run_docker_container("planner", "img", [], agent_id="pi"))
            if _leaks(_without_fixture_paths(out, docker.argv, home), credential):
                failures.append(case_id)
            if rejected and (codes != [1, 1, 1, 1] or docker.argv):
                failures.append(f"{case_id}:not-rejected")
            if not rejected:
                forwarded = _forwarded_run_failures(env, docker, credential, home)
                failures.extend(f"{case_id}:{where}" for where in forwarded)
                if _leaks(out, credential) and _leaks([" ".join(docker.argv)], credential):
                    collided.append(case_id)
    assert len(cases) == (len(INVALID_VALUE_ENV) + len(INVALID_VALUE_HOST_FIELDS)) * MATRIX_SIZE // (
        len(HOSTS) * len(SUFFIXES)
    )
    assert not failures, (
        f"{len(failures)}/{len(cases)} invalid-value cases failed, e.g. {failures[:5]} {seeded_secrets.seed_note()}"
    )
    assert collided, "no forwarded run drew a temp name sharing a run with its credential"


def _colliding_temp_names(credential: str, rng: random.Random) -> Callable[[], Iterator[str]]:
    # why: later draws come from `rng`, so a name already taken on disk is retried reproducibly
    name = re.search(r"[a-z0-9_]{4}", credential.lower()).group() * 2
    return lambda: itertools.chain([name], seeded_secrets.name_sequence(rng))


def _forwarded_run_failures(env: dict[str, str], docker: _DockerRecorder, credential: str, home: str) -> list[str]:
    failures = []
    if not docker.argv:
        failures.append("no-run")
    if _leaks(_without_fixture_paths([" ".join(docker.argv)], docker.argv, home), credential):
        failures.append("main-argv")
    failures.extend(f"not-env-file:{name}" for name in env if docker.env_file.get(name) != env[name])
    return failures


# why: mkstemp and mkdtemp both draw this name, which shares a 4-letter run with the credential below
_COLLIDING_TEMP_NAME = "zq" + _LETTERS.lower()
_COLLIDING_CREDENTIAL = "3/" + _LETTERS * 2


def test_pi_agent_dir_sharing_a_credential_run_is_not_a_leak(monkeypatch, tmp_path):
    _patch_docker_host(monkeypatch)
    monkeypatch.setattr(tempfile, "_get_candidate_names", lambda: itertools.repeat(_COLLIDING_TEMP_NAME))
    env = {**_LOCAL_ENV, "HOLON_TURN_TIMEOUT_SECONDS": f"socks5://{_COLLIDING_CREDENTIAL}@127.0.0.1/v1"}
    docker = _DockerRecorder()
    with (
        patch.object(local_llm, "host_models_json", lambda *_a, **_k: None),
        patch.object(subprocess, "run", docker),
        _captured({**env, "HOME": str(tmp_path)}),
    ):
        cli.run_docker_container("planner", "img", [], agent_id="pi")
    pi_dir = str(tmp_path / ".holon" / "tmp" / f"holon-pi-agent-{_COLLIDING_TEMP_NAME}")
    assert f"{pi_dir}{_PI_AGENT_MOUNT_SUFFIX}" in docker.argv, "the run must mount the forced pi-agent dir"
    assert _leaks([" ".join(docker.argv)], _COLLIDING_CREDENTIAL), "the unmasked argv must share a run"
    assert _forwarded_run_failures(env, docker, _COLLIDING_CREDENTIAL, str(tmp_path)) == []


# why: no character of `_LETTERS`, so only the HOME path below can share a run with the credential
_NEUTRAL_TEMP_NAME = "abcdehik"


def test_home_path_sharing_a_credential_run_is_not_a_leak(monkeypatch, tmp_path):
    # why: pytest's run directory or a random macOS `/var/folders` prefix precedes every pi-agent mount path
    _patch_docker_host(monkeypatch)
    monkeypatch.setattr(tempfile, "_get_candidate_names", lambda: itertools.repeat(_NEUTRAL_TEMP_NAME))
    home = tmp_path / f"home-{_LETTERS[3:]}{_LETTERS[0]}".lower()
    home.mkdir()
    env = {**_LOCAL_ENV, "HOLON_TURN_TIMEOUT_SECONDS": f"socks5://{_COLLIDING_CREDENTIAL}@127.0.0.1/v1"}
    docker = _DockerRecorder()
    with (
        patch.object(local_llm, "host_models_json", lambda *_a, **_k: None),
        patch.object(subprocess, "run", docker),
        _captured({**env, "HOME": str(home)}) as out,
    ):
        cli.run_docker_container("planner", "img", [], agent_id="pi")
    pi_dir = str(home / ".holon" / "tmp" / f"holon-pi-agent-{_NEUTRAL_TEMP_NAME}")
    assert f"{pi_dir}{_PI_AGENT_MOUNT_SUFFIX}" in docker.argv, "the run must mount the pi-agent dir under HOME"
    assert _leaks([" ".join(docker.argv)], _COLLIDING_CREDENTIAL), "the unmasked argv must share a run"
    assert _leaks(out, _COLLIDING_CREDENTIAL), "the unmasked echo must share a run"
    assert not _leaks(_without_fixture_paths(out, docker.argv, str(home)), _COLLIDING_CREDENTIAL)
    assert _forwarded_run_failures(env, docker, _COLLIDING_CREDENTIAL, str(home)) == []


@pytest.mark.parametrize(
    "template",
    [
        "-v {path}:{mount} -e X={fragment}",
        "-v {path}{fragment}:{mount}",
        "-v {fragment}{path}:{mount}",
        "-v {base}/{fragment}/{name}:{mount}",
        "{path}",
    ],
    ids=["elsewhere", "beside-path", "before-path", "nested", "unmounted"],
)
def test_fragment_beside_pi_agent_dir_is_still_a_leak(template, monkeypatch, tmp_path):
    # why: a slash-free lower-case fragment glued onto the dir stays in the dir's name
    monkeypatch.setenv("HOME", str(tmp_path))
    base = str(tmp_path / ".holon" / "tmp")
    name = f"holon-pi-agent-{_COLLIDING_TEMP_NAME}"
    path = os.path.join(base, name)
    mount = _PI_AGENT_MOUNT_SUFFIX.removeprefix(":")
    fragment = _COLLIDING_CREDENTIAL[2:6].lower()
    argv = ["docker", "run", *template.format(path=path, base=base, name=name, mount=mount, fragment=fragment).split()]
    clean = ["docker", "run", "-v", f"{path}:{mount}"]
    assert _leak_sites({"main-argv": [" ".join(clean)]}, clean, [_COLLIDING_CREDENTIAL]) == []
    assert _leak_sites({"main-argv": [" ".join(argv)]}, argv, [_COLLIDING_CREDENTIAL]) == ["main-argv"]


FORWARDED_HOLON_NAMES = (
    *INVALID_VALUE_ENV,
    "HOLON_AGENT_SETTINGS",
    "HOLON_AGENT_EFFORT",
    "HOLON_MODEL",
    "HOLON_PROXY_URL",
    "HOLON_EXAMPLE_SETTING",
)


def _run_forwarded_value(name: str, value: str) -> tuple[int, _DockerRecorder, list[str], dict[str, str], list[str]]:
    docker = _DockerRecorder()
    stdout = io.StringIO()
    with (
        patch.dict(os.environ, {name: value, "HOME": os.environ["HOME"]}, clear=True),
        patch.object(subprocess, "run", docker),
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        rc = cli.run_docker_container("planner", "img", [], agent_id="claude-agent")
        probe_argv, probe_env = preflight.build_probe_docker_cmd("claude-agent", "m", "img", "probe", session_mounts=[])
    echo = [line for line in stdout.getvalue().splitlines() if line.startswith("Executing:")]
    return rc, docker, probe_argv, probe_env, echo


def _forwarded_value_failures(name: str, value: str) -> list[str]:
    rc, docker, probe_argv, probe_env, echo = _run_forwarded_value(name, value)
    texts = {"main-argv": [" ".join(docker.argv)], "probe-argv": [" ".join(probe_argv)], "echo": echo}
    failures = _leak_sites(texts, docker.argv, [value])
    if rc != 0 or not echo:
        failures.append("no-run")
    if docker.env_file.get(name) != value or probe_env.get(name) != value:
        failures.append("not-env-file")
    return failures


@pytest.mark.parametrize("name", FORWARDED_HOLON_NAMES)
def test_every_forwarded_holon_value_stays_off_argv_and_echo(name, monkeypatch):
    _patch_docker_host(monkeypatch)
    rng = seeded_secrets.generator(f"url-redaction:forwarded:{name}")
    failures = [
        f"{name}:{shape}:{where}"
        for shape, make in _credential_shapes().items()
        for where in _forwarded_value_failures(name, make(rng))
    ]
    assert not failures, (
        f"{len(failures)} forwarded-value cases failed, e.g. {failures[:20]} {seeded_secrets.seed_note()}"
    )


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


def _unparseable_cases(rng: random.Random) -> Iterator[tuple[str, str, str, bool]]:
    # why: a delimiter inside the userinfo may make `urlsplit` read credential text as the host, withheld as ambiguous
    for f_id, form in UNPARSEABLE_FORMS.items():
        yield f"{f_id}/no-userinfo", form.format(u=""), "", True
        for c_id, make in _credential_shapes().items():
            credential = make(rng)
            plain = c_id in ("plain", "percent-encoded")
            yield f"{f_id}/{c_id}", form.format(u=f"alice:{credential}@"), credential, plain


@pytest.mark.parametrize("slot", ["HOLON_LOCAL_BASE_URL", "host-baseUrl"])
def test_doctor_survives_unparseable_base_url(slot, monkeypatch):  # noqa: C901
    # why: the run path handles the same config, so the doctor must exit 0 and name only the source
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kwargs: None)
    source = "HOLON_LOCAL_BASE_URL" if slot == "HOLON_LOCAL_BASE_URL" else "host models.json provider 'vmlx' baseUrl"
    expected = f"WARNING: {source} is not a parseable URL, so its server is not probed (value withheld)"
    failures: list[str] = []
    cases = list(_unparseable_cases(seeded_secrets.generator(f"url-redaction:unparseable:{slot}")))
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
            # why: plain stand-ins, not MagicMocks, as in `_doctor`
            patch.object(local_llm, "host_models_json", lambda *_a, host_config=host_config, **_k: host_config),
            patch.object(cli_doctor, "run_preflight_probe", probe_stand_in),
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
                        code = cli.run_doctor(doctor_args(as_json), fetch=fetch)
                    results[as_json] = (code, stdout.getvalue())
                    out.append(stdout.getvalue())
            except Exception as exc:  # why: any escape is the doctor crash; report the case id only
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
    assert not failures, (
        f"{len(failures)}/{len(cases)} unparseable-URL cases failed, e.g. {failures[:5]} {seeded_secrets.seed_note()}"
    )
