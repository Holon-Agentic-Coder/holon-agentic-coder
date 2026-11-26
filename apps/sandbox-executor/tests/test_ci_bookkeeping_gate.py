"""Hermetic tests for the bookkeeping-only push gate: the path rule, the rule order and the command.

Every API answer is served by ``tests.ci_bookkeeping_fakes.FakeGitHub``; nothing here reaches the network. The usual
scene is run 50 of ``test-unit.yml`` for a push that moved the pull-request head from ``SHA_B`` to ``SHA_C``.
"""

from __future__ import annotations

import json
import urllib.error
from dataclasses import replace
from email.message import Message
from pathlib import Path

import pytest

from scripts.ci_bookkeeping_api import COMPARE_FILE_CAP, GateClient, GateContext, context_from, load_event
from scripts.ci_bookkeeping_chain import CHAIN_RULES, RULE_PREVIOUS_RUN_SUCCEEDED, Decision
from scripts.ci_bookkeeping_gate import (
    BOOKKEEPING_ROOTS,
    BOOKKEEPING_SUFFIXES,
    RESULT_AFFECTING_LABELS,
    RUN_RULES,
    SKIP_RULES,
    decide,
    first_outside_path,
    is_bookkeeping_path,
    main,
    safe_decide,
)
from tests.ci_bookkeeping_fakes import (
    BRANCH,
    MACOS_JOB,
    REPO,
    TOKEN,
    WORKFLOW,
    FakeGitHub,
    comparison,
    gate_skipped_job,
    job,
    legacy_success_job,
    real_success_job,
)

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
SHA_D = "d" * 40
ZERO_SHA = "0" * 40
CURRENT_RUN = 50
PLAN_FILE = "plans/P-1-plan.md"
REPORT_FILE = "executions/E-1-execution.md"
LEDGER_FILE = "holon-knowledge/ledger/plans.jsonl"
CODE_FILE = "apps/sandbox-executor/src/flow/engine.py"


def context(**overrides: object) -> GateContext:
    base = GateContext(
        event_name="pull_request",
        action="synchronize",
        label="",
        before=SHA_B,
        head_sha=SHA_C,
        branch=BRANCH,
        run_number=CURRENT_RUN,
        run_attempt=1,
        workflow_file=WORKFLOW,
        job_name="unit",
        os_name="ubuntu-latest",
    )
    return replace(base, **overrides)


def label_context(label: str, action: str = "labeled", **overrides: object) -> GateContext:
    return context(action=action, label=label, before="", **overrides)


def pushed(*files: str | tuple[str, str], status: str = "ahead") -> FakeGitHub:
    """Return a fake in which the push from ``SHA_B`` to ``SHA_C`` changed ``files``."""
    return FakeGitHub({(SHA_B, SHA_C): comparison(*files, status=status)})


def gate(fake: FakeGitHub, gate_context: GateContext | None = None) -> Decision:
    return safe_decide(gate_context or context(), GateClient(REPO, TOKEN, fake))


# --- the path rule ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("plans/x.md", True),
        ("executions/a/b.md", True),
        (LEDGER_FILE, True),
        ("holon-knowledge/kb/index.json", True),
        ("plans/I-1-intent/P-1-plan.v2.md", True),
        ("plans/x.py", False),
        ("executions/run.sh", False),
        ("holon-knowledge/hooks/config.yml", False),
        ("plans/conftest.py", False),
        ("executions/Makefile", False),
        ("plans/x.MD", False),
        ("plans/x.md.py", False),
        ("plans/.md", False),
        ("plans/x.md/", False),
        ("plans_helper.py", False),
        ("src/holon-knowledge-tool.py", False),
        ("plans", False),
        ("plans/", False),
        ("Plans/x.md", False),
        ("docs/plans/x.md", False),
        ("/plans/x.md", False),
        ("", False),
    ],
)
def test_is_bookkeeping_path_matches_a_checked_file_type_under_a_whole_root_only(path: str, expected: bool) -> None:
    assert is_bookkeeping_path(path) is expected


def test_bookkeeping_roots_are_the_three_directories_the_flow_writes() -> None:
    assert BOOKKEEPING_ROOTS == ("plans", "executions", "holon-knowledge")


