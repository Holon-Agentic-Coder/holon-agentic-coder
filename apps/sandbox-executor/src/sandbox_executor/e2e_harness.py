"""End-to-end test harness and unconditional teardown utility for ``holon flow``."""

from __future__ import annotations

import enum
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sandbox_executor import local_llm
from sandbox_executor.budgets import Budgets
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.github.adapter import GitHubAdapter
from sandbox_executor.flow.pipeline import PipelineEngine
from sandbox_executor.git import GitRunnerCallable, run_git
from sandbox_executor.git_flow import checked_out_branch
from sandbox_executor.ledger import LedgerRewriteError
from sandbox_executor.local_llm import (
    ENV_AGENT_PROVIDER,
    ENV_HOST_LOCAL_HOSTS,
    ENV_LOCAL_BASE_URL,
    ENV_LOCAL_MODE,
    ENV_LOCAL_MODELS,
    ENV_LOCAL_PROVIDER,
    LocalLLMConfigError,
    ModelEnvelope,
)

__all__ = [
    "DEFAULT_E2E_MAX_ATTEMPTS",
    "E2EAttemptOutcome",
    "E2EError",
    "E2EMachineryError",
    "E2EModelOutputError",
    "E2EPipelineEngine",
    "E2ERunOutcome",
    "FailureCategory",
    "GhRunnerCallable",
    "PrefixScopingError",
    "TeardownReport",
    "ZeroPRCheckResult",
    "ZeroPRGuardViolationError",
    "assert_no_skip_review_flag",
    "assert_refs_within_intent_prefix",
    "build_e2e_intent_prefix",
    "classify_attempt_failure",
    "enforce_zero_pr_guard",
    "is_ref_within_intent_prefix",
    "normalise_ref_name",
    "run_e2e_flow_test",
    "run_e2e_with_retry",
    "teardown_e2e_intent",
    "validate_e2e_intent_prefix",
    "validate_e2e_local_llm_env",
]

DEFAULT_E2E_MAX_ATTEMPTS = 3
MAX_E2E_ATTEMPTS_CAP = 5

_INTENT_PREFIX_RE = re.compile(r"^I-(?P<epoch>\d+)-(?P<slug>[a-z0-9][a-z0-9-]*)$")
_PROTECTED_BRANCH_NAMES = frozenset({"main", "master", "HEAD", "origin", "origin/main", "origin/master", "origin/HEAD"})
_REF_PREFIXES = ("refs/heads/", "refs/remotes/origin/", "origin/")

_MACHINERY_ERROR_SNIPPETS: tuple[str, ...] = (
    "unhandled exception",
    "could not fetch",
    "host push of",
    "host fetch of",
    "could not check out",
    "without an active",
    "unique-id",
    "not valid json",
    "docker",
    "worktree",
    "refusing to reuse",
    "already exists on origin",
    "skip-push",
    "missing ticker",
    "ref too long",
    "slug too long",
    "exceeded its stage budget",
)

_MODEL_OUTPUT_ERROR_SNIPPETS: tuple[str, ...] = (
    "metrics validation failed",
    "empty or invalid plan file",
    "no planner agent produced",
    "over_budget",
    "exceeds the entropy budget",
    "touch_set_violation",
    "outside the plan's touch set",
    "empty_diff",
    "changed 0 path(s)",
    "carries no diff against",
    "produced no changes outside",
    "carries no executions.jsonl summary row",
    "executor agent recorded status 'failure'",
    "executor agent recorded status 'failed'",
    "agent exited with code",
    "model output",
)

GhRunnerCallable = Callable[..., subprocess.CompletedProcess[str]]


class FailureCategory(enum.StrEnum):
    """Classification of an E2E attempt failure into retryable model output vs non-retryable machinery."""

    MODEL_OUTPUT = "model_output"
    MACHINERY = "machinery"


class E2EError(RuntimeError):
    """Base exception for E2E flow harness failures."""


class E2EModelOutputError(E2EError):
    """Retryable failure caused by model output or generated code assertions failing."""


class E2EMachineryError(E2EError):
    """Non-retryable failure in flow machinery, configuration, git topology, or safety guards."""


class PrefixScopingError(E2EMachineryError):
    """Raised when an intent prefix is malformed or a candidate ref lies outside the intent prefix."""


class ZeroPRGuardViolationError(E2EMachineryError):
    """Raised when a stray pull request is detected for the disposable E2E intent or ``--skip-review`` is passed."""


@dataclass(frozen=True)
class TeardownReport:
    """Enumeration of every worktree and local/remote branch deleted during an E2E teardown."""

    intent_prefix: str
    removed_worktrees: tuple[str, ...] = ()
    deleted_local_branches: tuple[str, ...] = ()
    deleted_remote_branches: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def summary_line(self) -> str:
        """Render a concise one-line summary enumerating all deleted worktrees and branches."""
        wt = ", ".join(self.removed_worktrees) or "none"
        loc = ", ".join(self.deleted_local_branches) or "none"
        rem = ", ".join(self.deleted_remote_branches) or "none"
        return f"Teardown[{self.intent_prefix}]: worktrees=[{wt}] local_branches=[{loc}] remote_branches=[{rem}]"


