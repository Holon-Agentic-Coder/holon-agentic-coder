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

### Step 2: Add `flow/calibrate_rebuild.py`

- New module (123 lines) with `is_calibration_path`, `commits_missing_from_head`, `is_calibration_commit`,
  `holds_only_calibration_commits`, `stale_calibrated_refs` and `rebuild_calibrated_branch`.
- A commit counts as the flow's own only when it is a non-merge commit, its subject starts with `chore(calibration): `
  and every path is in the allow-list; a git failure counts as foreign.
- The origin branch is replaced only with
  `git push --force-with-lease=refs/heads/<calibrated>:<observed_sha> -- origin <start_sha>:refs/heads/<calibrated>`,
  after asserting the branch name ends with `/calibrated`; `skip_push` leaves origin untouched. A lease failure raises
  `CalibrationSyncError`.
- `CalibrationSyncError` moved here; `calibrate_sync.py` imports it, so existing imports keep working.
- `flow_module_budgets.json`: new entry at creation size, description extended.

### Step 3: Hook the rebuild into `prepare_calibrated_branch`

- Stale refs that hold only calibration commits (or are strict ancestors) are rebuilt and the call returns True;
  anything else raises the existing message unchanged. Module docstring updated. Budget raised by 6 lines (`183` to
  `189`).

### Step 4: Name the calibrated head in the final report

- `stages/calibrate.py`: the Stage 5 payload carries `calibrated_head` (the origin execution head when calibration
  succeeded, otherwise `None`; the sync failure path records `None` too); the completion log adds
  `covering execution head <sha12>`. The two sync `try` blocks were merged into one with identical behaviour. Budget
  raised by 4 lines (`347` to `351`).
- `cli/commands/flow.py`: `_calibrated_head_line` prints `calibration report covers execution head <sha12>` or
  `calibration report not refreshed for the current head` under the calibrate stage of the summary.

### Step 5: Hermetic tests

- New `tests/test_calibrate_rebuild.py`: own diverged branch rebuilt (local and origin, log names old and new SHAs);
  foreign commit stops with the existing message (non-calibration subject, and calibration subject touching `src/x.py`)
  leaving local and origin refs unchanged; merge commit counts as foreign; `skip_push` rebuilds locally only;
  origin-only stale branch replaced under a lease; summary names the head; failed Stage 5 summary says not refreshed;
  path allow-list cases.
- `tests/test_calibrate_sync.py`: `test_stale_existing_calibrated_branch_fails_loudly` became
  `test_strict_ancestor_calibrated_branch_is_rebuilt_from_the_origin_head`; the repo setup was lifted into
  `make_repos` so the new module can reuse it without importing a fixture.
