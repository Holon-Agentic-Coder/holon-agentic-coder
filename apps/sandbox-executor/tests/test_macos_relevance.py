"""Tests for the macOS relevance gate's decision script: the rule order, the log, ``main`` and the offline replay.

The changed files come from a ``FixtureRepo`` (a real temporary repository) or from a recording runner that fails the
test when an event rule should have decided without git; the per-file rules are tested in
``test_macos_relevance_rules.py`` and the wiring into the unit workflow in ``test_macos_relevance_wiring.py``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.ci_bookkeeping_chain import Decision
from scripts.ci_bookkeeping_gate import RESULT_AFFECTING_LABELS
from scripts.macos_relevance import (
    FORCE_LABEL,
    RUN_RULES,
    SKIP_RULES,
    SKIP_SENTENCE,
    Judgement,
    RelevanceFacts,
    facts_from,
    main,
    replay_range,
    safe_decide,
    verdict_line,
)
from scripts.macos_relevance_git import ChangedFile
from scripts.macos_relevance_rules import BY_CONTENT, Verdict
from tests.test_macos_relevance_git import NEUTRAL_MODULE, FixtureRepo

SHA = "c" * 40
PULL_REQUEST = RelevanceFacts(event_name="pull_request", run_attempt=1, labels=(), sha=SHA)


@pytest.fixture(autouse=True)
def _no_step_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


@pytest.fixture
def repo(tmp_path: Path) -> FixtureRepo:
    return FixtureRepo(tmp_path / "repo")


class RecordingRunner:
    """A ``GitRunner`` that records its calls and raises the error it is given, or ``AssertionError``."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[list[str]] = []
        self.error = error or AssertionError("git must not be called")

    def __call__(self, args: Sequence[str]) -> bytes:
        self.calls.append(list(args))
        raise self.error


def facts(**overrides: object) -> RelevanceFacts:
    return replace(PULL_REQUEST, **overrides)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"event_name": "push"}, Decision(False, "event-not-pull-request", "push")),
        ({"event_name": "workflow_dispatch"}, Decision(False, "event-not-pull-request", "workflow_dispatch")),
        ({"run_attempt": 2}, Decision(False, "rerun-attempt", "attempt 2")),
        ({"labels": ("docs", "ci-macos")}, Decision(False, "force-label", FORCE_LABEL)),
        ({"sha": ""}, Decision(False, "incomplete-event", "")),
        ({"run_attempt": 0}, Decision(False, "incomplete-event", "")),
    ],
)
def test_event_rules_run_without_git(overrides: dict[str, object], expected: Decision) -> None:
    runner = RecordingRunner()

    assert safe_decide(facts(**overrides), runner) == Judgement(expected)
    assert runner.calls == []


@pytest.mark.parametrize("label", ["ci-macos", "CI-MacOS"])
def test_the_force_label_is_read_in_any_case(tmp_path: Path, label: str) -> None:
    payload = event_payload(labels=[{"name": "documentation"}, {"name": label}])

    assert facts_from(environ_for(tmp_path, payload), payload).labels == ("documentation", "ci-macos")


def test_an_unexpected_exception_is_an_internal_error() -> None:
    judgement = safe_decide(facts(), RecordingRunner(RuntimeError("secret text")))

    assert judgement == Judgement(Decision(False, "internal-error", "RuntimeError"))


def test_the_force_label_is_result_affecting_for_the_bookkeeping_gate() -> None:
    assert FORCE_LABEL in RESULT_AFFECTING_LABELS


def test_run_and_skip_rules_are_disjoint() -> None:
    assert not set(RUN_RULES) & set(SKIP_RULES)
    assert len(set(RUN_RULES)) == len(RUN_RULES)
    assert SKIP_RULES == ("all-neutral",)


def test_a_judged_path_cannot_inject_a_workflow_command() -> None:
    change = ChangedFile("A", "docs/a\n::error::injected.md")

    line = verdict_line(Verdict(change, False))

    assert line == "  neutral   A docs/a?::error::injected.md"


def test_a_sensitive_verdict_line_names_the_old_path_side_and_reason() -> None:
    change = ChangedFile("R", "pkg/plain.py", "pkg/runner.py")

    line = verdict_line(Verdict(change, True, BY_CONTENT, "old", "process `import subprocess`"))

    assert line == "  sensitive R pkg/plain.py <- pkg/runner.py  old:process `import subprocess`"


def event_payload(labels: list[object] | None = None) -> dict[str, object]:
    return {"action": "synchronize", "pull_request": {"labels": labels or []}}


def environ_for(tmp_path: Path, payload: object, **overrides: str) -> dict[str, str]:
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(payload), encoding="utf-8")
    environ = {
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event_file),
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SHA": SHA,
        "GITHUB_OUTPUT": str(tmp_path / "output.txt"),
    }
    return {**environ, **overrides}


def step_output(environ: dict[str, str]) -> str:
    return Path(environ["GITHUB_OUTPUT"]).read_text(encoding="utf-8")


