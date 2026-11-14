# Execution Record: E-1794575181-a4b61ba6-claude-agent-claude-opus-5-5

## Summary

Implements plan `P-1794574974-21db7508-claude-agent-claude-opus-5-5` for intent `I-1794574957-calrebld`: Stage 5
rebuilds a stale `/calibrated` branch that holds only the flow's own calibration commits, keeps failing loudly on
foreign commits, and the final report names the execution head the calibration report covers.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 1.0   |
| entropy_actual        | 6.4   |
| impact_actual         | 72.0  |
| cost_actual           | 14.5  |
| learning_value_actual | 4.0   |

## Steps Executed

### Step 1: Confirm the shape of Stage 5 calibration commits (INFO_GATHERING)

- `run_calibrate` (`calibration.py`) commits `plans/<plan>_calibration.md` and, when the ACT-CAL row was appended,
  `holon-knowledge/ledger/executions.jsonl` with subject `chore(calibration): add calibration report for <plan>`.
- `stages/calibrate.py` adds two follow-up plain commits: `chore(calibration): record stage 5 reconciliation`
  (`holon-knowledge/ledger/executions.jsonl`) and `chore(calibration): record stage 5 promoted knowledge and wisdom`
  (`holon-knowledge/kb`, `holon-knowledge/wisdom`).
- `ledger_interleave.py` and `stage_conflicts.py` make no commits (`stage_conflicts.py` only runs `status`, `rev-parse`
  and `merge-tree`). No Stage 5 producer creates merge commits, so the plan's classifier stands.
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
  `test_strict_ancestor_calibrated_branch_is_rebuilt_from_the_origin_head`; the repo setup was lifted into `make_repos`
  so the new module can reuse it without importing a fixture.

### Step 6: Validation

- The first full-suite run failed 7 tests in `tests/flow/test_package_exports.py`: every public definition in a `flow/`
  module must be re-exported from `flow/__init__.py`. `commits_missing_from_head` and `is_calibration_commit` became
  private helpers; the other four public names are now imported and listed in `__all__`. The `__init__.py` budget was
  raised by 10 lines (`411` to `421`) with a description note.
- `git diff --stat main` touches only the declared modules, tests, `flow_module_budgets.json`, `flow/__init__.py`
  (export contract) and this record; no ledger file and no `uv.lock` change.
- Entropy and cost came in slightly above prediction because of the export-contract fix-up.

## Tests Run

All runs from the repository root. No timing measurement is involved, so each command ran once (the full suite ran
twice: once before and once after the export fix, both recorded).

| command                                                                                                                                                                                                                                                                      | result                                     | pytest time |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ | ----------- |
| `uv run pytest apps/sandbox-executor/tests/test_calibrate_rebuild.py apps/sandbox-executor/tests/test_calibrate_sync.py`                                                                                                                                                     | 26 passed                                  | `1.60s`     |
| same, after adding the origin-only lease test                                                                                                                                                                                                                                | 27 passed                                  | `4.91s`     |
| `uv run pytest apps/sandbox-executor/tests/test_calibrate_rebuild.py apps/sandbox-executor/tests/test_calibrate_sync.py apps/sandbox-executor/tests/test_calibrate_final_head_ci.py apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_calibration.py` | 454 passed, 61 subtests passed             | `4.39s`     |
| `uv run task test` (before the export fix)                                                                                                                                                                                                                                   | 7 failed, 3957 passed, 1 skipped           | `26.44s`    |
| `uv run pytest apps/sandbox-executor/tests/flow/test_package_exports.py apps/sandbox-executor/tests/test_calibrate_rebuild.py apps/sandbox-executor/tests/test_calibrate_sync.py`                                                                                            | 116 passed                                 | `1.53s`     |
| `uv run task test` (final)                                                                                                                                                                                                                                                   | 3962 passed, 1 skipped                     | `27.60s`    |
| `uv run task lint`                                                                                                                                                                                                                                                           | passed (ruff, format, flow module budgets) | n/a         |

`uv run task lint-docs` (`npx prettier --check "**/*.md"`, prettier `3.8.4`) reports 38 files, none of them in
`git diff main --name-only`; they are pre-existing (other agents' calibration reports and docs) and were left as they
are. This record and the plan file pass `npx prettier --check`. Criterion 4's `lint-docs` part therefore holds for every
file this execution touched but not for the repository as a whole.

Median of the two full-suite runs: `(26.44 + 27.60) / 2 = 27.02s` (the first run failed, the final run is the passing
figure).
