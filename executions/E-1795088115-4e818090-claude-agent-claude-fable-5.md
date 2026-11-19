# Execution Record: E-1795088115-4e818090-claude-agent-claude-fable-5

- **Plan ID:** P-1795087811-3604f487-claude-agent-claude-fable-5
- **Intent ID:** I-1794976491-shortref
- **Agent:** claude-agent/claude-fable-5

## Summary

In progress. Implements the short deterministic naming scheme: agent tickers (CLA/ANA/PIA), pure
model-code derivation, hexless `P-{epoch}-{ticker}-{model code}` / `E-{epoch}-{ticker}-{model code}`
IDs, plan files keyed under `plans/{intent}/`, lookups keyed on (intent, plan ID), slug length
enforcement, the one-off ledger/plan migration, and documentation updates.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Inventory of every ID and plan-path call site

Two search strategies converged (grep for `P-`/`E-` id patterns and `plans/` path joins; import graph
of `ref_names`/`ledger` builders). Every site below falls inside the widened touch set; none outside it.

**ID constructors** (change: new `P-{epoch}-{ticker}-{model code}` format, Step 3):

- `apps/sandbox-executor/src/sandbox_executor/ledger.py` — `build_plan_id`, `build_execution_id`
  (callers: `entrypoint/planner.py:322`, `entrypoint/executor.py:1064`, `flow/stages/plan.py:75`,
  `flow/stages/execute.py:145`). `flow/review/ids.py` builds `REV-` ids only — out of scope, kept.

**Parsers / pattern sites** (change: accept all three generations, Step 3):

- `entrypoint/planner.py` — `PLAN_FILE_PATTERN` (nested paths); consumed by `flow/stages/plan.py` and
  `flow/stages/plan_decision.py`.
- `flow/stages/execute.py` — `/E-(\d+)-[^/]+/_$` regex (generation-agnostic, no change).
- `calibration.py` — `extract_branch_components` (segment-based, generation-agnostic).
- `ledger.py` — `_extract_parent_ref` (reworked to full parent path for intent-keyed checks, Step 5).
- `flow/git_refs.py` — `_discover_child_branch` `preferred_suffix` call sites in `flow/stages/plan.py`
  and `flow/stages/execute.py` (new ticker/model-code suffix).

**Plan/report path construction** (change: `plans/{intent}/...` via new plan-path module, Step 4):

- `entrypoint/planner.py` — `plan_file_rel`; `flow/stages/plan.py` and `plan_decision.py` fallbacks;
  `entrypoint/executor.py` plan-file read (row value plus flat fallback).
- `calibration.py` — `run_calibrate` report path, `_parse_predicted_from_plan_markdown` candidates,
  report header plan link; `flow/calibrate_rerun.py` `report_rel`; `flow/stages/calibrate.py`
  fallback report path; `flow/calibrate_rebuild.py` `_is_stage5_path` (depth check).
- `apps/sandbox-executor/src/sandbox_executor/scaffold.py` — planner template write-protocol text
  names the plan file path (template placeholder becomes the full plan-file path).
- `scripts/pre_commit_check.py` — `^plans/.*` exemption already spans nested paths (no change needed;
  verified).

**Lookup keying** (change: key plans by (intent, plan ID), executions by full path, Step 5):

- `calibration.py` — `parse_predicted_metrics` / `_parse_predicted_from_plans_ledger` (plan-id-only
  match), `generate_calibration` executions.jsonl match (`execution_id` OR `plan_branch` becomes
  full-path keyed).
- `entrypoint/executor.py` — plans.jsonl lookup matches `plan_id` only (adds intent match).
- `ledger.py` — `validate_ledger_unique_ids` cross-parent semantics (repeated intent-and-plan pair
  fails; distinct intents may now share a new-format id).
- `flow/checkpoint.py` — stores `plan_branch`/`execution_branch` as full paths already (verified, no
  schema change).
- `cli/commands/calibrate.py` — takes the full execution branch (already full-path keyed).

**Tickers / slugs** (Steps 2 and 6):

- `agent_defaults.py` (ticker declarations), `cli/__init__.py` `run_doctor` (rejection),
  `flow/stages/intent.py` + `entrypoint/intent_creator.py` (intent-open rejection, slug length),
  `decomposition_rule.py` `_sub_intent_dicts` (derived child slug cut), `ref_names.py` (label digests
  removed, slug shortening and length check kept).

