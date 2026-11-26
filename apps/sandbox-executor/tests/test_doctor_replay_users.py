"""Proof for ``replayed_direct_report`` of ``tests/doctor_replay.py`` and its one user, the URL matrix's ``_doctor``.

Each block of a ``_doctor`` case calls the environment report (``cli_doctor.report_budgets_and_envelope``) directly on
stdout once, and the text and the ``--json`` doctor run call it again on a stream of their own. The direct call is
answered by the block's one report: ``replayed_direct_report`` hands it a buffer and copies the buffer to stdout, and
``replayed_environment_report`` answers that buffer from the block's store.

The identity test below runs 50 seeded matrix cases, every block of each, and compares the real direct call (stdout
text and exit code) with the replayed one and with the text the report writes on a stream. If production makes the
report write differently on stdout than on a stream, or return another code, it fails, and the replay must go.
"""

import io
import itertools
import os
import shutil
import socket
import sys
from contextlib import redirect_stdout

import pytest
from sandbox_executor import local_llm
from sandbox_executor.cli import doctor as cli_doctor

from tests import seeded_secrets
from tests import test_url_credential_redaction as redaction_matrix
from tests.doctor_replay import replayed_direct_report, replayed_environment_report

SAMPLED_CASES = 50


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
    """``(case_id, value)`` of every ``n``-th matrix case, ``n`` chosen so that 50 cases span the whole matrix."""
    step = redaction_matrix.MATRIX_SIZE // SAMPLED_CASES
    cases = redaction_matrix.matrix(seeded_secrets.generator("doctor-replay-users:direct"))
    return [(case_id, value) for case_id, value, _credential in itertools.islice(cases, 0, None, step)][:SAMPLED_CASES]


def _direct_call(fetch, *, replayed: bool) -> tuple[int, str]:
    """One direct report call on stdout, as `_doctor` makes it; its exit code and what it wrote on stdout."""
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        if replayed:
            with replayed_environment_report(), replayed_direct_report():
                code = cli_doctor.report_budgets_and_envelope(fetch=fetch)
        else:
            code = cli_doctor.report_budgets_and_envelope(fetch=fetch)
    return code, stdout.getvalue()


def _block_observations(value: str) -> list[tuple[str, tuple]]:
    """For every block of one `_doctor` case: the real direct call, the replayed one and the report on a stream."""
    env = {"HOLON_LOCAL_LLM": "1", "HOLON_LOCAL_BASE_URL": value, "HOLON_LOCAL_MODELS": "m1"}
    observed = []
    with redaction_matrix._captured(env):
        for host_config in (None, redaction_matrix._host_config(value)):
            for fetch in redaction_matrix._fetchers([]):
                with pytest.MonkeyPatch.context() as block:
                    block.setattr(local_llm, "host_models_json", lambda *_a, host_config=host_config, **_k: host_config)
                    real = _direct_call(fetch, replayed=False)
                    replayed = _direct_call(fetch, replayed=True)
                    stream = io.StringIO()
                    on_a_stream = (cli_doctor.report_budgets_and_envelope(fetch=fetch, out=stream), stream.getvalue())
                observed.append(
                    (f"host_config={host_config is not None} fetcher={fetch.__name__}", (real, replayed, on_a_stream))
                )
    return observed


def test_sample_spans_every_prefix_and_both_userinfo_forms():
    parts = [case_id.split("/") for case_id, _value in _sampled_values()]

    assert len(parts) == SAMPLED_CASES
    assert {p[0] for p in parts} == set(redaction_matrix.PREFIXES)
    assert {p[1] for p in parts} == set(redaction_matrix.USERINFO)


def test_direct_report_is_identical_replayed_real_and_on_a_stream(empty_environment):
    differing: list[str] = []
    texts = 0
    for case_id, value in _sampled_values():
        for block, (real, replayed, on_a_stream) in _block_observations(value):
            if not real == replayed == on_a_stream:
                differing.append(f"{case_id} {block}")
            texts += bool(real[1])

    assert not differing, (
        f"the direct report differs from its replay or from the report on a stream, so it must not be replayed: "
        f"{differing} {seeded_secrets.seed_note()}"
    )
    assert texts, "the sample must reach a report that writes something"


@pytest.fixture
def counted_report(monkeypatch):
    """Stand in for the real environment report with one that counts its runs and writes and returns the count."""
    runs: list[bool] = []

    def report(fetch=None, out=None):
        runs.append(out is None)
        print(f"report {len(runs)}", file=out or sys.stdout)
        return len(runs)

    monkeypatch.setattr(cli_doctor, "report_budgets_and_envelope", report)
    return runs


def test_a_direct_call_runs_on_a_stream_and_its_text_goes_to_stdout(counted_report, capsys):
    with replayed_direct_report():
        code = cli_doctor.report_budgets_and_envelope()

    assert code == 1
    assert counted_report == [False], "the report was handed a stream of its own"
    assert capsys.readouterr().out == "report 1\n"


def test_a_call_with_a_stream_is_passed_on_unchanged(counted_report, capsys):
    stream = io.StringIO()

    with replayed_direct_report():
        code = cli_doctor.report_budgets_and_envelope(out=stream)

    assert (code, stream.getvalue(), counted_report) == (1, "report 1\n", [False])
    assert capsys.readouterr().out == ""


def test_inside_the_environment_replay_one_report_answers_the_direct_call_and_the_streams(counted_report, capsys):
    streams = [io.StringIO(), io.StringIO()]

    with replayed_environment_report(), replayed_direct_report():
        codes = [cli_doctor.report_budgets_and_envelope()]
        codes += [cli_doctor.report_budgets_and_envelope(out=stream) for stream in streams]

    assert codes == [1, 1, 1]
    assert counted_report == [False]
    assert capsys.readouterr().out == "report 1\n"
    assert [stream.getvalue() for stream in streams] == ["report 1\n"] * 2


def test_the_original_report_is_back_after_the_context(counted_report):
    original = cli_doctor.report_budgets_and_envelope

    with replayed_direct_report():
        assert cli_doctor.report_budgets_and_envelope is not original
    with pytest.raises(RuntimeError, match="inside"), replayed_direct_report():
        raise RuntimeError("inside")

    assert cli_doctor.report_budgets_and_envelope is original
