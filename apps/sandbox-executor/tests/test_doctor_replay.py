"""Proof that ``tests/doctor_replay.py`` replays only what is identical, and that the replay keeps callers apart.

The redaction matrix (``tests/test_url_credential_redaction.py``) computes the configuration section of ``holon
doctor`` once per case and replays it for the other doctor runs of that case. The first identity test here runs the
matrix's own ``_doctor`` without that replay and compares what the real ``cli._configuration_section`` returns in each
of its eight call contexts. If production makes the section depend on the host configuration, the fetcher or
``--json``, it fails, and the replay must be removed from the matrix.

The matrix also runs the environment report (``cli.report_budgets_and_envelope``) only once for the two doctor runs of
one block of a case and replays its text and exit code for the second one. The second identity test runs ``_doctor``
without that replay and compares everything the three real reports of each block do (the direct one and those of the
two doctor runs): the text, the exit code, the log lines, the gateway probes and any other output. If production
makes the report depend on how often it ran or on who called it, it fails, and that replay must be removed.
"""

import argparse
import contextlib
import io
import itertools
import logging
import os
import shutil
import socket
import sys

import pytest
from sandbox_executor import cli, config_env, local_llm

from tests import seeded_secrets
from tests import test_url_credential_redaction as redaction_matrix
from tests.doctor_replay import replayed_configuration_section, replayed_environment_report

SAMPLED_SHAPES = ("plain", "double-slash", "percent-encoded", "digits1-slash", "digits3-fragment", "digits7-colon")
DOCTOR_ARGS = argparse.Namespace(agent="antigravity-agent", model="gemini-3.8-flash-medium", json=False, timeout=None)


def _refuse(*_args, **_kwargs):
    raise OSError("connection refused")


@pytest.fixture
def empty_environment(monkeypatch):
    """The process state the redaction matrix runs its cases in: no tools, no sockets, an empty environment."""
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    for name in list(os.environ):
        monkeypatch.delenv(name)


def _sampled_values() -> list[tuple[str, str]]:
    """``(case_id, value)`` for every prefix with six credential shapes; userinfo, host and suffix rotate."""
    rng = seeded_secrets.generator("doctor-replay:identity")
    shapes = redaction_matrix._credential_shapes()
    rotation = itertools.cycle(
        itertools.product(
            redaction_matrix.USERINFO.items(), redaction_matrix.HOSTS.items(), redaction_matrix.SUFFIXES.items()
        )
    )
    values = []
    for (p_id, prefix), c_id in itertools.product(redaction_matrix.PREFIXES.items(), SAMPLED_SHAPES):
        (u_id, userinfo), (h_id, host), (s_id, suffix) = next(rotation)
        credential = shapes[c_id](rng)
        values.append(
            (f"{p_id}/{u_id}/{c_id}/{h_id}/{s_id}", f"{prefix}{userinfo.format(c=credential)}@{host}{suffix}")
        )
    return values


def test_sample_covers_every_prefix_both_userinfo_forms_and_six_shapes():
    parts = [case_id.split("/") for case_id, _value in _sampled_values()]

    assert {p[0] for p in parts} == set(redaction_matrix.PREFIXES)
    assert {p[1] for p in parts} == set(redaction_matrix.USERINFO)
    assert {p[2] for p in parts} == set(SAMPLED_SHAPES)
    assert len(SAMPLED_SHAPES) >= 6


def test_configuration_section_is_identical_in_all_eight_doctor_contexts_of_a_case(monkeypatch, empty_environment):
    real = cli._configuration_section
    calls: list[tuple[tuple, tuple[bool, bool]]] = []

    def recording(args):
        section = real(args)
        calls.append((section, (local_llm.host_models_json() is None, args.json)))
        return section

    # The matrix's own `_doctor`, with the replay switched off, so every one of its doctor runs computes the section.
    monkeypatch.setattr(redaction_matrix, "replayed_configuration_section", contextlib.nullcontext)
    monkeypatch.setattr(cli, "_configuration_section", recording)
    differing: list[str] = []
    for case_id, value in _sampled_values():
        calls.clear()
        redaction_matrix._doctor(value)
        sections = [section for section, _context in calls]
        contexts = sorted(context for _section, context in calls)
        # Two fetchers for each of: host configuration absent or present, text or `--json`.
        assert contexts == sorted([*itertools.product((False, True), (False, True))] * 2), case_id
        if any(section != sections[0] for section in sections):
            differing.append(case_id)

    assert not differing, (
        f"the configuration section differs between the doctor runs of one case, so it must not be replayed: "
        f"{differing} {seeded_secrets.seed_note()}"
    )


