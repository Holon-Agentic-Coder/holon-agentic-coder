# Execution Record: E-1794523721-69784e3e-claude-agent-claude-sonnet-5-5

## Summary

A non-ledger pre-push sync conflict now keeps the executor's commits on the execution branch, records a `sync_conflict`
row with `conflicted_paths`, exits `0`, and the execute stage returns a resumable success carrying `execution_branch`.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.95  |
| entropy_actual        | 5.0   |
| impact_actual         | 70.0  |
| cost_actual           | 10.0  |
| learning_value_actual | 4.0   |

## Steps Executed

1. Step 1 (reproduction, by code reading): `publish_new_child_branches` already pushes failed execution branches, so the
   "record sync failure" commit does reach origin. The stage then fails with status `failure` and the branch only in the
   failure payload, so the flow treated the conflict as a failed execute. Mechanism: the failure mapping, not a lost
   branch.
2. Step 2: `entrypoint/sync_conflict.py` (`SyncConflictMessage`, `sync_conflict_row`); `executor.py` gains
   `_record_sync_failure`, which `main()` calls. Other sync failures are unchanged.
3. Step 3: `flow/stages/sync_conflict.py` returns a `StageStatus.SUCCESS` result with `sync_conflict: true`,
   `conflicted_paths` and `execution_branch`; one call in `stages/execute.py`. No `StageStatus` change. Budgets updated.
4. Step 4: `tests/test_pre_push_sync_conflict.py` covers the non-ledger, ledger-only and clean cases, the stage result
   and review acceptance of the branch.
5. Step 5: docs updated in `docs/ledger_schema.md` and the execution architecture specification.

## Tests Run

- `uv run pytest apps/sandbox-executor/tests/test_pre_push_sync_conflict.py apps/sandbox-executor/tests/test_executor.py`:
  276 passed in 36.19s.
- `uv run task test`: 3767 passed, 1 skipped in 34.60s.
- `uv run task lint`: passed.
- `npx prettier --check` on the two edited docs: passed. `uv run task lint-docs` over all markdown reports 37 files with
  style issues (for example `plans/*_calibration.md`) that this change did not touch; left alone.
