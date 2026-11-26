"""Hermetic tests of where a Stage 3 resume starts and which execution branches the host publishes.

Each test builds a local bare origin and a clone (the host worktree): an intent branch, its plan branch and a failed
execution branch whose summary row records a ``partial_head`` (agent commit, then the failure commit carrying the notes
and the row). No network, container or real remote is used.
"""

import json
import os
import subprocess
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sandbox_executor.entrypoint import executor
from sandbox_executor.entrypoint.execution_workspace import resolve_partial_head
from sandbox_executor.entrypoint.partial_resume import RESUMED, merge_plan_tip, start_execution_branch
from sandbox_executor.flow import (
    FlowContext,
    FlowStage,
    StageResult,
    StageStatus,
    admission,
    plan_refresh_stage_failure,
    run_execute_stage,
)
from sandbox_executor.flow.admission import AdmissionDecision
from sandbox_executor.git import GitRepository
from sandbox_executor.ledger import read_merged_rows_at_ref
from sandbox_executor.plan_paths import REFRESH_MERGE_PREFIX

from tests.repo_templates import copy_template, shared_template

INTENT = "I-1-x/_"
PREFIX = "I-1-x/P-2-claude-agent-claude-opus-5-5"
PLAN = f"{PREFIX}/_"
EXEC_ID = "E-100-claude-agent-claude-opus-5-5"
FAILED = f"{PREFIX}/{EXEC_ID}/_"
NEW_EXEC = f"{PREFIX}/E-200-claude-agent-claude-opus-5-5/_"
LEDGER = "holon-knowledge/ledger/executions.jsonl"


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return res.stdout.strip()


