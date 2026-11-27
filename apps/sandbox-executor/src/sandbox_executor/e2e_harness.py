from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sandbox_executor.budgets import Budgets
from sandbox_executor.e2e_guards import (
    assert_no_skip_review_flag,
    enforce_zero_pr_guard,
    validate_e2e_local_llm_env,
)
from sandbox_executor.e2e_teardown import (
    _intent_slug_from_prefix,
    _invoke_git,
    assert_refs_within_intent_prefix,
    build_e2e_intent_prefix,
    is_ref_within_intent_prefix,
    normalise_ref_name,
    teardown_e2e_intent,
    validate_e2e_intent_prefix,
)
from sandbox_executor.e2e_types import (
    DEFAULT_E2E_MAX_ATTEMPTS,
    MAX_E2E_ATTEMPTS_CAP,
    E2EAttemptOutcome,
    E2EError,
    E2EMachineryError,
    E2EModelOutputError,
    E2ERunOutcome,
    FailureCategory,
    GhRunnerCallable,
    PrefixScopingError,
    TeardownReport,
    ZeroPRCheckResult,
    ZeroPRGuardViolationError,
)
from sandbox_executor.flow.context import FlowContext, FlowStage, StageResult, StageStatus
from sandbox_executor.flow.pipeline import PipelineEngine
from sandbox_executor.git import GitRunnerCallable, run_git
from sandbox_executor.intent_assumption_rules import default_assumptions_fields
from sandbox_executor.ledger import LedgerRewriteError
from sandbox_executor.local_llm import LocalLLMConfigError

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

_MODEL_OUTPUT_FAILURE_REASONS = frozenset({"empty_diff", "touch_set_violation", "over_budget"})
_NON_RETRYABLE_MACHINERY_ERRORS = (
    E2EMachineryError,
    LocalLLMConfigError,
    LedgerRewriteError,
    TypeError,
    AttributeError,
    KeyError,
    ValueError,
)
_REQUIRED_SUCCESS_STAGES = (FlowStage.INTENT.value, FlowStage.PLAN.value, FlowStage.EXECUTE.value)
_RETRY_ALLOWED_OPTION_KEYS = frozenset(
    {"disposable_worktree", "worktree_branch", "worktree_resolver", "gh_runner", "git_runner"}
)
_DEFAULT_E2E_AGENT = "pi-agent"
_DEFAULT_E2E_MODEL = "local-model"


class E2EPipelineEngine(PipelineEngine):
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
        super().__init__(context, from_stage=from_stage, checkpoint_path=checkpoint_path, budgets=budgets, clock=clock)

    def execute_stage(self, stage: FlowStage) -> StageResult:
        if stage == FlowStage.REVIEW:
            now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            if not self.context.execution_branch:
                error_message = "Cannot enter Review pre-PR boundary without an active execution_branch in FlowContext."
                self.context.log(error_message)
                return StageResult(
                    FlowStage.REVIEW, StageStatus.FAILED, start_time=now, end_time=now, error=error_message
                )
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
        or payload.get("failure_reason") in _MODEL_OUTPUT_FAILURE_REASONS
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
    if isinstance(error, E2EModelOutputError):
        return FailureCategory.MODEL_OUTPUT
    if isinstance(error, _NON_RETRYABLE_MACHINERY_ERRORS):
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
    disposable_worktree = attempt_result.get("disposable_worktree") or default_worktree
    worktree_branch = attempt_result.get("worktree_branch") or default_worktree_branch
    stages = attempt_result.get("stage_results")
    stage_dict = dict(stages) if isinstance(stages, Mapping) else dict(attempt_result)
    return disposable_worktree, worktree_branch, stage_dict


def _check_stage_results_succeeded(stage_results: Mapping[str, Any]) -> None:
    for stage_name in _REQUIRED_SUCCESS_STAGES:
        if stage_name not in stage_results:
            continue
        raw = stage_results[stage_name]
        status = raw.status.value if isinstance(raw, StageResult) else str((raw or {}).get("status") or "")
        error = raw.error if isinstance(raw, StageResult) else str((raw or {}).get("error") or "")
        if status != StageStatus.SUCCESS.value:
            category = _classify_stage_results({stage_name: raw}) or FailureCategory.MACHINERY
            error_message = error or f"Stage '{stage_name}' did not succeed (status={status!r})."
            if category == FailureCategory.MODEL_OUTPUT:
                raise E2EModelOutputError(error_message)
            raise E2EMachineryError(error_message)


def _should_override_attempt_error(attempt_exc: BaseException | None, guard_exc: BaseException) -> bool:
    if attempt_exc is None or isinstance(guard_exc, ZeroPRGuardViolationError):
        return True
    return isinstance(guard_exc, E2EMachineryError) and not isinstance(attempt_exc, E2EMachineryError)


