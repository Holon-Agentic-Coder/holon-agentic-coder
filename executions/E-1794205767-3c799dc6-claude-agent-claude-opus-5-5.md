# Execution Record: E-1794205767-3c799dc6-claude-agent-claude-opus-5-5

## Summary

Execution of plan `P-1794205494-860c0862-claude-agent-claude-opus-5-5` for intent
`I-1794205479-flow-runs-in-dedicated-worktree-not-default-branch`: redirect `holon flow` runs that target the
default-branch worktree into a dedicated `flow-<slug>` worktree, refuse default-branch checkpoints, and refuse
default-branch worktrees in `holon review`, `holon review-loop` and `holon calibrate`.

In progress.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Write paths and environment-sensitive tests

- Every stage takes its repository from `context.target_worktree`: `flow/stages/intent.py:71,126`,
  `flow/stages/plan.py:53,209`, `flow/stages/execute.py:110`, `flow/stages/decomposition.py:123`,
  `flow/stages/review.py:52,79` (the `checkout` of the execution branch) and `flow/stages/calibrate.py:155` (ledger and
  calibration commits, `checkout`, `merge --ff-only`, `push`). No stage falls back to `os.getcwd()` or `cwd="."`.
- The default checkpoint path is `os.path.join(context.target_worktree, ".holon", "flow", ...)`
  (`flow/checkpoint.py:152-171`); `.holon/` is gitignored (`.gitignore:14`), so a user-supplied checkpoint inside the
  main worktree does not dirty `git status`.
- The engine guard is `flow_guard.pre_run_guard_error(context, worktree_validator)` (`flow_guard.py:17`), called from
  `flow/pipeline.py:294`; `run_flow_pipeline` assigns `target_worktree_problem` (`flow/pipeline.py:384`). The
  validator receives only the path, but `pre_run_guard_error` itself receives the whole context.
- Unplanned constraint: `tests/flow/test_move_proof.py` pins the `ast.dump` digest of every flow-package definition, so
  any edit to `run_flow_pipeline` (even line-neutral) fails that proof. Step 4 therefore probes `flow_guard.py`
  (outside the budgeted package) instead of `pipeline.py`.
- `cli/commands/__init__.py:15` skips modules whose name starts with `_`, so `_flow_worktree.py` is not a subcommand.
- Patch target to update: `tests/test_flow_target_worktree.py:259` patches
  `sandbox_executor.cli.commands.flow.ensure_worktree`.
- Tests that run against the process cwd with the default `--target-worktree .` (cwd is the real repository, so they
  would be redirected or refused on a `main` checkout): `tests/flow/test_pipeline.py:631`
  (`test_cli_flow_dry_run_dispatch`), `tests/test_preflight.py:567,581`, `tests/test_calibration.py:959,973`,
  `tests/test_review_loop.py:252,280`.
- Fixture repositories are created on `main` (`init.defaultBranch = main` in `tests/conftest.py`), so tests that pass a
  fixture worktree explicitly (`tests/test_flow_target_worktree.py`, `tests/test_preflight.py:631-755`,
  `tests/flow/test_dispatch.py:767`, `tests/flow/test_pipeline.py:1072`) are candidates for pinning; Step 8's run
  identifies the exact set.

### Step 2: Default-branch primitives in `git_flow.py`

- Added `checked_out_branch`, `normalise_base_branch`, `repository_default_branch`, `is_default_branch_worktree`,
  `flow_worktree_layout` (read-only derivation from the worktree's top level, so a subdirectory target never places
  `flow-<slug>` inside the repository), `ensure_flow_worktree_off_default` and `default_branch_worktree_problem`.
- The tail of `ensure_worktree` moved into `_attach_or_create_worktree`; `_resolve_start_point` now takes the preferred
  refs as varargs, so the off-default path prefers `origin/<base>` then `<base>` before the existing fallbacks, and logs
  a warning when `origin/<base>` does not resolve. `ensure_worktree` keeps its signature and behaviour.
- The module is 469 lines, above the plan's ~360 estimate (it is not in the flow budget file); the extra lines are
  docstrings required by `ruleset.md` §2.
- `tests/test_flow_target_worktree.py` and `tests/test_git.py`: 50 passed, unchanged.

### Step 3: `cli/commands/_flow_worktree.py`, redirect and checkpoint refusal

- Moved `_CHECKPOINT_TARGET_HINT`, `_extract_flow_slug`, `_flow_target_branch`, `_effective_flow_target`,
  `_nearest_existing_parent`, `_should_provision`, `_provision_flow_worktree` and `_ensure_flow_worktree` (renamed
  `_ensure_attached_worktree`, body unchanged) into the new module. `flow.py` went from 482 to 333 lines; the new module
  is 282 lines.
- Deviation from the plan: the redirect runs after the pre-flight probe, not before it.
  `tests/test_preflight.py::test_quota_exhausted_probe_aborts_flow_before_stage_one` pins the contract "Aborting before
  any branch, checkpoint or ledger write" on a fixture repository on `main`, and provisioning `flow-<slug>` before the
  probe broke it. `ensure_flow_worktree` (before preflight) now refuses default-branch checkpoints and accepts a
  default-branch CLI target; `redirect_default_branch_target` (after preflight, before `_build_flow_context` and
  `run_flow_pipeline`) provisions or reuses the sibling and rewrites `args.target_worktree` and a resumed context's
  `target_worktree`. No stage runs before either.
- Run summary prints `target worktree: <path>` and the JSON result always carries `target_worktree`.
- `--target-worktree` help for `holon flow` comes from `TARGET_WORKTREE_HELP`.
- Declared test change: the patch target in `tests/test_flow_target_worktree.py:259` now names
  `sandbox_executor.cli.commands._flow_worktree.ensure_worktree`.
- `tests/test_flow_target_worktree.py`, `tests/test_preflight.py`, `tests/flow`: 808 passed.

### Step 4: Engine-level guard (exploratory)

- Hypothesis adjusted after Step 1: `pipeline.py` cannot change at all (the move proof pins `run_flow_pipeline`), but
  `flow_guard.pre_run_guard_error` lives outside the budgeted package and already receives the whole context. When a
  `worktree_validator` is given (the `run_flow_pipeline` path), it now also returns
  `default_branch_worktree_problem(context.target_worktree, intent target_branch)`.
- Result: kept. `pipeline.py` is unchanged (zero lines), the budget and move proof are untouched, and the full suite
  showed no failure caused by the stricter guard (the only failure in that run came from Step 3's checkpoint refusal and
  is pinned in Step 7). Direct `PipelineEngine` use keeps its validator-free behaviour.
- Learning: invariants can be added to the budgeted flow package without growth when the engine already delegates to a
  hook that receives the context; the hook's module is the right home.
