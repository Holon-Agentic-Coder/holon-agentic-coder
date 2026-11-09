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
