# Execution Record: E-1794461599-c7922f92-claude-agent-claude-sonnet-5-5

## Summary

Name failed tests, keep the head and tail, and write the full redacted output to `.holon/review/verification/` when the
Stage 4 pre-push test step fails.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.9   |
| entropy_actual        | 3.0   |
| impact_actual         | 50.0  |
| cost_actual           | 8.0   |
| learning_value_actual | 3.0   |

## Steps Executed

- Step 1: confirmed `_bounded_error_context` caps at `2000` characters, `run_cmd` accepts `env`, `.holon/` is ignored.
- Step 2: added `entrypoint/review_failure_detail.py` (id extraction, names block, head-and-tail excerpt, file write).
- Step 3: wired `verification_error(..., workspace=)` and `-rf` via `PYTEST_ADDOPTS` in `_verify_before_push`.
- Step 4: added `tests/test_review_failure_detail.py` (7 tests).
- Step 5: documented in `docs/testing/testing_guide.md`.

## Tests Run

- `uv run pytest` on the new and resolver-failure tests: `26 passed` in `0.43s`.
- `uv run task lint`, `uv run task lint-docs`: pass.
- `uv run task test`: `3625 passed, 1 failed` in `33.92s`. The failure,
  `tests/flow/stages/test_children.py::...::test_child_payload_fallbacks_and_copies`, also fails on the unmodified tree
  (checked with `git stash`), so it is pre-existing and unrelated.
