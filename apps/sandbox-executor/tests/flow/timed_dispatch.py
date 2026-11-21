"""Timed Stage 3 dispatch through the real cli.dispatch_role_agent, shared by the stage budget tests."""

import os
from unittest.mock import patch

from sandbox_executor.flow import FlowContext, FlowStage, PipelineEngine

from tests.flow.lookup_patch import patch_lookup_sites


def dispatch_timed_execute(tmp_path, env, exit_code=7):
    """Run timed Stage 3 through the real cli.dispatch_role_agent and capture the executor container's env.

    With ``exit_code`` 0 the dispatch succeeds and the post-dispatch path runs against a discovered execution branch
    that changes one path and records a ``success`` summary row.
    """
    from sandbox_executor.budgets import stage_budget_handdown_env

    ctx = FlowContext(
        repo_dir=str(tmp_path),
        intent_data={"slug": "b", "goal": "B"},
        plan_branch="I-1-x/P-2-y/_",
        agent="claude-agent",
        model="claude-opus-5-5",
        dry_run=False,
    )
    seen = []

    def fake_container(role, image_name, container_args, **kwargs):
        # run_docker_container adds exactly this to the forwarded env (cli test covers the docker argv).
        seen.append((role, image_name, list(container_args), stage_budget_handdown_env()))
        return exit_code

    exec_branch = "I-1-x/P-2-y/E-3-claude-agent-claude-opus-5-5/_"
    with (
        patch.dict(os.environ, env),
        patch("sandbox_executor.flow.stages.execute.check_and_resolve_stage_conflicts"),
        patch("sandbox_executor.flow.stages.execute.fetch_base"),
        patch("sandbox_executor.flow.stages.execute.publish_new_child_branches", return_value=[]),
        patch("sandbox_executor.flow.stages.execute._snapshot_child_branches", return_value=set()),
        patch("sandbox_executor.cli.run_docker_container", side_effect=fake_container),
        patch("sandbox_executor.flow.stages.execute._fetch_branch_prefix_error", return_value=None),
        patch("sandbox_executor.flow.stages.execute._discover_child_branch", return_value=(exec_branch, None)),
        patch("sandbox_executor.flow.stages.execute._track_local_branch"),
        patch_lookup_sites(
            "sandbox_executor.flow.stages.execute._read_ledger_rows",
            "sandbox_executor.flow.stages.plan_decision._read_ledger_rows",
            return_value=[
                {"entry_type": "summary", "execution_id": "E-3-claude-agent-claude-opus-5-5", "status": "success"}
            ],
        ),
        patch_lookup_sites(
            "sandbox_executor.flow.stages.execute._resolve_branch_ref",
            "sandbox_executor.flow.stages.plan_decision._resolve_branch_ref",
            return_value="refs/remotes/origin/I-1-x/P-2-y/_",
        ),
        patch("sandbox_executor.flow.stages.execute.changed_paths", return_value=["src/app.py"]),
    ):
        now = [0.0]
        result = PipelineEngine(ctx, clock=lambda: now[0]).timed_execute_stage(FlowStage.EXECUTE)
    return result, seen