def test_bookkeeping_suffixes_are_the_file_types_the_skipped_hygiene_job_checks() -> None:
    assert BOOKKEEPING_SUFFIXES == (".md", ".json", ".jsonl")


def test_first_outside_path_names_the_first_path_that_is_not_bookkeeping() -> None:
    assert first_outside_path([PLAN_FILE, REPORT_FILE]) is None
    assert first_outside_path([]) is None
    assert first_outside_path([PLAN_FILE, CODE_FILE, "README.md"]) == CODE_FILE
    assert first_outside_path([PLAN_FILE, "plans/tool.py", CODE_FILE]) == "plans/tool.py"


# --- a push (synchronize) --------------------------------------------------------------------------------------------


def test_bookkeeping_only_push_after_a_real_success_skips_with_three_api_calls() -> None:
    fake = pushed(PLAN_FILE, REPORT_FILE, LEDGER_FILE).add_run(49, SHA_B, real_success_job())

    decision = gate(fake)

    assert decision == Decision(True, "bookkeeping-only", f"3 changed paths since the real success on {SHA_B[:12]}")
    assert len(fake.urls) == 3
    assert len(fake.calls("compare/")) == len(fake.calls("/runs?")) == len(fake.calls("/jobs?")) == 1
    assert fake.auth == [f"Bearer {TOKEN}"] * 3


def test_empty_commit_after_a_real_success_skips() -> None:
    decision = gate(pushed().add_run(49, SHA_B, real_success_job()))

    assert (decision.skip, decision.rule) == (True, "bookkeeping-only")
    assert decision.detail.startswith("0 changed paths")


def test_mixed_change_runs_names_the_path_and_does_not_ask_for_runs() -> None:
    fake = pushed(PLAN_FILE, CODE_FILE).add_run(49, SHA_B, real_success_job())

    assert gate(fake) == Decision(False, "non-bookkeeping-change", CODE_FILE)
    assert fake.calls("actions/") == []


@pytest.mark.parametrize("path", ["plans/tool.py", "executions/run.sh", "holon-knowledge/hooks/config.yml"])
def test_file_of_another_type_under_a_bookkeeping_root_runs(path: str) -> None:
    fake = pushed(PLAN_FILE, path).add_run(49, SHA_B, real_success_job())

    assert gate(fake) == Decision(False, "non-bookkeeping-change", path)
    assert fake.calls("actions/") == []


def test_rename_to_another_file_type_inside_a_bookkeeping_root_runs() -> None:
    decision = gate(pushed(("plans/notes.md", "plans/notes.py")).add_run(49, SHA_B, real_success_job()))

    assert decision == Decision(False, "non-bookkeeping-change", "plans/notes.py")


@pytest.mark.parametrize(
    "rename",
    [
        pytest.param(("src/notes.md", "plans/notes.md"), id="into a bookkeeping root"),
        pytest.param(("plans/notes.md", "src/notes.md"), id="out of a bookkeeping root"),
    ],
)
def test_rename_across_the_boundary_runs_in_both_directions(rename: tuple[str, str]) -> None:
    decision = gate(pushed(PLAN_FILE, rename).add_run(49, SHA_B, real_success_job()))

    assert (decision.skip, decision.rule, decision.detail) == (False, "non-bookkeeping-change", "src/notes.md")


def test_rename_inside_the_bookkeeping_roots_skips() -> None:
    decision = gate(pushed(("plans/a.md", "executions/a.md")).add_run(49, SHA_B, real_success_job()))

    assert (decision.skip, decision.rule) == (True, "bookkeeping-only")
    assert decision.detail.startswith("2 changed paths")


@pytest.mark.parametrize("status", ["diverged", "behind", "identical", ""])
def test_head_that_is_not_strictly_ahead_runs(status: str) -> None:
    fake = pushed(PLAN_FILE, status=status).add_run(49, SHA_B, real_success_job())

    assert gate(fake) == Decision(False, "force-push-or-diverged", f"compare status {status}")
    assert fake.calls("actions/") == []


def test_file_list_at_the_api_cap_runs() -> None:
    files = [f"plans/P-{index}.md" for index in range(COMPARE_FILE_CAP)]

    decision = gate(pushed(*files).add_run(49, SHA_B, real_success_job()))

    assert (decision.skip, decision.rule) == (False, "diff-too-large")


