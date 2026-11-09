# Execution Record: E-1794225351-0062997c-claude-agent-claude-opus-5-5

## Summary

Execution of plan `P-1794225025-924685ae-claude-agent-claude-opus-5-5` for intent
`I-1794224634-plan-owned-decomposition-with-plan-branch-children--1-record-the-decomposition-decision-on-the-plan-ro`:
record the decomposition decision on the plan row and make Stage 3 execute only that recorded decision.

In progress.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Move the decomposition rule into `sandbox_executor/decomposition_rule.py`

- Moved `DECISION_*`, the sub-intent list regexes and constants, `DecompositionDecision`, `is_placeholder_item`,
  `parse_sub_intent_list` and `should_decompose` out of `entrypoint/executor.py` into the new neutral module, which
  imports only `sandbox_executor.budgets`. `executor.py` re-exports the public names (`X as X` imports, like the
  redaction re-exports); `OVER_BUDGET_EXIT_CODE` and `_safe_float` stay in the executor. A private `_as_float` mirrors
  `_safe_float` in the new module.
- Deviation: `tests/test_complexity_gate.py` freezes the `(path, rule codes)` multiset of complexity `# noqa`
  annotations and explicitly forbids relocating one ("Refactor the function instead"), so moving `parse_sub_intent_list`
  with its `# noqa: C901, PLR0912, PLR0915` failed the gate. The function was refactored instead into a `_SubIntentScan`
  dataclass (one method per concern: fences, headings, item collection) plus `_sub_intent_dicts`, with no suppression at
  all. The line-level logic and comments are unchanged; the full decomposition regression corpus in
  `tests/test_executor.py` passes without any edit (`293 passed`).
- Added `InvalidPlanDecisionError`, `DecompositionDecision.to_plan_fields()` and `DecompositionDecision.from_plan_row()`
  with a strict shape check of recorded `sub_intents`; `tests/test_decomposition_rule.py` covers the round trip, the
  older-row `None`, corrupt records, the entrypoint-free imports and the re-export identity.

### Step 2: The planner records the decision on the plan row

- `entrypoint/planner.py`: `converge_prettier` now runs before the ledger append (it only touches the plan markdown),
  and a new `_plan_decision_fields(plan_entry, plan_md_path, intent_data)` helper reads the formatted plan back and
  returns `should_decompose(...).to_plan_fields()`, merged into the row before the single `atomic_ledger_append`.
  `main()` keeps its existing suppressions and gains no branch; over-budget plans are still appended (as `over_budget`).
- `tests/test_planner.py`: four tests (in-budget `execute` without `sub_intents`; listed plan `decomposed` with the list
  equal to `parse_sub_intent_list` of the committed file; a prettier re-wrap is reflected in the recorded description;
  over budget recorded). The harness gained an optional `converge_side_effect`. `test_planner_integration.py` needed no
  change.

### Step 3: `flow/stages/plan_decision.py` reads the recorded decision and back-fills older rows once

- New module (`177` lines before final formatting): `_plan_budget_decision` reads the newest matching plan row (as
  before) and returns `DecompositionDecision.from_plan_row(row)` when the row records a decision; an
  `InvalidPlanDecisionError` becomes `PlanDecisionError` (Stage 3 fails before dispatch). An older-image row is decided
  with `should_decompose` and `_backfill_plan_decision` appends one superseding row (planner row plus
  `to_plan_fields()`, `ledger_revision + 1`, fresh `created_at`, `decision_recorded_by: "flow"`) through
  `_append_superseding_plan_row`: a temporary detached worktree on `refs/remotes/origin/<plan_branch>`,
  `atomic_ledger_append`, `validate_ledger_file_unique_ids`, commit, push to the plan branch only, then
  `_sync_local_execution_refs`. A failed write is logged, a `LedgerRewriteError` propagates.
- `_over_budget_failure` moved verbatim (same AST); `_decomposition_record` returns the unchanged
  `{sub_intents, entropy_budget, plan_branch}` contract of `_run_decomposed_plan`.
