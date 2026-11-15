"""Tests that the Stage 4 review loop appends validated, byte-compatible ACT-* rows to the ledger."""

import json
import os
from unittest.mock import patch

import pytest
from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus, run_review_loop

from tests.flow.helpers import (
    EXECUTION_ID_PATTERNS,
    HISTORICAL_KEYS_BY_PREFIX,
    HISTORICAL_RESOLVER_KEYS,
    HISTORICAL_REVIEW_KEYS,
    REAL_EXECUTIONS_LEDGER,
    REVIEWER_ROW_KEYS,
    _ledger_fingerprint,
    _prefix,
    _read_rows,
    _verified_diff,
)
from tests.flow.lookup_patch import patch_lookup_sites


class TestStage4LedgerProvenance:
    """Stage 4 review loop must append validated, byte-compatible ACT-* rows to executions.jsonl."""

    @pytest.fixture(autouse=True)
    def _guard_real_ledger(self):
        """Fail if any test touches the repository's own canonical execution ledger."""
        before = _ledger_fingerprint(REAL_EXECUTIONS_LEDGER)
        yield
        assert _ledger_fingerprint(REAL_EXECUTIONS_LEDGER) == before, "Real executions.jsonl was modified"

    def _run_dirty_then_clean_loop(self, tmp_path, posting_side_effect=None, posting_return=None):
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
            dry_run=False,
        )
        # The local-tests fallback only passes on a recorded Stage 3 test result.
        ctx.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"test_pass_rate": 1.0, "exit_code": 0}
        )
        mergeability = iter([{"is_dirty": True}, {"is_dirty": False}])
        if posting_side_effect:
            posting: dict = {"side_effect": posting_side_effect}
        elif posting_return is not None:
            posting = {"return_value": posting_return}
        else:
            posting = {"return_value": {"dual_posted": True, "official_review": {"success": True}}}
        with (
            patch("sandbox_executor.flow.review.loop.shutil.which", return_value=None),
            patch(
                "sandbox_executor.flow.review.loop.check_pr_mergeability",
                side_effect=lambda *a, **k: next(mergeability),
            ),
            patch(
                "sandbox_executor.flow.review.loop.probe_ci_checks",
                return_value={"all_passed": True, "source": "local_tests"},
            ),
            patch("sandbox_executor.flow.review.loop.post_pr_review_consensus", **posting),
            _verified_diff(),
        ):
            result = run_review_loop(ctx, auto_pr=False, max_iterations=1)
        return ctx, result, tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"

    def test_review_loop_appends_act_rows_to_target_repository(self, tmp_path):
        _, result, ledger = self._run_dirty_then_clean_loop(tmp_path)

        # The dirty first iteration stops the loop (no resolver); the review of it is still posted.
        assert result["consensus"]["approved"] is False
        action_rows = [r for r in _read_rows(ledger) if _prefix(r) in HISTORICAL_KEYS_BY_PREFIX]
        assert [_prefix(r) for r in action_rows] == ["ACT-REV", "ACT-REV", "ACT-REV", "ACT-REV", "ACT-FIND", "ACT-POST"]
        for row in action_rows:
            assert row["entry_type"] == "action"
            assert row["action_origin"] == "pr_review"
        timestamps = [r["created_at"] for r in action_rows]
        assert timestamps == sorted(timestamps)
        assert action_rows[-1]["outcome"] == "success"

        ledger_dir = ledger.parent
        assert sorted(p.name for p in ledger_dir.iterdir() if p.suffix == ".jsonl") == ["executions.jsonl"]
        assert not (ledger_dir / "actions.jsonl").exists()
        assert not (ledger_dir / "reviews.jsonl").exists()

    def test_review_rows_are_byte_compatible_with_historical_shapes(self, tmp_path):
        import re

        _, _, ledger = self._run_dirty_then_clean_loop(tmp_path)

        raw_lines = [line for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        seen: set[str] = set()
        for raw in raw_lines:
            row = json.loads(raw)
            prefix = _prefix(row)
            if prefix not in HISTORICAL_KEYS_BY_PREFIX:
                continue
            seen.add(prefix)
            expected = HISTORICAL_KEYS_BY_PREFIX[prefix]
            if "review_reviewer_index" in row:
                # Per-reviewer rows keep the historical keys and add only the documented reviewer keys.
                expected = [*HISTORICAL_REVIEW_KEYS[:-1], *REVIEWER_ROW_KEYS, "created_at"]
            assert list(row.keys()) == expected, prefix
            assert re.match(EXECUTION_ID_PATTERNS[prefix], row["execution_id"]), row["execution_id"]
            # Serialised exactly as json.dumps would have written the literal.
            assert raw == json.dumps(row)
        # New runs never write the per-iteration resolver row; historical ACT-RES rows stay valid.
        assert seen == set(HISTORICAL_KEYS_BY_PREFIX) - {"ACT-RES"}

    def test_act_post_recorded_when_posting_raises(self, tmp_path, caplog):
        caplog.set_level("WARNING", logger="sandbox_executor.flow")
        ctx, result, ledger = self._run_dirty_then_clean_loop(tmp_path, posting_side_effect=RuntimeError("gh exploded"))

        post_rows = [r for r in _read_rows(ledger) if _prefix(r) == "ACT-POST"]
        assert len(post_rows) == 1
        assert post_rows[0]["outcome"] == "failure"
        assert post_rows[0]["error_context"] == "gh exploded"
        assert "dual-posted" not in post_rows[0]["action"]
        assert "posting to GitHub failed" in post_rows[0]["action"]
        assert list(post_rows[0].keys()) == [*HISTORICAL_RESOLVER_KEYS[:-1], "error_context", "created_at"]
        assert result["posting"] == {"error": "gh exploded"}
        assert any("gh exploded" in rec.getMessage() for rec in caplog.records if rec.levelname == "WARNING")
        assert any("gh exploded" in line for line in ctx.logs)

    def test_act_post_redacts_credentials_when_posting_raises(self, tmp_path, caplog):
        caplog.set_level("WARNING", logger="sandbox_executor.flow")
        sample_value = "ghp_" + "Q" * 36
        ctx, result, ledger = self._run_dirty_then_clean_loop(
            tmp_path, posting_side_effect=RuntimeError(f"gh exploded using {sample_value}")
        )

        post_rows = [r for r in _read_rows(ledger) if _prefix(r) == "ACT-POST"]
        assert len(post_rows) == 1
        assert post_rows[0]["error_context"] == "gh exploded using [REDACTED_SECRET]"
        assert sample_value not in ledger.read_text(encoding="utf-8")
        assert sample_value not in str(result["posting"])
        assert all(sample_value not in rec.getMessage() for rec in caplog.records)
        assert all(sample_value not in line for line in ctx.logs)

    def test_act_post_records_returned_failure_diagnostics(self, tmp_path):
        sample_value = "ghp_" + "Z" * 36
        returned_failure = {
            "dual_posted": False,
            "diagnostics": "Review posting failed: review=False, comment=True.",
            "discussion_comment": {"attempted": True, "success": True, "output": "posted"},
            "official_review": {
                "attempted": True,
                "success": False,
                "output": f"Official review submission failed (exit code 1): bad credentials {sample_value}",
            },
        }
        _, _, ledger = self._run_dirty_then_clean_loop(tmp_path, posting_return=returned_failure)

        post_rows = [r for r in _read_rows(ledger) if _prefix(r) == "ACT-POST"]
        assert len(post_rows) == 1
        error_context = post_rows[0]["error_context"]
        assert post_rows[0]["outcome"] == "failure"
        assert "Review posting failed: review=False, comment=True." in error_context
        assert "official_review: Official review submission failed (exit code 1)" in error_context
        assert "[REDACTED_SECRET]" in error_context
        assert "discussion_comment" not in error_context
        assert sample_value not in ledger.read_text(encoding="utf-8")

    def test_act_post_error_context_redacts_before_truncating(self, tmp_path):
        from sandbox_executor.flow import POSTING_ERROR_CONTEXT_MAX_CHARS

        sample_value = "ghp_" + "K" * 36
        padding = "x" * (POSTING_ERROR_CONTEXT_MAX_CHARS - 40)
        returned_failure = {
            "dual_posted": False,
            "official_review": {"success": False, "output": f"{padding} {sample_value} trailing detail"},
        }
        _, _, ledger = self._run_dirty_then_clean_loop(tmp_path, posting_return=returned_failure)

        error_context = next(r for r in _read_rows(ledger) if _prefix(r) == "ACT-POST")["error_context"]
        assert len(error_context) <= POSTING_ERROR_CONTEXT_MAX_CHARS
        assert "ghp_" not in error_context
        assert "ghp_" not in ledger.read_text(encoding="utf-8")

    def test_dry_run_suppresses_appends_visibly(self, tmp_path):
        ctx = FlowContext(repo_dir=str(tmp_path), execution_branch="I-1/P-2/E-3/_", dry_run=True)
        with (
            patch("sandbox_executor.flow.review.loop.shutil.which", return_value=None),
            patch("sandbox_executor.flow.review.loop.check_pr_mergeability", return_value={"is_dirty": False}),
            patch(
                "sandbox_executor.flow.review.loop.probe_ci_checks",
                return_value={"all_passed": True, "source": "local_tests"},
            ),
        ):
            run_review_loop(ctx, auto_pr=False, max_iterations=1)

        assert not (tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl").exists()
        assert any("Dry run" in line and "ledger record(s) not appended" in line for line in ctx.logs)

    def test_unwritable_ledger_warns_and_does_not_raise(self, tmp_path, caplog):
        caplog.set_level("WARNING")
        ctx = FlowContext(repo_dir=str(tmp_path), execution_branch="I-1/P-2/E-3/_", dry_run=False)
        ctx.stage_results["execute"] = StageResult(
            stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"test_pass_rate": 1.0, "exit_code": 0}
        )
        ledger_path = str(tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl")
        with (
            patch("sandbox_executor.flow.review.loop.shutil.which", return_value=None),
            patch("sandbox_executor.flow.review.loop.check_pr_mergeability", return_value={"is_dirty": False}),
            patch(
                "sandbox_executor.flow.review.loop.probe_ci_checks",
                return_value={"all_passed": True, "source": "local_tests"},
            ),
            patch("sandbox_executor.flow.review.loop.post_pr_review_consensus", return_value={"dual_posted": True}),
            patch("sandbox_executor.ledger.LedgerLock.__enter__", side_effect=PermissionError("ledger denied")),
            _verified_diff(),
            patch("sandbox_executor.knowledge.records.atomic_ledger_append", side_effect=PermissionError("denied")),
        ):
            result = run_review_loop(ctx, auto_pr=False, max_iterations=1)

        assert result["consensus"]["approved"] is True
        warnings = [rec.getMessage() for rec in caplog.records if rec.levelname == "WARNING"]
        assert any(ledger_path in msg and "ledger denied" in msg and "ACT-REV" in msg for msg in warnings)
        assert any(ledger_path in msg and "ACT-POST" in msg for msg in warnings)
        assert any("review summary" in msg for msg in warnings)
        assert any(ledger_path in line and "ledger denied" in line for line in ctx.logs)
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        assert not ledger_dir.exists() or not any(ledger_dir.iterdir())

    @pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores file permissions")
    def test_read_only_ledger_directory_warns_and_does_not_raise(self, tmp_path, caplog):
        caplog.set_level("WARNING")
        ledger_dir = tmp_path / "holon-knowledge" / "ledger"
        ledger_dir.mkdir(parents=True)
        ledger_dir.chmod(0o555)
        ctx = FlowContext(repo_dir=str(tmp_path), execution_branch="I-1/P-2/E-3/_", dry_run=False)
        try:
            with (
                patch("sandbox_executor.flow.review.loop.shutil.which", return_value=None),
                patch_lookup_sites(
                    "sandbox_executor.flow.review.loop.check_pr_mergeability",
                    "sandbox_executor.flow.stage_conflicts.check_pr_mergeability",
                    return_value={"is_dirty": False},
                ),
                patch_lookup_sites(
                    "sandbox_executor.flow.github.ci.probe_ci_checks",
                    "sandbox_executor.flow.review.loop.probe_ci_checks",
                    return_value={"all_passed": True, "source": "local_tests"},
                ),
                patch("sandbox_executor.flow.review.loop.post_pr_review_consensus", return_value={"dual_posted": True}),
            ):
                run_review_loop(ctx, auto_pr=False, max_iterations=1)
        finally:
            ledger_dir.chmod(0o755)

        warnings = [rec.getMessage() for rec in caplog.records if rec.levelname == "WARNING"]
        assert any(str(ledger_dir / "executions.jsonl") in msg for msg in warnings)
        assert list(ledger_dir.iterdir()) == []

    def test_record_review_action_rejects_invalid_record_without_writing(self, tmp_path, caplog):
        from sandbox_executor.flow import record_review_action

        caplog.set_level("WARNING")
        ctx = FlowContext(repo_dir=str(tmp_path))
        ledger = tmp_path / "executions.jsonl"
        bad = {
            "action_id": "ACT-REV-1-deadbeef",
            "execution_id": "REV-ITR-1-1",
            "action": "x",
            "category": "not-a-category",
            "target": "t",
            "outcome": "success",
        }
        assert record_review_action(ctx, str(ledger), bad) is None
        assert not ledger.exists()
        assert any("not-a-category" in rec.getMessage() for rec in caplog.records if rec.levelname == "WARNING")
