# Execution Record: E-1792992902-claude-agent-claude-opus-5-5

## Summary

Implementation of plan `P-1792992691-claude-agent-claude-opus-5-5` for intent
`I-1792992678-entropy-budget-handoff-and-no-human-halt-in-execute`: one shared entropy budget, no fabricated child work,
and no Stage 3 human halt. All seven plan steps were implemented on this execution branch: a single
`DEFAULT_ENTROPY_BUDGET` and `resolve_entropy_budget()` in `budgets.py`; the planner shows and persists the budget on
the `plans.jsonl` row; the executor returns `execute`, `decomposed` (only from the plan's own explicit sub-intent list)
or `over_budget`, never fabricates child work and never writes `intents.jsonl`; the flow re-plans over-budget plans at
most `MAX_REPLAN_ATTEMPTS` times, drives sub-intent lists sequentially through full child pipelines, and keeps the Stage
4 consensus halt as the only halt. Lint, the full test suite and the markdown check pass.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 1.0   |
| entropy_actual        | 5.3   |
| impact_actual         | 85.0  |
| cost_actual           | 15.0  |
| learning_value_actual | 4.5   |

## Steps Executed

1. **Baseline and Step 1 (shared budget).** `uv` was missing and was installed with `pip install --user uv`. Baseline on
   the plan branch: `2167 passed, 14 deselected`. Added `DEFAULT_ENTROPY_BUDGET = 15.0`, `MAX_REPLAN_ATTEMPTS = 2`,
   `MAX_SUB_INTENT_DEPTH = 2`, `ENV_REPLAN_FEEDBACK` and `resolve_entropy_budget(plan_row, intent_row)` to
   `sandbox_executor/budgets.py` (plan row, then intent row, then default; invalid values fall through).
2. **Step 2 (planner).** `entrypoint/planner.py` resolves the budget once, uses it for `{budget_from_intent}` and
   persists it as `entropy_budget` on the `plans.jsonl` row. Re-plan feedback arrives through the forwarded
   `HOLON_REPLAN_FEEDBACK` variable (the planner container receives no intent file from the flow, and every `HOLON_*`
   host variable is forwarded), is appended to the prompt as a "Re-plan request" paragraph and to the temporary intent
   file only, never to a ledger. The planner template gained an "Entropy Budget and Decomposition Contract" note, and
   `docs/ledger_schema.md` documents the new field and the executor's `decomposed` and `over_budget` rows.
3. **Step 3 (executor).** `should_decompose(plan_data, plan_content, intent_data=None)` now returns a frozen
   `DecompositionDecision` (`execute`, `decomposed`, `over_budget`) built on `parse_sub_intent_list()`, which only reads
   a level-2 or level-3 heading normalising to `sub intent list` or `sub intents`. The hard-coded fallback list was
   deleted. `main()` no longer appends to `intents.jsonl`; both non-execute outcomes record a single summary row, stage
   only `executions.jsonl`, skip the agent, and share the existing commit, pre-push sync and push tail through one
   `skip_agent` flag. The fallback plan row no longer carries a hard-coded budget.
4. **Step 4 (flow).** The Stage 3 `HALTED_FOR_HUMAN` branch in `run_execute_stage()` was deleted. A pre-dispatch check
   (`_plan_budget_decision`) reads the plan row and plan file from the plan branch and calls the executor's own
   `should_decompose()`; an `over_budget` decision fails the stage with `payload["over_budget"]` before any container
   starts, and an `over_budget` row from the container produces the same result. A `decomposed` row runs each item
   through a fresh `PipelineEngine` in a plain `for` loop (`_run_decomposed_plan`), with `target_branch` and
   `parent_intent_id` set to the parent intent branch, the parent budget inherited and `sub_intent_depth` bounded by
   `MAX_SUB_INTENT_DEPTH`; child logs are copied into the parent log with a `[sub-intent <slug>]` prefix and the first
   failing child fails the stage. `PipelineEngine.run()` gained `_replan_after_over_budget` (bounded by
   `MAX_REPLAN_ATTEMPTS`, checkpointed `FlowContext.replan_attempts` defaulting to 0, transient `replan_feedback` popped
   before each checkpoint) and skips the parent's Stage 4 and Stage 5 with `payload["reason"] = "decomposed"`. A
   decomposed Execute stage is exempt from the post-hoc stage-budget downgrade, because each child stage has its own
   budget. The Stage 4 `HALTED_FOR_HUMAN` handling and its Stage 5 calibration are unchanged; only a comment was added.
   Deviation from the plan text: the "Pipeline halted" log names `result.stage`, so a failed re-plan reports Stage 2.
5. **Step 5 (tests).** `test_budgets.py`: resolver order, invalid values and the single shared constant.
   `test_planner.py`: the row carries the intent budget (16.0) shown in the prompt, the default applies when the intent
   has none, and re-plan feedback reaches the prompt but never a ledger row. `test_executor.py`: the five old
   `should_decompose` tests (which pinned the fabricated list) were replaced by pytest tests for the 12.6 / 16
   regression, row-then-intent fallback, `over_budget` without a list, bulleted and numbered lists under the new and the
   legacy heading (built from fragments), slug de-duplication and ignored per-step bullets; two `main()` tests assert
   zero new `intents.jsonl` rows for both outcomes; a source hygiene test walks `src/` for the placeholder phrases
   (built from fragments). `test_flow.py`: a new `BudgetRoleAgents` fake (judging plans with the real
   `should_decompose`) drives a parametrised never-halts test over normal, over-budget and decomposed plans (both the
   pre-dispatch and the container-row paths), the regression case, the persisted row budget, exactly
   `MAX_REPLAN_ATTEMPTS` re-plans then the clear failure with no extra intent rows, recovery after one re-plan, two
   children run in order with no overlapping dispatch, the first failing child stopping the loop, the depth bound, the
   checkpoint round trip, and a source check that only `run_review_stage` constructs a halt. Existing Stage 4 halt and
   Stage 5 calibration tests are unmodified and pass.
6. **Step 6 (docs).** `docs/budgets.md` gained an "Entropy budget" section (default, resolver, persistence, re-plan and
   depth bounds); `docs/executor/execution_architecture_specification.md` describes the three outcomes and drops the old
   `5.0` default and `intents.jsonl` writes; `docs/planner/planning_architecture_specification.md` and
   `docs/planning_and_evaluation.md` describe the shown-and-persisted budget and how to write a sub-intent list;
   `docs/git_flow.md` states that Stage 3 never halts, that children run sequentially against their immediate parent
   branch, and that the Stage 4 consensus stop is the only halt; `docs/safety.md` budget enforcement now re-plans and
   fails instead of escalating to a human. Other docs flagged by grep (`faq.md`, `agents.md`, `core_concepts.md`,
   `metrics.md`) make no claim that Stage 3 halts for a human and were left unchanged.
7. **Step 7 (validation).** Lint, tests and markdown checks pass (see below). `apps/sandbox-executor/src/` contains no
   placeholder goal phrase and no entropy-budget literal other than `DEFAULT_ENTROPY_BUDGET = 15.0`; the only
   `status=StageStatus.HALTED_FOR_HUMAN` constructor left in `flow.py` is in `run_review_stage()`.

Residual risk: child pipelines are exercised only through the mocked dispatcher and temporary git fixtures; the first
real multi-child run will happen in production flow. Each child still stops at its own Stage 4 consensus halt.

## Tests Run

| command                                                                        | result                                          |
| ------------------------------------------------------------------------------ | ----------------------------------------------- |
| `uv run task test` (baseline, plan branch)                                     | `2167 passed, 14 deselected`                    |
| `uv run pytest apps/sandbox-executor/tests/test_executor.py`                   | `116 passed`                                    |
| `uv run pytest apps/sandbox-executor/tests/test_planner.py ...test_budgets.py` | `61 passed, 22 subtests passed`                 |
| `uv run pytest apps/sandbox-executor/tests/test_flow.py`                       | `224 passed`                                    |
| `uv run task test`                                                             | `2218 passed, 14 deselected`                    |
| `uv run task lint`                                                             | lock check, ruff check and ruff format all pass |
| `uv run task lint-docs`                                                        | all markdown files use Prettier code style      |
| grep for placeholder phrases and entropy literals in `src/`                    | none, apart from the shared constant            |
