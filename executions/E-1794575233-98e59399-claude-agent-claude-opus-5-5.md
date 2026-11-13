# Execution Record: E-1794575233-98e59399-claude-agent-claude-opus-5-5

## Summary

In progress: closing the gap in `decide_post` where a halt that lands on the last label (a FAILED verdict from a
crashed, timed-out or stalled reviewer, a `review_iteration` budget breach, an unresolved ref or unmeasured tests) was
treated as "iteration budget exhausted" and posted.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

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