def test_file_list_just_below_the_api_cap_skips() -> None:
    files = [f"plans/P-{index}.md" for index in range(COMPARE_FILE_CAP - 1)]

    assert gate(pushed(*files).add_run(49, SHA_B, real_success_job())).skip is True


@pytest.mark.parametrize(
    ("previous", "rule", "detail"),
    [
        pytest.param(job(conclusion="failure"), "previous-run-not-successful", "failure in run 49", id="failed"),
        pytest.param(job(conclusion="cancelled"), "previous-run-not-successful", "cancelled in run 49", id="cancelled"),
        pytest.param(
            job(marker="success", status="in_progress", conclusion=None),
            "previous-run-not-successful",
            "in_progress in run 49",
            id="the job is still running",
        ),
        pytest.param(
            legacy_success_job(),
            "previous-run-unverified",
            "no 'Full run completed' step in run 49",
            id="a success from before the gate",
        ),
        pytest.param(
            real_success_job(MACOS_JOB),
            "previous-run-missing",
            "no single matching job in run 49",
            id="only the other runner's job",
        ),
    ],
)
def test_previous_run_without_a_real_success_runs(previous: dict[str, object], rule: str, detail: str) -> None:
    assert gate(pushed(PLAN_FILE).add_run(49, SHA_B, previous)) == Decision(False, rule, detail)


def test_no_previous_run_runs() -> None:
    decision = gate(pushed(PLAN_FILE))

    assert (decision.skip, decision.rule) == (False, "previous-run-missing")


def test_previous_run_on_another_commit_runs() -> None:
    decision = gate(pushed(PLAN_FILE).add_run(49, SHA_A, real_success_job()))

    assert decision == Decision(False, "previous-run-missing", "run 49 is on another commit")


def test_previous_run_still_in_progress_runs() -> None:
    fake = pushed(PLAN_FILE).add_run(49, SHA_B, real_success_job(), status="in_progress")

    assert gate(fake) == Decision(False, "previous-run-not-finished", "run 49")


def test_previous_run_that_names_two_pull_requests_runs() -> None:
    both = [{"number": 7}, {"number": 8}]
    fake = pushed(PLAN_FILE).add_run(49, SHA_B, real_success_job(), pull_requests=both)

    assert gate(fake) == Decision(False, "shared-head-branch", "run 49 names 2 pull requests")
    assert fake.calls("/jobs?") == []


def test_run_of_another_workflow_or_branch_is_not_a_previous_run() -> None:
    fake = pushed(PLAN_FILE)
    fake.add_run(49, SHA_B, real_success_job(), path=".github/workflows/make.yml")
    fake.add_run(48, SHA_B, real_success_job(), head_branch="another-branch")

    assert gate(fake).rule == "previous-run-missing"


def test_later_run_of_the_same_workflow_is_ignored() -> None:
    fake = pushed(PLAN_FILE).add_run(51, SHA_D, job(conclusion="failure")).add_run(49, SHA_B, real_success_job())

    assert gate(fake).skip is True
    assert fake.calls("actions/runs/1051/") == []


# --- a chain of skipped runs -----------------------------------------------------------------------------------------


def chain_of_two_skips(last_job: dict[str, object], *files_since_real: str) -> FakeGitHub:
    """Return a fake with runs 49 and 48 skipped by the gate and run 47, on ``SHA_D``, ending in ``last_job``."""
    fake = pushed(PLAN_FILE)
    fake.compares[(SHA_D, SHA_C)] = comparison(*files_since_real)
    fake.add_run(49, SHA_B, gate_skipped_job()).add_run(48, SHA_A, gate_skipped_job()).add_run(47, SHA_D, last_job)
    return fake


def test_two_skipped_runs_then_a_real_success_skip_after_a_second_compare() -> None:
    fake = chain_of_two_skips(real_success_job(), PLAN_FILE, REPORT_FILE, LEDGER_FILE)

    decision = gate(fake)

    assert decision == Decision(True, "bookkeeping-only", f"3 changed paths since the real success on {SHA_D[:12]}")
    assert [url.rpartition("compare/")[2] for url in fake.calls("compare/")] == [
        f"{SHA_B}...{SHA_C}",
        f"{SHA_D}...{SHA_C}",
    ]
    assert len(fake.calls("/jobs?")) == 3


