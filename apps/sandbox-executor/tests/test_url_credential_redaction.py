"""URL credential redaction: no message built from a URL-valued setting echoes a 4-character fragment of its credential.

The rule under test: a warning, error, exception, log line or `--json` field may name the variable
and a host that was validated after the userinfo was removed with the shared last-`@` rule
(`redaction.trusted_url_authority`), and nothing else taken from the value. Every entry point that
turns a configured URL into a message is driven through the full shape matrix below, and the
captured output (logs, stdout, stderr, exception text, and the top-level `redact_cli_error`
rendering) must not hold any 4-character fragment of the credential. The #94 sites are driven too:
`holon doctor` (environment section, text and `--json`), the gateway probe (probe URL, cache line,
fetch errors, `redact_url`) and the model envelope lines.

The matrices are fixed shape matrices, not property tests: every credential and every temp name a case draws comes
from a generator seeded by `seeded_secrets` (a fixed default seed; set `CREDENTIAL_TEST_SEED=<n>` to rerun a seed, or
`CREDENTIAL_TEST_SEED=random` for a stress run). Credentials are built from letters that occur in no message built by
the code (`FGJQVZ`) and digits, and compared case-insensitively, so a 4-character check cannot match ordinary output.
The only incidental random text in the checked output, the exact `--env-file` path that `mkstemp` creates and the
pi-agent session dir that `mkdtemp` creates, is masked before the fragment check, so a random temp path cannot cause a
false leak. Failure messages name the case id and the seed, never the value.
"""

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
    # The first digit keeps every head of 5 digits or fewer inside the valid port range.
    return rng.choice("234") + "".join(rng.choice(_DIGITS) for _ in range(n - 1))


def _credential_shapes() -> dict[str, Callable[[random.Random], str]]:
    """Map each credential shape id to a builder that draws one credential of that shape from a generator."""
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
    """Yield `(case_id, value, credential)` for every prefix x userinfo x credential x host x suffix.

    Every credential is drawn from `rng`, so one generator state gives one matrix.
    """
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
    """A responding and a failing fetcher; both record the probe URL they were handed, which is shown too."""

    def ok(url: str, timeout: float) -> bytes:
        seen.append(url)
        # `HEALTH_TIGHT_BYTES` drives every #94 doctor line: the cache state, memory figures, both envelope warnings.
        return HEALTH_TIGHT_BYTES

    def fail(url: str, timeout: float) -> bytes:
        seen.append(url)
        raise OSError(f"connection refused by {url}")

    return ok, fail


def _doctor(value: str) -> list[str]:
    """#94 `holon doctor`: the environment section, then the unified report as text and as `--json`."""
    env = {"HOLON_LOCAL_LLM": "1", "HOLON_LOCAL_BASE_URL": value, "HOLON_LOCAL_MODELS": "m1"}
    texts: list[str] = []
    # The eight doctor runs of one case differ only in host configuration, fetcher and `--json`; the configuration
    # section reads none of them (proved in test_doctor_replay.py), so it is computed once per case and replayed.
    # The four blocks of a case share one environment, so one capture collects all of them.
    with (
        replayed_configuration_section(),
        # Plain stand-ins, not MagicMocks: building a mock per call dominated this matrix's runtime.
        patch.object(cli_doctor, "run_preflight_probe", probe_stand_in),
        _captured(env) as out,
    ):
        for host_config in (None, _host_config(value)):
            for fetch in _fetchers(texts):
                with (
                    patch.object(local_llm, "host_models_json", lambda *_a, host_config=host_config, **_k: host_config),
                    # The direct report and the two doctor runs of one block have the same host configuration and
                    # fetcher: the environment report runs once and its text and code are replayed for all three
                    # (proved in test_doctor_replay.py and test_doctor_replay_users.py).
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
    """`urllib.request.build_opener` stand-in: one opener that refuses every URL, whatever handlers are asked for."""
    return _REFUSING_OPENER


def _gateway_probe(value: str) -> list[str]:
    """#94 gateway probe: the probe URL, its cache line and error (injected and default fetchers), `redact_url`."""
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
    """Every matrix case leaves no credential fragment in logs, stdout, stderr or exception text.

    Each test runs one URL prefix's slice of the matrix, so the parallel runner can spread the slices across workers.
    """
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(root_ca, "generate_root_ca", lambda: pytest.fail("must not generate a CA"))
    # `_captured` swaps in an exact environment per case; starting from an empty one keeps each swap cheap.
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
    """The matrix is the full cross product and its ids are unique."""
    ids = [case_id for case_id, _, _ in matrix(seeded_secrets.generator("url-redaction:matrix"))]
    assert len(ids) == MATRIX_SIZE == len(set(ids))
    # Every prefix, userinfo form, credential shape, host and suffix is reached, in that order of the id.
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
    """A value whose userinfo boundary is unambiguous keeps its validated host in messages."""
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
        # urlsplit reads `alice` as the host here, so the two readings disagree.
        "http://alice:{c}//x@proxy.corp",
    ],
)
def test_ambiguous_or_invalid_host_is_withheld(form):
    """An `@` left after the userinfo, a bad port or a non-host authority shows nothing."""
    from sandbox_executor.redaction import trusted_url_authority

    rng = seeded_secrets.generator(f"url-redaction:ambiguous:{form}")
    assert trusted_url_authority(form.format(c=_letters(rng), d=_digit_head(rng, 4))) is None, (
        seeded_secrets.seed_note()
    )


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


