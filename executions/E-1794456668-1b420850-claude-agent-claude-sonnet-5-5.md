# Execution Record: E-1794456668-1b420850-claude-agent-claude-sonnet-5-5

## Summary

Implementing the Stage 5 final-head CI check (`flow/github/ci_final_head.py`), its hook in `calibrate.py` and the flow
summary, hermetic tests and docs. Complete. All gates pass except one failure that also occurs on the untouched baseline.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.95  |
| entropy_actual        | 6.5   |
| impact_actual         | 60.0  |
| cost_actual           | 15.0  |
| learning_value_actual | 4.0   |

## Steps Executed

1. Step 1-2: added `flow/github/ci_final_head.py` (`classify_final_head`, `check_final_head_ci`, `gh_step_lookup`,
   `format_final_head_ci`), reusing `probe_ci_checks`, `settle_pending_ci` and `_wait_for_ci_registration`. Added `link`
   to the `gh pr checks --json` fields in `github/ci.py` so the run id can be parsed (one existing test updated).
2. Step 3: `calibrate.py` calls the check after the execution-branch push and stores `payload["final_head_ci"]`; the
   status stays `SUCCESS`. `cli/commands/flow.py` prints the line in the summary (a minimal scope extension). Budgets:
   `stages/calibrate.py` 316 to 323 with a reason, new entry for `github/ci_final_head.py`. New public names added to
   `KNOWN_UNEXPORTED` in `test_package_exports.py`.
3. Step 4: `tests/test_calibrate_final_head_ci.py` (12 tests).
4. Step 5: `docs/git_flow.md` (new "Final-head CI check (Stage 5)" section) and `docs/budgets.md`.
5. Step 6: gates run below.

## Tests Run

- `uv run pytest apps/sandbox-executor/tests/test_calibrate_final_head_ci.py`: 12 passed, 0.08 s.
- `uv run task lint`: passed (41 flow modules within budget). `uv run task lint-docs`: passed.
- `uv run task test`: 3629 passed, 1 failed in 33.37 s. The failure,
  `test_children.py::...::test_child_payload_fallbacks_and_copies`, also fails with these changes stashed, so it is
  pre-existing and unrelated.
- No repeated runs: no timing measurement is involved.
