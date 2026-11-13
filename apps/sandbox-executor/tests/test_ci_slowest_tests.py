"""Slowest tests of a failed timing-gate job and the resolver finding built from them, from fakes only."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sandbox_executor.flow.github.ci_slowest_tests import (
    clean_test_name,
    durations_from_log,
    durations_from_records,
    gh_slowest_lookup,
    top_slowest,
)
from sandbox_executor.flow.review.ci_timing_finding import timing_gate_finding

GATE_DETAIL = {"name": "macos-unit", "failed_steps": ["CI Timing Gate"], "duration_seconds": 360.0, "timing_gate": True}
TESTS = {f"tests/test_{i}.py::test": float(i) for i in range(1, 9)}
STATUS = {"checks": [{"name": "macos-unit", "bucket": "fail", "link": "https://x/actions/runs/5/job/9"}]}


def test_top_slowest_returns_the_five_slowest_first() -> None:
    names = [name for name, _ in top_slowest(TESTS)]
    assert names == [f"tests/test_{i}.py::test" for i in (8, 7, 6, 5, 4)]


def test_names_are_cleaned_of_control_characters_and_truncated() -> None:
    assert clean_test_name("a\x1b[31mb\nc") == "a[31mbc"
    assert len(clean_test_name("x" * 1000)) == 200


def test_durations_from_records_prefers_the_matching_job(tmp_path: Path) -> None:
    for job, tests in (("linux-unit", {"a": 1.0}), ("macos-unit", {"b": 2.0})):
        directory = tmp_path / job
        directory.mkdir()
        (directory / "record.json").write_text(json.dumps({"job": job, "tests": tests}), encoding="utf-8")
    assert durations_from_records(tmp_path, "macos-unit (3.13)") == {"b": 2.0}
    assert durations_from_records(tmp_path / "missing", "x") == {}


def test_durations_from_log_parses_the_pytest_section() -> None:
    log = "== slowest 5 durations ==\n12.50s call     tests/test_a.py::test_slow\n0.01s setup    t::x\n"
    assert durations_from_log(log)["tests/test_a.py::test_slow"] == 12.5


def test_lookup_falls_back_to_the_log_and_yields_empty_on_errors(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def run(args: list[str], **_: Any) -> Any:
        calls.append(args)
        if args[2] == "download":
            return SimpleNamespace(returncode=1, stdout="", stderr="expired")
        return SimpleNamespace(returncode=0, stdout="3.00s call tests/t.py::slow\n")

    lookup = gh_slowest_lookup(str(tmp_path), {}, run)
    assert lookup(STATUS["checks"][0]) == [("tests/t.py::slow", 3.0)]
    assert any("--job" in c for c in calls)
    assert lookup({"name": "x", "link": "nope"}) == []

    def boom(args: list[str], **_: Any) -> Any:
        raise OSError("no gh")

    assert gh_slowest_lookup(str(tmp_path), {}, boom)(STATUS["checks"][0]) == []


def test_timing_gate_only_failure_names_the_five_slowest_tests_and_never_offers_a_rerun() -> None:
    seams = (lambda check: GATE_DETAIL, lambda check: top_slowest(TESTS))
    row = timing_gate_finding(2, STATUS, ["macos-unit"], seams)
    assert row is not None
    assert row.id == "FIND-CI-002"
    for i in (8, 7, 6, 5, 4):
        assert f"tests/test_{i}.py::test" in row.summary
    assert "tests/test_3.py::test" not in row.summary
    assert "macos-unit (CI Timing Gate, 6 min)" in row.summary
    text = f"{row.summary} {row.recommendation}".lower()
    assert "speed up" in text
    assert "rerun" not in text.replace("do not repeat the run", "")
    assert "retry" not in text


def test_fallback_wording_when_the_slowest_tests_cannot_be_fetched() -> None:
    row = timing_gate_finding(1, STATUS, ["macos-unit"], (lambda check: GATE_DETAIL, lambda check: []))
    assert row is not None
    assert "could not be fetched" in row.summary
    assert "--durations" in row.summary


def test_mixed_or_unknown_failures_keep_the_generic_finding() -> None:
    plain = {"name": "unit", "failed_steps": ["Run tests"], "duration_seconds": 1.0, "timing_gate": False}
    assert timing_gate_finding(1, STATUS, ["macos-unit"], (lambda c: plain, lambda c: [])) is None
    assert timing_gate_finding(1, STATUS, [], (lambda c: GATE_DETAIL, lambda c: [])) is None
    assert (
        timing_gate_finding(1, {**STATUS, "pending": 1}, ["macos-unit"], (lambda c: GATE_DETAIL, lambda c: [])) is None
    )
