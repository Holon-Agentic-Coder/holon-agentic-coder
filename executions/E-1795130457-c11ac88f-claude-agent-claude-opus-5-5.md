# Execution Record: E-1795130457-c11ac88f-claude-agent-claude-opus-5-5

- **Plan ID:** P-1795087811-3604f487-claude-agent-claude-fable-5
- **Intent ID:** I-1794976491-shortref
- **Agent:** claude-agent/claude-opus-5-5

## Summary

In progress. Continuation of the failed execution `E-1795088115-4e818090-claude-agent-claude-fable-5`, whose Steps 1–3
(call-site inventory, agent tickers and model codes, hexless IDs with collision safety) are already committed on this
branch, and whose pre-push merge conflict in `flow_module_budgets.json` was resolved by the merge commit `58949669`. This
run continues from Step 4.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Steps 1–3 (previous execution)

Reviewed commits `a2a96c6a` (Step 1), `7001a7c9` (Step 2) and `2302cfa2` (Step 3) and the previous execution notes; not
redone.

### Step 4: Plan files and calibration reports under `plans/{intent}/` mirroring branch nesting

- New focused module `plan_paths.py` owns the layout (pure): `plans/{parent}/{plan id}.md` with the calibration report
  beside it, where the parent is the full bare branch above the plan, so a root intent, a child intent
  (`{parent}--{index}-{slug}`), a child intent nested below a plan segment, and a plan nested under another plan all
  mirror their branch. Both directions are there (`split_plan_branch`, `plan_branch_from_path`), plus the flat legacy
  candidates and a tightened `PLAN_FILE_PATTERN` (directories must be `I-`/`P-` branch segments or `legacy`, so a `..`
  or foreign directory never matches).
- Writers moved to it: `entrypoint/planner.py` (plan file and the new `{plan_file}` prompt placeholder; a plan file
  already on disk under the intent is a taken name, so a new plan never overwrites one), `calibration.py`
  `run_calibrate` (report beside the plan), `flow/stages/calibrate.py` fallback report, `flow/calibrate_rerun.py`
  report lookup, `flow/calibrate_rebuild.py` (`is_calibration_path` accepts nested reports), `flow/stages/plan.py` and
  `plan_decision.py` (plan file of the row, branch-implied fallback), `scaffold.py` and
  `holon-config/prompts/planner.template.md` (`{plan_file}` placeholder).
- The plan-row reads in those stages and the executor already key on the intent and plan id together
  (`plan_paths.is_row_of_plan`), because the path and the row are resolved in the same lines; Step 5 completes the
  keyed lookups.
- `calibration.extract_branch_components` keeps the full parent (child intents and nested plans) and reports
  `plan_parent`.
- Budgets: `calibrate_rebuild.py` +1 (import); `stages/plan.py`, `stages/plan_decision.py`, `stages/calibrate.py`
  lowered to their new sizes. Complexity baseline ratcheted down by one (`generate_calibration` no longer needs
  `PLR0912`).
- Tests: new `tests/test_plan_paths.py` (22 tests: the nesting cases, round trips, legacy candidates, pattern rejection,
  intent-keyed rows); updated path expectations in `test_calibration.py`, `test_calibrate_rerun.py`,
  `test_calibrate_rebuild.py`, `flow/test_pipeline.py`, `flow/test_git_refs.py`, `flow/test_calibrate_nested_refs.py`,
  `flow/stages/test_decomposition.py`, `test_planner.py`, `test_unique_stage_ids.py`, `test_prompt_templates.py`.
- Verified: targeted suites green; full suite `-n auto --dist worksteal`: 4406 passed, 1 skipped in `17.58s`;
  `uv run task lint` clean.

### Step 5: Key every lookup on (intent, plan ID); executions on their full path

- Plan rows: `plan_paths.is_row_of_plan` (plan id *and* bare intent branch) now backs every plans.jsonl read — the
  executor's plan lookup (`entrypoint/executor.py`), Stage 2 adoption (`flow/stages/plan.py`), the Stage 3 decision
  read (`flow/stages/plan_decision.py`), touch-set admission (`flow/in_flight.py`), calibration's predicted metrics
  (`calibration.parse_predicted_metrics` now takes the plan branch; plan-file fallback tries the nested layout then
  the flat legacy file) and the review-loop CLI context (`entrypoint/review_loop.py`, which matched a plan id as a
  substring of the head branch).
