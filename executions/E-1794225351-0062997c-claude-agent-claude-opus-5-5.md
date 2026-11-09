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
  annotations and explicitly forbids relocating one ("Refactor the function instead"), so moving
  `parse_sub_intent_list` with its `# noqa: C901, PLR0912, PLR0915` failed the gate. The function was refactored
  instead into a `_SubIntentScan` dataclass (one method per concern: fences, headings, item collection) plus
  `_sub_intent_dicts`, with no suppression at all. The line-level logic and comments are unchanged; the full
  decomposition regression corpus in `tests/test_executor.py` passes without any edit (`293 passed`).
- Added `InvalidPlanDecisionError`, `DecompositionDecision.to_plan_fields()` and `DecompositionDecision.from_plan_row()`
  with a strict shape check of recorded `sub_intents`; `tests/test_decomposition_rule.py` covers the round trip, the
  older-row `None`, corrupt records, the entrypoint-free imports and the re-export identity.

### Step 2: The planner records the decision on the plan row

- `entrypoint/planner.py`: `converge_prettier` now runs before the ledger append (it only touches the plan markdown),
  and a new `_plan_decision_fields(plan_entry, plan_md_path, intent_data)` helper reads the formatted plan back and
  returns `should_decompose(...).to_plan_fields()`, merged into the row before the single `atomic_ledger_append`.
  `main()` keeps its existing suppressions and gains no branch; over-budget plans are still appended (as
  `over_budget`).
- `tests/test_planner.py`: four tests (in-budget `execute` without `sub_intents`; listed plan `decomposed` with the list
  equal to `parse_sub_intent_list` of the committed file; a prettier re-wrap is reflected in the recorded description;
  over budget recorded). The harness gained an optional `converge_side_effect`. `test_planner_integration.py` needed no
  change.

### Step 3: `flow/stages/plan_decision.py` reads the recorded decision and back-fills older rows once

- New module (`177` lines before final formatting): `_plan_budget_decision` reads the newest matching plan row (as
  before) and returns `DecompositionDecision.from_plan_row(row)` when the row records a decision; an
  `InvalidPlanDecisionError` becomes `PlanDecisionError` (Stage 3 fails before dispatch). An older-image row is decided
  with `should_decompose` and `_backfill_plan_decision` appends one superseding row (planner row plus
  `to_plan_fields()`, `ledger_revision` + 1, fresh `created_at`, `decision_recorded_by: "flow"`) through
  `_append_superseding_plan_row`: a temporary detached worktree on `refs/remotes/origin/<plan_branch>`,
  `atomic_ledger_append`, `validate_ledger_file_unique_ids`, commit, push to the plan branch only, then
  `_sync_local_execution_refs`. A failed write is logged, a `LedgerRewriteError` propagates.
- `_over_budget_failure` moved verbatim (same AST); `_decomposition_record` returns the unchanged
  `{sub_intents, entropy_budget, plan_branch}` contract of `_run_decomposed_plan`.
- `stages/plan.py` lost the two functions and the entrypoint import (it no longer imports
  `sandbox_executor.entrypoint.executor`); `_format_entropy` stays for `pipeline.py`. `flow/__init__.py` and
  `stages/execute.py` import the moved names from `plan_decision`. Flow tests that patched helpers at
  `stages.plan` are re-pointed in Step 5.
