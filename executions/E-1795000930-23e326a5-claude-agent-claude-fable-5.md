# Execution Record: E-1795000930-23e326a5-claude-agent-claude-fable-5

## Summary

Implements plan `P-1794976507-654a2c7f-claude-agent-claude-fable-5` for intent `I-1794976491-shortref`: replace
hex-bearing plan/execution IDs with `{ticker}-{model code}` names, key plan files and lookups by intent path, keep old
generations parseable, and migrate the existing `plans/` tree and ledger path fields under the one-off maintainer
exemption.

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

- ID construction (writes): `ledger.py` `build_plan_id` / `build_execution_id` / `build_stage_disambiguator` (hex
  source); `ref_names.py` `fit_plan_id` / `fit_execution_id` / `original_labels` / `ensure_chain_fits`; callers
  `entrypoint/planner.py:322`, `entrypoint/executor.py:1064`, `flow/stages/plan.py:75`, `flow/stages/execute.py:145`.
- Flat plan-file paths (writes): `entrypoint/planner.py` `plan_file_rel` + `PLAN_FILE_PATTERN` (`plans/{plan_id}.md`);
  `calibration.py:1580` (`plans/{plan_id}_calibration.md`); `flow/stages/plan.py:87,162` and
  `flow/stages/plan_decision.py:57` (fallback `plans/{plan_id}.md`); planner prompt templates `scaffold.py:193` and
  `holon-config/prompts/planner.template.md`.
- Flat lookups (reads): `calibration.py` `_parse_predicted_from_plans_ledger` (bare `plan_id` ledger match),
  `_parse_predicted_from_plan_markdown` (flat `plans/{plan_id}.md` candidates); `entrypoint/executor.py:1101` (plan row
  matched by branch tail `plan_id` only); `flow/calibrate_rerun.py:134` (`plans/{plan_id}_calibration.md`);
  `flow/calibrate_rebuild.py:40` `is_calibration_path` (requires exactly one `/`); `flow/stages/plan.py:162` and
  `flow/stages/plan_decision.py` (rows filtered by bare `plan_id`).
- Duplicate check: `ledger.py` `validate_ledger_unique_ids` (bare-ID collision across distinct parents; same-parent
  repeats are legitimate superseding rows appended by `flow/git_refs.py:_append_ledger_row` and
  `flow/stages/plan_decision.py:_backfill_plan_row`), called from `entrypoint/planner.py:592`, `flow/git_refs.py:291`,
  `flow/pipeline.py:214`.
- Append-only gates for the migration commit: `tests/test_ledger_append_only.py` guards runtime appends (byte-prefix
  assertion in `atomic_ledger_append`), not git history; the migration rewrites the ledgers outside
  `atomic_ledger_append`, so no guard weakening is needed and the guard keeps rejecting non-exempt rewrites.
  `scripts/pre_commit_check.py` checks credentials only.
- Checkpoints (`flow/checkpoint.py`) already store full branch paths; CLI `calibrate` takes the full execution branch;
  CLI ledger queries filter by substring, not bare ID — no change needed beyond the shared helpers.

### Step 2: Agent ticker registry

`AGENT_TICKERS` (`claude = CLA`, `antigravity = ANA`, `pi = PIA`) and `ticker_for` with `MissingAgentTickerError` in
`agent_defaults.py`; no fallback and no derivation. Wired into `holon doctor` (rejection before the probe, `ticker` in
the JSON agent payload) and into intent opening in both `flow/stages/intent.py` and `entrypoint/intent_creator.py`,
before any branch is created, naming the runner. Tests in `test_agent_defaults.py` cover the three fixed tickers,
format, uniqueness, and both rejection paths (asserting no branch exists after refusal).

### Step 3: Pure model-code derivation

New module `plan_paths.py`: `model_code` (no table; first letters upper-cased, version parts kept whole and dot-joined,
`provider/` prefix dropped, ref-invalid characters removed) plus `stage_label` = `{ticker}-{model code}`. All seven
required mappings pass and fuzzed local-model names validate against `git check-ref-format` (`test_plan_paths.py`).

### Step 4: Generation-3 builders, three-generation parser, collision rule

`parse_stage_id` classifies all three generations (8-hex second field = gen 2, three-uppercase second field = gen 3,
else gen 1) and exposes the generation; a test parses every historical ID in both real ledgers as gen 1/2.
`ledger.build_plan_id` / `build_execution_id` now produce `P-{epoch}-{ticker}-{model code}` with an `exists` collision
probe that bumps the epoch by one second deterministically (up to 60) and then raises `DuplicateStageIdError` — never
overwriting. `fit_plan_id` / `fit_execution_id` in `ref_names.py` take the short label, never shorten it, and raise
`RefTooLongError` when the parent chain cannot fit. `validate_ledger_unique_ids` keeps the cross-parent collision check
for gen 1/2 only (gen-3 names legally repeat across parents).

