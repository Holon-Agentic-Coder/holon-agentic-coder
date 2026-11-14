# Execution Record: E-1794658074-9d9b6e87-claude-agent-claude-opus-5-5

## Summary

In progress: fixing the Stage 5 `git diff` exit 128 "ambiguous argument" on nested sub-intent branch names.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

1. Step 1 (reproduction): added `apps/sandbox-executor/tests/flow/test_calibrate_nested_refs.py`, a hermetic nested
   layout (root intent, `--1-child` child intent, child plan and child execution branches) against a local bare origin.
   Of the three cases, only the plan branch absent everywhere reproduces the reported failure through
   `generate_calibration`: `RuntimeError` "Git diff failed ... exit code 128: fatal: ambiguous argument". The
   remote-only case resolves through `resolve_git_ref`'s fetch fallback, and the local case is the control. So the
   nested `/_` name alone does not fail; an unresolvable plan ref, passed raw to `git diff`, does. The absent case is
   marked `xfail(strict=True)` until Step 3.
2. Step 2 (helper): added `apps/sandbox-executor/src/sandbox_executor/git_revisions.py` (outside `flow/`, so no budget
   entry). `qualify_branch_revision(repo_dir, name, remote="origin")` strips `refs/heads/`, `refs/remotes/<remote>/` and
   `<remote>/` prefixes, then returns a verified hex SHA, `refs/heads/<name>` or `refs/remotes/<remote>/<name>`, else
   None. It runs only `rev-parse --verify --quiet <rev>^{commit}` and never fetches. `revision_args(*revs)` returns the
   revisions followed by `--`. The module imports only `re` and `sandbox_executor.git`.
3. Step 3 (calibration fix): `calibration.py` carries a no-growth gate (`tests/test_calibration_dual_ev.py` asserts
   `calibration.py <= 1684` lines), so the new logic lives in a new focused module,
   `apps/sandbox-executor/src/sandbox_executor/calibration_diff.py`, and `calibration.py` shrank from 1678 to 1652
   lines. `patch_diff_range` qualifies the plan and execution refs through `qualify_branch_revision`; a missing, empty
   or equal base degrades to the execution head's first parent (`diff_base = "first_parent_fallback"`, logged as a
   warning naming the plan branch). An unqualifiable target (`HEAD`, a PR ref) is passed through, so an unknown head
   still fails the diff loudly. `merge_history` runs `rev-parse --verify <rev>^{commit}`, `merge-base` (no `--`; refs are
   qualified) and `rev-list --count --merges <range> --`. `parse_actual_metrics` now runs
   `git diff --numstat <range> --` and records `exec_meta["diff_base"]`. `generate_calibration` passes an empty plan ref
   (with a warning) instead of the raw name when `resolve_git_ref` fails, and the report adds a `Patch Size Base` line
   only for the fallback. `run_calibrate` now fast-forwards from and starts a checkout at
   `refs/remotes/origin/<calibrated>`; `checkout <calibrated>` keeps the short name so HEAD stays attached. Two existing
   tests in `tests/test_calibration.py` had argv-only expectation updates (the qualified diff range and the qualified
   checkout start point); no behavioural assertion changed. The Step 1 absent case now passes without `xfail` and
   asserts the fallback label and the warning.