- `stages/plan.py` lost the two functions and the entrypoint import (it no longer imports
  `sandbox_executor.entrypoint.executor`); `_format_entropy` stays for `pipeline.py`. `flow/__init__.py` and
  `stages/execute.py` import the moved names from `plan_decision`. Flow tests that patched helpers at `stages.plan` are
  re-pointed in Step 5.

### Step 4: The executor container reads the decision from the plan row

- The `plans.jsonl` lookup keeps the newest matching row (the `break` is gone), so a back-filled row wins.
- New `_recorded_decision(plan_data, plan_content, intent_data)` helper: `from_plan_row`, falling back to
  `should_decompose` only for a row without the field; an `InvalidPlanDecisionError` prints a clear error and exits 1
  before any row is written. `main()` gains no branch.
- The `decomposed` summary writes `plan_decision` (`plan_id`, `plan_branch`, `decision`, `sub_intent_count`) and no
  `sub_intents`; summary, action and commit texts now read "the plan records N sub-intent(s)". The `over_budget` row and
  `OVER_BUDGET_EXIT_CODE` are unchanged. `grep sub_intents entrypoint/executor.py` shows one read
  (`len(decision.sub_intents)`).
- `tests/test_executor.py`: four old-contract assertions updated (two summary/stdout texts, the decomposed row shape and
  the pre-push superseding row); new tests for goal 8 (a) on the container, a recorded `over_budget` winning over a list
  in the markdown (the recorded `execute` variant would dispatch an agent turn, so the skip-agent path is used to prove
  the record wins), newest-row reads and the corrupt-record exit. `297 passed`.

### Step 5: Stage 3 executes only the recorded decision; package proofs stay green

- `stages/execute.py` (still `396` lines, its budget): `DECISION_*` and `InvalidPlanDecisionError` come from
  `sandbox_executor.decomposition_rule` (no new entrypoint import; only `OVER_BUDGET_EXIT_CODE` and `_sanitize_string`
  remain from the executor); a corrupt recorded decision fails the stage before dispatch; a `decomposed` executor
  summary runs `_run_decomposed_plan` with `{**precheck.to_plan_fields(), "plan_branch": ...}` (the unchanged
  `sub_intents`/`entropy_budget`/`plan_branch` contract) and fails with "the executor reported 'decomposed' but the plan
  row records '<decision>'" when the plan row does not record `decomposed`. Docstring rewritten with no growth.
- Deviation from the plan's helper names: `tests/flow/test_package_exports.py` requires every move-proof key to be
  re-exported from `flow/__init__.py`, whose budget (`362`) left room for exactly one new name on the existing import
  line. So `plan_decision.py` defines one new function, `_backfill_plan_row` (row building, worktree append, push, ref
  sync and logging), the plan's `PlanDecisionError` became a re-raised `InvalidPlanDecisionError` carrying the Stage 3
  message, and the record dict is built inline in `execute.py` instead of `_decomposition_record`. `plan_decision.py` is
  `150` lines.
- `flow/__init__.py`: `359` lines (budget `362`, unchanged); `_backfill_plan_row` added to the import and `__all__`.
- `flow_module_budgets.json`: `stages/plan.py` lowered `247 -> 200`, `stages/plan_decision.py` added at `150`; nothing
  raised.
- `tests/flow/fixtures/flow_py_definitions.json`: digests computed with `tests/flow/move_proof.py`'s
  `package_definitions()`; `_backfill_plan_row` added and `_plan_budget_decision` and `run_execute_stage` refreshed,
  each listed in `amended_definitions` with the reason; `_over_budget_failure` keeps its digest.
- `tests/flow/helpers.py`: the fake planners record `to_plan_fields()` on their rows (switch
  `planner_records_decision=False` models an older image); `BudgetRoleAgents`' executor reads `from_plan_row` (rule
  fallback), writes `plan_decision` instead of `sub_intents`, and can be forced to report a decision
  (`executor_decision`) to model a disagreement.
