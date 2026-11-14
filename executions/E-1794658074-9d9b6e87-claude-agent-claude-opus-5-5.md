# Execution Record: E-1794658074-9d9b6e87-claude-agent-claude-opus-5-5

## Summary

Stage 5 lost the calibration report on decomposed flows because `parse_actual_metrics` in `calibration.py` (not the
`flow/stages/` modules the intent named) ran `git diff --numstat <plan>...<exec>` with the raw plan branch name when
`resolve_git_ref` could not resolve it. A hermetic nested layout reproduced exit 128 "ambiguous argument" only when the
child plan branch is absent everywhere; the slashes and trailing `/_` alone do not fail. The fix qualifies every
revision (`refs/heads/...`, `refs/remotes/origin/...` or a SHA), ends revision arguments with `--` where git accepts it,
and degrades an unresolvable plan branch to the execution head's first parent (`diff_base = "first_parent_fallback"`,
logged and shown in the report) so Stage 5 still commits the report. New modules: `git_revisions.py` (qualification
helper) and `calibration_diff.py` (diff range and merge history, because `calibration.py` has a no-growth line gate).
The sweep covered `calibrate_sync.py`, `calibrate_rebuild.py`, `stages/review.py` and `run_calibrate`;
`stages/calibrate.py` needed no change. No new dependency or `# noqa`; the budget of `calibrate_sync.py` rose by 1 line.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.95  |
| entropy_actual        | 6.0   |
| impact_actual         | 85.0  |
| cost_actual           | 16.0  |
| learning_value_actual | 4.5   |

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
   still fails the diff loudly. `merge_history` runs `rev-parse --verify <rev>^{commit}`, `merge-base` (no `--`; refs
   are qualified) and `rev-list --count --merges <range> --`. `parse_actual_metrics` now runs
   `git diff --numstat <range> --` and records `exec_meta["diff_base"]`. `generate_calibration` passes an empty plan ref
   (with a warning) instead of the raw name when `resolve_git_ref` fails, and the report adds a `Patch Size Base` line
   only for the fallback. `run_calibrate` now fast-forwards from and starts a checkout at
   `refs/remotes/origin/<calibrated>`; `checkout <calibrated>` keeps the short name so HEAD stays attached. Two existing
   tests in `tests/test_calibration.py` had argv-only expectation updates (the qualified diff range and the qualified
   checkout start point); no behavioural assertion changed. The Step 1 absent case now passes without `xfail` and
   asserts the fallback label and the warning.
4. Step 4 (sweep): `qualify_branch_revision` gained an optional `run` git runner (the caller's patched runner, as
   `calibrate_sync._rev` and `record_starting_branch` already do), so `stages/review.py` tests that replace its
   `run_git` never spawn real git. The budget of `calibrate_sync.py` was raised by 1 line (186 to 187, with the reason
   appended to the budget description); every other flow module stayed within budget. One existing test
   (`tests/flow/test_pipeline.py::test_stage5_calibration_success_still_pushes`) had its argv expectation updated to the
   qualified merge source. Audit table:

   | file                   | call                                                                           | before                          | after                                                         |
   | ---------------------- | ------------------------------------------------------------------------------ | ------------------------------- | ------------------------------------------------------------- |
   | `calibration.py`       | patch-size `diff --numstat`                                                    | `<plan>...<exec>` (raw names)   | `refs/heads/...` or `refs/remotes/origin/...` range plus `--` |
   | `calibration.py`       | `rev-parse` of the base                                                        | `rev-parse <plan>`              | `rev-parse --verify <qualified>^{commit}`                     |
   | `calibration.py`       | `merge-base`                                                                   | raw names                       | qualified refs (git takes no `--` here)                       |
   | `calibration.py`       | `rev-list --count --merges`                                                    | raw range                       | qualified range plus `--`                                     |
   | `calibration.py`       | `run_calibrate` ff-only merge                                                  | `origin/<calibrated>`           | `refs/remotes/origin/<calibrated>`                            |
   | `calibration.py`       | `run_calibrate` `checkout -b` start point                                      | `origin/<calibrated>`           | `refs/remotes/origin/<calibrated>` (new branch name kept)     |
   | `calibration.py`       | `checkout <calibrated>`                                                        | short name                      | unchanged: a qualified name would detach HEAD                 |
   | `calibrate_sync.py`    | `_divergence_message` `rev-list --count`                                       | `origin/<b>..<b>`               | `refs/remotes/origin/<b>..refs/heads/<b> --`                  |
   | `calibrate_sync.py`    | `publish_calibration` ff-only merge                                            | `<calibrated>`                  | `refs/heads/<calibrated>`                                     |
   | `calibrate_sync.py`    | `_rev`, `checkout`, `branch`, `push`                                           | qualified refs or short names   | already safe (audited)                                        |
   | `calibrate_rebuild.py` | `rev-list <start>..<existing>`                                                 | SHAs, no separator              | SHAs plus `--`                                                |
   | `calibrate_rebuild.py` | `rev-list --parents`, `log -1`, `diff-tree`                                    | SHA, no separator               | SHA plus `--`                                                 |
   | `calibrate_rebuild.py` | `merge-base --is-ancestor`, `push`, `branch`                                   | SHAs, qualified lease with `--` | already safe (audited)                                        |
   | `stages/calibrate.py`  | `rev-parse --abbrev-ref HEAD`, `add`, `commit -- <paths>`, `status -- <paths>` | no branch revisions             | already safe (audited)                                        |
   | `stages/review.py`     | Stage 4 `diff --numstat`                                                       | `<plan> <exec>` (raw names)     | qualified refs plus `--`; skipped when either is unresolvable |

