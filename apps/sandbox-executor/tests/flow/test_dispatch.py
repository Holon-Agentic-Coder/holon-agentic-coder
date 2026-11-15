"""Tests for Stages 1-3 role-agent dispatch through ``sandbox_executor.flow.dispatch``."""

import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.cli import main as cli_main
from sandbox_executor.flow import (
    SKIP_PUSH_DISPATCH_ERROR,
    FlowContext,
    PipelineEngine,
    StageStatus,
    load_checkpoint,
    run_calibrate_stage,
    run_execute_stage,
    run_intent_stage,
    run_plan_stage,
    save_checkpoint,
)

from tests.flow.helpers import (
    FakeRoleAgents,
    _append_jsonl,
    _configure_identity,
    _dispatch_context,
    _git,
    _ledger_rows,
    _push_child_branch,
)
from tests.flow.lookup_patch import patch_lookup_sites


class TestFlowRoleDispatch:
    """Stages 1-3 dispatch the same role agents as the per-stage commands and never author content."""

    def test_pipeline_dispatches_intent_plan_execute_with_resolved_runner(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(host, agents)

        results = PipelineEngine(ctx).run()

        assert agents.roles() == ["intent-creator", "planner", "executor"]
        for call in agents.calls:
            assert call["runner"] == "claude-agent"
            assert call["model"] == "claude-opus-5-5"
            assert call["target_worktree"] == str(host)
        assert agents.calls[1]["args"] == [ctx.intent_branch, "claude-agent", "claude-opus-5-5"]
        assert agents.calls[2]["args"] == [ctx.plan_branch, "claude-agent", "claude-opus-5-5"]
        assert results["intent"].status == StageStatus.SUCCESS
        assert results["plan"].status == StageStatus.SUCCESS
        assert results["execute"].status == StageStatus.SUCCESS
        assert ctx.plan_branch.startswith(ctx.intent_branch.removesuffix("/_") + "/P-")
        assert ctx.execution_branch.startswith(ctx.plan_branch.removesuffix("/_") + "/E-")
        assert results["execute"].payload["changed_files"] == 1
        # The container recorded no test result, so Stage 3 reports it as not measured rather than passed.
        assert results["execute"].payload["test_pass_rate"] is None

        # The flow authored nothing: every ledger row on origin came from a (simulated) container.
        exec_rows = _ledger_rows(host, f"origin/{ctx.execution_branch}", "executions.jsonl")
        assert len(exec_rows) == 1
        assert len(_ledger_rows(host, f"origin/{ctx.execution_branch}", "plans.jsonl")) == 1
        assert len(_ledger_rows(host, f"origin/{ctx.execution_branch}", "intents.jsonl")) == 1
        assert _git(host, "status", "--porcelain", "--untracked-files=no") == ""

    @pytest.mark.parametrize(
        ("recorded", "expected"), [(0.75, 0.75), (1, 1.0), (0.0, 0.0), ("1.0", None), (True, None)]
    )
    def test_stage3_carries_container_recorded_test_pass_rate(self, flow_repos, tmp_path, recorded, expected):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin, recorded_test_pass_rate=recorded)
        ctx = _dispatch_context(host, agents)

        results = PipelineEngine(ctx).run()

        assert results["execute"].status == StageStatus.SUCCESS
        assert results["execute"].payload["test_pass_rate"] == expected

    def test_stage1_intent_dispatch_carries_resolved_payload(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(
            host,
            agents,
            intent_data={
                "slug": "Resolved Q",
                "goal": "Build stage 1",
                "questions": ["What protocol?"],
                "answers": {"What protocol?": "OAuth2"},
            },
        )

        res = run_intent_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert agents.roles() == ["intent-creator"]
        payload = agents.intent_payloads[0]
        assert payload["slug"] == "resolved-q"
        assert payload["branch"] == ctx.intent_branch.removesuffix("/_")
        assert payload["answers"] == {"What protocol?": "OAuth2"}
        rows = _ledger_rows(host, f"origin/{ctx.intent_branch}", "intents.jsonl")
        assert [r["slug"] for r in rows] == ["resolved-q"]
        # The container committed the row on the intent branch in the worktree, which stays clean; the temporary
        # payload file is removed.
        assert _git(host, "status", "--porcelain") == ""
        assert _git(host, "rev-parse", "--abbrev-ref", "HEAD") == ctx.intent_branch
        assert not any((tmp_path / "home" / ".holon" / "tmp").iterdir())

    def test_stage1_reuses_existing_intent_branch_without_redispatch(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS

        again = _dispatch_context(host, agents, intent_data=dict(ctx.intent_data))
        assert run_intent_stage(again).status == StageStatus.SUCCESS
        assert agents.roles() == ["intent-creator"]

    @staticmethod
    def _child_intent(goal: str) -> dict[str, Any]:
        parent = "I-1-parent/_"
        return {
            "branch": "I-1-parent--1-add-the-parser",
            "slug": "add-the-parser",
            "goal": goal,
            "description": goal,
            "parent_intent_id": parent,
            "target_branch": "main",
            "sub_intent_depth": 1,
        }

    def test_stage1_reuses_existing_child_branch_recording_the_same_goal(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        first = _dispatch_context(host, agents, intent_data=self._child_intent("Add the parser"))
        assert run_intent_stage(first).status == StageStatus.SUCCESS

        resumed = _dispatch_context(host, agents, intent_data=self._child_intent("Add the parser"))
        res = run_intent_stage(resumed)

        assert res.status == StageStatus.SUCCESS
        assert res.payload["dispatched"] is False
        assert resumed.intent_branch == "I-1-parent--1-add-the-parser/_"
        assert agents.roles() == ["intent-creator"]

    @pytest.mark.parametrize("changed", ["goal", "description"])
    def test_stage1_refuses_existing_child_branch_recording_a_different_goal(self, flow_repos, tmp_path, changed):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        first = _dispatch_context(host, agents, intent_data=self._child_intent("Add the parser"))
        assert run_intent_stage(first).status == StageStatus.SUCCESS

        # A re-plan changed the item at the same position while keeping its slug.
        replanned_data = {**self._child_intent("Add the parser"), changed: "Add the parser with error recovery"}
        res = run_intent_stage(_dispatch_context(host, agents, intent_data=replanned_data))

        assert res.status == StageStatus.FAILED
        assert "'I-1-parent--1-add-the-parser/_' already exists on origin for a different intent" in (res.error or "")
        assert agents.roles() == ["intent-creator"]
        rows = _ledger_rows(host, "origin/I-1-parent--1-add-the-parser/_", "intents.jsonl")
        assert [r["goal"] for r in rows] == ["Add the parser"]

    def test_stage1_refuses_existing_branch_without_an_intent_row(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        branch = "I-1-parent--1-add-the-parser/_"
        _git(host, "branch", branch, "main")
        _git(host, "push", "-q", "origin", branch)

        res = run_intent_stage(_dispatch_context(host, agents, intent_data=self._child_intent("Add the parser")))

        assert res.status == StageStatus.FAILED
        assert f"'{branch}' already exists on origin but records no intent row" in (res.error or "")
        assert agents.roles() == []

    def test_silent_planner_fails_plan_stage_without_fabricating(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin, planner_writes_plan=False)
        ctx = _dispatch_context(host, agents)

        results = PipelineEngine(ctx).run()

        assert results["plan"].status == StageStatus.FAILED
        assert "refusing to fabricate a plan" in (results["plan"].error or "")
        assert ctx.intent_branch in (results["plan"].error or "")
        assert "execute" not in results
        assert agents.roles() == ["intent-creator", "planner"]
        assert ctx.plan_branch is None
        remote_branches = _git(host, "branch", "-r")
        assert "/P-" not in remote_branches
        assert not list(host.glob("plans/P-*.md"))
        assert (host / "holon-knowledge/ledger/plans.jsonl").read_text(encoding="utf-8") == ""

    def test_stage2_adopts_planner_branch_and_metrics(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS

        with patch("sandbox_executor.flow.stages.calibrate.format_calibration_report") as mock_format:
            res = run_plan_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        mock_format.assert_not_called()  # The planner container owns formatting.
        assert res.payload["plan_file"] == f"plans/{res.payload['plan_id']}.md"
        assert res.payload["plan_id"].endswith("-claude-agent-claude-opus-5-5")
        assert res.payload["metrics"]["ev"] == pytest.approx(35.4)
        assert "Implementation step for" not in _git(host, "show", f"{ctx.plan_branch}:{res.payload['plan_file']}")
        assert len(_ledger_rows(host, ctx.plan_branch, "plans.jsonl")) == 1

    def test_stage2_ignores_stale_plan_branches(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin, planner_writes_plan=False)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        # Plan branches pushed before dispatch (an old run, and one stamped "now") are not this run's plan.
        _push_child_branch(tmp_path, origin, ctx.intent_branch, "P-1000-claude-agent-claude-opus-5-5")
        _push_child_branch(tmp_path, origin, ctx.intent_branch, f"P-{int(time.time())}-claude-agent-claude-opus-5-5")

        res = run_plan_stage(ctx)

        assert res.status == StageStatus.FAILED  # Zero new branches appeared during dispatch.
        assert "refusing to fabricate a plan" in (res.error or "")
        assert ctx.plan_branch is None

    def test_stage2_adopts_plan_from_clock_skewed_container(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin, clock_offset=-3600)  # Container clock an hour behind the host.
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS

        res = run_plan_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert int(res.payload["plan_id"].split("-")[1]) < int(time.time()) - 3000
        assert ctx.plan_branch == f"{ctx.intent_branch.removesuffix('/_')}/{res.payload['plan_id']}/_"

    def test_stage2_ignores_newer_sibling_present_before_dispatch(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        # A concurrent run with the same runner/model whose branch name carries a later timestamp.
        sibling = _push_child_branch(
            tmp_path, origin, ctx.intent_branch, f"P-{int(time.time()) + 600}-claude-agent-claude-opus-5-5"
        )

        res = run_plan_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert ctx.plan_branch != sibling
        assert ctx.plan_branch.endswith(f"/{res.payload['plan_id']}/_")
        assert int(res.payload["plan_id"].split("-")[1]) <= int(time.time())

    def test_stage2_fails_when_several_new_plan_branches_appear(self, flow_repos, tmp_path):
        host, origin = flow_repos

        def concurrent_planner(role: str, args: list[str]) -> None:
            if role == "planner":
                _push_child_branch(tmp_path, origin, args[0], "P-1-claude-agent-claude-opus-5-5")

        agents = FakeRoleAgents(tmp_path, origin, during_dispatch=concurrent_planner)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS

        res = run_plan_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "ambiguous P-branches" in (res.error or "")
        assert "refusing to guess" in (res.error or "")
        assert ctx.plan_branch is None

    def test_stage2_rejects_new_branch_from_another_runner(self, flow_repos, tmp_path):
        host, origin = flow_repos

        def other_runner(role: str, args: list[str]) -> None:
            if role == "planner":
                _push_child_branch(tmp_path, origin, args[0], f"P-{int(time.time())}-codex-agent-gpt-6")

        agents = FakeRoleAgents(tmp_path, origin, planner_writes_plan=False, during_dispatch=other_runner)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS

        res = run_plan_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "do not match the dispatched runner/model" in (res.error or "")
        assert ctx.plan_branch is None

    def test_stage3_fails_when_several_new_execution_branches_appear(self, flow_repos, tmp_path):
        host, origin = flow_repos

        def concurrent_executor(role: str, args: list[str]) -> None:
            if role == "executor":
                _push_child_branch(tmp_path, origin, args[0], "E-1-claude-agent-claude-opus-5-5")

        agents = FakeRoleAgents(tmp_path, origin, during_dispatch=concurrent_executor)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS

        res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "ambiguous E-branches" in (res.error or "")
        assert ctx.execution_branch is None

    def test_stage3_adopts_execution_despite_preexisting_sibling(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin, clock_offset=-120)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS
        sibling = _push_child_branch(
            tmp_path, origin, ctx.plan_branch, f"E-{int(time.time()) + 600}-claude-agent-claude-opus-5-5"
        )

        res = run_execute_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert ctx.execution_branch != sibling
        assert ctx.execution_branch.endswith(f"/{res.payload['execution_id']}/_")

    def test_stage2_fails_closed_when_origin_cannot_be_snapshotted(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS

        with patch("sandbox_executor.flow.git_refs._fetch_branch_prefix", return_value=False):
            res = run_plan_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "before dispatching the planner" in (res.error or "")
        assert agents.roles() == ["intent-creator"]

    @staticmethod
    def _fail_fetch_after(agents: "FakeRoleAgents", role: str) -> Callable[..., subprocess.CompletedProcess[str]]:
        """Return a run_git stand-in whose fetches fail (with a secret in stderr) once ``role`` was dispatched."""
        from sandbox_executor import flow as flow_module

        real_run_git = flow_module.run_git

        def fake_run_git(args, *a, **kw):
            if args and args[0] == "fetch" and role in agents.roles():
                return subprocess.CompletedProcess(
                    ["git", *args], 128, "", "fatal: unable to access origin: token=ghp_supersecret123 expired"
                )
            return real_run_git(args, *a, **kw)

        return fake_run_git

    def test_stage1_fails_closed_when_pre_dispatch_fetch_fails(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(host, agents)
        from sandbox_executor import flow as flow_module

        real_run_git = flow_module.run_git

        def fake_run_git(args, *a, **kw):
            if args and args[0] == "fetch" and str(args[-1]).startswith("+refs/heads/I-"):
                return subprocess.CompletedProcess(
                    ["git", *args], 128, "", "fatal: unable to access origin: token=ghp_supersecret123 expired"
                )
            return real_run_git(args, *a, **kw)

        with patch_lookup_sites(
            "sandbox_executor.flow.git_refs.run_git",
            "sandbox_executor.flow.stage_conflicts.run_git",
            side_effect=fake_run_git,
        ):
            res = run_intent_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "before dispatching the intent creator" in (res.error or "")
        assert "ghp_supersecret123" not in (res.error or "")
        assert agents.roles() == []

    def test_stage1_reports_post_dispatch_fetch_failure(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(host, agents)

        with patch_lookup_sites(
            "sandbox_executor.flow.git_refs.run_git",
            "sandbox_executor.flow.stage_conflicts.run_git",
            side_effect=self._fail_fetch_after(agents, "intent-creator"),
        ):
            res = run_intent_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "intent-creator agent exited 0" in (res.error or "")
        assert "could not be fetched" in (res.error or "")
        assert "was not found on origin" not in (res.error or "")
        assert "ghp_supersecret123" not in (res.error or "")
        assert agents.roles() == ["intent-creator"]

    def test_stage2_reports_post_dispatch_fetch_failure(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS

        with patch_lookup_sites(
            "sandbox_executor.flow.git_refs.run_git",
            "sandbox_executor.flow.github.pr.run_git",
            "sandbox_executor.flow.stage_conflicts.run_git",
            side_effect=self._fail_fetch_after(agents, "planner"),
        ):
            res = run_plan_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "planner agent exited 0" in (res.error or "")
        assert "could not be fetched" in (res.error or "")
        assert "no planner agent produced" not in (res.error or "")
        assert "ghp_supersecret123" not in (res.error or "")
        assert ctx.plan_branch is None

    def test_stage3_reports_post_dispatch_fetch_failure(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS

        with patch_lookup_sites(
            "sandbox_executor.flow.git_refs.run_git",
            "sandbox_executor.flow.github.pr.run_git",
            "sandbox_executor.flow.stage_conflicts.run_git",
            side_effect=self._fail_fetch_after(agents, "executor"),
        ):
            res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "executor agent exited 0" in (res.error or "")
        assert "could not be fetched" in (res.error or "")
        assert "no execution branch" not in (res.error or "")
        assert "ghp_supersecret123" not in (res.error or "")
        assert ctx.execution_branch is None

    def test_empty_execution_fails_records_failed_and_never_opens_pr(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin, executor_mode="empty")
        ctx = _dispatch_context(host, agents, skip_review=False)
        real_run = subprocess.run
        gh_calls: list[list[str]] = []

        def fake_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd and cmd[0] == "gh":
                gh_calls.append(list(cmd))
                return MagicMock(returncode=1, stdout="", stderr="gh disabled in tests")
            return real_run(cmd, *args, **kwargs)

        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run", side_effect=fake_run),
            patch("sandbox_executor.flow.review.loop.create_or_update_pr") as mock_pr,
        ):
            results = PipelineEngine(ctx).run()

        assert results["execute"].status == StageStatus.FAILED
        assert "no changes outside holon-knowledge/ledger/, plans/ and executions/" in (results["execute"].error or "")
        assert results["execute"].payload["status"] == "failed"
        assert results["execute"].payload["superseding_row_recorded"] is True
        assert "review" not in results
        mock_pr.assert_not_called()
        assert not any(call[:3] == ["gh", "pr", "create"] for call in gh_calls)

        rows = _ledger_rows(host, f"origin/{ctx.execution_branch}", "executions.jsonl")
        exec_id = results["execute"].payload["execution_id"]
        exec_rows = [r for r in rows if r.get("execution_id") == exec_id]
        assert [r["status"] for r in exec_rows] == ["success", "failed"]
        latest = exec_rows[-1]
        assert latest["ledger_revision"] == 2
        assert latest["failure_reason"] == "empty_diff"
        assert "test_pass_rate" not in latest
        assert latest["agent_output_bytes"] == 12  # Authoritative fields are repeated, not merged backwards.
        # Append-only: the container's original row is untouched.
        assert exec_rows[0]["test_pass_rate"] == 1.0
        error = results["execute"].error or ""
        assert "exited with code 0" in error
        assert "container recorded status 'success'; recorded a superseding status 'failed' row" in error
        # The local execution branch is fast-forwarded to the pushed superseding row.
        local_rows = _ledger_rows(host, ctx.execution_branch, "executions.jsonl")
        assert [r["status"] for r in local_rows if r.get("execution_id") == exec_id] == ["success", "failed"]
        assert _git(host, "rev-parse", ctx.execution_branch) == _git(
            host, "rev-parse", f"origin/{ctx.execution_branch}"
        )
        assert _git(host, "ls-remote", str(origin), ctx.execution_branch).split()[0] == _git(
            host, "rev-parse", ctx.execution_branch
        )
        assert _git(host, "status", "--porcelain", "--untracked-files=no") == ""
        assert _git(host, "worktree", "list").count("\n") == 0

    def test_empty_execution_already_failed_is_not_duplicated(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin, executor_mode="empty")
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS
        agents.executor_mode = "agent_failure"

        res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        rows = _ledger_rows(host, f"origin/{ctx.execution_branch}", "executions.jsonl")
        assert [r["status"] for r in rows] == ["failure"]
        error = res.error or ""
        assert "container already recorded status 'failure': Plan execution failed" in error
        assert "no superseding row was written" in error
        assert "recorded a superseding" not in error
        assert res.payload["container_status"] == "failure"
        assert res.payload["superseding_row_recorded"] is False

    def test_empty_execution_without_summary_row_reports_missing_row(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin, executor_mode="empty_no_row")
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS

        res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        error = res.error or ""
        assert "carries no executions.jsonl summary row" in error
        assert "so no superseding row was recorded" in error
        assert "recorded a superseding" not in error
        assert res.payload["container_status"] is None
        assert res.payload["superseding_row_recorded"] is False
        assert _ledger_rows(host, f"origin/{ctx.execution_branch}", "executions.jsonl") == []

    def test_empty_execution_reports_failed_superseding_write(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin, executor_mode="empty")
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS
        from sandbox_executor import flow as flow_module

        real_run_git = flow_module.run_git

        def fake_run_git(args, *a, **kw):
            if args and args[0] == "push":
                raise RuntimeError("push rejected: token=ghp_supersecret123 expired")
            return real_run_git(args, *a, **kw)

        with patch_lookup_sites(
            "sandbox_executor.flow.git_refs.run_git",
            "sandbox_executor.flow.github.pr.run_git",
            "sandbox_executor.flow.stage_conflicts.run_git",
            side_effect=fake_run_git,
        ):
            res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        error = res.error or ""
        assert "recording the superseding 'failed' row (failure_reason 'empty_diff') failed" in error
        assert "the ledger still shows 'success'" in error
        assert "push rejected" in error
        assert "ghp_supersecret123" not in error
        assert res.payload["superseding_row_recorded"] is False
        rows = _ledger_rows(host, f"origin/{ctx.execution_branch}", "executions.jsonl")
        assert [r["status"] for r in rows] == ["success"]
        assert _ledger_rows(host, ctx.execution_branch, "executions.jsonl") == rows

    def test_superseding_row_leaves_diverged_local_branch_untouched(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin, executor_mode="empty")
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS
        from sandbox_executor import flow as flow_module

        real_track = flow_module._track_local_branch

        def diverge_local(context, branch):
            real_track(context, branch)
            if "/E-" in branch:
                tree = _git(host, "rev-parse", f"{branch}^{{tree}}")
                commit = _git(host, "commit-tree", tree, "-p", branch, "-m", "local operator commit")
                _git(host, "update-ref", f"refs/heads/{branch}", commit)

        with patch("sandbox_executor.flow.stages.execute._track_local_branch", side_effect=diverge_local):
            res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert res.payload["superseding_row_recorded"] is True
        assert _git(host, "log", "-1", "--format=%s", ctx.execution_branch) == "local operator commit"
        assert any("has diverged from origin" in line for line in ctx.logs)
        rows = _ledger_rows(host, f"origin/{ctx.execution_branch}", "executions.jsonl")
        assert [r["status"] for r in rows] == ["success", "failed"]

    def test_track_local_branch_keeps_unpushed_local_commits(self, flow_repos):
        host, _origin = flow_repos
        from sandbox_executor import flow as flow_module

        branch = "I-1-reused/_"
        _git(host, "branch", branch, "main")
        _git(host, "push", "-q", "origin", branch)
        _git(host, "fetch", "-q", "origin")
        tree = _git(host, "rev-parse", f"{branch}^{{tree}}")
        local_commit = _git(host, "commit-tree", tree, "-p", branch, "-m", "unpushed local commit")
        _git(host, "update-ref", f"refs/heads/{branch}", local_commit)
        ctx = FlowContext(repo_dir=str(host), token_reduce=False)

        flow_module._track_local_branch(ctx, branch)

        assert _git(host, "rev-parse", f"refs/heads/{branch}") == local_commit
        assert any("has diverged from origin" in line for line in ctx.logs)

    def test_track_local_branch_fast_forwards_a_behind_local_branch(self, flow_repos, tmp_path):
        host, origin = flow_repos
        from sandbox_executor import flow as flow_module

        branch = "I-1-reused/_"
        _git(host, "branch", branch, "main")
        _git(host, "push", "-q", "origin", branch)
        other = tmp_path / "other-ff"
        subprocess.run(["git", "clone", "-q", "-b", branch, str(origin), str(other)], check=True, capture_output=True)
        _configure_identity(other)
        _git(other, "commit", "-q", "--allow-empty", "-m", "pushed by the container")
        _git(other, "push", "-q", "origin", branch)
        _git(host, "fetch", "-q", "origin")
        ctx = FlowContext(repo_dir=str(host), token_reduce=False)

        flow_module._track_local_branch(ctx, branch)

        assert _git(host, "rev-parse", f"refs/heads/{branch}") == _git(host, "rev-parse", f"origin/{branch}")
        assert not any("Warning" in line for line in ctx.logs)

    def test_flow_auto_detects_token_reduction_but_manual_commands_require_opt_in(self, monkeypatch):
        """Documented limit of the equivalence guarantee: only 'holon flow' auto-detects holon-coherence."""
        from sandbox_executor import flow as flow_module

        monkeypatch.delenv("HOLON_TOKEN_REDUCE", raising=False)
        monkeypatch.delenv("HOLON_PASSIVE_MONITORING", raising=False)
        monkeypatch.setenv("HOLON_AGENT", "antigravity-agent")
        monkeypatch.setenv("HOLON_MODEL", "gemini-3.8-flash-medium")
        with patch("sandbox_executor.cli.detect_holon_coherence", return_value=True):
            dispatcher = MagicMock(return_value=0)
            ctx = FlowContext(repo_dir=".", dispatcher=dispatcher)
            flow_module._dispatch_role(ctx, "planner", ["I-1-x/_"])
            assert dispatcher.call_args.kwargs["token_reduce"] is True

            with (
                patch("sandbox_executor.cli.dispatch_role_agent", return_value=0) as mock_dispatch,
                patch("sandbox_executor.cli.commands.plan.prepare_worktree", return_value="."),
                patch("sandbox_executor.cli.commands.plan.push_checked_out_branch"),
                patch("sys.argv", ["holon", "plan", "I-1-x/_"]),
                pytest.raises(SystemExit),
            ):
                cli_main()
            assert mock_dispatch.call_args.kwargs["token_reduce"] is False

    def test_stage3_agent_failure_status_fails_stage(self, flow_repos, tmp_path):
        host, origin = flow_repos
        # The failed agent still edited a file, so the failure comes from the container's own status.
        agents = FakeRoleAgents(tmp_path, origin, executor_mode="change_failure")
        ctx = _dispatch_context(host, agents)
        assert run_intent_stage(ctx).status == StageStatus.SUCCESS
        assert run_plan_stage(ctx).status == StageStatus.SUCCESS

        res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "recorded status 'failure'" in (res.error or "")
        rows = _ledger_rows(host, f"origin/{ctx.execution_branch}", "executions.jsonl")
        assert [r["status"] for r in rows] == ["failure"]

    @pytest.mark.parametrize("code", [1, 2])
    def test_stage3_nonzero_container_exit_fails_stage(self, flow_repos, code):
        host, _origin = flow_repos
        # Any non-zero exit except the executor's over-budget refusal code (read from its pushed row) fails at once.
        ctx = _dispatch_context(host, MagicMock(return_value=code), plan_branch="I-1-x/P-1-claude-agent-m/_")

        res = run_execute_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert res.error == f"Execute stage failed: executor agent exited with code {code}."
        assert res.payload["exit_code"] == code

    def test_skip_push_is_rejected_before_dispatch(self, flow_repos):
        host, _origin = flow_repos
        dispatcher = MagicMock(return_value=0)
        ctx = _dispatch_context(host, dispatcher, skip_push=True)

        results = PipelineEngine(ctx).run()

        assert results["intent"].status == StageStatus.FAILED
        assert results["intent"].error == SKIP_PUSH_DISPATCH_ERROR
        dispatcher.assert_not_called()

        for stage_fn, extra in (
            (run_plan_stage, {"intent_branch": "I-1-x/_"}),
            (run_execute_stage, {"plan_branch": "I-1-x/P-1-a-b/_"}),
        ):
            stage_ctx = _dispatch_context(host, dispatcher, skip_push=True, **extra)
            res = stage_fn(stage_ctx)
            assert res.status == StageStatus.FAILED
            assert res.error == SKIP_PUSH_DISPATCH_ERROR
        dispatcher.assert_not_called()

    def test_dry_run_never_dispatches(self, tmp_path):
        dispatcher = MagicMock(return_value=0)
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "dry", "goal": "Dry run"},
            dry_run=True,
            skip_push=True,
            dispatcher=dispatcher,
        )
        results = PipelineEngine(ctx).run()
        assert results["execute"].status == StageStatus.SUCCESS
        dispatcher.assert_not_called()

    def test_default_dispatcher_is_cli_dispatch_role_agent(self, flow_repos, tmp_path):
        host, origin = flow_repos
        agents = FakeRoleAgents(tmp_path, origin)
        ctx = _dispatch_context(host, None)
        with patch("sandbox_executor.cli.dispatch_role_agent", side_effect=agents) as mock_dispatch:
            res = run_intent_stage(ctx)
        assert res.status == StageStatus.SUCCESS
        mock_dispatch.assert_called_once()
        assert mock_dispatch.call_args.args[:3] == ("intent-creator", "claude-agent", "claude-opus-5-5")

    def test_checkpoint_does_not_serialise_dispatcher(self, tmp_path):
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            intent_data={"slug": "cp-dispatch", "goal": "Checkpoint"},
            dispatcher=MagicMock(return_value=0),
            token_reduce=False,
        )
        assert "dispatcher" not in ctx.to_dict()
        saved = save_checkpoint(ctx, str(tmp_path / "checkpoint.json"))
        with open(saved, encoding="utf-8") as f:
            assert "dispatcher" not in f.read()
        assert load_checkpoint(saved).context.dispatcher is None


# ---------------------------------------------------------------------------
# Merge interactions: #93 runner resolution / pre-flight and #90 Stage 5 reconciliation with role-agent dispatch
# ---------------------------------------------------------------------------
class TestFlowDispatchMergeInteractions:
    """Regression tests for behaviour that only holds when the merged features work together."""

    def test_flow_dispatches_the_runner_and_model_resolved_by_the_cli_after_preflight(self, flow_repos, monkeypatch):
        """'holon flow' resolves runner/model (#93), pre-flights them, then Stage 1 dispatches with those values."""
        from sandbox_executor.agent_defaults import DEFAULT_MODELS

        host, _origin = flow_repos
        intent_file = host.parent / "intent.json"
        intent_file.write_text(json.dumps({"slug": "resolved-runner", "goal": "g"}), encoding="utf-8")
        monkeypatch.setenv("HOLON_AGENT", "claude-agent")
        monkeypatch.setenv("HOLON_MODEL", DEFAULT_MODELS["claude"])
        monkeypatch.delenv("HOLON_SKIP_PREFLIGHT", raising=False)
        order = MagicMock()
        order.probe.return_value = MagicMock(ok=True)
        order.dispatch.return_value = 1  # stop the flow at Stage 1; only the dispatch arguments matter here

        with (
            patch("sandbox_executor.cli.run_preflight_probe", order.probe),
            patch("sandbox_executor.cli.dispatch_role_agent", order.dispatch),
            patch("sandbox_executor.cli.detect_holon_coherence", return_value=False),
            patch("sys.argv", ["holon", "flow", str(intent_file), "--target-worktree", str(host), "--skip-review"]),
            pytest.raises(SystemExit),
        ):
            cli_main()

        expected = ("claude-agent", DEFAULT_MODELS["claude"])
        assert [c[0] for c in order.mock_calls] == ["probe", "dispatch"]
        assert order.probe.call_args.args[:2] == expected
        assert order.dispatch.call_args.args[0] == "intent-creator"
        assert order.dispatch.call_args.args[1:3] == expected

    def test_stage5_checks_out_execution_branch_then_commits_reconciliation_on_calibrated(self, tmp_path):
        """Stage 5 calibrates from the execution branch (not the operator's HEAD) and, on success, commits the
        reconciliation rows on /calibrated before fast-forwarding the execution branch to it."""

        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout

        execution_branch = "I-100-intent/P-200-plan/E-300-exec/_"
        calibrated_branch = "I-100-intent/P-200-plan/E-300-exec/calibrated"
        git("init", "-q", "-b", "main")
        _configure_identity(tmp_path)
        ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_text("", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "initial")
        git("checkout", "-q", "-b", execution_branch)
        (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "execution change")
        git("checkout", "-q", "main")  # the operator's checkout is elsewhere when Stage 5 starts
        main_tip = git("rev-parse", "main").strip()

        seen_head: list[str] = []

        def fake_run_calibrate(**_kwargs):
            seen_head.append(git("rev-parse", "--abbrev-ref", "HEAD").strip())
            git("checkout", "-q", "-B", calibrated_branch)
            (tmp_path / "plans").mkdir()
            (tmp_path / "plans" / "P-200-plan_calibration.md").write_text("# Calibration\n", encoding="utf-8")
            git("add", "plans")
            git("commit", "-q", "-m", "calibration report")
            report = MagicMock()
            report.calibrated_branch = calibrated_branch
            report.to_dict.return_value = {"calibrated_branch": calibrated_branch}
            return report

        def fake_reconcile(**kwargs):
            _append_jsonl(Path(kwargs["ledger_path"]), {"entry_type": "action", "action_id": "ACT-RECON-1"})

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch=execution_branch,
            plan_branch="I-100-intent/P-200-plan/_",
            dry_run=False,
            skip_push=True,
        )
        with (
            patch("sandbox_executor.flow.stages.calibrate.run_calibrate", side_effect=fake_run_calibrate),
            patch("sandbox_executor.flow.stages.calibrate.reconcile_records", side_effect=fake_reconcile),
        ):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert seen_head == [execution_branch]
        assert git("log", "-1", "--format=%s", calibrated_branch).strip() == (
            "chore(calibration): record stage 5 reconciliation"
        )
        assert "ACT-RECON-1" in git("show", f"{calibrated_branch}:holon-knowledge/ledger/executions.jsonl")
        assert git("rev-parse", execution_branch).strip() == git("rev-parse", calibrated_branch).strip()
        assert git("rev-parse", "main").strip() == main_tip
        assert git("status", "--porcelain").strip() == ""
