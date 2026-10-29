# Execution Record: E-1793275792-claude-agent-claude-opus-5-5

## Summary

Implements plan `P-1793275622-claude-agent-claude-opus-5-5` for intent
`I-1793275605-ledger-writes-are-pure-appends-never-reorder-intents`: every write to `holon-knowledge/ledger/*.jsonl` is a
pure append, guarded by a byte-prefix check, and no stage pre-check rewrites or stages a ledger.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Reproduce the reorder and inventory every ledger writer (done)

- Environment note: `uv` was not installed in the sandbox, so it was installed with `pip install --user uv` and the
  workspace was synced with `uv sync`. No project dependency changed.
- Writer inventory under `apps/sandbox-executor/src/sandbox_executor`:
  - Rewriting: `flow.check_and_resolve_stage_conflicts` (substring scan for `<<<<<<<`, `|||||||`, `=======`,
    `>>>>>>>` anywhere in the file, then `reconcile_jsonl_ledger_conflict`, `open(..., "w")` and `git add`).
  - Rewriting: `flow.reconcile_ledger_file` (temp file plus `os.replace`), reached through `flow.reconcile_ledgers` and
    `entrypoint/executor.py` `sync_and_reconcile_pre_push`, which ran `reconcile_ledgers` and `git add` after every
    merge, including a clean one.
  - Sort and dedupe core: `flow.reconcile_ledger_rows` (primary-key dedupe, `ledger_revision`/`created_at` winner,
    `created_at` sort), re-serialised with default `json.dumps` separators.
  - Pure appends (`"a"` mode): `ledger.LedgerLock`, `ledger.atomic_ledger_append`, `append_action_record`,
    `append_summary_record`, `try_append_ledger_line`, `try_append_action_record`, `knowledge/records.py`,
    `entrypoint/intent_creator.py`, `entrypoint/planner.py`, the executor's execution-row writers, `flow.py` Stage 3
    row writer and calibration ACT-CAL records.
- Reproduction: `tests/test_ledger_append_only.py::test_stage_precheck_leaves_ledgers_byte_identical` fails on the
  unmodified code. The intents ledger (a row mentioning `|||||||` in a JSON string) is deduplicated (the earlier `late`
  row is dropped), sorted by `created_at` and re-serialised with `", "` separators. All three movements happen.
- Real data, read-only: running the old `reconcile_jsonl_ledger_conflict` over main's ledgers is byte-identity for
  `intents.jsonl` (209,499 bytes) but rewrites `plans.jsonl` and `executions.jsonl` (341,674 to 341,198 bytes), which
  `sync_and_reconcile_pre_push` would hit after any divergent merge.
- Stage 3 reaches `sync_and_reconcile_pre_push` through the executor entrypoint before its push; the flow's own stage
  pre-checks call `check_and_resolve_stage_conflicts` directly.

### Step 2: Add the prefix guard helper in ledger.py (done)

- Added `LedgerRewriteError` (a `RuntimeError`), `assert_ledger_prefix(path, before)`, `_read_ledger_bytes` (a missing
  file reads as `b""`) and `_first_divergence` to `ledger.py`.
- `atomic_ledger_append` now captures the file bytes under the lock, yields, flushes and then calls
  `assert_ledger_prefix`. `LedgerLock` is unchanged.
- `append_action_record` and `append_summary_record` now write through `atomic_ledger_append` instead of a bare
  `LedgerLock`, so every in-module appender is guarded.
- Guard tests (`TestPrefixGuard`, 5 cases including an in-context rewrite) pass, and `test_ledger_lock.py` and
  `test_ledger_query.py` stay green: `59 passed` with the Step 1 reproduction still failing as intended.