def test_matrix_doctor_computes_the_section_once_per_case(monkeypatch, empty_environment):
    real = cli._configuration_section
    computed: list[argparse.Namespace] = []

    def counting(args):
        computed.append(args)
        return real(args)

    monkeypatch.setattr(cli, "_configuration_section", counting)

    redaction_matrix._doctor("http://alice:FGJQVZFGJQVZ@localhost:8081/v1")
    redaction_matrix._doctor("http://alice:ZVQJGFZVQJGF@localhost:8081/v1")

    assert len(computed) == 2, "one real computation per case; nothing is carried from one case to the next"


@pytest.fixture
def counted_section(monkeypatch):
    """Stand in for the real section with one that counts its calls and returns a fresh value built from the key."""
    calls: list[tuple[str | None, str | None, str | None]] = []

    def section(args):
        calls.append((os.environ.get("HOLON_LOCAL_BASE_URL"), args.agent, args.model))
        return {"settings": [{"key": "HOLON_LOCAL_BASE_URL", "shadowed": []}], "call": len(calls)}, ["heading"]

    monkeypatch.setattr(cli, "_configuration_section", section)
    return calls


def test_replay_computes_once_per_environment_agent_and_model(counted_section, monkeypatch):
    monkeypatch.setenv("HOLON_LOCAL_BASE_URL", "one")
    other_agent = argparse.Namespace(agent="pi-agent", model=DOCTOR_ARGS.model)
    other_model = argparse.Namespace(agent=DOCTOR_ARGS.agent, model="m")

    with replayed_configuration_section():
        first = [cli._configuration_section(DOCTOR_ARGS) for _ in range(3)]
        monkeypatch.setenv("HOLON_LOCAL_BASE_URL", "two")
        second = [cli._configuration_section(DOCTOR_ARGS) for _ in range(2)]
        monkeypatch.setenv("HOLON_UNRELATED", "x")
        cli._configuration_section(DOCTOR_ARGS)
        cli._configuration_section(other_agent)
        cli._configuration_section(other_model)
        monkeypatch.delenv("HOLON_UNRELATED")
        again = cli._configuration_section(DOCTOR_ARGS)

    assert counted_section == [
        ("one", DOCTOR_ARGS.agent, DOCTOR_ARGS.model),
        ("two", DOCTOR_ARGS.agent, DOCTOR_ARGS.model),
        ("two", DOCTOR_ARGS.agent, DOCTOR_ARGS.model),
        ("two", "pi-agent", DOCTOR_ARGS.model),
        ("two", DOCTOR_ARGS.agent, "m"),
    ]
    assert [section[0]["call"] for section in first] == [1, 1, 1]
    assert [section[0]["call"] for section in second] == [2, 2]
    assert again[0]["call"] == 2, "the same environment again is the same key"


def test_each_context_starts_with_an_empty_store(counted_section):
    for _case in range(3):
        with replayed_configuration_section():
            cli._configuration_section(DOCTOR_ARGS)
            cli._configuration_section(DOCTOR_ARGS)

    assert len(counted_section) == 3


def test_a_caller_that_mutates_its_result_does_not_change_the_next_callers(counted_section):
    with replayed_configuration_section():
        payload, lines = cli._configuration_section(DOCTOR_ARGS)
        payload["settings"][0]["shadowed"].append("mutated")
        payload["leak"] = True
        lines.append("mutated")
        next_payload, next_lines = cli._configuration_section(DOCTOR_ARGS)

    assert next_payload == {"settings": [{"key": "HOLON_LOCAL_BASE_URL", "shadowed": []}], "call": 1}
    assert next_lines == ["heading"]
    assert next_payload is not payload and next_lines is not lines


def test_a_remembered_resolution_is_never_replayed(counted_section):
    config_env.remember(config_env.load_effective_config())

    with replayed_configuration_section():
        results = [cli._configuration_section(DOCTOR_ARGS) for _ in range(3)]

    assert len(counted_section) == 3
    assert [payload["call"] for payload, _lines in results] == [1, 2, 3]