def test_chain_with_a_file_outside_the_roots_since_the_real_success_runs() -> None:
    fake = chain_of_two_skips(real_success_job(), PLAN_FILE, CODE_FILE)

    assert gate(fake) == Decision(False, "non-bookkeeping-change", CODE_FILE)


def test_chain_whose_real_success_is_not_an_ancestor_runs() -> None:
    fake = chain_of_two_skips(real_success_job())
    fake.compares[(SHA_D, SHA_C)] = comparison(PLAN_FILE, status="diverged")

    assert gate(fake).rule == "force-push-or-diverged"


def test_chain_that_ends_in_a_failure_runs() -> None:
    fake = chain_of_two_skips(job(conclusion="failure"), PLAN_FILE)

    assert gate(fake) == Decision(False, "previous-run-not-successful", "failure in run 47")
    assert len(fake.calls("compare/")) == 1


def test_chain_that_ends_before_the_gate_existed_runs() -> None:
    assert gate(chain_of_two_skips(legacy_success_job(), PLAN_FILE)).rule == "previous-run-unverified"


def test_chain_of_skipped_runs_only_runs() -> None:
    fake = pushed(PLAN_FILE).add_run(49, SHA_B, gate_skipped_job()).add_run(48, SHA_A, gate_skipped_job())

    assert gate(fake) == Decision(False, "previous-run-missing", "only gate-skipped runs on the branch")


def test_chain_longer_than_the_hop_limit_runs() -> None:
    fake = pushed(PLAN_FILE)
    for number in range(49, 20, -1):
        fake.add_run(number, SHA_B if number == 49 else SHA_A, gate_skipped_job())

    assert gate(fake).rule == "chain-too-long"


# --- API errors ------------------------------------------------------------------------------------------------------

API_CALLS = ["compare/", "/runs?", "/jobs?"]


def forbidden() -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.github.com/secret-url", 403, "secret reason", Message(), None)


@pytest.mark.parametrize("part", API_CALLS)
@pytest.mark.parametrize(
    ("error", "detail"),
    [
        pytest.param(OSError("secret text"), "OSError", id="network error"),
        pytest.param(TimeoutError("secret text"), "TimeoutError", id="timeout"),
        pytest.param(forbidden(), "HTTPError 403", id="forbidden"),
    ],
)
def test_error_on_any_api_call_runs_without_the_error_text(part: str, error: Exception, detail: str) -> None:
    fake = pushed(PLAN_FILE).add_run(49, SHA_B, real_success_job())
    fake.errors[part] = error

    assert gate(fake) == Decision(False, "api-error", detail)


@pytest.mark.parametrize("part", API_CALLS)
@pytest.mark.parametrize(
    ("body", "detail"),
    [
        pytest.param(b"<html>not json</html>", "JSONDecodeError", id="not JSON"),
        pytest.param(b"[]", "ValueError", id="JSON of another shape"),
        pytest.param(b"{}", "ValueError", id="an empty object"),
    ],
)
def test_unexpected_answer_on_any_api_call_runs(part: str, body: bytes, detail: str) -> None:
    fake = pushed(PLAN_FILE).add_run(49, SHA_B, real_success_job())
    fake.bodies[part] = body

    assert gate(fake) == Decision(False, "api-error", detail)


def test_run_entry_without_its_number_runs() -> None:
    fake = pushed(PLAN_FILE).add_run(49, SHA_B, real_success_job())
    del fake.runs[0]["run_number"]

    assert gate(fake) == Decision(False, "api-error", "KeyError")


def test_error_on_the_second_compare_of_a_chain_runs() -> None:
    fake = chain_of_two_skips(real_success_job(), PLAN_FILE)
    fake.errors[f"compare/{SHA_D}"] = OSError("secret text")

    assert gate(fake) == Decision(False, "api-error", "OSError")


