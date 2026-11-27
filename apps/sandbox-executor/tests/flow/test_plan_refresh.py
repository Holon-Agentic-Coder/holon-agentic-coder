from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor.flow import (
    PLAN_REFRESH_KIND,
    FlowContext,
    FlowStage,
    StageResult,
    StageStatus,
    plan_refresh_stage_failure,
    run_execute_stage,
)
from sandbox_executor.flow.admission import check_admission
from sandbox_executor.flow.admission_decision import AdmissionDecision
from sandbox_executor.flow.checkpoint import load_checkpoint
from sandbox_executor.flow.checkpoint_discovery import get_default_checkpoint_path
from sandbox_executor.ledger import build_execution_id, read_merged_rows_at_ref
from sandbox_executor.plan_paths import REFRESH_MERGE_PREFIX

from tests.repo_templates import copy_template, shared_template

INTENT = "I-1-x/_"
PREFIX = "I-1-x/P-2-claude-agent-claude-opus-5-5"
PLAN = f"{PREFIX}/_"
AGENT, MODEL = "claude-agent", "claude-opus-5-5"
EXECUTIONS = "holon-knowledge/ledger/executions.jsonl"
PLANS = "holon-knowledge/ledger/plans.jsonl"
# why: the root conftest stubs check_admission for every test; these tests need the real admission state
REAL_CHECK = check_admission
WAITED_SINCE = "2026-11-20T01:00:00.000Z"
BLOCKED = {
    "state": "blocked",
    "blocked_since": WAITED_SINCE,
    "blocked_by": [{"kind": "pull_request", "number": 7, "branch": "feature", "files": ["src/app.py"]}],
}

REFRESHED = {"state": "admitted", "waited_since": WAITED_SINCE, "refreshed_branch": PLAN}


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return res.stdout.strip()


def row(name: str, created_at: str) -> str:
    return json.dumps({"entry_type": "note", "id": name, "created_at": created_at}) + "\n"


def commit_and_push_on_branch(
    repo: Path, branch: str, files: dict[str, str], message: str, *, append: bool = True
) -> str:
    git(repo, "checkout", "-q", "-B", branch, f"origin/{branch}")
    for path, text in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a" if append else "w", encoding="utf-8") as f:
            f.write(text)
        git(repo, "add", path)
    git(repo, "commit", "-q", "-m", message)
    git(repo, "push", "-q", "origin", branch)
    git(repo, "checkout", "-q", "main")
    return git(repo, "rev-parse", f"origin/{branch}")


