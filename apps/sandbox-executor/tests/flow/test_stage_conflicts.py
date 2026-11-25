"""Tests for the proactive stage conflict check and the review loop agent identity."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.flow import (
    REVIEW_GIT_ENV,
    ConflictResolutionError,
    check_and_resolve_stage_conflicts,
    reconcile_jsonl_ledger_conflict,
)
from sandbox_executor.flow.stage_conflict_resolution import (
    ConflictKind,
    classify_conflicts,
    resolve_classified_conflicts,
)
from sandbox_executor.flow.stage_conflicts import (
    ConflictDetection,
    _parse_porcelain_unmerged,
    detect_stage_conflicts,
)

from tests.flow.lookup_patch import patch_lookup_sites
from tests.ledger_conflict_repos import FLOW_BRANCH, build_flow_repos, commit_on


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

            with pytest.raises(ConflictResolutionError, match="Unresolvable code conflicts"):
                check_and_resolve_stage_conflicts(str(tmp_path), target_branch="main", stage_name="test")


LEDGER = "holon-knowledge/ledger/executions.jsonl"
PLANS_LEDGER = "holon-knowledge/ledger/plans.jsonl"


def _git_proc(stdout: str = "", returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.stdout = stdout
    proc.returncode = returncode
    return proc


class TestDetectStageConflicts:
    """detect_stage_conflicts: porcelain unmerged paths, the upstream probe, then conflict-marked ledgers."""

    def test_porcelain_parsing_follows_renames_strips_quotes_and_drops_duplicates(self):
        stdout = 'UU src/a.py\nM  src/b.py\nAA "src/q x.py"\nUD old.py -> new.py\nUU src/a.py\n\nU\n'
        assert _parse_porcelain_unmerged(stdout) == ["src/a.py", "src/q x.py", "new.py"]

    def test_probe_is_skipped_when_files_are_unmerged(self, tmp_path: Path):
        with (
            patch("sandbox_executor.flow.stage_conflicts.run_git", return_value=_git_proc("UU src/a.py\n")),
            patch("sandbox_executor.flow.stage_conflicts.check_pr_mergeability") as probe,
        ):
            detection = detect_stage_conflicts(str(tmp_path), "main", "feature")
        probe.assert_not_called()
        assert detection == ConflictDetection(["src/a.py"], frozenset(), "feature")

    def test_probe_is_skipped_without_a_branch(self, tmp_path: Path):
        with (
            patch("sandbox_executor.flow.stage_conflicts.run_git", return_value=_git_proc("")),
            patch("sandbox_executor.flow.stage_conflicts.check_pr_mergeability") as probe,
        ):
            detection = detect_stage_conflicts(str(tmp_path), "main", None)
        probe.assert_not_called()
        assert detection == ConflictDetection([], frozenset(), "")

    def test_probed_paths_then_conflict_marked_ledgers_are_appended(self, tmp_path: Path):
        ledger = tmp_path / LEDGER
        ledger.parent.mkdir(parents=True)
        ledger.write_text('<<<<<<< HEAD\n{"a": 1}\n=======\n{"b": 2}\n>>>>>>> main\n', encoding="utf-8")
        merge_tree = f"0123abcd\0src/a.py\0{LEDGER}\0\0" + "1\0src/a.py\0CONFLICT (content): Merge in keep.md\0"
        with (
            patch(
                "sandbox_executor.flow.stage_conflicts.run_git",
                side_effect=[_git_proc(""), _git_proc(returncode=0)],
            ),
            patch(
                "sandbox_executor.flow.target_probe.run_git", return_value=_git_proc(merge_tree, returncode=1)
            ) as merge_tree_git,
            patch(
                "sandbox_executor.flow.stage_conflicts.check_pr_mergeability",
                return_value={"mergeable": "CONFLICTING"},
            ),
            patch("sandbox_executor.flow.stage_conflicts._resolve_branch_ref", return_value="refs/heads/feature"),
        ):
            detection = detect_stage_conflicts(str(tmp_path), "main", "feature")
        assert merge_tree_git.call_args.args[0] == [
            "merge-tree",
            "--write-tree",
            "--name-only",
            "-z",
            "refs/heads/feature",
            "origin/main",
        ]
        assert detection.conflicting_files == ["src/a.py", LEDGER]
        assert detection.probed_files == frozenset({"src/a.py", LEDGER})

    def test_a_failed_merge_tree_probe_reports_no_paths(self, tmp_path: Path):
        with (
            patch("sandbox_executor.flow.stage_conflicts.run_git", side_effect=[_git_proc(""), _git_proc()]),
            patch("sandbox_executor.flow.target_probe.run_git", return_value=_git_proc(returncode=128)),
            patch(
                "sandbox_executor.flow.stage_conflicts.check_pr_mergeability",
                return_value={"mergeable": "CONFLICTING"},
            ),
        ):
            detection = detect_stage_conflicts(str(tmp_path), "main", "HEAD")
        assert detection.conflicting_files == []


class TestClassifyAndResolveConflicts:
    """classify_conflicts labels each path in order; resolve_classified_conflicts append-merges the ledgers."""

    def test_classify_labels_code_ledgers_and_deferred_ledgers_in_order(self):
        detection = ConflictDetection(
            ["src\\a.py", "holon-knowledge\\ledger\\plans.jsonl", LEDGER], frozenset({LEDGER}), "feature"
        )
        assert classify_conflicts(detection) == [
            ("src\\a.py", ConflictKind.CODE),
            (PLANS_LEDGER, ConflictKind.LEDGER),
            (LEDGER, ConflictKind.DEFERRED_LEDGER),
        ]

    def test_a_probed_ledger_of_the_checkout_is_resolved_not_deferred(self):
        detection = ConflictDetection([LEDGER], frozenset({LEDGER}), "HEAD")
        assert classify_conflicts(detection) == [(LEDGER, ConflictKind.LEDGER)]

    def test_resolve_keeps_the_order_and_counts_a_failed_ledger_as_unresolved(self, tmp_path: Path):
        classified = [
            ("b.py", ConflictKind.CODE),
            (PLANS_LEDGER, ConflictKind.LEDGER),
            (LEDGER, ConflictKind.LEDGER),
            ("a.py", ConflictKind.CODE),
            ("holon-knowledge/ledger/intents.jsonl", ConflictKind.DEFERRED_LEDGER),
        ]
        with patch(
            "sandbox_executor.flow.stage_conflict_resolution._resolve_conflicting_ledger",
            side_effect=lambda _repo, path: path == PLANS_LEDGER,
        ) as resolver:
            resolved, unresolved = resolve_classified_conflicts(str(tmp_path), classified)
        assert resolved == [PLANS_LEDGER]
        assert unresolved == ["b.py", LEDGER, "a.py"]
        assert [c.args[1] for c in resolver.call_args_list] == [PLANS_LEDGER, LEDGER]

    def test_the_stage_error_lists_unresolved_paths_in_detection_order(self, tmp_path: Path):
        status = f"UU b.py\nUU {LEDGER}\nUU a.py\n"
        with (
            patch("sandbox_executor.flow.stage_conflicts.run_git", return_value=_git_proc(status)),
            patch("sandbox_executor.flow.stage_conflict_resolution._resolve_conflicting_ledger", return_value=False),
            pytest.raises(ConflictResolutionError) as raised,
        ):
            check_and_resolve_stage_conflicts(str(tmp_path), stage_name="plan")
        assert str(raised.value) == f"Unresolvable merge conflicts detected in stage 'plan': b.py, {LEDGER}, a.py"


class TestMechanicalClassesInTheWrapper:
    """The wrapper resolves the ``conflict_classes`` mechanical classes on a real unmerged checkout."""

    def test_a_kb_timestamp_only_unmerged_file_is_resolved(self, tmp_path: Path):
        def entry(updated_at: str) -> str:
            return json.dumps({"title": "t", "updated_at": updated_at}, indent=2) + "\n"

        kb = "holon-knowledge/kb/entry.json"
        _, work = build_flow_repos(tmp_path, {kb: entry("1")})
        commit_on(work, "main", {kb: entry("3")}, "target")
        commit_on(work, FLOW_BRANCH, {kb: entry("2")}, "flow")
        subprocess.run(["git", "merge", "--no-ff", "--no-commit", "origin/main"], cwd=work, capture_output=True)

        result = check_and_resolve_stage_conflicts(str(work), stage_name="execute", branch=None)

        assert result["status"] == "resolved"
        assert result["resolved_paths"] == [{"path": kb, "class": "kb_timestamp_only"}]
        assert result["resolved_ledgers"] == []
        assert (work / kb).read_text(encoding="utf-8") == entry("3")
