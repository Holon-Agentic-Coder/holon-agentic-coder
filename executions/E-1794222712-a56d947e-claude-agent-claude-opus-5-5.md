# Execution Record: E-1794222712-a56d947e-claude-agent-claude-opus-5-5

## Summary

Implements plan `P-1794222469-9841ab3e-claude-agent-claude-opus-5-5`: a host-provisioned worktree bind-mount model
(`sandbox_executor.worktree_mount`), `workspace_mount` on the dispatch path, and deletion of the Stage 4 named-volume
machinery. In progress.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Probe linked-worktree git resolution across split mount targets

No Docker daemon in the execution sandbox, so the container view was simulated in `/tmp/probe-wm` (bare remote, main
clone, linked worktrees `flow-a` and `flow-b`): `flow-a` was copied to a second path standing in for the container
workspace and its host path was hidden. Observed with git 2.47.3:

- `rev-parse HEAD`, `status`, `commit` and `push` to the bare remote all work from the second path with the
  back-pointer path absent: only the common directory has to resolve at its identical absolute path.
- With the back-pointer path absent, `git worktree list` marks the worktree itself `prunable`, so the worktree is
  also mounted at its own host path (two worktree targets).
- `gc.auto=0` makes `git gc --auto` a no-op, and `git gc` with `gc.worktreePruneExpire=never` keeps every
  registration.
- An explicit `git worktree prune` ignores `gc.worktreePruneExpire` and removed both registrations whose paths were
  missing. Config cannot guard it; the module docstring records that role entrypoints must never run it.
- `safe.directory` was not exercised (same uid on both sides); both container paths are listed as the conservative
  default.

Adopted mount set: worktree at the container workspace, worktree at its host path, common directory at its host path
(skipped when inside the worktree). Config: `gc.auto=0`, `gc.worktreePruneExpire=never`, `safe.directory` for the
container workspace and the host path.

### Step 2: Add `worktree_mount.py` (test a)

Added `sandbox_executor/worktree_mount.py` (`WorktreeMount`, `WorktreeMountError`, `resolve_worktree_mount`,
`docker_mount_args`, `git_config_entries`) reusing `git_flow.ensure_worktree`, `ensure_flow_worktree_off_default`,
`target_worktree_problem`, `checked_out_branch` and `is_default_branch_worktree` with no `git_flow.py` change. It
refuses relative paths, commas, paths under `/home/holon` and image system directories, bare repositories, plain
directories, worktree subdirectories, detached heads, the default-branch worktree and unregistered git directories,
and warns on Linux when the worktree owner uid is not 1000. `tests/test_worktree_mount.py` covers test (a) with 16
hermetic cases.
