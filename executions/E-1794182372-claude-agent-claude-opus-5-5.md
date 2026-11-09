# Execution Record: E-1794182372-claude-agent-claude-opus-5-5

- **Plan ID:** P-1794182135-claude-agent-claude-opus-5-5
- **Intent ID:** I-1794182121-validate-target-worktree-is-git-worktree
- **Agent:** claude-agent/claude-opus-5-5
- **Started At:** 2026-11-08

## Summary

Adds fail-fast validation that the resolved `--target-worktree` of `holon flow` (after auto-provisioning, and the
`target_worktree` restored from a `--checkpoint` resume) is an attached, non-bare git working tree, plus an injectable
`PipelineEngine` guard wired by `run_flow_pipeline()`.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Shared target-worktree validator in `git_flow`

Added `target_worktree_problem(target_worktree, subject="Target worktree", hint="")` plus a private
`_target_worktree_reason()` built only on `is_bare_repository()` and `is_working_tree()`. The message comes from the
module-level `TARGET_WORKTREE_PROBLEM_TEMPLATE`, names the absolute path and reason, and points to
`git worktree add <path> <branch>` or a bare clone. `subject` and `hint` let the CLI word the checkpoint variant without
the helper knowing about checkpoints. Note: `uv` was not on `PATH` in the sandbox, so it was installed with
`pip install --user uv` and `uv sync` was run; `uv.lock` is unchanged.

### Step 2: Validate the resolved target after auto-provisioning in the flow CLI

In `cli/commands/flow.py`, `_ensure_flow_worktree()` now returns `bool`. Branch selection moved to
`_flow_target_branch()`, and provisioning (with `RuntimeError`/`ValueError` caught and logged, redacted) moved to
`_provision_flow_worktree()`. `_should_provision()` limits auto-provisioning to a bare clone or an absent CLI target, so
a plain non-git directory is never handed to `ensure_worktree()` and its cwd fallback. After that, the final path goes
through `target_worktree_problem()`. On a problem the command prints `Error: <message>`, calls `sys.exit(1)` and
returns `False`, and `run()` returns before the preflight gate.

Deviation: the `--skip-push` outside `--dry-run` rejection was moved out of `_check_dry_run_and_preflight()` into a new
`_check_skip_push()` that runs _before_ the worktree check. Without this, the existing
`test_flow_skip_push_rejected_outside_dry_run_for_any_resume` failed: its checkpoint records a plain temporary
directory, so the worktree error replaced the expected skip-push error. Argument-level errors now come first, and the
worktree check still runs before preflight and before any stage.

### Step 3: Validate the `target_worktree` restored from a checkpoint resume

`_effective_flow_target()` mirrors `_update_flow_context()`: an explicit `--target-worktree` other than `"."` wins,
otherwise a checkpoint's recorded `target_worktree` is used. A checkpoint-restored target is auto-provisioned only when
it is bare, and in that case only `context.target_worktree` is updated. An absent or plain recorded path exits 1. The
message starts `Checkpoint '<file>' target_worktree '<path>' ...` and ends with the hint to pass `--target-worktree`.

Deviation (fixture only): `test_flow_checkpoint_resumption_flag_overrides` recorded a plain temporary directory as the
checkpoint's `target_worktree`. Under acceptance criterion 2 that resume is now correctly rejected, so the fixture runs
`git init -q` on the directory first. None of the test's assertions changed. Steps 2 and 3 share one function, so they
were committed together.
