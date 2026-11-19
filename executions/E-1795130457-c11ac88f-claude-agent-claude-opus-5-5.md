# Execution Record: E-1795130457-c11ac88f-claude-agent-claude-opus-5-5

- **Plan ID:** P-1795087811-3604f487-claude-agent-claude-fable-5
- **Intent ID:** I-1794976491-shortref
- **Agent:** claude-agent/claude-opus-5-5

## Summary

In progress. Continuation of the failed execution `E-1795088115-4e818090-claude-agent-claude-fable-5`, whose Steps 1–3
(call-site inventory, agent tickers and model codes, hexless IDs with collision safety) are already committed on this
branch, and whose pre-push merge conflict in `flow_module_budgets.json` was resolved by the merge commit `58949669`.
This run continues from Step 4.

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
  `run_calibrate` (report beside the plan), `flow/stages/calibrate.py` fallback report, `flow/calibrate_rerun.py` report
  lookup, `flow/calibrate_rebuild.py` (`is_calibration_path` accepts nested reports), `flow/stages/plan.py` and
  `plan_decision.py` (plan file of the row, branch-implied fallback), `scaffold.py` and
  `holon-config/prompts/planner.template.md` (`{plan_file}` placeholder).
- The plan-row reads in those stages and the executor already key on the intent and plan id together
  (`plan_paths.is_row_of_plan`), because the path and the row are resolved in the same lines; Step 5 completes the keyed
  lookups.
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

- Plan rows: `plan_paths.is_row_of_plan` (plan id _and_ bare intent branch) now backs every plans.jsonl read — the
  executor's plan lookup (`entrypoint/executor.py`), Stage 2 adoption (`flow/stages/plan.py`), the Stage 3 decision read
  (`flow/stages/plan_decision.py`), touch-set admission (`flow/in_flight.py`), calibration's predicted metrics
  (`calibration.parse_predicted_metrics` now takes the plan branch; plan-file fallback tries the nested layout then the
  flat legacy file) and the review-loop CLI context (`entrypoint/review_loop.py`, which matched a plan id as a substring
  of the head branch).
- Execution rows: `plan_paths.is_row_of_execution` keys an execution on its id and its plan when the branch is known;
  `flow/git_refs._latest_execution_summary` takes the execution branch (callers in `stages/execute.py`,
  `stages/integration.py`, `partial_salvage.py`); `calibration_ev.is_matching_record` never matches a record of another
  plan, whatever its id (review rows of the plan still match through the plan); a bare execution id given to
  `holon calibrate` resolves only when exactly one plan owns it (`calibration._unique_plan_branch_of_execution`).
- Duplicate check (`ledger.validate_ledger_unique_ids`): plan ids are keyed on the full intent path; a repeated (intent,
  plan id) pair at one `ledger_revision` fails, a superseding row (higher revision) passes, and the same plan id under
  two intents passes. Execution rows are keyed, not repeat-checked (the real `executions.jsonl` legitimately repeats two
  (plan, execution) pairs at revision 1 from reruns). The real `plans.jsonl` passes.
- Flow checkpoints already store full `plan_branch`/`execution_branch` paths (no schema change, so resume from an older
  checkpoint is unaffected). `cli/` has no plan or execution lookup of its own (`holon calibrate` goes through
  `generate_calibration`).
- Residual: the knowledge index (`knowledge/index.py`) indexes action rows by `execution_id` alone for consult
  analytics; action rows carry no plan, so two same-named executions would share index entries. Not a lookup of a plan
  or execution artefact; left as is.
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
  (Stage 1 in `flow/stages/intent.py`, and `entrypoint/intent_creator.py`); an intent that already records its branch (a
  resume) is not re-judged. A slug derived from the goal or description is cut to 32 characters at a word boundary
  (previously a 40-character prefix of the text).
- Child slugs: `decomposition_rule._sub_intent_dicts` cuts derived sub-intent slugs to 32 characters at a word boundary
  (previously 48), duplicate suffix included (`truncate_derived_slug` takes a `limit`); `flow/child_progress.py` applies
  the same cut to a recorded slug when the child branch is first derived, so an older plan row's long slug also obeys
  the limit. Deeper nesting still falls back to the kept digest shortening (`original_slug` recorded).
- Ref-limit acceptance test (`test_two_levels_of_32_character_nesting_fit_without_shortening`, from Step 3) computes the
  calibrated branch of a root plus two nested child intents with 32-character slugs from the real grammar and asserts it
  fits 255 unshortened; `test_deeper_nesting_falls_back_to_slug_shortening_and_the_length_check` covers the fallback.
- Budget: `stages/intent.py` lowered from 228 to 220.
- Tests: Stage 1 rejection and derived-slug cut, `root_intent_slug` resume and sanitising cases (`test_ref_names.py`),
  intent-creator rejection before any branch (`test_intent_creator.py`), sub-intent slug cut and duplicate suffix within
  the limit (`test_executor.py`), child slug cut and deep-parent shortening (`flow/test_child_progress.py`).
- Verified: full suite `-n auto --dist worksteal`: 4414 passed, 1 skipped in `18.00s`; `uv run task lint` clean.

### Step 7: One-off migration of `plans/` and the two ledger path fields

- New standalone script `scripts/migrate_plan_layout.py` (imports `plan_paths`; keeps the one-off out of the engine). It
  computes the whole migration in memory first: every flat plan file and calibration report owned by a `plans.jsonl` row
  moves to `plans/{intent_branch}/{plan id}.md` (or `_calibration.md`), taking the plan id from the row (older plan
  files were named without the hex the branch carried); a rowless file is matched to a supplied pull request head by
  plan id (the legacy hex is ignored when comparing) or goes to `plans/legacy/`. Only `plan_file` (`plans.jsonl`) and
  `calibration_report_path` (`executions.jsonl`) are rewritten, by replacing the value inside the row's raw text, and
  every rewritten row is verified to parse to the old row with only that field changed; the ledgers are then compared
  row by row (same count and order, every other byte identical). Any ambiguity aborts before anything changes: two files
  for one target, an occupied target, a stored path that cannot be rewritten alone.
- Tests (`tests/test_migrate_plan_layout.py`, 6): full migration over the nesting cases (root, child intent below a plan
  segment, already migrated row, compact-serialised row, rowless file matched by PR head, rowless file to
  `plans/legacy/`), field-restricted rewrite and key order, report contents, a second run that reports nothing and
  leaves the tree byte-identical (hashed), a dry run, and the three abort paths.
- Real run (PR head list from `gh pr list --state all --limit 1000`, 205 heads):
  `PYTHONPATH=apps/sandbox-executor/src uv run python scripts/migrate_plan_layout.py --pr-heads <file>` moved 267 files
  (145 plan files, 122 calibration reports) and rewrote 244 stored paths (144 `plan_file`, 100
  `calibration_report_path`); both rowless files of `P-1794391254` matched the PR head under `I-1794391245-worktree`, so
  `plans/legacy/` was not needed. The `PYTHONPATH` prefix is only needed in this sandbox, whose `PYTHONPATH` carries an
  older engine copy of `sandbox_executor`.
- Independent verification after the real run: `plans.jsonl` 144 rows, 144 changed only in `plan_file`;
  `executions.jsonl` 4600 rows, 100 changed only in `calibration_report_path`; for every changed line the old line with
  the old value replaced by the new one is byte-equal to the new line; no stored path points at a missing file; no file
  is left directly in `plans/`. A second run printed "Nothing to migrate" and a hash of every file under `plans/` and
  both ledgers was byte-identical before and after it.
- Append-only enforcement keeps passing: `test_ledger_append_only.py`, `test_pre_commit_check.py` and
  `scripts/pre_commit_check.py` on the staged migration are green; no check needed an allowance, so none was weakened.
- Test fallout fixed: `test_artefacts.py` real-plan validation now globs plans recursively (it had started skipping),
  `flow/test_dispatch.py` dry-run assertion checks for plan files at any depth.
- Merge risk (recorded for the maintainer, not acted on): the rewritten rows include the last row of `plans.jsonl`, so
  plan rows appended on `main` before this lands will conflict in that ledger, and the append-only reconciler would keep
  this side's rows and append the other side's old form of a conflicting row. Such a merge needs a maintainer decision
  under the same one-off exemption; re-running the script afterwards migrates any newly added flat plan file.

#### Migration report (real run)

The 15 new paths that match no pull request head are plans whose PRs predate the listed heads, plus this intent's own
plan (its PR does not exist yet).

