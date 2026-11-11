# Execution Record: E-1794391637-5549d1a0-claude-agent-claude-sonnet-5-5

## Summary

Implemented plan `P-1794391544-abdc9b6d-claude-agent-claude-sonnet-5-5`: one documented ref limit
(`MAX_REF_LENGTH = 255`) and one builder module, `sandbox_executor/ref_names.py`, through which every branch name is
fitted and measured as if it ended in `/calibrated`. A name that fits is unchanged; a longer one is shortened
deterministically with a 6-character `sha256` digest of the original text.

Deviations from the plan:

- The module lives at `sandbox_executor/ref_names.py`, not `flow/ref_names.py`. Importing anything under `flow/` runs
  `flow/__init__.py`, which imports `entrypoint.planner` and `ledger`; `ledger.py`, `planner.py` and `executor.py` need
  the builder, so a `flow/` location created an import cycle. It is therefore not in `flow_module_budgets.json`.
- Budgets raised by 1 to 3 lines, each for one `ref_names` import and one routed call: `child_progress.py` 194 to 195,
  `stages/calibrate.py` 315 to 316, `stages/intent.py` 204 to 207.
- Plan and execution ids are fitted inside `ledger.build_plan_id` and `ledger.build_execution_id`, so the entrypoints
  and the dry-run stages share one path without editing every call site.
- Intent branches are measured with room for one plan and one execution at their shortest labels (and, for a child, for
  the depth-limited child levels, falling back to plan and execution only). Names that fit with that room are unchanged.
- Three definitions in the move-proof snapshot were refreshed under `amended_definitions` with this execution as the
  reason.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.95  |
| entropy_actual        | 7.5   |
| impact_actual         | 80.0  |
| cost_actual           | 22.0  |
| learning_value_actual | 6.0   |

## Steps Executed

1. **Step 1 (audit):** creation sites are `entrypoint/intent_creator.py`, `entrypoint/planner.py`,
   `entrypoint/executor.py`, `flow/stages/{intent,plan,execute,calibrate}.py`, `flow/child_progress.py` and
   `calibration.py`; plan and execution ids come from `ledger.build_plan_id` and `ledger.build_execution_id`. Fewer than
   15 sites, so no sub-intent was needed.
2. **Step 2 (module):** `ref_names.py` with the constants, `measured_length`, `fit_parts`, plan, execution, root and
   child fitters, `RefTooLongError`, and the helpers below.
3. **Step 3 (wiring):** ledger id builders, `_child_intent_branch`, Stage 1 and the intent creator
   (`resolve_root_intent_branch`), and the calibration branch (`calibration_branch`). A recorded `branch` is used as it
   is, so resume never recomputes.
4. **Step 4 (failures):** Stage 1 measures the plan and execution chain before any branch exists and fails naming the
   limit, the length and the segment; the executor's refused-push report names
   `ref too long: branch <name> (<n> characters)` and keeps the `UNPUSHED EXECUTION PATCH`.
5. **Step 5 (ledger):** `original_slug` on intent rows, `original_agent` and `original_model` on plan and execution
   rows, only when shortened.
6. **Step 6 (tests):** `tests/test_ref_names.py` (24 tests): boundaries at 243, 244 and 245 without the prefix and 254,
   255 and 256 with it, determinism, no collision, the failing decomposed case with four children, depth three, resume,
   ledger fields, Stage 1 failure with no `.git`, the push rejection and the static literal-suffix check.
7. **Step 7 (docs):** `docs/git_flow.md` section 2.5.1 and a note in `docs/ledger_schema.md`.

## Tests Run

- `uv run pytest apps/sandbox-executor/tests/test_ref_names.py`: 24 passed.
- `uv run task test` equivalent (`uv run pytest -n auto --dist worksteal -m "not integration_test and not stress"`):
  3607 passed, 651 subtests passed, 30.97s. One run; the intent has no timing target, so no repetition and no median.
- `ruff check .`, `ruff format --check .`, `scripts/check_flow_module_budgets.py` and `npx prettier --check "**/*.md"`:
  all pass.
