from unittest.mock import patch

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
from sandbox_executor.ledger import read_merged_rows
from sandbox_executor.ledger.fragments import iter_fragment_files

from tests.flow.github.fake_github import FakeGitHub, checks_for, spawn_failure, view_for
from tests.review_fakes import clean_reviewer


class TestSemanticPRAndConsensusReview:
    def test_generate_semantic_pr_metadata_maps_categories_to_conventional_prefixes_and_scopes(self):
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

    def test_generate_semantic_pr_metadata_body_has_summary_changes_references_and_plan_sections(self):
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

    def test_generate_semantic_pr_metadata_falls_back_to_defaults_for_empty_intent(self):
        title, body = generate_semantic_pr_metadata({})
        assert title == "feat(core): Update components"
        assert "## Summary" in body
        assert "No description provided." in body
        assert "## Key Changes" in body
        assert "## References" in body
        assert "None" in body
        assert "## Plan & Validation Metadata" in body
        assert "- **Plan ID:** `N/A`" in body

    def test_generate_semantic_pr_metadata_truncates_titles_over_100_characters(self):
        intent = {
            "category": "feature",
            "labels": ["extremely-long-label-scope"],
            "title": "A" * 120,
        }
        title, _ = generate_semantic_pr_metadata(intent)
        assert len(title) <= 100
        assert title.endswith("...")

    def test_post_pr_review_consensus_posts_once_as_approving_review(self):
        github = FakeGitHub(review_results=[(0, "success", "")])
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
            repo_dir="/tmp",
            target="42",
            consensus_data=consensus_data,
            reviewers=[clean_reviewer(1)],
            github=github,
        )

        assert res["dual_posted"] is True
        assert res["discussion_comment"]["attempted"] is False
        assert res["discussion_comment"]["success"] is False
        assert res["official_review"]["success"] is True
        assert res["official_review"]["self_approval_fallback"] is False
        assert res["official_review"]["flag"] == "--approve"

        # why: only the official review posts, never gh pr comment, to avoid duplicate posts while dual_posted is True
        assert github.methods() == ["post_review"]
        target, flag, body = github.calls[0][1]
        assert (target, flag) == ("42", "--approve")
        assert body.strip()

    def test_post_pr_review_consensus_falls_back_to_comment_review_on_422_self_approval(self):
        github = FakeGitHub(
            review_results=[
                (1, "", "GraphQL: Can not approve your own pull request (HTTP 422)"),
                (0, "review comment posted", ""),
            ]
        )
        consensus_data = {
            "approved": True,
            "ensemble_verdict": "APPROVED",
            "status": "approved",
            "iterations": 1,
            "max_review_iterations": 25,
        }
        res = post_pr_review_consensus(
            repo_dir="/tmp",
            target="42",
            consensus_data=consensus_data,
            reviewers=[clean_reviewer(1)],
            github=github,
        )

        assert res["dual_posted"] is True
        assert res["discussion_comment"]["attempted"] is False
        assert res["official_review"]["success"] is True
        assert res["official_review"]["self_approval_fallback"] is True
        assert res["official_review"]["flag"] == "--comment"

        assert github.methods() == ["post_review", "post_review"]
        assert github.calls[0][1][:2] == ("42", "--approve")
        assert github.calls[1][1][:2] == ("42", "--comment")
        assert github.calls[0][1][2] == github.calls[1][1][2]

    def test_post_pr_review_consensus_review_failure_falls_back_to_comment(self):
        github = FakeGitHub(review_results=[(1, "", "API rate limit")], comment_result=(0, "comment posted", ""))

        consensus_data = {"approved": True}
        res = post_pr_review_consensus(repo_dir="/tmp", target="42", consensus_data=consensus_data, github=github)

        assert res["official_review"]["success"] is False
        assert res["discussion_comment"]["attempted"] is True
        assert res["discussion_comment"]["success"] is True
        assert res["dual_posted"] is True
        assert github.methods() == ["post_review", "post_comment"]
        assert github.calls[1][1][0] == "42"

    def test_post_pr_review_consensus_non_422_failure_skips_self_approval_fallback(self):
        github = FakeGitHub(
            review_results=[(1, "", "Internal Server Error (HTTP 500)")], comment_result=(0, "commented", "")
        )

        consensus_data = {"approved": True, "ensemble_verdict": "APPROVED"}
        res = post_pr_review_consensus(
            repo_dir="/tmp",
            target="42",
            consensus_data=consensus_data,
            reviewers=[clean_reviewer(1)],
            github=github,
        )

        assert res["official_review"]["success"] is False
        assert res["official_review"]["self_approval_fallback"] is False
        assert github.methods() == ["post_review", "post_comment"]
        assert github.calls[0][1][:2] == ("42", "--approve")
        assert github.calls[1][1][0] == "42"

    def test_post_pr_review_consensus_when_gh_missing_returns_diagnostics(self):
        github = FakeGitHub(available=False)
        consensus_data = {"approved": True, "status": "approved", "iterations": 1}
        res = post_pr_review_consensus(repo_dir="/tmp", target="42", consensus_data=consensus_data, github=github)

        assert res["gh_available"] is False
        assert res["dual_posted"] is False
        assert res["discussion_comment"]["attempted"] is False
        assert res["discussion_comment"]["success"] is False
        assert "not available in PATH" in res["diagnostics"]
        assert "not available in PATH" in res["discussion_comment"]["output"]
        assert res["official_review"]["attempted"] is False
        assert res["official_review"]["success"] is False
        assert "not available in PATH" in res["official_review"]["output"]
        assert github.calls == []

    def test_post_pr_review_consensus_failure_surfaces_exit_code_and_stderr(self):
        github = FakeGitHub(
            review_results=[(2, "", "GraphQL error: Network timeout")],
            comment_result=(2, "", "GraphQL error: Network timeout"),
        )

        consensus_data = {"approved": True}
        res = post_pr_review_consensus(repo_dir="/tmp", target="42", consensus_data=consensus_data, github=github)

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

    def test_post_pr_review_consensus_spawn_failure_is_reported_as_an_exception(self):
        github = FakeGitHub(review_results=[spawn_failure()], comment_result=spawn_failure())

        res = post_pr_review_consensus(repo_dir="/tmp", target="42", consensus_data={"approved": True}, github=github)

        assert "gh pr review encountered exception: timed out" in res["official_review"]["output"]
        assert "gh pr comment encountered exception: timed out" in res["discussion_comment"]["output"]
        assert res["dual_posted"] is False

    def test_create_or_update_pr_creates_when_absent_and_edits_when_present(self):
        create = FakeGitHub(create_number=1)
        res_create = create_or_update_pr("/tmp", "my-branch", "main", "feat: title", "body", github=create)
        assert res_create["action"] == "create"
        assert res_create["success"] is True
        assert res_create["pr_number"] == 1
        assert create.calls == [("open_or_update", ("my-branch", "main", "feat: title", "body"))]

        edit = FakeGitHub(prs={"my-branch": 99})
        res_edit = create_or_update_pr("/tmp", "my-branch", "main", "feat: new title", "new body", github=edit)
        assert res_edit["action"] == "edit"
        assert res_edit["pr_number"] == 99
        assert res_edit["success"] is True
        assert edit.calls == [("open_or_update", ("my-branch", "main", "feat: new title", "new body"))]

    def test_create_or_update_pr_not_attempted_and_error_outcomes(self):
        assert create_or_update_pr("/tmp", "b", github=FakeGitHub(available=False))["attempted"] is False
        assert create_or_update_pr("/tmp", "  ", github=FakeGitHub())["attempted"] is False
        res = create_or_update_pr("/tmp", "b", github=FakeGitHub(open_error="timed out"))
        assert (res["attempted"], res["success"], res["action"], res["pr_number"]) == (True, False, "error", None)
        assert res["output"] == "timed out"

    def test_review_loop_ledger_records_evaluation_reviewers_findings_and_dual_post_without_resolver(self, tmp_path):
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

        # why: no resolver exists, so the loop stops after the dirty first view and never reaches the clean second one
        github = FakeGitHub(
            views=[
                view_for({"is_dirty": True, "merge_state_status": "DIRTY", "mergeable": "CONFLICTING"}),
                view_for({"is_dirty": False, "merge_state_status": "CLEAN", "mergeable": "MERGEABLE"}),
            ],
            checks=checks_for({"all_passed": True, "passed": 1, "total": 1, "source": "github_cli"}),
            head_sha="stage3-head",
        )
        with (
            patch("sandbox_executor.flow._execution_effective_diff", return_value=(True, "1 file changed")),
        ):
            res = run_review_loop(
                ctx,
                target="I-100-intent/P-200-plan/E-300-exec/_",
                max_iterations=1,
                dry_run=False,
                github=github,
            )
            assert res["consensus"]["approved"] is False
            assert res["consensus"]["status"] == "conflicted_dirty"
            assert res["ensemble_verdict"] == "CHANGES_REQUESTED"
            assert res["total_iterations"] == 1
            assert github.methods()[0] == "open_or_update" and "post_review" in github.methods()

        exec_file = ledger_dir / "executions.jsonl"
        assert iter_fragment_files(str(exec_file))
        records = read_merged_rows(str(exec_file))

        action_records = [r for r in records if r.get("entry_type") == "action"]

        eval_records = [r for r in action_records if "evaluation for" in r.get("action", "")]
        assert len(eval_records) == 1
        assert eval_records[0]["review_iteration"] == 1
        assert eval_records[0]["category"] == "code"

        reviewer_records = [
            r for r in action_records if "review_reviewer_index" in r and r["action_id"][:7] == "ACT-REV"
        ]
        assert [r["review_reviewer_index"] for r in reviewer_records] == [1, 2, 3]
        assert {r["review_verdict"] for r in reviewer_records} == {"APPROVED"}

        finding_records = [r for r in action_records if "finding detected" in r.get("action", "")]
        assert len(finding_records) == 1
        assert finding_records[0]["review_finding_ids"] == ["FIND-DIRTY-001"]
        assert finding_records[0]["review_severity"] == "critical"
        assert finding_records[0]["outcome"] == "failure"

        assert not [r for r in action_records if str(r.get("action_id", "")).startswith("ACT-RES")]

        post_records = [r for r in action_records if "Review consensus dual-posted" in r.get("action", "")]
        assert len(post_records) == 1
        assert post_records[0]["outcome"] == "success"
        assert "dual-post" in post_records[0]["labels"]
