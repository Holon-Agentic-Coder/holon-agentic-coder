"""Hermetic tests for the push-to-``main`` route of the bookkeeping gate: the evidence chain, its rules and fail-open.

Every API answer is served by ``tests.ci_bookkeeping_fakes.FakeGitHub``; nothing here reaches the network. The usual
scene is run 90 of ``test-unit.yml`` for the push of ``MERGE``, the merge commit of pull request #7 whose head ``HEAD``
has the same tree and ran the unit job in run 80. ``MERGE`` is the tip of ``main``, so the superseded check reads the
tip and leaves the decision to the evidence chain.
"""

from __future__ import annotations

import ast
import json
import urllib.error
from collections.abc import Callable
from dataclasses import replace
from email.message import Message
from pathlib import Path

import pytest

from scripts import ci_main_push_gate
from scripts.ci_bookkeeping_api import GateClient, GateContext
from scripts.ci_bookkeeping_chain import Decision
from scripts.ci_bookkeeping_gate import (
    RUN_RULES,
    SKIP_RULES,
    decide,
    diff_rule,
    is_bookkeeping_path,
    main,
    safe_decide,
)
from scripts.ci_main_push_gate import (
    GATED_WORKFLOWS,
    MAIN_PUSH_RUN_RULES,
    MAIN_PUSH_SKIP_RULES,
    MAIN_REF,
    decide_main_push,
    is_main_push,
)
from scripts.ci_merge_push_gate import GateRules
from tests.ci_bookkeeping_fakes import (
    API,
    MACOS_JOB,
    REPO,
    TOKEN,
    UNIT_JOB,
    FakeGitHub,
    cancelled_job,
    comparison,
    gate_skipped_job,
    job,
    legacy_success_job,
    pull,
    real_success_job,
)

MERGE = "e" * 40
HEAD = "c" * 40
OTHER = "d" * 40
PRE_MERGE = "b" * 40
MAIN_TIP = "f" * 40
TREE = "1" * 40
OTHER_TREE = "2" * 40
CURRENT_RUN = 90
HEAD_RUN = 80
STRESS = "test-stress.yml"
STRESS_JOB = "Workspace survival (ubuntu-latest)"
ARGV = ["--job-name", "unit", "--os", "ubuntu-latest"]

Scene = Callable[[], tuple[GateContext, FakeGitHub]]


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


def merged_scene(*jobs: dict[str, object], workflow: str = "test-unit.yml") -> FakeGitHub:
    """Return a fake for one merged pull request with equal trees and one head run holding ``jobs``."""
    fake = FakeGitHub().set_tip(MERGE)
    fake.pulls[MERGE] = [pull(7, HEAD, MERGE)]
    fake.trees.update({MERGE: TREE, HEAD: TREE})
    fake.add_run(HEAD_RUN, HEAD, *jobs, path=f".github/workflows/{workflow}")
    return fake


def client(fake: FakeGitHub) -> GateClient:
    return GateClient(REPO, TOKEN, fake)


def judge(context: GateContext, fake: FakeGitHub) -> Decision:
    return safe_decide(context, client(fake))


# --- the skip ---------------------------------------------------------------------------------------------------------


def test_a_merge_commit_with_the_tested_tree_skips() -> None:
    fake = merged_scene(real_success_job(), real_success_job(MACOS_JOB))

    decision = judge(push_context(), fake)

    assert decision == Decision(True, "main-push-tested-tree", f"#7 head {HEAD[:12]} run {HEAD_RUN} real success")
    assert len(fake.urls) == 6
    assert len(fake.calls("git/ref/heads/main")) == 1


def test_a_gate_skipped_head_job_skips_and_says_so() -> None:
    fake = merged_scene(gate_skipped_job()).add_run(HEAD_RUN - 1, OTHER, real_success_job())
    fake.commits[HEAD] = (TREE, [OTHER])
    fake.compares[(OTHER, HEAD)] = comparison("plans/x.md")

    decision = judge(push_context(), fake)

    assert decision.skip is True
    assert decision.rule == "main-push-tested-tree"
    assert decision.detail.endswith("gate-skipped")


