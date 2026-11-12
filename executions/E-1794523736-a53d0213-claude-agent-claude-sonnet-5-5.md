# Execution Record: E-1794523736-a53d0213-claude-agent-claude-sonnet-5-5

## Summary

Added `review_failure.py` (classification, redacted bounded excerpt, reset-time parsing, wait ceiling), wired it into `review_ensemble.py` (named stop reason, excerpt in the log and failure reason, one bounded quota wait and retry per ensemble), added hermetic tests and docs.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.95   |
| entropy_actual        | 4.0   |
| impact_actual         | 55.0   |
| cost_actual           | 10.0   |
| learning_value_actual | 4.0   |

## Steps Executed

- Step 1: added `review_failure.py` with `FailureCause`, `ReviewerFailure`, `classify_failure`, `parse_reset_time`, `redacted_excerpt`, `describe`, `quota_wait_seconds`, `wait_ceiling`; reuses `preflight._PATTERNS`.
- Step 2: `_log_turn_completion` and `_missing_report_reason` name the cause and carry the redacted excerpt; `ReviewerResult.retry_after_seconds` added (not serialised into the flow summary; the reason text carries the cause).
- Step 3: `run_ensemble` takes an injectable `sleep`, waits once per ensemble for a quota reset within `HOLON_REVIEW_QUOTA_WAIT_SECONDS` (default `900`), credits the stage timer and retries that reviewer once. No existing handling was found.
- Step 4: added `tests/test_review_failure.py` (23 tests).
- Step 5: documented in `docs/review_consensus.md` and `docs/budgets.md`.

## Tests Run

- `uv run pytest apps/sandbox-executor/tests/test_review_failure.py`: pass.
- `uv run task test`: `3782 passed, 1 skipped` in 34.78s.
- `uv run task lint`: pass.
- `uv run task lint-docs`: the 38 prettier warnings are pre-existing (same count without my changes).