def _delimiter_cases(rng: random.Random) -> Iterator[tuple[str, str, list[str]]]:
    """Yield `(case_id, value, parts)`: each delimiter between two password parts drawn from `rng`."""
    for f_id, form in DELIMITER_FORMS.items():
        for delim in URL_DELIMITERS:
            if "alice:" not in form and delim in "/?#":
                # A bare user with a raw `/`, `?` or `#` reads as host + path (`https://host/p@x`).
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
    """Stand in a docker binary and empty mount helpers, so `run_docker_container` reaches the echo."""
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr("sandbox_executor.launcher.mounts.get_agent_session_mounts", lambda agent_id: [])
    monkeypatch.setattr(cli.git_flow, "sandbox_worktree_mounts", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "sandbox_executor.launcher.sidecar.get_token_reduction_mounts_and_envs", lambda **kwargs: ([], {})
    )


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
PI_AGENT_DIR_PLACEHOLDER = "<pi-agent-dir>"
_PI_AGENT_MOUNT_SUFFIX = f":{local_llm.CONTAINER_AGENT_DIR}:rw"
# `mkdtemp(prefix="holon-pi-agent-", dir="~/.holon/tmp")` names: the prefix and exactly 8 random `[a-z0-9_]` characters.
_PI_AGENT_DIR_NAME = re.compile(r"holon-pi-agent-[a-z0-9_]{8}")


def _fixture_paths(argv: list[str], home: str) -> dict[str, str]:
    """Map each fixture-owned temp path `argv` names to its placeholder: the `--env-file` path and the pi-agent dir.

    The env-file comes from `mkstemp` and the pi-agent session dir from `mkdtemp` under the isolated HOME's
    `.holon/tmp`; their random characters can share a 4-letter run with a credential without any of it having leaked.
    Only a mount host path of the exact `mkdtemp` shape directly under `home`'s `.holon/tmp` counts, so text glued
    onto it is still checked.
    """
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
    """Replace each exact fixture-owned temp path of `argv` in `texts` with its placeholder, and nothing else.

    `home` is the isolated HOME the run used; it defaults to the current one.
    """
    paths = _fixture_paths(argv, home or os.path.expanduser("~"))
    masked = []
    for text in texts:
        for path in sorted(paths, key=len, reverse=True):
            text = text.replace(path, paths[path])
        masked.append(text)
    return masked


def _leak_sites(texts: dict[str, list[str]], argv: list[str], parts: list[str]) -> list[str]:
    """Name each checked text that holds a fragment of any password part once the fixture temp paths are masked."""
    return [
        where
        for where, text in texts.items()
        if any(_leaks(_without_fixture_paths(text, argv), part) for part in parts)
    ]