def test_a_single_parent_bookkeeping_head_over_skipped_merge_runs_on_main() -> None:
    fake = merged_scene(gate_skipped_job())
    fake.add_run(HEAD_RUN - 1, OTHER, gate_skipped_job())
    fake.add_run(HEAD_RUN - 2, PRE_MERGE, real_success_job())
    fake.commits[HEAD] = (TREE, [OTHER])
    fake.commits[OTHER] = (TREE, [PRE_MERGE, MAIN_TIP])
    fake.compares[(PRE_MERGE, HEAD)] = comparison("scripts/from_main.py", "executions/E-1.md")

    decision = judge(push_context(), fake)

    assert (decision.skip, decision.rule) == (False, "main-push-head-skip-unproven")
    assert "non-bookkeeping-change scripts/from_main.py" in decision.detail
    assert f"since {PRE_MERGE[:12]}" in decision.detail


def test_a_single_parent_bookkeeping_head_over_cancelled_merge_runs_on_main() -> None:
    fake = merged_scene(gate_skipped_job())
    fake.add_run(HEAD_RUN - 1, OTHER, cancelled_job())
    fake.add_run(HEAD_RUN - 2, PRE_MERGE, real_success_job())
    fake.commits[HEAD] = (TREE, [OTHER])
    fake.commits[OTHER] = (TREE, [PRE_MERGE, MAIN_TIP])

    decision = judge(push_context(), fake)

    assert (decision.skip, decision.rule) == (False, "main-push-head-skip-unproven")
    assert "previous-run-not-successful cancelled" in decision.detail


def merge_head_scene(*changed: str) -> FakeGitHub:
    """Return a fake whose head merged ``main`` into ``PRE_MERGE`` and whose job the gate skipped there.

    The job really succeeded on ``PRE_MERGE`` in the run before; ``changed`` are the paths of ``PRE_MERGE...HEAD``.
    """
    fake = merged_scene(gate_skipped_job()).add_run(HEAD_RUN - 1, PRE_MERGE, real_success_job())
    fake.commits[HEAD] = (TREE, [PRE_MERGE, MAIN_TIP])
    fake.compares[(PRE_MERGE, HEAD)] = comparison(*changed)
    return fake


def test_a_merge_push_skip_on_the_head_is_no_evidence_and_main_runs() -> None:
    decision = judge(push_context(), merge_head_scene("scripts/from_main.py", "plans/x.md"))

    assert (decision.skip, decision.rule) == (False, "main-push-head-skip-unproven")
    assert "non-bookkeeping-change scripts/from_main.py" in decision.detail
    assert f"since {PRE_MERGE[:12]}" in decision.detail


def test_a_gate_skipped_merge_head_with_only_bookkeeping_since_a_real_success_skips() -> None:
    decision = judge(push_context(), merge_head_scene("plans/x.md", "executions/E-1.md"))

    assert (decision.skip, decision.rule) == (True, "main-push-tested-tree")
    assert decision.detail.endswith("gate-skipped")


def test_a_gate_skipped_merge_head_without_a_real_success_runs() -> None:
    fake = merge_head_scene("plans/x.md")
    fake.runs.pop()

    decision = judge(push_context(), fake)

    assert (decision.skip, decision.rule) == (False, "main-push-head-skip-unproven")
    assert "only gate-skipped runs on the branch" in decision.detail


def test_the_macos_leg_skips_on_its_own_job() -> None:
    fake = merged_scene(job(UNIT_JOB, conclusion="failure"), real_success_job(MACOS_JOB))

    assert judge(push_context(os_name="macos-latest"), fake).skip is True


def test_the_workspace_survival_job_skips_in_the_stress_workflow() -> None:
    fake = merged_scene(real_success_job(STRESS_JOB), workflow=STRESS)
    context = push_context(workflow_file=STRESS, job_name="Workspace survival")

    assert judge(context, fake).rule == "main-push-tested-tree"