- Execution rows: `plan_paths.is_row_of_execution` keys an execution on its id and its plan when the branch is known;
  `flow/git_refs._latest_execution_summary` takes the execution branch (callers in `stages/execute.py`,
  `stages/integration.py`, `partial_salvage.py`); `calibration_ev.is_matching_record` never matches a record of
  another plan, whatever its id (review rows of the plan still match through the plan); a bare execution id given to
  `holon calibrate` resolves only when exactly one plan owns it (`calibration._unique_plan_branch_of_execution`).
- Duplicate check (`ledger.validate_ledger_unique_ids`): plan ids are keyed on the full intent path; a repeated
  (intent, plan id) pair at one `ledger_revision` fails, a superseding row (higher revision) passes, and the same plan
  id under two intents passes. Execution rows are keyed, not repeat-checked (the real `executions.jsonl` legitimately
  repeats two (plan, execution) pairs at revision 1 from reruns). The real `plans.jsonl` passes.
- Flow checkpoints already store full `plan_branch`/`execution_branch` paths (no schema change, so resume from an older
  checkpoint is unaffected). `cli/` has no plan or execution lookup of its own (`holon calibrate` goes through
  `generate_calibration`).
- Residual: the knowledge index (`knowledge/index.py`) indexes action rows by `execution_id` alone for consult
  analytics; action rows carry no plan, so two same-named executions would share index entries. Not a lookup of a
  plan or execution artefact; left as is.
- Budgets: `git_refs.py` +1, `stages/execute.py` +2 (recorded in the budgets description).
- Tests: same-second twin intents resolve to their own plan rows and plan files (`test_calibration.py`,
  `test_unique_stage_ids.py`, `test_plan_paths.py`), repeated pair rejected, superseding row and cross-intent id
  permitted, execution rows never matched across plans, ambiguous bare execution id resolves to nothing, the newer
  same-named execution row of another plan never wins.
- Verified: full suite `-n auto --dist worksteal`: 4407 passed, 1 skipped in `18.18s`; `uv run task lint` clean.

### Step 6: Remove obsolete agent/model shortening; enforce the 32-character slug limit

- Already in place from Step 3 of the previous execution (re-verified by grep): the label-digest segment fitting and
  `original_labels` are gone from `ref_names.py`, new plan and execution rows carry no `original_agent`/`original_model`
  (readers never depended on them), and slug shortening plus the length check are kept.
- Root slug: new `ref_names.root_intent_slug` owns the rule. A slug supplied in the intent file over 32 characters is
  rejected when the intent is opened, with the limit and the actual length in the message, before any branch exists
  (Stage 1 in `flow/stages/intent.py`, and `entrypoint/intent_creator.py`); an intent that already records its branch
  (a resume) is not re-judged. A slug derived from the goal or description is cut to 32 characters at a word boundary
  (previously a 40-character prefix of the text).
- Child slugs: `decomposition_rule._sub_intent_dicts` cuts derived sub-intent slugs to 32 characters at a word boundary
  (previously 48), duplicate suffix included (`truncate_derived_slug` takes a `limit`); `flow/child_progress.py` applies
  the same cut to a recorded slug when the child branch is first derived, so an older plan row's long slug also obeys
  the limit. Deeper nesting still falls back to the kept digest shortening (`original_slug` recorded).
- Ref-limit acceptance test (`test_two_levels_of_32_character_nesting_fit_without_shortening`, from Step 3) computes
  the calibrated branch of a root plus two nested child intents with 32-character slugs from the real grammar and
  asserts it fits 255 unshortened; `test_deeper_nesting_falls_back_to_slug_shortening_and_the_length_check` covers the
  fallback.
- Budget: `stages/intent.py` lowered from 228 to 220.
- Tests: Stage 1 rejection and derived-slug cut, `root_intent_slug` resume and sanitising cases
  (`test_ref_names.py`), intent-creator rejection before any branch (`test_intent_creator.py`), sub-intent slug cut and
  duplicate suffix within the limit (`test_executor.py`), child slug cut and deep-parent shortening
  (`flow/test_child_progress.py`).
- Verified: full suite `-n auto --dist worksteal`: 4414 passed, 1 skipped in `18.00s`; `uv run task lint` clean.
