"""Proof that ``tests/doctor_replay.py`` replays only what is identical, and that the replay keeps callers apart.

The redaction matrix (``tests/test_url_credential_redaction.py``) computes the configuration section of ``holon
doctor`` once per case and replays it for the other doctor runs of that case. The first identity test here runs the
matrix's own ``_doctor`` without that replay and compares what the real ``cli_doctor._configuration_section`` returns
in each of its eight call contexts. If production makes the section depend on the host configuration, the fetcher or
``--json``, it fails, and the replay must be removed from the matrix.

The matrix also runs the environment report (``cli_doctor.report_budgets_and_envelope``) only once for the direct call
and the two doctor runs of one block of a case and replays its text and exit code for the others. The second identity
test runs ``_doctor`` without that replay (and without the direct-call one) and compares everything the three real
reports of each block do (the direct one and two doctor runs): the text, the exit code, the log lines, the gateway
probes and any other output. If production makes the report depend on how often it ran or on who called it, it fails,
and that replay must be removed.

The invalid-value matrix of the same file (``_invalid_value_doctor``) uses both replays too, in another context: an
isolated ``HOME``, a docker stand-in, an invalid value in a budget, envelope or host models setting, and a report on
a stream of its own before the text and the ``--json`` doctor run. It has its own two identity tests here, over a
sample of its own cases that holds every invalid-value slot with every URL prefix.

A worker that ran ``cli.main`` earlier still has the CLI's log handler, which repeats every log line on stderr. The
tests of the environment report therefore run in both states (``cli_log_handler``), whatever ran before them.
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
from sandbox_executor.cli import doctor as cli_doctor

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


@pytest.fixture(params=[False, True], ids=["no-cli-log-handler", "cli-log-handler"])
def cli_log_handler(request, monkeypatch) -> bool:
    """Both states ``cli.configure_cli_logging`` of an earlier test can leave: without and with its stderr handler."""
    package_logger = logging.getLogger(cli.PACKAGE_LOGGER_NAME)
    handlers = [handler for handler in package_logger.handlers if not isinstance(handler, cli._CurrentStderrHandler)]
    if request.param:
        handlers.append(cli._CurrentStderrHandler())
    monkeypatch.setattr(package_logger, "handlers", handlers)
    return request.param


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
    real = cli_doctor._configuration_section
    calls: list[tuple[tuple, tuple[bool, bool]]] = []

    def recording(args):
        section = real(args)
        calls.append((section, (local_llm.host_models_json() is None, args.json)))
        return section

    # The matrix's own `_doctor`, with the replay switched off, so every one of its doctor runs computes the section.
    monkeypatch.setattr(redaction_matrix, "replayed_configuration_section", contextlib.nullcontext)
    monkeypatch.setattr(cli_doctor, "_configuration_section", recording)
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
    real = cli_doctor._configuration_section
    computed: list[argparse.Namespace] = []

    def counting(args):
        computed.append(args)
        return real(args)

    monkeypatch.setattr(cli_doctor, "_configuration_section", counting)

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

    monkeypatch.setattr(cli_doctor, "_configuration_section", section)
    return calls


def test_replay_computes_once_per_environment_agent_and_model(counted_section, monkeypatch):
    monkeypatch.setenv("HOLON_LOCAL_BASE_URL", "one")
    other_agent = argparse.Namespace(agent="pi-agent", model=DOCTOR_ARGS.model)
    other_model = argparse.Namespace(agent=DOCTOR_ARGS.agent, model="m")

    with replayed_configuration_section():
        first = [cli_doctor._configuration_section(DOCTOR_ARGS) for _ in range(3)]
        monkeypatch.setenv("HOLON_LOCAL_BASE_URL", "two")
        second = [cli_doctor._configuration_section(DOCTOR_ARGS) for _ in range(2)]
        monkeypatch.setenv("HOLON_UNRELATED", "x")
        cli_doctor._configuration_section(DOCTOR_ARGS)
        cli_doctor._configuration_section(other_agent)
        cli_doctor._configuration_section(other_model)
        monkeypatch.delenv("HOLON_UNRELATED")
        again = cli_doctor._configuration_section(DOCTOR_ARGS)

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
            cli_doctor._configuration_section(DOCTOR_ARGS)
            cli_doctor._configuration_section(DOCTOR_ARGS)

    assert len(counted_section) == 3


def test_a_caller_that_mutates_its_result_does_not_change_the_next_callers(counted_section):
    with replayed_configuration_section():
        payload, lines = cli_doctor._configuration_section(DOCTOR_ARGS)
        payload["settings"][0]["shadowed"].append("mutated")
        payload["leak"] = True
        lines.append("mutated")
        next_payload, next_lines = cli_doctor._configuration_section(DOCTOR_ARGS)

    assert next_payload == {"settings": [{"key": "HOLON_LOCAL_BASE_URL", "shadowed": []}], "call": 1}
    assert next_lines == ["heading"]
    assert next_payload is not payload and next_lines is not lines


def test_a_remembered_resolution_is_never_replayed(counted_section):
    config_env.remember(config_env.load_effective_config())

    with replayed_configuration_section():
        results = [cli_doctor._configuration_section(DOCTOR_ARGS) for _ in range(3)]

    assert len(counted_section) == 3
    assert [payload["call"] for payload, _lines in results] == [1, 2, 3]


def test_the_original_function_is_back_after_the_context(counted_section):
    original = cli_doctor._configuration_section

    with replayed_configuration_section():
        assert cli_doctor._configuration_section is not original
    with pytest.raises(RuntimeError, match="inside"), replayed_configuration_section():
        raise RuntimeError("inside")

    assert cli_doctor._configuration_section is original


def _recorded_environment_reports(monkeypatch) -> list[tuple[tuple, tuple]]:
    """Record ``(context, everything observable)`` of every real environment report; the replay is off in both users."""
    real, real_fetchers = cli_doctor.report_budgets_and_envelope, redaction_matrix._fetchers
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
    monkeypatch.setattr(redaction_matrix, "replayed_direct_report", contextlib.nullcontext)
    # One recording fetcher per fetcher of a block, so the three calls of the block share it as they do in the matrix.
    monkeypatch.setattr(redaction_matrix, "_fetchers", lambda seen: tuple(map(probe_recording, real_fetchers(seen))))
    monkeypatch.setattr(redaction_matrix, "_empty_health", probe_recording(redaction_matrix._empty_health))
    monkeypatch.setattr(cli_doctor, "report_budgets_and_envelope", recording)
    return reports


def test_environment_report_is_identical_in_the_three_calls_of_every_doctor_block(
    monkeypatch, empty_environment, cli_log_handler
):
    reports = _recorded_environment_reports(monkeypatch)
    differing: list[str] = []
    probing_blocks = logging_blocks = stderr_blocks = 0
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
            stderr_blocks += bool(first[2][1])

    assert not differing, (
        f"the environment report differs between the three calls of one doctor block, so it must not be replayed: "
        f"{differing} {seeded_secrets.seed_note()}"
    )
    assert probing_blocks and logging_blocks, "the sample must reach the gateway probe and a logged line"
    assert bool(stderr_blocks) == cli_log_handler, "log lines reach stderr exactly when the CLI's handler is there"


def _shows_another_line(replayed: list[str], every_report_real: list[str]) -> bool:
    """Whether one of the two runs of a case shows a text with a line that is not a text both runs show.

    A text that only one run shows is allowed if each of its lines is a text both runs show: that is stderr under the
    CLI's log handler, which holds every logged line once per report that ran.
    """
    in_both = set(replayed) & set(every_report_real)
    in_one_only = set(replayed) ^ set(every_report_real)
    return not all(line in in_both for text in in_one_only for line in text.splitlines())


def _differs_in_more_than_repetition(replayed: list[str], every_report_real: list[str]) -> bool:
    """Whether the two runs of one case show different texts, or the replayed one does not show fewer of them."""
    return _shows_another_line(replayed, every_report_real) or len(replayed) >= len(every_report_real)


def test_environment_replay_changes_only_how_often_the_matrix_sees_a_text(
    monkeypatch, empty_environment, cli_log_handler
):
    differing: list[str] = []
    stderr_repetitions = 0
    for case_id, value in _sampled_values():
        with monkeypatch.context() as without_replay:
            without_replay.setattr(redaction_matrix, "replayed_environment_report", contextlib.nullcontext)
            without_replay.setattr(redaction_matrix, "replayed_direct_report", contextlib.nullcontext)
            every_report_real = redaction_matrix._doctor(value)
        replayed = redaction_matrix._doctor(value)
        if _differs_in_more_than_repetition(replayed, every_report_real):
            differing.append(case_id)
        stderr_repetitions += set(replayed) != set(every_report_real)

    assert not differing, f"{differing} {seeded_secrets.seed_note()}"
    assert bool(stderr_repetitions) == cli_log_handler, "without the CLI's handler both runs show the same set of texts"


def test_matrix_doctor_runs_the_environment_report_once_per_block(monkeypatch, empty_environment):
    real = cli_doctor.report_budgets_and_envelope
    ran: list[tuple[bool, bool]] = []

    def counting(fetch=None, out=None):
        ran.append((local_llm.host_models_json() is None, out is None))
        return real(fetch=fetch, out=out)

    monkeypatch.setattr(cli_doctor, "report_budgets_and_envelope", counting)

    redaction_matrix._doctor("http://alice:FGJQVZFGJQVZ@localhost:8081/v1")
    redaction_matrix._doctor("http://alice:ZVQJGFZVQJGF@localhost:8081/v1")

    # Per block: one report, on a stream, for the direct call and the two doctor runs. Nothing is carried further.
    per_block = [(True, False)] * 2 + [(False, False)] * 2
    assert ran == per_block * 2


@pytest.fixture
def counted_report(monkeypatch):
    """Stand in for the real environment report with one that counts its runs and writes and returns the count."""
    runs: list[tuple[str | None, object]] = []

    def report(fetch=None, out=None):
        runs.append((os.environ.get("HOLON_LOCAL_BASE_URL"), fetch))
        print(f"report {len(runs)}", file=out or sys.stdout)
        return len(runs)

    monkeypatch.setattr(cli_doctor, "report_budgets_and_envelope", report)
    return runs


def test_environment_replay_runs_once_per_environment_host_configuration_and_fetcher(counted_report, monkeypatch):
    def fetch(url: str, timeout: float) -> bytes:
        return b"{}"

    def other_fetch(url: str, timeout: float) -> bytes:
        return b"{}"

    monkeypatch.setenv("HOLON_LOCAL_BASE_URL", "one")
    with replayed_environment_report():
        first = [cli_doctor.report_budgets_and_envelope(fetch=fetch, out=io.StringIO()) for _ in range(3)]
        cli_doctor.report_budgets_and_envelope(fetch=other_fetch, out=io.StringIO())
        monkeypatch.setenv("HOLON_LOCAL_BASE_URL", "two")
        cli_doctor.report_budgets_and_envelope(fetch=fetch, out=io.StringIO())
        with monkeypatch.context() as other_host_configuration:
            other_host_configuration.setattr(local_llm, "host_models_json", lambda: {"providers": {}})
            cli_doctor.report_budgets_and_envelope(fetch=fetch, out=io.StringIO())
        again = cli_doctor.report_budgets_and_envelope(fetch=fetch, out=io.StringIO())

    assert counted_report == [("one", fetch), ("one", other_fetch), ("two", fetch), ("two", fetch)]
    assert first == [1, 1, 1]
    assert again == 3, "the same environment, host configuration and fetcher again is the same key"


def test_a_report_without_a_stream_of_its_own_is_never_replayed(counted_report, capsys):
    with replayed_environment_report():
        on_stdout = [cli_doctor.report_budgets_and_envelope() for _ in range(2)]
        with_a_stream = cli_doctor.report_budgets_and_envelope(out=io.StringIO())
        on_stdout.append(cli_doctor.report_budgets_and_envelope())

    assert on_stdout == [1, 2, 4], "every call on stdout runs the report"
    assert with_a_stream == 3, "and a call with a stream is not answered from a call on stdout"
    assert capsys.readouterr().out == "report 1\nreport 2\nreport 4\n"


def test_every_caller_with_a_stream_gets_the_report_text_on_it_and_the_exit_code(counted_report, capsys):
    streams = [io.StringIO(), io.StringIO(), io.StringIO()]

    with replayed_environment_report():
        codes = [cli_doctor.report_budgets_and_envelope(out=stream) for stream in streams]

    assert codes == [1, 1, 1]
    assert len(counted_report) == 1
    assert [stream.getvalue() for stream in streams] == ["report 1\n"] * 3
    assert capsys.readouterr().out == "", "the text goes to the caller's stream and nowhere else"


def test_each_environment_replay_context_starts_empty_and_restores_the_report(counted_report):
    original = cli_doctor.report_budgets_and_envelope

    for _block in range(3):
        with replayed_environment_report():
            assert cli_doctor.report_budgets_and_envelope is not original
            cli_doctor.report_budgets_and_envelope(out=io.StringIO())
            cli_doctor.report_budgets_and_envelope(out=io.StringIO())
    with pytest.raises(RuntimeError, match="inside"), replayed_environment_report():
        raise RuntimeError("inside")

    assert len(counted_report) == 3
    assert cli_doctor.report_budgets_and_envelope is original


# Every 37th case of the invalid-value matrix: 37 shares no factor with its eight slots, so the slots rotate.
INVALID_VALUE_SAMPLE_STEP = 37
InvalidValueCase = tuple[str, dict[str, str], dict | None, str]


@pytest.fixture
def invalid_value_process(monkeypatch):
    """The process state the invalid-value matrix runs its cases in: a docker stand-in and an empty environment."""
    redaction_matrix._isolate_invalid_value_process(monkeypatch)


def _sampled_invalid_value_cases() -> list[InvalidValueCase]:
    cases = redaction_matrix._invalid_value_cases(seeded_secrets.generator("doctor-replay:invalid-value"))
    return list(itertools.islice(cases, 0, None, INVALID_VALUE_SAMPLE_STEP))


def _invalid_value_doctor(case: InvalidValueCase, home: str) -> tuple[list[int], list[str]]:
    """Run the doctor part of one invalid-value case as the matrix does; the exit codes and the captured texts."""
    with redaction_matrix._invalid_value_case(*case, home) as (out, _docker):
        codes = redaction_matrix._invalid_value_doctor(out)
    return codes, out


def test_invalid_value_sample_covers_every_slot_with_every_prefix_userinfo_form_and_shape():
    parts = [case_id.split("/") for case_id, _env, _host_config, _credential in _sampled_invalid_value_cases()]
    slots = [f"env:{name}" for name in redaction_matrix.INVALID_VALUE_ENV]
    slots += [f"host:{field}" for field in redaction_matrix.INVALID_VALUE_HOST_FIELDS]

    assert {(p[0], p[1]) for p in parts} == set(itertools.product(slots, redaction_matrix.PREFIXES))
    assert {(p[0], p[2]) for p in parts} == set(itertools.product(slots, redaction_matrix.USERINFO))
    assert {p[3] for p in parts} == set(redaction_matrix._credential_shapes())


def test_configuration_section_is_identical_in_the_text_and_the_json_run_of_an_invalid_value_case(
    monkeypatch, invalid_value_process, tmp_path
):
    real = cli_doctor._configuration_section
    calls: list[tuple[tuple, bool]] = []

    def recording(args):
        section = real(args)
        calls.append((section, args.json))
        return section

    # The matrix's own `_invalid_value_doctor`, with the replay switched off, so both doctor runs compute the section.
    monkeypatch.setattr(redaction_matrix, "replayed_configuration_section", contextlib.nullcontext)
    monkeypatch.setattr(cli_doctor, "_configuration_section", recording)
    differing: list[str] = []
    for case in _sampled_invalid_value_cases():
        calls.clear()
        _invalid_value_doctor(case, str(tmp_path))
        (text_section, text_json), (json_section, json_json) = calls
        assert (text_json, json_json) == (False, True), case[0]
        if text_section != json_section:
            differing.append(case[0])

    assert not differing, (
        f"the configuration section differs between the text and the `--json` doctor run of one invalid-value case, "
        f"so it must not be replayed: {differing} {seeded_secrets.seed_note()}"
    )


def test_environment_report_is_identical_in_the_three_calls_of_an_invalid_value_case(
    monkeypatch, invalid_value_process, cli_log_handler, tmp_path
):
    reports = _recorded_environment_reports(monkeypatch)
    differing: list[str] = []
    probing_cases = failing_cases = 0
    for case in _sampled_invalid_value_cases():
        reports.clear()
        _invalid_value_doctor(case, str(tmp_path))
        # The report on a stream of its own, then the text and the `--json` doctor run: no call is on stdout.
        assert [context for context, _observed in reports] == [(case[2] is None, "_empty_health", False)] * 3, case[0]
        first = reports[0][1]
        if any(observed != first for _context, observed in reports):
            differing.append(case[0])
        probing_cases += bool(first[4])
        failing_cases += first[0] != 0

    assert not differing, (
        f"the environment report differs between the three calls of one invalid-value case, so it must not be "
        f"replayed: {differing} {seeded_secrets.seed_note()}"
    )
    assert probing_cases and failing_cases, "the sample must reach the gateway probe and a rejected value"


def test_replays_change_only_how_often_the_invalid_value_matrix_sees_a_text(
    monkeypatch, invalid_value_process, cli_log_handler, tmp_path
):
    differing: list[str] = []
    for case in _sampled_invalid_value_cases():
        with monkeypatch.context() as without_replay:
            without_replay.setattr(redaction_matrix, "replayed_configuration_section", contextlib.nullcontext)
            without_replay.setattr(redaction_matrix, "replayed_environment_report", contextlib.nullcontext)
            real_codes, every_section_real = _invalid_value_doctor(case, str(tmp_path))
        codes, replayed = _invalid_value_doctor(case, str(tmp_path))
        if codes != real_codes or _shows_another_line(replayed, every_section_real):
            differing.append(case[0])

    assert not differing, f"{differing} {seeded_secrets.seed_note()}"


def test_invalid_value_doctor_computes_the_section_and_the_report_once_per_case(
    monkeypatch, invalid_value_process, tmp_path
):
    real_section, real_report = cli_doctor._configuration_section, cli_doctor.report_budgets_and_envelope
    computed: list[str] = []

    def counting_section(args):
        computed.append("section")
        return real_section(args)

    def counting_report(fetch=None, out=None):
        computed.append("report")
        return real_report(fetch=fetch, out=out)

    monkeypatch.setattr(cli_doctor, "_configuration_section", counting_section)
    monkeypatch.setattr(cli_doctor, "report_budgets_and_envelope", counting_report)

    for case in _sampled_invalid_value_cases()[:2]:
        _invalid_value_doctor(case, str(tmp_path))

    assert computed == ["report", "section"] * 2, "one real run of each per case; nothing is carried to the next case"
