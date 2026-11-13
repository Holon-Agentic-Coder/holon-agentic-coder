# Execution Record: E-1794575233-98e59399-claude-agent-claude-opus-5-5

## Summary

Closed the gap in `decide_post` (`flow/review/posting.py`). Before this change, a halt that landed on the last label was
treated as "iteration budget exhausted" and posted to the pull request. Such halts include a FAILED verdict from a
crashed, timed-out or stalled reviewer turn, a `review_iteration` budget breach, an unresolved ref and unmeasured tests.
A red test confirmed the leak, including after a checkpoint resume (`first_iteration=3, max_iterations=1`). Now only a
consensus approval, or a cap reached with every reviewer completed and a CHANGES_REQUESTED verdict, posts. A withheld
post is logged locally and recorded as `review_post_withheld` on the existing `REV-...` summary row in
`executions.jsonl`. No new ledger, dependency or `# noqa` was added.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.95  |
| entropy_actual        | 3.6   |
| impact_actual         | 65.0  |
| cost_actual           | 8.0   |
| learning_value_actual | 3.0   |

## Steps Executed

1. Step 1 (red reproduction): added loop tests to `tests/flow/review/test_posting_terminal.py` (failed reviewer turn at
   the cap, a resumed pass with `first_iteration=3, max_iterations=1`, a `review_iteration` budget breach at the cap,
   and a positive cap post). On unmodified code the poster was called once with `ensemble_verdict: FAILED` (and once on
   the budget breach), confirming the gap exactly where the plan predicted. `test_loop.py` is already 382 lines, so per
   plan Step 4.6 the posting tests live in `test_posting_terminal.py` (the intent's `tests/flow/test_loop.py` does not
   exist).
2. Step 2 (gate): `flow/review/posting.py` gained `PostFacts.verdict_terminal` and the pure helper
   `is_terminal_verdict(verdict, reviewers)` (CHANGES_REQUESTED or APPROVED with every reviewer `COMPLETED`).
   `decide_post` now withholds a non-terminal verdict even at the cap; the halted and failed-verdict branches share one
   return to stay within `PLR0911`. `loop.py` passes `verdict_terminal` and widens `halted_early` to cover
   `budget_breach`, `missing_ref` and `tests_not_measured`. Budgets: `review/loop.py` `904 -> 907`, `review/posting.py`
   `139 -> 155`, with the reason appended to the budget file's description. No `# noqa` was added. Two existing tests in
   `tests/test_review_loop.py` encoded the leak and were updated: the blocking-outcome matrix now expects no post for
   the failed and unparseable reviewer cases, and the CLI post-failure test now measures its tests (`ci=_GREEN_CI`) so
   it still reaches the post.
3. Step 3 (local record): `log_skipped_post` already logs "Not posting a review consensus ..." with the new reason. The
   `REV-...` summary row in `executions.jsonl` gains one optional key, `review_post_withheld` (the decision reason or
   `null`). `docs/ledger_schema.md` imposes no closed key set on summary rows, so no schema change or new ledger was
   needed. No `ACT-POST` row is written for a withheld post.
4. Step 4 (tests): `tests/flow/review/test_posting_terminal.py` now covers a failed reviewer turn at the cap
   (parametrised crash, timeout and stall reasons, asserting the "Not posting" log line, `status: failed` and
   `review_post_withheld`), a resumed pass, a `review_iteration` budget breach driven by an injected clock and sleep, an
   unresolved ref at the cap, and the positive cap post with every reviewer completed (`review_post_withheld` is
   `null`). The existing approval-posts-once and cap-posts-once tests still pass. The `decide_post` matrix gained
   `verdict_terminal` rows. The intent's `tests/flow/test_loop.py` does not exist; the real
   `tests/flow/review/test_loop.py` is already 382 lines, so the posting tests stay in the focused
   `test_posting_terminal.py`.
5. Step 5 (docs): `docs/review_consensus.md` ("When the review is posted") and the Stage 4 row of
   `docs/ledger_schema.md` now describe the halted-iteration rule and `review_post_withheld`. The budget description
   note was added in Step 2.
6. Step 6 (gates): the first full-suite run failed `tests/flow/test_package_exports.py` because the new public helper
   was not exported from `flow/__init__.py`. The helper was renamed to the module-private `_is_terminal_verdict` (as
   earlier executions did) rather than growing the package exports. All gates then passed.

## Tests Run

- Step 1 red run, `uv run pytest apps/sandbox-executor/tests/flow/review/test_posting_terminal.py -k ...`: the new loop
  tests failed with the poster called once (`ensemble_verdict: FAILED`, and on the budget breach), as predicted.
- `uv run pytest tests/flow/review tests/test_review_loop.py tests/test_review_ensemble.py tests/test_review_dispatch.py -n auto --dist worksteal`
  after Step 2: 304 passed in 2.39s (after updating the two leak-encoding tests; the first run had 3 failures from
  them).
- `uv run pytest tests/flow/review tests/test_review_loop.py tests/test_review_ensemble.py tests/test_review_dispatch.py tests/flow/test_module_budgets.py tests/test_ledger_append_only.py -n auto --dist worksteal`:
  352 passed in 2.55s.
- `uv run task test` (run 1): 2 failed, 3951 passed, 1 skipped in 22.43s (package-export convention; fixed as in Step
  6).
- `uv run task test` (run 2, final code): 3952 passed, 1 skipped in 23.22s. These are two runs of different code, not a
  timing measurement, so no median is computed.
- `uv run task lint`: passed (`uv lock --check`, ruff check, ruff format, flow module budgets: 55 modules within
  budget).
- `uv run task lint-docs`: 38 Prettier warnings, all in files this change does not touch. A run with this change stashed
  reports the same 38. The touched docs and this record pass Prettier.
