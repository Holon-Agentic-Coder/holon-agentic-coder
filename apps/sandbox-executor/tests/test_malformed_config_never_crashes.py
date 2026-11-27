import argparse
import contextlib
import io
import json
import logging
import os
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from unittest.mock import patch

from sandbox_executor import budget_limits, budgets, cli, local_llm
from sandbox_executor.cli import doctor as cli_doctor
from sandbox_executor.preflight import PreflightResult, PreflightStatus

from tests import seeded_secrets
from tests.fast_home import fast_home

_FRAGMENT = 6
_LOOPBACK = "http://127.0.0.1:8081/v1"


def _credential(label: str) -> str:
    # why: these letters occur in no message, so a leak check that ignores case finds only the credential
    alphabet = "FGJQVZ0123456789"
    rng = seeded_secrets.generator(f"malformed-config:{label}")
    return "sk-" + "".join(rng.choice(alphabet) for _ in range(32))


def _leaks(text: str, credential: str) -> bool:
    body = credential[3:].lower()
    lowered = text.lower()
    return any(body[i : i + _FRAGMENT] in lowered for i in range(len(body) - _FRAGMENT + 1))


def _valid_model(credential: str) -> dict:
    return {"id": "sample-model", "contextWindow": 32768, "maxTokens": 4096, "note": credential}


def _valid_provider(credential: str) -> dict:
    return {
        "baseUrl": _LOOPBACK,
        "apiKey": credential,
        "api": "openai-completions",
        "models": [_valid_model(credential)],
    }


def _wrong_values(credential: str) -> list:
    return [None, True, 0, -1, 1.5, 10**30, credential, "", [], [credential], {}, {"k": credential}]