# --- event rules: no API call ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "rule", "detail"),
    [
        pytest.param({"event_name": "push"}, "event-not-pull-request", "push", id="push to main"),
        pytest.param({"event_name": "workflow_dispatch"}, "event-not-pull-request", "workflow_dispatch", id="manual"),
        pytest.param({"event_name": "pull_request_target"}, "event-not-pull-request", "pull_request_target", id="prt"),
        pytest.param({"event_name": ""}, "event-not-pull-request", "", id="no event name"),
        pytest.param({"run_attempt": 2}, "rerun-attempt", "attempt 2", id="second attempt"),
        pytest.param({"action": "opened"}, "action-not-eligible", "opened", id="opened"),
        pytest.param({"action": "reopened"}, "action-not-eligible", "reopened", id="reopened"),
        pytest.param({"action": ""}, "action-not-eligible", "", id="no action"),
        pytest.param({"before": ""}, "missing-previous-head", "", id="no previous head"),
        pytest.param({"before": ZERO_SHA}, "missing-previous-head", ZERO_SHA, id="all-zero previous head"),
        pytest.param({"head_sha": ""}, "incomplete-event", "", id="no head"),
        pytest.param({"branch": ""}, "incomplete-event", "", id="no branch"),
        pytest.param({"workflow_file": ""}, "incomplete-event", "", id="no workflow file"),
        pytest.param({"job_name": ""}, "incomplete-event", "", id="no job name"),
        pytest.param({"run_number": 0}, "incomplete-event", "", id="no run number"),
        pytest.param({"run_attempt": 0}, "incomplete-event", "", id="no run attempt"),
        pytest.param({"action": "labeled", "label": ""}, "incomplete-event", "", id="label event without a label"),
    ],
)
def test_event_that_is_not_eligible_runs_without_an_api_call(
    overrides: dict[str, object], rule: str, detail: str
) -> None:
    fake = pushed(PLAN_FILE).add_run(49, SHA_B, real_success_job())

    assert gate(fake, context(**overrides)) == Decision(False, rule, detail)
    assert fake.urls == []


def test_rerun_of_a_bookkeeping_only_push_runs_in_full() -> None:
    fake = pushed(PLAN_FILE).add_run(49, SHA_B, real_success_job())

    assert gate(fake).skip is True
    assert gate(fake, context(run_attempt=2)).skip is False


# --- label events ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["labeled", "unlabeled"])
@pytest.mark.parametrize(
    "label", ["timing-regression-accepted", "module-budget-change", "Module-Budget-Change", "ci-macos", "CI-MacOS"]
)
def test_result_affecting_label_runs_without_an_api_call(action: str, label: str) -> None:
    fake = FakeGitHub().add_run(49, SHA_C, real_success_job())

    assert gate(fake, label_context(label, action)) == Decision(False, "result-affecting-label", label)
    assert fake.urls == []


def test_result_affecting_labels_are_the_ones_the_workflows_and_the_macos_relevance_gate_read() -> None:
    assert sorted(RESULT_AFFECTING_LABELS) == ["ci-macos", "module-budget-change", "timing-regression-accepted"]


@pytest.mark.parametrize("action", ["labeled", "unlabeled"])
def test_other_label_after_a_real_success_on_the_same_head_skips_without_a_compare(action: str) -> None:
    fake = FakeGitHub().add_run(49, SHA_C, real_success_job())

    decision = gate(fake, label_context("documentation", action))

    assert (decision.skip, decision.rule) == (True, "label-not-result-affecting")
    assert fake.calls("compare/") == []
    assert len(fake.urls) == 2


def test_other_label_after_a_failed_run_on_the_same_head_runs() -> None:
    fake = FakeGitHub().add_run(49, SHA_C, job(conclusion="failure"))

    assert gate(fake, label_context("documentation")).rule == "previous-run-not-successful"


def test_other_label_when_the_previous_run_was_on_an_older_head_runs() -> None:
    fake = FakeGitHub().add_run(49, SHA_B, real_success_job())

    assert gate(fake, label_context("documentation")).rule == "previous-run-missing"


def test_other_label_after_a_skipped_run_compares_from_the_real_success() -> None:
    fake = FakeGitHub({(SHA_B, SHA_C): comparison(PLAN_FILE)})
    fake.add_run(49, SHA_C, gate_skipped_job()).add_run(48, SHA_B, real_success_job())

    assert gate(fake, label_context("documentation")).rule == "label-not-result-affecting"
    assert len(fake.calls("compare/")) == 1

    fake.compares[(SHA_B, SHA_C)] = comparison(PLAN_FILE, CODE_FILE)
    assert gate(fake, label_context("documentation")) == Decision(False, "non-bookkeeping-change", CODE_FILE)


