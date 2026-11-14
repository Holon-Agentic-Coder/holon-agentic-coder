# Execution Record: E-1794663558-0405d999-claude-agent-claude-opus-5-5

## Summary

Implements plan `P-1794663411-f5308cfe-claude-agent-claude-opus-5-5`: touch-set admission before Stage 3 (refusal naming
pull requests, branches and files, with a recorded `--allow-overlap` override) and enforcement after it (an out-of-set
execution diff fails Stage 3 with `touch_set_violation` and returns once to Stage 2).

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 1.0   |
| entropy_actual        | 9.5   |
| impact_actual         | 82.0  |
| cost_actual           | 14.0  |
| learning_value_actual | 5.0   |

## Steps Executed

### Step 1: entry-against-entry overlap helpers (`touch_set.py`)

- Added `entries_overlap`, `overlapping_entries`, `files_in_set` and `out_of_set_files`, sharing one `_code_paths`
  normaliser with `score_touch_set`. Glob against directory or glob uses the conservative literal-prefix rule; metadata
  entries never overlap. Module docstring now names the admission and enforcement users.
- Tests in `tests/test_touch_set.py` cover literal, directory, `*`, `**`, conservative glob and metadata cases.

### Step 2: in-flight discovery (`flow/in_flight.py`, exploratory)

- `open_flow_pull_requests`: one `gh pr list --state open --limit 50 --json number,headRefName,baseRefName`, keeping
  heads shaped like a flow execution branch (`I-.../P-.../E-.../_`), then `gh pr diff <n> --name-only` per kept pull
  request (metadata dropped). `local_in_flight_flows`: `git worktree list --porcelain`, then every
  `.holon/flow/checkpoint-*.json` of each other worktree whose current stage is execute or review and whose plan branch
  still resolves. `discover_in_flight_flows` excludes the run's own root intent and lists a local flow that also has an
  open pull request once. `read_plan_touch_set` reads the newest plan row through the existing ledger read helpers.
- Any `gh` or `git` failure returns `AdmissionUnavailable(reason)`; runners are parameters (the `gh` default is the
  existing `github/merge.py` `run_gh` seam).
- Learning: on fixtures (non-flow head, own-lineage head, a second worktree holding live, deleted-plan, still-planning,
  own-lineage and unparseable checkpoints) discovery classified every case correctly, so the checkpoint half was kept.
  Known gaps: a checkpoint left at execute or review by an abandoned run whose plan branch still exists counts as in
  flight (false positive, overridable), and local flows compare declared sets only (no real diff). A fork head that is
  flow-shaped is read like any other head; its plan row is usually absent locally, so only its diff is compared.

### Step 3: admission decision, Stage 3 gate and `--allow-overlap`

- New `flow/admission.py`: `check_admission` (own declared set against each in-flight flow's declared set and real pull
  request diff; a missing touch set and unavailable discovery are refused, fail closed), `format_refusal` (names pull
  request, branch and files and points to `--allow-overlap`) and `admission_stage_failure` (the Stage 3 hook: refusal
  with `admission_refused`, `conflicts`, `admission_reason` and `dispatched: false`; dry run only logs; an override
  appends one row and is itself refused if that row cannot be written).
- `stages/execute.py` calls the hook after the plan budget and sub-intent depth checks and before child-branch
  snapshotting and dispatch (one more line logs it in a dry run). `FlowContext.allow_overlap` round-trips with a false
  default; `holon flow --allow-overlap` sets it.
- Override row: `entry_type` and `kind` both `admission_override` on the plan branch's `executions.jsonl`, with
  `plan_id`, `plan_branch`, `intent_id`, `conflicts`, `reason`, `recorded_by: "flow"` and `created_at`, no
  `execution_id`. Every summary reader filters by `entry_type` (`parse_summary_records`, `_latest_execution_summary`,
  `query_ledger(entry_type=...)`), so `ledger.py` needed no new filter; a test pins that.
- Deviation from the touch set: `tests/conftest.py` gained an autouse `stub_stage3_admission` fixture that admits every
  Stage 3, so the existing flow tests never reach the host `gh`; the admission tests restore the real check with fake
  runners.

### Step 4: enforcement after Stage 3 and one bounded re-plan

- New `flow/stages/touch_set_guard.py`: `touch_set_violation` (out-of-set non-metadata files via `out_of_set_files`;
  nothing when the plan declares no set), `touch_set_violation_result` (superseding `failed` summary row with
  `failure_reason: "touch_set_violation"`, payload `touch_set_violation` and `replan_requested: true`) and
  `superseding_failure_row`, the row builder moved out of the empty-diff guard so both guards share it.
- `stages/execute.py`: the empty-diff guard now calls `superseding_failure_row`; the enforcement hook runs after the
  container status checks (so a failed or empty execution keeps its existing handling and resume path) and before the
  success result. The file shrank to 415 lines, inside its 416 budget.
- `pipeline.py`: `_replan_after_over_budget` now re-plans for `over_budget` or `touch_set_violation`
  (`_replan_requested`), sharing `MAX_REPLAN_ATTEMPTS`; `_replan_feedback` and `_replan_exhausted_error` split the text
  so every method stays within the complexity limits, and the exhausted message names the files. Re-planning re-runs
  Stage 3, so admission runs again before the next dispatch.
- Deviation from the touch set: `flow/stages/children.py` passes `allow_overlap` to child intents and
  `tests/flow/stages/test_decomposition.py` classifies the new field as inherited (its field-classification test
  requires every `FlowContext` field to be listed).

### Step 5: hermetic tests