def _build_host_on_main_with_plan_branch(root: Path, *, main_moved: bool) -> str | None:
    origin = root / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = root / "host"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True)
    for key, value in (("user.email", "t@holon.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        git(repo, "config", key, value)
    git(repo, "checkout", "-q", "-b", "main")
    base = {"src/app.py": "APP = 1\n", EXECUTIONS: row("base", "2026-11-19T00:00:00.000Z"), PLANS: row("p", "x")}
    for path, text in base.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "push", "-q", "origin", "main")
    for parent, branch, path in (("main", INTENT, "intents/I-1-x.md"), (INTENT, PLAN, "plans/I-1-x/P-2.md")):
        git(repo, "push", "-q", "origin", f"refs/remotes/origin/{parent}:refs/heads/{branch}")
        git(repo, "fetch", "-q", "origin")
        commit_and_push_on_branch(repo, branch, {path: "# doc\n"}, f"add {path}")
    return merge_overlapping_change_on_main(repo) if main_moved else None


def _private_copy_of_host_template(
    tmp_path_factory: pytest.TempPathFactory, tmp_path: Path, *, main_moved: bool
) -> tuple[Path, str | None]:
    name = f"plan-refresh-host{'-main-moved' if main_moved else ''}"
    template, main_sha = shared_template(
        tmp_path_factory, name, lambda root: _build_host_on_main_with_plan_branch(root, main_moved=main_moved)
    )
    copy_template(template, tmp_path)
    return tmp_path / "host", main_sha


@pytest.fixture
def host(tmp_path_factory: pytest.TempPathFactory, tmp_path: Path) -> Path:
    return _private_copy_of_host_template(tmp_path_factory, tmp_path, main_moved=False)[0]


@pytest.fixture
def host_with_overlap_merged_on_main(tmp_path_factory: pytest.TempPathFactory, tmp_path: Path) -> tuple[Path, str]:
    repo, main_sha = _private_copy_of_host_template(tmp_path_factory, tmp_path, main_moved=True)
    assert main_sha is not None
    return repo, main_sha


def merge_overlapping_change_on_main(repo: Path, files: dict[str, str] | None = None) -> str:
    return commit_and_push_on_branch(
        repo, "main", files or {"docs/overlap.md": "merged while the flow waited\n"}, "overlap"
    )


def stage_context(repo: Path, admission: dict | None) -> FlowContext:
    ctx = FlowContext(
        target_worktree=str(repo),
        plan_branch=PLAN,
        intent_branch=INTENT,
        intent_data={"slug": "x", "target_branch": "main"},
        agent=AGENT,
        model=MODEL,
        admission=admission,
    )
    ctx.stage_results["intent"] = StageResult(stage=FlowStage.INTENT, status=StageStatus.SUCCESS)
    ctx.stage_results["plan"] = StageResult(stage=FlowStage.PLAN, status=StageStatus.SUCCESS)
    return ctx


class PlanTipRecordingExecutor:
    def __init__(self, repo: Path) -> None:
        self.repo, self.plan_tips, self.branches = repo, [], []

    def __call__(self, _ctx: FlowContext, _role: str, _args: list[str]) -> int:
        tip = git(self.repo, "rev-parse", f"refs/remotes/origin/{PLAN}")
        exec_id = build_execution_id(int(time.time()) + len(self.branches), PREFIX, AGENT, MODEL)
        branch = f"{PREFIX}/{exec_id}/_"
        self.plan_tips.append(tip)
        self.branches.append(branch)
        git(self.repo, "checkout", "-q", "-b", branch, tip)
        (self.repo / "src/feature.py").write_text("FEATURE = 1\n", encoding="utf-8")
        summary = {"entry_type": "summary", "execution_id": exec_id, "plan_branch": PLAN, "status": "success"}
        with open(self.repo / EXECUTIONS, "a", encoding="utf-8") as f:
            f.write(json.dumps({**summary, "created_at": "2026-11-20T09:00:00.000Z"}) + "\n")
        git(self.repo, "add", "src/feature.py", EXECUTIONS)
        git(self.repo, "commit", "-q", "-m", "execution")
        return 0


@contextmanager
def stage_seams(executor: PlanTipRecordingExecutor) -> Iterator[None]:
    admitted = AdmissionDecision(admitted=True)
    with (
        patch("sandbox_executor.flow.stages.execute.at_stage_boundary", return_value=None),
        patch("sandbox_executor.flow.stages.execute._plan_budget_decision", return_value=None),
        patch("sandbox_executor.flow.stages.execute.touch_set_violation_result", return_value=None),
        patch("sandbox_executor.flow.stages.execute._dispatch_role", side_effect=executor),
        patch("sandbox_executor.flow.admission.check_admission", REAL_CHECK),
        patch("sandbox_executor.flow.admission.decide_admission", return_value=admitted),
    ):
        yield


def run_stage(repo: Path, ctx: FlowContext, executor: PlanTipRecordingExecutor) -> StageResult:
    with stage_seams(executor):
        result = run_execute_stage(ctx)
    git(repo, "checkout", "-q", "main")
    return result


def saved_admission(repo: Path) -> dict | None:
    return load_checkpoint(get_default_checkpoint_path(str(repo), "x")).context.admission


def refresh_rows(repo: Path, ref: str = f"refs/remotes/origin/{PLAN}") -> list[dict]:
    rows = read_merged_rows_at_ref(str(repo), ref, "executions.jsonl")
    return [r for r in rows if r.get("kind") == PLAN_REFRESH_KIND]


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=repo).returncode == 0


def resolve_conflict_by_merging_main_into_plan(repo: Path) -> None:
    # why: a merge keeps origin/main an ancestor of the plan tip, so the resumed refresh is a no-op
    git(repo, "checkout", "-q", "-B", PLAN, f"origin/{PLAN}")
    subprocess.run(["git", "merge", "-q", "origin/main"], cwd=repo, capture_output=True)
    (repo / "src/app.py").write_text("APP = 23\n", encoding="utf-8")
    git(repo, "add", "src/app.py")
    git(repo, "commit", "-q", "--no-edit")
    git(repo, "push", "-q", "origin", PLAN)
    git(repo, "checkout", "-q", "main")


