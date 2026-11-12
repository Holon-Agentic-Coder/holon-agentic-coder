# Execution Record: E-1794478418-d600c349-claude-agent-claude-sonnet-5-5

## Summary

Added `flow/partial_salvage.py`, which commits an interrupted run's leftovers on the failed execution branch on a Stage
3 resume, pushes it and appends a superseding `failure` row with `partial_head`. Wired it into `run_execute_stage`,
added tests and docs.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.9   |
| entropy_actual        | 5.5   |
| impact_actual         | 55.0  |
| cost_actual           | 12.0  |
| learning_value_actual | 4.0   |

## Steps Executed

1. Step 1: surveyed helpers. Branch rule: salvage only when HEAD is on an `E-` branch of this plan. Any other dirty
   branch (plan, `main`, detached) is refused with a message, a deliberate narrowing of rule (b) in the plan.
2. Step 2: added `flow/partial_salvage.py` (`leftover_paths`, `salvage_leftovers`, `salvage_for_resume`).
3. Step 3: wired it into `run_execute_stage`; updated `flow_module_budgets.json` and the `flow` package exports.
4. Step 4: added `tests/test_partial_salvage.py` (10 tests).
5. Step 5: updated `docs/git_flow.md`, `docs/ledger_schema.md` and the execution architecture specification.

## Tests Run

- `uv run pytest apps/sandbox-executor/tests/test_partial_salvage.py`: 10 passed in 0.57s.
- `uv run task lint`: passed. `uv run task lint-docs`: passed.
- `uv run task test`: 3734 passed, 1 skipped in 34.13s (an earlier run failed 6 package-export tests, fixed by exporting
  the new names).