```text
Moved 267 file(s):
  plans/P-1787048480-antigravity-agent-gemini-3.5-flash.md -> plans/I-1787048466-executor-architecture-spec/P-1787048480-antigravity-agent-gemini-3.5-flash.md
  plans/P-1787051525-antigravity-agent-gemini-3.5-flash.md -> plans/I-1787051498-executor-plan-calibration/P-1787051525-antigravity-agent-gemini-3.5-flash.md
  plans/P-1787051525-antigravity-agent-gemini-3.5-flash_calibration.md -> plans/I-1787051498-executor-plan-calibration/P-1787051525-antigravity-agent-gemini-3.5-flash_calibration.md
  plans/P-1787563567-antigravity-agent-gemini-3.5-flash.md -> plans/I-1787563553-record-agent-version/P-1787563567-antigravity-agent-gemini-3.5-flash.md
  plans/P-1787563567-antigravity-agent-gemini-3.5-flash_calibration.md -> plans/I-1787563553-record-agent-version/P-1787563567-antigravity-agent-gemini-3.5-flash_calibration.md
  plans/P-1787928257-antigravity-agent-gemini-3.5-flash.md -> plans/I-1787928238-token-reduction-phase1/P-1787928257-antigravity-agent-gemini-3.5-flash.md
  plans/P-1787928877-antigravity-agent-gemini-3.5-flash.md -> plans/I-1787928862-token-reduction-phase2/P-1787928877-antigravity-agent-gemini-3.5-flash.md
  plans/P-1787928942-antigravity-agent-gemini-3.5-flash.md -> plans/I-1787928920-token-reduction-phase3/P-1787928942-antigravity-agent-gemini-3.5-flash.md
  plans/P-1787928955-antigravity-agent-gemini-3.5-flash.md -> plans/I-1787928927-token-reduction-phase4/P-1787928955-antigravity-agent-gemini-3.5-flash.md
  plans/P-1788088971-antigravity-agent-gemini-3.5-flash.md -> plans/I-1788088964-mitm-telemetry-metrics/P-1788088971-antigravity-agent-gemini-3.5-flash.md
  plans/P-1788173219-antigravity-agent-gemini-3.5-flash.md -> plans/I-1788172624-fix-mitm-proxy-400/P-1788173219-antigravity-agent-gemini-3.5-flash.md
  plans/P-1788173219-antigravity-agent-gemini-3.5-flash_calibration.md -> plans/I-1788172624-fix-mitm-proxy-400/P-1788173219-antigravity-agent-gemini-3.5-flash_calibration.md
  plans/P-1788176334-antigravity-agent-gemini-3.5-flash.md -> plans/I-1788176325-fix-mitm-telemetry-logging-and-sse-parsing/P-1788176334-antigravity-agent-gemini-3.5-flash.md
  plans/P-1789686633-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1789686556-package-and-document-holon-cli/P-1789686633-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1790379384-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1790379374-add-holon-calibrate-command/P-1790379384-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1790379384-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1790379374-add-holon-calibrate-command/P-1790379384-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1790382430-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1790382419-automate-holon-flow-lifecycle-pipeline/P-1790382430-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1790382661-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1790382620-make-sandbox-executor-tests-hermetic/P-1790382661-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1790562483-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1790562445-preserve-parent-history-in-executor-git-recovery/P-1790562483-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1790562483-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1790562445-preserve-parent-history-in-executor-git-recovery/P-1790562483-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1790564277-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1790564113-record-agent-output-and-default-active-repo-url/P-1790564277-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1790564277-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1790564113-record-agent-output-and-default-active-repo-url/P-1790564277-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1791097643-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791097634-converge-prettier-on-flow-produced-markdown/P-1791097643-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1791097643-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1791097634-converge-prettier-on-flow-produced-markdown/P-1791097643-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1791151683-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791151674-calibration-integrity-resilience-and-staleness-detection/P-1791151683-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1791151683-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1791151674-calibration-integrity-resilience-and-staleness-detection/P-1791151683-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1791190262-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791190247-redact-credentials-closed-list/P-1791190262-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1791190262-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1791190247-redact-credentials-closed-list/P-1791190262-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1791203032-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791203018-optimize-test-performance/P-1791203032-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1791203032-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1791203018-optimize-test-performance/P-1791203032-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1791281550-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791281538-stop-ledger-collisions/P-1791281550-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1791281550-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1791281538-stop-ledger-collisions/P-1791281550-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1791289002-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791288951-standardize-tooling-hygiene-and-scaffolding/P-1791289002-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1791547495-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791547482-drop-executions-and-fix-ledger-rev2/P-1791547495-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1791547495-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1791547482-drop-executions-and-fix-ledger-rev2/P-1791547495-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1791553791-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791553778-add-gh-and-openssl-prerequisite-checks/P-1791553791-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1791553791-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1791553778-add-gh-and-openssl-prerequisite-checks/P-1791553791-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1791787590-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791787565-calibration-merge-base-and-docker-probe-unusable/P-1791787590-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1791798221-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791798210-resolve-repo-url-dynamically/P-1791798221-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1791812932-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791812918-executor-toolbelt-and-lint-targets/P-1791812932-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1791839038-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791839030-append-every-action-to-execution-ledger/P-1791839038-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1791882348-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791881582-holon-flow-runner-flags-and-docs/P-1791882348-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1791978568-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791978544-submodule-update-and-prettier-yaml/P-1791978568-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1791983277-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791983257-proxy-routing-and-codebase-audit/P-1791983277-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1792016262-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792016246-audit-remediation-reliability-security/P-1792016262-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1792202834-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792202073-action-kb-wisdom-consult-and-recording/P-1792202834-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1792202834-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1792202073-action-kb-wisdom-consult-and-recording/P-1792202834-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1792211663-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792211166-standardise-license-apache-2-and-australian-spelling/P-1792211663-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1792211663-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1792211166-standardise-license-apache-2-and-australian-spelling/P-1792211663-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1792216015-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792215929-update-docker-agent-images-and-build-bake/P-1792216015-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1792216015-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1792215929-update-docker-agent-images-and-build-bake/P-1792216015-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1792220973-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792220943-native-holon-review-loop-step/P-1792220973-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1792220973-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1792220943-native-holon-review-loop-step/P-1792220973-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1792243199-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792242950-flow-review-pr-and-build-enhancements/P-1792243199-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1792243199-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1792242950-flow-review-pr-and-build-enhancements/P-1792243199-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1792302582-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792302568-review-loop-agent-conflict-guard-and-pre-commit/P-1792302582-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1792302582-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1792302568-review-loop-agent-conflict-guard-and-pre-commit/P-1792302582-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1792372417-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792372416-comprehensive-ecosystem-audit-and-bean-generation/P-1792372417-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1792372417-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1792372416-comprehensive-ecosystem-audit-and-bean-generation/P-1792372417-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1792384528-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792384524-audit-remediation-git-enau-sec-lock/P-1792384528-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1792384528-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1792384524-audit-remediation-git-enau-sec-lock/P-1792384528-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1792480383-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792480378-audit-and-remediate-documentation-gaps/P-1792480383-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1792480383-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1792480378-audit-and-remediate-documentation-gaps/P-1792480383-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1792501386-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792501322-install-gh-in-agent-images-and-add-review-tests/P-1792501386-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1792501386-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1792501322-install-gh-in-agent-images-and-add-review-tests/P-1792501386-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1792763923-claude-agent-claude-opus-5-5.md -> plans/I-1792635019-flow-stage-4-5-ledger-action-entries/P-1792763923-claude-agent-claude-opus-5-5.md
  plans/P-1792763923-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1792635019-flow-stage-4-5-ledger-action-entries/P-1792763923-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1792765112-claude-agent-claude-opus-5-5.md -> plans/I-1792762866-mergeability-missing-ref-not-conflict/P-1792765112-claude-agent-claude-opus-5-5.md
  plans/P-1792765112-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1792762866-mergeability-missing-ref-not-conflict/P-1792765112-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1792768937-claude-agent-claude-opus-5-5.md -> plans/I-1792765419-inherit-default-runner-from-harness-agent/P-1792768937-claude-agent-claude-opus-5-5.md
  plans/P-1792768937-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1792765419-inherit-default-runner-from-harness-agent/P-1792768937-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1792788153-claude-agent-claude-opus-5-5.md -> plans/I-1792765424-incremental-artifacts-budgets-and-local-llm-envelope/P-1792788153-claude-agent-claude-opus-5-5.md
  plans/P-1792788153-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1792765424-incremental-artifacts-budgets-and-local-llm-envelope/P-1792788153-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1792788551-claude-agent-claude-opus-5-5.md -> plans/I-1792765428-flow-dispatch-role-agents-and-empty-diff-guard/P-1792788551-claude-agent-claude-opus-5-5.md
  plans/P-1792788551-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1792765428-flow-dispatch-role-agents-and-empty-diff-guard/P-1792788551-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1792815994-claude-agent-claude-opus-5-5.md -> plans/I-1792815987-redact-github-token-in-cli-echoes-v2/P-1792815994-claude-agent-claude-opus-5-5.md
  plans/P-1792815994-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1792815987-redact-github-token-in-cli-echoes-v2/P-1792815994-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1792992691-claude-agent-claude-opus-5-5.md -> plans/I-1792992678-entropy-budget-handoff-and-no-human-halt-in-execute/P-1792992691-claude-agent-claude-opus-5-5.md
  plans/P-1792992691-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1792992678-entropy-budget-handoff-and-no-human-halt-in-execute/P-1792992691-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1793104256-claude-agent-claude-opus-5-5.md -> plans/I-1793104241-faster-unit-tests-xdist-and-cheaper-git-fixtures/P-1793104256-claude-agent-claude-opus-5-5.md
  plans/P-1793104256-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793104241-faster-unit-tests-xdist-and-cheaper-git-fixtures/P-1793104256-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1793189449-claude-agent-claude-opus-5-5.md -> plans/I-1793189433-review-loop-fail-closed-target-and-guards/P-1793189449-claude-agent-claude-opus-5-5.md
  plans/P-1793189449-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793189433-review-loop-fail-closed-target-and-guards/P-1793189449-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1793200653-claude-agent-claude-opus-5-5.md -> plans/I-1793200635-review-ensemble-module/P-1793200653-claude-agent-claude-opus-5-5.md
  plans/P-1793200653-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793200635-review-ensemble-module/P-1793200653-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1793223729-claude-agent-claude-opus-5-5.md -> plans/I-1793223711-review-loop-wire-real-ensemble/P-1793223729-claude-agent-claude-opus-5-5.md
  plans/P-1793223729-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793223711-review-loop-wire-real-ensemble/P-1793223729-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1793241058-claude-agent-claude-opus-5-5.md -> plans/I-1793241043-claude-runner-permission-bypass-and-one-pass-placeholders/P-1793241058-claude-agent-claude-opus-5-5.md
  plans/P-1793241058-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793241043-claude-runner-permission-bypass-and-one-pass-placeholders/P-1793241058-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1793246201-claude-agent-claude-opus-5-5.md -> plans/I-1793246184-execute-failure-keeps-partial-code-and-resumes-from-it/P-1793246201-claude-agent-claude-opus-5-5.md
  plans/P-1793246201-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793246184-execute-failure-keeps-partial-code-and-resumes-from-it/P-1793246201-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1793246292-claude-agent-claude-opus-5-5.md -> plans/I-1793246276-prompts-repeat-test-runs-only-for-performance-under-xdist/P-1793246292-claude-agent-claude-opus-5-5.md
  plans/P-1793246292-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793246276-prompts-repeat-test-runs-only-for-performance-under-xdist/P-1793246292-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1793246383-claude-agent-claude-opus-5-5.md -> plans/I-1793246367-url-fuzz-leak-check-ignores-random-temp-paths/P-1793246383-claude-agent-claude-opus-5-5.md
  plans/P-1793246383-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793246367-url-fuzz-leak-check-ignores-random-temp-paths/P-1793246383-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1793246475-claude-agent-claude-opus-5-5.md -> plans/I-1793246459-plain-language-entropy-and-expected-value-explainer/P-1793246475-claude-agent-claude-opus-5-5.md
  plans/P-1793246475-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793246459-plain-language-entropy-and-expected-value-explainer/P-1793246475-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1793246567-claude-agent-claude-opus-5-5.md -> plans/I-1793246551-ci-timing-regression-gate/P-1793246567-claude-agent-claude-opus-5-5.md
  plans/P-1793246567-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793246551-ci-timing-regression-gate/P-1793246567-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1793275622-claude-agent-claude-opus-5-5.md -> plans/I-1793275605-ledger-writes-are-pure-appends-never-reorder-intents/P-1793275622-claude-agent-claude-opus-5-5.md
  plans/P-1793275622-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793275605-ledger-writes-are-pure-appends-never-reorder-intents/P-1793275622-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1793275710-claude-agent-claude-opus-5-5.md -> plans/I-1793275695-docs-sub-intent-merge-target-and-automatic-nested-prs/P-1793275710-claude-agent-claude-opus-5-5.md
  plans/P-1793275710-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793275695-docs-sub-intent-merge-target-and-automatic-nested-prs/P-1793275710-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1793319426-claude-agent-claude-opus-5-5.md -> plans/I-1793319411-calibration-report-passes-repository-prettier-check/P-1793319426-claude-agent-claude-opus-5-5.md
  plans/P-1793319426-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793319411-calibration-report-passes-repository-prettier-check/P-1793319426-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1793319521-claude-agent-claude-opus-5-5.md -> plans/I-1793319507-remaining-docs-follow-sub-intent-merge-rulings/P-1793319521-claude-agent-claude-opus-5-5.md
  plans/P-1793319521-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793319507-remaining-docs-follow-sub-intent-merge-rulings/P-1793319521-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1793325374-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793325098-ci-timing-gate-composite-action/P-1793325374-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1793334817-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793334797-hac-ruff-complexity-gate-with-inline-baseline/P-1793334817-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1793334817-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1793334797-hac-ruff-complexity-gate-with-inline-baseline/P-1793334817-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1793339950-claude-agent-claude-opus-5-5.md -> plans/I-1793339935-flow-tests-never-write-the-checkout-knowledge-index/P-1793339950-claude-agent-claude-opus-5-5.md
  plans/P-1793339950-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793339935-flow-tests-never-write-the-checkout-knowledge-index/P-1793339950-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1793340046-claude-agent-claude-opus-5-5.md -> plans/I-1793340032-split-cli-into-self-registering-command-modules/P-1793340046-claude-agent-claude-opus-5-5.md
  plans/P-1793340046-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793340032-split-cli-into-self-registering-command-modules/P-1793340046-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1793349090-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793349075-route-stage-4-reviewer-turns-through-review-loop-container/P-1793349090-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1793349090-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1793349075-route-stage-4-reviewer-turns-through-review-loop-container/P-1793349090-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1793355620-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793355604-remove-fallback-defaults-for-agent-and-model/P-1793355620-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1793355620-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1793355604-remove-fallback-defaults-for-agent-and-model/P-1793355620-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1793425551-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793425536-remove-github-pr-terminology-across-holon-flow/P-1793425551-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1793425551-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1793425536-remove-github-pr-terminology-across-holon-flow/P-1793425551-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1793840814-claude-agent-claude-opus-5-5.md -> plans/I-1793840798-render-review-consensus-comment-from-reviewer-data/P-1793840814-claude-agent-claude-opus-5-5.md
  plans/P-1793840814-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793840798-render-review-consensus-comment-from-reviewer-data/P-1793840814-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1793856357-claude-agent-claude-opus-5-5.md -> plans/I-1793856341-engine-owned-review-loop-single-shot-container/P-1793856357-claude-agent-claude-opus-5-5.md
  plans/P-1793856357-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793856341-engine-owned-review-loop-single-shot-container/P-1793856357-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1793869856-antigravity-agent-gemini-4-argon.md -> plans/I-1793869839-stage-4-resolver-review-loop-converges/P-1793869856-antigravity-agent-gemini-4-argon.md
  plans/P-1793869856-antigravity-agent-gemini-4-argon_calibration.md -> plans/I-1793869839-stage-4-resolver-review-loop-converges/P-1793869856-antigravity-agent-gemini-4-argon_calibration.md
  plans/P-1793921914-antigravity-agent-gemini-4-argon.md -> plans/I-1793921896-bump-default-turn-timeout-to-1-hour/P-1793921914-antigravity-agent-gemini-4-argon.md
  plans/P-1793921914-antigravity-agent-gemini-4-argon_calibration.md -> plans/I-1793921896-bump-default-turn-timeout-to-1-hour/P-1793921914-antigravity-agent-gemini-4-argon_calibration.md
  plans/P-1793925096-antigravity-agent-gemini-4-argon.md -> plans/I-1793925076-increase-default-review-iteration-stage-budget-to-2-hours/P-1793925096-antigravity-agent-gemini-4-argon.md
  plans/P-1793925096-antigravity-agent-gemini-4-argon_calibration.md -> plans/I-1793925076-increase-default-review-iteration-stage-budget-to-2-hours/P-1793925096-antigravity-agent-gemini-4-argon_calibration.md
  plans/P-1793932304-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793932288-ci-is-a-post-review-gate-do-not-wait-before-reviewers/P-1793932304-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1793932304-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1793932288-ci-is-a-post-review-gate-do-not-wait-before-reviewers/P-1793932304-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1793939982-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793939965-stage-4-resolver-fixes-ci-and-test-failures/P-1793939982-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1793939982-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1793939965-stage-4-resolver-fixes-ci-and-test-failures/P-1793939982-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1793942508-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793942491-stage-5-commits-promoted-knowledge-and-wisdom/P-1793942508-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1793942508-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1793942491-stage-5-commits-promoted-knowledge-and-wisdom/P-1793942508-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1793953342-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793953326-fix-resolver-run-turn-argument-signature/P-1793953342-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1793953342-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1793953326-fix-resolver-run-turn-argument-signature/P-1793953342-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1793955693-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793955677-suppress-docker-desktop-cli-hints/P-1793955693-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1793955693-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1793955677-suppress-docker-desktop-cli-hints/P-1793955693-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1793960347-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793960332-treat-kb-wb-facts-as-immutable-append-only-records/P-1793960347-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1793960347-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1793960332-treat-kb-wb-facts-as-immutable-append-only-records/P-1793960347-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1794003851-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794003835-fix-resolver-runner-build-cmd-attribute-error/P-1794003851-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1794003851-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794003835-fix-resolver-runner-build-cmd-attribute-error/P-1794003851-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1794009796-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794009779-flow-auto-create-worktree-if-not-exists/P-1794009796-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1794009796-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794009779-flow-auto-create-worktree-if-not-exists/P-1794009796-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1794041623-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794041607-add-holon-models-command-to-list-supported-models/P-1794041623-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1794041623-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794041607-add-holon-models-command-to-list-supported-models/P-1794041623-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1794049540-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794049523-replace-repo-dir-with-target-worktree/P-1794049540-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1794049540-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794049523-replace-repo-dir-with-target-worktree/P-1794049540-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1794089619-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794089603-pass-prompts-to-agent-runners-via-files/P-1794089619-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1794089619-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794089603-pass-prompts-to-agent-runners-via-files/P-1794089619-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1794124844-claude-agent-claude-fable-5.md -> plans/I-1794124652-post-consensus-review-once-per-pr-exit/P-1794124844-claude-agent-claude-fable-5.md
  plans/P-1794124844-claude-agent-claude-fable-5_calibration.md -> plans/I-1794124652-post-consensus-review-once-per-pr-exit/P-1794124844-claude-agent-claude-fable-5_calibration.md
  plans/P-1794124920-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794124904-fix-review-loop-observability-blindspot/P-1794124920-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1794124920-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794124904-fix-review-loop-observability-blindspot/P-1794124920-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1794132519-claude-agent-claude-fable-5.md -> plans/I-1794132504-purge-harness-bean-references-from-code-and-docs/P-1794132519-claude-agent-claude-fable-5.md
  plans/P-1794132519-claude-agent-claude-fable-5_calibration.md -> plans/I-1794132504-purge-harness-bean-references-from-code-and-docs/P-1794132519-claude-agent-claude-fable-5_calibration.md
  plans/P-1794136024-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794136007-prompts-carry-module-and-complexity-rules/P-1794136024-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1794136024-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794136007-prompts-carry-module-and-complexity-rules/P-1794136024-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1794136106-claude-agent-claude-fable-5.md -> plans/I-1794136091-persist-review-loop-sandbox-across-iterations/P-1794136106-claude-agent-claude-fable-5.md
  plans/P-1794136106-claude-agent-claude-fable-5_calibration.md -> plans/I-1794136091-persist-review-loop-sandbox-across-iterations/P-1794136106-claude-agent-claude-fable-5_calibration.md
  plans/P-1794147403-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794147383-executor-git-repository-adapter/P-1794147403-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1794147403-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794147383-executor-git-repository-adapter/P-1794147403-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1794182135-claude-agent-claude-opus-5-5.md -> plans/I-1794182121-validate-target-worktree-is-git-worktree/P-1794182135-claude-agent-claude-opus-5-5.md
  plans/P-1794182135-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794182121-validate-target-worktree-is-git-worktree/P-1794182135-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1794182557-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794182541-unique-plan-and-execution-ids/P-1794182557-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1794182557-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794182541-unique-plan-and-execution-ids/P-1794182557-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1794192045-c23d4516-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794192030-split-flow-py-into-flow-package/P-1794192045-c23d4516-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1794192045-claude-agent-claude-opus-5-5.md -> plans/I-1794192030-split-flow-py-into-flow-package/P-1794192045-c23d4516-claude-agent-claude-opus-5-5.md
  plans/P-1794196917-8bed7894-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794196901-resolver-uv-available-and-fail-loudly/P-1794196917-8bed7894-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1794196917-claude-agent-claude-opus-5-5.md -> plans/I-1794196901-resolver-uv-available-and-fail-loudly/P-1794196917-8bed7894-claude-agent-claude-opus-5-5.md
  plans/P-1794197261-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794197246-dual-ev-calibration-with-and-without-bugs/P-1794197261-edaaa218-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1794197261-edaaa218-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794197246-dual-ev-calibration-with-and-without-bugs/P-1794197261-edaaa218-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1794205494-860c0862-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794205479-flow-runs-in-dedicated-worktree-not-default-branch/P-1794205494-860c0862-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1794205494-claude-agent-claude-opus-5-5.md -> plans/I-1794205479-flow-runs-in-dedicated-worktree-not-default-branch/P-1794205494-860c0862-claude-agent-claude-opus-5-5.md
  plans/P-1794205877-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794205862-resolver-commits-locally-skip-push-fix/P-1794205877-cbad6e47-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1794205877-cbad6e47-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794205862-resolver-commits-locally-skip-push-fix/P-1794205877-cbad6e47-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1794220535-37b66e75-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794220515-run-ci-on-pull-requests-into-any-branch/P-1794220535-37b66e75-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1794220535-claude-agent-claude-opus-5-5.md -> plans/I-1794220515-run-ci-on-pull-requests-into-any-branch/P-1794220535-37b66e75-claude-agent-claude-opus-5-5.md
  plans/P-1794224651-claude-agent-claude-opus-5-5.md -> plans/I-1794224634-plan-owned-decomposition-with-plan-branch-children/P-1794224651-93cef978-claude-agent-claude-opus-5-5.md
  plans/P-1794225025-924685ae-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794224634-plan-owned-decomposition-with-plan-branch-children--1-record-the-decomposition-decision-on-the-plan-ro/P-1794225025-924685ae-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1794225025-claude-agent-claude-opus-5-5.md -> plans/I-1794224634-plan-owned-decomposition-with-plan-branch-children--1-record-the-decomposition-decision-on-the-plan-ro/P-1794225025-924685ae-claude-agent-claude-opus-5-5.md
  plans/P-1794227872-152246bc-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794224634-plan-owned-decomposition-with-plan-branch-children--2-nest-decomposed-children-under-the-plan-branch-m/P-1794227872-152246bc-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1794227872-claude-agent-claude-opus-5-5.md -> plans/I-1794224634-plan-owned-decomposition-with-plan-branch-children--2-nest-decomposed-children-under-the-plan-branch-m/P-1794227872-152246bc-claude-agent-claude-opus-5-5.md
  plans/P-1794268104-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794268087-mount-host-worktree-into-sandbox-containers/P-1794268104-fd29467b-antigravity-agent-gemini-3.8-flash-medium.md
  plans/P-1794268104-fd29467b-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794268087-mount-host-worktree-into-sandbox-containers/P-1794268104-fd29467b-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/P-1794294214-0a2285cf-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794294198-review-consensus-never-approves-past-an-open-critical/P-1794294214-0a2285cf-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1794294214-claude-agent-claude-opus-5-5.md -> plans/I-1794294198-review-consensus-never-approves-past-an-open-critical/P-1794294214-0a2285cf-claude-agent-claude-opus-5-5.md
  plans/P-1794388224-67cb4776-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794388202-retire-the-flow-move-proof-snapshot/P-1794388224-67cb4776-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794388224-bd55e506-claude-agent-claude-sonnet-5-5.md -> plans/I-1794388202-review-loop-waits-for-ci-outside-the-iteration-budget/P-1794388224-bd55e506-claude-agent-claude-sonnet-5-5.md
  plans/P-1794388224-bd55e506-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794388202-review-loop-waits-for-ci-outside-the-iteration-budget/P-1794388224-bd55e506-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794388224-claude-agent-claude-sonnet-5-5.md -> plans/I-1794388202-retire-the-flow-move-proof-snapshot/P-1794388224-67cb4776-claude-agent-claude-sonnet-5-5.md
  plans/P-1794391254-8ec0e2e8-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794391245-worktree/P-1794391254-8ec0e2e8-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794391254-claude-agent-claude-sonnet-5-5.md -> plans/I-1794391245-worktree/P-1794391254-8ec0e2e8-claude-agent-claude-sonnet-5-5.md
  plans/P-1794391544-abdc9b6d-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794391536-refcap/P-1794391544-abdc9b6d-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794391544-claude-agent-claude-sonnet-5-5.md -> plans/I-1794391536-refcap/P-1794391544-abdc9b6d-claude-agent-claude-sonnet-5-5.md
  plans/P-1794446836-06880b0b-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794446604-ids/P-1794446836-06880b0b-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794446836-claude-agent-claude-sonnet-5-5.md -> plans/I-1794446604-ids/P-1794446836-06880b0b-claude-agent-claude-sonnet-5-5.md
  plans/P-1794446908-claude-agent-claude-sonnet-5-5.md -> plans/I-1794446637-creds/P-1794446908-d2eae989-claude-agent-claude-sonnet-5-5.md
  plans/P-1794446908-d2eae989-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794446637-creds/P-1794446908-d2eae989-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794447543-712b026e-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794447526-nofetch/P-1794447543-712b026e-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794447543-claude-agent-claude-sonnet-5-5.md -> plans/I-1794447526-nofetch/P-1794447543-712b026e-claude-agent-claude-sonnet-5-5.md
  plans/P-1794452423-claude-agent-claude-sonnet-5-5.md -> plans/I-1794452407-nofetch2/P-1794452423-f3b616c7-claude-agent-claude-sonnet-5-5.md
  plans/P-1794452423-f3b616c7-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794452407-nofetch2/P-1794452423-f3b616c7-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794456566-07246b8e-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794456550-calci/P-1794456566-07246b8e-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794456566-claude-agent-claude-sonnet-5-5.md -> plans/I-1794456550-calci/P-1794456566-07246b8e-claude-agent-claude-sonnet-5-5.md
  plans/P-1794458347-aa9e9dfa-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794458330-stubfetch/P-1794458347-aa9e9dfa-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794458347-claude-agent-claude-sonnet-5-5.md -> plans/I-1794458330-stubfetch/P-1794458347-aa9e9dfa-claude-agent-claude-sonnet-5-5.md
  plans/P-1794461511-3cca99da-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794461494-failnames/P-1794461511-3cca99da-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794461511-claude-agent-claude-sonnet-5-5.md -> plans/I-1794461494-failnames/P-1794461511-3cca99da-claude-agent-claude-sonnet-5-5.md
  plans/P-1794464849-2a9a4a6b-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794464833-scrubcreds/P-1794464849-2a9a4a6b-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794464849-claude-agent-claude-sonnet-5-5.md -> plans/I-1794464833-scrubcreds/P-1794464849-2a9a4a6b-claude-agent-claude-sonnet-5-5.md
  plans/P-1794476365-334a8266-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794476347-nopost/P-1794476365-334a8266-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794476365-claude-agent-claude-sonnet-5-5.md -> plans/I-1794476347-nopost/P-1794476365-334a8266-claude-agent-claude-sonnet-5-5.md
  plans/P-1794478317-4eb07fff-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794478300-salvage/P-1794478317-4eb07fff-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794478317-claude-agent-claude-sonnet-5-5.md -> plans/I-1794478300-salvage/P-1794478317-4eb07fff-claude-agent-claude-sonnet-5-5.md
  plans/P-1794480915-c8cd81b2-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794480898-mdgate/P-1794480915-c8cd81b2-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794480915-claude-agent-claude-sonnet-5-5.md -> plans/I-1794480898-mdgate/P-1794480915-c8cd81b2-claude-agent-claude-sonnet-5-5.md
  plans/P-1794523633-edd36e36-claude-agent-claude-sonnet-5-5.md -> plans/I-1794523617-revlog/P-1794523633-edd36e36-claude-agent-claude-sonnet-5-5.md
  plans/P-1794523633-edd36e36-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794523617-revlog/P-1794523633-edd36e36-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794523636-de5c2107-claude-agent-claude-sonnet-5-5.md -> plans/I-1794523620-keeppush/P-1794523636-de5c2107-claude-agent-claude-sonnet-5-5.md
  plans/P-1794523636-de5c2107-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794523620-keeppush/P-1794523636-de5c2107-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794529179-a03ed51a-claude-agent-claude-sonnet-5-5.md -> plans/I-1794529163-attrib/P-1794529179-a03ed51a-claude-agent-claude-sonnet-5-5.md
  plans/P-1794529179-a03ed51a-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794529163-attrib/P-1794529179-a03ed51a-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794529181-eabd8e76-claude-agent-claude-sonnet-5-5.md -> plans/I-1794529165-calsync/P-1794529181-eabd8e76-claude-agent-claude-sonnet-5-5.md
  plans/P-1794529181-eabd8e76-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794529165-calsync/P-1794529181-eabd8e76-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794529184-344adb09-claude-agent-claude-sonnet-5-5.md -> plans/I-1794529168-pidir/P-1794529184-344adb09-claude-agent-claude-sonnet-5-5.md
  plans/P-1794529184-344adb09-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794529168-pidir/P-1794529184-344adb09-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794546213-1bdbac00-claude-agent-claude-sonnet-5-5.md -> plans/I-1794546197-chkgate/P-1794546213-1bdbac00-claude-agent-claude-sonnet-5-5.md
  plans/P-1794546213-1bdbac00-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794546197-chkgate/P-1794546213-1bdbac00-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794546217-ec4d1e6a-claude-agent-claude-sonnet-5-5.md -> plans/I-1794546201-keepdiff/P-1794546217-ec4d1e6a-claude-agent-claude-sonnet-5-5.md
  plans/P-1794546217-ec4d1e6a-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794546201-keepdiff/P-1794546217-ec4d1e6a-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794546221-53ff4e71-claude-agent-claude-sonnet-5-5.md -> plans/I-1794546205-measure/P-1794546221-53ff4e71-claude-agent-claude-sonnet-5-5.md
  plans/P-1794546221-53ff4e71-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794546205-measure/P-1794546221-53ff4e71-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794567531-a466a71e-claude-agent-claude-sonnet-5-5.md -> plans/I-1794567515-sleepfix/P-1794567531-a466a71e-claude-agent-claude-sonnet-5-5.md
  plans/P-1794567531-a466a71e-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794567515-sleepfix/P-1794567531-a466a71e-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794567535-986a8099-claude-agent-claude-sonnet-5-5.md -> plans/I-1794567519-lockfrz/P-1794567535-986a8099-claude-agent-claude-sonnet-5-5.md
  plans/P-1794567535-986a8099-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794567519-lockfrz/P-1794567535-986a8099-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794567538-ea208f06-claude-agent-claude-sonnet-5-5.md -> plans/I-1794567523-savepat/P-1794567538-ea208f06-claude-agent-claude-sonnet-5-5.md
  plans/P-1794567538-ea208f06-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794567523-savepat/P-1794567538-ea208f06-claude-agent-claude-sonnet-5-5_calibration.md
  plans/P-1794574974-21db7508-claude-agent-claude-opus-5-5.md -> plans/I-1794574957-calrebld/P-1794574974-21db7508-claude-agent-claude-opus-5-5.md
  plans/P-1794574974-21db7508-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794574957-calrebld/P-1794574974-21db7508-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1794575045-ca973365-claude-agent-claude-opus-5-5.md -> plans/I-1794575029-nopost/P-1794575045-ca973365-claude-agent-claude-opus-5-5.md
  plans/P-1794575045-ca973365-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794575029-nopost/P-1794575045-ca973365-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1794617720-cbecf7aa-claude-agent-claude-opus-5-5.md -> plans/I-1794617704-touchset/P-1794617720-cbecf7aa-claude-agent-claude-opus-5-5.md
  plans/P-1794617720-cbecf7aa-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794617704-touchset/P-1794617720-cbecf7aa-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1794617990-33b5cb73-claude-agent-claude-opus-5-5.md -> plans/I-1794617704-touchset/P-1794617720-cbecf7aa-claude-agent-claude-opus-5-5--1-add-the-planner-touch-set-estimate-a-touch-set-p/P-1794617990-33b5cb73-claude-agent-claude-opus-5-5.md
  plans/P-1794657809-c6ebdbd6-claude-agent-claude-opus-5-5.md -> plans/I-1794657793-mergemth/P-1794657809-c6ebdbd6-claude-agent-claude-opus-5-5.md
  plans/P-1794657809-c6ebdbd6-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794657793-mergemth/P-1794657809-c6ebdbd6-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1794657847-a55ed825-claude-agent-claude-opus-5-5.md -> plans/I-1794657831-calbrnch/P-1794657847-a55ed825-claude-agent-claude-opus-5-5.md
  plans/P-1794657847-a55ed825-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794657831-calbrnch/P-1794657847-a55ed825-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1794660305-d5b6233a-claude-agent-claude-opus-5-5.md -> plans/I-1794660290-probehttp/P-1794660305-d5b6233a-claude-agent-claude-opus-5-5.md
  plans/P-1794660305-d5b6233a-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794660290-probehttp/P-1794660305-d5b6233a-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1794663302-17aba811-claude-agent-claude-opus-5-5.md -> plans/I-1794663286-hygiene/P-1794663302-17aba811-claude-agent-claude-opus-5-5.md
  plans/P-1794663302-17aba811-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794663286-hygiene/P-1794663302-17aba811-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1794663411-f5308cfe-claude-agent-claude-opus-5-5.md -> plans/I-1794617704-touchset/P-1794617720-cbecf7aa-claude-agent-claude-opus-5-5--2-add-touch-set-admission-before-stage-3-and-enfor/P-1794663411-f5308cfe-claude-agent-claude-opus-5-5.md
  plans/P-1794663411-f5308cfe-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794617704-touchset/P-1794617720-cbecf7aa-claude-agent-claude-opus-5-5--2-add-touch-set-admission-before-stage-3-and-enfor/P-1794663411-f5308cfe-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1794727651-2a1e918e-claude-agent-claude-opus-5-5.md -> plans/I-1794727635-guardbase/P-1794727651-2a1e918e-claude-agent-claude-opus-5-5.md
  plans/P-1794727651-2a1e918e-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794727635-guardbase/P-1794727651-2a1e918e-claude-agent-claude-opus-5-5_calibration.md
  plans/P-1794891094-eede5d91-claude-agent-claude-fable-5.md -> plans/I-1794891078-s5rerun/P-1794891094-eede5d91-claude-agent-claude-fable-5.md
  plans/P-1794891094-eede5d91-claude-agent-claude-fable-5_calibration.md -> plans/I-1794891078-s5rerun/P-1794891094-eede5d91-claude-agent-claude-fable-5_calibration.md
  plans/P-1794892061-52a8e074-claude-agent-claude-fable-5.md -> plans/I-1794891082-beadspil/P-1794892061-52a8e074-claude-agent-claude-fable-5.md
  plans/P-1794892061-52a8e074-claude-agent-claude-fable-5_calibration.md -> plans/I-1794891082-beadspil/P-1794892061-52a8e074-claude-agent-claude-fable-5_calibration.md
  plans/P-1794976517-cac841ef-claude-agent-claude-fable-5.md -> plans/I-1794976501-macgate/P-1794976517-cac841ef-claude-agent-claude-fable-5.md
  plans/P-1794976517-cac841ef-claude-agent-claude-fable-5_calibration.md -> plans/I-1794976501-macgate/P-1794976517-cac841ef-claude-agent-claude-fable-5_calibration.md
  plans/P-1795087811-3604f487-claude-agent-claude-fable-5.md -> plans/I-1794976491-shortref/P-1795087811-3604f487-claude-agent-claude-fable-5.md
Rewrote 244 ledger path(s):
  holon-knowledge/ledger/plans.jsonl:1 plan_file: plans/P-1787048480-antigravity-agent-gemini-3.5-flash.md -> plans/I-1787048466-executor-architecture-spec/P-1787048480-antigravity-agent-gemini-3.5-flash.md
  holon-knowledge/ledger/plans.jsonl:2 plan_file: plans/P-1787051525-antigravity-agent-gemini-3.5-flash.md -> plans/I-1787051498-executor-plan-calibration/P-1787051525-antigravity-agent-gemini-3.5-flash.md
  holon-knowledge/ledger/plans.jsonl:3 plan_file: plans/P-1787563567-antigravity-agent-gemini-3.5-flash.md -> plans/I-1787563553-record-agent-version/P-1787563567-antigravity-agent-gemini-3.5-flash.md
  holon-knowledge/ledger/plans.jsonl:4 plan_file: plans/P-1787928257-antigravity-agent-gemini-3.5-flash.md -> plans/I-1787928238-token-reduction-phase1/P-1787928257-antigravity-agent-gemini-3.5-flash.md
  holon-knowledge/ledger/plans.jsonl:5 plan_file: plans/P-1787928877-antigravity-agent-gemini-3.5-flash.md -> plans/I-1787928862-token-reduction-phase2/P-1787928877-antigravity-agent-gemini-3.5-flash.md
  holon-knowledge/ledger/plans.jsonl:6 plan_file: plans/P-1787928942-antigravity-agent-gemini-3.5-flash.md -> plans/I-1787928920-token-reduction-phase3/P-1787928942-antigravity-agent-gemini-3.5-flash.md
  holon-knowledge/ledger/plans.jsonl:7 plan_file: plans/P-1787928955-antigravity-agent-gemini-3.5-flash.md -> plans/I-1787928927-token-reduction-phase4/P-1787928955-antigravity-agent-gemini-3.5-flash.md
  holon-knowledge/ledger/plans.jsonl:8 plan_file: plans/P-1788088971-antigravity-agent-gemini-3.5-flash.md -> plans/I-1788088964-mitm-telemetry-metrics/P-1788088971-antigravity-agent-gemini-3.5-flash.md
  holon-knowledge/ledger/plans.jsonl:9 plan_file: plans/P-1788173219-antigravity-agent-gemini-3.5-flash.md -> plans/I-1788172624-fix-mitm-proxy-400/P-1788173219-antigravity-agent-gemini-3.5-flash.md
  holon-knowledge/ledger/plans.jsonl:10 plan_file: plans/P-1788176334-antigravity-agent-gemini-3.5-flash.md -> plans/I-1788176325-fix-mitm-telemetry-logging-and-sse-parsing/P-1788176334-antigravity-agent-gemini-3.5-flash.md
  holon-knowledge/ledger/plans.jsonl:11 plan_file: plans/P-1789686633-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1789686556-package-and-document-holon-cli/P-1789686633-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:12 plan_file: plans/P-1790379384-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1790379374-add-holon-calibrate-command/P-1790379384-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:13 plan_file: plans/P-1790382430-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1790382419-automate-holon-flow-lifecycle-pipeline/P-1790382430-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:14 plan_file: plans/P-1790382661-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1790382620-make-sandbox-executor-tests-hermetic/P-1790382661-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:15 plan_file: plans/P-1790562483-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1790562445-preserve-parent-history-in-executor-git-recovery/P-1790562483-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:16 plan_file: plans/P-1790564277-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1790564113-record-agent-output-and-default-active-repo-url/P-1790564277-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:17 plan_file: plans/P-1791097643-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791097634-converge-prettier-on-flow-produced-markdown/P-1791097643-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:18 plan_file: plans/P-1791151683-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791151674-calibration-integrity-resilience-and-staleness-detection/P-1791151683-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:19 plan_file: plans/P-1791190262-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791190247-redact-credentials-closed-list/P-1791190262-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:20 plan_file: plans/P-1791203032-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791203018-optimize-test-performance/P-1791203032-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:21 plan_file: plans/P-1791281550-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791281538-stop-ledger-collisions/P-1791281550-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:22 plan_file: plans/P-1791289002-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791288951-standardize-tooling-hygiene-and-scaffolding/P-1791289002-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:23 plan_file: plans/P-1791547495-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791547482-drop-executions-and-fix-ledger-rev2/P-1791547495-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:24 plan_file: plans/P-1791553791-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791553778-add-gh-and-openssl-prerequisite-checks/P-1791553791-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:25 plan_file: plans/P-1791787590-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791787565-calibration-merge-base-and-docker-probe-unusable/P-1791787590-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:26 plan_file: plans/P-1791798221-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791798210-resolve-repo-url-dynamically/P-1791798221-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:27 plan_file: plans/P-1791812932-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791812918-executor-toolbelt-and-lint-targets/P-1791812932-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:28 plan_file: plans/P-1791839038-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791839030-append-every-action-to-execution-ledger/P-1791839038-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:29 plan_file: plans/P-1791882348-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791881582-holon-flow-runner-flags-and-docs/P-1791882348-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:30 plan_file: plans/P-1791978568-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791978544-submodule-update-and-prettier-yaml/P-1791978568-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:31 plan_file: plans/P-1791983277-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1791983257-proxy-routing-and-codebase-audit/P-1791983277-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:32 plan_file: plans/P-1792016262-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792016246-audit-remediation-reliability-security/P-1792016262-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:33 plan_file: plans/P-1792202834-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792202073-action-kb-wisdom-consult-and-recording/P-1792202834-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:34 plan_file: plans/P-1792211663-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792211166-standardise-license-apache-2-and-australian-spelling/P-1792211663-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:35 plan_file: plans/P-1792216015-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792215929-update-docker-agent-images-and-build-bake/P-1792216015-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:36 plan_file: plans/P-1792220973-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792220943-native-holon-review-loop-step/P-1792220973-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:37 plan_file: plans/P-1792243199-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792242950-flow-review-pr-and-build-enhancements/P-1792243199-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:38 plan_file: plans/P-1792302582-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792302568-review-loop-agent-conflict-guard-and-pre-commit/P-1792302582-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:39 plan_file: plans/P-1792372417-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792372416-comprehensive-ecosystem-audit-and-bean-generation/P-1792372417-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:40 plan_file: plans/P-1792384528-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792384524-audit-remediation-git-enau-sec-lock/P-1792384528-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:41 plan_file: plans/P-1792480383-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792480378-audit-and-remediate-documentation-gaps/P-1792480383-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:42 plan_file: plans/P-1792501386-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1792501322-install-gh-in-agent-images-and-add-review-tests/P-1792501386-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:43 plan_file: plans/P-1792763923-claude-agent-claude-opus-5-5.md -> plans/I-1792635019-flow-stage-4-5-ledger-action-entries/P-1792763923-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:44 plan_file: plans/P-1792765112-claude-agent-claude-opus-5-5.md -> plans/I-1792762866-mergeability-missing-ref-not-conflict/P-1792765112-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:45 plan_file: plans/P-1792768937-claude-agent-claude-opus-5-5.md -> plans/I-1792765419-inherit-default-runner-from-harness-agent/P-1792768937-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:46 plan_file: plans/P-1792788153-claude-agent-claude-opus-5-5.md -> plans/I-1792765424-incremental-artifacts-budgets-and-local-llm-envelope/P-1792788153-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:47 plan_file: plans/P-1792788551-claude-agent-claude-opus-5-5.md -> plans/I-1792765428-flow-dispatch-role-agents-and-empty-diff-guard/P-1792788551-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:48 plan_file: plans/P-1792815994-claude-agent-claude-opus-5-5.md -> plans/I-1792815987-redact-github-token-in-cli-echoes-v2/P-1792815994-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:49 plan_file: plans/P-1792992691-claude-agent-claude-opus-5-5.md -> plans/I-1792992678-entropy-budget-handoff-and-no-human-halt-in-execute/P-1792992691-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:50 plan_file: plans/P-1793104256-claude-agent-claude-opus-5-5.md -> plans/I-1793104241-faster-unit-tests-xdist-and-cheaper-git-fixtures/P-1793104256-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:51 plan_file: plans/P-1793189449-claude-agent-claude-opus-5-5.md -> plans/I-1793189433-review-loop-fail-closed-target-and-guards/P-1793189449-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:52 plan_file: plans/P-1793200653-claude-agent-claude-opus-5-5.md -> plans/I-1793200635-review-ensemble-module/P-1793200653-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:53 plan_file: plans/P-1793223729-claude-agent-claude-opus-5-5.md -> plans/I-1793223711-review-loop-wire-real-ensemble/P-1793223729-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:54 plan_file: plans/P-1793241058-claude-agent-claude-opus-5-5.md -> plans/I-1793241043-claude-runner-permission-bypass-and-one-pass-placeholders/P-1793241058-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:55 plan_file: plans/P-1793246292-claude-agent-claude-opus-5-5.md -> plans/I-1793246276-prompts-repeat-test-runs-only-for-performance-under-xdist/P-1793246292-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:56 plan_file: plans/P-1793246201-claude-agent-claude-opus-5-5.md -> plans/I-1793246184-execute-failure-keeps-partial-code-and-resumes-from-it/P-1793246201-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:57 plan_file: plans/P-1793246383-claude-agent-claude-opus-5-5.md -> plans/I-1793246367-url-fuzz-leak-check-ignores-random-temp-paths/P-1793246383-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:58 plan_file: plans/P-1793246475-claude-agent-claude-opus-5-5.md -> plans/I-1793246459-plain-language-entropy-and-expected-value-explainer/P-1793246475-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:59 plan_file: plans/P-1793246567-claude-agent-claude-opus-5-5.md -> plans/I-1793246551-ci-timing-regression-gate/P-1793246567-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:60 plan_file: plans/P-1793275622-claude-agent-claude-opus-5-5.md -> plans/I-1793275605-ledger-writes-are-pure-appends-never-reorder-intents/P-1793275622-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:61 plan_file: plans/P-1793275710-claude-agent-claude-opus-5-5.md -> plans/I-1793275695-docs-sub-intent-merge-target-and-automatic-nested-prs/P-1793275710-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:62 plan_file: plans/P-1793319426-claude-agent-claude-opus-5-5.md -> plans/I-1793319411-calibration-report-passes-repository-prettier-check/P-1793319426-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:63 plan_file: plans/P-1793319521-claude-agent-claude-opus-5-5.md -> plans/I-1793319507-remaining-docs-follow-sub-intent-merge-rulings/P-1793319521-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:64 plan_file: plans/P-1793325374-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793325098-ci-timing-gate-composite-action/P-1793325374-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:65 plan_file: plans/P-1793334817-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793334797-hac-ruff-complexity-gate-with-inline-baseline/P-1793334817-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:66 plan_file: plans/P-1793339950-claude-agent-claude-opus-5-5.md -> plans/I-1793339935-flow-tests-never-write-the-checkout-knowledge-index/P-1793339950-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:67 plan_file: plans/P-1793340046-claude-agent-claude-opus-5-5.md -> plans/I-1793340032-split-cli-into-self-registering-command-modules/P-1793340046-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:68 plan_file: plans/P-1793349090-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793349075-route-stage-4-reviewer-turns-through-review-loop-container/P-1793349090-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:69 plan_file: plans/P-1793355620-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793355604-remove-fallback-defaults-for-agent-and-model/P-1793355620-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:70 plan_file: plans/P-1793425551-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793425536-remove-github-pr-terminology-across-holon-flow/P-1793425551-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:71 plan_file: plans/P-1793840814-claude-agent-claude-opus-5-5.md -> plans/I-1793840798-render-review-consensus-comment-from-reviewer-data/P-1793840814-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:72 plan_file: plans/P-1793856357-claude-agent-claude-opus-5-5.md -> plans/I-1793856341-engine-owned-review-loop-single-shot-container/P-1793856357-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:73 plan_file: plans/P-1793869856-antigravity-agent-gemini-4-argon.md -> plans/I-1793869839-stage-4-resolver-review-loop-converges/P-1793869856-antigravity-agent-gemini-4-argon.md
  holon-knowledge/ledger/plans.jsonl:74 plan_file: plans/P-1793921914-antigravity-agent-gemini-4-argon.md -> plans/I-1793921896-bump-default-turn-timeout-to-1-hour/P-1793921914-antigravity-agent-gemini-4-argon.md
  holon-knowledge/ledger/plans.jsonl:75 plan_file: plans/P-1793925096-antigravity-agent-gemini-4-argon.md -> plans/I-1793925076-increase-default-review-iteration-stage-budget-to-2-hours/P-1793925096-antigravity-agent-gemini-4-argon.md
  holon-knowledge/ledger/plans.jsonl:76 plan_file: plans/P-1793932304-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793932288-ci-is-a-post-review-gate-do-not-wait-before-reviewers/P-1793932304-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:77 plan_file: plans/P-1793939982-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793939965-stage-4-resolver-fixes-ci-and-test-failures/P-1793939982-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:78 plan_file: plans/P-1793942508-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793942491-stage-5-commits-promoted-knowledge-and-wisdom/P-1793942508-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:79 plan_file: plans/P-1793953342-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793953326-fix-resolver-run-turn-argument-signature/P-1793953342-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:80 plan_file: plans/P-1793955693-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793955677-suppress-docker-desktop-cli-hints/P-1793955693-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:81 plan_file: plans/P-1793960347-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1793960332-treat-kb-wb-facts-as-immutable-append-only-records/P-1793960347-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:82 plan_file: plans/P-1794003851-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794003835-fix-resolver-runner-build-cmd-attribute-error/P-1794003851-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:83 plan_file: plans/P-1794009796-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794009779-flow-auto-create-worktree-if-not-exists/P-1794009796-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:84 plan_file: plans/P-1794041623-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794041607-add-holon-models-command-to-list-supported-models/P-1794041623-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:85 plan_file: plans/P-1794049540-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794049523-replace-repo-dir-with-target-worktree/P-1794049540-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:86 plan_file: plans/P-1794089619-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794089603-pass-prompts-to-agent-runners-via-files/P-1794089619-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:87 plan_file: plans/P-1794124844-claude-agent-claude-fable-5.md -> plans/I-1794124652-post-consensus-review-once-per-pr-exit/P-1794124844-claude-agent-claude-fable-5.md
  holon-knowledge/ledger/plans.jsonl:88 plan_file: plans/P-1794124920-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794124904-fix-review-loop-observability-blindspot/P-1794124920-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:89 plan_file: plans/P-1794132519-claude-agent-claude-fable-5.md -> plans/I-1794132504-purge-harness-bean-references-from-code-and-docs/P-1794132519-claude-agent-claude-fable-5.md
  holon-knowledge/ledger/plans.jsonl:90 plan_file: plans/P-1794136024-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794136007-prompts-carry-module-and-complexity-rules/P-1794136024-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:91 plan_file: plans/P-1794136106-claude-agent-claude-fable-5.md -> plans/I-1794136091-persist-review-loop-sandbox-across-iterations/P-1794136106-claude-agent-claude-fable-5.md
  holon-knowledge/ledger/plans.jsonl:92 plan_file: plans/P-1794147403-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794147383-executor-git-repository-adapter/P-1794147403-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:93 plan_file: plans/P-1794182135-claude-agent-claude-opus-5-5.md -> plans/I-1794182121-validate-target-worktree-is-git-worktree/P-1794182135-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:94 plan_file: plans/P-1794182557-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794182541-unique-plan-and-execution-ids/P-1794182557-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:95 plan_file: plans/P-1794192045-claude-agent-claude-opus-5-5.md -> plans/I-1794192030-split-flow-py-into-flow-package/P-1794192045-c23d4516-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:96 plan_file: plans/P-1794196917-claude-agent-claude-opus-5-5.md -> plans/I-1794196901-resolver-uv-available-and-fail-loudly/P-1794196917-8bed7894-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:97 plan_file: plans/P-1794197261-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794197246-dual-ev-calibration-with-and-without-bugs/P-1794197261-edaaa218-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:98 plan_file: plans/P-1794205494-claude-agent-claude-opus-5-5.md -> plans/I-1794205479-flow-runs-in-dedicated-worktree-not-default-branch/P-1794205494-860c0862-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:99 plan_file: plans/P-1794205877-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794205862-resolver-commits-locally-skip-push-fix/P-1794205877-cbad6e47-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:100 plan_file: plans/P-1794220535-claude-agent-claude-opus-5-5.md -> plans/I-1794220515-run-ci-on-pull-requests-into-any-branch/P-1794220535-37b66e75-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:101 plan_file: plans/P-1794224651-claude-agent-claude-opus-5-5.md -> plans/I-1794224634-plan-owned-decomposition-with-plan-branch-children/P-1794224651-93cef978-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:102 plan_file: plans/P-1794225025-claude-agent-claude-opus-5-5.md -> plans/I-1794224634-plan-owned-decomposition-with-plan-branch-children--1-record-the-decomposition-decision-on-the-plan-ro/P-1794225025-924685ae-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:103 plan_file: plans/P-1794227872-claude-agent-claude-opus-5-5.md -> plans/I-1794224634-plan-owned-decomposition-with-plan-branch-children--2-nest-decomposed-children-under-the-plan-branch-m/P-1794227872-152246bc-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:104 plan_file: plans/P-1794268104-antigravity-agent-gemini-3.8-flash-medium.md -> plans/I-1794268087-mount-host-worktree-into-sandbox-containers/P-1794268104-fd29467b-antigravity-agent-gemini-3.8-flash-medium.md
  holon-knowledge/ledger/plans.jsonl:105 plan_file: plans/P-1794294214-claude-agent-claude-opus-5-5.md -> plans/I-1794294198-review-consensus-never-approves-past-an-open-critical/P-1794294214-0a2285cf-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:106 plan_file: plans/P-1794388224-claude-agent-claude-sonnet-5-5.md -> plans/I-1794388202-retire-the-flow-move-proof-snapshot/P-1794388224-67cb4776-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:107 plan_file: plans/P-1794388224-bd55e506-claude-agent-claude-sonnet-5-5.md -> plans/I-1794388202-review-loop-waits-for-ci-outside-the-iteration-budget/P-1794388224-bd55e506-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:108 plan_file: plans/P-1794391544-claude-agent-claude-sonnet-5-5.md -> plans/I-1794391536-refcap/P-1794391544-abdc9b6d-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:109 plan_file: plans/P-1794446836-claude-agent-claude-sonnet-5-5.md -> plans/I-1794446604-ids/P-1794446836-06880b0b-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:110 plan_file: plans/P-1794446908-claude-agent-claude-sonnet-5-5.md -> plans/I-1794446637-creds/P-1794446908-d2eae989-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:111 plan_file: plans/P-1794447543-claude-agent-claude-sonnet-5-5.md -> plans/I-1794447526-nofetch/P-1794447543-712b026e-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:112 plan_file: plans/P-1794452423-claude-agent-claude-sonnet-5-5.md -> plans/I-1794452407-nofetch2/P-1794452423-f3b616c7-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:113 plan_file: plans/P-1794456566-claude-agent-claude-sonnet-5-5.md -> plans/I-1794456550-calci/P-1794456566-07246b8e-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:114 plan_file: plans/P-1794458347-claude-agent-claude-sonnet-5-5.md -> plans/I-1794458330-stubfetch/P-1794458347-aa9e9dfa-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:115 plan_file: plans/P-1794461511-claude-agent-claude-sonnet-5-5.md -> plans/I-1794461494-failnames/P-1794461511-3cca99da-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:116 plan_file: plans/P-1794464849-claude-agent-claude-sonnet-5-5.md -> plans/I-1794464833-scrubcreds/P-1794464849-2a9a4a6b-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:117 plan_file: plans/P-1794476365-claude-agent-claude-sonnet-5-5.md -> plans/I-1794476347-nopost/P-1794476365-334a8266-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:118 plan_file: plans/P-1794478317-claude-agent-claude-sonnet-5-5.md -> plans/I-1794478300-salvage/P-1794478317-4eb07fff-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:119 plan_file: plans/P-1794480915-claude-agent-claude-sonnet-5-5.md -> plans/I-1794480898-mdgate/P-1794480915-c8cd81b2-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:120 plan_file: plans/P-1794523636-de5c2107-claude-agent-claude-sonnet-5-5.md -> plans/I-1794523620-keeppush/P-1794523636-de5c2107-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:121 plan_file: plans/P-1794523633-edd36e36-claude-agent-claude-sonnet-5-5.md -> plans/I-1794523617-revlog/P-1794523633-edd36e36-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:122 plan_file: plans/P-1794529184-344adb09-claude-agent-claude-sonnet-5-5.md -> plans/I-1794529168-pidir/P-1794529184-344adb09-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:123 plan_file: plans/P-1794529179-a03ed51a-claude-agent-claude-sonnet-5-5.md -> plans/I-1794529163-attrib/P-1794529179-a03ed51a-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:124 plan_file: plans/P-1794529181-eabd8e76-claude-agent-claude-sonnet-5-5.md -> plans/I-1794529165-calsync/P-1794529181-eabd8e76-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:125 plan_file: plans/P-1794546217-ec4d1e6a-claude-agent-claude-sonnet-5-5.md -> plans/I-1794546201-keepdiff/P-1794546217-ec4d1e6a-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:126 plan_file: plans/P-1794546221-53ff4e71-claude-agent-claude-sonnet-5-5.md -> plans/I-1794546205-measure/P-1794546221-53ff4e71-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:127 plan_file: plans/P-1794546213-1bdbac00-claude-agent-claude-sonnet-5-5.md -> plans/I-1794546197-chkgate/P-1794546213-1bdbac00-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:128 plan_file: plans/P-1794567531-a466a71e-claude-agent-claude-sonnet-5-5.md -> plans/I-1794567515-sleepfix/P-1794567531-a466a71e-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:129 plan_file: plans/P-1794567535-986a8099-claude-agent-claude-sonnet-5-5.md -> plans/I-1794567519-lockfrz/P-1794567535-986a8099-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:130 plan_file: plans/P-1794567538-ea208f06-claude-agent-claude-sonnet-5-5.md -> plans/I-1794567523-savepat/P-1794567538-ea208f06-claude-agent-claude-sonnet-5-5.md
  holon-knowledge/ledger/plans.jsonl:131 plan_file: plans/P-1794574974-21db7508-claude-agent-claude-opus-5-5.md -> plans/I-1794574957-calrebld/P-1794574974-21db7508-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:132 plan_file: plans/P-1794575045-ca973365-claude-agent-claude-opus-5-5.md -> plans/I-1794575029-nopost/P-1794575045-ca973365-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:133 plan_file: plans/P-1794617720-cbecf7aa-claude-agent-claude-opus-5-5.md -> plans/I-1794617704-touchset/P-1794617720-cbecf7aa-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:134 plan_file: plans/P-1794617990-33b5cb73-claude-agent-claude-opus-5-5.md -> plans/I-1794617704-touchset/P-1794617720-cbecf7aa-claude-agent-claude-opus-5-5--1-add-the-planner-touch-set-estimate-a-touch-set-p/P-1794617990-33b5cb73-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:135 plan_file: plans/P-1794657809-c6ebdbd6-claude-agent-claude-opus-5-5.md -> plans/I-1794657793-mergemth/P-1794657809-c6ebdbd6-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:136 plan_file: plans/P-1794657847-a55ed825-claude-agent-claude-opus-5-5.md -> plans/I-1794657831-calbrnch/P-1794657847-a55ed825-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:137 plan_file: plans/P-1794660305-d5b6233a-claude-agent-claude-opus-5-5.md -> plans/I-1794660290-probehttp/P-1794660305-d5b6233a-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:138 plan_file: plans/P-1794663411-f5308cfe-claude-agent-claude-opus-5-5.md -> plans/I-1794617704-touchset/P-1794617720-cbecf7aa-claude-agent-claude-opus-5-5--2-add-touch-set-admission-before-stage-3-and-enfor/P-1794663411-f5308cfe-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:139 plan_file: plans/P-1794663302-17aba811-claude-agent-claude-opus-5-5.md -> plans/I-1794663286-hygiene/P-1794663302-17aba811-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:140 plan_file: plans/P-1794727651-2a1e918e-claude-agent-claude-opus-5-5.md -> plans/I-1794727635-guardbase/P-1794727651-2a1e918e-claude-agent-claude-opus-5-5.md
  holon-knowledge/ledger/plans.jsonl:141 plan_file: plans/P-1794891094-eede5d91-claude-agent-claude-fable-5.md -> plans/I-1794891078-s5rerun/P-1794891094-eede5d91-claude-agent-claude-fable-5.md
  holon-knowledge/ledger/plans.jsonl:142 plan_file: plans/P-1794892061-52a8e074-claude-agent-claude-fable-5.md -> plans/I-1794891082-beadspil/P-1794892061-52a8e074-claude-agent-claude-fable-5.md
  holon-knowledge/ledger/plans.jsonl:143 plan_file: plans/P-1794976517-cac841ef-claude-agent-claude-fable-5.md -> plans/I-1794976501-macgate/P-1794976517-cac841ef-claude-agent-claude-fable-5.md
  holon-knowledge/ledger/plans.jsonl:144 plan_file: plans/P-1795087811-3604f487-claude-agent-claude-fable-5.md -> plans/I-1794976491-shortref/P-1795087811-3604f487-claude-agent-claude-fable-5.md
  holon-knowledge/ledger/executions.jsonl:462 calibration_report_path: plans/P-1792992691-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1792992678-entropy-budget-handoff-and-no-human-halt-in-execute/P-1792992691-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:468 calibration_report_path: plans/P-1793104256-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793104241-faster-unit-tests-xdist-and-cheaper-git-fixtures/P-1793104256-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:473 calibration_report_path: plans/P-1793189449-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793189433-review-loop-fail-closed-target-and-guards/P-1793189449-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:478 calibration_report_path: plans/P-1793200653-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793200635-review-ensemble-module/P-1793200653-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:483 calibration_report_path: plans/P-1793223729-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793223711-review-loop-wire-real-ensemble/P-1793223729-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:488 calibration_report_path: plans/P-1793241058-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793241043-claude-runner-permission-bypass-and-one-pass-placeholders/P-1793241058-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:508 calibration_report_path: plans/P-1793246292-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793246276-prompts-repeat-test-runs-only-for-performance-under-xdist/P-1793246292-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:509 calibration_report_path: plans/P-1793246383-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793246367-url-fuzz-leak-check-ignores-random-temp-paths/P-1793246383-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:510 calibration_report_path: plans/P-1793246475-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793246459-plain-language-entropy-and-expected-value-explainer/P-1793246475-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:511 calibration_report_path: plans/P-1793246567-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793246551-ci-timing-regression-gate/P-1793246567-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:518 calibration_report_path: plans/P-1793275710-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793275695-docs-sub-intent-merge-target-and-automatic-nested-prs/P-1793275710-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:519 calibration_report_path: plans/P-1793275622-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793275605-ledger-writes-are-pure-appends-never-reorder-intents/P-1793275622-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:526 calibration_report_path: plans/P-1793319521-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793319507-remaining-docs-follow-sub-intent-merge-rulings/P-1793319521-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:527 calibration_report_path: plans/P-1793319426-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793319411-calibration-report-passes-repository-prettier-check/P-1793319426-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:539 calibration_report_path: plans/P-1793334817-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1793334797-hac-ruff-complexity-gate-with-inline-baseline/P-1793334817-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:548 calibration_report_path: plans/P-1793349090-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1793349075-route-stage-4-reviewer-turns-through-review-loop-container/P-1793349090-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:549 calibration_report_path: plans/P-1793340046-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793340032-split-cli-into-self-registering-command-modules/P-1793340046-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:557 calibration_report_path: plans/P-1793339950-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793339935-flow-tests-never-write-the-checkout-knowledge-index/P-1793339950-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:558 calibration_report_path: plans/P-1793355620-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1793355604-remove-fallback-defaults-for-agent-and-model/P-1793355620-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:564 calibration_report_path: plans/P-1793425551-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1793425536-remove-github-pr-terminology-across-holon-flow/P-1793425551-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:568 calibration_report_path: plans/P-1793840814-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793840798-render-review-consensus-comment-from-reviewer-data/P-1793840814-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:572 calibration_report_path: plans/P-1793856357-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793856341-engine-owned-review-loop-single-shot-container/P-1793856357-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:588 calibration_report_path: plans/P-1793869856-antigravity-agent-gemini-4-argon_calibration.md -> plans/I-1793869839-stage-4-resolver-review-loop-converges/P-1793869856-antigravity-agent-gemini-4-argon_calibration.md
  holon-knowledge/ledger/executions.jsonl:600 calibration_report_path: plans/P-1793921914-antigravity-agent-gemini-4-argon_calibration.md -> plans/I-1793921896-bump-default-turn-timeout-to-1-hour/P-1793921914-antigravity-agent-gemini-4-argon_calibration.md
  holon-knowledge/ledger/executions.jsonl:623 calibration_report_path: plans/P-1793925096-antigravity-agent-gemini-4-argon_calibration.md -> plans/I-1793925076-increase-default-review-iteration-stage-budget-to-2-hours/P-1793925096-antigravity-agent-gemini-4-argon_calibration.md
  holon-knowledge/ledger/executions.jsonl:636 calibration_report_path: plans/P-1793932304-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1793932288-ci-is-a-post-review-gate-do-not-wait-before-reviewers/P-1793932304-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:648 calibration_report_path: plans/P-1793939982-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1793939965-stage-4-resolver-fixes-ci-and-test-failures/P-1793939982-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:653 calibration_report_path: plans/P-1793942508-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1793942491-stage-5-commits-promoted-knowledge-and-wisdom/P-1793942508-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:665 calibration_report_path: plans/P-1793953342-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1793953326-fix-resolver-run-turn-argument-signature/P-1793953342-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:677 calibration_report_path: plans/P-1793955693-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1793955677-suppress-docker-desktop-cli-hints/P-1793955693-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:835 calibration_report_path: plans/P-1793960347-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1793960332-treat-kb-wb-facts-as-immutable-append-only-records/P-1793960347-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:846 calibration_report_path: plans/P-1794003851-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794003835-fix-resolver-runner-build-cmd-attribute-error/P-1794003851-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:858 calibration_report_path: plans/P-1794009796-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794009779-flow-auto-create-worktree-if-not-exists/P-1794009796-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:870 calibration_report_path: plans/P-1794041623-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794041607-add-holon-models-command-to-list-supported-models/P-1794041623-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:876 calibration_report_path: plans/P-1794049540-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794049523-replace-repo-dir-with-target-worktree/P-1794049540-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:1028 calibration_report_path: plans/P-1794089619-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794089603-pass-prompts-to-agent-runners-via-files/P-1794089619-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:1030 calibration_report_path: plans/P-1794089619-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794089603-pass-prompts-to-agent-runners-via-files/P-1794089619-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:1037 calibration_report_path: plans/P-1794124844-claude-agent-claude-fable-5_calibration.md -> plans/I-1794124652-post-consensus-review-once-per-pr-exit/P-1794124844-claude-agent-claude-fable-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:1046 calibration_report_path: plans/P-1794124920-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794124904-fix-review-loop-observability-blindspot/P-1794124920-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:1058 calibration_report_path: plans/P-1794132519-claude-agent-claude-fable-5_calibration.md -> plans/I-1794132504-purge-harness-bean-references-from-code-and-docs/P-1794132519-claude-agent-claude-fable-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:1070 calibration_report_path: plans/P-1794136024-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794136007-prompts-carry-module-and-complexity-rules/P-1794136024-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:1129 calibration_report_path: plans/P-1794147403-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794147383-executor-git-repository-adapter/P-1794147403-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:1253 calibration_report_path: plans/P-1794136106-claude-agent-claude-fable-5_calibration.md -> plans/I-1794136091-persist-review-loop-sandbox-across-iterations/P-1794136106-claude-agent-claude-fable-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:1302 calibration_report_path: plans/P-1794182135-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794182121-validate-target-worktree-is-git-worktree/P-1794182135-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:1388 calibration_report_path: plans/P-1794182557-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794182541-unique-plan-and-execution-ids/P-1794182557-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:1428 calibration_report_path: plans/P-1794192045-c23d4516-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794192030-split-flow-py-into-flow-package/P-1794192045-c23d4516-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:1437 calibration_report_path: plans/P-1794192045-c23d4516-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794192030-split-flow-py-into-flow-package/P-1794192045-c23d4516-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:1469 calibration_report_path: plans/P-1794196917-8bed7894-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794196901-resolver-uv-available-and-fail-loudly/P-1794196917-8bed7894-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:1502 calibration_report_path: plans/P-1794196917-8bed7894-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794196901-resolver-uv-available-and-fail-loudly/P-1794196917-8bed7894-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:1532 calibration_report_path: plans/P-1794197261-edaaa218-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794197246-dual-ev-calibration-with-and-without-bugs/P-1794197261-edaaa218-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:1563 calibration_report_path: plans/P-1794205494-860c0862-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794205479-flow-runs-in-dedicated-worktree-not-default-branch/P-1794205494-860c0862-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:1576 calibration_report_path: plans/P-1794205877-cbad6e47-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794205862-resolver-commits-locally-skip-push-fix/P-1794205877-cbad6e47-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:1616 calibration_report_path: plans/P-1794220535-37b66e75-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794220515-run-ci-on-pull-requests-into-any-branch/P-1794220535-37b66e75-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:1658 calibration_report_path: plans/P-1794225025-924685ae-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794224634-plan-owned-decomposition-with-plan-branch-children--1-record-the-decomposition-decision-on-the-plan-ro/P-1794225025-924685ae-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:1737 calibration_report_path: plans/P-1794227872-152246bc-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794224634-plan-owned-decomposition-with-plan-branch-children--2-nest-decomposed-children-under-the-plan-branch-m/P-1794227872-152246bc-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:1932 calibration_report_path: plans/P-1794268104-fd29467b-antigravity-agent-gemini-3.8-flash-medium_calibration.md -> plans/I-1794268087-mount-host-worktree-into-sandbox-containers/P-1794268104-fd29467b-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  holon-knowledge/ledger/executions.jsonl:1982 calibration_report_path: plans/P-1794294214-0a2285cf-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794294198-review-consensus-never-approves-past-an-open-critical/P-1794294214-0a2285cf-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:2043 calibration_report_path: plans/P-1794388224-bd55e506-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794388202-review-loop-waits-for-ci-outside-the-iteration-budget/P-1794388224-bd55e506-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:2135 calibration_report_path: plans/P-1794388224-67cb4776-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794388202-retire-the-flow-move-proof-snapshot/P-1794388224-67cb4776-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:2235 calibration_report_path: plans/P-1794391254-8ec0e2e8-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794391245-worktree/P-1794391254-8ec0e2e8-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:2350 calibration_report_path: plans/P-1794391544-abdc9b6d-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794391536-refcap/P-1794391544-abdc9b6d-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:2388 calibration_report_path: plans/P-1794447543-712b026e-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794447526-nofetch/P-1794447543-712b026e-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:2408 calibration_report_path: plans/P-1794446908-d2eae989-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794446637-creds/P-1794446908-d2eae989-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:2409 calibration_report_path: plans/P-1794452423-f3b616c7-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794452407-nofetch2/P-1794452423-f3b616c7-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:2522 calibration_report_path: plans/P-1794461511-3cca99da-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794461494-failnames/P-1794461511-3cca99da-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:2644 calibration_report_path: plans/P-1794464849-2a9a4a6b-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794464833-scrubcreds/P-1794464849-2a9a4a6b-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:2753 calibration_report_path: plans/P-1794456566-07246b8e-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794456550-calci/P-1794456566-07246b8e-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:2800 calibration_report_path: plans/P-1794458347-aa9e9dfa-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794458330-stubfetch/P-1794458347-aa9e9dfa-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:2827 calibration_report_path: plans/P-1794476365-334a8266-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794476347-nopost/P-1794476365-334a8266-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:2896 calibration_report_path: plans/P-1794478317-4eb07fff-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794478300-salvage/P-1794478317-4eb07fff-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:2924 calibration_report_path: plans/P-1794480915-c8cd81b2-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794480898-mdgate/P-1794480915-c8cd81b2-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:2968 calibration_report_path: plans/P-1794446836-06880b0b-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794446604-ids/P-1794446836-06880b0b-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:3089 calibration_report_path: plans/P-1794523636-de5c2107-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794523620-keeppush/P-1794523636-de5c2107-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:3113 calibration_report_path: plans/P-1794523633-edd36e36-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794523617-revlog/P-1794523633-edd36e36-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:3237 calibration_report_path: plans/P-1794529184-344adb09-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794529168-pidir/P-1794529184-344adb09-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:3279 calibration_report_path: plans/P-1794529179-a03ed51a-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794529163-attrib/P-1794529179-a03ed51a-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:3473 calibration_report_path: plans/P-1794529181-eabd8e76-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794529165-calsync/P-1794529181-eabd8e76-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:3574 calibration_report_path: plans/P-1794546217-ec4d1e6a-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794546201-keepdiff/P-1794546217-ec4d1e6a-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:3602 calibration_report_path: plans/P-1794546213-1bdbac00-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794546197-chkgate/P-1794546213-1bdbac00-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:3671 calibration_report_path: plans/P-1794546221-53ff4e71-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794546205-measure/P-1794546221-53ff4e71-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:3767 calibration_report_path: plans/P-1794567531-a466a71e-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794567515-sleepfix/P-1794567531-a466a71e-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:3797 calibration_report_path: plans/P-1794567535-986a8099-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794567519-lockfrz/P-1794567535-986a8099-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:3906 calibration_report_path: plans/P-1794567538-ea208f06-claude-agent-claude-sonnet-5-5_calibration.md -> plans/I-1794567523-savepat/P-1794567538-ea208f06-claude-agent-claude-sonnet-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:3960 calibration_report_path: plans/P-1794575045-ca973365-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794575029-nopost/P-1794575045-ca973365-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:3972 calibration_report_path: plans/P-1794574974-21db7508-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794574957-calrebld/P-1794574974-21db7508-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:3979 calibration_report_path: plans/P-1793246201-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1793246184-execute-failure-keeps-partial-code-and-resumes-from-it/P-1793246201-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:3996 calibration_report_path: plans/P-1794657809-c6ebdbd6-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794657793-mergemth/P-1794657809-c6ebdbd6-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:4038 calibration_report_path: plans/P-1794657847-a55ed825-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794657831-calbrnch/P-1794657847-a55ed825-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:4087 calibration_report_path: plans/P-1794660305-d5b6233a-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794660290-probehttp/P-1794660305-d5b6233a-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:4141 calibration_report_path: plans/P-1794663302-17aba811-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794663286-hygiene/P-1794663302-17aba811-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:4327 calibration_report_path: plans/P-1794663411-f5308cfe-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794617704-touchset/P-1794617720-cbecf7aa-claude-agent-claude-opus-5-5--2-add-touch-set-admission-before-stage-3-and-enfor/P-1794663411-f5308cfe-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:4330 calibration_report_path: plans/P-1794663302-17aba811-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794663286-hygiene/P-1794663302-17aba811-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:4371 calibration_report_path: plans/P-1794727651-2a1e918e-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794727635-guardbase/P-1794727651-2a1e918e-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:4411 calibration_report_path: plans/P-1794617720-cbecf7aa-claude-agent-claude-opus-5-5_calibration.md -> plans/I-1794617704-touchset/P-1794617720-cbecf7aa-claude-agent-claude-opus-5-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:4469 calibration_report_path: plans/P-1794892061-52a8e074-claude-agent-claude-fable-5_calibration.md -> plans/I-1794891082-beadspil/P-1794892061-52a8e074-claude-agent-claude-fable-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:4487 calibration_report_path: plans/P-1794892061-52a8e074-claude-agent-claude-fable-5_calibration.md -> plans/I-1794891082-beadspil/P-1794892061-52a8e074-claude-agent-claude-fable-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:4499 calibration_report_path: plans/P-1794891094-eede5d91-claude-agent-claude-fable-5_calibration.md -> plans/I-1794891078-s5rerun/P-1794891094-eede5d91-claude-agent-claude-fable-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:4501 calibration_report_path: plans/P-1794891094-eede5d91-claude-agent-claude-fable-5_calibration.md -> plans/I-1794891078-s5rerun/P-1794891094-eede5d91-claude-agent-claude-fable-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:4554 calibration_report_path: plans/P-1794891094-eede5d91-claude-agent-claude-fable-5_calibration.md -> plans/I-1794891078-s5rerun/P-1794891094-eede5d91-claude-agent-claude-fable-5_calibration.md
  holon-knowledge/ledger/executions.jsonl:4595 calibration_report_path: plans/P-1794976517-cac841ef-claude-agent-claude-fable-5_calibration.md -> plans/I-1794976501-macgate/P-1794976517-cac841ef-claude-agent-claude-fable-5_calibration.md
15 new path(s) match no supplied pull request head branch:
  plans/I-1787048466-executor-architecture-spec/P-1787048480-antigravity-agent-gemini-3.5-flash.md
  plans/I-1787051498-executor-plan-calibration/P-1787051525-antigravity-agent-gemini-3.5-flash.md
  plans/I-1787051498-executor-plan-calibration/P-1787051525-antigravity-agent-gemini-3.5-flash_calibration.md
  plans/I-1787563553-record-agent-version/P-1787563567-antigravity-agent-gemini-3.5-flash.md
  plans/I-1787563553-record-agent-version/P-1787563567-antigravity-agent-gemini-3.5-flash_calibration.md
  plans/I-1787928862-token-reduction-phase2/P-1787928877-antigravity-agent-gemini-3.5-flash.md
  plans/I-1787928920-token-reduction-phase3/P-1787928942-antigravity-agent-gemini-3.5-flash.md
  plans/I-1787928927-token-reduction-phase4/P-1787928955-antigravity-agent-gemini-3.5-flash.md
  plans/I-1788172624-fix-mitm-proxy-400/P-1788173219-antigravity-agent-gemini-3.5-flash.md
  plans/I-1788172624-fix-mitm-proxy-400/P-1788173219-antigravity-agent-gemini-3.5-flash_calibration.md
  plans/I-1788176325-fix-mitm-telemetry-logging-and-sse-parsing/P-1788176334-antigravity-agent-gemini-3.5-flash.md
  plans/I-1790379374-add-holon-calibrate-command/P-1790379384-antigravity-agent-gemini-3.8-flash-medium.md
  plans/I-1790379374-add-holon-calibrate-command/P-1790379384-antigravity-agent-gemini-3.8-flash-medium_calibration.md
  plans/I-1794224634-plan-owned-decomposition-with-plan-branch-children/P-1794224651-93cef978-claude-agent-claude-opus-5-5.md
  plans/I-1794976491-shortref/P-1795087811-3604f487-claude-agent-claude-fable-5.md
```