def _execute_guarded_attempt(
    attempt: int,
    intent_prefix: str,
    attempt_fn: Callable[[int, str], Mapping[str, Any] | None],
    repo_dir: str,
    options: Mapping[str, Any],
) -> tuple[E2EAttemptOutcome, BaseException | None]:
    disposable_worktree: str | None = options.get("disposable_worktree")
    worktree_branch: str | None = options.get("worktree_branch")
    worktree_resolver: Callable[[int, str], tuple[str | None, str | None]] | None = options.get("worktree_resolver")
    if worktree_resolver is not None:
        resolved_worktree, resolved_branch = worktree_resolver(attempt, intent_prefix)
        disposable_worktree = resolved_worktree or disposable_worktree
        worktree_branch = resolved_branch or worktree_branch
    gh_runner: GhRunnerCallable | None = options.get("gh_runner")
    git_runner: GitRunnerCallable = options.get("git_runner") or run_git
    stage_results: dict[str, Any] = {}
    attempt_exc: BaseException | None = None
    zero_pr_result: ZeroPRCheckResult | None = None

    try:
        try:
            raw_outcome = attempt_fn(attempt, intent_prefix)
            disposable_worktree, worktree_branch, stage_results = _extract_attempt_worktree_info(
                raw_outcome, disposable_worktree, worktree_branch
            )
            _check_stage_results_succeeded(stage_results)
        except BaseException as exc:
            attempt_exc = exc
        finally:
            try:
                zero_pr_result = enforce_zero_pr_guard(
                    repo_dir, intent_prefix, worktree_branch=worktree_branch, gh_runner=gh_runner
                )
            except BaseException as guard_exc:
                if _should_override_attempt_error(attempt_exc, guard_exc):
                    attempt_exc = guard_exc
    finally:
        teardown_report = teardown_e2e_intent(
            repo_dir,
            intent_prefix,
            disposable_worktree=disposable_worktree,
            worktree_branch=worktree_branch,
            git_runner=git_runner,
        )

    if attempt_exc is None:
        return (
            E2EAttemptOutcome(
                attempt=attempt,
                intent_prefix=intent_prefix,
                succeeded=True,
                stage_results=stage_results,
                teardown_report=teardown_report,
                zero_pr_result=zero_pr_result,
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
            teardown_report=teardown_report,
            zero_pr_result=zero_pr_result,
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
    epoch_fn: Callable[[], int] | None = kwargs.pop("epoch_fn", None)
    extra_keys = set(kwargs) - _RETRY_ALLOWED_OPTION_KEYS
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
        if attempt_exc is not None and not isinstance(attempt_exc, Exception):
            raise attempt_exc
        if outcome.failure_category == FailureCategory.MACHINERY:
            if isinstance(attempt_exc, E2EMachineryError):
                raise attempt_exc
            raise E2EMachineryError(
                f"Non-retryable E2E machinery failure on attempt {attempt}: {attempt_exc}"
            ) from attempt_exc

    error_message = f"E2E model-output failure persisted after {validated_max} attempt(s): {last_error}"
    if isinstance(last_error, E2EModelOutputError):
        raise E2EModelOutputError(error_message) from last_error
    raise E2EModelOutputError(error_message)


def run_e2e_flow_test(
    repo_dir: str,
    *,
    base_slug: str = "e2e-flow-verify",
    max_attempts: int = DEFAULT_E2E_MAX_ATTEMPTS,
    dispatcher: Callable[..., int] | None = None,
    **kwargs: Any,
) -> E2ERunOutcome:
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
    resolved_agent = agent or os.getenv("HOLON_AGENT", "").strip() or _DEFAULT_E2E_AGENT
    resolved_model = model or os.getenv("HOLON_MODEL", "").strip() or _DEFAULT_E2E_MODEL

    def _resolve_attempt_worktree(_attempt: int, intent_prefix: str) -> tuple[str, str]:
        slug = _intent_slug_from_prefix(intent_prefix)
        worktree_branch = f"flow-{slug}"
        disposable_worktree = os.path.join(os.path.dirname(os.path.abspath(repo_dir)), worktree_branch)
        return disposable_worktree, worktree_branch

    def _run_single_attempt(_attempt: int, intent_prefix: str) -> Mapping[str, Any]:
        slug = _intent_slug_from_prefix(intent_prefix)
        disposable_worktree, worktree_branch = _resolve_attempt_worktree(_attempt, intent_prefix)
        if not os.path.exists(disposable_worktree):
            add_result = _invoke_git(
                git_runner,
                ["worktree", "add", "--no-track", "-b", worktree_branch, disposable_worktree, "main"],
                repo_dir,
            )
            if add_result.returncode != 0:
                raise E2EMachineryError(
                    f"Failed to provision disposable E2E worktree {disposable_worktree!r}: "
                    f"{(add_result.stderr or add_result.stdout).strip()}"
                )

        intent_data: dict[str, Any] = {
            "slug": slug,
            "branch": intent_prefix,
            "target_branch": "main",
            "description": "Disposable E2E flow verification intent.",
            "goal": "Add a deterministic E2E verification helper and unit test.",
            "labels": ["e2e", "flow"],
            **default_assumptions_fields(
                "E2E harness executes against a disposable worktree.",
                "Verified by Stage 1-4 E2E flow checks.",
            ),
        }
        flow_context = FlowContext(
            target_worktree=disposable_worktree,
            intent_data=intent_data,
            agent=resolved_agent,
            model=resolved_model,
            dry_run=False,
            token_reduce=False,
            skip_calibrate=True,
            auto_calibrate=False,
            dispatcher=dispatcher,
        )
        checkpoint_file = os.path.join(disposable_worktree, ".holon", "e2e_checkpoint.json")
        engine = E2EPipelineEngine(flow_context, checkpoint_path=checkpoint_file)
        results = engine.run()
        _check_stage_results_succeeded(results)
        review_result = results.get(FlowStage.REVIEW.value)
        if review_result is None or not review_result.payload.get("stopped_before_pr"):
            raise E2EMachineryError("E2EPipelineEngine did not stop at the Stage 4 pre-PR boundary.")
        if assertion_fn is not None:
            assertion_fn(flow_context, results)
        return {
            "disposable_worktree": disposable_worktree,
            "worktree_branch": worktree_branch,
            "stage_results": results,
        }

    return run_e2e_with_retry(
        _run_single_attempt,
        repo_dir=repo_dir,
        base_slug=base_slug,
        max_attempts=max_attempts,
        worktree_resolver=_resolve_attempt_worktree,
        gh_runner=gh_runner,
        git_runner=git_runner,
    )
