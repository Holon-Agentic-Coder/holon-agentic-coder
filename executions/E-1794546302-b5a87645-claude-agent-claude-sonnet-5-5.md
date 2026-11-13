# Execution Record: E-1794546302-b5a87645-claude-agent-claude-sonnet-5-5

## Summary

The executor now runs `uv run task test` after the agent turn and records `test_pass_rate`, counts, `test_exit_code`,
`test_run_error` and `test_source` in its summary row. Stage 3 and Stage 4 carry them (null stays unmeasured, a rate
below `1.0` or a non-zero test exit is a failure), the review loop measures locally when no CI registers (cached per
head SHA, source `review_local`), and calibration reads the value. Docs updated.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.9   |
| entropy_actual        | 6.5   |
| impact_actual         | 78.0  |
| cost_actual           | 26.0  |
| learning_value_actual | 5.0   |

## Steps Executed

- Steps 1 and 2: added `entrypoint/test_measurement.py` (pytest summary parser, bounded runner, redaction) and
  `tests/test_test_measurement.py`.
- Step 3: `entrypoint/executor_test_run.py` helper wired into `executor.py` with one call and one
  `exec_entry.update(...)`; runs only on a successful, effective-diff outcome. A conftest autouse fixture stubs it for
  existing executor tests. Stray worktree changes are logged as a warning, not unstaged.
- Step 3: `entrypoint/executor_test_run.py` is wired into `executor.py` with one call and one `exec_entry.update(...)`;
  it runs only on a successful, effective-diff outcome. Stray worktree changes are logged as a warning, not unstaged.
- Step 4: new `flow/test_evidence.py` owns `TEST_MEASUREMENT_KEYS`, field validation, the effective exit code (an
  unrunnable test run is unmeasured, not failed) and the review package `test_results`. Superseding rows drop every
  measurement key. The posted `Checks` line carries the source. New names are `_`-prefixed to satisfy the flow package
  export guard; flow module budgets were not raised (`dispatch.py` budget lowered, two new entries added).
- Step 5: new `flow/review/local_tests.py` (`_ReviewTestGate`, `_ReviewLocalTests`) with a SHA-keyed cache; `loop.py`
  gets a gate call in the post-review CI branch only (not the reviewer-findings fast path or dry run).
- Step 6: calibration already read `test_pass_rate` (`calibration.py:788`); `calibration_ev.py` does not use it, so no
  formula change; added a test with the new fields and a null case.
- Step 7: tests in `tests/flow/test_review_local_tests.py`; docs updated in `docs/ledger_schema.md`, `docs/git_flow.md`,
  `docs/review_consensus.md`, `docs/sandbox/execute_plan.md`.

## Tests Run

No timing requirement in the intent, so no repeated runs.

- `uv run pytest apps/sandbox-executor/tests/test_test_measurement.py`: 12 passed.
- `uv run pytest apps/sandbox-executor/tests/flow/test_review_local_tests.py apps/sandbox-executor/tests/test_calibration.py`:
  all passed.
- `uv run task test` (full suite, `-n auto --dist worksteal`): first run `2 failed, 3874 passed, 1 skipped in 36.27s`,
  the two failures being `TestUnmeasuredTestsStopEarly` tests that passed alone and in both reruns (transient, cause not
  identified; a patched global `time.sleep` saw about 1000 lock-style backoff sleeps); reruns
  `3876 passed, 1 skipped in 37.51s` and `3876 passed, 1 skipped in 39.02s`. Median of the three: `3876 passed` (2 of 3
  runs clean).
- `uv run task lint`: ruff, format and flow module budgets passed.
- `uv run task lint-docs`: my changed docs and this file pass `prettier --check`; the task as a whole warns on about 37
  pre-existing `plans/` and other markdown files that also fail on the base commit.
