"""Hermetic tests for the superseded check of the push-to-``main`` route: skip only a strict ancestor of the tip.

Every API answer is served by ``tests.ci_bookkeeping_fakes.FakeGitHub``; nothing here reaches the network. The usual
burst scene is run 90 of ``test-unit.yml`` for the push of ``MERGE`` while ``main`` has already moved on to
``MAIN_TIP``, which descends from ``MERGE``.
"""

from __future__ import annotations

import ast
import json
import urllib.error
from dataclasses import replace
from email.message import Message
from pathlib import Path

import pytest

from scripts import ci_main_push_superseded
from scripts.ci_bookkeeping_api import GateClient, GateContext
from scripts.ci_bookkeeping_chain import CHAIN_RULES, Decision
from scripts.ci_bookkeeping_gate import RUN_RULES, SKIP_RULES, diff_rule, is_bookkeeping_path, main, safe_decide
from scripts.ci_main_push_gate import MAIN_PUSH_RUN_RULES, MAIN_PUSH_SKIP_RULES, MAIN_REF, decide_main_push
from scripts.ci_main_push_superseded import RULE_SUPERSEDED, SUPERSEDED_SKIP_RULES
from scripts.ci_merge_push_gate import MERGE_PUSH_RUN_RULES, MERGE_PUSH_SKIP_RULES, GateRules
from tests.ci_bookkeeping_fakes import API, REPO, TOKEN, FakeGitHub, comparison, pull, real_success_job

MERGE = "e" * 40
HEAD = "c" * 40
OTHER = "d" * 40
MAIN_TIP = "f" * 40
TREE = "1" * 40
CURRENT_RUN = 90
HEAD_RUN = 80
REF_READ = "git/ref/heads/main"
SUPERSEDED = Decision(True, RULE_SUPERSEDED, f"pushed {MERGE[:12]} superseded by tip {MAIN_TIP[:12]}")


def push_context(**overrides: object) -> GateContext:
    base = GateContext(
        event_name="push",
        action="",
        label="",
        before=OTHER,
        head_sha="",
        branch="",
        run_number=CURRENT_RUN,
        run_attempt=1,
        workflow_file="test-unit.yml",
        job_name="unit",
        os_name="ubuntu-latest",
        sha=MERGE,
        ref=MAIN_REF,
    )
    return replace(base, **overrides)


def merged_scene(tip: str) -> FakeGitHub:
    """Return a fake for pull request #7 merged as ``MERGE`` with the tested tree and ``tip`` as the tip of main."""
    fake = FakeGitHub().set_tip(tip)
    fake.pulls[MERGE] = [pull(7, HEAD, MERGE)]
    fake.trees.update({MERGE: TREE, HEAD: TREE})
    return fake.add_run(HEAD_RUN, HEAD, real_success_job())


def burst_scene(status: str = "ahead") -> FakeGitHub:
    """Return a fake whose tip ``MAIN_TIP`` compares to ``MERGE`` with ``status`` and that knows no pull request."""
    fake = FakeGitHub().set_tip(MAIN_TIP)
    fake.compares[(MERGE, MAIN_TIP)] = comparison(status=status)
    fake.pulls[MERGE] = []
    return fake


def judge(context: GateContext, fake: FakeGitHub) -> Decision:
    return decide_main_push(context, GateClient(REPO, TOKEN, fake), GateRules(diff_rule, is_bookkeeping_path))


# --- the skip ---------------------------------------------------------------------------------------------------------


def test_a_strict_ancestor_of_the_tip_skips_with_two_reads() -> None:
    fake = burst_scene()

    assert judge(push_context(), fake) == SUPERSEDED
    assert fake.urls == [f"{API}{REF_READ}", f"{API}compare/{MERGE}...{MAIN_TIP}"]
    assert fake.calls("/pulls") == []


# --- every doubt keeps the existing rules -----------------------------------------------------------------------------