def _documents(credential: str) -> Iterator[tuple[str, bytes, bool]]:  # noqa: C901, PLR0912
    def dump(doc) -> bytes:
        return json.dumps(doc).encode("utf-8")

    cred = credential.encode()
    # why: the credential sits next to the bad bytes, so an echoed decode error would leak it
    for name, raw in {
        "latin1-byte": b'{"providers": {"p": {"apiKey": "' + cred + b'\xff"}}}',
        "lone-continuation": b'{"k": "' + cred + b'\x80"}',
        "truncated-utf8": b'{"k": "' + cred + b'\xe2\x82"}',
        "utf16-bom": '{"k": "x"}'.encode("utf-16"),
        "overlong-nul": b'{"k": "\xc0\x80' + cred + b'"}',
        "invalid-syntax": b'{"apiKey": "' + cred + b'",,}',
        "trailing-garbage": dump({"k": credential}) + b" " + cred,
        "empty-file": b"",
    }.items():
        yield f"encoding:{name}", raw, True
    # why: json.loads accepts a lone surrogate escape, yet no terminal can print the resulting str
    lone, escaped = chr(0xD800), chr(0xDCFF)

    def with_provider(**fields) -> dict:
        return {"providers": {"p": {**_valid_provider(credential), **fields}}}

    def with_model(**fields) -> dict:
        return with_provider(models=[{**_valid_model(credential), **fields}])

    for name, doc in {
        "model-id": with_model(id=f"bad{lone}id"),
        "model-id-escaped-byte": with_model(id=f"m{escaped}"),
        "provider-name": {"providers": {f"p{lone}": _valid_provider(credential)}},
        "baseUrl": with_provider(baseUrl=_LOOPBACK + lone),
        "apiKey": with_provider(apiKey=credential + lone),
        "api": with_provider(api=f"openai{escaped}"),
        "reasoning": with_model(reasoning=f"off{lone}"),
        "unread-field": with_provider(note=f"x{lone}"),
        "unread-key": with_provider(**{f"k{escaped}": 1}),
    }.items():
        yield f"surrogate:{name}", dump(doc), True
    yield "surrogate:utf8-encoded", b'{"providers": {"p": {"models": [{"id": "m\xed\xa0\x80"}]}}}', True
    for name, literal in {
        "5000-digit-int": b"9" * 5000,
        "neg-5000-digit-int": b"-" + b"9" * 5000,
        "20-digit-int": b"9" * 20,
        "float-overflow": b"1e400",
        "neg-float-overflow": b"-1e400",
        "nan": b"NaN",
        "infinity": b"Infinity",
    }.items():
        yield f"number:{name}:extra", b'{"apiKey": "' + cred + b'", "x": ' + literal + b"}", True
        yield (
            f"number:{name}:contextWindow",
            (
                b'{"providers": {"p": {"baseUrl": "' + _LOOPBACK.encode() + b'", "apiKey": "' + cred + b'", '
                b'"models": [{"id": "m", "contextWindow": ' + literal + b"}]}}}"
            ),
            True,
        )
    yield "nesting:200k-list", b"[" * 200_000, True
    yield "nesting:100k-object", b'{"a":' * 100_000, True
    deep: dict = {"apiKey": credential}
    for _ in range(local_llm.MAX_JSON_DEPTH + 1):
        deep = {"d": deep}
    provider = _valid_provider(credential)
    provider["extra"] = deep
    yield "nesting:past-bound", dump({"providers": {"p": provider}}), True
    for index, value in enumerate(_wrong_values(credential)):
        yield f"type:top:{index}", dump(value), not isinstance(value, dict)
        if value is not None and not isinstance(value, dict):
            yield f"type:providers:{index}", dump({"providers": value}), True
        if not isinstance(value, dict):
            yield f"type:provider:{index}", dump({"providers": {"p": value}}), True
        for key in ("baseUrl", "apiKey", "api"):
            if not isinstance(value, str):
                provider = _valid_provider(credential)
                provider[key] = value
                yield f"type:provider.{key}:{index}", dump({"providers": {"p": provider}}), True
        if value is not None and not isinstance(value, list):
            provider = _valid_provider(credential)
            provider["models"] = value
            yield f"type:models:{index}", dump({"providers": {"p": provider}}), True
        if not isinstance(value, dict):
            provider = _valid_provider(credential)
            provider["models"] = [value]
            yield f"type:model:{index}", dump({"providers": {"p": provider}}), True
        for key in ("id", "contextWindow", "maxTokens", "reasoning"):
            accepted = (key == "reasoning" and isinstance(value, bool | str)) or (key == "id" and value == credential)
            if accepted:
                continue
            model = _valid_model(credential)
            model[key] = value
            provider = _valid_provider(credential)
            provider["models"] = [model]
            yield f"type:model.{key}:{index}", dump({"providers": {"p": provider}}), True
    # why: missing fields may be accepted (synthesised fallback or env window) or rejected, but never crash
    for name, doc in {
        "no-providers": {"apiKey": credential},
        "empty-providers": {"providers": {}},
        "provider-no-baseUrl": {"providers": {"p": {"apiKey": credential, "models": [{"id": "m"}]}}},
        "provider-no-models": {"providers": {"p": {"baseUrl": _LOOPBACK, "apiKey": credential}}},
        "model-no-id": {"providers": {"p": {"baseUrl": _LOOPBACK, "models": [{"contextWindow": 32768}]}}},
        "model-no-window": {"providers": {"p": {"baseUrl": _LOOPBACK, "apiKey": credential, "models": [{"id": "m"}]}}},
        "remote-only": {"providers": {"cloud": {"baseUrl": "https://api.example.invalid/v1", "apiKey": credential}}},
    }.items():
        yield f"missing:{name}", dump(doc), name == "model-no-id"


_NUMERIC_VARS = (
    *budgets.ENV_STAGE_BUDGET_MINUTES.values(),
    budgets.ENV_STAGE_BUDGET_SECONDS,
    budgets.ENV_TURN_TIMEOUT,
    budgets.ENV_TURN_STALL,
    local_llm.ENV_CONTEXT_RESERVE_TOKENS,
    local_llm.ENV_LOCAL_CONTEXT_WINDOW,
)
_ENVELOPE_VARS = (local_llm.ENV_CONTEXT_RESERVE_TOKENS, local_llm.ENV_LOCAL_CONTEXT_WINDOW)