def test_ac1_a_flow_admitted_after_a_wait_gets_main_merged_before_dispatch(
    host_with_overlap_merged_on_main: tuple[Path, str],
) -> None:
    host, main_sha = host_with_overlap_merged_on_main
    old_tip = git(host, "rev-parse", f"origin/{PLAN}")
    executor = PlanTipRecordingExecutor(host)

    result = run_stage(host, stage_context(host, BLOCKED), executor)

    assert result.status == StageStatus.SUCCESS, result.error
    refreshed_plan_tip = git(host, "rev-parse", f"origin/{PLAN}")
    assert executor.plan_tips == [refreshed_plan_tip]
    assert is_ancestor(host, main_sha, refreshed_plan_tip) and is_ancestor(
        host, main_sha, f"origin/{executor.branches[0]}"
    )
    plan_branch_was_not_force_pushed = is_ancestor(host, old_tip, refreshed_plan_tip)
    assert plan_branch_was_not_force_pushed
    merge = git(host, "rev-parse", f"{refreshed_plan_tip}~1")
    assert git(host, "rev-list", "--parents", "-n", "1", merge).split()[1:] == [old_tip, main_sha]
    assert git(host, "log", "-1", "--format=%s", merge).startswith(REFRESH_MERGE_PREFIX)
    fast_forwarded_local_plan_tip = git(host, "rev-parse", f"refs/heads/{PLAN}")
    assert fast_forwarded_local_plan_tip == refreshed_plan_tip

    assert saved_admission(host) == {**REFRESHED, "refreshed_to": main_sha}
    assert result.payload["plan_refreshed_to"] == main_sha
    [recorded] = refresh_rows(host)
    assert recorded["refreshed_to"] == main_sha and recorded["merge_commit"] == merge
    assert recorded["plan_branch"] == PLAN and recorded["waited_since"] == WAITED_SINCE
    execution_branch_ref = f"refs/remotes/origin/{executor.branches[0]}"
    assert len(refresh_rows(host, execution_branch_ref)) == 1


def test_ac2_a_flow_admitted_on_its_first_check_is_left_untouched(
    host_with_overlap_merged_on_main: tuple[Path, str],
) -> None:
    host, _main_sha = host_with_overlap_merged_on_main
    old_tip = git(host, "rev-parse", f"origin/{PLAN}")
    executor = PlanTipRecordingExecutor(host)

    result = run_stage(host, stage_context(host, None), executor)

    assert result.status == StageStatus.SUCCESS, result.error
    assert git(host, "rev-parse", f"origin/{PLAN}") == old_tip and executor.plan_tips == [old_tip]
    assert refresh_rows(host) == [] and "plan_refreshed_to" not in result.payload
    assert saved_admission(host) == {"state": "admitted"}


def test_ac3_ledger_only_conflicts_are_interleaved_by_created_at(host: Path) -> None:
    commit_and_push_on_branch(
        host, PLAN, {EXECUTIONS: row("plan-1", "2026-11-20T01:00:00.000Z"), PLANS: row("plan", "1")}, "plan"
    )
    commit_and_push_on_branch(host, PLAN, {EXECUTIONS: row("plan-3", "2026-11-20T03:00:00.000Z")}, "plan again")
    main_sha = merge_overlapping_change_on_main(
        host, {EXECUTIONS: row("main-2", "2026-11-20T02:00:00.000Z"), PLANS: row("main", "2")}
    )
    base = git(host, "show", f"origin/main~1:{EXECUTIONS}") + "\n"
    executor = PlanTipRecordingExecutor(host)

    result = run_stage(host, stage_context(host, BLOCKED), executor)

    assert result.status == StageStatus.SUCCESS, result.error
    assert len(executor.plan_tips) == 1
    merged = git(host, "show", f"origin/{PLAN}:{EXECUTIONS}") + "\n"
    assert merged.startswith(base) and "<<<<<<<" not in merged
    appended = [json.loads(line) for line in merged[len(base) :].splitlines()]
    assert [r.get("id") for r in appended if r.get("entry_type") == "note"] == ["plan-1", "main-2", "plan-3"]
    assert "<<<<<<<" not in git(host, "show", f"origin/{PLAN}:{PLANS}")
    assert saved_admission(host)["refreshed_to"] == main_sha