5. Step 5 (tests): `tests/flow/test_git_revisions.py` covers a local-only nested branch, a remote-only branch (bare and
   `origin/` input), qualified and padded input (idempotent), full and short SHAs, missing and empty names (None), a
   branch name that is also a worktree path, the caller's git runner and `revision_args`.
   `tests/flow/test_calibrate_sync_refs.py` asserts the exact argv of `_divergence_message`, the `publish_calibration`
   merge (checkout keeps the short name), the `calibrate_rebuild` `rev-list`, `rev-list --parents`, `log -1` and
   `diff-tree` calls and the Stage 4 numstat (plus the skipped numstat for an unresolvable branch). A real-git test
   shows a same-named tag (which git prefers to a branch) plus a colliding worktree path: the qualified count reports 2
   local commits where the old `origin/<b>..<b>` form counted 0 against the tag.
   `tests/flow/test_calibrate_nested_refs.py` gained a `parse_actual_metrics` argv test for both `diff_base` labels and
   an end-to-end `run_calibrate_stage` (real `run_calibrate`, `skip_push=True`, the existing Prettier fake, conflicts
   and final-head CI patched as in `tests/test_calibrate_sync.py`). It asserts `SUCCESS`, the `/calibrated` branch, the
   `chore(calibration): add calibration report` commit on the fast-forwarded execution branch, a real (not fallback)
   report and the restored starting branch. With `calibration.py` reverted to `main`, the end-to-end absent-plan case
   fails and the local case passes, so the test pins the defect.
6. Step 6 (gates): affected tests, the full suite once, lint and lint-docs ran as listed below. The ledger directory
   still holds exactly `intents.jsonl`, `plans.jsonl` and `executions.jsonl`; the only ledger changes on the branch are
   the flow's own intent and plan appends. `uv.lock` and both `pyproject.toml` files are unchanged.

## Tests Run

The intent does not concern timing, so every command ran once (no repetitions, no median to report beyond the single
run).

| command                                                                                                                                                                                                                                                                                                                                                 | result                                    | pytest time |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- | ----------- |
| `uv run pytest apps/sandbox-executor/tests/flow/test_calibrate_nested_refs.py` (Step 1, before the fix)                                                                                                                                                                                                                                                 | 3 passed, 1 xfailed                       | `0.27s`     |
| `uv run pytest apps/sandbox-executor/tests/flow/test_git_revisions.py apps/sandbox-executor/tests/flow/test_calibrate_nested_refs.py apps/sandbox-executor/tests/flow/test_calibrate_sync_refs.py -n auto --dist worksteal`                                                                                                                             | 25 passed                                 | `2.20s`     |
| `uv run pytest apps/sandbox-executor/tests/flow/ apps/sandbox-executor/tests/test_calibration.py apps/sandbox-executor/tests/test_calibration_dual_ev.py apps/sandbox-executor/tests/test_calibrate_sync.py apps/sandbox-executor/tests/test_calibrate_rebuild.py apps/sandbox-executor/tests/test_calibrate_final_head_ci.py -n auto --dist worksteal` | 890 passed, 8 subtests passed             | `7.77s`     |
| `uv run task test` (full suite, once)                                                                                                                                                                                                                                                                                                                   | 4001 passed, 1 skipped                    | `23.34s`    |
| `uv run task lint`                                                                                                                                                                                                                                                                                                                                      | passed (ruff, format, 56 flow budgets)    | n/a         |
| `uv run task lint-docs`                                                                                                                                                                                                                                                                                                                                 | failed: 38 pre-existing files (see below) | n/a         |

`uv run task lint-docs` (Prettier `3.8.4` via npx) flags 38 markdown files, 33 of them older `plans/*_calibration.md`
reports plus `.agents/holon_flow.md`, `docs/optimisation/token_reduction_measurement_plan.md`,
`holon-config/metrics/README.md` and two older `plans/P-179038*.md` files. None is touched by this branch; this
execution record and the plan file pass `npx prettier --check`. Per the plan, unrelated files were not reformatted.
