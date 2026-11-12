"""Tests for semantic PR metadata and the posted consensus review."""

import json
from unittest.mock import MagicMock, patch

from sandbox_executor.flow import (
    FlowContext,
    FlowStage,
    StageResult,
    StageStatus,
    create_or_update_pr,
    generate_semantic_pr_metadata,
    post_pr_review_consensus,
    run_review_loop,
)

from tests.review_fakes import clean_reviewer


class TestSemanticPRAndConsensusReview:
    """Hermetic unit tests for semantic PR generation, single-post review consensus,
    and granular ledger action logging."""

    def test_generate_semantic_pr_metadata_conventional_categories(self):
        """Verify category mapping to conventional commit prefixes and scope detection."""
        cases = [
            ({"category": "feature", "slug": "my-feat", "title": "Add cool feature"}, "feat(my): Add cool feature"),
            ({"category": "fix", "labels": ["api"], "title": "Fix crash on null"}, "fix(api): Fix crash on null"),
            (
                {"category": "bugfix", "slug": "db-patch", "title": "Patch database leak"},
                "fix(db): Patch database leak",
            ),
            (
                {"category": "refactor", "slug": "cleanup-flow", "title": "Refactor flow stages"},
                "refactor(cleanup): Refactor flow stages",
            ),
            (
                {"category": "documentation", "slug": "docs-guide", "title": "Update README"},
                "docs(docs): Update README",
            ),
            ({"category": "chore", "title": "Bump dependencies"}, "chore(core): Bump dependencies"),
            ({"category": "test", "labels": ["flow"], "title": "Add unit tests"}, "test(flow): Add unit tests"),
            (
                {"category": "ci", "labels": ["github-actions"], "title": "Update matrix"},
                "ci(github-actions): Update matrix",
            ),
        ]
        for intent, expected_prefix in cases:
            title, _ = generate_semantic_pr_metadata(intent)
            assert title == expected_prefix, f"Expected '{expected_prefix}', got '{title}'"

    def test_generate_semantic_pr_metadata_markdown_structure(self):
        """Verify structured markdown body includes Summary, Key Changes, References, and Plan Metadata."""
        intent = {
            "category": "feature",
            "slug": "flow-enhancements",
            "title": "Enhance review loop and PR generation",
            "description": "Comprehensive flow pipeline enhancements.",
            "goal": "1) MAKEFILE ALIGNMENT\n2) LEDGER ACTIONS\n3) PR CONSENSUS",
            "references": ["0116", "0113", "0119", "0109"],
            "labels": ["makefile", "flow"],
        }
        plan_meta = {
            "plan_id": "P-1792243199-antigravity",
            "ev": 81.0,
            "agent": "antigravity-agent",
            "model": "gemini-3.8-flash-medium",
        }
        title, body = generate_semantic_pr_metadata(intent, plan_meta)

        assert title == "feat(makefile): Enhance review loop and PR generation"
        assert "## Summary" in body
        assert "Comprehensive flow pipeline enhancements." in body
        assert "## Key Changes" in body
        assert "- MAKEFILE ALIGNMENT" in body
        assert "- LEDGER ACTIONS" in body
        assert "- PR CONSENSUS" in body
        assert "## References" in body
        assert "References: 0116, 0113, 0119, 0109" in body
        assert "## Plan & Validation Metadata" in body
        assert "- **Plan ID:** `P-1792243199-antigravity`" in body
        assert "- **Expected Value (EV):** `81.00`" in body
        assert "- **Agent:** `antigravity-agent`" in body
        assert "- **Model:** `gemini-3.8-flash-medium`" in body

    def test_generate_semantic_pr_metadata_fallback_handling(self):
        """Verify resilient fallback defaults for empty or missing intent data."""
        title, body = generate_semantic_pr_metadata({})
        assert title == "feat(core): Update components"
        assert "## Summary" in body
        assert "No description provided." in body
        assert "## Key Changes" in body
        assert "## References" in body
        assert "None" in body
        assert "## Plan & Validation Metadata" in body
        assert "- **Plan ID:** `N/A`" in body

    def test_generate_semantic_pr_metadata_title_length_truncation(self):
        """Verify title truncation when length exceeds 100 characters."""
        intent = {
            "category": "feature",
            "labels": ["extremely-long-label-scope"],
            "title": "A" * 120,
        }
        title, _ = generate_semantic_pr_metadata(intent)
        assert len(title) <= 100
        assert title.endswith("...")

    def test_post_pr_review_consensus_single_post(self):
        """Verify the consensus report is posted exactly once via gh pr review --approve."""
        with patch("shutil.which", return_value="/usr/bin/gh"), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="success", stderr="")
            consensus_data = {
                "approved": True,
                "ensemble_verdict": "APPROVED",
                "status": "approved",
                "iterations": 1,
                "max_review_iterations": 25,
                "ci_checks": {"passed": 5, "total": 5, "source": "github_cli"},
                "mergeability": {"mergeable": "MERGEABLE", "merge_state_status": "CLEAN"},
                "trailers": "Action-Origin: pr_review",
            }
            res = post_pr_review_consensus(
                repo_dir="/tmp", target="42", consensus_data=consensus_data, reviewers=[clean_reviewer(1)]
            )

            assert res["dual_posted"] is True
            assert res["discussion_comment"]["attempted"] is False
            assert res["discussion_comment"]["success"] is False
            assert res["official_review"]["success"] is True
            assert res["official_review"]["self_approval_fallback"] is False
            assert res["official_review"]["flag"] == "--approve"

            # Exactly one visible post: the official review submission, never gh pr comment.
            assert mock_run.call_count == 1
            review_args = mock_run.call_args_list[0][0][0]
            assert review_args[:5] == ["gh", "pr", "review", "42", "--approve"]

    def test_post_pr_review_consensus_self_approval_422_fallback(self):
        """Verify HTTP 422 author self-approval restriction triggers graceful fallback to --comment."""
        with patch("shutil.which", return_value="/usr/bin/gh"), patch("subprocess.run") as mock_run:
            mock_approve = MagicMock(
                returncode=1,
                stdout="",
                stderr="GraphQL: Can not approve your own pull request (HTTP 422)",
            )
            mock_fallback = MagicMock(returncode=0, stdout="review comment posted", stderr="")
            mock_run.side_effect = [mock_approve, mock_fallback]

            consensus_data = {
                "approved": True,
                "ensemble_verdict": "APPROVED",
                "status": "approved",
                "iterations": 1,
                "max_review_iterations": 25,
            }
            res = post_pr_review_consensus(
                repo_dir="/tmp", target="42", consensus_data=consensus_data, reviewers=[clean_reviewer(1)]
            )

            assert res["dual_posted"] is True
            assert res["discussion_comment"]["attempted"] is False
            assert res["official_review"]["success"] is True
            assert res["official_review"]["self_approval_fallback"] is True
            assert res["official_review"]["flag"] == "--comment"

            # Both calls belong to the gh pr review ladder; no gh pr comment is issued.
            assert mock_run.call_count == 2
            approve_args = mock_run.call_args_list[0][0][0]
            fallback_args = mock_run.call_args_list[1][0][0]
            assert approve_args[:5] == ["gh", "pr", "review", "42", "--approve"]
            assert fallback_args[:5] == ["gh", "pr", "review", "42", "--comment"]

    def test_post_pr_review_consensus_review_failure_falls_back_to_comment(self):
        """Verify a failed gh pr review submission falls back to a last-resort gh pr comment."""
        with patch("shutil.which", return_value="/usr/bin/gh"), patch("subprocess.run") as mock_run:
            mock_review = MagicMock(returncode=1, stdout="", stderr="API rate limit")
            mock_comment = MagicMock(returncode=0, stdout="comment posted", stderr="")
            mock_run.side_effect = [mock_review, mock_comment]

            consensus_data = {"approved": True}
            res = post_pr_review_consensus(repo_dir="/tmp", target="42", consensus_data=consensus_data)

            assert res["official_review"]["success"] is False
            assert res["discussion_comment"]["attempted"] is True
            assert res["discussion_comment"]["success"] is True
            assert res["dual_posted"] is True
            assert mock_run.call_count == 2
            # The review submission runs first; gh pr comment only after it failed.
            review_args = mock_run.call_args_list[0][0][0]
            comment_args = mock_run.call_args_list[1][0][0]
            assert review_args[:3] == ["gh", "pr", "review"]
            assert comment_args[:4] == ["gh", "pr", "comment", "42"]

    def test_post_pr_review_consensus_non_422_failure_no_fallback(self):
        """Verify a non-422 --approve error skips the self-approval fallback and goes to gh pr comment."""
        with patch("shutil.which", return_value="/usr/bin/gh"), patch("subprocess.run") as mock_run:
            mock_review = MagicMock(returncode=1, stdout="", stderr="Internal Server Error (HTTP 500)")
            mock_comment = MagicMock(returncode=0, stdout="commented", stderr="")
            mock_run.side_effect = [mock_review, mock_comment]

            consensus_data = {"approved": True, "ensemble_verdict": "APPROVED"}
            res = post_pr_review_consensus(
                repo_dir="/tmp", target="42", consensus_data=consensus_data, reviewers=[clean_reviewer(1)]
            )

            assert res["official_review"]["success"] is False
            assert res["official_review"]["self_approval_fallback"] is False
            assert mock_run.call_count == 2
            # No gh pr review --comment retry: the failed --approve goes straight to the last resort.
            review_args = mock_run.call_args_list[0][0][0]
            comment_args = mock_run.call_args_list[1][0][0]
            assert review_args[:5] == ["gh", "pr", "review", "42", "--approve"]
            assert comment_args[:4] == ["gh", "pr", "comment", "42"]

    def test_post_pr_review_consensus_when_gh_missing_returns_diagnostics(self):
        """Verify post_pr_review_consensus returns clear diagnostics and handles gh absence cleanly."""
        with patch("shutil.which", return_value=None):
            consensus_data = {"approved": True, "status": "approved", "iterations": 1}
            res = post_pr_review_consensus(repo_dir="/tmp", target="42", consensus_data=consensus_data)

            assert res["gh_available"] is False
            assert res["dual_posted"] is False
            assert res["discussion_comment"]["attempted"] is False
            assert res["discussion_comment"]["success"] is False
            assert "not available in PATH" in res["diagnostics"]
            assert "not available in PATH" in res["discussion_comment"]["output"]
            assert res["official_review"]["attempted"] is False
            assert res["official_review"]["success"] is False
            assert "not available in PATH" in res["official_review"]["output"]

    def test_post_pr_review_consensus_error_output_surfaces_diagnostic_context(self):
        """Verify non-zero gh failure captures and surfaces exit code and stderr diagnostics."""
        with patch("shutil.which", return_value="/usr/bin/gh"), patch("subprocess.run") as mock_run:
            mock_review = MagicMock(returncode=2, stdout="", stderr="GraphQL error: Network timeout")
            mock_comment = MagicMock(returncode=2, stdout="", stderr="GraphQL error: Network timeout")
            mock_run.side_effect = [mock_review, mock_comment]

            consensus_data = {"approved": True}
            res = post_pr_review_consensus(repo_dir="/tmp", target="42", consensus_data=consensus_data)

            assert res["gh_available"] is True
            assert res["dual_posted"] is False
            assert res["official_review"]["success"] is False
            assert "exit code 2" in res["official_review"]["output"]
            assert "Network timeout" in res["official_review"]["output"]
            assert res["discussion_comment"]["attempted"] is True
            assert res["discussion_comment"]["success"] is False
            assert "exit code 2" in res["discussion_comment"]["output"]
            assert "Network timeout" in res["discussion_comment"]["output"]
            assert "Review posting failed" in res["diagnostics"]

    def test_create_or_update_pr(self):
        """Verify create_or_update_pr handles creation when absent and edit when present."""
        with patch("shutil.which", return_value="/usr/bin/gh"), patch("subprocess.run") as mock_run:
            # Case 1: PR does not exist -> create
            mock_view_fail = MagicMock(returncode=1, stdout="", stderr="no pull requests found")
            mock_create_ok = MagicMock(returncode=0, stdout="https://github.com/org/repo/pull/1\n", stderr="")
            mock_run.side_effect = [mock_view_fail, mock_create_ok]

            res_create = create_or_update_pr("/tmp", "my-branch", "main", "feat: title", "body")
            assert res_create["action"] == "create"
            assert res_create["success"] is True
            assert res_create["pr_number"] == 1
            create_args = mock_run.call_args_list[1][0][0]
            assert "create" in create_args

            # Case 2: PR exists -> edit
            mock_view_ok = MagicMock(returncode=0, stdout=json.dumps({"number": 99, "title": "Old"}), stderr="")
            mock_edit_ok = MagicMock(returncode=0, stdout="https://github.com/org/repo/pull/99\n", stderr="")
            mock_run.side_effect = [mock_view_ok, mock_edit_ok]

            res_edit = create_or_update_pr("/tmp", "my-branch", "main", "feat: new title", "new body")
            assert res_edit["action"] == "edit"
            assert res_edit["pr_number"] == 99
            assert res_edit["success"] is True
            edit_args = mock_run.call_args_list[3][0][0]
            assert "edit" in edit_args

    def test_review_loop_granular_ledger_records(self, tmp_path):
        """Verify executions.jsonl records the evaluation, per-reviewer rows, findings and dual-posting, no resolver."""
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True)
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            dry_run=False,
            max_review_iterations=3,
        )
        ctx.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE,
            status=StageStatus.SUCCESS,
            payload={"test_pass_rate": 1.0, "exit_code": 0},
        )

        with (
            patch("sandbox_executor.flow.review.loop.check_pr_mergeability") as mock_merge,
            patch("sandbox_executor.flow.review.loop.probe_ci_checks") as mock_ci,
            patch("sandbox_executor.flow.review.loop.post_pr_review_consensus") as mock_post,
            patch("sandbox_executor.flow._execution_effective_diff", return_value=(True, "1 file changed")),
            patch("sandbox_executor.flow.review.loop.create_or_update_pr", return_value={"success": True}),
        ):
            # Iteration 1: finding detected (dirty conflict). There is no resolver, so the loop stops there and the
            # clean second probe is never reached.
            mock_merge.side_effect = [
                {"is_dirty": True, "merge_state_status": "DIRTY", "mergeable": "CONFLICTING"},
                {"is_dirty": False, "merge_state_status": "CLEAN", "mergeable": "MERGEABLE"},
            ]
            mock_ci.return_value = {"all_passed": True, "passed": 1, "total": 1, "source": "github_cli"}
            mock_post.return_value = {
                "target": "I-100-intent/P-200-plan/E-300-exec/_",
                "dual_posted": True,
                "official_review": {"success": True},
            }

            res = run_review_loop(
                ctx,
                target="I-100-intent/P-200-plan/E-300-exec/_",
                max_iterations=1,
                dry_run=False,
            )
            assert res["consensus"]["approved"] is False
            assert res["consensus"]["status"] == "conflicted_dirty"
            assert res["ensemble_verdict"] == "CHANGES_REQUESTED"
            assert res["total_iterations"] == 1

        exec_file = ledger_dir / "executions.jsonl"
        assert exec_file.exists()
        records = [json.loads(line) for line in exec_file.read_text(encoding="utf-8").splitlines() if line.strip()]

        action_records = [r for r in records if r.get("entry_type") == "action"]

        # 1. Evaluation records
        eval_records = [r for r in action_records if "evaluation for" in r.get("action", "")]
        assert len(eval_records) == 1
        assert eval_records[0]["review_iteration"] == 1
        assert eval_records[0]["category"] == "code"

        # 2. One ACT-REV row per (fake) reviewer
        reviewer_records = [
            r for r in action_records if "review_reviewer_index" in r and r["action_id"][:7] == "ACT-REV"
        ]
        assert [r["review_reviewer_index"] for r in reviewer_records] == [1, 2, 3]
        assert {r["review_verdict"] for r in reviewer_records} == {"APPROVED"}

        # 3. Finding detection records, one per finding
        finding_records = [r for r in action_records if "finding detected" in r.get("action", "")]
        assert len(finding_records) == 1
        assert finding_records[0]["review_finding_ids"] == ["FIND-DIRTY-001"]
        assert finding_records[0]["review_severity"] == "critical"
        assert finding_records[0]["outcome"] == "failure"

        # 4. No resolver row: no automatic resolver exists
        assert not [r for r in action_records if str(r.get("action_id", "")).startswith("ACT-RES")]

        # 5. Review dual-posting action record
        post_records = [r for r in action_records if "Review consensus dual-posted" in r.get("action", "")]
        assert len(post_records) == 1
        assert post_records[0]["outcome"] == "success"
        assert "dual-post" in post_records[0]["labels"]