def commit_file(repo: Path, path: str, text: str, message: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as f:
        f.write(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def failure_row(exec_id: str, partial_head: str) -> str:
    row = {"entry_type": "summary", "execution_id": exec_id, "plan_branch": PLAN, "status": "failure"}
    return json.dumps({**row, "partial_head": partial_head}) + "\n"


def _build_resume_repos(root: Path) -> None:
    """A bare origin and the host worktree of ``host`` with the four pushed branches, back on ``main``."""
    origin = root / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = root / "host"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True)
    for key, value in (("user.email", "t@holon.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        git(repo, "config", key, value)
    git(repo, "checkout", "-q", "-b", "main")
    commit_file(repo, "src/app.py", "APP = 1\n", "base")
    commit_file(repo, LEDGER, json.dumps({"entry_type": "summary", "execution_id": "E-1-a"}) + "\n", "ledger")
    git(repo, "checkout", "-q", "-b", INTENT)
    commit_file(repo, "intents/I-1-x.md", "# intent\n", "intent")
    git(repo, "checkout", "-q", "-b", PLAN)
    commit_file(repo, "plans/I-1-x/P-2.md", "# plan\n", "plan")
    git(repo, "checkout", "-q", "-b", FAILED)
    partial_head = commit_file(repo, "src/app.py", "PARTIAL = 2\n", "agent work")
    commit_file(repo, f"executions/{EXEC_ID}.md", "# notes\n", "notes")
    commit_file(repo, LEDGER, failure_row(EXEC_ID, partial_head), "failure row")
    for branch in ("main", INTENT, PLAN, FAILED):
        git(repo, "push", "-q", "origin", branch)
    git(repo, "checkout", "-q", "main")


@pytest.fixture(scope="session")
def resume_repos_template(tmp_path_factory: pytest.TempPathFactory, hermetic_git_config: str) -> Path:
    """The repositories of ``_build_resume_repos``, built once and read-only; ``host`` copies them per test."""
    return shared_template(tmp_path_factory, "resume-repos-template", _build_resume_repos)[0]


@pytest.fixture
def host(resume_repos_template: Path, tmp_path: Path) -> Path:
    """The host worktree, back on main, with a pushed failed execution branch whose recorded partial head is HEAD~1."""
    return copy_template(resume_repos_template, tmp_path) / "host"


def recorded_head(repo: Path) -> str:
    return git(repo, "rev-parse", f"{FAILED}~2")


def push_failed(repo: Path, *, force: bool = False) -> None:
    git(repo, "push", "-q", *(["--force"] if force else []), "origin", FAILED)


def resume(repo: Path, merge: Callable[[str], str | None] | None = None) -> str | None:
    """Create NEW_EXEC as the executor does on a resume of FAILED, from the plan tip the host fetched."""
    plan_tip = git(repo, "rev-parse", f"refs/remotes/origin/{PLAN}")
    git_repo = GitRepository(repo)
    merge = merge or partial(
        merge_plan_tip,
        git_repo,
        configure_identity=partial(executor._configure_executor_identity, str(repo)),
        reconcile=partial(executor._reconcile_conflicted_merge, git_repo, str(repo)),
    )
    resolve = partial(resolve_partial_head, git_repo, plan_tip=plan_tip, plan_branch_prefix=PREFIX)
    return start_execution_branch(git_repo, (PLAN, NEW_EXEC), FAILED, plan_tip, (resolve, merge))


def test_resume_under_a_repeated_override_continues_the_partial_head(host: Path, monkeypatch) -> None:
    overridden = AdmissionDecision(admitted=True, reason="overlaps another flow", overridden=True)
    monkeypatch.setattr(admission, "check_admission", lambda *_a, **_k: overridden)
    for start in ("2026-11-20T00:00:00Z", "2026-11-20T01:00:00Z"):
        ctx = FlowContext(target_worktree=str(host), plan_branch=PLAN, intent_branch=INTENT, allow_overlap=True)
        assert admission.admission_stage_failure(ctx, start) is None
    plan_rows = read_merged_rows_at_ref(str(host), f"refs/remotes/origin/{PLAN}", os.path.basename(LEDGER))
    assert sum('"admission_override"' in json.dumps(row) for row in plan_rows) == 1
    plan_tip = git(host, "rev-parse", f"refs/remotes/origin/{PLAN}")
    assert git(host, "rev-parse", f"{plan_tip}~1") == git(host, "rev-parse", f"{FAILED}~3")  # one record commit

    assert resume(host) == recorded_head(host)
    assert git(host, "rev-parse", f"{NEW_EXEC}^1") == recorded_head(host)
    git(host, "merge-base", "--is-ancestor", plan_tip, NEW_EXEC)  # raises unless it descends from today's plan tip
    assert "PARTIAL = 2" in (host / "src/app.py").read_text(encoding="utf-8")


def test_a_plan_tip_moved_by_more_than_ledgers_is_refused(host: Path) -> None:
    git(host, "checkout", "-q", PLAN)
    commit_file(host, "src/app.py", "# plan revision\n", "plan revision")
    git(host, "push", "-q", "origin", PLAN)
    plan_tip = git(host, "rev-parse", "HEAD")
    start = resolve_partial_head(GitRepository(host), FAILED, plan_tip, PREFIX)
    assert start.head is None
    assert start.reason.startswith(
        "not a descendant of the plan tip, and the plan branch changed more than its ledgers"
    )


def test_a_partial_head_forked_on_the_intent_branch_is_refused(host: Path) -> None:
    git(host, "checkout", "-q", FAILED)
    git(host, "reset", "-q", "--hard", f"origin/{INTENT}")
    partial_head = commit_file(host, "src/app.py", "OFF_PLAN = 3\n", "agent work off the plan")
    commit_file(host, LEDGER, failure_row(EXEC_ID, partial_head), "failure row")
    push_failed(host, force=True)
    git(host, "checkout", "-q", "main")
    plan_tip = git(host, "rev-parse", f"refs/remotes/origin/{PLAN}")
    start = resolve_partial_head(GitRepository(host), FAILED, plan_tip, PREFIX)
    assert start.head is None and "forked off the plan branch history" in start.reason


def move_main(repo: Path) -> None:
    git(repo, "checkout", "-q", "main")
    commit_file(repo, "docs/main.md", "merged while the flow waited\n", "overlapping change")
    git(repo, "push", "-q", "origin", "main")


def test_partial_work_forked_before_a_refresh_merge_resumes(host: Path) -> None:
    move_main(host)
    waited = {"state": "admitted", "waited_since": "2026-11-20T01:00:00.000Z"}
    ctx = FlowContext(target_worktree=str(host), plan_branch=PLAN, intent_branch=INTENT, admission=waited)
    assert plan_refresh_stage_failure(ctx, "2026-11-20T02:00:00Z") is None
    plan_tip = git(host, "rev-parse", f"refs/remotes/origin/{PLAN}")
    assert git(host, "log", "-1", "--format=%s", f"{plan_tip}~1").startswith(REFRESH_MERGE_PREFIX)

    assert resume(host) == recorded_head(host)
    git(host, "merge-base", "--is-ancestor", plan_tip, NEW_EXEC)  # raises unless it descends from today's plan tip
    assert "PARTIAL = 2" in (host / "src/app.py").read_text(encoding="utf-8")
    assert (host / "docs/main.md").exists()


@pytest.mark.parametrize("subject", [f"{REFRESH_MERGE_PREFIX}P-2 onto origin/main", "Merge main into the plan"])
def test_a_merge_that_is_not_a_clean_refresh_is_refused(host: Path, subject: str) -> None:
    move_main(host)
    git(host, "checkout", "-q", PLAN)
    git(host, "merge", "-q", "--no-ff", "--no-commit", "main")
    if subject.startswith(REFRESH_MERGE_PREFIX):  # a forged refresh message on a merge that also edits the plan
        commit_file(host, "src/extra.py", "EXTRA = 1\n", subject)
    else:
        git(host, "commit", "-q", "-m", subject)
    git(host, "push", "-q", "origin", PLAN)
    start = resolve_partial_head(GitRepository(host), FAILED, git(host, "rev-parse", "HEAD"), PREFIX)
    assert start.head is None and "the plan branch changed more than its ledgers" in start.reason


def test_resume_starts_from_a_merge_commit_on_the_branch_tip(host: Path) -> None:
    rows_before = git(host, "show", f"refs/remotes/origin/{FAILED}:{LEDGER}")
    git(host, "checkout", "-q", "-b", "upstream", "main")  # a local stand-in for a newer origin/main
    commit_file(host, "docs/upstream.md", "# upstream\n", "upstream change")
    git(host, "checkout", "-q", FAILED)
    git(host, "merge", "-q", "--no-edit", "upstream")
    merge_commit = git(host, "rev-parse", "HEAD")
    push_failed(host)
    git(host, "checkout", "-q", "main")

    plan_tip = git(host, "rev-parse", f"refs/remotes/origin/{PLAN}")
    start = resolve_partial_head(GitRepository(host), FAILED, plan_tip, PREFIX)
    assert start.head == merge_commit
    assert start.reason.startswith(f"resumed from the branch tip {merge_commit[:12]}, 4 commit(s) after")
    assert resume(host) == merge_commit
    assert git(host, "rev-parse", NEW_EXEC) == merge_commit
    assert git(host, "show", f"refs/remotes/origin/{FAILED}:{LEDGER}") == rows_before  # no hand-written row


def test_a_tip_that_lost_the_recorded_head_falls_back_and_says_why(host: Path, capsys) -> None:
    head = recorded_head(host)
    git(host, "checkout", "-q", FAILED)
    git(host, "reset", "-q", "--hard", f"origin/{PLAN}")
    commit_file(host, "src/other.py", "REWRITTEN = 1\n", "rewritten work")
    tip = commit_file(host, LEDGER, failure_row(EXEC_ID, head), "failure row")
    push_failed(host, force=True)
    git(host, "checkout", "-q", "main")

    assert resume(host) == head
    err = capsys.readouterr().err
    assert f"the branch tip {tip[:12]} does not descend from the recorded partial head {head[:12]}" in err
    assert "resuming from the recorded head" in err
    assert git(host, "rev-parse", NEW_EXEC) == head


def test_a_tip_with_only_the_failure_commit_resumes_the_recorded_head(host: Path) -> None:
    plan_tip = git(host, "rev-parse", f"refs/remotes/origin/{PLAN}")
    start = resolve_partial_head(GitRepository(host), FAILED, plan_tip, PREFIX)
    assert (start.head, start.reason) == (recorded_head(host), RESUMED)


def move_plan_by_a_ledger_row(repo: Path) -> str:
    git(repo, "checkout", "-q", PLAN)
    row = json.dumps({"entry_type": "admission_override", "kind": "admission_override", "plan_branch": PLAN})
    plan_tip = commit_file(repo, LEDGER, row + "\n", "execute: record the --allow-overlap admission override")
    git(repo, "push", "-q", "origin", PLAN)
    git(repo, "checkout", "-q", "main")
    return plan_tip


def test_a_ledger_conflict_with_the_plan_tip_is_reconciled(host: Path) -> None:
    head = recorded_head(host)
    git(host, "checkout", "-q", FAILED)
    commit_file(host, "src/later.py", "LATER = 1\n", "human fix after the failure")
    push_failed(host)
    git(host, "checkout", "-q", "main")
    plan_tip = move_plan_by_a_ledger_row(host)
    plan_ledger = git(host, "show", f"{plan_tip}:{LEDGER}")

    assert resume(host) == git(host, "rev-parse", FAILED)
    git(host, "merge-base", "--is-ancestor", plan_tip, NEW_EXEC)
    merged = git(host, "show", f"{NEW_EXEC}:{LEDGER}")
    assert merged.startswith(plan_ledger) and f'"partial_head": "{head}"' in merged


def test_a_failed_plan_tip_merge_falls_back_to_the_plan_tip(host: Path, capsys) -> None:
    plan_tip = move_plan_by_a_ledger_row(host)
    assert resume(host, merge=lambda _tip: "conflict in src/app.py") is None
    err = capsys.readouterr().err
    assert "could not merge the current plan tip into the partial work: conflict in src/app.py" in err
    assert git(host, "rev-parse", NEW_EXEC) == plan_tip


def stage_context(repo: Path) -> FlowContext:
    ctx = FlowContext(
        repo_dir=str(repo), plan_branch=PLAN, intent_data={"slug": "x"}, agent="claude-agent", model="claude-opus-5-5"
    )
    ctx.stage_results["intent"] = StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)
    ctx.stage_results["plan"] = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
    return ctx


def run_stage_with(repo: Path, executor_commits: Any) -> FlowContext:
    """Run Stage 3 with a fake executor that commits on a new execution branch and exits 1."""
    ctx = stage_context(repo)

    def dispatch(_ctx: FlowContext, _role: str, _args: list[str]) -> int:
        executor_commits(repo)
        return 1

    with (
        patch("sandbox_executor.flow.stages.execute.at_stage_boundary", return_value=None),
        patch("sandbox_executor.flow.stages.execute.fetch_base"),
        patch("sandbox_executor.flow.stages.execute._plan_budget_decision", return_value=None),
        patch("sandbox_executor.flow.stages.execute._dispatch_role", side_effect=dispatch),
    ):
        result = run_execute_stage(ctx)
    assert result.status == StageStatus.FAILED
    git(repo, "checkout", "-q", "main")
    return ctx


def crash_before_row(repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", NEW_EXEC, f"origin/{PLAN}")
    commit_file(repo, "src/app.py", "CRASHED = 1\n", "agent work before the crash")


def fail_with_row(repo: Path) -> None:
    crash_before_row(repo)
    commit_file(repo, LEDGER, failure_row("E-200-claude-agent-claude-opus-5-5", "0" * 40), "failure row")


def test_an_attempt_that_fails_before_its_row_leaves_no_pushed_branch(host: Path) -> None:
    ctx = run_stage_with(host, crash_before_row)
    assert git(host, "ls-remote", "origin", f"refs/heads/{NEW_EXEC}") == ""
    assert git(host, "rev-parse", "--verify", f"refs/heads/{NEW_EXEC}")  # kept local for the human
    held = f"Stage 3: execution branch '{NEW_EXEC}' has no ledger row; kept local and not pushed."
    assert any(line.endswith(held) for line in ctx.logs)


def test_an_attempt_that_records_its_row_is_pushed(host: Path) -> None:
    ctx = run_stage_with(host, fail_with_row)
    assert git(host, "ls-remote", "origin", f"refs/heads/{NEW_EXEC}") != ""
    assert not any("has no ledger row" in line for line in ctx.logs)
