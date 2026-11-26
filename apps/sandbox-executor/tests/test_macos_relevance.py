"""Tests for the macOS relevance gate: the path classes, the rule order, ``main`` and the offline replay.

Every API answer comes from ``FakeGitHub``, so no test reaches the network; the wiring of the gate into the unit
workflow is tested in ``test_macos_relevance_wiring.py``.
"""

from __future__ import annotations

import json
import urllib.error
from dataclasses import replace
from email.message import Message
from pathlib import Path

import pytest

from scripts.ci_bookkeeping_api import COMPARE_FILE_CAP, GateClient
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
    is_platform_neutral,
    judge_paths,
    main,
    safe_decide,
)
from tests.ci_bookkeeping_fakes import REPO, TOKEN, FakeGitHub, comparison

BASE = "b" * 40
HEAD = "h" * 40
DOC = "docs/safety.md"
PLAN = "plans/I-x/P-y.md"
SOURCE = "apps/sandbox-executor/src/sandbox_executor/launcher.py"


@pytest.fixture(autouse=True)
def _no_step_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


PULL_REQUEST = RelevanceFacts(event_name="pull_request", run_attempt=1, labels=(), base_sha=BASE, head_sha=HEAD)


def facts(**overrides: object) -> RelevanceFacts:
    return replace(PULL_REQUEST, **overrides)


def decide_with(*files: str | tuple[str, str], **overrides: object) -> Decision:
    fake = FakeGitHub({(BASE, HEAD): comparison(*files)})
    return safe_decide(facts(**overrides), GateClient(REPO, TOKEN, fake)).decision


@pytest.mark.parametrize(
    "path",
    [
        PLAN,
        "holon-knowledge/ledger/plans.jsonl",
        "executions/E-1.md",
        DOC,
        "README.md",
        "apps/sandbox-executor/README.md",
        "LICENSE",
    ],
)
def test_neutral_paths(path: str) -> None:
    assert is_platform_neutral(path)


@pytest.mark.parametrize(
    "path",
    [
        "scripts/x.py",
        "apps/sandbox-executor/src/sandbox_executor/claude_keychain.py",
        "apps/sandbox-executor/build_all_images.sh",
        "pyproject.toml",
        "uv.lock",
        "apps/sandbox-executor/uv.lock",
        "apps/sandbox-executor/pyproject.toml",
        ".github/workflows/test-unit.yml",
        ".github/workflows/README.md",
        ".github/actions/macos-relevance-gate/action.yml",
        "scripts/macos_relevance.py",
        "plans/tool.py",
        "docs/plans.MD.py",
        "docs/NOTES.MD",
        "LICENSE.py",
        "",
    ],
)
def test_sensitive_paths(path: str) -> None:
    assert not is_platform_neutral(path)


def test_judge_paths_keeps_the_input_order() -> None:
    assert judge_paths([SOURCE, DOC]) == [(SOURCE, "sensitive"), (DOC, "neutral")]


def test_a_judged_path_cannot_inject_a_workflow_command() -> None:
    path = "docs/a\n::error::injected.md"
    judgement = Judgement(Decision(False, "platform-sensitive-change", path), (path,))

    lines = judgement.lines()

    assert lines[1] == "  neutral   docs/a?::error::injected.md"
    assert not any(line.startswith("::") for line in "\n".join(lines).splitlines())


def test_all_neutral_changes_skip() -> None:
    assert decide_with(DOC, PLAN, "LICENSE") == Decision(True, "all-platform-neutral", "3 changed files")


def test_one_sensitive_file_among_neutral_ones_runs_and_names_it() -> None:
    assert decide_with(DOC, SOURCE, PLAN) == Decision(False, "platform-sensitive-change", SOURCE)


def test_a_rename_out_of_a_neutral_name_runs() -> None:
    assert decide_with(("docs/old.md", "scripts/new.py")) == Decision(
        False, "platform-sensitive-change", "scripts/new.py"
    )


@pytest.mark.parametrize("path", ["pyproject.toml", "uv.lock", "apps/sandbox-executor/uv.lock"])
def test_a_dependency_or_lock_file_change_runs(path: str) -> None:
    assert decide_with(DOC, path) == Decision(False, "platform-sensitive-change", path)


@pytest.mark.parametrize("path", [".github/workflows/test-unit.yml", ".github/actions/macos-relevance-gate/action.yml"])
def test_a_workflow_or_gate_change_runs(path: str) -> None:
    assert decide_with(DOC, path) == Decision(False, "platform-sensitive-change", path)


@pytest.mark.parametrize("label", ["ci-macos", "CI-MacOS"])
def test_the_force_label_runs_without_an_api_call(tmp_path: Path, label: str) -> None:
    fake = FakeGitHub()
    payload = event_payload(labels=[{"name": "documentation"}, {"name": label}])

    judgement = safe_decide(facts_from(environ_for(tmp_path, payload), payload), GateClient(REPO, TOKEN, fake))

    assert judgement.decision == Decision(False, "force-label", FORCE_LABEL)
    assert fake.urls == []


def test_an_empty_changed_file_list_runs() -> None:
    assert decide_with() == Decision(False, "no-changed-files", "")