### Step 8: Documentation, templates, config examples and module budgets

- `docs/git_flow.md`: section 1.5 no longer claims project-wide unique IDs keyed bare (plans are keyed by intent,
  executions by full path); the branch grammar tables and examples use `P-{timestamp}-{ticker}-{model code}` and
  `E-{timestamp}-{ticker}-{model code}`; section 2.5.1 drops the agent/model label shortening, records that tickers and
  model codes are never shortened, adds the 32-character slug rule and the two-level nesting guarantee, and notes that
  older rows may still carry `original_agent`/`original_model`; new section 2.5.2 documents tickers, the model-code rule
  with the required examples, same-second variants, the two still-valid older generations (never renamed) and the
  `plans/{intent}/` layout with the one-off migration; the stage table shows the nested plan and report paths.
- `README.md` (plan ID section with tickers, model codes and the old generations; branch grammar and example IDs),
  `docs/ledger_schema.md` (calibration report path, retired `original_agent`/`original_model`, the plan file layout, the
  migration as the one exempted rewrite, the (intent, plan ID) duplicate check), `docs/sandbox/create_plan.md`,
  `docs/sandbox/execute_plan.md`, `docs/planning_and_evaluation.md`, `docs/examples.md`, `docs/budgets.md`,
  `docs/directory_structure.md`, `.agents/holon_flow.md`: example IDs and plan paths moved to the new scheme. The one
  real historical plan ID cited in `docs/metrics-explained.md` stays as it is (it names an existing plan).