def _run_delimiter_case(value: str) -> tuple[int, _DockerRecorder, list[str], dict[str, str], list[str]]:
    """Run the main container and the probe for one value; return `(rc, docker, probe_argv, probe_secret, echo)`."""
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
        for case_id, value, parts in _delimiter_cases(seeded_secrets.generator("url-redaction:delimiters"))
        for where in _delimiter_case_failures(case_id, value, parts)
    ]
    assert not failures, f"{len(failures)} delimiter cases failed, e.g. {failures[:40]} {seeded_secrets.seed_note()}"


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
    monkeypatch.setattr(env_files, "env_file_dir", lambda: str(env_dir))
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


# A credential-bearing value placed in a non-URL envelope or budget setting by mistake. The
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
}


def _invalid_value_cases(rng: random.Random) -> Iterator[tuple[str, dict[str, str], dict | None, str]]:
    """Yield `(case_id, env, host_config, credential)`: every credential shape in every invalid-value slot."""
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
    """The gateway fetcher of every invalid-value case: one object, so the three reports of a case share it."""
    return b"{}"


def _isolate_invalid_value_process(monkeypatch) -> None:
    """The process state every invalid-value case starts from: a docker stand-in, no mounts, an empty environment."""
    _patch_docker_host(monkeypatch)
    for name in list(os.environ):
        monkeypatch.delenv(name)


@contextlib.contextmanager
def _invalid_value_case(
    case_id: str, env: dict[str, str], host_config: dict | None, credential: str, home: str
) -> Iterator[tuple[list[str], _DockerRecorder]]:
    """Run one invalid-value case under the isolated `home`; yields the captured texts and the docker recorder."""
    docker = _DockerRecorder()
    # Each temp name is first drawn sharing a run with the credential, so the masking under the isolated HOME is
    # what keeps a forwarded run from reading as a leak, not the luck of a random name.
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
    """The environment section on a stream of its own, then the unified report as text and as `--json`: the codes."""
    # All three reports see one environment, host configuration and fetcher, and both configuration sections one
    # agent and model, so each is computed once and replayed. Proved for this function in test_doctor_replay.py.
    with replayed_configuration_section(), replayed_environment_report():
        env_out = io.StringIO()
        codes = [cli_doctor.report_budgets_and_envelope(fetch=_empty_health, out=env_out)]
        out.append(env_out.getvalue())
        codes.extend(cli.run_doctor(doctor_args(as_json), fetch=_empty_health) for as_json in (False, True))
    return codes


def test_credential_in_invalid_envelope_value_never_echoed(monkeypatch, tmp_path):
    """Doctor (environment section, text and `--json`) and the run path withhold an invalid value.

    The isolated `HOME` comes from `fast_home` (memory-backed when the host has it, else under `tmp_path`), and the
    pi-agent session dir is created under it per case; the `HOME` path and the temp name can share a 4-character run
    with the credential, so what keeps the `HOME` path from reading as a fragment is that the whole pi-agent mount path
    is masked, not the choice of `HOME`.
    """
    _isolate_invalid_value_process(monkeypatch)
    failures: list[str] = []
    collided: list[str] = []
    cases = list(_invalid_value_cases(seeded_secrets.generator("url-redaction:invalid-envelope")))
    with fast_home(str(tmp_path)) as home:
        for case_id, env, host_config, credential in cases:
            # A budget float falls back to its default, so the run goes on to forward the host `HOLON_*` variable to
            # the container unchanged: through the env-file, so neither the argv nor the echo may hold a fragment of it.
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
    """Return a `tempfile._get_candidate_names` stand-in whose first name shares a 4-character run with `credential`.

    Later draws come from `rng`, so a name already taken on disk is retried as usual and still reproducibly.
    """
    name = re.search(r"[a-z0-9_]{4}", credential.lower()).group() * 2
    return lambda: itertools.chain([name], seeded_secrets.name_sequence(rng))


def _forwarded_run_failures(env: dict[str, str], docker: _DockerRecorder, credential: str, home: str) -> list[str]:
    """Name what is wrong with a run, under the isolated `home`, that forwarded every `HOLON_*` variable of `env`."""
    failures = []
    if not docker.argv:
        failures.append("no-run")
    if _leaks(_without_fixture_paths([" ".join(docker.argv)], docker.argv, home), credential):
        failures.append("main-argv")
    failures.extend(f"not-env-file:{name}" for name in env if docker.env_file.get(name) != env[name])
    return failures


