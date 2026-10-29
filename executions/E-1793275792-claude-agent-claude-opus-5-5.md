# Execution Record: E-1793275792-claude-agent-claude-opus-5-5

## Summary

Implements plan `P-1793275622-claude-agent-claude-opus-5-5` for intent
`I-1793275605-ledger-writes-are-pure-appends-never-reorder-intents`: every write to `holon-knowledge/ledger/*.jsonl` is
a pure append, guarded by a byte-prefix check, and no stage pre-check rewrites or stages a ledger.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 1.0   |
| entropy_actual        | 5.6   |
| impact_actual         | 82.0  |
| cost_actual           | 13.0  |
| learning_value_actual | 5.0   |

## Steps Executed

### Step 1: Reproduce the reorder and inventory every ledger writer (done)

- Environment note: `uv` was not installed in the sandbox, so it was installed with `pip install --user uv` and the
  workspace was synced with `uv sync`. No project dependency changed.
- Writer inventory under `apps/sandbox-executor/src/sandbox_executor`:
  - Rewriting: `flow.check_and_resolve_stage_conflicts` (substring scan for `<<<<<<<`, `|||||||`, `=======`, `>>>>>>>`
    anywhere in the file, then `reconcile_jsonl_ledger_conflict`, `open(..., "w")` and `git add`).
  - Rewriting: `flow.reconcile_ledger_file` (temp file plus `os.replace`), reached through `flow.reconcile_ledgers` and
    `entrypoint/executor.py` `sync_and_reconcile_pre_push`, which ran `reconcile_ledgers` and `git add` after every
    merge, including a clean one.
  - Sort and dedupe core: `flow.reconcile_ledger_rows` (primary-key dedupe, `ledger_revision`/`created_at` winner,
    `created_at` sort), re-serialised with default `json.dumps` separators.
  - Pure appends (`"a"` mode): `ledger.LedgerLock`, `ledger.atomic_ledger_append`, `append_action_record`,
    `append_summary_record`, `try_append_ledger_line`, `try_append_action_record`, `knowledge/records.py`,
    `entrypoint/intent_creator.py`, `entrypoint/planner.py`, the executor's execution-row writers, `flow.py` Stage 3 row
    writer and calibration ACT-CAL records.
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
- Affected tests: `793` collected, `787 passed`, `6 failed`. All 6 are `TestLedgerReconciliation` cases that encode the
  old dedupe or sort contract, which is under the plan's 10-test discard limit. They are updated in Step 4.

### Step 4: Add the pytest suite for the pure-append guarantee (done)

- `apps/sandbox-executor/tests/test_ledger_append_only.py` (19 tests). The `intents.jsonl` seed has out-of-order
  `created_at` values, a repeated `branch` key and a `|||||||` substring inside a JSON string. Each ledger is snapshot
  as bytes.
  - `TestIntentStageLedgers`: drives the real `run_intent_stage` against a local bare origin (no remote network). The
    stand-in container runs the stage pre-check and then a guarded `atomic_ledger_append`, as `intent_creator` does.
    - Success: on the intent branch every ledger starts with main's bytes, and `intents.jsonl` grows by exactly one
      line.
    - Failed Stage 1 (the stand-in exits 3): the ledgers are byte-identical, `git diff --cached --name-only` is empty
      and tracked status is clean.
    - Dry run: the same assertions, and no dispatch.
  - `test_stage_precheck_leaves_ledgers_byte_identical`: the Step 1 reproduction, now green, with fake `run_git` and no
    ledger `add` call.
  - `TestAppendOnlyResolution`: marker-free input is returned unchanged, and a file without markers is not rewritten
    (its mtime is unchanged). Real conflicts, in both standard and diff3 form, give the ours bytes and then the missing
    theirs lines in their original order. A `theirs` prefix keeps the target's bytes first, and lines that are already
    present are not repeated. A pre-check on a `UU` ledger resolves it without staging it.
  - `TestPrePushMerge`: `sync_and_reconcile_pre_push` on a real local clone. After a ledger conflict the result is
    main's bytes followed by the branch row. After a clean merge git's result is left as it is.
  - `TestPrefixGuard` (from Step 2).
- Hermeticity: git runs only against `tmp_path` repositories, and `check_pr_mergeability` is stubbed so `gh` is never
  invoked. There is no Docker and no LLM runner. The suite-wide `conftest.py` guards stay active.
- Updated 6 `TestLedgerReconciliation` tests in `test_flow.py`, with a comment on the append-only contract. They used to
  assert key-based dedupe, a `ledger_revision` winner and a `created_at` sort. They now assert byte identity for
  marker-free content, ours-then-theirs order for conflicts, and exact-repeat-only dedupe in `reconcile_ledger_rows`.
