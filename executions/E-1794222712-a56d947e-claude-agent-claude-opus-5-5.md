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

### Step 3: `workspace_mount` on `run_docker_container` and `dispatch_role_agent`

`review_workspace_volume: str | None` became `workspace_mount: WorktreeMount | None` on both functions. A mount adds
the `docker_mount_args` bind mounts, pins `HOLON_TARGET_WORKTREE` to `mount.container_workspace` and appends
`git_config_entries` after the credential entries. The `GIT_CONFIG_*` block now goes through a new
`cli.git_config_env(entries)` helper, which emits the same variables in the same order as before, so no credential
test changed and the existing `noqa` on `run_docker_container` was not widened. The unused `SANDBOX_WORKSPACE_MOUNT`
import left `cli/__init__.py`. In `tests/test_cli.py` the two `review_workspace_volume` tests were replaced by four
`workspace_mount` tests: byte-identical argv for `None`, each bind mount exactly once with the isolation argv otherwise
unchanged and no `volume`/`chown`, a forwarded host `HOLON_TARGET_WORKTREE` overridden, and contiguous
`GIT_CONFIG_COUNT=6` with entries 0 and 1 still the scoped credential helper when a token is present.

### Step 4: Delete the Stage 4 named-volume machinery (test f)

- `flow/review/workspace.py` keeps only `HOST_REVIEW_REFUSAL` and `reviewers_sandboxed`. It went from 168 to 21 lines.
- `flow/review/loop.py`: the session wrapper is gone. `run_review_loop` is the former `_run_review_loop_iterations`
  body, with the same signature minus `workspace_session`. The `inspect` import was dropped. 960 to 911 lines.
- `flow/review/ensemble.py` and `flow/review/resolver.py`: `workspace_volume` became
  `workspace_mount: WorktreeMount | None`, forwarded as the `workspace_mount` dispatch keyword only when set, so
  injected `_dispatch_role` seams keep working. Nothing sets it in this sub-intent.
- `flow/__init__.py` no longer imports or exports `ReviewWorkspaceSession`, `review_session_volume_name`,
  `review_persist_enabled`, `REVIEW_SESSION_VOLUME_LABEL`, `_DOCKER_CLI_TIMEOUT_SECONDS`,
  `_REVIEW_PERSIST_DISABLED_VALUES` or `_run_review_loop_iterations`.
- `flow/dispatch.py` docstring now names `workspace_mount`. `agent_runner.SANDBOX_WORKSPACE_UID_GID` is deleted.
- Declared test deletions: in `tests/test_review_dispatch.py`, `_FakeDockerCli`, `_session` and the six session tests
  (shared volume, teardown on raise, kill switch, in-container session, creation-failure fallback, persist toggle).
  `tests/test_review_workspace_integration.py` was deleted. `tests/hermetic_fixtures.py` is kept; its exports are still
  used by `conftest.py` and the planner, intent-creator, hermetic-guard and survival-stress tests.
- Test (f), added to `tests/test_review_dispatch.py`: a host-mode two-iteration cycle with a recording
  `subprocess.run` launches no docker command, no `volume` or `chown` argv, and no dispatch keyword containing
  `volume` or `workspace_mount`. The in-container loop launches no docker command. Importing the deleted names from
  `sandbox_executor.flow` raises `ImportError`.
- Unplanned coupling: `tests/flow/fixtures/flow_py_definitions.json` (the flow move proof) tracks every definition.
  13 deleted keys were removed. The six deliberately changed definitions (`ResolverInput`,
  `_dispatch_review_ensemble`, `_dispatch_review_resolver`, `_dispatch_role`, `_invoke_resolver_pass` and
  `run_review_loop`) were refreshed and noted in `amended_definitions`. The move proof's top-level floor in
  `tests/flow/test_move_proof.py` dropped from 124 to 120, with a comment naming this execution.
- The grep guard over `src/` and `tests/` only hits test (f)'s absence assertions and the snapshot's amendment notes.
