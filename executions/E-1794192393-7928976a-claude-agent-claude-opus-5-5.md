# Execution Record: E-1794192393-7928976a-claude-agent-claude-opus-5-5

## Summary

Implementation of plan `P-1794192045-c23d4516-claude-agent-claude-opus-5-5`: split
`apps/sandbox-executor/src/sandbox_executor/flow.py` into the `sandbox_executor.flow` package (move only). In progress.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Map definitions, dependency graph, patch lookups and baselines

- Source blob for the snapshot: `HEAD:apps/sandbox-executor/src/sandbox_executor/flow.py` =
  `aa8d5f19a171ce97e8dd5ecf598a57fe86f976c3` (6,159 lines). No rebase was needed: the branch sits on the plan commit and
  this sandbox has no network access to fetch `main`.
- `ast` pass: 124 top-level `def`/`class` nodes (confirmed), plus 26 module-level assignments (including `logger` and
  the `CHECKPOINT_MIGRATION_REGISTRY[(0, 1)]` registration), so 150 statements. No `globals()` or `sys.modules` lookups.
- The manifest follows the intent's clusters, with these measured adjustments:
  - `_support_legacy_repo_dir` goes to `context.py`, because it decorates `FlowContext` and is used nowhere else.
    Leaving it in `checkpoint.py` would create a `context <-> checkpoint` cycle.
  - `CURRENT_CONTEXT_SCHEMA_VERSION` (used by `context` and `checkpoint`) and `CI_REGISTRATION_POLL_SECONDS` (used by
    `github/ci` and `review/loop`) go to `constants.py`. Every other constant has a single user and goes with it:
    `EXECUTOR_GIT_ENV` to `git_refs`, `FAILED_EXECUTION_STATUSES` to `stages/execute`, `POSTING_ERROR_CONTEXT_MAX_CHARS`
    and `_SEVERITY_RANK` to `review/records`, `TESTS_NOT_MEASURED_ERROR` to `stages/review`. The two constants with no
    user inside the engine go with their subject: `REVIEW_GIT_ENV` to `stages/review` and `HOST_REVIEW_REFUSAL` to
    `review/workspace`.
- SCC check: after these adjustments there is exactly one cycle, `stages/decomposition -> pipeline -> stages/execute ->
  stages/decomposition` (`_run_decomposed_plan` uses `PipelineEngine`). It is broken by late binding in
  `flow/__init__.py`, plus a `TYPE_CHECKING` import in `decomposition.py` so ruff `F821` stays clean without a `# noqa`.
- Patch lookup map: 45 distinct `"sandbox_executor.flow.<name>"` strings plus 14 `setattr`/`patch.object` names. The
  per-site map is in Step 4.
- Baseline from a temporary worktree of `HEAD` (`/tmp/flowsplit/base`): 3,131 collected node IDs, 337 of them in
  `test_flow.py` (280 test functions once parametrisation is folded). The 15 affected files gave 1774 passed in 6.48s
  (`-n auto --dist worksteal`).
- Environment note: the sandbox had no `uv` (installed with `pip install --user uv`), and `PYTHONPATH=/home/holon/src`
  points at the executor's own copy of `sandbox_executor`, which shadows the workspace. Every command below runs with
  `PYTHONPATH` unset.