@dataclass(frozen=True)
class ZeroPRCheckResult:
    """Outcome of verifying that zero pull requests exist for the disposable E2E intent prefix."""

    intent_prefix: str
    passed: bool
    matching_prs: tuple[int, ...] = ()
    closed_prs: tuple[int, ...] = ()


@dataclass(frozen=True)
class E2EAttemptOutcome:
    """Outcome of a single E2E flow attempt including its teardown and zero-PR check records."""

    attempt: int
    intent_prefix: str
    succeeded: bool
    stage_results: dict[str, Any] = field(default_factory=dict)
    teardown_report: TeardownReport | None = None
    zero_pr_result: ZeroPRCheckResult | None = None
    failure_category: FailureCategory | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class E2ERunOutcome:
    """Aggregated outcome of an E2E flow test run across up to ``max_attempts`` attempts."""

    succeeded: bool
    attempts_run: int
    intent_prefix: str
    stage_results: dict[str, Any] = field(default_factory=dict)
    teardown_reports: tuple[TeardownReport, ...] = ()
    zero_pr_results: tuple[ZeroPRCheckResult, ...] = ()
    attempts: tuple[E2EAttemptOutcome, ...] = ()
    failure_category: FailureCategory | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class _TeardownScope:
    """Internal parameter bundle for prefix-scoped worktree and branch teardown."""

    repo_dir: str
    intent_prefix: str
    disposable_worktree: str | None
    worktree_branch: str | None
    git_runner: GitRunnerCallable


def validate_e2e_intent_prefix(intent_prefix: str) -> str:
    """Validate and return a bare root intent prefix ``I-<timestamp>-<slug>``."""
    if not isinstance(intent_prefix, str):
        raise PrefixScopingError(f"Intent prefix must be a string, got {type(intent_prefix).__name__}.")
    cleaned = intent_prefix.strip()
    if not cleaned or cleaned in _PROTECTED_BRANCH_NAMES:
        raise PrefixScopingError(f"Invalid or protected E2E intent prefix: {intent_prefix!r}.")
    if "/" in cleaned or "*" in cleaned or "?" in cleaned or ".." in cleaned:
        raise PrefixScopingError(
            f"E2E intent prefix {intent_prefix!r} must be a bare root prefix without slashes or wildcards."
        )
    if not _INTENT_PREFIX_RE.fullmatch(cleaned):
        raise PrefixScopingError(
            f"E2E intent prefix {intent_prefix!r} does not match required 'I-<timestamp>-<slug>' format."
        )
    return cleaned


def build_e2e_intent_prefix(slug: str, epoch: int | None = None) -> str:
    """Construct and validate a unique disposable E2E intent prefix ``I-<epoch>-<slug>``."""
    clean_slug = re.sub(r"[^a-z0-9]+", "-", slug.strip().lower()).strip("-")
    if not clean_slug:
        raise PrefixScopingError(f"Cannot build E2E intent prefix from empty slug {slug!r}.")
    ts = int(time.time()) if epoch is None else int(epoch)
    if ts <= 0:
        raise PrefixScopingError(f"Intent epoch must be a positive integer, got {ts!r}.")
    return validate_e2e_intent_prefix(f"I-{ts}-{clean_slug}")


def normalise_ref_name(ref_name: str) -> str:
    """Strip leading ``refs/heads/``, ``refs/remotes/origin/``, or ``origin/`` from ``ref_name``."""
    cleaned = ref_name.strip()
    for prefix in _REF_PREFIXES:
        if cleaned.startswith(prefix):
            return cleaned[len(prefix) :]
    return cleaned


def _intent_slug_from_prefix(validated_prefix: str) -> str:
    match = _INTENT_PREFIX_RE.fullmatch(validated_prefix)
    return match.group("slug") if match else ""


def _is_valid_disposable_worktree_branch(branch: str, validated_prefix: str) -> bool:
    slug = _intent_slug_from_prefix(validated_prefix)
    if not branch or not slug or branch in _PROTECTED_BRANCH_NAMES:
        return False
    return branch == f"flow-{slug}" or branch == f"flow-{validated_prefix.lower()}"


def is_ref_within_intent_prefix(
    ref_name: str,
    intent_prefix: str,
    worktree_branch: str | None = None,
) -> bool:
    """Return True only when ``ref_name`` belongs strictly to ``intent_prefix`` (or its disposable worktree branch)."""
    validated_prefix = validate_e2e_intent_prefix(intent_prefix)
    if not isinstance(ref_name, str) or not ref_name.strip():
        return False
    normalised = normalise_ref_name(ref_name)
    if not normalised or normalised in _PROTECTED_BRANCH_NAMES:
        return False
    if normalised == validated_prefix or normalised.startswith(f"{validated_prefix}/"):
        return True
    if worktree_branch is not None:
        norm_wt = normalise_ref_name(worktree_branch)
        if _is_valid_disposable_worktree_branch(norm_wt, validated_prefix) and normalised == norm_wt:
            return True
    return False