### Step 5: Nested plan-file paths

`plan_paths.plan_file_rel` / `calibration_report_rel` mirror the full branch nesting (root, child intents, plans under
plans). Writers switched: `entrypoint/planner.py` (plan file + `plan_file` placeholder threaded into
`holon-config/prompts/planner.template.md` and the `scaffold.py` fallback), `calibration.py` and
`flow/stages/calibrate.py` (report written beside the plan file via `resolve_calibration_report_rel`).
`PLAN_FILE_PATTERN` accepts nested paths.

### Step 6: Lookups keyed on (intent, plan ID) / full path

`ledger.latest_plan_row`, `plan_pair_exists`, `execution_pair_exists`; the executor's plan lookup,
`flow/stages/plan.py`, `flow/stages/plan_decision.py`, `calibration.parse_predicted_metrics` and
`flow/calibrate_rerun.report_staleness` all resolve by (intent, plan ID) with labelled flat old-generation fallbacks;
`flow/calibrate_rebuild.is_calibration_path` accepts nested reports. Branch discovery (`_discover_child_branch`) accepts
both-generation suffixes via `preferred_stage_suffixes`. New tests: same-second two-intents resolution and
old-generation fallback in `test_calibration.py`; duplicate-pair failure in `test_unique_stage_ids.py`.

### Step 7: Agent/model shortening removal

`original_labels` and the label digests are gone from `ref_names.py`; new ledger rows no longer carry `original_agent` /
`original_model` (planner and executor writers updated); slug shortening and the length check remain. Readers were
already tolerant of the old fields.

### Step 8: 32-character slug limit and fit proof

`MAX_SLUG_LENGTH = 32`, `SlugTooLongError` (message gives limit and length) and `truncate_slug` (word-boundary cut,
never empty) in `ref_names.py`. A supplied root slug over the limit is rejected at intent open in the flow stage and in
`intent_creator.py`, before any branch; derived root and child slugs are truncated (`fit_child_intent_branch` records
the original in `originals`). Acceptance test: root + two nested child levels with 32-character slugs compose a gen-3
calibrated branch of ≤ 255 characters with `refs/heads/` and `/calibrated`, unshortened; deeper nesting falls back to
the existing shortening/length check (tested).

Flow module size ratchet: `flow_module_budgets.json` entries for `stages/intent.py` (245), `stages/plan.py` (213),
`stages/plan_decision.py` (115), `stages/execute.py` (416), `calibrate_rerun.py` (290) and `git_refs.py` (402) were
raised minimally for the intent-time enforcement and the old-generation fallbacks; shared helpers were deliberately
placed in `plan_paths.py` / `ledger.py` instead of growing the flow package further.

### Step 9: One-off migration of plans/ and the two ledger path fields

New `plan_migration.py` with thin CLI `holon migrate-plans` (`cli/commands/migrate_plans.py`); dry-run by default,
`--apply` to execute, `git mv` for moves, every ledger rewrite computed and field-masked byte-verified before anything
on disk changes. Run on the real tree with a PR head-branch list built from the 67 locally-known `origin/` refs plus
every `plan_branch` recorded in `executions.jsonl` (195 branches):

- Dry run: `265 file move(s), 242 ledger rewrite(s)`; legacy bucket 25 of 265 files (`9.4%`, under the `~20%` abort
  threshold) — plan files with no ledger row and no matching PR head branch (salvaged partial plans), moved to
  `plans/legacy/`.
- Apply: 265 moves (`git status` shows pure renames), 143 `plan_file` rewrites in `plans.jsonl` and 99
  `calibration_report_path` rewrites in `executions.jsonl`.
- Independent verification: with the two fields masked, both ledgers are byte-identical to their pre-migration state;
  row counts unchanged (143 and 4557 rows); nothing left directly in `plans/` outside intent folders and
  `plans/legacy/`; every rewritten path points at an existing file (`missing_targets` empty).
- Idempotence: a second `--apply` run reported `0 file move(s), 0 ledger rewrite(s)` (also exercised twice in
  `test_plan_migration.py`).
- The append-only guard is untouched; `test_plan_migration.py` proves it still rejects a non-exempt historical-row edit
  after the migration.

## Tests Run
