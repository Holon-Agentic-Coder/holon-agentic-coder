# Execution Record: E-1794215017-1a30d783-claude-agent-claude-opus-5-5

## Summary

Execution of plan `P-1794214810-8b2770b7-claude-agent-claude-opus-5-5` for intent
`I-1794214501-mount-host-worktree-into-sandbox-containers--1-add-host-provisioned-worktree-bind-mount-dispatc`: add
host-provisioned worktree bind-mount dispatch for role containers and delete the Stage 4 named-volume machinery.

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

### Step 1: Inventory and pin every reference to the volume machinery

- A repository grep for the volume symbols (`ReviewWorkspaceSession`, `review_session_volume_name`,
  `review_persist_enabled`, `REVIEW_SESSION_VOLUME_LABEL`, `holon.review-session`, `HOLON_REVIEW_PERSIST`,
  `SANDBOX_WORKSPACE_UID_GID`, `review_workspace_volume`, `workspace_volume`, `workspace_session`, `holon-review-ws-`)
  hit exactly the files the plan lists, plus historical plans, execution records, KB entries and ledgers, which stay
  untouched. Nothing in the `Makefile`, `holon-config/`, `apps/sandbox-executor/entrypoint/` or the Dockerfiles refers
  to them.
- `_run_review_loop_iterations` is re-exported by `flow/__init__.py` but no test or script calls it directly.
- The move-proof snapshot (`tests/flow/fixtures/flow_py_definitions.json`) is edited by hand. `definitions` maps
  each key to its `ast.dump` digest, and `amended_definitions` records why a digest changed. Deleted keys have to be
  removed, and changed keys need a new digest plus an amendment note.
- Baseline: `uv run pytest apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_review_dispatch.py apps/sandbox-executor/tests/flow -q -n auto --dist worksteal`
  gave 916 passed and 53 subtests passed in 5.32s.

### Step 2: Add `worktree_mount.py` with test (a)

- New `apps/sandbox-executor/src/sandbox_executor/worktree_mount.py` at the package root, next to `git_flow.py`. It
  defines the frozen `WorkspaceMount` dataclass, `WorkspaceMountError`, `resolve_workspace_mount`,
  `workspace_mount_args` and `workspace_mount_env`.
- `resolve_workspace_mount` provisions through `git_flow.ensure_worktree` only when a `slug` is given. Otherwise a
  non-worktree is refused with the `target_worktree_problem` message. It also refuses a bare repository, `/` and the
  home directory, and a missing or unreadable common git directory. In addition, it refuses a worktree whose
  `--git-dir` lies outside both mounted paths, because the container could not resolve it. `--git-common-dir` and
  `--git-dir` are resolved against the top level and then `realpath`-normalised.
- New `tests/test_worktree_mount.py` with 10 hermetic cases: linked worktree resolution, the `.git` pointer and
  `commondir` resolving under the mounted common directory, exactly two binds with the identity path, a single bind for
  a main worktree, provisioning of an absent `flow-demo` from a bare clone, refusal of a plain directory, a bare
  repository, `/` and `$HOME`, and the env pin. Each test `chdir`s into `tmp_path` so `ensure_worktree`'s cwd
  fallback can never reach the real repository.
- Ruff is clean with no `noqa`. `uv run pytest apps/sandbox-executor/tests/test_worktree_mount.py -q` gave 10 passed
  in 0.11s.
