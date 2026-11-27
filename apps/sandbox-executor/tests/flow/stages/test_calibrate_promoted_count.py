"""Stage 5 reports only the knowledge/wisdom candidates the current run newly promoted."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus, run_calibrate_stage
from sandbox_executor.knowledge import ActionIndexDatabase

from tests.flow.helpers import _append_jsonl, _configure_identity

EXECUTION_BRANCH = "I-100-intent/P-200-plan/E-300-exec/_"
CALIBRATED_BRANCH = "I-100-intent/P-200-plan/E-300-exec/calibrated"
PROMOTED_COMMIT = "chore(calibration): record stage 5 promoted knowledge and wisdom"
OLD_FINGERPRINTS = [f"oldfail{i}_fp" for i in range(3)]
NEW_FINGERPRINTS = [f"newfail{i}_fp" for i in range(2)]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def _setup_repo(repo: Path) -> None:
    """Create a repository on the execution branch with an empty ledger and a gitignored index."""
    _git(repo, "init", "-q", "-b", "main")
    _configure_identity(repo)
    (repo / ".gitignore").write_text("holon-knowledge/.index/\n", encoding="utf-8")
    ledger = repo / "holon-knowledge" / "ledger" / "executions.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    _git(repo, "checkout", "-q", "-b", EXECUTION_BRANCH)
    (repo / "src.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "execution change")


def _seed_index(repo: Path, fingerprints: list[str]) -> None:
    """Seed the real action index with three failures per fingerprint (a qualifying failure mode)."""
    index_dir = repo / "holon-knowledge" / ".index"
    index_dir.mkdir(parents=True, exist_ok=True)
    seed_ledger = index_dir / "seed.jsonl"
    with open(seed_ledger, "w", encoding="utf-8") as f:
        for fp in fingerprints:
            for i in range(3):
                row = {"action_id": f"act-{fp}-{i}", "context_fingerprint": fp, "verdict": "failure"}
                f.write(json.dumps(row) + "\n")
    ActionIndexDatabase(db_path=str(index_dir / "action_index.db")).sync_ledger(str(seed_ledger))


def _commit_failure_modes(repo: Path, fingerprints: list[str]) -> None:
    """Commit failure-mode files for ``fingerprints``, as an earlier flow would have."""
    target = repo / "holon-knowledge" / "kb" / "failure_modes"
    target.mkdir(parents=True, exist_ok=True)
    for fp in fingerprints:
        (target / f"KB-failure-{fp[:8]}.json").write_text(json.dumps({"kb_id": f"KB-failure-{fp[:8]}"}) + "\n")
    _git(repo, "add", "holon-knowledge/kb")
    _git(repo, "commit", "-q", "-m", "earlier promotions")


def _run_stage(repo: Path, run_number: int = 1) -> tuple[FlowContext, object]:
    """Run Stage 5 with a fake calibration engine and reconciliation, and the real promotion."""

    def fake_run_calibrate(**_kwargs):
        _git(repo, "checkout", "-q", "-B", CALIBRATED_BRANCH)
        (repo / "plans").mkdir(exist_ok=True)
        (repo / "plans" / "P-200-plan_calibration.md").write_text(f"# Calibration {run_number}\n", encoding="utf-8")
        _git(repo, "add", "plans")
        _git(repo, "commit", "-q", "-m", "calibration report")
        report = MagicMock()
        report.calibrated_branch = CALIBRATED_BRANCH
        report.to_dict.return_value = {"calibrated_branch": CALIBRATED_BRANCH}
        return report

    def fake_reconcile(**kwargs):
        _append_jsonl(Path(kwargs["ledger_path"]), {"entry_type": "action", "action_id": f"ACT-RECON-{run_number}"})

    ctx = FlowContext(
        repo_dir=str(repo),
        execution_branch=EXECUTION_BRANCH,
        plan_branch="I-100-intent/P-200-plan/_",
        dry_run=False,
        skip_push=True,
    )
    ctx.stage_results["execute"] = StageResult(stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS)
    ctx.stage_results["review"] = StageResult(
        stage=FlowStage.REVIEW,
        status=StageStatus.APPROVED,
        payload={
            "consensus": {"approved": True, "target_commit": "a" * 40, "pr_number": 7},
            "posting": {"dual_posted": True},
        },
    )
    with (
        patch("sandbox_executor.flow.stages.calibrate.run_calibrate", side_effect=fake_run_calibrate),
        patch("sandbox_executor.flow.stages.calibrate.reconcile_records", side_effect=fake_reconcile),
    ):
        res = run_calibrate_stage(ctx)
    return ctx, res


def _logged(ctx: FlowContext, message: str) -> bool:
    return any(msg.endswith(f"] {message}") for msg in ctx.logs)


def _promoted_commit_files(repo: Path) -> list[str]:
    assert _git(repo, "log", "-1", "--format=%s", CALIBRATED_BRANCH).strip() == PROMOTED_COMMIT
    return _git(repo, "show", "--name-only", "--format=", CALIBRATED_BRANCH).split()


def test_stage5_logs_only_the_candidates_this_run_promoted(tmp_path: Path) -> None:
    """With N candidates already promoted and M new qualifying ones, Stage 5 logs and commits M."""
    _setup_repo(tmp_path)
    _seed_index(tmp_path, OLD_FINGERPRINTS + NEW_FINGERPRINTS)
    _commit_failure_modes(tmp_path, OLD_FINGERPRINTS)

    ctx, res = _run_stage(tmp_path)

    assert res.status == StageStatus.SUCCESS
    m, total = len(NEW_FINGERPRINTS), len(OLD_FINGERPRINTS) + len(NEW_FINGERPRINTS)
    assert _logged(ctx, f"Stage 5 promoted {m} new knowledge/wisdom candidate(s) this run.")
    assert not any(f"promoted {total}" in msg for msg in ctx.logs)
    assert len(res.payload["promoted_candidates"]) == m
    fact_files = [p for p in _promoted_commit_files(tmp_path) if Path(p).name.startswith("KB-")]
    assert sorted(fact_files) == sorted(
        f"holon-knowledge/kb/failure_modes/KB-failure-{fp[:8]}.json" for fp in NEW_FINGERPRINTS
    )


def test_stage5_second_run_promotes_and_commits_nothing(tmp_path: Path) -> None:
    """A second Stage 5 against the same index logs no new candidates and adds no promoted-artefacts commit."""
    _setup_repo(tmp_path)
    _seed_index(tmp_path, NEW_FINGERPRINTS)
    _run_stage(tmp_path, run_number=1)
    assert _git(tmp_path, "log", "--format=%s", CALIBRATED_BRANCH).count(PROMOTED_COMMIT) == 1

    ctx, res = _run_stage(tmp_path, run_number=2)

    assert res.status == StageStatus.SUCCESS
    assert _logged(ctx, "Stage 5 promoted no new knowledge/wisdom candidates this run.")
    assert res.payload["promoted_candidates"] == []
    assert _git(tmp_path, "log", "--format=%s", CALIBRATED_BRANCH).count(PROMOTED_COMMIT) == 1
    assert not any("zero modifications to commit" in msg for msg in ctx.logs)


def test_stage5_commits_straggler_artifacts_when_nothing_is_new(tmp_path: Path) -> None:
    """Artefacts an earlier failed calibration left uncommitted are committed even when this run promotes none."""
    _setup_repo(tmp_path)
    straggler = tmp_path / "holon-knowledge" / "kb" / "failure_modes" / "KB-failure-straggle.json"
    straggler.parent.mkdir(parents=True)
    straggler.write_text('{"kb_id": "KB-failure-straggle"}\n', encoding="utf-8")
    (tmp_path / "holon-knowledge" / ".index").mkdir(parents=True)
    (tmp_path / "holon-knowledge" / ".index" / "action_index.db").touch()

    with patch("sandbox_executor.flow.stages.calibrate.promote_pending_candidates", return_value=[]):
        ctx, res = _run_stage(tmp_path)

    assert res.status == StageStatus.SUCCESS
    assert _logged(ctx, "Stage 5 promoted no new knowledge/wisdom candidates this run.")
    assert _promoted_commit_files(tmp_path) == ["holon-knowledge/kb/failure_modes/KB-failure-straggle.json"]