# --- the rule sets ---------------------------------------------------------------------------------------------------


def test_rule_sets_are_disjoint_and_hold_every_chain_rule_that_can_be_printed() -> None:
    assert set(SKIP_RULES) == {"bookkeeping-only", "label-not-result-affecting"}
    assert not set(SKIP_RULES) & set(RUN_RULES)
    assert len(set(RUN_RULES)) == len(RUN_RULES)
    assert set(CHAIN_RULES) - {RULE_PREVIOUS_RUN_SUCCEEDED} <= set(RUN_RULES)
    assert RULE_PREVIOUS_RUN_SUCCEEDED not in RUN_RULES + SKIP_RULES


def scenes() -> list[tuple[FakeGitHub, GateContext]]:
    """Return one scene per way through the gate, a skip of each kind among them."""
    broken = pushed(PLAN_FILE)
    broken.errors["compare/"] = OSError("secret text")
    return [
        (pushed(PLAN_FILE).add_run(49, SHA_B, real_success_job()), context()),
        (FakeGitHub().add_run(49, SHA_C, real_success_job()), label_context("documentation")),
        (pushed(PLAN_FILE, CODE_FILE), context()),
        (pushed(PLAN_FILE, status="diverged"), context()),
        (pushed(PLAN_FILE), context()),
        (pushed(PLAN_FILE).add_run(49, SHA_B, job(conclusion="failure")), context()),
        (pushed(PLAN_FILE).add_run(49, SHA_B, legacy_success_job()), context()),
        (pushed(PLAN_FILE).add_run(49, SHA_B, gate_skipped_job()), context()),
        (
            pushed(PLAN_FILE).add_run(49, SHA_B, real_success_job(), pull_requests=[{"number": 7}, {"number": 8}]),
            context(),
        ),
        (pushed("plans/tool.py").add_run(49, SHA_B, real_success_job()), context()),
        (broken, context()),
        (pushed(PLAN_FILE), context(event_name="push")),
        (pushed(PLAN_FILE), context(run_attempt=2)),
        (pushed(PLAN_FILE), context(before=ZERO_SHA)),
        (FakeGitHub(), label_context("module-budget-change")),
    ]


def test_skip_is_returned_only_under_the_two_skip_rules() -> None:
    decisions = [gate(fake, gate_context) for fake, gate_context in scenes()]

    assert {decision.rule for decision in decisions if decision.skip} == set(SKIP_RULES)
    for decision in decisions:
        assert decision.rule in (SKIP_RULES if decision.skip else RUN_RULES)


def test_decide_leaves_exceptions_to_safe_decide() -> None:
    fake = pushed(PLAN_FILE)
    fake.errors["compare/"] = OSError("secret text")

    with pytest.raises(OSError, match="secret text"):
        decide(context(), GateClient(REPO, TOKEN, fake))


def test_decision_line_names_the_rule_and_replaces_unprintable_characters() -> None:
    assert Decision(True, "bookkeeping-only").line() == "bookkeeping-gate: decision=SKIP rule=bookkeeping-only"
    assert (
        Decision(False, "non-bookkeeping-change", "src/a\nb::warning::\x1b[0m.py").line()
        == "bookkeeping-gate: decision=RUN rule=non-bookkeeping-change detail=src/a?b::warning::?[0m.py"
    )


# --- the event and the environment -----------------------------------------------------------------------------------


def event_payload(action: str = "synchronize", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "action": action,
        "before": SHA_B,
        "after": SHA_C,
        "pull_request": {"number": 7, "head": {"sha": SHA_C, "ref": BRANCH}},
    }
    return {**payload, **overrides}


def gate_environ(tmp_path: Path, payload: object, **overrides: str) -> dict[str, str]:
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(payload), encoding="utf-8")
    environ = {
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event_file),
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_TOKEN": TOKEN,
        "GITHUB_RUN_NUMBER": str(CURRENT_RUN),
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_WORKFLOW_REF": f"{REPO}/.github/workflows/{WORKFLOW}@refs/pull/7/merge",
        "GITHUB_OUTPUT": str(tmp_path / "output.txt"),
    }
    return {**environ, **overrides}


