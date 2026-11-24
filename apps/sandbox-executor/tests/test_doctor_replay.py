"""Proof that ``tests/doctor_replay.py`` replays only what is identical, and that the replay keeps callers apart.

The redaction matrix (``tests/test_url_credential_redaction.py``) computes the configuration section of ``holon
doctor`` once per case and replays it for the other doctor runs of that case. The first test here runs the matrix's
own ``_doctor`` without the replay and compares what the real ``cli._configuration_section`` returns in each of its
eight call contexts. If production makes the section depend on the host configuration, the fetcher or ``--json``, it
fails, and the replay must be removed from the matrix.
"""

import argparse
import contextlib
import itertools
import os
import shutil
import socket

import pytest
from sandbox_executor import cli, config_env, local_llm

from tests import seeded_secrets
from tests import test_url_credential_redaction as redaction_matrix
from tests.doctor_replay import replayed_configuration_section

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
