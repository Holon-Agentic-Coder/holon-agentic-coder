"""Tests for the Stage 5 commits of promoted knowledge base and wisdom artefacts."""

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from sandbox_executor.flow import FlowContext, StageStatus, run_calibrate_stage
from sandbox_executor.ledger import read_merged_text

from tests.flow.helpers import _append_jsonl, _configure_identity
from tests.flow.sleep_patch import scoped_sleep_patch


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

    def test_stage5_calibration_failure_skips_reconciliation_and_promotion(self, tmp_path):
        """When calibration fails, Stage 5 fails closed: nothing is reconciled, promoted or committed."""

        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout

        execution_branch = "I-100-intent/P-200-plan/E-300-exec/_"
        self._setup_repo(tmp_path, execution_branch)
        execution_tip = git("rev-parse", execution_branch).strip()
        ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"

        def fake_run_calibrate(**_kwargs):
            raise RuntimeError("calibration failure")

        fake_reconcile = MagicMock()
        fake_promote = MagicMock(return_value=[{"id": "KB-fail", "type": "kb"}])

        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch=execution_branch,
            plan_branch="I-100-intent/P-200-plan/_",
            dry_run=False,
            skip_push=True,
        )
        with (
            scoped_sleep_patch("sandbox_executor.flow.calibrate_retry") as sleep,
            patch("sandbox_executor.flow.stages.calibrate.run_calibrate", side_effect=fake_run_calibrate),
            patch("sandbox_executor.flow.stages.calibrate.reconcile_records", fake_reconcile),
            patch("sandbox_executor.flow.stages.calibrate.promote_pending_candidates", fake_promote),
        ):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.FAILED
        fake_reconcile.assert_not_called()
        fake_promote.assert_not_called()
        sleep.assert_not_called()
        status = git("status", "--porcelain", "--untracked-files=all")
        assert "holon-knowledge/kb/KB-fail.json" not in status
        assert "holon-knowledge/wisdom/WB-fail.json" not in status
        assert git("rev-parse", execution_branch).strip() == execution_tip
        rows = [line for line in (read_merged_text(str(ledger)) or "").splitlines() if line.strip()]
        assert len(rows) == 1
        assert '"outcome": "failure"' in rows[0]
        assert "ACT-CAL-" in rows[0]

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

    def test_stage5_self_check_failure_fails_stage_commits_and_pushes_nothing(self, tmp_path):
        """A report the self-check rejects fails Stage 5: no commit, no push, the condition named in the log."""
        from sandbox_executor.flow import FlowStage, StageResult

        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout

        execution_branch = "I-100-intent/P-200-plan/E-300-exec/_"
        self._setup_repo(tmp_path, execution_branch)
        approved = git("rev-parse", "main").strip()
        heads_before = git("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads")
        ctx = FlowContext(
            repo_dir=str(tmp_path),
            execution_branch=execution_branch,
            plan_branch="I-100-intent/P-200-plan/_",
            dry_run=False,
            skip_push=False,
        )
        # The review approved the initial commit; the execution head adds code after it, so they disagree.
        review_payload = {"consensus": {"approved": True, "target_commit": approved}}
        ctx.stage_results["review"] = StageResult(
            stage=FlowStage.REVIEW, status=StageStatus.SUCCESS, payload=review_payload
        )
        # The merged-head CI gate passed for the moved head; the self-check must still reject the report.
        gate_passed = (None, {"state": "passed", "head": git("rev-parse", execution_branch).strip()})
        with (
            patch("sandbox_executor.flow.stages.calibrate.stage5_merged_head_gate", return_value=gate_passed),
            patch("sandbox_executor.flow.stages.calibrate.publish_calibration") as mock_publish,
            patch("sandbox_executor.flow.stages.calibrate.reconcile_records") as mock_reconcile,
            patch("sandbox_executor.flow.stages.calibrate.record_calibration_action") as mock_record,
        ):
            res = run_calibrate_stage(ctx)

        assert res.status == StageStatus.FAILED
        assert "evaluated_not_approved" in res.error
        assert "nothing was committed or pushed" in res.error
        assert res.payload["self_check_conditions"] == ["evaluated_not_approved"]
        assert ctx.stage_results["calibrate"] is res
        assert any("self-check violated: evaluated_not_approved" in line for line in ctx.logs)
        mock_publish.assert_not_called()
        mock_reconcile.assert_not_called()
        mock_record.assert_not_called()
        assert git("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads") == heads_before
        assert not (tmp_path / "plans").exists()