def test_ac4_a_non_ledger_conflict_stops_stage_3_before_dispatch_and_stays_resumable(host: Path) -> None:
    old_tip = commit_and_push_on_branch(host, PLAN, {"src/app.py": "APP = 2\n"}, "plan edits app", append=False)
    main_sha = merge_overlapping_change_on_main(host, {"src/app.py": "APP = 3\n", "README.md": "readme\n"})
    executor = PlanTipRecordingExecutor(host)

    result = run_stage(host, stage_context(host, BLOCKED), executor)

    assert result.status == StageStatus.FAILED and executor.plan_tips == []
    assert result.payload["dispatched"] is False and result.payload["plan_refresh_conflicts"] == ["src/app.py"]
    assert "conflicts in src/app.py" in str(result.error) and "Stage 3 stopped before dispatch" in str(result.error)
    assert git(host, "rev-parse", f"origin/{PLAN}") == old_tip
    assert len(git(host, "worktree", "list").splitlines()) == 1
    assert subprocess.run(["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=host).returncode != 0
    refresh_still_due = {"state": "admitted", "waited_since": WAITED_SINCE}
    assert saved_admission(host) == refresh_still_due

    resolve_conflict_by_merging_main_into_plan(host)
    resumed = load_checkpoint(get_default_checkpoint_path(str(host), "x")).context

    again = run_stage(host, resumed, executor)

    assert again.status == StageStatus.SUCCESS, again.error
    assert executor.plan_tips == [git(host, "rev-parse", f"origin/{PLAN}")]
    assert saved_admission(host)["refreshed_to"] == main_sha
    [recorded] = refresh_rows(host)
    assert recorded["refreshed_to"] == main_sha and recorded["merge_commit"] is None


def test_a_rerun_after_a_lost_checkpoint_write_records_no_second_row(
    host_with_overlap_merged_on_main: tuple[Path, str],
) -> None:
    host, main_sha = host_with_overlap_merged_on_main
    waited = {"state": "admitted", "waited_since": WAITED_SINCE}
    assert plan_refresh_stage_failure(stage_context(host, dict(waited)), "t") is None
    tip = git(host, "rev-parse", f"origin/{PLAN}")

    admission_that_lost_refreshed_to = dict(waited)
    assert plan_refresh_stage_failure(stage_context(host, admission_that_lost_refreshed_to), "t") is None

    assert git(host, "rev-parse", f"origin/{PLAN}") == tip and len(refresh_rows(host)) == 1
    assert saved_admission(host)["refreshed_to"] == main_sha


def test_a_dry_run_only_logs_the_refresh(host_with_overlap_merged_on_main: tuple[Path, str]) -> None:
    host, _main_sha = host_with_overlap_merged_on_main
    old_tip = git(host, "rev-parse", f"origin/{PLAN}")
    ctx = stage_context(host, BLOCKED)
    ctx.dry_run = True

    assert plan_refresh_stage_failure(ctx, "t") is None

    assert git(host, "rev-parse", f"origin/{PLAN}") == old_tip and refresh_rows(host) == []
    assert any("would be refreshed onto origin/main" in line for line in ctx.logs)


def test_a_plan_already_containing_main_is_recorded_without_a_merge_commit(host: Path) -> None:
    ctx = stage_context(host, {"state": "admitted", "waited_since": WAITED_SINCE})
    old_tip = git(host, "rev-parse", f"origin/{PLAN}")

    assert plan_refresh_stage_failure(ctx, "t") is None

    main_sha = git(host, "rev-parse", "origin/main")
    tip_before_the_refresh_row_commit = git(host, "rev-parse", f"origin/{PLAN}~1")
    assert tip_before_the_refresh_row_commit == old_tip
    [recorded] = refresh_rows(host)
    assert recorded["refreshed_to"] == main_sha and recorded["merge_commit"] is None
    assert ctx.admission == {**REFRESHED, "refreshed_to": main_sha}
    rerun_failure = plan_refresh_stage_failure(ctx, "t")
    assert rerun_failure is None and len(refresh_rows(host)) == 1


def test_a_re_planned_branch_is_refreshed_although_an_earlier_plan_was(
    host_with_overlap_merged_on_main: tuple[Path, str],
) -> None:
    # why: a re-plan (touch_set_violation, over budget) cuts a new plan branch from the stale intent tip
    host, main_sha = host_with_overlap_merged_on_main
    earlier = {**REFRESHED, "refreshed_to": main_sha, "refreshed_branch": "I-1-x/P-1-claude-agent-claude-opus-5-5/_"}
    ctx = stage_context(host, earlier)
    old_tip = git(host, "rev-parse", f"origin/{PLAN}")

    assert plan_refresh_stage_failure(ctx, "t") is None

    new_tip = git(host, "rev-parse", f"origin/{PLAN}")
    assert is_ancestor(host, main_sha, new_tip) and is_ancestor(host, old_tip, new_tip)
    assert ctx.admission == {**REFRESHED, "refreshed_to": main_sha} and len(refresh_rows(host)) == 1