def test_the_tip_itself_keeps_the_existing_rules_and_reads_only_the_tip() -> None:
    fake = merged_scene(MERGE)

    decision = judge(push_context(), fake)

    assert (decision.skip, decision.rule) == (True, "main-push-tested-tree")
    assert len(fake.calls(REF_READ)) == 1
    assert fake.calls("compare/") == []


def test_a_commit_that_is_not_an_ancestor_of_the_tip_runs_in_full() -> None:
    decision = judge(push_context(), burst_scene("diverged"))

    assert decision == Decision(False, "main-push-no-pull-request", MERGE[:12])


@pytest.mark.parametrize("status", ["identical", "behind"])
def test_an_identical_or_behind_tip_is_no_skip(status: str) -> None:
    decision = judge(push_context(), burst_scene(status))

    assert (decision.skip, decision.rule) == (False, "main-push-no-pull-request")


def test_a_tip_lookup_that_raises_keeps_the_existing_rules() -> None:
    fake = burst_scene()
    fake.errors[REF_READ] = urllib.error.HTTPError(f"{API}{REF_READ}", 403, "forbidden", Message(), None)

    decision = safe_decide(push_context(), GateClient(REPO, TOKEN, fake))

    assert decision == Decision(False, "main-push-no-pull-request", MERGE[:12])
    assert fake.calls("compare/") == []


def test_a_ref_answer_without_a_commit_keeps_the_existing_rules() -> None:
    fake = burst_scene()
    fake.bodies[REF_READ] = b'{"ref": "refs/heads/main"}'

    assert judge(push_context(), fake).rule == "main-push-no-pull-request"
    assert fake.calls("compare/") == []


def test_a_compare_without_a_file_list_keeps_the_existing_rules() -> None:
    fake = burst_scene()
    fake.compares[(MERGE, MAIN_TIP)] = {"status": "ahead"}

    assert judge(push_context(), fake).rule == "main-push-no-pull-request"


def test_a_rerun_of_a_superseded_commit_runs_in_full_and_reads_nothing() -> None:
    fake = burst_scene()

    decision = judge(push_context(run_attempt=2), fake)

    assert decision == Decision(False, "main-push-rerun-attempt", "attempt 2")
    assert fake.urls == []


# --- bookkeeping ------------------------------------------------------------------------------------------------------


def test_the_rule_is_new_and_apart_from_every_other_rule() -> None:
    main_push = MAIN_PUSH_RUN_RULES + MAIN_PUSH_SKIP_RULES
    others = main_push + RUN_RULES + SKIP_RULES + CHAIN_RULES + MERGE_PUSH_RUN_RULES + MERGE_PUSH_SKIP_RULES

    assert SUPERSEDED_SKIP_RULES == ("main-push-superseded",)
    assert RULE_SUPERSEDED not in others


def test_main_prints_the_superseded_rule_in_the_log_and_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps({"ref": MAIN_REF, "before": OTHER, "after": MERGE}), encoding="utf-8")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    environ = {
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_EVENT_PATH": str(event_file),
        "GITHUB_REF": MAIN_REF,
        "GITHUB_SHA": MERGE,
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_TOKEN": TOKEN,
        "GITHUB_RUN_NUMBER": str(CURRENT_RUN),
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_WORKFLOW_REF": f"{REPO}/.github/workflows/test-unit.yml@{MAIN_REF}",
        "GITHUB_OUTPUT": str(tmp_path / "output.txt"),
    }

    assert main(["--job-name", "unit", "--os", "ubuntu-latest"], environ, burst_scene()) == 0

    line = SUPERSEDED.line()
    assert line.startswith("bookkeeping-gate: decision=SKIP rule=main-push-superseded detail=pushed ")
    assert capsys.readouterr().out == f"{line}\n"
    assert line in summary.read_text(encoding="utf-8")
    assert Path(environ["GITHUB_OUTPUT"]).read_text(encoding="utf-8") == "skip=true\n"


def test_the_module_keeps_python_39_syntax() -> None:
    source = Path(ci_main_push_superseded.__file__).read_text(encoding="utf-8")

    ast.parse(source, filename=ci_main_push_superseded.__file__, feature_version=(3, 9))
