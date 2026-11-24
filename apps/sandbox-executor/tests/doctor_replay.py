"""Replay of the two sections of ``holon doctor`` that repeat unchanged inside one redaction matrix case.

One matrix case of ``tests/test_url_credential_redaction.py`` calls ``cli.run_doctor`` several times with the same
process environment and the same ``--agent`` and ``--model``; only the host configuration stand-in, the gateway
fetcher and the ``--json`` flag differ.

- ``cli._configuration_section`` reads none of those three, so it returns the same value in every call of the case,
  and :func:`replayed_configuration_section` computes it once.
- ``cli.report_budgets_and_envelope`` reads the host configuration and the fetcher, but neither ``--json`` nor the
  stream it writes to. One block of the case (one host configuration, one fetcher) runs it three times: directly, and
  inside the text and the ``--json`` doctor run. :func:`replayed_environment_report` runs it once per block and hands
  the other two callers the text and the exit code of that run. The doctor runs still format the text themselves.

The rule: a replay is allowed only for a call that is proven identical. The proof is ``tests/test_doctor_replay.py``;
if production makes a section depend on anything outside its key, that test fails and the replay must go. A store
lives only as long as its context (one matrix case for the configuration section, one block of a case for the
environment report), so nothing is carried further. Every caller receives its own copy.
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

from sandbox_executor import cli, config_env, local_llm


def _environment() -> tuple[tuple[str, str], ...]:
    return tuple(sorted(os.environ.items()))


@contextlib.contextmanager
def replayed_configuration_section() -> Iterator[None]:
    """Within the context, compute ``cli._configuration_section`` once per environment, agent and model."""
    real = cli._configuration_section
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

    with patch.object(cli, "_configuration_section", replaying):
        yield


@contextlib.contextmanager
def replayed_environment_report() -> Iterator[None]:
    """Within the context, run ``cli.report_budgets_and_envelope`` once per environment, host configuration and fetcher.

    The real report writes into a buffer; every caller, the first one included, gets that text on its own stream
    (``out``, or the current ``sys.stdout``) and the exit code. The host configuration stand-in and the fetcher are
    part of the key as objects, so a block that installs another stand-in or passes another fetcher runs the report.
    """
    real = cli.report_budgets_and_envelope
    store: dict[tuple[Any, ...], tuple[str, int]] = {}

    def replaying(fetch: local_llm.Fetcher | None = None, out: TextIO | None = None) -> int:
        key = (_environment(), local_llm.host_models_json, fetch)
        if key not in store:
            written = io.StringIO()
            code = real(fetch=fetch, out=written)
            store[key] = (written.getvalue(), code)
        text, code = store[key]
        (out or sys.stdout).write(text)
        return code

    with patch.object(cli, "report_budgets_and_envelope", replaying):
        yield