- `holon-config/world/constraints.md` §1 and its scaffold twin in `scaffold.py`: only the branch-name examples changed;
  no rule, threshold or constraint wording changed. `holon-config/prompts/planner.template.md` (in Step 4) uses the
  `{plan_file}` placeholder; `executor.template.md`, `review_loop.template.md` and `holon-config/metrics/README.md` show
  no plan or execution ID, branch name or plan path, so they needed no change.
- `flow_module_budgets.json`: adjusted in Steps 4–6 only for modules whose size changed (raised `calibrate_rebuild.py`
  +1, `git_refs.py` +1, `stages/execute.py` +2; lowered `stages/plan.py`, `stages/plan_decision.py`,
  `stages/calibrate.py`, `stages/intent.py`), each with its reason in the description.
- Grep gate: no `{hex}`, `{agent}-{model}`, flat `plans/P-` or `plans/{plan_id}` example remains outside the deliberate
  older-generation notes.
- Prettier: `npx prettier` in this sandbox resolves a cached 3.8.4 that flags 39 files; the same 39 fail on the
  pre-migration tree (`a6943046`) under 3.8.4, so they are not caused by this change. Under the current release (3.10.1,
  what an unpinned `npx --yes prettier` fetches) every markdown file passes once the previous execution's notes
  (`executions/E-1795088115-...md`, which already failed before this run) were re-wrapped by Prettier (whitespace only,
  no wording change).
- Verified: full suite `-n auto --dist worksteal`: 4420 passed, 1 skipped in `17.97s`; `uv run task lint` clean.
