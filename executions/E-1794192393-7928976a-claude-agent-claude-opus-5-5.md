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
  `aa8d5f19a171ce97e8dd5ecf598a57fe86f976c3` (6,159 lines). The branch was not rebased: the execution protocol forbids it, and
  the branch is the direct child of the plan commit.
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

### Step 2: Generate the `flow/` package and remove `flow.py`

- A scratch generator (`/tmp/flowsplit/generate.py` with `manifest.py`, outside the repository and not committed)
  copies each statement's exact source slice, with its directly preceding comments, into its target module in original
  order. The three-line `# ---` section banners are dropped, because each module docstring now names its section.
- Each module gets a docstring, `from __future__ import annotations`, the imports it loads (copied from the `flow.py`
  header, plus absolute `sandbox_executor.flow.<sub>` imports), and `logger = logging.getLogger(__name__)` where it
  logs. Then `ruff check --fix --select F401,I001,RUF022` and `ruff format` normalise the imports. No body was touched.
- 23 modules plus four `__init__.py` files. `flow/__init__.py` re-exports all 148 bound names and the five imported names
  reached as `flow.<name>` (`SKIP_PUSH_DISPATCH_ERROR`, `build_pr_review_consensus_report`, `parse_metrics`,
  `redact_agent_secrets`, `run_git`) through an explicit `__all__` (153 names). It then binds
  `stages.decomposition.PipelineEngine` late.
- `flow.py` was removed with `git rm`. The 19 `("sandbox_executor/flow.py", codes)` entries in
  `tests/test_complexity_gate.py` `BASELINE_SNAPSHOT` were re-keyed to the submodule carrying each suppression, with the
  same multiset of codes. The baseline counts are untouched (the file holds `184` and `90`, not the `187` and `91` the
  plan quoted).
- Checks: imports of `sandbox_executor.flow`, `sandbox_executor.cli`, `sandbox_executor.entrypoint.review_loop` and
  `sandbox_executor.review_target` succeed. `ruff check .` and `ruff format --check .` are clean, and
  `test_complexity_gate.py` passes (5 passed). A preview AST comparison found 0 missing and 0 differing statements.
