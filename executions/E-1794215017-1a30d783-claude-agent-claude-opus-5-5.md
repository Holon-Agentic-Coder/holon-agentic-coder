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
- The move-proof snapshot (`tests/flow/fixtures/flow_py_definitions.json`) is edited by hand. `definitions` maps each
  key to its `ast.dump` digest, and `amended_definitions` records why a digest changed. Deleted keys have to be removed,
  and changed keys need a new digest plus an amendment note.
- Baseline:
  `uv run pytest apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_review_dispatch.py apps/sandbox-executor/tests/flow -q -n auto --dist worksteal`
  gave 916 passed and 53 subtests passed in 5.32s.

### Step 2: Add `worktree_mount.py` with test (a)

- New `apps/sandbox-executor/src/sandbox_executor/worktree_mount.py` at the package root, next to `git_flow.py`. It
  defines the frozen `WorkspaceMount` dataclass, `WorkspaceMountError`, `resolve_workspace_mount`,
  `workspace_mount_args` and `workspace_mount_env`.
- `resolve_workspace_mount` provisions through `git_flow.ensure_worktree` only when a `slug` is given. Otherwise a
  non-worktree is refused with the `target_worktree_problem` message. It also refuses a bare repository, `/` and the
  home directory, and a missing or unreadable common git directory. In addition, it refuses a worktree whose `--git-dir`
  lies outside both mounted paths, because the container could not resolve it. `--git-common-dir` and `--git-dir` are
  resolved against the top level and then `realpath`-normalised.
- New `tests/test_worktree_mount.py` with 10 hermetic cases: linked worktree resolution, the `.git` pointer and
  `commondir` resolving under the mounted common directory, exactly two binds with the identity path, a single bind for
  a main worktree, provisioning of an absent `flow-demo` from a bare clone, refusal of a plain directory, a bare
  repository, `/` and `$HOME`, and the env pin. Each test `chdir`s into `tmp_path` so `ensure_worktree`'s cwd fallback
  can never reach the real repository.
- Ruff is clean with no `noqa`. `uv run pytest apps/sandbox-executor/tests/test_worktree_mount.py -q` gave 10 passed in
  0.11s.

### Step 3: `run_docker_container` and `dispatch_role_agent` take `workspace_mount`

- `cli/__init__.py`: `review_workspace_volume: str | None` became `workspace_mount: WorkspaceMount | None` in both
  functions. The parameter count is unchanged and no `noqa` was added. Each of the two old `if` blocks was replaced
  one-for-one: `env_to_forward.update(workspace_mount_env(...))` and `docker_cmd.extend(workspace_mount_args(...))`, at
  the same argv position (after the result-file mount, before the session mounts). The `SANDBOX_WORKSPACE_MOUNT` import
  in `cli` is gone, since `worktree_mount` now owns the mountpoint. `dispatch_role_agent` forwards the mount only when
  it is not None and never derives one from `target_worktree`.
- `flow/dispatch.py`: only the `_dispatch_role` docstring example changed. Its move-proof digest is refreshed in Step 5.
- `tests/test_cli.py`: the two `review_workspace_volume` tests were replaced by
  `test_workspace_mount_bind_mounts_worktree_and_git_dir_and_pins_the_workspace` and
  `test_dispatch_role_agent_forwards_the_workspace_mount`. The first checks exactly the two binds, that the pin
  overrides a forwarded host `HOLON_TARGET_WORKTREE`, that the argv is otherwise byte-identical, and that there is no
  `docker volume` call and no named-volume source. The second checks that the mount is forwarded unchanged.
- `uv run pytest apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_worktree_mount.py apps/sandbox-executor/tests/test_review_dispatch.py -q -n auto --dist worksteal`
  gave 422 passed and 53 subtests passed in 2.17s.
- At this commit the flow review modules still pass `review_workspace_volume` through `_dispatch_role`. Their tests stay
  green because they use injected dispatchers. Step 4 removes those call sites.

### Step 4: Delete the Stage 4 named-volume machinery and its tests; add test (f)

- `flow/review/workspace.py` keeps only `HOST_REVIEW_REFUSAL` and `reviewers_sandboxed` and is retitled "the sandbox
  guard for reviewer turns". The following are deleted: `ReviewWorkspaceSession`, `review_session_volume_name`,
  `review_persist_enabled` and `HOLON_REVIEW_PERSIST`, `REVIEW_SESSION_VOLUME_LABEL`, `_REVIEW_PERSIST_DISABLED_VALUES`,
  `_DOCKER_CLI_TIMEOUT_SECONDS`, the root `chown` init container and the now-unused imports.