def test_a_truncated_comparison_runs() -> None:
    files = [f"docs/{index}.md" for index in range(COMPARE_FILE_CAP)]

    assert decide_with(*files) == Decision(False, "diff-too-large", f"{COMPARE_FILE_CAP} paths or more")


def test_an_unreadable_changed_file_list_runs_without_the_error_text() -> None:
    fake = FakeGitHub()
    fake.errors["compare/"] = urllib.error.HTTPError("url", 502, "secret text", Message(), None)

    judgement = safe_decide(facts(), GateClient(REPO, TOKEN, fake))

    assert judgement.decision == Decision(False, "api-error", "HTTPError 502")


def test_a_non_json_answer_runs() -> None:
    fake = FakeGitHub()
    fake.bodies["compare/"] = b"<html>not json</html>"

    judgement = safe_decide(facts(), GateClient(REPO, TOKEN, fake))

    assert judgement.decision == Decision(False, "api-error", "JSONDecodeError")


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"event_name": "push"}, Decision(False, "event-not-pull-request", "push")),
        ({"event_name": "workflow_dispatch"}, Decision(False, "event-not-pull-request", "workflow_dispatch")),
        ({"run_attempt": 2}, Decision(False, "rerun-attempt", "attempt 2")),
        ({"base_sha": ""}, Decision(False, "incomplete-event", "")),
        ({"head_sha": ""}, Decision(False, "incomplete-event", "")),
        ({"run_attempt": 0}, Decision(False, "incomplete-event", "")),
    ],
)
def test_event_rules_run_without_an_api_call(overrides: dict[str, object], expected: Decision) -> None:
    fake = FakeGitHub()

    assert safe_decide(facts(**overrides), GateClient(REPO, TOKEN, fake)).decision == expected
    assert fake.urls == []


def test_the_force_label_is_result_affecting_for_the_bookkeeping_gate() -> None:
    assert FORCE_LABEL in RESULT_AFFECTING_LABELS


def test_run_and_skip_rules_are_disjoint() -> None:
    assert not set(RUN_RULES) & set(SKIP_RULES)
    assert len(set(RUN_RULES)) == len(RUN_RULES)


def event_payload(labels: list[object] | None = None) -> dict[str, object]:
    return {
        "action": "synchronize",
        "pull_request": {"base": {"sha": BASE}, "head": {"sha": HEAD}, "labels": labels or []},
    }


def environ_for(tmp_path: Path, payload: object, **overrides: str) -> dict[str, str]:
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(payload), encoding="utf-8")
    environ = {
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event_file),
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_TOKEN": TOKEN,
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_OUTPUT": str(tmp_path / "output.txt"),
    }
    return {**environ, **overrides}


def step_output(environ: dict[str, str]) -> str:
    return Path(environ["GITHUB_OUTPUT"]).read_text(encoding="utf-8")


def test_facts_are_read_from_the_environment_and_the_event(tmp_path: Path) -> None:
    payload = event_payload(labels=[{"name": "Docs"}, "not a label"])

    assert facts_from(environ_for(tmp_path, payload), payload) == facts(labels=("docs",))


def test_main_skips_and_lists_every_judged_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    environ = environ_for(tmp_path, event_payload())
    summary = tmp_path / "summary.md"
    fake = FakeGitHub({(BASE, HEAD): comparison(DOC, PLAN)})

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        assert main([], environ, fake) == 0

    out = capsys.readouterr().out
    assert out.splitlines() == [
        "macos-relevance-gate: decision=SKIP rule=all-platform-neutral detail=2 changed files",
        f"  neutral   {DOC}",
        f"  neutral   {PLAN}",
        SKIP_SENTENCE,
    ]
    assert step_output(environ) == "skip=true\n"
    assert summary.read_text(encoding="utf-8") == out


def test_main_runs_and_marks_the_sensitive_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    environ = environ_for(tmp_path, event_payload())
    fake = FakeGitHub({(BASE, HEAD): comparison(DOC, SOURCE)})

    assert main([], environ, fake) == 0

    out = capsys.readouterr().out
    assert f"  sensitive {SOURCE}" in out.splitlines()
    assert SKIP_SENTENCE not in out
    assert step_output(environ) == "skip=false\n"


def test_main_runs_on_a_missing_event_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    environ = environ_for(tmp_path, {}, GITHUB_EVENT_PATH=str(tmp_path / "absent.json"))

    assert main([], environ, FakeGitHub()) == 0

    assert "rule=incomplete-event" in capsys.readouterr().out
    assert step_output(environ) == "skip=false\n"


def test_main_runs_on_a_bad_argument(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    environ = environ_for(tmp_path, event_payload())

    assert main(["--no-such-flag"], environ, FakeGitHub()) == 0

    assert "macos-relevance-gate: decision=RUN rule=api-error detail=SystemExit" in capsys.readouterr().out
    assert step_output(environ) == "skip=false\n"


def test_replay_judges_a_paths_file_without_the_network(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths_file = tmp_path / "paths.txt"
    paths_file.write_text(f"{DOC}\n\n{SOURCE}\n", encoding="utf-8")
    fake = FakeGitHub()

    assert main(["--paths-file", str(paths_file)], {}, fake) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"macos-relevance-gate: decision=RUN rule=platform-sensitive-change detail={SOURCE}",
        f"  neutral   {DOC}",
        f"  sensitive {SOURCE}",
    ]
    assert fake.urls == []
