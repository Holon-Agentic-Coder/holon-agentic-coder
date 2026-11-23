"""Review-loop stubs shared by the review-loop tests: a ledger-backed context and patched GitHub/CI seams.

A plain helper module (not a test module), so importing it never makes pytest collect or re-register test code.
"""

import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

EXEC_TARGET = "I-100-intent/P-200-plan/E-300-exec/_"
_CLEAN_MERGE = {"is_dirty": False, "merge_state_status": "CLEAN", "mergeable": "MERGEABLE"}
_GREEN_CI = {"all_passed": True, "passed": 1, "failed": 0, "pending": 0, "total": 1, "source": "github_cli"}


def _ensemble_ctx(tmp_path: Path, **kwargs):
    from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus

    (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True, exist_ok=True)
    ctx = FlowContext(
        repo_dir=str(tmp_path),
        execution_branch=EXEC_TARGET,
        agent="fake-agent",
        model="fake-model",
        token_reduce=False,
        **kwargs,
    )
    ctx.stage_results["execute"] = StageResult(
        stage=FlowStage.EXECUTE, status=StageStatus.SUCCESS, payload={"test_pass_rate": 1.0, "exit_code": 0}
    )
    return ctx


def _ledger(tmp_path: Path) -> list[dict]:
    ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
    return [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()] if ledger.exists() else []


def _rows(tmp_path: Path, prefix: str) -> list[dict]:
    return [r for r in _ledger(tmp_path) if str(r.get("action_id", "")).startswith(prefix + "-")]


class _LoopStubs:
    """Patch mergeability, CI, the empty-diff guard, the reviewed commit and posting; ``post`` is the posting mock."""

    def __init__(self, merge=None, ci=None):
        self.merge = dict(merge or _CLEAN_MERGE)
        self.ci = dict(ci or _GREEN_CI)
        self._patches = []
        self.post = MagicMock(return_value={"dual_posted": True, "official_review": {"success": True}})

    def __enter__(self):
        from sandbox_executor.review_target import DiffVerdict

        self._patches = [
            patch("sandbox_executor.flow.review.loop.shutil.which", return_value=None),
            patch(
                "sandbox_executor.flow.review.loop.check_pr_mergeability", side_effect=lambda *a, **k: dict(self.merge)
            ),
            patch("sandbox_executor.flow.review.loop.probe_ci_checks", side_effect=lambda *a, **k: dict(self.ci)),
            patch("sandbox_executor.flow.review.local_tests._default_pr_head", return_value="stage3-head"),
            patch("sandbox_executor.flow.review.loop.post_pr_review_consensus", self.post),
            patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="abc1234"),
            patch("sandbox_executor.review_target.verify_review_diff", return_value=(DiffVerdict.VERIFIED, "1 path")),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()


def _loop_ctx(tmp_path: Path, target: str = "feature/x", dry_run: bool = False):
    from sandbox_executor.flow import FlowContext, FlowStage, StageResult, StageStatus

    (tmp_path / "holon-knowledge" / "ledger").mkdir(parents=True, exist_ok=True)
    ctx = FlowContext(repo_dir=str(tmp_path), execution_branch=target, token_reduce=False, dry_run=dry_run)
    ctx.stage_results["execute"] = StageResult(
        stage=FlowStage.EXECUTE,
        status=StageStatus.SUCCESS,
        payload={"head_sha": "stage3-head", "test_pass_rate": 1.0, "exit_code": 0},
    )
    return ctx


def _green_loop_patches(verdict=None, post=None, which=None, ci=None):
    from sandbox_executor.review_target import DiffVerdict

    stack = ExitStack()
    verify = stack.enter_context(
        patch(
            "sandbox_executor.review_target.verify_review_diff",
            return_value=(verdict or DiffVerdict.VERIFIED, "guard detail"),
        )
    )
    stack.enter_context(patch("sandbox_executor.flow.review.loop.shutil.which", return_value=which))
    stack.enter_context(
        patch("sandbox_executor.flow.review.loop.check_pr_mergeability", return_value={"is_dirty": False})
    )
    stack.enter_context(
        patch(
            "sandbox_executor.flow.review.loop.probe_ci_checks",
            return_value=ci or {"all_passed": True, "source": "local_tests"},
        )
    )
    stack.enter_context(patch("sandbox_executor.flow.review.local_tests._default_pr_head", return_value="stage3-head"))
    stack.enter_context(patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="abc123"))
    mock_post = stack.enter_context(
        patch(
            "sandbox_executor.flow.review.loop.post_pr_review_consensus",
            **(post or {"return_value": {"dual_posted": True}}),
        )
    )
    return stack, verify, mock_post
