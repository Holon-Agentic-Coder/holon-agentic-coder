# Execution Record: E-1795087654-dd8d6294-claude-agent-claude-fable-5

## Summary

Continuation of the failed execution `E-1795000930-23e326a5-claude-agent-claude-fable-5` (last commit `ed611238`) for
plan `P-1794976507-654a2c7f-claude-agent-claude-fable-5`, intent `I-1794976491-shortref`. The prior run completed and
committed Steps 1–9 (its notes record them through Step 9), plus Step 10 (documentation sweep, commit `c61346eb`) and a
Step 11 lint-fix commit (`ed611238`), but died before running and recording the verification gates. This run resumed at
Step 11, ran the affected tests, the full suite once, `uv run task lint` and `uv run task lint-docs`, and recorded the
results. No further code changes were needed: all gates pass on the inherited tree as committed. Acceptance criterion 8
is therefore satisfied, completing the plan.

Actual metrics are reported for the plan as a whole (both executions combined), since this run completes the plan whose
predictions they calibrate against.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.95  |
| entropy_actual        | 12.5  |
| impact_actual         | 88.0  |
| cost_actual           | 32.0  |
| learning_value_actual | 6.5   |

`success_actual = 0.95`: all eight acceptance criteria verified green (tests, lint, lint-docs), with the residual 0.05
reserved for the human review gate on the root-intent PR. `entropy_actual = 12.5` is slightly under the 13.6 predicted —
the migration's legacy bucket (`9.4%`) stayed well under the abort threshold and no step was discarded.
`cost_actual = 32.0` is marginally over `cost_pred = 30.0` because the first execution died at the verification gate and
this continuation re-established context before finishing Step 11.

## Steps Executed

### Steps 1–10 (inherited from E-1795000930, commits `1d7261ca`..`c61346eb`)

Completed by the prior execution on this branch and verified present in its commits: flat-key inventory (Step 1), agent
ticker registry with doctor and intent-time enforcement (Step 2), pure `model_code` derivation in `plan_paths.py` (Step
3), generation-3 ID builders with three-generation parser and same-second collision rule (Step 4), nested plan-file
paths for new writes (Step 5), lookups keyed on (intent, plan ID) / full path with labelled old-generation fallbacks
(Step 6), removal of agent/model label digests and `original_agent` / `original_model` for new rows (Step 7),
32-character slug limit with intent-time rejection and ref-limit fit proof (Step 8), the one-off `plans/` migration (265
moves, 143 `plan_file` + 99 `calibration_report_path` rewrites, legacy bucket `9.4%`, idempotent second run) (Step 9),
and the documentation sweep to generation-3 names (Step 10). Details are in
`executions/E-1795000930-23e326a5-claude-agent-claude-fable-5.md`.

### Step 11: Full test suite and lint gates (this execution)

Ran the affected tests first, then the full suite once, then both lint tasks, exactly as the plan's test-run discipline
requires (no repeated runs: the intent measures no performance or timing behaviour, so N = 1 per command). All four
commands passed with no code changes required; the only edit this run made is this execution-notes file (plus its
prettier formatting). Results are in `## Tests Run`.

## Tests Run

All commands run from the repository root on the final tree (`ed611238` plus this notes file). The full suite runs via
`uv run task test` with `-n auto --dist worksteal`. Single run per command (N = 1; nothing timing-sensitive is being
measured, so the median equals the single recorded figure).

| command                                 | result                            | pytest time |
| --------------------------------------- | --------------------------------- | ----------- |
| `uv run pytest <9 affected test files>` | 331 passed, 0 failed              | 5.42s       |
| `uv run task test` (full suite)         | 4366 passed, 2 skipped, 0 failed  | 17.34s      |
| `uv run task lint`                      | pass (ruff, format, flow budgets) | n/a         |
| `uv run task lint-docs`                 | pass (after formatting this file) | n/a         |

The nine affected test files: `test_ref_names.py`, `test_agent_defaults.py`, `test_ledger_query.py`,
`test_calibration.py`, `test_calibrate_rerun.py`, `test_ledger_append_only.py`, `test_plan_paths.py`,
`test_plan_migration.py`, `test_unique_stage_ids.py`. Median pytest-reported time: affected run `5.42s`, full suite
`17.34s` (one run each). The first `uv run task lint-docs` invocation flagged only this newly created notes file;
`prettier --write` was applied to it and the task re-run to confirm a clean pass.
