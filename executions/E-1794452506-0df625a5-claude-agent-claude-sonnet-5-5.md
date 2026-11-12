# Execution Record: E-1794452506-0df625a5-claude-agent-claude-sonnet-5-5

## Summary

Stop `_execution_effective_diff` running `git fetch` in a mounted role container; compare the host-fetched
`refs/remotes/origin/` refs instead.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.9   |
| entropy_actual        | 2.0   |
| impact_actual         | 65.0  |
| cost_actual           | 6.0   |
| learning_value_actual | 4.0   |

## Steps Executed

- Step 1: `_execution_effective_diff` in `flow/git_refs.py` now calls `_origin_refs_error`. When `workspace_mounted()`
  it only verifies the `refs/remotes/origin/` refs and names the missing branch (plan first) with "the host must fetch
  it before dispatch"; on the host it fetches as before. The `git_refs.py` budget was raised from `373` to `393`, with
  the reason recorded in `flow_module_budgets.json`.
- Step 2: audit. The `_resolve_review_commit` fetch is already guarded by `fetch=not workspace_mounted()`, and
  `review_target` fetches are already guarded. The remaining `_fetch_branch_prefix*`, `_snapshot_child_branches`,
  `children.py`, `merge.py` and `calibration.py` fetches are host-side and unreachable from the review-loop container,
  so no shared helper was guarded.
- Step 3 and 4: new `tests/test_review_mounted_no_fetch.py` (9 tests, local-path origin, `subprocess.run` guard that
  rejects ssh and records fetch, pull, ls-remote and clone). Includes the `classify_review_diff` entry-path walk and a
  host negative control. 5 tests fail with the fix reverted.
- Step 5: `docs/git_flow.md` updated; budgets and docs recorded.

## Tests Run

- `uv run pytest apps/sandbox-executor/tests/test_review_mounted_no_fetch.py -q`: 9 passed.
- `uv run task lint`, `uv run task lint-docs`: pass.
- `uv run task test`: `3588 passed, 1 failed` in 37.94s. The failure,
  `test_children.py::test_child_payload_fallbacks_and_copies`, also fails on the commit before this change (unrelated).
  An earlier run also showed `test_ledger_lock` failing once; it passed on rerun.