- `tests/test_touch_set.py` (Step 1 cases), `tests/flow/test_in_flight.py` (fake `gh`, a second worktree with live,
  stale, still-planning, own-lineage and unparseable checkpoints, `gh` and `git` failures),
  `tests/flow/test_admission.py` (declared-set and real-diff overlap, metadata-only overlap admitted, missing touch set
  refused, fail-closed discovery, refusal payload, exactly one override row, dry run, ledger readers ignore the override
  row).
- `tests/flow/stages/test_execute_touch_set.py`: through `PipelineEngine` with fake role agents whose plan rows carry a
  touch set: refusal before the executor is dispatched, `--allow-overlap` dispatching once with one override row,
  metadata-only extras passing, an out-of-set file returning to Stage 2 once and succeeding with a widened set, and a
  persistent violation failing after `MAX_REPLAN_ATTEMPTS` with the superseding `touch_set_violation` row.
- `tests/flow/test_pipeline.py`: stubbed-stage re-plan once and the exhausted message, plus `--allow-overlap` reaching
  the context; `tests/flow/test_context.py`: `allow_overlap` round-trips and defaults to false for old checkpoints.
- No test reaches a real `gh`, network or role container: discovery takes injected runners, and the suite-wide stub
  admits Stage 3 elsewhere.

### Step 6: documentation

- `docs/touch_set.md`: the "will read the same rows later" sentence is replaced by sections on Stage 3 admission (what
  counts as in flight, what is compared, the refusal and fail-closed cases, the stale-checkpoint gap), `--allow-overlap`
  and enforcement with the bounded re-plan.
- `docs/ledger_schema.md`: the `admission_override` entry type of `executions.jsonl` and the `touch_set_violation`
  superseding row and Stage 3 payload.
- `npx prettier --check` passes on both docs and these notes; `uv run task lint-docs` still reports 38 other files that
  were already unformatted before this execution (none touched here).

### Step 7: budgets, lint and verification

- `flow_module_budgets.json`: `admission.py` (154), `in_flight.py` (190) and `stages/touch_set_guard.py` (93) added at
  creation size; `__init__.py`, `context.py`, `pipeline.py` and `stages/children.py` raised by 34, 4, 23 and 1 lines;
  `stages/execute.py` lowered by 1 to 415. One explanatory sentence was added to the description. No `noqa` was added or
  widened.
- The first full run caught `test_no_other_module_builds_a_branch_with_a_literal_suffix`: `in_flight.py` built the plan
  branch with a literal `/_`; it now appends `ref_names.INTENT_SUFFIX`.
- Touch-set dogfooding by hand: every changed file is in the plan's `## Touch Set` except `flow/stages/children.py`,
  `tests/conftest.py` and `tests/flow/stages/test_decomposition.py` (explained in Steps 3 and 4) and these notes
  (metadata). `ledger.py`, declared, needed no change. No dependency changed, so `uv.lock` is untouched.

## Tests Run

Single runs only (the intent sets no timing target); every run is listed with its pytest-reported result and time.

| command                                                                                            | result                           | time   |
| -------------------------------------------------------------------------------------------------- | -------------------------------- | ------ |
| `uv run pytest apps/sandbox-executor/tests/test_touch_set.py` (Step 1, first)                      | 1 failed, 76 passed              | 0.12s  |
| `uv run pytest apps/sandbox-executor/tests/test_touch_set.py` (expectation fixed)                  | 78 passed                        | 0.11s  |
| `uv run pytest apps/sandbox-executor/tests/flow/test_in_flight.py`                                 | 7 passed                         | 0.18s  |
| execute, pipeline, context, exports, budgets and CLI tests (Step 3, `-n auto --dist worksteal`)    | 1 failed (budgets), 515 passed   | 3.13s  |
| `uv run pytest apps/sandbox-executor/tests/flow/test_admission.py`                                 | 8 passed                         | 0.31s  |
| `uv run pytest apps/sandbox-executor/tests/flow/test_admission.py` (ledger reader test added)      | 9 passed                         | 0.35s  |
| `uv run pytest apps/sandbox-executor/tests/flow -n auto --dist worksteal` (Step 4)                 | 2 failed, 756 passed             | 8.58s  |
| `uv run pytest apps/sandbox-executor/tests/flow/stages/test_decomposition.py -n auto`              | 79 passed                        | 5.04s  |
| `uv run pytest apps/sandbox-executor/tests/flow/stages/test_execute_touch_set.py -n auto`          | 5 passed                         | 2.59s  |
| plan Step 5 affected-test command (`-n auto --dist worksteal`)                                     | 176 passed                       | 5.23s  |
| plan Step 5 affected-test command, after the lint fix                                              | 176 passed                       | 3.00s  |
| `test_pipeline.py`, `test_decomposition.py` and `test_execute_touch_set.py` after the re-plan tidy | 146 passed                       | 4.47s  |
| `test_ref_names.py` and `test_in_flight.py` after the suffix fix                                   | 35 passed                        | 0.35s  |
| `uv run task test` (full suite, first)                                                             | 1 failed, 4109 passed, 1 skipped | 23.67s |
| `uv run task test` (full suite, final)                                                             | 4110 passed, 1 skipped           | 23.58s |

- Medians: full suite `23.625s` (two runs at different commits), plan Step 5 affected-test command `4.115s`.
- The Step 3 and Step 4 failures were the expected module-budget check (settled in Step 7) and the field-classification
  test of child contexts (fixed in Step 4).
- `uv run task lint`: ruff check, ruff format check, the `uv.lock` check and the flow module budget check all pass.
- `uv run task lint-docs`: the changed docs and these notes pass `npx prettier --check`; the task still reports 38 files
  outside this change that were already unformatted.
