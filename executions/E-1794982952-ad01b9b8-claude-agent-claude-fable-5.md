# Execution Record: E-1794982952-ad01b9b8-claude-agent-claude-fable-5

- **Plan ID:** P-1794976507-654a2c7f-claude-agent-claude-fable-5
- **Intent:** I-1794976491-shortref
- **Agent:** claude-agent/claude-fable-5

## Summary

Implementing the short-ref naming scheme: agent tickers, pure model codes, hex-free
gen-3 plan/execution IDs (`P-{epoch}-{ticker}-{model code}`), nested plan-file paths
keyed by intent, intent-scoped lookups, the 32-character slug limit, and the one-off
migration of `plans/` and the two ledger path fields.

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

Two full sweeps (one per domain) converged on a closed site list; no third sweep added sites.

**Plan-path builders (flat `plans/{plan_id}.md` / `_calibration.md`):**

- `entrypoint/planner.py` — `plan_file_rel` (the single plan-path builder), `PLAN_FILE_PATTERN`
- `calibration.py` — `_parse_predicted_from_plan_markdown`, `format_markdown_report` link,
  `run_calibrate` (`report_rel = plans/{plan_id}_calibration.md`)
- `flow/stages/calibrate.py` — fallback report path
- `flow/calibrate_rerun.py` — `report_staleness`
- `flow/calibrate_rebuild.py` — `is_calibration_path` (requires exactly one `/`, rejects nested)
- `flow/stages/plan.py`, `flow/stages/plan_decision.py` — inline `plans/{plan_id}.md` fallbacks

**Lookups by bare plan ID:** `calibration._parse_predicted_from_plans_ledger` (substring match),
`entrypoint/executor.py` plan-row scan, `entrypoint/review_loop.py` head-branch scan,
`flow/stages/plan.py`, `flow/stages/plan_decision.py`, `flow/in_flight.read_plan_touch_set`.

**Lookups by bare execution ID:** `flow/git_refs._latest_execution_summary` (callers in
`flow/stages/execute.py`, `flow/stages/integration.py`, `flow/partial_salvage.py`),
`calibration.parse_actual_metrics` / `generate_calibration`, `calibration_ev.is_matching_record`.
Execution notes path `executions/{exec_id}.md` is built flat in `entrypoint/executor.py`,
`entrypoint/execution_workspace.py` (`ENGINE_OWNED_NOTES_GLOB`), `flow/partial_salvage.py`.

**Duplicate check:** `ledger.validate_ledger_unique_ids` + `_extract_parent_ref`; call sites
`planner.main`, `flow/git_refs._append_ledger_row`, `ledger.validate_stage_ledger` (via
`flow/pipeline.py`), `ledger.append_summary_record`. Current rule: same ID across *different*
parents fails; same-parent repeats (superseding rows, `ledger_revision` bumps) pass. Historical
`executions.jsonl` contains two repeated `(plan_branch, execution_id)` pairs with non-increasing
revisions (`[1, null]`, `[1, 1]`), so the new pair rule is enforced for plan rows (at most one
creation row with `ledger_revision <= 1` per pair) and executions stay guarded at creation time.

**Append-only guard:** runtime byte-prefix guard only (`ledger.atomic_ledger_append` /
`assert_ledger_prefix`); `scripts/pre_commit_check.py` does not compare ledgers against git
history. The migration rewrites the two files directly (outside the append path), which is the
sanctioned route; the runtime guard is untouched and still rejects non-exempt rewrites.

**Checkpoints/CLI:** checkpoints and CLI already key on full branches (no bare-ID fields).

**Intent-time hook points:** host side `flow/stages/intent.py:run_intent_stage` (before
`resolve_root_intent_branch`); container side `entrypoint/intent_creator.py:main` before
`start_branch_from_host_fetch`. Doctor: `cli/__init__.py:run_doctor`.

### Step 2: Agent ticker registry with doctor and intent-time enforcement

`AGENT_TICKERS` (`claude = CLA`, `antigravity = ANA`, `pi = PIA`) and `ticker_for_agent` (typed
`MissingAgentTickerError`, no fallback) added to `agent_defaults.py`. `holon doctor` rejects a
tickerless runner by name before the preflight probe; `run_intent_stage` refuses one before any
branch name is resolved or created. Tests in `test_agent_defaults.py` cover the three fixed
tickers, uniqueness, the three-uppercase-letter format, doctor rejection and intent-time rejection
with no branch created. `uv run pytest apps/sandbox-executor/tests/test_agent_defaults.py`: 50
passed.

### Step 3: Pure model-code derivation function

`plan_paths.model_code`: drops everything before `/`, first letter of each alphabetic word
upper-cased, version words kept whole with consecutive ones dot-joined, invalid ref characters
stripped, never empty. All seven required mappings pass as a parametrised test; fuzzed names
(including `qwen2.5-coder:32b`) and every historical model label in `plans.jsonl` validate with
`git check-ref-format --allow-onelevel`. No lookup table exists.

### Step 4: Gen-3 ID builders, three-generation parser, collision rule

New module `apps/sandbox-executor/src/sandbox_executor/plan_paths.py` (ref_names.py would have
exceeded the ~400-line sizing rule): `new_stage_id` / `uncollided_stage_id` build
`P-/E-{epoch}-{ticker}-{model code}` with no hex; a same-parent taken name bumps the epoch by one
second (at most 5 bumps) or raises `DuplicateStageNameError`, never overwriting.
`parse_stage_id` classifies all three generations (8-char lowercase hex second field = gen 2,
three uppercase letters = gen 3, else gen 1) and exposes `generation` on the result; a fuzz test
classifies 100% of real ledger IDs as gen 1/2. `ledger.build_plan_id` / `build_execution_id` now
take an `exists` callback (planner checks plan file + (intent, plan) ledger pair + local ref;
executor checks (plan, execution) pair + local ref) and measure ref-limit room.
`ref_names.py` lost the hex/label fitting machinery (`fit_plan_id`, `fit_execution_id`,
`original_labels`); `ensure_chain_fits` measures gen-3 segments (`MAX_STAGE_SEGMENT_LENGTH`).
`test_plan_paths.py` and rewritten `test_ref_names.py` / `test_unique_stage_ids.py`: 90 passed.

## Tests Run

(pending)
