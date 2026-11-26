"""Review-loop stubs shared by the review-loop tests: a ledger-backed context and patched GitHub/CI seams.

A plain helper module (not a test module), so importing it never makes pytest collect or re-register test code.
"""

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from sandbox_executor.flow.github.adapter_runner import GhResult
from sandbox_executor.ledger import read_merged_rows

from tests.flow.github.fake_github import FakeGitHub, checks_for, ci_checks_payload, merge_view, view_for

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
    return read_merged_rows(str(tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"))


def _rows(tmp_path: Path, prefix: str) -> list[dict]:
    return [r for r in _ledger(tmp_path) if str(r.get("action_id", "")).startswith(prefix + "-")]


class _LoopStubs:
    """Inject a ``FakeGitHub`` as the loop's adapter and patch the empty-diff guard and the reviewed commit.

    ``merge`` and ``ci`` are the mergeability and CI states the fake reports (through ``view_for`` and ``checks_for``);
    ``github`` is the fake, whose ``calls`` show what the loop asked GitHub. ``shutil.which`` stays patched to None so
    the ``gh`` callers outside the adapter (reviewed-target and timing lookups) never reach a real ``gh``.
    """

    def __init__(self, merge=None, ci=None):
        self.merge = dict(merge or _CLEAN_MERGE)
        self.ci = dict(ci or _GREEN_CI)
        self._patches = []
        self.github = FakeGitHub(standing_view=view_for(self.merge), checks=checks_for(self.ci), head_sha="stage3-head")

    def posts(self) -> list[tuple]:
        """The ``post_review`` calls the loop made, as ``(target, flag, body)``."""
        return [args for name, args in self.github.calls if name == "post_review"]

    def __enter__(self):
        from sandbox_executor.review_target import DiffVerdict

        self._patches = [
            patch("shutil.which", return_value=None),
            patch("sandbox_executor.flow.review.loop.GitHubAdapter", return_value=self.github),
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
    """Patch the empty-diff guard and inject a clean, green ``FakeGitHub``; return ``(stack, verify, github)``.

    ``which=None`` (the default) is a fake without ``gh``: no PR is opened, CI falls back to local tests and
    mergeability to local git. ``post`` lists scripted ``post_review`` outcomes; ``ci`` is a CI status dict.
    """
    from sandbox_executor.review_target import DiffVerdict

    stack = ExitStack()
    verify = stack.enter_context(
        patch(
            "sandbox_executor.review_target.verify_review_diff",
            return_value=(verdict or DiffVerdict.VERIFIED, "guard detail"),
        )
    )
    github = FakeGitHub(
        available=bool(which),
        standing_view=merge_view(),
        checks=checks_for(ci or {"all_passed": True, "source": "local_tests"}),
        review_results=list(post or []),
        head_sha="stage3-head",
    )
    stack.enter_context(patch("shutil.which", return_value=which))
    stack.enter_context(patch("sandbox_executor.flow.review.loop.GitHubAdapter", return_value=github))
    stack.enter_context(patch("sandbox_executor.flow.review.loop._resolve_review_commit", return_value="abc123"))
    return stack, verify, github


def _green_github(**kwargs) -> FakeGitHub:
    """A ``FakeGitHub`` with a clean PR and one passing GitHub check."""
    return FakeGitHub(**{"standing_view": merge_view(), "checks": ci_checks_payload(passed=1), **kwargs})


def _pr_opens(github: FakeGitHub) -> list[tuple]:
    """The ``open_or_update`` calls the loop made, as ``(branch, base, title, body)``."""
    return [args for name, args in github.calls if name == "open_or_update"]


def _posts(github: FakeGitHub) -> list[tuple]:
    """The ``post_review`` calls the loop made, as ``(target, flag, body)``."""
    return [args for name, args in github.calls if name == "post_review"]


def _green_patches(verdict=None, which=None, ci=None, failure: GhResult | None = None):
    """``_green_loop_patches`` with a fake that has ``gh`` (``shutil.which`` still fences the other gh callers).

    ``failure`` makes the review and the last-resort discussion comment both fail with that result.
    """
    stack, verify, github = _green_loop_patches(verdict=verdict, which=which, ci=ci)
    github.available = True
    if failure is not None:
        github.review_results, github.comment_result = [failure], failure
    return stack, verify, github
