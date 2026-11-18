# Execution Record: E-1795000930-23e326a5-claude-agent-claude-fable-5

## Summary

Implements plan `P-1794976507-654a2c7f-claude-agent-claude-fable-5` for intent `I-1794976491-shortref`:
replace hex-bearing plan/execution IDs with `{ticker}-{model code}` names, key plan files and lookups
by intent path, keep old generations parseable, and migrate the existing `plans/` tree and ledger path
fields under the one-off maintainer exemption.

(In progress.)

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Inventory of flat-key plan/execution ID uses

Sites where a plan or execution ID is constructed or used without its intent/plan context:

- ID construction (writes): `ledger.py` `build_plan_id` / `build_execution_id` /
  `build_stage_disambiguator` (hex source); `ref_names.py` `fit_plan_id` / `fit_execution_id` /
  `original_labels` / `ensure_chain_fits`; callers `entrypoint/planner.py:322`,
  `entrypoint/executor.py:1064`, `flow/stages/plan.py:75`, `flow/stages/execute.py:145`.
- Flat plan-file paths (writes): `entrypoint/planner.py` `plan_file_rel` + `PLAN_FILE_PATTERN`
  (`plans/{plan_id}.md`); `calibration.py:1580` (`plans/{plan_id}_calibration.md`);
  `flow/stages/plan.py:87,162` and `flow/stages/plan_decision.py:57` (fallback
  `plans/{plan_id}.md`); planner prompt templates `scaffold.py:193` and
  `holon-config/prompts/planner.template.md`.
- Flat lookups (reads): `calibration.py` `_parse_predicted_from_plans_ledger` (bare `plan_id`
  ledger match), `_parse_predicted_from_plan_markdown` (flat `plans/{plan_id}.md` candidates);
  `entrypoint/executor.py:1101` (plan row matched by branch tail `plan_id` only);
  `flow/calibrate_rerun.py:134` (`plans/{plan_id}_calibration.md`);
  `flow/calibrate_rebuild.py:40` `is_calibration_path` (requires exactly one `/`);
  `flow/stages/plan.py:162` and `flow/stages/plan_decision.py` (rows filtered by bare `plan_id`).
- Duplicate check: `ledger.py` `validate_ledger_unique_ids` (bare-ID collision across distinct
  parents; same-parent repeats are legitimate superseding rows appended by
  `flow/git_refs.py:_append_ledger_row` and `flow/stages/plan_decision.py:_backfill_plan_row`),
  called from `entrypoint/planner.py:592`, `flow/git_refs.py:291`, `flow/pipeline.py:214`.
- Append-only gates for the migration commit: `tests/test_ledger_append_only.py` guards runtime
  appends (byte-prefix assertion in `atomic_ledger_append`), not git history; the migration
  rewrites the ledgers outside `atomic_ledger_append`, so no guard weakening is needed and the
  guard keeps rejecting non-exempt rewrites. `scripts/pre_commit_check.py` checks credentials
  only.
- Checkpoints (`flow/checkpoint.py`) already store full branch paths; CLI `calibrate` takes the
  full execution branch; CLI ledger queries filter by substring, not bare ID — no change needed
  beyond the shared helpers.

## Tests Run