- Re-pointed patches of `_resolve_branch_ref`, `_read_ledger_rows` and `_git_show_text` from `stages.plan` to
  `stages.plan_decision` in `test_decomposition.py` and `test_pipeline.py`. Two container-row decomposition tests now
  assert the new contract: with no readable plan-row decision a `decomposed` executor report fails Stage 3 and no child
  runs (the depth bound holds trivially).
- `git diff --stat` against the plan commit shows no change to `stages/decomposition.py`, `pipeline.py`,
  `checkpoint.py`, `flow/github/`, `docs/architecture.md`, `docs/git_flow.md` or `docs/safety.md`. Flow suite:
  `505 passed`.

### Step 6: Hermetic tests for goal 8 (a) and (g)

- `tests/test_decomposition_rule.py` (added in Step 1): round trip of all three decisions, `None` for older rows, eleven
  corrupt-record cases, entrypoint-free imports (AST), re-export identity.
- `tests/flow/stages/test_plan_decision.py` (new, bare origin plus host clone via `flow_repos` and `BudgetRoleAgents`):
  a recorded `decomposed` row is returned as is and the plan branch tip is unchanged (goal 8 a, flow side); a recorded
  `execute` is never re-decided from a plan file that lists sub-intents; a corrupt record raises, fails Stage 3 before
  dispatch and writes nothing; an older row gains exactly one superseding row (`ledger_revision` 2,
  `decision_recorded_by: "flow"`, planner fields unchanged, planner ledger a byte prefix, second read writes nothing,
  local branch fast-forwarded, no worktree left); a refused push is logged and the decision still applies; a recorded
  `ledger_revision` is incremented.
- `tests/flow/stages/test_decomposition.py` (new class `TestStage3ExecutesThePlanRowsDecision`): goal 8 (a) end to end
  (children equal the plan row's list, the record handed to `_run_decomposed_plan` is the plan row's, the execution
  summary has `plan_decision` and no `sub_intents`, no plan row written); an executor disagreeing with the plan row
  fails Stage 3 with the mismatch message and runs no child; goal 8 (g) for an in-budget plan (`entropy` equal to the
  budget) with a recorded and an older-image row: same Stage 3 payload keys as before, summary without
  `plan_decision`/`sub_intents`, and the only difference for the older image is one back-filled row.
- Mutation check (once, local, not committed): re-adding `exec_entry["sub_intents"]` in the executor and removing the
  `return recorded` read-first branch in `plan_decision.py` made `8` of the new and updated tests fail
  (`8 failed, 387 passed`); both files were restored from backups and `git diff` on `src/` was clean afterwards.

### Step 7: Document the new plan-row fields in `docs/ledger_schema.md`

- Stage record matrix: the Stage 2 row lists `entropy`, `entropy_budget`, `decomposition_decision` and `sub_intents` and
  replaces "never writes one" with the one-time flow back-fill (`ledger_revision + 1`, `decision_recorded_by: "flow"`,
  temporary worktree, plan branch only, newest row wins); the Stages 1-3 sentence names the back-fill next to the
  empty-diff row.
- New "Plan row decomposition decision" paragraph after the `entropy_budget` paragraph (types,
  `{slug, description, goal}` items, `sandbox_executor/decomposition_rule.py`, the shared serialisation, historic rows
  never rewritten).
- Stage 3 executor rows and the summary field list: `decomposed` summaries carry `plan_decision` (`plan_id`,
  `plan_branch`, `decision`, `sub_intent_count`) and no `sub_intents`; historic summaries with `sub_intents` stay valid
  and are no longer read. `plans.jsonl` field list gained `decomposition_decision`, `sub_intents`, `ledger_revision` and
  `decision_recorded_by`.
- `npx prettier --write` then `--check` on both markdown files: the second pass is a no-op.