def test_an_older_head_run_holds_the_job_when_the_newest_lacks_it() -> None:
    fake = merged_scene(real_success_job()).add_run(HEAD_RUN + 1, HEAD, real_success_job(MACOS_JOB))

    decision = judge(push_context(), fake)

    assert decision.skip is True
    assert f"run {HEAD_RUN} " in decision.detail


def test_a_head_run_of_another_pull_request_is_passed_over() -> None:
    fake = merged_scene(real_success_job())
    fake.add_run(HEAD_RUN + 1, HEAD, real_success_job(), pull_requests=[{"number": 8, "base": {"ref": "main"}}])
    fake.add_run(HEAD_RUN + 2, HEAD, real_success_job(), pull_requests=[{"number": 7, "base": {"ref": "release"}}])

    decision = judge(push_context(), fake)

    assert decision.skip is True
    assert f"run {HEAD_RUN} " in decision.detail


def test_a_head_run_of_another_pull_request_alone_runs_in_full() -> None:
    fake = FakeGitHub().set_tip(MERGE)
    fake.pulls[MERGE] = [pull(7, HEAD, MERGE)]
    fake.trees.update({MERGE: TREE, HEAD: TREE})
    fake.add_run(HEAD_RUN, HEAD, real_success_job(), pull_requests=[{"number": 7, "base": {"ref": "release"}}])

    assert judge(push_context(), fake).rule == "main-push-head-job-missing"


def test_a_head_run_naming_no_pull_request_still_counts() -> None:
    fake = FakeGitHub().set_tip(MERGE)
    fake.pulls[MERGE] = [pull(7, HEAD, MERGE)]
    fake.trees.update({MERGE: TREE, HEAD: TREE})
    fake.add_run(HEAD_RUN, HEAD, real_success_job(), pull_requests=None)

    assert judge(push_context(), fake).rule == "main-push-tested-tree"


# --- every rule that runs the full job --------------------------------------------------------------------------------


def _with_pulls(*pulls: dict[str, object]) -> FakeGitHub:
    fake = merged_scene(real_success_job())
    fake.pulls[MERGE] = list(pulls)
    return fake


def _with_tree(tree: str) -> FakeGitHub:
    fake = merged_scene(real_success_job())
    fake.trees[HEAD] = tree
    return fake


def _without_runs() -> FakeGitHub:
    fake = merged_scene(real_success_job())
    fake.runs.clear()
    return fake


def _without_head_branch() -> FakeGitHub:
    fake = merge_head_scene("plans/x.md")
    fake.pulls[MERGE] = [pull(7, HEAD, MERGE, head={"sha": HEAD})]
    return fake