# A temp name sharing a 4-letter run with the credential below; mkstemp and mkdtemp both draw it.
_COLLIDING_TEMP_NAME = "zq" + _LETTERS.lower()
_COLLIDING_CREDENTIAL = "3/" + _LETTERS * 2


def test_pi_agent_dir_sharing_a_credential_run_is_not_a_leak(monkeypatch, tmp_path):
    """A forwarded budget value whose credential shares a run with the random pi-agent dir name is not a leak."""
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


# A temp name with no character of `_LETTERS`, so only the HOME path below can share a run with the credential.
_NEUTRAL_TEMP_NAME = "abcdehik"


def test_home_path_sharing_a_credential_run_is_not_a_leak(monkeypatch, tmp_path):
    """An isolated HOME whose path shares a run with the credential is not a leak through the masked pi-agent dir.

    pytest's run directory (`pytest-<N>`, which grows when the suite is rerun) or a random macOS `/var/folders` prefix
    sits in front of every pi-agent mount path; the whole fixture path is masked, so it cannot read as a fragment.
    """
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
    """A credential fragment in the argv is reported even when glued onto the masked pi-agent dir.

    The fragment is slash-free and lower-case, so glued onto the dir it stays in the dir's name; a pi-agent-shaped
    dir nested deeper than `.holon/tmp` or not mounted at the container agent dir is not masked either.
    """
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


# Every forwarded `HOLON_*` name checked by the env-file routing matrix: the invalid-value names (including the
# `TIMEOUT` and `BUDGET` ones), names whose value is not secret-shaped, and an arbitrary name.
FORWARDED_HOLON_NAMES = (
    *INVALID_VALUE_ENV,
    "HOLON_AGENT_SETTINGS",
    "HOLON_AGENT_EFFORT",
    "HOLON_MODEL",
    "HOLON_PROXY_URL",
    "HOLON_EXAMPLE_SETTING",
)


def _run_forwarded_value(name: str, value: str) -> tuple[int, _DockerRecorder, list[str], dict[str, str], list[str]]:
    """Run the main container and the probe with only `name=value` set (plus the isolated `HOME`)."""
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
    """Name every site where a forwarded `HOLON_*` value shows, or the env-file that lacks it."""
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
    """A bare credential under any forwarded `HOLON_*` name reaches only the env-file, in the main run and probe."""
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


# Base URLs `urlsplit` (or its lazy port check) rejects, or that have no usable `scheme://host`.
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
    """Yield `(case_id, value, credential, plain)`: every form with no userinfo and with each credential shape.

    `plain` marks values whose userinfo boundary is unambiguous, so the parse failure is the only problem and the
    doctor must name the source. With a delimiter inside the userinfo `urlsplit` may read part of the credential as
    the host; such a value is withheld as ambiguous instead, which the leak check covers.
    """
    for f_id, form in UNPARSEABLE_FORMS.items():
        yield f"{f_id}/no-userinfo", form.format(u=""), "", True
        for c_id, make in _credential_shapes().items():
            credential = make(rng)
            plain = c_id in ("plain", "percent-encoded")
            yield f"{f_id}/{c_id}", form.format(u=f"alice:{credential}@"), credential, plain


@pytest.mark.parametrize("slot", ["HOLON_LOCAL_BASE_URL", "host-baseUrl"])
def test_doctor_survives_unparseable_base_url(slot, monkeypatch):  # noqa: C901, PLR0915
    """An unparseable host-side base URL is skipped by the probe, never crashes `holon doctor`.

    Text and `--json` both exit 0 (the run path handles the same config), `--json` stays valid JSON with the problem
    under `environment.warnings`, the warning names only the source, and no part of the value is echoed.
    """
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
            # Plain stand-ins, not MagicMocks, as in `_doctor`.
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
            except Exception as exc:  # any escape is the doctor crash; report the case id only
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