def _numeric_values(credential: str) -> list[str]:
    return [
        "1e308",
        "1.7976931348623157e308",
        "inf",
        "-inf",
        "nan",
        "-1",
        "0",
        "1e-320",
        "9" * 5000,
        "604801",
        "10081",
        "4294967296",
        "0x10",
        "1_0",
        " 12 ",
        # why: only the exact (case-insensitive, trimmed) word means "no ceiling"
        "unlimited",
        " UnLimited\t",
        "unlimitedd",
        "UNLIMIT",
        "un limited",
        "unlimited inf",
        credential,
        "12" + os.fsdecode(b"\xff"),
        os.fsdecode(b"\xed\xa0\x80"),
    ]


# why: plain stand-ins built once, since building per-call MagicMocks was a large share of each case's cost
_PROBE = PreflightResult(
    status=PreflightStatus.OK,
    agent="pi-agent",
    model="m",
    image="holon/agent-pi",
    exit_code=0,
    output_excerpt="",
    hint="",
)


def _probe(*_args, **_kwargs) -> PreflightResult:
    return _PROBE


def _docker_only(name: str, *_args, **_kwargs) -> str | None:
    return "/usr/bin/docker" if name == "docker" else None


def _no_mounts(*_args, **_kwargs) -> list[str]:
    return []


def _no_token_reduction(*_args, **_kwargs) -> tuple[list[str], dict[str, str]]:
    return [], {}


class _DockerRun:
    def __init__(self) -> None:
        self.argv: list[str] = []

    def __call__(self, cmd, *_args, **_kwargs) -> subprocess.CompletedProcess:
        self.argv = list(cmd)
        return subprocess.CompletedProcess(cmd, 0)


class _Workspace:
    def __init__(self, root: str, models: bytes | None, extra_env: dict[str, str], credential: str) -> None:
        self.root = root
        self.agent_dir = os.path.join(root, "agent")
        os.makedirs(self.agent_dir, exist_ok=True)
        path = os.path.join(self.agent_dir, "models.json")
        if models is None:
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)
        else:
            with open(path, "wb") as handle:
                handle.write(models)
        self.env = {
            "HOME": root,
            local_llm.ENV_PI_AGENT_DIR: self.agent_dir,
            local_llm.ENV_LOCAL_MODE: "1",
            local_llm.ENV_LOCAL_BASE_URL: f"http://user:{credential}@localhost:8082/v1",
            local_llm.ENV_LOCAL_MODELS: "sample-synth",
            local_llm.ENV_LOCAL_CONTEXT_WINDOW: "32768",
            "GITHUB_TOKEN": "sample-placeholder",
            **extra_env,
        }

    def scrub(self, text: str) -> str:
        return text.replace(self.root, "<HOME>")


@contextlib.contextmanager
def _captured_logs() -> Iterator[io.TextIOWrapper]:
    buffer = _strict_stream()
    handler = logging.StreamHandler(buffer)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield buffer
    finally:
        root.removeHandler(handler)


def _deep_health(_url: str, _timeout: float) -> bytes:
    return b"[" * 200_000


def _strict_stream() -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict", write_through=True)


def _text(stream: io.TextIOWrapper) -> str:
    stream.flush()
    return stream.buffer.getvalue().decode("utf-8")  # type: ignore[attr-defined]


