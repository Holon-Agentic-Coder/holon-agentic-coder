# Execution Record: E-1794663558-0405d999-claude-agent-claude-opus-5-5

## Summary

Implements plan `P-1794663411-f5308cfe-claude-agent-claude-opus-5-5`: touch-set admission before Stage 3 (refusal
naming pull requests, branches and files, with a recorded `--allow-overlap` override) and enforcement after it (an
out-of-set execution diff fails Stage 3 with `touch_set_violation` and returns once to Stage 2).

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

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
  flight (false positive, overridable), and local flows compare declared sets only (no real diff). A fork head that
  is flow-shaped is read like any other head; its plan row is usually absent locally, so only its diff is compared.

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
