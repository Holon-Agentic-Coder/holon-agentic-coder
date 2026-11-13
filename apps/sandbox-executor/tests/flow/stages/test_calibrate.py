"""Tests for the Stage 5 commits of promoted knowledge base and wisdom artefacts."""

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from sandbox_executor.flow import FlowContext, StageStatus, run_calibrate_stage

from tests.flow.helpers import _append_jsonl, _configure_identity


class TestStage5PromotedArtifactsCommit:
    """Hermetic unit tests for Stage 5 promoted knowledge base and wisdom commits."""

    @staticmethod
    def _setup_repo(tmp_path: Path, execution_branch: str) -> None:
        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout

        git("init", "-q", "-b", "main")
        _configure_identity(tmp_path)
        (tmp_path / ".gitignore").write_text("holon-knowledge/.index/\n", encoding="utf-8")
        ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_text("", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "initial")
        git("checkout", "-q", "-b", execution_branch)
        (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "execution change")
        (tmp_path / "holon-knowledge" / ".index").mkdir(parents=True, exist_ok=True)
        (tmp_path / "holon-knowledge" / ".index" / "action_index.db").touch()

    def test_stage5_commits_promoted_knowledge_and_wisdom_on_calibrated(self, tmp_path):
        """When candidate artifacts are promoted on /calibrated, stage and commit them and ff execution_branch."""

        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout

        execution_branch = "I-100-intent/P-200-plan/E-300-exec/_"
        calibrated_branch = "I-100-intent/P-200-plan/E-300-exec/calibrated"
        self._setup_repo(tmp_path, execution_branch)

        def fake_run_calibrate(**_kwargs):
            git("checkout", "-q", "-B", calibrated_branch)
            (tmp_path / "plans").mkdir(exist_ok=True)
            (tmp_path / "plans" / "P-200-plan_calibration.md").write_text("# Calibration\n", encoding="utf-8")
            git("add", "plans")
            git("commit", "-q", "-m", "calibration report")
            report = MagicMock()
            report.calibrated_branch = calibrated_branch
            report.to_dict.return_value = {"calibrated_branch": calibrated_branch}
            return report

        def fake_reconcile(**kwargs):
            _append_jsonl(Path(kwargs["ledger_path"]), {"entry_type": "action", "action_id": "ACT-RECON-1"})

        def fake_promote(index_db, kb_dir, wb_dir, min_trials=2, dry_run=False):
            os.makedirs(kb_dir, exist_ok=True)
            os.makedirs(wb_dir, exist_ok=True)
            with open(os.path.join(kb_dir, "KB-test.json"), "w", encoding="utf-8") as f:
                f.write('{"title": "test kb"}\n')
            with open(os.path.join(wb_dir, "WB-test.json"), "w", encoding="utf-8") as f:
                f.write('{"title": "test wisdom"}\n')
            return [{"id": "KB-test", "type": "kb"}, {"id": "WB-test", "type": "wisdom"}]

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
            patch("sandbox_executor.flow.stages.calibrate.promote_pending_candidates", side_effect=fake_promote),
        ):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        log_messages = git("log", "-3", "--format=%s", calibrated_branch).strip().splitlines()
        assert log_messages[0] == "chore(calibration): record stage 5 promoted knowledge and wisdom"
        assert log_messages[1] == "chore(calibration): record stage 5 reconciliation"
        assert log_messages[2] == "calibration report"

        assert git("rev-parse", execution_branch).strip() == git("rev-parse", calibrated_branch).strip()
        assert '{"title": "test kb"}' in git("show", f"{calibrated_branch}:holon-knowledge/kb/KB-test.json")
        assert '{"title": "test wisdom"}' in git("show", f"{calibrated_branch}:holon-knowledge/wisdom/WB-test.json")
        assert git("status", "--porcelain").strip() == ""

    def test_stage5_promoted_candidates_noop_when_empty(self, tmp_path):
        """When promote_pending_candidates returns empty, no promoted artifacts commit is created."""

        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout

        execution_branch = "I-100-intent/P-200-plan/E-300-exec/_"
        calibrated_branch = "I-100-intent/P-200-plan/E-300-exec/calibrated"
        self._setup_repo(tmp_path, execution_branch)

        def fake_run_calibrate(**_kwargs):
            git("checkout", "-q", "-B", calibrated_branch)
            (tmp_path / "plans").mkdir(exist_ok=True)
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
            patch("sandbox_executor.flow.stages.calibrate.promote_pending_candidates", return_value=[]),
        ):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        latest_msg = git("log", "-1", "--format=%s", calibrated_branch).strip()
        assert latest_msg == "chore(calibration): record stage 5 reconciliation"
        all_logs = git("log", "--format=%s", calibrated_branch)
        assert "record stage 5 promoted knowledge and wisdom" not in all_logs
        assert res.payload["promoted_candidates"] == []

    def test_stage5_promoted_candidates_uncommitted_on_calibration_failure(self, tmp_path):
        """When calibration fails, promoted candidate artifacts remain uncommitted in the working tree."""

        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout

        execution_branch = "I-100-intent/P-200-plan/E-300-exec/_"
        self._setup_repo(tmp_path, execution_branch)

        def fake_run_calibrate(**_kwargs):
            raise RuntimeError("calibration failure")

        def fake_reconcile(**kwargs):
            _append_jsonl(Path(kwargs["ledger_path"]), {"entry_type": "action", "action_id": "ACT-RECON-1"})

        def fake_promote(index_db, kb_dir, wb_dir, min_trials=2, dry_run=False):
            os.makedirs(kb_dir, exist_ok=True)
            os.makedirs(wb_dir, exist_ok=True)
            with open(os.path.join(kb_dir, "KB-fail.json"), "w", encoding="utf-8") as f:
                f.write('{"title": "fail kb"}\n')
            with open(os.path.join(wb_dir, "WB-fail.json"), "w", encoding="utf-8") as f:
                f.write('{"title": "fail wisdom"}\n')
            return [{"id": "KB-fail", "type": "kb"}]

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
            patch("sandbox_executor.flow.stages.calibrate.promote_pending_candidates", side_effect=fake_promote),
        ):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        assert any(
            "Stage 5 promoted knowledge/wisdom artifacts left uncommitted: calibration failed." in msg
            for msg in ctx.logs
        )
        status = git("status", "--porcelain", "--untracked-files=all")
        assert "holon-knowledge/kb/KB-fail.json" in status
        assert "holon-knowledge/wisdom/WB-fail.json" in status
        assert git("log", "-1", "--format=%s", execution_branch).strip() == "execution change"

    def test_stage5_promoted_candidates_dry_run_noop(self, tmp_path):
        """When running in dry-run mode, promote_pending_candidates gets dry_run=True and commits are skipped."""

        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout

        execution_branch = "I-100-intent/P-200-plan/E-300-exec/_"
        self._setup_repo(tmp_path, execution_branch)

        mock_promote = MagicMock(return_value=[{"id": "KB-dry"}])

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch=execution_branch,
            plan_branch="I-100-intent/P-200-plan/_",
            dry_run=True,
            skip_push=True,
        )
        with patch("sandbox_executor.flow.stages.calibrate.promote_pending_candidates", mock_promote):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        mock_promote.assert_called_once()
        assert mock_promote.call_args.kwargs["dry_run"] is True
        assert git("log", "-1", "--format=%s", execution_branch).strip() == "execution change"

    def test_stage5_promoted_candidates_zero_churn_when_facts_exist(self, tmp_path):
        """When promoted candidates correspond to pre-existing unchanged facts, 0 commits are created."""

        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout

        execution_branch = "I-100-intent/P-200-plan/E-300-exec/_"
        calibrated_branch = "I-100-intent/P-200-plan/E-300-exec/calibrated"
        self._setup_repo(tmp_path, execution_branch)

        # Pre-create and commit KB and WB facts on execution branch
        kb_file = tmp_path / "holon-knowledge" / "kb" / "patterns" / "KB-existing.json"
        wb_file = tmp_path / "holon-knowledge" / "wisdom" / "heuristics" / "WB-existing.json"
        kb_file.parent.mkdir(parents=True, exist_ok=True)
        wb_file.parent.mkdir(parents=True, exist_ok=True)
        kb_file.write_text('{"kb_id": "KB-existing"}\n', encoding="utf-8")
        wb_file.write_text('{"wb_id": "WB-existing"}\n', encoding="utf-8")
        git("add", "holon-knowledge")
        git("commit", "-q", "-m", "pre-existing facts")

        def fake_run_calibrate(**_kwargs):
            git("checkout", "-q", "-B", calibrated_branch)
            (tmp_path / "plans").mkdir(exist_ok=True)
            (tmp_path / "plans" / "P-200-plan_calibration.md").write_text("# Calibration\n", encoding="utf-8")
            git("add", "plans")
            git("commit", "-q", "-m", "calibration report")
            report = MagicMock()
            report.calibrated_branch = calibrated_branch
            report.to_dict.return_value = {"calibrated_branch": calibrated_branch}
            return report

        def fake_reconcile(**kwargs):
            _append_jsonl(Path(kwargs["ledger_path"]), {"entry_type": "action", "action_id": "ACT-RECON-1"})

        def fake_promote(index_db, kb_dir, wb_dir, min_trials=2, dry_run=False):
            return [
                {"kb_id": "KB-existing", "persisted_path": str(kb_file)},
                {"wb_id": "WB-existing", "persisted_path": str(wb_file)},
            ]

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
            patch("sandbox_executor.flow.stages.calibrate.promote_pending_candidates", side_effect=fake_promote),
        ):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.SUCCESS
        all_logs = git("log", "--format=%s", calibrated_branch)
        assert "record stage 5 promoted knowledge and wisdom" not in all_logs
        assert git("status", "--porcelain").strip() == ""
        assert any(
            "Stage 5 promoted knowledge/wisdom artifacts unchanged: zero modifications to commit." in msg
            for msg in ctx.logs
        )