def _run_doctor(ws: _Workspace, as_json: bool, fetch: Callable[[str, float], bytes]) -> tuple[int, str]:
    out, err = _strict_stream(), _strict_stream()
    args = argparse.Namespace(agent="pi", model=None, json=as_json, timeout=None)
    with (
        _captured_logs() as logs,
        patch.dict(os.environ, ws.env, clear=True),
        patch.object(cli_doctor, "run_preflight_probe", _probe),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        code = cli.run_doctor(args, fetch=fetch)
    stdout = _text(out)
    if as_json:
        payload = json.loads(stdout)
        assert set(payload["environment"]) == {"ok", "lines", "warnings"}
    return code, ws.scrub(stdout + _text(err) + _text(logs))


def _run_path(ws: _Workspace) -> tuple[int, str, list[str]]:
    out, err = _strict_stream(), _strict_stream()
    stage_dir = os.path.join(ws.root, "stage-agent-dir")
    os.makedirs(stage_dir, exist_ok=True)
    docker = _DockerRun()
    with (
        _captured_logs() as logs,
        patch.dict(os.environ, ws.env, clear=True),
        patch("shutil.which", _docker_only),
        patch("subprocess.run", docker),
        patch("sandbox_executor.launcher.mounts.get_agent_session_mounts", _no_mounts),
        patch.object(cli.git_flow, "sandbox_worktree_mounts", _no_mounts),
        patch("sandbox_executor.launcher.sidecar.get_token_reduction_mounts_and_envs", _no_token_reduction),
        patch.object(tempfile, "mkdtemp", lambda *_args, **_kwargs: stage_dir),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        timer = budgets.StageTimer("execute", budgets.load_budgets(os.environ).stage_seconds("execute"))
        with budgets.active_stage_timer(timer):
            code = cli.run_docker_container(
                "planner", "holon/agent-pi", ["branch", "pi-agent", "m"], agent_id="pi", token_reduce=False
            )
    return code, ws.scrub(_text(out) + _text(err) + _text(logs)), docker.argv


def _check(case: str, failures: list[str], body: Callable[[], None]) -> None:
    try:
        body()
    except AssertionError as exc:
        failures.append(f"{case}: {exc}")
    # why: the property under test is that nothing escapes
    except BaseException as exc:
        failures.append(f"{case}: raised {type(exc).__name__}")


def _handed_down(argv: list[str]) -> list[str]:
    prefix = f"{budgets.ENV_STAGE_BUDGET_SECONDS}="
    return [arg[len(prefix) :] for arg in argv if arg.startswith(prefix)]


def _assert_handdown_finite(argv: list[str]) -> None:
    for value in _handed_down(argv):
        if value == budget_limits.UNLIMITED:
            continue
        assert value.isdigit() and int(value) >= 1, "hand-down is not a positive integer or 'unlimited'"


def test_malformed_models_json_never_crashes_doctor_or_run_path():
    credential = _credential("malformed_models_json_never_crashes_doctor_or_run_path")
    failures: list[str] = []
    cases = 0
    with fast_home() as root:
        for case, raw, must_reject in _documents(credential):
            cases += 1
            ws = _Workspace(root, raw, {}, credential)

            def doctor(as_json: bool, ws=ws, case=case, must_reject=must_reject) -> None:
                code, output = _run_doctor(ws, as_json, _deep_health)
                assert code in (0, 1), f"doctor exit {code}"
                assert "Traceback" not in output, "traceback in output"
                assert not _leaks(output, credential), "credential fragment in doctor output"
                if must_reject:
                    assert code == 1 and "Error: " in output, "malformed document accepted by doctor"

            def run(ws=ws, must_reject=must_reject) -> None:
                code, output, argv = _run_path(ws)
                assert "Traceback" not in output, "traceback in output"
                assert not _leaks(output, credential), "credential fragment in run-path output"
                if must_reject:
                    assert code == 1 and "Error: " in output, "malformed document accepted by run path"
                elif code != 0:
                    assert "Error: " in output, "non-zero run path without a clean error"
                _assert_handdown_finite(argv)

            _check(f"{case}/doctor-text", failures, lambda: doctor(False))
            _check(f"{case}/doctor-json", failures, lambda: doctor(True))
            _check(f"{case}/run", failures, run)
    assert cases >= 150, f"matrix shrank to {cases} documents"
    assert not failures, f"{len(failures)} failures ({seeded_secrets.seed_note()}):\n" + "\n".join(failures)


def test_malformed_numeric_env_never_crashes_doctor_or_run_path():
    credential = _credential("malformed_numeric_env_never_crashes_doctor_or_run_path")
    failures: list[str] = []
    valid = json.dumps({"providers": {"p": _valid_provider(credential)}}).encode()
    with fast_home() as root:
        for var in _NUMERIC_VARS:
            for index, value in enumerate(_numeric_values(credential)):
                case = f"env:{var}:{index}"
                ws = _Workspace(root, valid, {var: value}, credential)

                def doctor(as_json: bool, ws=ws) -> None:
                    code, output = _run_doctor(ws, as_json, _deep_health)
                    assert code in (0, 1), f"doctor exit {code}"
                    assert "Traceback" not in output, "traceback in output"
                    assert "inf" not in output.replace("info", "").lower(), "non-finite value in doctor output"
                    assert "nan" not in output.lower(), "NaN in doctor output"
                    assert not _leaks(output, credential), "credential fragment in doctor output"

                def run(ws=ws, var=var) -> None:
                    code, output, argv = _run_path(ws)
                    assert "Traceback" not in output, "traceback in output"
                    if var in _ENVELOPE_VARS and code != 0:
                        assert code == 1 and "Error: " in output, "envelope value without a clean error"
                    elif var not in _ENVELOPE_VARS:
                        assert code == 0, f"budget value broke the run path (exit {code})"
                    _assert_handdown_finite(argv)
                    for printed in ("Error: ", "Warning: ", "Ignoring "):
                        for line in output.splitlines():
                            if printed in line:
                                assert not _leaks(line, credential), "credential fragment in a message"

                _check(f"{case}/doctor-text", failures, lambda: doctor(False))
                _check(f"{case}/doctor-json", failures, lambda: doctor(True))
                _check(f"{case}/run", failures, run)
    assert not failures, f"{len(failures)} failures ({seeded_secrets.seed_note()}):\n" + "\n".join(failures)


_STRING_VARS = (
    local_llm.ENV_LOCAL_BASE_URL,
    local_llm.ENV_LOCAL_PROVIDER,
    local_llm.ENV_LOCAL_MODELS,
    local_llm.ENV_HOST_LOCAL_HOSTS,
    local_llm.ENV_REASONING_MODE,
    local_llm.ENV_AGENT_PROVIDER,
)


def _unencodable_values(var: str) -> list[str]:
    # why: a lone surrogate cannot reach os.environ, so the bytes of a UTF-8-encoded surrogate stand in for it
    base = {
        local_llm.ENV_LOCAL_BASE_URL: "http://localhost:8082/v1",
        local_llm.ENV_HOST_LOCAL_HOSTS: "localhost:8082",
        local_llm.ENV_REASONING_MODE: "off",
    }.get(var, "sample-value")
    values = []
    for bad in (os.fsdecode(b"\xff"), os.fsdecode(b"\x80"), os.fsdecode(b"\xed\xa0\x80")):
        values.extend((base + bad, base[:4] + bad + base[4:]))
    return values


def test_unencodable_string_env_never_crashes_doctor_or_run_path():
    credential = _credential("unencodable_string_env_never_crashes_doctor_or_run_path")
    failures: list[str] = []
    valid = json.dumps({"providers": {"p": _valid_provider(credential)}}).encode()
    with fast_home() as root:
        for models in (None, valid):
            source = "synthesised" if models is None else "models.json"
            for var in (*_STRING_VARS, local_llm.ENV_LOCAL_MODE):
                for index, value in enumerate(_unencodable_values(var)):
                    case = f"string-env:{source}:{var}:{index}"
                    ws = _Workspace(root, models, {var: value}, credential)

                    def doctor(as_json: bool, ws=ws, var=var) -> None:
                        code, output = _run_doctor(ws, as_json, _deep_health)
                        assert code in (0, 1), f"doctor exit {code}"
                        assert "Traceback" not in output, "traceback in output"
                        assert not _leaks(output, credential), "credential fragment in doctor output"
                        if var in _STRING_VARS:
                            assert code == 1 and f"Error: {var} " in output, "unencodable value accepted by doctor"

                    def run(ws=ws, var=var) -> None:
                        code, output, argv = _run_path(ws)
                        assert "Traceback" not in output, "traceback in output"
                        if var in _STRING_VARS:
                            assert code == 1 and f"Error: {var} " in output, "unencodable value accepted by run path"
                        _assert_handdown_finite(argv)

                    _check(f"{case}/doctor-text", failures, lambda: doctor(False))
                    _check(f"{case}/doctor-json", failures, lambda: doctor(True))
                    _check(f"{case}/run", failures, run)
    assert not failures, f"{len(failures)} failures ({seeded_secrets.seed_note()}):\n" + "\n".join(failures)