def test_facts_are_read_from_the_environment_and_the_event(tmp_path: Path) -> None:
    payload = event_payload(labels=[{"name": "Docs"}, "not a label"])

    assert facts_from(environ_for(tmp_path, payload), payload) == facts(labels=("docs",))


def test_main_skips_a_neutral_pull_request_and_lists_every_judged_path(
    tmp_path: Path, repo: FixtureRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    sha = repo.merge_pull_request({"pkg/calc.py": NEUTRAL_MODULE + "EXTRA = 1\n", "docs/new.md": "x\n"})
    environ = environ_for(tmp_path, event_payload(), GITHUB_SHA=sha)
    summary = tmp_path / "summary.md"

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        assert main([], environ, repo.run) == 0

    out = capsys.readouterr().out
    assert out.splitlines() == [
        "macos-relevance-gate: skip (all-neutral): 2 changed files",
        "  neutral   A docs/new.md",
        "  neutral   M pkg/calc.py",
        SKIP_SENTENCE,
    ]
    assert step_output(environ) == "skip=true\n"
    assert summary.read_text(encoding="utf-8") == out


def test_main_runs_and_marks_the_sensitive_path(
    tmp_path: Path, repo: FixtureRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    sha = repo.merge_pull_request({"docs/new.md": "x\n", "pkg/calc.py": "import subprocess\n"})
    environ = environ_for(tmp_path, event_payload(), GITHUB_SHA=sha)

    assert main([], environ, repo.run) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "macos-relevance-gate: run (sensitive-content): pkg/calc.py (new: process `import subprocess`)"
    assert lines[1:] == ["  neutral   A docs/new.md", "  sensitive M pkg/calc.py  new:process `import subprocess`"]
    assert step_output(environ) == "skip=false\n"


def test_main_runs_with_the_force_label_without_git(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    environ = environ_for(tmp_path, event_payload(labels=[{"name": "ci-macos"}]))
    runner = RecordingRunner()

    assert main([], environ, runner) == 0

    assert capsys.readouterr().out.splitlines() == ["macos-relevance-gate: run (force-label): ci-macos"]
    assert runner.calls == []
    assert step_output(environ) == "skip=false\n"


def test_main_runs_on_a_push_to_main(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    environ = environ_for(tmp_path, {}, GITHUB_EVENT_NAME="push")

    assert main([], environ, RecordingRunner()) == 0

    assert "run (event-not-pull-request): push" in capsys.readouterr().out
    assert step_output(environ) == "skip=false\n"


def test_main_runs_on_a_missing_event_file_and_sha(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    environ = environ_for(tmp_path, {}, GITHUB_EVENT_PATH=str(tmp_path / "absent.json"), GITHUB_SHA="")

    assert main([], environ, RecordingRunner()) == 0

    assert "run (incomplete-event)" in capsys.readouterr().out
    assert step_output(environ) == "skip=false\n"


def test_main_runs_on_a_bad_argument(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    environ = environ_for(tmp_path, event_payload())

    assert main(["--no-such-flag"], environ, RecordingRunner()) == 0

    assert "macos-relevance-gate: run (internal-error): SystemExit" in capsys.readouterr().out
    assert step_output(environ) == "skip=false\n"


def test_main_runs_when_the_judgement_cannot_be_written(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    environ = environ_for(tmp_path, event_payload(), GITHUB_OUTPUT=str(tmp_path / "absent" / "output.txt"))

    assert main([], environ, RecordingRunner(RuntimeError("boom"))) == 0

    assert "run (internal-error): FileNotFoundError" in capsys.readouterr().out


def test_replay_commit_needs_no_github_environment(repo: FixtureRepo, capsys: pytest.CaptureFixture[str]) -> None:
    repo.git("checkout", "-q", "main")
    repo.write({"pkg/calc.py": NEUTRAL_MODULE + "EXTRA = 1\n"})
    sha = repo.commit("feat: neutral (#12)")

    assert main(["--replay-commit", sha], {}, repo.run) == 0

    assert capsys.readouterr().out.splitlines() == [
        "macos-relevance-gate: skip (all-neutral): 1 changed files",
        "  neutral   M pkg/calc.py",
        SKIP_SENTENCE,
    ]


def test_replay_range_prints_one_row_per_pull_request(repo: FixtureRepo) -> None:
    repo.git("checkout", "-q", "main")
    repo.write({"docs/a.md": "a\n"})
    first = repo.commit("docs: a (#5)")
    repo.write({"docs/b.md": "b\n"})
    repo.commit("chore: no pull request number")
    repo.write({"tools/x.sh": "echo | cat\n"})
    repo.commit("build: x (#6)")

    rows = replay_range(repo.run, f"{first}^..main")

    assert rows[2:] == [
        "| #5 | skip | all-neutral | - |",
        "| #6 | run | sensitive-path | tools/x.sh (new: shell-or-container) |",
    ]