- Revert check: with Step 3's `flow.py` and `executor.py` restored to the Step 2 commit, 13 of the 19 new tests fail.
  The 6 that still pass are the guard tests and the dry run, which skips the pre-check on both versions.
- `uv run pytest apps/sandbox-executor/tests/test_ledger_append_only.py apps/sandbox-executor/tests/test_ledger_lock.py apps/sandbox-executor/tests/test_flow.py -n auto --dist worksteal`:
  `344 passed` (before the 2 pre-push tests were added).

### Step 5: Document the pure-append guarantee in docs/ledger_schema.md (done)

- Added one "Pure-Append Guarantee" bullet to the `LedgerLock` section of `docs/ledger_schema.md`. It covers the
  byte-prefix rule, `assert_ledger_prefix` and `LedgerRewriteError`, append-only conflict resolution and no staging on
  failed or dry runs. `docs/git_flow.md`, `docs/examples.md` and `README.md` are untouched.
- `npx prettier --check docs/ledger_schema.md`: passes.

### Step 6: Run lint, docs lint and the full test suite once (done)

- The first affected-test run surfaced 4 failures in `test_intent_creator.py`. Those tests mock `builtins.open` and kept
  only the last handle opened per path, so the guard's post-write `"rb"` read replaced the appending handle the tests
  inspect. The fix is test bookkeeping only: record a handle only when it was opened for writing, with a comment. The
  tests' semantics are unchanged. No other failures.
- `uv run task lint`, `uv run task lint-docs` and `uv run task test` all exit 0 (figures under Tests Run).
- The diff against the plan commit touches only `ledger.py`, `flow.py`, `entrypoint/executor.py`, their tests,
  `docs/ledger_schema.md` and this record.

### Step 7: Verify byte-prefix preservation against main's real ledgers (done)

- `test_repository_ledgers_survive_precheck_and_reconcile_byte_for_byte` copies the repository's three ledgers into
  `tmp_path`, which includes the `intents.jsonl` row that mentions `|||||||`. On the copies it runs
  `check_and_resolve_stage_conflicts` with fake git, then `reconcile_ledger_file`, and asserts byte identity and no
  ledger `add` call. It then appends one row through `atomic_ledger_append` and asserts an exact prefix. The originals'
  SHA-256 digests are unchanged at the end. The test is skipped if the ledgers are absent.
- The old code rewrote these real files (Step 1: `executions.jsonl` shrank from 341,674 to 341,198 bytes). They now pass
  through unchanged.
- `git status --porcelain holon-knowledge/ledger` was empty after the full suite.

### Residual notes

- Stage pre-checks no longer run `git add`. A genuinely unmerged (`UU`) ledger in the operator's checkout is resolved in
  the working tree by pure append, and the commit step that owns the ledger stages it.
- Historical rows on main were not repaired, as the intent's Q&A requires.

## Tests Run

All `pytest` runs used `-n auto --dist worksteal`. Each figure was run once and is not a timing measurement. The intent
has no performance goal, so a median is the single value.

| command                                                                                                                                   | result                                           | pytest time |
| ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ | ----------- |
| `uv run pytest apps/sandbox-executor/tests/test_ledger_append_only.py` (Step 1, unmodified code)                                          | `1 failed` (expected reproduction)               | `0.01s`     |
| `uv run pytest test_ledger_append_only.py test_ledger_lock.py test_ledger_query.py` (Step 2)                                              | `59 passed`, `1 failed` (reproduction)           | `3.32s`     |
| `uv run pytest test_ledger_append_only.py test_flow.py test_review_loop.py test_planner.py test_calibration.py test_executor.py` (Step 3) | `787 passed`, `6 failed` (old contract tests)    | `29.19s`    |
| `uv run pytest test_ledger_append_only.py test_ledger_lock.py test_flow.py` (Step 4)                                                      | `344 passed`                                     | `5.96s`     |
| `uv run pytest test_ledger_append_only.py` with Step 3 reverted locally                                                                   | `13 failed`, `6 passed` (expected)               | `1.99s`     |
| `uv run pytest test_ledger_append_only.py` (Step 7 added)                                                                                 | `20 passed`                                      | `3.06s`     |
| `uv run pytest` affected set (append-only, lock, flow, review loop, planner, intent creator, executor)                                    | `760 passed`, `4 failed` (`test_intent_creator`) | `28.95s`    |
| `uv run pytest` affected set, after the intent creator mock fix                                                                           | `764 passed`                                     | `27.95s`    |
| `uv run task test` (full suite, once)                                                                                                     | `2811 passed`, `0 failed`                        | `30.74s`    |
| `uv run task lint`                                                                                                                        | passed                                           | n/a         |
| `uv run task lint-docs`                                                                                                                   | passed                                           | n/a         |

Median of the single full-suite run: `30.74s`.