RUN_SCENES = [
    pytest.param("main-push-rerun-attempt", lambda: (push_context(run_attempt=2), merged_scene()), id="rerun"),
    pytest.param("main-push-incomplete-event", lambda: (push_context(sha=""), merged_scene()), id="no sha"),
    pytest.param("main-push-incomplete-event", lambda: (push_context(run_number=0), merged_scene()), id="no number"),
    pytest.param("main-push-no-pull-request", lambda: (push_context(), _with_pulls()), id="no pull request"),
    pytest.param(
        "main-push-several-pull-requests",
        lambda: (push_context(), _with_pulls(pull(7, HEAD, MERGE), pull(8, OTHER, MERGE))),
        id="two pull requests",
    ),
    pytest.param(
        "main-push-pull-request-not-merged",
        lambda: (push_context(), _with_pulls(pull(7, HEAD, MERGE, merged_at=None))),
        id="not merged",
    ),
    pytest.param(
        "main-push-pull-request-not-merged",
        lambda: (push_context(), _with_pulls(pull(7, HEAD, MERGE, base={"ref": "dev"}))),
        id="base not main",
    ),
    pytest.param(
        "main-push-pull-request-not-merged",
        lambda: (push_context(), _with_pulls(pull(7, HEAD, OTHER))),
        id="other merge commit",
    ),
    pytest.param("main-push-tree-mismatch", lambda: (push_context(), _with_tree(OTHER_TREE)), id="tree mismatch"),
    pytest.param("main-push-head-job-missing", lambda: (push_context(), _without_runs()), id="no head run"),
    pytest.param(
        "main-push-head-job-missing",
        lambda: (push_context(), merged_scene(real_success_job(MACOS_JOB))),
        id="only the other runner's job",
    ),
    pytest.param(
        "main-push-head-job-not-finished",
        lambda: (push_context(), merged_scene(job(status="in_progress", conclusion=None))),
        id="in progress",
    ),
    pytest.param(
        "main-push-head-job-not-successful",
        lambda: (push_context(), merged_scene(job(marker="success", conclusion="failure"))),
        id="failure",
    ),
    pytest.param(
        "main-push-head-job-not-successful",
        lambda: (push_context(), merged_scene(job(marker="failure"))),
        id="marker failed",
    ),
    pytest.param(
        "main-push-head-job-unverified",
        lambda: (push_context(), merged_scene(legacy_success_job())),
        id="no marker step",
    ),
    pytest.param(
        "main-push-head-skip-unproven",
        lambda: (push_context(), merge_head_scene("scripts/from_main.py")),
        id="merge-push skip on the head",
    ),
    pytest.param(
        "main-push-head-skip-unproven",
        lambda: (push_context(), _without_head_branch()),
        id="no head branch",
    ),
]


@pytest.mark.parametrize(("rule", "scene"), RUN_SCENES)
def test_every_missing_piece_of_evidence_runs_the_full_job(rule: str, scene: Scene) -> None:
    context, fake = scene()

    decision = judge(context, fake)

    assert (decision.skip, decision.rule) == (False, rule)
    assert decision.rule in MAIN_PUSH_RUN_RULES
    assert all(url.startswith(API) for url in fake.urls)


def test_every_main_push_rule_is_reached_by_a_test() -> None:
    reached = {param.values[0] for param in RUN_SCENES} | {"main-push-tested-tree"}

    assert reached == set(MAIN_PUSH_RUN_RULES + MAIN_PUSH_SKIP_RULES)


def test_rule_names_are_unique_and_apart_from_the_pull_request_rules() -> None:
    names = MAIN_PUSH_RUN_RULES + MAIN_PUSH_SKIP_RULES

    assert len(set(names)) == len(names)
    assert set(names).isdisjoint(RUN_RULES + SKIP_RULES)
    assert all(name.startswith("main-push-") for name in names)


def test_a_rerun_reads_nothing_from_the_api() -> None:
    fake = merged_scene(real_success_job())

    judge(push_context(run_attempt=3), fake)

    assert fake.urls == []


# --- fail-open --------------------------------------------------------------------------------------------------------


def _forbidden() -> urllib.error.HTTPError:
    return urllib.error.HTTPError(f"{API}commits/{MERGE}/pulls", 403, f"secret {TOKEN}", Message(), None)


@pytest.mark.parametrize(
    ("part", "error", "body", "detail"),
    [
        pytest.param("commits/", _forbidden(), None, "HTTPError 403", id="HTTP 403"),
        pytest.param("git/commits/", ValueError(f"secret {TOKEN}"), None, "ValueError", id="error on the tree"),
        pytest.param("/pulls", None, b'{"message": "secret"}', "ValueError", id="pulls not an array"),
        pytest.param("git/commits/", None, b'{"tree": {}}', "ValueError", id="commit without a tree"),
        pytest.param("/pulls", None, b'[{"number": 7}]', "KeyError", id="pull without a base"),
    ],
)
def test_every_error_is_an_api_error_and_runs_in_full(
    part: str, error: Exception | None, body: bytes | None, detail: str
) -> None:
    fake = merged_scene(real_success_job())
    if error is not None:
        fake.errors[part] = error
    if body is not None:
        fake.bodies[part] = body

    decision = judge(push_context(), fake)

    assert decision == Decision(False, "api-error", detail)
    assert TOKEN not in decision.line()
    assert "secret" not in decision.line()