def test_the_original_function_is_back_after_the_context(counted_section):
    original = cli._configuration_section

    with replayed_configuration_section():
        assert cli._configuration_section is not original
    with pytest.raises(RuntimeError, match="inside"), replayed_configuration_section():
        raise RuntimeError("inside")

    assert cli._configuration_section is original


def _recorded_environment_reports(monkeypatch) -> list[tuple[tuple, tuple]]:
    """Record ``(context, everything observable)`` of every real environment report; the matrix replay is off."""
    real, real_fetchers = cli.report_budgets_and_envelope, redaction_matrix._fetchers
    reports: list[tuple[tuple, tuple]] = []
    probes: list[tuple[str, float]] = []

    def probe_recording(fetch):
        def recorded(url: str, timeout: float) -> bytes:
            probes.append((url, timeout))
            return fetch(url, timeout)

        recorded.__name__ = fetch.__name__
        return recorded

    def recording(fetch, out=None):
        # Inside the matrix's `_captured`, `sys.stdout` and `sys.stderr` are buffers.
        streams = (out or sys.stdout, sys.stdout, sys.stderr)
        starts = [len(stream.getvalue()) for stream in streams]
        first_probe = len(probes)
        logged = redaction_matrix._Capture()
        logger = logging.getLogger("sandbox_executor")
        logger.addHandler(logged)
        try:
            # The caller's own arguments: the report sees the same fetcher object and stream as in the matrix.
            code = real(fetch=fetch, out=out)
        finally:
            logger.removeHandler(logged)
        text, stdout, stderr = (stream.getvalue()[start:] for stream, start in zip(streams, starts, strict=True))
        beside_the_report = (stdout if out is not None else "", stderr)
        context = (local_llm.host_models_json() is None, fetch.__name__, out is None)
        reports.append((context, (code, text, beside_the_report, logged.lines, probes[first_probe:])))
        return code

    monkeypatch.setattr(redaction_matrix, "replayed_environment_report", contextlib.nullcontext)
    # One recording fetcher per fetcher of a block, so the three calls of the block share it as they do in the matrix.
    monkeypatch.setattr(redaction_matrix, "_fetchers", lambda seen: tuple(map(probe_recording, real_fetchers(seen))))
    monkeypatch.setattr(cli, "report_budgets_and_envelope", recording)
    return reports


def test_environment_report_is_identical_in_the_three_calls_of_every_doctor_block(monkeypatch, empty_environment):
    reports = _recorded_environment_reports(monkeypatch)
    differing: list[str] = []
    probing_blocks = logging_blocks = 0
    for case_id, value in _sampled_values():
        reports.clear()
        redaction_matrix._doctor(value)
        blocks = [reports[start : start + 3] for start in range(0, len(reports), 3)]
        # Host configuration absent or present, each with the responding and the failing fetcher.
        assert [block[0][0][:2] for block in blocks] == [*itertools.product((True, False), ("ok", "fail"))], case_id
        for block in blocks:
            (host_absent, fetcher, _direct), first = block[0]
            # The direct call on stdout first, then the two doctor runs, which pass their own stream.
            assert [context for context, _observed in block] == [
                (host_absent, fetcher, True),
                (host_absent, fetcher, False),
                (host_absent, fetcher, False),
            ], case_id
            if any(observed != first for _context, observed in block):
                differing.append(f"{case_id} host_absent={host_absent} fetcher={fetcher}")
            probing_blocks += bool(first[4])
            logging_blocks += bool(first[3])

    assert not differing, (
        f"the environment report differs between the three calls of one doctor block, so it must not be replayed: "
        f"{differing} {seeded_secrets.seed_note()}"
    )
    assert probing_blocks and logging_blocks, "the sample must reach the gateway probe and a logged line"


def test_environment_replay_changes_only_how_often_the_matrix_sees_a_text(monkeypatch, empty_environment):
    differing: list[str] = []
    for case_id, value in _sampled_values():
        with monkeypatch.context() as without_replay:
            without_replay.setattr(redaction_matrix, "replayed_environment_report", contextlib.nullcontext)
            every_report_real = redaction_matrix._doctor(value)
        replayed = redaction_matrix._doctor(value)
        if set(replayed) != set(every_report_real) or len(replayed) >= len(every_report_real):
            differing.append(case_id)

    assert not differing, f"{differing} {seeded_secrets.seed_note()}"


