# Execution Record: E-1794388336-46c54247-claude-agent-claude-sonnet-5-5

## Summary

Pending CI is now waited out on its own wall-clock budget (`HOLON_REVIEW_CI_WAIT_SECONDS`, default `1800` s) with
back-off `15`, `30`, `60`, `120` s (ceiling `120` s), outside the iteration timer, and recorded once as `ci_wait`. A
pending-at-deadline result is unknown (`ci_green = None`), never `all_passed`, never `tests_failed`, and records no
`FIND-CI` finding. The stage error names pending, failed (with check names) or not measured, and the old catch-all
phrase appears only under a real local test failure.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.9   |
| entropy_actual        | 6.0   |
| impact_actual         | 55.0  |
| cost_actual           | 18.0  |
| learning_value_actual | 4.0   |

## Steps Executed

1. Layout: new `flow/github/ci_wait.py` and `flow/review/ci_outcome.py`; `_wait_for_pending_ci` was removed from `ci.py`
   and the `flow/__init__.py` re-export. Budgets: `github/ci.py` lowered to `193`, new entries `117` and `39`;
   `review/loop.py` stays at `903`, `stages/review.py` at `298`.
2. Wait and budget: `resolve_review_ci_wait_seconds` in `budgets.py` (built on `_read_positive`), `StageTimer.credit`.
3. Loop: the post-review gate calls `settle_pending_ci` on its own policy and credits the waited time back to the
   iteration timer. Deviation from the plan: the wait stays in the post-review gate rather than moving ahead of the
   reviewer dispatch, which would add an extra CI probe per iteration and reshape the loop; the iteration is still never
   charged.
4. Outcomes: `describe_ci_outcome` and `gate_ci_flags`; the stage reports `ci_wait` and `tests_failed` from the loop
   result.
5. Tests: new `tests/test_review_ci_wait.py` (16 tests). Edited old assertions: `tests/test_review_dispatch.py` (a
   failed GitHub check is now named instead of the catch-all), `tests/flow/github/test_ci.py` (fake loop result now
   carries `tests_failed`), and the two `_wait_for_pending_ci` tests in `tests/test_review_loop.py` were replaced by the
   new file.
6. Docs: `docs/git_flow.md` section "CI wait budget and outcomes" and a row plus note in `docs/budgets.md`.

Move-proof refresh (stated per the intent): `tests/flow/fixtures/flow_py_definitions.json` was edited because the
snapshot checks every definition. `_wait_for_pending_ci` moved to `removed_definitions`; the digests of
`run_review_loop` and `run_review_stage` were refreshed and listed as amended; the new definitions in the two new
modules were declared under `added_definitions`. Nothing else in that file changed, and `docs/architecture.md` is
untouched.

## Tests Run

- `uv run pytest apps/sandbox-executor/tests/test_review_ci_wait.py`: 16 passed, `0.38s`.
- `uv run task lint`: passed (ruff check, format check, flow module budgets within budget).
- `uv run task lint-docs`: passed.
- `uv run task test` (full suite, once): `3595 passed, 1 failed` in `31.34s`. The one failure,
  `tests/flow/stages/test_children.py::TestChildLoopWithStubbedPipelines::test_child_payload_fallbacks_and_copies`, also
  fails on the baseline without these changes (checked with the changes stashed), so it is not caused by this work.