# --- routing ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"event_name": "pull_request"}, id="pull request"),
        pytest.param({"event_name": "schedule"}, id="schedule"),
        pytest.param({"event_name": "workflow_dispatch"}, id="workflow dispatch"),
        pytest.param({"ref": "refs/heads/dev"}, id="push to another branch"),
        pytest.param({"ref": ""}, id="no ref"),
        pytest.param({"workflow_file": "test-hygiene.yml"}, id="another workflow"),
    ],
)
def test_only_a_push_to_main_in_a_gated_workflow_takes_the_route(overrides: dict[str, object]) -> None:
    assert is_main_push(push_context(**overrides)) is False


@pytest.mark.parametrize("workflow", GATED_WORKFLOWS)
def test_both_gated_workflows_take_the_route(workflow: str) -> None:
    assert is_main_push(push_context(workflow_file=workflow)) is True


@pytest.mark.parametrize("event_name", ["schedule", "workflow_dispatch"])
def test_a_scheduled_or_manual_run_keeps_the_gate_rule_and_reads_nothing(event_name: str) -> None:
    fake = merged_scene(real_success_job())

    decision = decide(push_context(event_name=event_name), client(fake))

    assert decision == Decision(False, "event-not-pull-request", event_name)
    assert fake.urls == []


def test_a_push_in_another_workflow_keeps_the_gate_rule() -> None:
    fake = merged_scene(real_success_job())

    decision = decide(push_context(workflow_file="make.yml"), client(fake))

    assert decision.rule == "event-not-pull-request"
    assert fake.urls == []


def test_the_route_decides_like_the_module() -> None:
    context = push_context()

    assert decide(context, client(merged_scene(real_success_job()))) == decide_main_push(
        context, client(merged_scene(real_success_job())), GateRules(diff_rule, is_bookkeeping_path)
    )


# --- the command ------------------------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_step_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


def push_environ(tmp_path: Path, **overrides: str) -> dict[str, str]:
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps({"ref": MAIN_REF, "before": OTHER, "after": MERGE}), encoding="utf-8")
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
    return {**environ, **overrides}


def test_main_prints_the_skip_rule_and_writes_skip_true(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    environ = push_environ(tmp_path)
    fake = merged_scene(real_success_job())

    assert main(ARGV, environ, fake) == 0

    assert capsys.readouterr().out == (
        f"bookkeeping-gate: decision=SKIP rule=main-push-tested-tree "
        f"detail=#7 head {HEAD[:12]} run {HEAD_RUN} real success\n"
    )
    assert Path(environ["GITHUB_OUTPUT"]).read_text(encoding="utf-8") == "skip=true\n"
    assert fake.auth == [f"Bearer {TOKEN}"] * len(fake.urls)
    assert all(TOKEN not in url for url in fake.urls)


def test_main_runs_in_full_on_a_tree_mismatch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    environ = push_environ(tmp_path)

    assert main(ARGV, environ, _with_tree(OTHER_TREE)) == 0

    assert "decision=RUN rule=main-push-tree-mismatch" in capsys.readouterr().out
    assert Path(environ["GITHUB_OUTPUT"]).read_text(encoding="utf-8") == "skip=false\n"


def test_main_runs_in_full_when_the_token_may_not_read_pull_requests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    environ = push_environ(tmp_path)
    fake = merged_scene(real_success_job())
    fake.errors["/pulls"] = _forbidden()

    assert main(ARGV, environ, fake) == 0

    captured = capsys.readouterr()
    assert captured.out == "bookkeeping-gate: decision=RUN rule=api-error detail=HTTPError 403\n"
    assert TOKEN not in captured.out + captured.err
    assert Path(environ["GITHUB_OUTPUT"]).read_text(encoding="utf-8") == "skip=false\n"


def test_the_module_keeps_python_39_syntax() -> None:
    source = Path(ci_main_push_gate.__file__).read_text(encoding="utf-8")

    ast.parse(source, filename=ci_main_push_gate.__file__, feature_version=(3, 9))
