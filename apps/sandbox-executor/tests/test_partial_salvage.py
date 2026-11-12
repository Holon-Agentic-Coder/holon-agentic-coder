"""Hermetic tests of the Stage 3 resume salvage: interrupted edits become a labelled partial-work commit."""

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sandbox_executor.flow import (
    FlowContext,
    FlowStage,
    StageResult,
    StageStatus,
    failed_partial_head,
    run_execute_stage,
)
from sandbox_executor.flow.partial_salvage import SalvageError, leftover_paths, salvage_leftovers

PLAN = "I-1-x/P-2-claude-agent-claude-opus-5-5/_"
EXEC_ID = "E-100-claude-agent-claude-opus-5-5"
EXEC_BRANCH = f"I-1-x/P-2-claude-agent-claude-opus-5-5/{EXEC_ID}/_"
LEDGER = "holon-knowledge/ledger/executions.jsonl"


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return res.stdout.strip()


def commit_all(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def work(tmp_path: Path) -> Path:
    """A clone of a local bare origin, on a fresh local execution branch of the plan (as a killed run leaves it)."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True)
    for key, value in (("user.email", "t@holon.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        git(repo, "config", key, value)
    git(repo, "checkout", "-q", "-b", "main")
    (repo / "tests").mkdir()
    (repo / "tests" / "conftest.py").write_text("# original\n", encoding="utf-8")
    (repo / "holon-knowledge" / "ledger").mkdir(parents=True)
    (repo / LEDGER).write_text(json.dumps({"entry_type": "summary", "execution_id": "E-1-a"}) + "\n", encoding="utf-8")
    (repo / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
    commit_all(repo, "base")
    git(repo, "push", "-q", "origin", "main")
    git(repo, "checkout", "-q", "-b", PLAN)
    git(repo, "push", "-q", "origin", PLAN)
    git(repo, "checkout", "-q", "-b", EXEC_BRANCH)
    return repo


def context(repo: Path) -> FlowContext:
    return FlowContext(
        repo_dir=str(repo),
        plan_branch=PLAN,
        intent_data={"slug": "x"},
        agent="claude-agent",
        model="claude-opus-5-5",
    )


def prefix() -> str:
    return PLAN.removesuffix("/_")


def rows(repo: Path, ref: str = f"origin/{EXEC_BRANCH}") -> list[dict[str, Any]]:
    return [json.loads(line) for line in git(repo, "show", f"{ref}:{LEDGER}").splitlines()]


def test_dirty_tracked_file_is_committed_and_recorded(work: Path) -> None:
    (work / "tests" / "conftest.py").write_text("# half-written helper\n", encoding="utf-8")
    result = salvage_leftovers(context(work), prefix())
    assert result.branch == EXEC_BRANCH
    assert result.files == ["tests/conftest.py"]
    assert git(work, "status", "--porcelain") == ""
    assert (
        git(work, "log", "-1", "--format=%s", result.sha)
        == f"execute: {EXEC_ID} partial work (interrupted) for plan {PLAN}"
    )
    assert failed_partial_head(str(work), f"refs/remotes/origin/{EXEC_BRANCH}", EXEC_ID) == result.sha
    last = rows(work)[-1]
    assert last["salvaged"] is True and last["salvaged_file_count"] == 1 and last["status"] == "failure"


def test_dirty_untracked_file_is_committed(work: Path) -> None:
    (work / "tests" / "network_guard.py").write_text("# new\n", encoding="utf-8")
    (work / "ignored.log").write_text("noise\n", encoding="utf-8")
    result = salvage_leftovers(context(work), prefix())
    assert result.files == ["tests/network_guard.py"]
    assert git(work, "show", "--name-only", "--format=", result.sha) == "tests/network_guard.py"
    assert (work / "ignored.log").exists()


def test_no_leftovers_changes_nothing(work: Path) -> None:
    head = git(work, "rev-parse", "HEAD")
    result = salvage_leftovers(context(work), prefix())
    assert (result.branch, result.sha, result.files) == (None, None, [])
    assert git(work, "rev-parse", "HEAD") == head
    assert git(work, "branch", "--show-current") == EXEC_BRANCH
    assert git(work, "ls-remote", "origin", EXEC_BRANCH) == ""


def test_existing_partial_commits_stay_intact(work: Path) -> None:
    (work / "a.py").write_text("one\n", encoding="utf-8")
    commit_all(work, "agent work")
    first = git(work, "rev-parse", "HEAD")
    (work / "b.py").write_text("two\n", encoding="utf-8")
    result = salvage_leftovers(context(work), prefix())
    assert git(work, "rev-parse", f"{result.sha}~1") == first
    assert failed_partial_head(str(work), f"refs/remotes/origin/{EXEC_BRANCH}", EXEC_ID) == result.sha


def test_only_engine_owned_files_dirty_makes_no_commit(work: Path) -> None:
    head = git(work, "rev-parse", "HEAD")
    (work / LEDGER).write_text("{}\n", encoding="utf-8")
    (work / "executions").mkdir()
    (work / "executions" / f"{EXEC_ID}.md").write_text("# notes\n", encoding="utf-8")
    assert leftover_paths(str(work)) == []
    result = salvage_leftovers(context(work), prefix())
    assert result.sha is None
    assert git(work, "rev-parse", "HEAD") == head
    assert git(work, "ls-remote", "origin", EXEC_BRANCH) == ""


def test_mixed_edits_commit_only_real_work_and_keep_ledger_bytes(work: Path) -> None:
    ledger_before = (work / LEDGER).read_bytes()
    (work / LEDGER).write_bytes(ledger_before + b'{"engine": true}\n')
    (work / "tests" / "conftest.py").write_text("# real\n", encoding="utf-8")
    result = salvage_leftovers(context(work), prefix())
    assert result.files == ["tests/conftest.py"]
    assert git(work, "show", "--name-only", "--format=", result.sha) == "tests/conftest.py"
    partial_rows = git(work, "show", f"{result.sha}:{LEDGER}").encode() + b"\n"
    assert partial_rows == ledger_before
    assert rows(work)[0] == {"entry_type": "summary", "execution_id": "E-1-a"}


@pytest.mark.parametrize("branch", ["main", PLAN])
def test_dirty_worktree_on_other_branch_is_refused_untouched(work: Path, branch: str) -> None:
    git(work, "checkout", "-q", branch)
    (work / "tests" / "conftest.py").write_text("# edit\n", encoding="utf-8")
    head = git(work, "rev-parse", "HEAD")
    with pytest.raises(SalvageError, match="not an execution branch"):
        salvage_leftovers(context(work), prefix())
    assert git(work, "rev-parse", "HEAD") == head
    assert git(work, "status", "--porcelain") == "M tests/conftest.py"


def stage_args(work: Path) -> list[str]:
    ctx = context(work)
    ctx.stage_results["intent"] = StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)
    ctx.stage_results["plan"] = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
    dispatched: list[list[str]] = []

    def dispatch(_ctx: FlowContext, _role: str, args: list[str]) -> int:
        dispatched.append(list(args))
        return 1

    with (
        patch("sandbox_executor.flow.stages.execute.check_and_resolve_stage_conflicts"),
        patch("sandbox_executor.flow.stages.execute.fetch_base"),
        patch("sandbox_executor.flow.stages.execute.publish_new_child_branches", return_value=[]),
        patch("sandbox_executor.flow.stages.execute._plan_budget_decision", return_value=None),
        patch("sandbox_executor.flow.stages.execute._dispatch_role", side_effect=dispatch),
    ):
        run_execute_stage(ctx, resume_partial=True)
    assert len(dispatched) == 1
    return dispatched[0]


def test_resume_dispatches_the_salvaged_branch(work: Path) -> None:
    (work / "tests" / "conftest.py").write_text("# interrupted\n", encoding="utf-8")
    assert stage_args(work) == [PLAN, "claude-agent", "claude-opus-5-5", EXEC_BRANCH]


def test_clean_resume_dispatches_three_arguments(work: Path) -> None:
    assert stage_args(work) == [PLAN, "claude-agent", "claude-opus-5-5"]