- `flow/review/loop.py`: the session-owning wrapper is deleted, and `_run_review_loop_iterations` is renamed back to
  `run_review_loop`, with no alias. The `workspace_session` parameter and its docstring, both `workspace_volume=`
  arguments and the `inspect` import are removed. A grep showed no caller passing `workspace_session` outside the
  deleted tests. Every existing `run_review_loop(...)` call binds the same parameters as before.
- `flow/review/ensemble.py` and `flow/review/resolver.py`: the `workspace_volume` parameters, the
  `ResolverInput.workspace_volume` field, the docstrings and the `volume_kwargs` spreading are deleted.
  `_invoke_resolver_pass` calls `_dispatch_review_resolver` with four arguments.
- `flow/__init__.py`: the deleted symbols and `_run_review_loop_iterations` are removed from the imports and from
  `__all__`. `agent_runner.py`: `SANDBOX_WORKSPACE_UID_GID` and its comment are deleted, and the
  `SANDBOX_WORKSPACE_MOUNT` comment now names the worktree bind mount.
- Tests: `tests/test_review_workspace_integration.py` is deleted. In `tests/test_review_dispatch.py`, `_FakeDockerCli`,
  `_session` and the session, volume, kill-switch and failing-create tests are deleted. The in-container test is kept as
  `test_in_container_mode_runs_the_ensemble_without_dispatching`, minus its docker assertions. Test (f),
  `test_review_loop_dispatches_without_volumes_root_init_or_workspace_mount`, runs a 2-iteration
  objecting-then-approving cycle through the real `dispatch_role_agent`, with `cli.run_docker_container` and
  `subprocess.run` recorded. It checks for no `docker volume` call, no `--user root` or `--entrypoint chown` container,
  no `review_workspace_volume` or `workspace_mount` kwarg, and no `sandbox_executor.flow.ReviewWorkspaceSession`.
- `tests/hermetic_fixtures.py`'s seeded-remote volume is test infrastructure for remote fixtures, not Stage 4 machinery,
  and is left unchanged.
- Deviation: the move-proof snapshot refresh was pulled forward from Step 5 into this commit, so that this commit stays
  green. The 13 deleted keys were removed. The 6 changed definitions (`_dispatch_role`, `_dispatch_review_ensemble`,
  `run_review_loop`, `ResolverInput`, `_dispatch_review_resolver`, `_invoke_resolver_pass`) got fresh digests and an
  `amended_definitions` note naming this execution. The amendment note for `_run_review_loop_iterations` went with its
  key. `test_snapshot_covers_every_flow_py_definition` pinned the floor at exactly the old top-level count (124). It now
  reads 120, with a comment, because four top-level definitions were deleted rather than moved.
- The listed review tests plus `test_cli.py`, `test_worktree_mount.py` and `tests/flow` gave 1136 passed and 53 subtests
  passed in 5.79s before the floor fix. The only failure was the floor, and after the fix `tests/flow` gave 497 passed
  in 5.96s.
- Side effect, cleaned up: running `uv run python` from `apps/sandbox-executor` created a stray `.venv` and `uv.lock`
  there. Both were deleted, and neither was committed.

### Step 5: Update docs, lower flow budgets, refresh the move-proof snapshot

- `apps/sandbox-executor/README.md`: the "Persistent review workspace" section is replaced by "Review workspace". It
  says each pass clones fresh, that there is no persistence switch, and that `workspace_mount` and
  `worktree_mount.resolve_workspace_mount()` exist but stay dormant until the container-side purge lands.
  `HOLON_REVIEW_PERSIST` and the prune command are gone.
- `docs/git_flow.md`: the same edit to the "Persistent review workspace" paragraph. `docs/architecture.md`: the
  `flow/review/workspace.py` row no longer mentions the session volume. This file was not listed in the plan; a grep
  found it.
- `flow_module_budgets.json`, all lowered to the new `wc -l` sizes: `__init__.py` from 362 to 348, `review/ensemble.py`
  from 141 to 136, `review/loop.py` from 962 to 914, `review/resolver.py` from 362 to 352 and `review/workspace.py` from
  168 to 21. `dispatch.py` stays at 99. No budget was raised.
- Move-proof snapshot: already refreshed in Step 4 (see the deviation there).
- Left for the sibling container-side sub-intent: `entrypoint/review_loop.py` `_clone_or_fetch` and its docstring, which
  still mention a reused checkout, and their tests in `tests/test_review_loop.py`.
- `uv run task lint` passed: ruff clean, 185 files formatted, flow module budgets passed for 28 modules.
  `uv run task lint-docs` passed.
