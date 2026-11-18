# Execution Record: E-1794976816-388fae94-claude-agent-claude-fable-5

## Summary

Implementing plan `P-1794976507-654a2c7f-claude-agent-claude-fable-5` for intent `I-1794976491-shortref`:
replace hex-bearing plan/execution IDs with `P-{epoch}-{ticker}-{model code}` names, key plan files and
lookups by intent path (`plans/{intent}/{plan id}.md`), keep both old generations parseable, enforce the
32-character slug limit, and run the one-off migration of `plans/` and the two ledger path fields under
the maintainer's exemption.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Inventory of flat-key plan/execution ID uses (INFO_GATHERING)

ID construction (hex sources):

- `ledger.py`: `build_plan_id`, `build_execution_id`, `build_stage_disambiguator` (8-char hex).
- `ref_names.py`: `fit_plan_id`, `fit_execution_id`, `original_labels`, `_DISAMBIGUATOR_LENGTH`,
  `_SEGMENT_FIXED_LENGTH`.
- Callers: `entrypoint/planner.py:322`, `entrypoint/executor.py:1064`, `flow/stages/plan.py:75`,
  `flow/stages/execute.py:145`.

Flat plan-file path joins (`plans/{plan_id}.md`):

- `entrypoint/planner.py` `plan_file_rel` + `PLAN_FILE_PATTERN`.
- `flow/stages/plan.py` (dry-run payload and row fallback), `flow/stages/plan_decision.py` (row fallback).
- `calibration.py` `_parse_predicted_from_plan_markdown` candidates and the report's plan-reference line.

Flat calibration-report paths (`plans/{plan_id}_calibration.md`):

- `calibration.py` `run_calibrate` write site; `flow/stages/calibrate.py` fallback report write;
  `flow/calibrate_rerun.py` `report_staleness` read; `flow/calibrate_rebuild.py` `is_calibration_path`
  (`count("/") == 1` check).

Bare-ID ledger lookups:

- `entrypoint/executor.py` plan row match by `plan_id` suffix only.
- `calibration.py` `_parse_predicted_from_plans_ledger` row match by `plan_id` only;
  `generate_calibration` execution-row lookup by `execution_id` (resolves `plan_branch` — already full-path).
- `ledger.validate_ledger_unique_ids`: rejected the same ID across distinct parents (inverted by Step 6).

Branch discovery suffixes carrying agent/model labels: `flow/stages/plan.py` and `flow/stages/execute.py`
`preferred_suffix=f"-{agent}-{model}"`.

Checkpoints (`flow/checkpoint.py`, `flow/child_progress.py`) already store full branch paths — no flat keys.

Intent-open sites for ticker/slug gates: `flow/stages/intent.py` (before `resolve_root_intent_branch`),
`entrypoint/intent_creator.py`, `cli/commands/intent.py` (host, before dispatch), `cli/__init__.py run_doctor`.

Append-only gating: `test_ledger_append_only.py` guards process writes (prefix guard) and verifies precheck /
reconcile leave ledgers byte-identical; `scripts/pre_commit_check.py` checks credentials/scratch/spelling only.
Neither forbids a maintainer-exempted migration commit, so the migration needs no guard weakening.

Hypothesis check: all flat-key sites are inside the predicted set (`ref_names.py`, `ledger.py`,
`calibration.py`, `flow/stages/*`, `flow/calibrate_*`, entrypoints, CLI). No site outside it was found in two
sweeps (grep for hex construction; grep for `plans/` path joins).

## Tests Run

(pending)
