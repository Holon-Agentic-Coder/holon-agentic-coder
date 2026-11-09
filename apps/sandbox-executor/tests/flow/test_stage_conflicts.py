"""Tests for the proactive stage conflict check and the review loop agent identity."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.flow import (
    REVIEW_GIT_ENV,
    ConflictResolutionError,
    check_and_resolve_stage_conflicts,
    reconcile_jsonl_ledger_conflict,
)

from tests.flow.lookup_patch import patch_lookup_sites


class TestProactiveConflictAndReviewLoop:
    """Tests for proactive conflict detection and Review Loop Agent identity."""

    def test_review_git_env(self):
        assert REVIEW_GIT_ENV["GIT_AUTHOR_NAME"] == "Holon Review Loop Agent"
        assert REVIEW_GIT_ENV["GIT_AUTHOR_EMAIL"] == "review-loop-agent@holon-agentic-coder.com"
        assert REVIEW_GIT_ENV["GIT_COMMITTER_NAME"] == "Holon Review Loop Agent"
        assert REVIEW_GIT_ENV["GIT_COMMITTER_EMAIL"] == "review-loop-agent@holon-agentic-coder.com"

    def test_reconcile_jsonl_ledger_diff3_reset(self):
        content = (
            "<<<<<<< HEAD\n"
            '{"action_id": "ACT-001", "execution_id": "E-001", "outcome": "success", "status": "done"}\n'
            "||||||| merged common ancestors\n"
            '{"action_id": "ACT-001", "execution_id": "E-001", "outcome": "pending", "status": "open"}\n'
            "=======\n"
            '{"action_id": "ACT-002", "execution_id": "E-001", "outcome": "success", "status": "done"}\n'
            ">>>>>>> origin/main\n"
        )
        reconciled = reconcile_jsonl_ledger_conflict(content)
        lines = [json.loads(line) for line in reconciled.strip().splitlines() if line.strip()]
        action_ids = {r.get("action_id") for r in lines}
        assert "ACT-001" in action_ids
        assert "ACT-002" in action_ids

    def test_check_and_resolve_stage_conflicts_magicmock_safety(self, tmp_path: Path):
        # Ensure isinstance(..., str) guards prevent MagicMock stdout crashes
        with patch_lookup_sites(
            "sandbox_executor.flow.github.pr.run_git", "sandbox_executor.flow.stage_conflicts.run_git"
        ) as mock_git:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = MagicMock()  # Non-string object mock
            mock_git.return_value = mock_proc

            res = check_and_resolve_stage_conflicts(str(tmp_path), target_branch="main", stage_name="test")
            assert res["conflicts_detected"] is False
            assert res["resolved"] is True

    def test_check_and_resolve_stage_conflicts_unresolvable_raises(self, tmp_path: Path):
        with patch("sandbox_executor.flow.stage_conflicts.run_git") as mock_git:
            mock_status = MagicMock()
            mock_status.returncode = 0
            mock_status.stdout = "UU src/main.py\n"
            mock_git.return_value = mock_status

            with pytest.raises(ConflictResolutionError, match="Unresolvable merge conflict"):
                check_and_resolve_stage_conflicts(str(tmp_path), target_branch="main", stage_name="test")
