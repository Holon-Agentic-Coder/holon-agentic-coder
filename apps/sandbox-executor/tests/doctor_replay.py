"""Replay of the two sections of ``holon doctor`` that repeat unchanged inside one redaction matrix case.

One matrix case of ``tests/test_url_credential_redaction.py`` calls ``cli.run_doctor`` several times with the same
process environment and the same ``--agent`` and ``--model``; only the host configuration stand-in, the gateway
fetcher and the ``--json`` flag differ.

- ``cli._configuration_section`` reads none of those three, so it returns the same value in every call of the case,
  and :func:`replayed_configuration_section` computes it once.
- ``cli.report_budgets_and_envelope`` reads the host configuration and the fetcher, but not ``--json``. One block of
  the case (one host configuration, one fetcher) runs it three times: directly on stdout, and inside the text and the
  ``--json`` doctor run, which both hand in a stream of their own. :func:`replayed_environment_report` leaves the
  direct call alone, runs the report for the first caller with a stream and hands every later one the text and the
  exit code of that run. Both doctor runs still format the text themselves.

:func:`replayed_direct_report`, entered inside :func:`replayed_environment_report`, hands a direct call a stream of its
own and copies what was written to it onto stdout, so the direct call of a block becomes one more caller of that
block's single report.

There are two users, and each has its own proofs in ``tests/test_doctor_replay.py`` (the direct-call replay of the
first one in ``tests/test_doctor_replay_users.py``):

- ``_doctor``, the doctor entry point of the URL matrix: eight doctor runs and four direct reports on stdout per case,
  in four blocks. One report runs for the direct call and the two doctor runs of a block.
- ``_invalid_value_doctor``, the doctor part of the invalid-value matrix: one report on a stream of its own, then the
  text and the ``--json`` doctor run, all with one environment, one host configuration and one fetcher. No call is on
  stdout, so one report runs for the three.

The rule: a replay is allowed only for a call that is proven identical. If production makes a section depend on
anything outside its key, the proof of that user fails and the replay must go there. A user that is not listed here
has no proof and must not use a replay. A store lives only as long as its context (one matrix case for the
configuration section, one block of a case for the environment report), so nothing is carried further. Every caller
receives its own copy.
"""

import argparse
import contextlib
import io
import os
import pickle
import sys
from collections.abc import Iterator
from typing import Any, TextIO
from unittest.mock import patch

from sandbox_executor import config_env, local_llm
from sandbox_executor.cli import doctor as cli_doctor


def _environment() -> tuple[tuple[str, str], ...]:
    return tuple(sorted(os.environ.items()))


@contextlib.contextmanager
def replayed_configuration_section() -> Iterator[None]:
    """Within the context, compute ``cli._configuration_section`` once per environment, agent and model."""
    real = cli_doctor._configuration_section
    store: dict[tuple[Any, ...], bytes] = {}

    def replaying(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
        if config_env.current() is not None:
            # A resolution remembered by ``cli.main`` is state outside the key: such a call is never replayed.
            return real(args)
        key = (_environment(), getattr(args, "agent", None), getattr(args, "model", None))
        if key not in store:
            store[key] = pickle.dumps(real(args))
        # Unpickling the stored bytes builds new objects for every caller, as a deep copy does, in less time.
        return pickle.loads(store[key])

    with patch.object(cli_doctor, "_configuration_section", replaying):
        yield


@contextlib.contextmanager
def replayed_environment_report() -> Iterator[None]:
    """Within the context, run ``cli.report_budgets_and_envelope`` once for the calls on a stream that share its inputs.

    A call without a stream of its own (the direct call, on stdout) always runs the real report. A call with one (a
    doctor run, or a report into a buffer) is run once per environment, host configuration and fetcher: the real
    report writes into a buffer, and every such caller, the first one included, gets that text on its stream and the
    exit code. The host configuration stand-in and the fetcher are part of the key as objects, so a block that
    installs another stand-in or passes another fetcher runs the report again.
    """
    real = cli_doctor.report_budgets_and_envelope
    store: dict[tuple[Any, ...], tuple[str, int]] = {}

    def replaying(fetch: local_llm.Fetcher | None = None, out: TextIO | None = None) -> int:
        if out is None:
            return real(fetch=fetch)
        key = (_environment(), local_llm.host_models_json, fetch)
        if key not in store:
            written = io.StringIO()
            code = real(fetch=fetch, out=written)
            store[key] = (written.getvalue(), code)
        text, code = store[key]
        out.write(text)
        return code

    with patch.object(cli_doctor, "report_budgets_and_envelope", replaying):
        yield


@contextlib.contextmanager
def replayed_direct_report() -> Iterator[None]:
    """Within the context, run a ``cli.report_budgets_and_envelope`` call without a stream on a stream of its own.

    The report is run with a buffer as its stream, through whatever ``report_budgets_and_envelope`` is when the context
    is entered, and the buffer's text is then written on stdout. Entered inside :func:`replayed_environment_report`,
    the direct call of a block is thus answered by the block's one report, like the doctor runs of the block. A call
    with a stream of its own is passed on unchanged.
    """
    report = cli_doctor.report_budgets_and_envelope

    def on_a_stream(fetch: local_llm.Fetcher | None = None, out: TextIO | None = None) -> int:
        if out is not None:
            return report(fetch=fetch, out=out)
        written = io.StringIO()
        code = report(fetch=fetch, out=written)
        sys.stdout.write(written.getvalue())
        return code

    with patch.object(cli_doctor, "report_budgets_and_envelope", on_a_stream):
        yield
