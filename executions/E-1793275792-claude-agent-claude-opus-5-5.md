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

### Step 3: Remove the rewriting paths and stop staging ledgers (done)

- `flow.py`: a ledger counts as conflicted only when git reports it unmerged or a line starts with a marker
  (`_has_conflict_marker_lines`, `_ledgers_with_conflict_markers`). JSON rows that mention `|||||||` no longer count.
- The `check_and_resolve_stage_conflicts` pre-check resolves a conflicted ledger through `_resolve_conflicting_ledger`
  (which calls `reconcile_ledger_file`). It no longer runs `git add`, so a failed or dry run cannot leave a ledger
  staged.
- New pure-append resolution: `_split_conflict_sides` keeps exact line text for each side and drops the diff3 base.
  `_append_only_union` keeps the prefix side byte for byte and appends the other side's missing lines in their original
  order, comparing exact text. `resolve_append_only_conflict(content, prefix_side)` returns the prefix and the result.
  `_write_resolved_ledger` writes it (temp file plus `os.replace`, the only non-append write left, because the file
  holds markers) and then calls `assert_ledger_prefix`.
- Public names kept: `reconcile_jsonl_ledger_conflict` and `reconcile_ledger_content` return marker-free content
  unchanged. `reconcile_ledger_file` does not touch a file that has no markers. `reconcile_ledgers` resolves only
  marker-bearing files. `reconcile_ledger_rows` no longer sorts or picks a winner by key or revision; it only drops
  exact repeats, keeping the original order. The now-unused `_parse_ledger_timestamp` was removed.
- `entrypoint/executor.py` `sync_and_reconcile_pre_push`: no reconciliation after a clean merge. On a ledger-only
  conflict, each conflicted ledger is resolved with `prefix_side="theirs"`, so the target branch's published bytes stay
  a prefix and the branch's rows follow. The extra `reconcile_ledgers` pass over all three ledgers was removed.
- Deviation from the plan: the plan suggested keeping "ours" as the prefix everywhere. For the pre-push merge of the
  target into the branch, "theirs" is the target branch. Keeping it as the prefix is what makes main's file a byte
  prefix of the branch's file, which is the intent's acceptance condition. The stage pre-check keeps "ours".
- Affected tests: `793` collected, `787 passed`, `6 failed`. All 6 are `TestLedgerReconciliation` cases that encode
  the old dedupe or sort contract, which is under the plan's 10-test discard limit. They are updated in Step 4.
