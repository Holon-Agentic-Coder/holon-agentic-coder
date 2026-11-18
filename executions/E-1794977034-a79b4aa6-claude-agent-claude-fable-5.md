# Execution Record: E-1794977034-a79b4aa6-claude-agent-claude-fable-5

- **Plan ID:** P-1794976517-cac841ef-claude-agent-claude-fable-5
- **Intent ID:** I-1794976501-macgate
- **Agent:** claude-agent/claude-fable-5

## Summary

In progress. Implements the two coupled fixes of the plan: (1) `.github/workflows/test-unit.yml` moves from the
fixed-budget composite action to the baseline-comparison gate `scripts/ci_timing_gate.py` (per-OS `--min-delta`,
tolerance 50 %, label-proof `--ceiling 600`); (2) `check_final_head_ci` in `flow/github/ci_final_head.py` gains a
one-shot automatic rerun for timing-gate-only failures on bookkeeping-only heads, surfaced in the Stage 5 verdict and
the invalidation comment.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Verify repo-level assumptions (INFO_GATHERING)

All load-bearing anchors re-verified on the execution branch:

1. The only non-test caller of `check_final_head_ci` is `flow/stages/calibrate.py` (line 319, no seams argument, so
   defaults must keep working). No second off-limits caller exists; the seam-only design holds.
2. `ci_failure_detail.py` has `TIMING_GATE_STEP_PREFIX = "CI Timing Gate"` and `parse_run_id` over
   `/actions/runs/(\d+)` links; `failure_detail` tags `timing_gate` when every failed step name starts with the
   prefix.
3. The three scoped flow modules sit exactly at their budgets: `github/ci_final_head.py` 190/190,
   `github/ci_reverify.py` 97/97, `github/approval_invalidation.py` 137/137 (`wc -l` matches
   `check_flow_module_budgets.py`, which counts `splitlines()`).
4. `test-stress.yml` is the canonical `scripts/ci_timing_gate.py` wiring (start-epoch step, `TIMING_MODE` from the
   event name, artefact upload on `main` pushes, `--runs 5 --tolerance 0.5 --min-delta 120 --ceiling 600`).
5. **Discrepancy against the plan's expectation:** one test does assert the current contents of `test-unit.yml` —
   `test_every_workflow_wires_start_and_check_steps` in `apps/sandbox-executor/tests/test_ci_timing_gate.py` lists
   `test-unit.yml` among the five workflows that must wire the `./.github/actions/ci-timing-gate` composite action.
   That file is in the declared touch set; Step 5 updates it by removing `test-unit.yml` from the composite-action
   list (the other four workflows stay covered) and pinning the new `ci_timing_gate.py` wiring with a stricter
   dedicated contract test. This replaces an assertion made obsolete by the intended change; no behavioural
   assertion is weakened.

Additional execution-time constraint found: `tests/flow/test_package_exports.py` ratchets that every new public
(top-level, non-underscore) definition under `flow/` must be re-exported from `flow/__init__.py`, which is
off-limits. Consequence: every new helper added in Step 3/4 is module-private (`_`-prefixed); only the existing
`FinalHeadSeams` dataclass (already in `KNOWN_UNEXPORTED`) gains fields.

## Tests Run