ARGV = ["--job-name", "unit", "--os", "ubuntu-latest"]


def step_output(environ: dict[str, str]) -> str:
    return Path(environ["GITHUB_OUTPUT"]).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _no_step_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


def test_context_is_read_from_the_environment_and_the_event(tmp_path: Path) -> None:
    environ = gate_environ(tmp_path, event_payload())

    built = context_from(environ, load_event(environ["GITHUB_EVENT_PATH"]), "unit", "ubuntu-latest")

    assert built == context()


def test_context_of_a_label_event_carries_the_label_name(tmp_path: Path) -> None:
    payload = event_payload("labeled", label={"name": "documentation"})
    del payload["before"]
    environ = gate_environ(tmp_path, payload)

    built = context_from(environ, load_event(environ["GITHUB_EVENT_PATH"]), "unit", "ubuntu-latest")

    assert built == label_context("documentation")


@pytest.mark.parametrize(
    ("workflow_ref", "expected"),
    [
        ("acme/widgets/.github/workflows/make.yml@refs/heads/main", "make.yml"),
        ("acme/widgets/.github/workflows/make.yaml@refs/pull/7/merge", "make.yaml"),
        ("acme/widgets/.github/workflows/make.yml@refs/heads/feature/a.yml", "make.yml"),
        ("acme/widgets/.github/workflows/make@refs/heads/main", ""),
        ("", ""),
    ],
)
def test_workflow_file_is_the_last_part_of_the_workflow_reference(workflow_ref: str, expected: str) -> None:
    built = context_from({"GITHUB_WORKFLOW_REF": workflow_ref}, {}, "unit", "")

    assert built.workflow_file == expected


def test_context_of_an_empty_environment_is_empty_text_and_zero() -> None:
    built = context_from({}, {}, "unit", "")

    assert built == GateContext("", "", "", "", "", "", 0, 0, "", "unit", "")


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"action": 7, "before": None, "pull_request": "text", "label": []}, id="values of other types"),
        pytest.param({"pull_request": {"head": [SHA_C]}, "label": {"name": 1}}, id="children of other types"),
    ],
)
def test_context_treats_values_of_an_unexpected_type_as_missing(payload: dict[str, object]) -> None:
    built = context_from({"GITHUB_RUN_NUMBER": "-3", "GITHUB_RUN_ATTEMPT": "1.5"}, payload, "unit", "")

    assert built == GateContext("", "", "", "", "", "", 0, 0, "", "unit", "")


@pytest.mark.parametrize("content", ["", "not json", "[1, 2]", '"text"'])
def test_broken_event_file_is_an_empty_payload(tmp_path: Path, content: str) -> None:
    event_file = tmp_path / "event.json"
    event_file.write_text(content, encoding="utf-8")

    assert load_event(str(event_file)) == {}


def test_missing_event_file_is_an_empty_payload(tmp_path: Path) -> None:
    assert load_event(str(tmp_path / "absent.json")) == {}
    assert load_event("") == {}


# --- the command -----------------------------------------------------------------------------------------------------


