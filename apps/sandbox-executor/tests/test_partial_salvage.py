"""Hermetic tests of the Stage 3 resume salvage: interrupted edits become a labelled partial-work commit."""

import functools
import json
import os
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
from sandbox_executor.flow.partial_salvage import (
    SalvageError,
    _publish_partial,
    _salvage_row,
    leftover_paths,
    salvage_leftovers,
)
from sandbox_executor.ledger import read_merged_rows_at_ref

from scripts.normalise_ledgers import canonical_created_at
from tests.repo_templates import copy_template, shared_template

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


def _build_work(root: Path) -> None:
    """A clone of a local bare origin, on a fresh local execution branch of the plan (as a killed run leaves it)."""
    origin = root / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = root / "work"
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


@pytest.fixture
def work(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A private copy of the clone ``_build_work`` builds once per run, and its origin."""
    copy_template(shared_template(tmp_path_factory, "partial-salvage", _build_work)[0], tmp_path)
    return tmp_path / "work"


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
    return read_merged_rows_at_ref(str(repo), ref, os.path.basename(LEDGER))


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
        patch("sandbox_executor.flow.stages.execute.at_stage_boundary", return_value=None),
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


def test_a_never_pushed_branch_reaches_origin_only_with_its_row(work: Path, monkeypatch) -> None:
    (work / "tests" / "conftest.py").write_text("# interrupted\n", encoding="utf-8")
    monkeypatch.setattr(
        "sandbox_executor.flow.execution_publish._append_superseding_execution_row", lambda *_a: "push rejected"
    )
    with pytest.raises(SalvageError, match="push rejected"):
        salvage_leftovers(context(work), prefix())
    assert git(work, "ls-remote", "origin", EXEC_BRANCH) == ""
    assert subprocess.run(
        ["git", "rev-parse", "--verify", "-q", f"refs/remotes/origin/{EXEC_BRANCH}"], cwd=work
    ).returncode


def test_sidecar_scratch_files_are_excluded_from_salvage_and_logged(work: Path) -> None:
    (work / "tests" / "conftest.py").write_text("# real interrupted edit\n", encoding="utf-8")
    (work / "mitm_wire_logs").mkdir()
    (work / "mitm_wire_logs" / "wire_log_1.json").write_text('{"prompt": "secret"}\n', encoding="utf-8")
    (work / "cache").mkdir()
    (work / "cache" / "llm_cache.db").write_bytes(b"SQLite format 3\x00")
    ctx = context(work)
    result = salvage_leftovers(ctx, prefix())
    assert result.files == ["tests/conftest.py"]
    assert git(work, "show", "--name-only", "--format=", result.sha) == "tests/conftest.py"
    tree = set(git(work, "ls-tree", "-r", "--name-only", result.sha).splitlines())
    assert "mitm_wire_logs/wire_log_1.json" not in tree
    assert "cache/llm_cache.db" not in tree
    joined_logs = "\n".join(ctx.logs)
    assert "mitm_wire_logs/wire_log_1.json" in joined_logs
    assert "cache/llm_cache.db" in joined_logs


def _dirty_code_and_notes(work: Path) -> None:
    (work / "tests" / "conftest.py").write_text("# interrupted\n", encoding="utf-8")
    (work / "executions").mkdir()
    (work / "executions" / f"{EXEC_ID}.md").write_text("# notes\n", encoding="utf-8")


def test_include_notes_commits_the_branch_notes_and_marks_the_row(work: Path) -> None:
    _dirty_code_and_notes(work)
    result = salvage_leftovers(context(work), prefix(), include_notes=True)
    assert sorted(result.files) == [f"executions/{EXEC_ID}.md", "tests/conftest.py"]
    assert git(work, "status", "--porcelain") == ""
    assert rows(work)[-1]["adopted_unrecorded"] is True


def test_notes_stay_uncommitted_by_default(work: Path) -> None:
    _dirty_code_and_notes(work)
    result = salvage_leftovers(context(work), prefix())
    assert git(work, "show", "--name-only", "--format=", result.sha) == "tests/conftest.py"
    assert "adopted_unrecorded" not in rows(work)[-1]


def test_publish_partial_on_an_origin_only_branch_appends_the_row_without_a_push(work: Path) -> None:
    (work / "a.py").write_text("work\n", encoding="utf-8")
    commit_all(work, "agent work")
    tip = git(work, "rev-parse", "HEAD")
    git(work, "push", "-q", "origin", EXEC_BRANCH)
    git(work, "checkout", "-q", PLAN)
    git(work, "branch", "-q", "-D", EXEC_BRANCH)
    ctx, target = context(work), (EXEC_BRANCH, EXEC_ID)
    with patch("sandbox_executor.flow.partial_salvage.push_branch") as push:
        _publish_partial(ctx, target, tip, prefix(), functools.partial(_salvage_row, ctx, target, tip, 0))
    push.assert_not_called()
    assert failed_partial_head(str(work), f"refs/remotes/origin/{EXEC_BRANCH}", EXEC_ID) == tip


def test_salvage_row_from_a_prior_row_stamps_the_canonical_z_created_at(work: Path) -> None:
    prior = {
        "entry_type": "summary",
        "execution_id": EXEC_ID,
        "plan_branch": PLAN,
        "status": "failure",
        "agent": "prior-agent",
        "ledger_revision": 1,
        "created_at": "2026-01-01T00:00:00.000000+00:00",
    }
    with (work / LEDGER).open("a", encoding="utf-8") as ledger:
        ledger.write(json.dumps(prior) + "\n")
    commit_all(work, "prior row")
    git(work, "push", "-q", "origin", EXEC_BRANCH)
    sha = git(work, "rev-parse", "HEAD")

    row = _salvage_row(context(work), (EXEC_BRANCH, EXEC_ID), sha, 1)

    assert row["created_at"].endswith("Z")
    assert "+00:00" not in row["created_at"]
    assert canonical_created_at(row["created_at"]) == row["created_at"]
    assert row["ledger_revision"] == 2
    assert row["agent"] == "prior-agent"