**Docs/templates/config** (Step 8): `README.md`; `docs/git_flow.md`, `docs/directory_structure.md`,
`docs/examples.md`, `docs/ledger_schema.md`, `docs/core_concepts.md` and subpages printing IDs;
`.agents/holon_flow.md`; `holon-config/prompts/planner.template.md`, `executor.template.md`,
`review_loop.template.md`; `holon-config/metrics/README.md`; `holon-config/world/constraints.md`
(§1 branch-name examples only); `apps/sandbox-executor/flow_module_budgets.json`.

**Ledger reality checks** (inputs to Steps 5 and 7, from the real ledgers): `plans.jsonl` has no
repeated (intent, plan) pair and no plan id shared across intents; `executions.jsonl` has repeated
(plan, execution) summary pairs from superseding rows (`ledger_revision` 1→2) and reruns, and one row
whose `plan_branch` embeds the execution segment — the parent of an execution row is therefore
normalised to the path up to its last `P-` segment.

### Step 2: Agent tickers and the pure model-code function

- `agent_defaults.py`: `AGENT_TICKERS` declared beside the runner definitions (claude = CLA,
  antigravity = ANA, pi = PIA only; no fallback).
- New focused module `naming.py` beside `ref_names.py`: `model_code` (pure rule, no table),
  `ticker_for`/`agent_ticker` with `MissingTickerError` naming the runner, new-generation
  `stage_id`, three-generation `parse_stage_id`, and `distinct_stage_id` same-second collision
  variants (`-2` … `-9`, then `StageIdCollisionError`).
- `cli/__init__.py` `run_doctor`: rejects a resolved runner without a ticker before the probe.
- `ref_names.resolve_root_intent_branch`: rejects a ticker-less runner before any branch is created;
  `flow/stages/intent.py` surfaces `MissingTickerError` as a Stage 1 failure.
- Tests: `tests/test_naming.py` (26 tests) — the seven required mappings, the
  `qwen2.5-coder:32b` ref-validity case, duplicate-ticker failure, doctor and intent-time rejection.
- Verified: `uv run pytest apps/sandbox-executor/tests/test_naming.py` (26 passed, 0.20s);
  `test_agent_defaults.py test_ref_names.py test_cli.py` (396 passed, 2.19s); ruff clean.

### Step 3: New-format ID generation with collision safety; both old generations stay parseable

- `ledger.py`: `build_plan_id`/`build_execution_id` now compose `P-{epoch}-{ticker}-{model code}` /
  `E-{epoch}-{ticker}-{model code}` via `naming.stage_id`, derive same-second variants from a `taken`
  set via `naming.distinct_stage_id`, and measure the composed branch with
  `ref_names.ensure_stage_segment_fits` (plans reserve room for the execution segment below).
- `ref_names.py`: the label-digest segment fitting (`fit_plan_id`/`fit_execution_id`,
  `_segment_parts`) and `original_labels` are removed (no reader of `original_agent`/`original_model`
  existed); slug shortening, `fit_parts` and the length check stay; `ensure_chain_fits` measures real
  ticker and model-code lengths; slug helpers (`MAX_SLUG_LENGTH`, `validate_supplied_slug`,
  `truncate_derived_slug`) added for Step 6 wiring. (The Step 6 label-digest removal landed here
  because the same functions had to change for the new format; Step 6 wires the slug limits.)
- `entrypoint/planner.py`: taken ids read from the intent's `plans.jsonl` rows
  (`_taken_plan_ids`); raw model name passed to the builder; no `original_*` fields on new rows; a
  model name with no derivable code fails clearly.
- `entrypoint/executor.py`: `_taken_execution_ids` lists sibling `E-` branches (local and origin);
  no `original_*` fields on new rows.
- `flow/stages/plan.py`, `flow/stages/execute.py`: dry-run naming uses the raw model; branch
  discovery suffix is now `-{ticker}-{model code}`; a ticker-less runner fails the stage before
  dispatch. `flow/git_refs.py` `_matches_dispatch_suffix` accepts a same-second variant suffix.
- `flow_module_budgets.json`: budgets raised for `git_refs.py` (+6), `stages/plan.py` (+4),
  `stages/execute.py` (+6), `stages/intent.py` (+1) with the reason recorded in the description.
- Tests: `test_ref_names.py` rewritten for the new arithmetic (two-level 32-char nesting fits 255
  unshortened; deeper nesting falls back to slug shortening and the kept length check);
  `test_unique_stage_ids.py` same-second variant tests for plans and executions;
  `test_naming.py` parses every distinct real-ledger id as exactly one legacy generation;
  `test_planner.py` model-code branch cases; flow fakes emit new-format names.
- Verified: targeted suites green; full suite `-n auto --dist worksteal`: 1 failed, 4338 passed —
  the one failure (`test_workspace_survival_stress`) clones committed HEAD and resolves at commit
  (re-verified after commit below); ruff + format clean.