def test_matrix_doctor_runs_the_environment_report_twice_per_block(monkeypatch, empty_environment):
    real = cli.report_budgets_and_envelope
    ran: list[tuple[bool, bool]] = []

    def counting(fetch=None, out=None):
        ran.append((local_llm.host_models_json() is None, out is None))
        return real(fetch=fetch, out=out)

    monkeypatch.setattr(cli, "report_budgets_and_envelope", counting)

    redaction_matrix._doctor("http://alice:FGJQVZFGJQVZ@localhost:8081/v1")
    redaction_matrix._doctor("http://alice:ZVQJGFZVQJGF@localhost:8081/v1")

    # Per block: the direct call on stdout, then one report for the two doctor runs. Nothing is carried further.
    per_block = [(True, True), (True, False)] * 2 + [(False, True), (False, False)] * 2
    assert ran == per_block * 2


@pytest.fixture
def counted_report(monkeypatch):
    """Stand in for the real environment report with one that counts its runs and writes and returns the count."""
    runs: list[tuple[str | None, object]] = []

    def report(fetch=None, out=None):
        runs.append((os.environ.get("HOLON_LOCAL_BASE_URL"), fetch))
        print(f"report {len(runs)}", file=out or sys.stdout)
        return len(runs)

    monkeypatch.setattr(cli, "report_budgets_and_envelope", report)
    return runs


def test_environment_replay_runs_once_per_environment_host_configuration_and_fetcher(counted_report, monkeypatch):
    def fetch(url: str, timeout: float) -> bytes:
        return b"{}"

    def other_fetch(url: str, timeout: float) -> bytes:
        return b"{}"

    monkeypatch.setenv("HOLON_LOCAL_BASE_URL", "one")
    with replayed_environment_report():
        first = [cli.report_budgets_and_envelope(fetch=fetch, out=io.StringIO()) for _ in range(3)]
        cli.report_budgets_and_envelope(fetch=other_fetch, out=io.StringIO())
        monkeypatch.setenv("HOLON_LOCAL_BASE_URL", "two")
        cli.report_budgets_and_envelope(fetch=fetch, out=io.StringIO())
        with monkeypatch.context() as other_host_configuration:
            other_host_configuration.setattr(local_llm, "host_models_json", lambda: {"providers": {}})
            cli.report_budgets_and_envelope(fetch=fetch, out=io.StringIO())
        again = cli.report_budgets_and_envelope(fetch=fetch, out=io.StringIO())

    assert counted_report == [("one", fetch), ("one", other_fetch), ("two", fetch), ("two", fetch)]
    assert first == [1, 1, 1]
    assert again == 3, "the same environment, host configuration and fetcher again is the same key"


def test_a_report_without_a_stream_of_its_own_is_never_replayed(counted_report, capsys):
    with replayed_environment_report():
        on_stdout = [cli.report_budgets_and_envelope() for _ in range(2)]
        with_a_stream = cli.report_budgets_and_envelope(out=io.StringIO())
        on_stdout.append(cli.report_budgets_and_envelope())

    assert on_stdout == [1, 2, 4], "every call on stdout runs the report"
    assert with_a_stream == 3, "and a call with a stream is not answered from a call on stdout"
    assert capsys.readouterr().out == "report 1\nreport 2\nreport 4\n"


def test_every_caller_with_a_stream_gets_the_report_text_on_it_and_the_exit_code(counted_report, capsys):
    streams = [io.StringIO(), io.StringIO(), io.StringIO()]

    with replayed_environment_report():
        codes = [cli.report_budgets_and_envelope(out=stream) for stream in streams]

    assert codes == [1, 1, 1]
    assert len(counted_report) == 1
    assert [stream.getvalue() for stream in streams] == ["report 1\n"] * 3
    assert capsys.readouterr().out == "", "the text goes to the caller's stream and nowhere else"


def test_each_environment_replay_context_starts_empty_and_restores_the_report(counted_report):
    original = cli.report_budgets_and_envelope

    for _block in range(3):
        with replayed_environment_report():
            assert cli.report_budgets_and_envelope is not original
            cli.report_budgets_and_envelope(out=io.StringIO())
            cli.report_budgets_and_envelope(out=io.StringIO())
    with pytest.raises(RuntimeError, match="inside"), replayed_environment_report():
        raise RuntimeError("inside")

    assert len(counted_report) == 3
    assert cli.report_budgets_and_envelope is original