def test_main_writes_skip_true_and_prints_the_rule(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    environ = gate_environ(tmp_path, event_payload())
    fake = pushed(PLAN_FILE, REPORT_FILE).add_run(49, SHA_B, real_success_job())

    assert main(ARGV, environ, fake) == 0

    assert step_output(environ) == "skip=true\n"
    printed = capsys.readouterr().out
    assert printed == (
        f"bookkeeping-gate: decision=SKIP rule=bookkeeping-only "
        f"detail=2 changed paths since the real success on {SHA_B[:12]}\n"
    )
    assert len(fake.urls) == 3


def test_main_writes_skip_false_and_prints_the_rule(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    environ = gate_environ(tmp_path, event_payload())
    fake = pushed(PLAN_FILE, CODE_FILE).add_run(49, SHA_B, real_success_job())

    assert main(ARGV, environ, fake) == 0

    assert step_output(environ) == "skip=false\n"
    assert capsys.readouterr().out == (
        f"bookkeeping-gate: decision=RUN rule=non-bookkeeping-change detail={CODE_FILE}\n"
    )


def test_main_skips_a_label_event_with_a_label_that_changes_no_result(tmp_path: Path) -> None:
    payload = event_payload("labeled", label={"name": "documentation"})
    del payload["before"]
    environ = gate_environ(tmp_path, payload)

    assert main(ARGV, environ, FakeGitHub().add_run(49, SHA_C, real_success_job())) == 0

    assert step_output(environ) == "skip=true\n"


def test_main_appends_to_the_step_output_file(tmp_path: Path) -> None:
    environ = gate_environ(tmp_path, event_payload())
    Path(environ["GITHUB_OUTPUT"]).write_text("earlier=1\n", encoding="utf-8")

    assert main(ARGV, environ, pushed(PLAN_FILE).add_run(49, SHA_B, real_success_job())) == 0

    assert step_output(environ) == "earlier=1\nskip=true\n"


def test_main_adds_the_decision_line_to_the_step_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    environ = gate_environ(tmp_path, event_payload())

    assert main(ARGV, environ, pushed(PLAN_FILE).add_run(49, SHA_B, real_success_job())) == 0

    assert summary.read_text(encoding="utf-8") == capsys.readouterr().out


def test_main_runs_in_full_when_the_event_file_is_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    environ = gate_environ(tmp_path, event_payload(), GITHUB_EVENT_PATH=str(tmp_path / "absent.json"))
    fake = pushed(PLAN_FILE).add_run(49, SHA_B, real_success_job())

    assert main(ARGV, environ, fake) == 0

    assert step_output(environ) == "skip=false\n"
    assert capsys.readouterr().out == "bookkeeping-gate: decision=RUN rule=action-not-eligible\n"
    assert fake.urls == []


def test_main_runs_in_full_with_an_empty_environment(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeGitHub()

    assert main(ARGV, {}, fake) == 0

    assert capsys.readouterr().out == "bookkeeping-gate: decision=RUN rule=event-not-pull-request\n"
    assert fake.urls == []


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param([], id="no job name"),
        pytest.param(["--job-name", "unit", "--unknown"], id="an unknown option"),
        pytest.param(["--help"], id="help"),
    ],
)
def test_main_runs_in_full_on_a_bad_command_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    environ = gate_environ(tmp_path, event_payload())
    fake = pushed(PLAN_FILE).add_run(49, SHA_B, real_success_job())

    assert main(argv, environ, fake) == 0

    assert step_output(environ) == "skip=false\n"
    assert "bookkeeping-gate: decision=RUN rule=api-error detail=SystemExit\n" in capsys.readouterr().out
    assert fake.urls == []


def test_main_runs_in_full_when_the_step_summary_cannot_be_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "absent-directory" / "summary.md"))
    environ = gate_environ(tmp_path, event_payload())

    assert main(ARGV, environ, pushed(PLAN_FILE).add_run(49, SHA_B, real_success_job())) == 0

    assert step_output(environ) == "skip=false\n"
    assert capsys.readouterr().out.endswith("bookkeeping-gate: decision=RUN rule=api-error detail=FileNotFoundError\n")


def test_main_returns_zero_when_the_step_output_cannot_be_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    environ = gate_environ(tmp_path, event_payload(), GITHUB_OUTPUT=str(tmp_path / "absent-directory" / "output.txt"))

    assert main(ARGV, environ, pushed(PLAN_FILE).add_run(49, SHA_B, real_success_job())) == 0

    assert capsys.readouterr().out.endswith("bookkeeping-gate: decision=RUN rule=api-error detail=FileNotFoundError\n")


def test_main_never_prints_the_token_or_the_error_text(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    environ = gate_environ(tmp_path, event_payload())
    fake = pushed(PLAN_FILE)
    fake.errors["compare/"] = OSError(f"secret text with {TOKEN}")

    assert main(ARGV, environ, fake) == 0

    captured = capsys.readouterr()
    assert captured.out == "bookkeeping-gate: decision=RUN rule=api-error detail=OSError\n"
    assert TOKEN not in captured.out + captured.err
    assert "secret text" not in captured.out + captured.err
    assert TOKEN not in step_output(environ)
    assert all(TOKEN not in url for url in fake.urls)
    assert fake.auth == [f"Bearer {TOKEN}"]