def assert_refs_within_intent_prefix(
    refs: Iterable[str],
    intent_prefix: str,
    worktree_branch: str | None = None,
) -> tuple[str, ...]:
    """Verify every ref in ``refs`` is within ``intent_prefix``; raise :class:`PrefixScopingError` otherwise."""
    validated_prefix = validate_e2e_intent_prefix(intent_prefix)
    checked: list[str] = []
    out_of_scope: list[str] = []
    for ref in refs:
        if not is_ref_within_intent_prefix(ref, validated_prefix, worktree_branch=worktree_branch):
            out_of_scope.append(str(ref))
        else:
            checked.append(normalise_ref_name(ref))
    if out_of_scope:
        raise PrefixScopingError(f"Refusing to touch ref(s) outside intent prefix {validated_prefix!r}: {out_of_scope}")
    return tuple(checked)


def _invoke_git(git_runner: GitRunnerCallable, args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    return git_runner(args, cwd=cwd, check=False)


def _candidate_worktree_paths(repo_dir: str, disposable_worktree: str | None) -> list[str]:
    if not disposable_worktree:
        return []
    abs_repo = os.path.abspath(repo_dir)
    abs_wt = os.path.abspath(disposable_worktree)
    if abs_wt == abs_repo:
        raise PrefixScopingError(
            f"Refusing to tear down primary repository directory {abs_repo!r} as a disposable worktree."
        )
    paths = [abs_wt]
    parent = Path(abs_wt).parent
    base_name = Path(abs_wt).name
    if parent.is_dir():
        paths.extend(str(sibling.resolve()) for sibling in sorted(parent.glob(f"{base_name}-rev*")))
    return paths


def _remove_disposable_worktrees(scope: _TeardownScope, errors: list[str]) -> list[str]:
    removed: list[str] = []
    slug = _intent_slug_from_prefix(scope.intent_prefix)
    for wt_path in _candidate_worktree_paths(scope.repo_dir, scope.disposable_worktree):
        if not os.path.exists(wt_path):
            continue
        current = checked_out_branch(wt_path)
        if current in ("main", "master"):
            raise PrefixScopingError(
                f"Refusing to remove worktree {wt_path!r} because it has protected branch {current!r} checked out."
            )
        if (
            current is not None
            and not Path(wt_path).name.split("-rev")[0].endswith(slug)
            and not is_ref_within_intent_prefix(current, scope.intent_prefix, worktree_branch=scope.worktree_branch)
        ):
            raise PrefixScopingError(f"Refusing to remove worktree {wt_path!r} on out-of-prefix branch {current!r}.")
        res = _invoke_git(scope.git_runner, ["worktree", "remove", "--force", wt_path], scope.repo_dir)
        if res.returncode == 0 or not os.path.exists(wt_path):
            removed.append(wt_path)
        else:
            errors.append(f"git worktree remove {wt_path} failed: {(res.stderr or res.stdout).strip()}")
    _invoke_git(scope.git_runner, ["worktree", "prune"], scope.repo_dir)
    return removed


def _delete_scoped_local_branches(scope: _TeardownScope, errors: list[str]) -> list[str]:
    res = _invoke_git(scope.git_runner, ["for-each-ref", "--format=%(refname:short)", "refs/heads/"], scope.repo_dir)
    if res.returncode != 0:
        errors.append(f"git for-each-ref refs/heads/ failed: {(res.stderr or res.stdout).strip()}")
        return []
    matching = [
        line.strip()
        for line in res.stdout.splitlines()
        if line.strip()
        and is_ref_within_intent_prefix(line.strip(), scope.intent_prefix, worktree_branch=scope.worktree_branch)
    ]
    if not matching:
        return []
    current = checked_out_branch(scope.repo_dir)
    if current and current in matching:
        _invoke_git(scope.git_runner, ["checkout", "-q", "--detach", "HEAD"], scope.repo_dir)
    batch_res = _invoke_git(scope.git_runner, ["branch", "-D", *matching], scope.repo_dir)
    if batch_res.returncode == 0:
        return matching
    deleted: list[str] = []
    for branch in matching:
        del_res = _invoke_git(scope.git_runner, ["branch", "-D", branch], scope.repo_dir)
        if del_res.returncode == 0:
            deleted.append(branch)
        else:
            errors.append(f"git branch -D {branch} failed: {(del_res.stderr or del_res.stdout).strip()}")
    return deleted


def _collect_scoped_refs(lines: Iterable[str], scope: _TeardownScope, discovered: set[str]) -> None:
    for line in lines:
        raw = line.strip()
        if not raw or raw == "refs/remotes/origin/HEAD":
            continue
        short = normalise_ref_name(raw)
        if is_ref_within_intent_prefix(short, scope.intent_prefix, worktree_branch=scope.worktree_branch):
            discovered.add(short)


def _discover_scoped_remote_branches(scope: _TeardownScope) -> list[str]:
    if _invoke_git(scope.git_runner, ["remote", "get-url", "origin"], scope.repo_dir).returncode != 0:
        return []
    discovered: set[str] = set()
    tracking_res = _invoke_git(
        scope.git_runner, ["for-each-ref", "--format=%(refname)", "refs/remotes/origin/"], scope.repo_dir
    )
    if tracking_res.returncode == 0:
        _collect_scoped_refs(tracking_res.stdout.splitlines(), scope, discovered)
    if discovered:
        return sorted(discovered)
    ls_res = _invoke_git(
        scope.git_runner,
        ["ls-remote", "--heads", "origin", f"refs/heads/{scope.intent_prefix}", f"refs/heads/{scope.intent_prefix}/*"],
        scope.repo_dir,
    )
    if ls_res.returncode == 0:
        ref_cols = [parts[1] for line in ls_res.stdout.splitlines() if len(parts := line.strip().split()) >= 2]
        _collect_scoped_refs(ref_cols, scope, discovered)
    return sorted(discovered)


def _delete_scoped_remote_branches(scope: _TeardownScope, errors: list[str]) -> list[str]:
    branches = _discover_scoped_remote_branches(scope)
    if not branches:
        return []
    batch_push = _invoke_git(scope.git_runner, ["push", "origin", "--delete", *branches], scope.repo_dir)
    if batch_push.returncode == 0:
        return branches
    deleted: list[str] = []
    for branch in branches:
        push_res = _invoke_git(scope.git_runner, ["push", "origin", "--delete", branch], scope.repo_dir)
        _invoke_git(scope.git_runner, ["update-ref", "-d", f"refs/remotes/origin/{branch}"], scope.repo_dir)
        if push_res.returncode == 0 or (
            _invoke_git(
                scope.git_runner, ["rev-parse", "--verify", f"refs/remotes/origin/{branch}"], scope.repo_dir
            ).returncode
            != 0
        ):
            deleted.append(branch)
        else:
            errors.append(f"git push origin --delete {branch} failed: {(push_res.stderr or push_res.stdout).strip()}")
    return deleted


def teardown_e2e_intent(
    repo_dir: str,
    intent_prefix: str,
    *,
    disposable_worktree: str | None = None,
    worktree_branch: str | None = None,
    **kwargs: Any,
) -> TeardownReport:
    """Unconditionally remove the disposable worktree and all local/remote branches under ``intent_prefix``."""
    candidate_refs: Iterable[str] | None = kwargs.pop("candidate_refs", None)
    git_runner: GitRunnerCallable = kwargs.pop("git_runner", run_git)
    if kwargs:
        raise TypeError(f"teardown_e2e_intent() got unexpected keyword argument(s): {', '.join(sorted(kwargs))}")

    validated_prefix = validate_e2e_intent_prefix(intent_prefix)
    if candidate_refs is not None:
        assert_refs_within_intent_prefix(candidate_refs, validated_prefix, worktree_branch=worktree_branch)
    if worktree_branch is not None and not _is_valid_disposable_worktree_branch(
        normalise_ref_name(worktree_branch), validated_prefix
    ):
        raise PrefixScopingError(
            f"Worktree branch {worktree_branch!r} does not match disposable intent prefix {validated_prefix!r}."
        )

    scope = _TeardownScope(repo_dir, validated_prefix, disposable_worktree, worktree_branch, git_runner)
    errors: list[str] = []
    removed_worktrees = _remove_disposable_worktrees(scope, errors)
    deleted_local = _delete_scoped_local_branches(scope, errors)
    deleted_remote = _delete_scoped_remote_branches(scope, errors)
    return TeardownReport(
        intent_prefix=validated_prefix,
        removed_worktrees=tuple(removed_worktrees),
        deleted_local_branches=tuple(deleted_local),
        deleted_remote_branches=tuple(deleted_remote),
        errors=tuple(errors),
    )


def assert_no_skip_review_flag(
    cli_args: Sequence[str] = (),
    options: Mapping[str, Any] | None = None,
) -> None:
    """Raise :class:`ZeroPRGuardViolationError` if ``--skip-review`` or ``skip_review`` is present."""
    for arg in cli_args:
        token = str(arg).strip()
        if token == "--skip-review" or token.startswith("--skip-review="):
            raise ZeroPRGuardViolationError(
                "--skip-review was removed from 'holon flow' to protect the Stage 4 consensus boundary; "
                "E2E runs must halt at the Stage 4 pre-PR boundary via E2EPipelineEngine instead."
            )
    if options and "skip_review" in options:
        raise ZeroPRGuardViolationError(
            "'skip_review' is forbidden in E2E flow options; use E2EPipelineEngine to halt before PR creation."
        )


def _default_gh_runner(repo_dir: str) -> GhRunnerCallable | None:
    adapter = GitHubAdapter(repo_dir)
    return adapter.gh if adapter.available else None


def _parse_pr_list_items(stdout: str) -> list[dict[str, Any]]:
    text = (stdout or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise E2EMachineryError(f"gh pr list returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise E2EMachineryError("gh pr list JSON payload must be a list of pull request objects.")
    return [item for item in parsed if isinstance(item, dict)]


def _close_stray_pr(
    runner: GhRunnerCallable,
    repo_dir: str,
    pr_number: int,
    validated_prefix: str,
) -> tuple[bool, str]:
    close_args = [
        "pr",
        "close",
        str(pr_number),
        "--comment",
        f"Closed stray E2E pull request for disposable intent {validated_prefix}.",
    ]
    close_proc = runner(close_args, cwd=repo_dir)
    if close_proc.returncode == 0:
        return True, ""
    detail = (close_proc.stderr or close_proc.stdout or "").strip() or f"exit {close_proc.returncode}"
    return False, f"#{pr_number} ({detail})"


def enforce_zero_pr_guard(
    repo_dir: str,
    intent_prefix: str,
    *,
    worktree_branch: str | None = None,
    gh_runner: GhRunnerCallable | None = None,
) -> ZeroPRCheckResult:
    """Assert that zero pull requests reference branches under ``intent_prefix``."""
    validated_prefix = validate_e2e_intent_prefix(intent_prefix)
    runner = gh_runner if gh_runner is not None else _default_gh_runner(repo_dir)
    if runner is None:
        return ZeroPRCheckResult(intent_prefix=validated_prefix, passed=True)

    list_args = ["pr", "list", "--state", "all", "--json", "number,headRefName,baseRefName,state"]
    list_proc = runner(list_args, cwd=repo_dir)
    if list_proc.returncode != 0:
        detail = (list_proc.stderr or list_proc.stdout or "").strip() or f"exit code {list_proc.returncode}"
        raise E2EMachineryError(
            f"Zero-PR guard could not inspect pull requests for {validated_prefix!r}: gh pr list failed ({detail})."
        )

    matching_numbers: list[int] = []
    closed_numbers: list[int] = []
    close_failures: list[str] = []
    matching_details: list[str] = []

    for item in _parse_pr_list_items(list_proc.stdout):
        head_ref = str(item.get("headRefName") or "")
        base_ref = str(item.get("baseRefName") or "")
        head_matches = is_ref_within_intent_prefix(head_ref, validated_prefix, worktree_branch=worktree_branch)
        base_matches = is_ref_within_intent_prefix(base_ref, validated_prefix, worktree_branch=worktree_branch)
        raw_num = item.get("number")
        if not (head_matches or base_matches) or not isinstance(raw_num, int) or isinstance(raw_num, bool):
            continue
        matching_numbers.append(raw_num)
        state = str(item.get("state") or "OPEN").upper()
        matching_details.append(f"#{raw_num} (head={head_ref!r}, base={base_ref!r}, state={state})")
        if state == "OPEN":
            closed, err_detail = _close_stray_pr(runner, repo_dir, raw_num, validated_prefix)
            if closed:
                closed_numbers.append(raw_num)
            else:
                close_failures.append(err_detail)

    if matching_numbers:
        failure_suffix = f"; close failure(s): {', '.join(close_failures)}" if close_failures else ""
        raise ZeroPRGuardViolationError(
            f"Zero-PR guard violated for E2E intent prefix {validated_prefix!r}: found stray PR(s) "
            f"{', '.join(matching_details)}; closed PR(s): {closed_numbers}{failure_suffix}."
        )
    return ZeroPRCheckResult(intent_prefix=validated_prefix, passed=True)


class E2EPipelineEngine(PipelineEngine):
    """PipelineEngine subclass that halts cleanly at the Stage 4 pre-PR boundary without ``--skip-review``."""

    def __init__(
        self,
        context: FlowContext,
        from_stage: FlowStage | str | None = None,
        checkpoint_path: str | None = None,
        budgets: Budgets | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        context.skip_calibrate = True
        context.auto_calibrate = False
        super().__init__(
            context,
            from_stage=from_stage,
            checkpoint_path=checkpoint_path,
            budgets=budgets,
            clock=clock,
        )

    def execute_stage(self, stage: FlowStage) -> StageResult:
        """Run Stages 1-3 normally and halt at the Stage 4 pre-PR boundary before any GitHub PR operation."""
        if stage == FlowStage.REVIEW:
            now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            if not self.context.execution_branch:
                err = "Cannot enter Review pre-PR boundary without an active execution_branch in FlowContext."
                self.context.log(err)
                return StageResult(FlowStage.REVIEW, StageStatus.FAILED, start_time=now, end_time=now, error=err)
            self.context.log("Stage 4 (REVIEW): E2E harness halting at pre-PR boundary before GitHub PR creation.")
            result = StageResult(
                stage=FlowStage.REVIEW,
                status=StageStatus.HALTED_FOR_HUMAN,
                start_time=now,
                end_time=now,
                payload={
                    "halted_for_human": True,
                    "stopped_before_pr": True,
                    "pr_created": False,
                    "execution_branch": self.context.execution_branch,
                },
            )
            self.context.stage_results[FlowStage.REVIEW.value] = result
            return result
        return super().execute_stage(stage)


def _matches_any(text: str, snippets: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(snippet in lowered for snippet in snippets)


def _is_model_output_payload(payload: Mapping[str, Any]) -> bool:
    return bool(
        payload.get("over_budget")
        or payload.get("touch_set_violation")
        or payload.get("empty_diff")
        or payload.get("failure_reason") in ("empty_diff", "touch_set_violation", "over_budget")
    )


def _classify_stage_results(stage_results: Mapping[str, StageResult | dict[str, Any]]) -> FailureCategory | None:
    for raw in stage_results.values():
        if isinstance(raw, StageResult):
            status, error_text, payload = raw.status.value, raw.error or "", raw.payload or {}
        elif isinstance(raw, Mapping):
            status = str(raw.get("status") or "")
            error_text = str(raw.get("error") or "")
            payload = raw.get("payload") if isinstance(raw.get("payload"), Mapping) else {}
        else:
            continue
        if status != StageStatus.FAILED.value and not error_text:
            continue
        if _is_model_output_payload(payload):
            return FailureCategory.MODEL_OUTPUT
        if _matches_any(error_text, _MACHINERY_ERROR_SNIPPETS):
            return FailureCategory.MACHINERY
        if _matches_any(error_text, _MODEL_OUTPUT_ERROR_SNIPPETS):
            return FailureCategory.MODEL_OUTPUT
        return FailureCategory.MACHINERY
    return None


def classify_attempt_failure(
    error: BaseException | None = None,
    stage_results: Mapping[str, StageResult | dict[str, Any]] | None = None,
) -> FailureCategory:
    """Classify an attempt failure into :attr:`FailureCategory.MODEL_OUTPUT` or :attr:`FailureCategory.MACHINERY`."""
    if isinstance(error, E2EModelOutputError):
        return FailureCategory.MODEL_OUTPUT
    if isinstance(
        error,
        (E2EMachineryError, LocalLLMConfigError, LedgerRewriteError, TypeError, AttributeError, KeyError, ValueError),
    ):
        return FailureCategory.MACHINERY
    if stage_results:
        from_stages = _classify_stage_results(stage_results)
        if from_stages is not None:
            return from_stages
    if error is not None:
        message = str(error)
        if _matches_any(message, _MACHINERY_ERROR_SNIPPETS):
            return FailureCategory.MACHINERY
        if _matches_any(message, _MODEL_OUTPUT_ERROR_SNIPPETS):
            return FailureCategory.MODEL_OUTPUT
    return FailureCategory.MACHINERY


def _validate_max_attempts(max_attempts: int) -> int:
    valid_int = not isinstance(max_attempts, bool) and isinstance(max_attempts, int)
    if not valid_int or not (1 <= max_attempts <= MAX_E2E_ATTEMPTS_CAP):
        raise E2EMachineryError(
            f"max_attempts must be an integer between 1 and {MAX_E2E_ATTEMPTS_CAP}, got {max_attempts!r}."
        )
    return max_attempts


def _extract_attempt_worktree_info(
    attempt_result: Mapping[str, Any] | None,
    default_worktree: str | None,
    default_worktree_branch: str | None,
) -> tuple[str | None, str | None, dict[str, Any]]:
    if not isinstance(attempt_result, Mapping):
        return default_worktree, default_worktree_branch, {}
    wt = attempt_result.get("disposable_worktree") or default_worktree
    wt_branch = attempt_result.get("worktree_branch") or default_worktree_branch
    stages = attempt_result.get("stage_results")
    stage_dict = dict(stages) if isinstance(stages, Mapping) else dict(attempt_result)
    return wt, wt_branch, stage_dict


def _check_stage_results_succeeded(stage_results: Mapping[str, Any]) -> None:
    for stage_name in (FlowStage.INTENT.value, FlowStage.PLAN.value, FlowStage.EXECUTE.value):
        if stage_name not in stage_results:
            continue
        raw = stage_results[stage_name]
        status = raw.status.value if isinstance(raw, StageResult) else str((raw or {}).get("status") or "")
        error = raw.error if isinstance(raw, StageResult) else str((raw or {}).get("error") or "")
        if status != StageStatus.SUCCESS.value:
            category = _classify_stage_results({stage_name: raw}) or FailureCategory.MACHINERY
            msg = error or f"Stage '{stage_name}' did not succeed (status={status!r})."
            if category == FailureCategory.MODEL_OUTPUT:
                raise E2EModelOutputError(msg)
            raise E2EMachineryError(msg)


def _execute_guarded_attempt(
    attempt: int,
    intent_prefix: str,
    attempt_fn: Callable[[int, str], Mapping[str, Any] | None],
    repo_dir: str,
    options: Mapping[str, Any],
) -> tuple[E2EAttemptOutcome, BaseException | None]:
    """Run one attempt with nested finally blocks guaranteeing zero-PR verification and unconditional teardown."""
    wt_path: str | None = options.get("disposable_worktree")
    wt_branch: str | None = options.get("worktree_branch")
    gh_runner: GhRunnerCallable | None = options.get("gh_runner")
    git_runner: GitRunnerCallable = options.get("git_runner") or run_git
    stage_results: dict[str, Any] = {}
    attempt_exc: BaseException | None = None
    zero_pr_res: ZeroPRCheckResult | None = None

    try:
        try:
            raw_outcome = attempt_fn(attempt, intent_prefix)
            wt_path, wt_branch, stage_results = _extract_attempt_worktree_info(raw_outcome, wt_path, wt_branch)
            _check_stage_results_succeeded(stage_results)
        except BaseException as exc:
            attempt_exc = exc
        finally:
            try:
                zero_pr_res = enforce_zero_pr_guard(
                    repo_dir, intent_prefix, worktree_branch=wt_branch, gh_runner=gh_runner
                )
            except BaseException as guard_exc:
                if attempt_exc is None:
                    attempt_exc = guard_exc
    finally:
        td_report = teardown_e2e_intent(
            repo_dir, intent_prefix, disposable_worktree=wt_path, worktree_branch=wt_branch, git_runner=git_runner
        )

    if attempt_exc is None:
        return (
            E2EAttemptOutcome(
                attempt=attempt,
                intent_prefix=intent_prefix,
                succeeded=True,
                stage_results=stage_results,
                teardown_report=td_report,
                zero_pr_result=zero_pr_res,
            ),
            None,
        )
    category = classify_attempt_failure(attempt_exc, stage_results)
    return (
        E2EAttemptOutcome(
            attempt=attempt,
            intent_prefix=intent_prefix,
            succeeded=False,
            stage_results=stage_results,
            teardown_report=td_report,
            zero_pr_result=zero_pr_res,
            failure_category=category,
            error_message=str(attempt_exc),
        ),
        attempt_exc,
    )


def run_e2e_with_retry(
    attempt_fn: Callable[[int, str], Mapping[str, Any] | None],
    *,
    repo_dir: str,
    base_slug: str = "e2e-flow",
    max_attempts: int = DEFAULT_E2E_MAX_ATTEMPTS,
    **kwargs: Any,
) -> E2ERunOutcome:
    """Execute ``attempt_fn`` with unconditional per-attempt teardown, zero-PR guard, and retry classification."""
    epoch_fn: Callable[[], int] | None = kwargs.pop("epoch_fn", None)
    extra_keys = set(kwargs) - {"disposable_worktree", "worktree_branch", "gh_runner", "git_runner"}
    if extra_keys:
        raise TypeError(f"run_e2e_with_retry() got unexpected keyword argument(s): {', '.join(sorted(extra_keys))}")

    validated_max = _validate_max_attempts(max_attempts)
    teardown_reports: list[TeardownReport] = []
    zero_pr_results: list[ZeroPRCheckResult] = []
    attempt_outcomes: list[E2EAttemptOutcome] = []
    base_epoch = int(time.time()) if epoch_fn is None else int(epoch_fn())
    last_error: BaseException | None = None

    for attempt in range(1, validated_max + 1):
        epoch = (base_epoch + attempt - 1) if epoch_fn is None else int(epoch_fn())
        attempt_slug = base_slug if attempt == 1 else f"{base_slug}-{attempt}"
        intent_prefix = build_e2e_intent_prefix(attempt_slug, epoch=epoch)
        outcome, attempt_exc = _execute_guarded_attempt(attempt, intent_prefix, attempt_fn, repo_dir, kwargs)
        if outcome.teardown_report is not None:
            teardown_reports.append(outcome.teardown_report)
        if outcome.zero_pr_result is not None:
            zero_pr_results.append(outcome.zero_pr_result)
        attempt_outcomes.append(outcome)

        if outcome.succeeded:
            return E2ERunOutcome(
                succeeded=True,
                attempts_run=attempt,
                intent_prefix=intent_prefix,
                stage_results=outcome.stage_results,
                teardown_reports=tuple(teardown_reports),
                zero_pr_results=tuple(zero_pr_results),
                attempts=tuple(attempt_outcomes),
            )

        last_error = attempt_exc
        if outcome.failure_category == FailureCategory.MACHINERY:
            if isinstance(attempt_exc, E2EMachineryError):
                raise attempt_exc
            raise E2EMachineryError(
                f"Non-retryable E2E machinery failure on attempt {attempt}: {attempt_exc}"
            ) from attempt_exc

    msg = f"E2E model-output failure persisted after {validated_max} attempt(s): {last_error}"
    if isinstance(last_error, E2EModelOutputError):
        raise E2EModelOutputError(msg) from last_error
    raise E2EModelOutputError(msg)


def _synthesised_host_config_from_env(env: Mapping[str, str]) -> dict[str, Any]:
    """Build a minimal host config dictionary from an explicit ``env`` mapping without ``os.environ`` fallback."""
    local_llm.check_text_settings(env)
    base_url = env.get(ENV_LOCAL_BASE_URL, "").strip()
    if not base_url:
        raise LocalLLMConfigError(
            f"{ENV_LOCAL_MODE} is enabled but {ENV_LOCAL_BASE_URL} is not set in the provided environment."
        )
    model_ids = [item.strip() for item in env.get(ENV_LOCAL_MODELS, "").split(",") if item.strip()]
    if not model_ids:
        raise LocalLLMConfigError(f"{ENV_LOCAL_BASE_URL} is set but no model ids were declared in {ENV_LOCAL_MODELS}.")
    provider = env.get(ENV_LOCAL_PROVIDER, "").strip() or env.get(ENV_AGENT_PROVIDER, "").strip() or "local"
    return {
        "providers": {
            provider: {
                "baseUrl": base_url,
                "api": "openai-completions",
                "apiKey": "holon-local",
                "models": [{"id": mid} for mid in model_ids],
            }
        }
    }


def _allow_list_from_env(env: Mapping[str, str]) -> set[str]:
    """Normalise ``HOLON_HOST_LOCAL_HOSTS`` from ``env`` using the same scheme/path stripping as ``local_llm``."""
    raw = env.get(ENV_HOST_LOCAL_HOSTS, "")
    if not local_llm.is_utf8_text(raw):
        raise LocalLLMConfigError(
            f"{ENV_HOST_LOCAL_HOSTS} is not valid UTF-8 text (a lone surrogate or a non-UTF-8 byte); value withheld."
        )
    allow: set[str] = set()
    for entry in raw.split(","):
        cleaned = re.sub(r"^https?://", "", entry.strip().lower()).split("/")[0].strip()
        if cleaned:
            allow.add(cleaned)
    return allow


def validate_e2e_local_llm_env(
    env: Mapping[str, str] | None = None,
) -> tuple[bool, str, list[ModelEnvelope]]:
    """Verify whether host-local LLM mode is requested and validly configured for an opt-in E2E run."""
    effective_env = os.environ if env is None else env
    raw_mode = effective_env.get(ENV_LOCAL_MODE, "").strip().lower()
    if raw_mode not in ("1", "true", "yes", "on"):
        return (
            False,
            (
                f"{ENV_LOCAL_MODE} is not enabled. Set {ENV_LOCAL_MODE}=1 (plus {ENV_LOCAL_BASE_URL} and "
                f"{ENV_LOCAL_MODELS}, or ~/.pi/agent/models.json) and invoke 'uv run task test-e2e' "
                "(see docs/flow_e2e.md)."
            ),
            [],
        )
    host_cfg = local_llm.host_models_json() if env is None else _synthesised_host_config_from_env(effective_env)
    allow = local_llm.host_local_allow_list() if env is None else _allow_list_from_env(effective_env)
    _config, envelopes = local_llm.build_envelope_config(host_cfg, allow, env=effective_env)
    if not envelopes:
        raise LocalLLMConfigError(
            f"{ENV_LOCAL_MODE}=1 is enabled but no model entries were resolved from "
            f"host models.json or {ENV_LOCAL_MODELS}."
        )
    summary = "; ".join(f"{env_item.envelope_line()} {env_item.reasoning_line()}" for env_item in envelopes)
    return True, summary, envelopes


def run_e2e_flow_test(
    repo_dir: str,
    *,
    base_slug: str = "e2e-flow-verify",
    max_attempts: int = DEFAULT_E2E_MAX_ATTEMPTS,
    dispatcher: Callable[..., int] | None = None,
    **kwargs: Any,
) -> E2ERunOutcome:
    """Run a full disposable ``holon flow`` E2E test through Stages 1-3, halting at Stage 4 pre-PR boundary."""
    cli_args: Sequence[str] = kwargs.pop("cli_args", ())
    options: Mapping[str, Any] | None = kwargs.pop("options", None)
    agent: str | None = kwargs.pop("agent", None)
    model: str | None = kwargs.pop("model", None)
    assertion_fn: Callable[[FlowContext, dict[str, StageResult]], None] | None = kwargs.pop("assertion_fn", None)
    gh_runner: GhRunnerCallable | None = kwargs.pop("gh_runner", None)
    git_runner: GitRunnerCallable = kwargs.pop("git_runner", run_git)
    if kwargs:
        raise TypeError(f"run_e2e_flow_test() got unexpected keyword argument(s): {', '.join(sorted(kwargs))}")

    assert_no_skip_review_flag(cli_args=cli_args, options=options)
    resolved_agent = agent or os.getenv("HOLON_AGENT", "").strip() or "pi-agent"
    resolved_model = model or os.getenv("HOLON_MODEL", "").strip() or "local-model"

    def _run_single_attempt(_attempt: int, intent_prefix: str) -> Mapping[str, Any]:
        slug = _intent_slug_from_prefix(intent_prefix)
        worktree_branch = f"flow-{slug}"
        disposable_wt = os.path.join(os.path.dirname(os.path.abspath(repo_dir)), worktree_branch)
        if not os.path.exists(disposable_wt):
            add_res = _invoke_git(
                git_runner,
                ["worktree", "add", "--no-track", "-b", worktree_branch, disposable_wt, "main"],
                repo_dir,
            )
            if add_res.returncode != 0:
                raise E2EMachineryError(
                    f"Failed to provision disposable E2E worktree {disposable_wt!r}: "
                    f"{(add_res.stderr or add_res.stdout).strip()}"
                )

        intent_data: dict[str, Any] = {
            "slug": slug,
            "branch": intent_prefix,
            "target_branch": "main",
            "description": "Disposable E2E flow verification intent.",
            "goal": "Add a deterministic E2E verification helper and unit test.",
            "labels": ["e2e", "flow"],
        }
        ctx = FlowContext(
            target_worktree=disposable_wt,
            intent_data=intent_data,
            agent=resolved_agent,
            model=resolved_model,
            dry_run=False,
            token_reduce=False,
            skip_calibrate=True,
            auto_calibrate=False,
            dispatcher=dispatcher,
        )
        checkpoint_file = os.path.join(disposable_wt, ".holon", "e2e_checkpoint.json")
        engine = E2EPipelineEngine(ctx, checkpoint_path=checkpoint_file)
        results = engine.run()
        _check_stage_results_succeeded(results)
        review_res = results.get(FlowStage.REVIEW.value)
        if review_res is None or not review_res.payload.get("stopped_before_pr"):
            raise E2EMachineryError("E2EPipelineEngine did not stop at the Stage 4 pre-PR boundary.")
        if assertion_fn is not None:
            assertion_fn(ctx, results)
        return {
            "disposable_worktree": disposable_wt,
            "worktree_branch": worktree_branch,
            "stage_results": results,
        }

    return run_e2e_with_retry(
        _run_single_attempt,
        repo_dir=repo_dir,
        base_slug=base_slug,
        max_attempts=max_attempts,
        gh_runner=gh_runner,
        git_runner=git_runner,
    )
