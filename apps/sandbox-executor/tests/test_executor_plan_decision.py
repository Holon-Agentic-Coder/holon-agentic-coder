"""Tests for the executor entrypoint reading the decomposition decision the plan row records."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.entrypoint import executor

from tests.test_executor import _cuts_branch, _run_executor_main, _validate_stub


def test_main_decomposed_reads_the_list_the_plan_row_records(tmp_path):
    """Goal 8 (a): the executor reads the recorded decision, not the plan file, and writes no sub_intents."""
    recorded = [{"slug": "add-the-parser", "description": "Add the parser", "goal": "Add the parser"}]
    plan_row = {
        "plan_id": "P-123",
        "intent_branch": "I-456/_",
        "entropy": 9.0,
        "entropy_budget": 5.0,
        "plan_file": "plans/P-123.md",
        "decomposition_decision": "decomposed",
        "sub_intents": recorded,
    }
    intent_row = {"branch": "I-456", "slug": "parent", "entropy_budget": 5.0}

    rows = _run_executor_main(tmp_path, plan_row, "# Plan without a list\n", intent_row)

    (summary,) = [r for r in rows if r.get("entry_type") == "summary"]
    assert summary["status"] == "decomposed"
    assert summary["plan_decision"]["sub_intent_count"] == 1
    assert "sub_intents" not in summary


def test_main_decomposed_records_sub_intents_without_intent_rows(tmp_path):
    plan_row = {"plan_id": "P-123", "intent_branch": "I-456/_", "entropy": 9.0, "plan_file": "plans/P-123.md"}
    intent_row = {"branch": "I-456", "slug": "parent", "entropy_budget": 5.0}
    plan_md = "# Plan\n\n## Sub-intent list\n\n1. Add the parser\n2. Add the CLI flag\n"

    rows = _run_executor_main(tmp_path, plan_row, plan_md, intent_row)

    summaries = [r for r in rows if r.get("entry_type") == "summary"]
    assert len(summaries) == 1
    assert summaries[0]["status"] == "decomposed"
    assert summaries[0]["entropy_budget"] == 5.0
    # The plan row is the only record of the list: the summary refers to it and carries no sub_intents.
    assert "sub_intents" not in summaries[0]
    assert summaries[0]["plan_decision"] == {
        "plan_id": "P-123",
        "plan_branch": "I-456/P-123/_",
        "decision": "decomposed",
        "sub_intent_count": 2,
    }


def test_main_recorded_over_budget_wins_over_a_list_in_the_plan_file(tmp_path):
    """A recorded decision is never re-decided from the markdown, even when the file now lists sub-intents."""
    plan_row = {
        "plan_id": "P-123",
        "intent_branch": "I-456/_",
        "entropy": 12.6,
        "entropy_budget": 5.0,
        "plan_file": "plans/P-123.md",
        "decomposition_decision": "over_budget",
    }
    intent_row = {"branch": "I-456", "slug": "parent", "entropy_budget": 5.0}
    plan_md = "# Plan\n\n## Sub-intent list\n\n1. Add the parser\n"

    rows = _run_executor_main(tmp_path, plan_row, plan_md, intent_row)

    assert [r["status"] for r in rows if r.get("entry_type") == "summary"] == ["over_budget"]


def test_main_reads_the_newest_matching_plan_row(tmp_path):
    """A superseding plan row the flow back-filled is the one the executor reads."""
    planner_row = {"plan_id": "P-123", "intent_branch": "I-456/_", "entropy": 12.6, "plan_file": "plans/P-123.md"}
    superseding = {
        **planner_row,
        "entropy_budget": 5.0,
        "decomposition_decision": "decomposed",
        "sub_intents": [{"slug": "a", "description": "A", "goal": "A"}],
        "ledger_revision": 2,
        "decision_recorded_by": "flow",
    }
    commands: list = []
    runner = MagicMock()
    runner.get_version.return_value = "1.0.0"

    def side_effect(args, cwd=None, **kwargs):
        res = MagicMock(returncode=0, stdout="", stderr="")
        commands.append(list(args))
        if args[:4] == ["git", "diff", "--cached", "--quiet"]:
            res.returncode = 1
        if _cuts_branch(args):
            ledger = os.path.join(cwd, "holon-knowledge/ledger")
            os.makedirs(ledger, exist_ok=True)
            with open(os.path.join(ledger, "plans.jsonl"), "w") as f:
                f.write(json.dumps(planner_row) + "\n" + json.dumps(superseding) + "\n")
        return res

    repo = str(tmp_path / "repo")
    with (
        patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": repo}),
        patch("sandbox_executor.entrypoint.executor.workspace_mounted", return_value=True),
        patch("sandbox_executor.entrypoint.executor.run_cmd", side_effect=side_effect),
        patch("sandbox_executor.entrypoint.executor.get_runner", return_value=runner),
        patch("sandbox_executor.entrypoint.executor.validate_mounted_workspace", new=_validate_stub),
        patch("sys.argv", ["executor.py", "I-456/P-123/_", "claude-agent", "claude-opus-5-5"]),
    ):
        executor.main()
    with open(os.path.join(repo, "holon-knowledge/ledger/executions.jsonl")) as f:
        (summary,) = [r for r in map(json.loads, f) if r.get("entry_type") == "summary"]
    assert (summary["status"], summary["plan_decision"]["sub_intent_count"]) == ("decomposed", 1)
    assert ["git", "commit", "-m", "execute: the plan I-456/P-123/_ records 1 sub-intent(s)"] in commands


def test_main_corrupt_recorded_decision_exits_without_a_row(tmp_path, capsys):
    """A corrupt record is never silently re-decided: the container exits 1 and writes no execution row."""
    plan_row = {"plan_id": "P-123", "intent_branch": "I-456/_", "entropy": 2.0, "decomposition_decision": "split"}
    runner = MagicMock()

    def side_effect(args, cwd=None, **kwargs):
        if _cuts_branch(args):
            ledger = os.path.join(cwd, "holon-knowledge/ledger")
            os.makedirs(ledger, exist_ok=True)
            with open(os.path.join(ledger, "plans.jsonl"), "w") as f:
                f.write(json.dumps(plan_row) + "\n")
        return MagicMock(returncode=0, stdout="", stderr="")

    repo = str(tmp_path / "repo")
    with (
        patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": repo}),
        patch("sandbox_executor.entrypoint.executor.workspace_mounted", return_value=True),
        patch("sandbox_executor.entrypoint.executor.run_cmd", side_effect=side_effect),
        patch("sandbox_executor.entrypoint.executor.get_runner", return_value=runner),
        patch("sandbox_executor.entrypoint.executor.validate_mounted_workspace", new=_validate_stub),
        patch("sys.argv", ["executor.py", "I-456/P-123/_", "claude-agent", "claude-opus-5-5"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        executor.main()
    assert exc_info.value.code == 1
    assert "records an invalid decomposition decision" in capsys.readouterr().err
    assert not os.path.exists(os.path.join(repo, "holon-knowledge/ledger/executions.jsonl"))
    runner.build_cmd.assert_not_called()
