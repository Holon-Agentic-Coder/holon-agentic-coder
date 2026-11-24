"""Replay of the configuration section of ``holon doctor`` inside one redaction matrix case.

One matrix case of ``tests/test_url_credential_redaction.py`` calls ``cli.run_doctor`` several times with the same
process environment and the same ``--agent`` and ``--model``; only the host configuration stand-in, the gateway
fetcher and the ``--json`` flag differ. ``cli._configuration_section`` reads none of those three, so it returns the
same value in every call of the case, and :func:`replayed_configuration_section` computes it once.

The rule: a replay is allowed only for a call that is proven identical. The proof is ``tests/test_doctor_replay.py``;
if production makes the section depend on anything outside the key (the whole process environment, ``args.agent`` and
``args.model``), that test fails and the replay must go. The store lives only as long as the context, which a caller
enters once per matrix case, so nothing is carried from one case to the next. Every caller receives its own deep copy.
"""

import argparse
import contextlib
import copy
import os
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

from sandbox_executor import cli, config_env

Section = tuple[dict[str, Any], list[str]]


@contextlib.contextmanager
def replayed_configuration_section() -> Iterator[None]:
    """Within the context, compute ``cli._configuration_section`` once per environment, agent and model."""
    real = cli._configuration_section
    store: dict[tuple[Any, ...], Section] = {}

    def replaying(args: argparse.Namespace) -> Section:
        if config_env.current() is not None:
            # A resolution remembered by ``cli.main`` is state outside the key: such a call is never replayed.
            return real(args)
        key = (tuple(sorted(os.environ.items())), getattr(args, "agent", None), getattr(args, "model", None))
        if key not in store:
            store[key] = real(args)
        return copy.deepcopy(store[key])

    with patch.object(cli, "_configuration_section", replaying):
        yield
