# Execution Record: E-1794447618-d6053952-claude-agent-claude-sonnet-5-5

## Summary

Make the review diff path fetch-free inside a mounted role container, and fetch the plan, execution and base branches on
the host before the review-loop container is dispatched.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.5   |
| entropy_actual        | 4.0   |
| impact_actual         | 60.0  |
| cost_actual           | 10.0  |
| learning_value_actual | 3.0   |

## Steps Executed

- Step 1: added `review_target.workspace_is_mounted()` (reads `HOLON_TARGET_WORKTREE` against `SANDBOX_WORKSPACE_MOUNT`,
  so no entrypoint import enters the flow engine). `_fetched_branch_ref` and `_pr_head_ref` no longer fetch when
  mounted. A missing `refs/remotes/origin/<branch>` ref is refused with "the host must fetch it before dispatch".
- Step 2: audit found two more in-container fetches: `review_target._resolve_branch` (via
  `_resolve_review_commit(fetch=True)`) and `flow/review/loop.py` (`_resolve_review_commit(fetch=...)`). Both are now
  gated on `workspace_is_mounted()`. `git_refs._execution_effective_diff`, `flow/stages/children.py` and
  `flow/github/merge.py` are host-side and unchanged. `review/loop.py` budget raised 906 to 908, reason recorded in
  `flow_module_budgets.json`.
- Step 3: new `cli/commands/review_fetch.py`; `_container_review_worktree` fetches plan, execution and base once each
  via `git_flow.fetch_origin_branch` before dispatch. Base is the PR base, else the intent's `target_branch` from the
  fetched plan branch. Skipped without an `origin` remote. A failure exits 1 before the container is dispatched.
- Step 4: tests in `tests/test_review_target.py` and `tests/test_review_dispatch.py`.
- Step 5: `docs/git_flow.md` updated.

## Tests Run

- `uv run pytest` on `test_review_target.py`, `test_review_dispatch.py` and `test_cli.py`: 387 passed.
- `uv run task test` (once): 3567 passed, 1 failed in about 33s. The failure is
  `test_children.py::TestChildLoopWithStubbedPipelines::test_child_payload_fallbacks_and_copies`, which also fails on
  the base commit `aef3064` and is unrelated; it was not worked around.
- `uv run task lint`: passed, including the flow module budgets. `uv run task lint-docs`: passed after Prettier.
