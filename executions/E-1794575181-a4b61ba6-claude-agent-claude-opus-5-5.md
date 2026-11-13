# Execution Record: E-1794575181-a4b61ba6-claude-agent-claude-opus-5-5

## Summary

Implements plan `P-1794574974-21db7508-claude-agent-claude-opus-5-5` for intent `I-1794574957-calrebld`: Stage 5
rebuilds a stale `/calibrated` branch that holds only the flow's own calibration commits, keeps failing loudly on
foreign commits, and the final report names the execution head the calibration report covers.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Confirm the shape of Stage 5 calibration commits (INFO_GATHERING)

- `run_calibrate` (`calibration.py`) commits `plans/<plan>_calibration.md` and, when the ACT-CAL row was appended,
  `holon-knowledge/ledger/executions.jsonl` with subject `chore(calibration): add calibration report for <plan>`.
- `stages/calibrate.py` adds two follow-up plain commits: `chore(calibration): record stage 5 reconciliation`
  (`holon-knowledge/ledger/executions.jsonl`) and `chore(calibration): record stage 5 promoted knowledge and wisdom`
  (`holon-knowledge/kb`, `holon-knowledge/wisdom`).
- `ledger_interleave.py` and `stage_conflicts.py` make no commits (`stage_conflicts.py` only runs `status`,
  `rev-parse` and `merge-tree`). No Stage 5 producer creates merge commits, so the plan's classifier stands.
- `run_calibrate` checks out an existing local `/calibrated` as is and only warns when
  `merge --ff-only origin/<calibrated>` fails, so a locally rebuilt branch beside a stale remote (with `--skip-push`)
  proceeds.
- Allow-list confirmed: `plans/*_calibration.md`, `holon-knowledge/ledger/executions.jsonl`, `holon-knowledge/kb/`,
  `holon-knowledge/wisdom/`.
