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

## Tests Run

(pending)