def test_stage5_forwards_the_context_review_accounting_to_the_calibrate_step(tmp_path):
    """The calibrate step receives the counters of the flow's context; a context without them passes None."""
    from sandbox_executor.flow.stage5 import Stage5Steps, run_stage5
    from sandbox_executor.review_accounting import count_accounting

    seen: list[dict] = []

    def calibrate(**kwargs):
        seen.append(kwargs)
        return MagicMock(calibrated_branch=None, to_dict=MagicMock(return_value={}))

    steps = Stage5Steps(calibrate, MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock())
    counted = FlowContext(repo_dir=str(tmp_path), execution_branch="I-1-x/P-2-a/E-3-a/_")
    count_accounting(counted, "scoped_reviews")
    run_stage5(counted, steps, None, report_only=True)
    run_stage5(FlowContext(repo_dir=str(tmp_path), execution_branch="I-1-x/P-2-a/E-3-a/_"), steps, None, True)

    assert seen[0]["review_accounting"]["scoped_reviews"] == 1
    assert "seen_events" not in seen[0]["review_accounting"]
    assert seen[1]["review_accounting"] is None


def _setup_repo_on_main_with_submodule(tmp_path: Path, execution_branch: str, *, create_local_module: bool) -> Path:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout

    TestStage5PromotedArtifactsCommit._setup_repo(tmp_path, execution_branch)
    git("checkout", "-q", "main")
    (tmp_path / ".gitmodules").write_text(
        '[submodule "holon-coherence"]\n\tpath = holon-coherence\n\turl = ./holon-coherence\n',
        encoding="utf-8",
    )
    sub_dir = tmp_path / "holon-coherence"
    sub_dir.mkdir(exist_ok=True)
    module_gitdir = tmp_path / ".git" / "modules" / "holon-coherence"
    if create_local_module:
        subprocess.run(["git", "init", "--bare", "-q", str(module_gitdir)], check=True)
        subprocess.run(
            ["git", "config", "--file", str(module_gitdir / "config"), "core.bare", "false"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "config",
                "--file",
                str(module_gitdir / "config"),
                "core.worktree",
                "/nonexistent-container/workspace/holon-coherence",
            ],
            check=True,
        )
    (sub_dir / ".git").write_text(
        "gitdir: /nonexistent-container/workspace/.git/modules/holon-coherence\n",
        encoding="utf-8",
    )
    return module_gitdir


def test_stage5_repairs_container_submodule_gitfile_before_checkout(tmp_path):
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout

    execution_branch = "I-100-intent/P-200-plan/E-300-exec/_"
    calibrated_branch = "I-100-intent/P-200-plan/E-300-exec/calibrated"
    module_gitdir = _setup_repo_on_main_with_submodule(tmp_path, execution_branch, create_local_module=True)
    observed_branches: list[str] = []

    def fake_run_calibrate(**_kwargs):
        observed_branches.append(git("rev-parse", "--abbrev-ref", "HEAD").strip())
        git("checkout", "-q", "-B", calibrated_branch)
        (tmp_path / "plans").mkdir(exist_ok=True)
        (tmp_path / "plans" / "P-200-plan_calibration.md").write_text("# Calibration\n", encoding="utf-8")
        git("add", "plans")
        git("commit", "-q", "-m", "calibration report")
        report = MagicMock()
        report.calibrated_branch = calibrated_branch
        report.to_dict.return_value = {"calibrated_branch": calibrated_branch}
        return report

    ctx = FlowContext(
        repo_dir=str(tmp_path),
        execution_branch=execution_branch,
        plan_branch="I-100-intent/P-200-plan/_",
        dry_run=False,
        skip_push=True,
    )
    with (
        patch("sandbox_executor.flow.stages.calibrate.run_calibrate", side_effect=fake_run_calibrate),
        patch("sandbox_executor.flow.stages.calibrate.reconcile_records"),
        patch("sandbox_executor.flow.stages.calibrate.promote_pending_candidates", return_value=[]),
    ):
        res = run_calibrate_stage(ctx)

    assert res.status == StageStatus.SUCCESS
    assert observed_branches == [execution_branch]
    assert (tmp_path / "holon-coherence" / ".git").read_text(encoding="utf-8").strip() == (
        "gitdir: ../.git/modules/holon-coherence"
    )
    worktree_val = subprocess.run(
        ["git", "config", "--file", str(module_gitdir / "config"), "--get", "core.worktree"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert worktree_val == "../../../holon-coherence"
    assert any("holon-coherence/.git" in line for line in ctx.logs)


def test_stage5_rejects_unrepairable_submodule_gitfile_before_checkout(tmp_path):
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout

    execution_branch = "I-100-intent/P-200-plan/E-300-exec/_"
    _setup_repo_on_main_with_submodule(tmp_path, execution_branch, create_local_module=False)
    ctx = FlowContext(
        repo_dir=str(tmp_path),
        execution_branch=execution_branch,
        plan_branch="I-100-intent/P-200-plan/_",
        dry_run=False,
        skip_push=True,
    )
    with patch("sandbox_executor.flow.stages.calibrate._checkout_execution_branch") as mock_checkout:
        res = run_calibrate_stage(ctx)

    assert res.status == StageStatus.FAILED
    assert "holon-coherence/.git" in (res.error or "")
    mock_checkout.assert_not_called()
    assert git("rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
