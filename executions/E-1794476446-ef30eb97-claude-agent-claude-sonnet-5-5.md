# Execution Record: E-1794476446-ef30eb97-claude-agent-claude-sonnet-5-5

## Summary

The Stage 4 review loop now posts its consensus review only on a terminal exit: consensus approval, or the label
budget (`last_label`) exhausted. The post decision and the post and ACT-POST block moved out of `review/loop.py` into
the new `review/posting.py` (`decide_post()`, `post_review_consensus()`). `post_pr_review_consensus` is still imported
in `loop.py` and passed in as a callable, so existing patch targets keep working. A resolver error or the
no-convergence halt posts nothing even at the cap; a halt before the cap (dirty merge, tests not measured, no resolver
pass applicable, budget stop) posts nothing. Each non-terminal skip logs the reason and the report stays local.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.95  |
| entropy_actual        | 5.0   |
| impact_actual         | 60.0  |
| cost_actual           | 12.0  |
| learning_value_actual | 4.0   |

## Steps Executed

1. Mapped exits: the loop breaks on missing ref, tests not measured, approval, iteration budget breach, dry run, a
   non-changes-requested verdict, no resolver pass applicable, no-convergence and resolver error. No host-side poster
   exists besides the loop. Resolver errors, failed gates and stage budget stops surface as `resolver_error`.
2. Added `review/posting.py` and wired it into `loop.py`. Terminal means consensus reached, or `iteration >= last_label`
   with no resolver error and no no-convergence halt. Lowered the `review/loop.py` budget to 896 and added
   `review/posting.py` at 139.
3. Added `tests/flow/review/test_posting_terminal.py` (18 tests). Updated existing tests that relied on a non-terminal
   exit posting: `test_records.py`, `test_ensemble.py`, `test_consensus_integrity.py`, `test_review_post.py` and
   `test_review_loop.py` now use a one-pass budget so the cap is reached; `test_review_dispatch.py` and
   `test_review_resolver_failure.py` now assert nothing is posted; `test_package_exports.py` lists the new public names
   as unexported. Not done: the check that the new tests fail against the old code (loop and posting are one change).
4. Updated `docs/review_consensus.md`, `docs/ledger_schema.md` (prettier realigned its table) and the
   `run_review_loop` docstring.
5. Ran lint, lint-docs and the full suite.

## Tests Run

- `uv run pytest` on the affected files: passed (682 passed after the fixes, then 107 in `test_review_loop.py`).
- `uv run task lint`: passed.
- `uv run task lint-docs`: passed.
- `uv run task test`: `3719 passed, 1 skipped` in 33.83s (one run, no timing target, so no median).
