# Execution Record: E-1793856533-claude-agent-claude-opus-5-5

## Summary

Implementing plan `P-1793856357-claude-agent-claude-opus-5-5` for intent
`I-1793856341-engine-owned-review-loop-single-shot-container`: the flow engine owns the Stage 4 review loop and
dispatches the review container for exactly one ensemble pass per engine iteration, passing its iteration number only as
a label.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Confirm label destinations and checkpoint timing (read-only)

- (a) The host consumes only `reviewers` and the verdict from the container result file
  (`_dispatch_review_ensemble` in `flow.py` calls `deserialize_review_outcome` and `_attach_transported_detail`). The
  container runs with `--skip-post`, so the posted body is built on the host from the host loop's own `iteration`
  (`consensus_data["iterations"]` in `run_review_loop`). The container's own ledger rows use its own counter, so it
  must receive the label too.
- (b) `PipelineEngine.run` calls `self.save_checkpoint()` straight after `after_stage` for every stage result,
  including a FAILED Stage 4, before deciding whether to halt. A count set on the context at the end of
  `run_review_stage` is therefore persisted. One count update after the loop is enough.
- `entrypoint/role_dispatcher.sh` forwards `review-loop` arguments verbatim (`"$@"`), so the new `--iteration` flag
  needs no out-of-scope change.
- Extra finding: `PipelineEngine.stage_budget_seconds` derives the Stage 4 ceiling from `consensus["iterations"]`.
  With a label of N and one pass, that would grant N iteration budgets, so the loop now also reports `passes`
  and the ceiling prefers it.

### Step 2: Add a labelling start (`first_iteration`) to `run_review_loop`

- `run_review_loop` takes keyword-only `first_iteration: int = 1` (clamped to at least 1). The counter starts at
  `first_label - 1` and runs while it is below `last_label = first_label + budget - 1`, so every label use inside the
  loop (ledger `review_iteration`, finding IDs, trailers, `consensus["iterations"]`) carries the label unchanged.
- The budget-exhausted log compares against `last_label`; the dry-run `not_measured` check uses `passes`.
- The loop result's `consensus` gains `passes` (passes actually run); `PipelineEngine.stage_budget_seconds` prefers it
  over `iterations`, so a single labelled pass keeps a one-pass ceiling.
- `uv` was not on `PATH` in this sandbox; installed it with `pip install --user uv` (environment only, no project
  dependency change). Existing flow and review-loop tests: 389 passed, unchanged.

### Step 3: Single-shot dispatch and the container's `--iteration` label flag

- `_dispatch_review_ensemble` now sends `--max-iterations=1` (named constant `_ENGINE_PASSES_PER_DISPATCH`) and a
  separate `--iteration={iteration}` label.
- `entrypoint/review_loop.py` gains `--iteration` (int, default 1), rejected through `parser.error` when below 1, and
  passes it to `run_review_loop` as `first_iteration`. `--max-iterations` keeps its standalone meaning and default 25.
- `role_dispatcher.sh` forwards arguments verbatim, so no out-of-scope change was needed. 389 affected tests pass.

### Step 4: Checkpoint the engine's review count and resume from it

- `FlowContext.review_iterations_done: int = 0`, serialised in `to_dict` and read in `from_dict` with
  `int(data.get(...) or 0)`: old checkpoints load as 0, no schema bump. Not copied by `_child_context`.
- New helper `_review_window(context)` returns `(done + 1, max_review_iterations - done)`. `run_review_stage` fails
  through `_stage_failure` without dispatching when the remaining budget is 0 or less; otherwise it runs
  `run_review_loop(max_iterations=remaining, first_iteration=first)` and records
  `max(done, total_iterations)` back on the context, which the engine's post-stage checkpoint save persists.
- The stage payload's `consensus["max_review_iterations"]` is overridden with the configured engine budget, so it
  does not shrink to the remaining passes on resume.
- Declared test change: `test_child_context_inherits_or_resets_every_field` classifies the new field (child resets to
  0). 389 affected tests pass.

### Step 5: Keep the standalone `review` command's loop untouched

- No edit to `cli/commands/review.py`: `_build_container_review_args` still forwards the user's `--max-iterations`
  and adds no `--iteration` (the container default of 1 applies). `run_review_loop_cli` does not pass
  `first_iteration`. The help text already reads "Maximum number of review loop iterations", so it was left as is.

### Step 6: Add focused pytest coverage

- `tests/test_flow.py`, new `TestEngineOwnedReviewLoop`: parametrised single-shot dispatch (iterations 1, 4, 25 each
  yield exactly `--max-iterations=1` and `--iteration=N`); checkpoint round-trip resume (`done=3`, budget 5 gives
  `first_iteration=4`, `max_iterations=2`, saved count 4, payload budget stays 5); exhausted budget (`done=5`) fails
  without calling the loop; an old checkpoint without the key loads as 0; the Stage 4 ceiling counts passes, not the
  label.
- `tests/test_review_loop.py`: host container dispatch with `first_iteration=4` writes ledger rows with
  `review_iteration` 4 and posts a consensus rendering `Iteration 4`; the entrypoint passes `--iteration=7` as
  `first_iteration` and defaults to 1 and 25; `--iteration=0` is rejected; the standalone loop still labels from 1;
  `_build_container_review_args` forwards `--max-iterations=5` and no `--iteration`.
- Against the pre-change source (`git checkout <Step 1 commit> -- apps/sandbox-executor/src`), 12 of the 14 new or
  changed tests failed; the 2 that passed are the standalone regression pins, as intended.
- Lint follow-up: `run_review_stage` hit `PLR0911`; rather than add a new `noqa`, the budget check and count update
  moved into `_run_engine_review_loop`, which returns a failed loop result with an `error` that the existing
  "Autonomous review halted" path turns into a FAILED stage.
