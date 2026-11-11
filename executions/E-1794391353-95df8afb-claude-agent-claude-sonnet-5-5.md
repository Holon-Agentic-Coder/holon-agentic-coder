# Execution Record: E-1794391353-95df8afb-claude-agent-claude-sonnet-5-5

## Summary

Implementing plan `P-1794391254-8ec0e2e8-claude-agent-claude-sonnet-5-5`: mount the host worktree for all four stages,
move every remote git operation to the host, and remove `HOLON_REPO_URL`. Complete: all six plan steps are done and the
gates pass.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.9   |
| entropy_actual        | 16.0  |
| impact_actual         | 85.0  |
| cost_actual           | 40.0  |
| learning_value_actual | 6.0   |

## Steps Executed

### Step 1: host-side remote module

- Added `flow/host_remote.py` (`HostRemoteError`, `fetch_base`, `push_branch`, `push_commit`, `remote_error`,
  `new_local_child_branches`, `publish_new_child_branches`), reusing `_fetch_refspecs_error` (now takes the remote name)
  and `run_git`. Errors carry the operation, branch and remote name and pass through `redact_agent_secrets`.
- Deviation: `provision_worktree` was dropped. The roles branch off the ref the host fetched
  (`start_branch_from_host_fetch` in `entrypoint/execution_workspace.py`, which reuses `validate_mounted_workspace` for
  the clean-tree check), so a host checkout step added nothing.

### Step 2: mount the worktree for the intent and plan roles

- `HOST_WORKTREE_ROLES` now holds all four roles, so `run_docker_container` mounts and pins the worktree through the
  same code path. The intent creator and planner no longer clone, clean or push; Stage 1 and Stage 2 fetch before
  dispatch and push after it (the plan stage pushes every new local `P-` branch, the executor stage every new `E-`
  branch).
- The standalone `holon intent`, `holon plan` and `holon execute` fetch on the host and push the checked-out branch
  afterwards (`cli/commands/_host_publish.py`).

### Step 3: review loop and executor

- The resolver only commits (`_commit_resolution`); the host pushes the head fast-forward only
  (`flow/review/resolver.py::_push_resolver_head`). `_gh_runner` and `github_repo_slug` were removed.
- The review-loop container made no fetch (the diff check and branch fetches already ran on the host), so there was
  nothing further to move there.
- The executor requires the mount, no longer clones, repairs a re-initialised repository, fetches or pushes, and its
  pre-push sync reads the target ref the host fetched. The stderr patch fallback for a refused push was deleted with it.

### Step 4: remove `HOLON_REPO_URL`

- Deleted `get_repo_url`, `resolve_target_repo*`, `credential_host`, `normalize_repo_remote`, `strip_url_credentials`,
  `get_ssh_auth_mounts`, `GIT_CREDENTIAL_HELPER`, the SSH mounts and the credential-helper env. `RETIRED_ENV_VARS` in
  `agent_runner.py` is the one place that names the variable, so a stale value is dropped by the forwarding loop and the
  preflight probe.
- Sweep: `docker/` does not exist and `.github` has no match; `entrypoint/role_dispatcher.sh` runs no git.

### Step 5: hermetic tests

- Added `tests/flow/test_host_remote.py` and `tests/flow/test_worktree_all_stages.py` (placed under `tests/flow/`
  because they use the `flow_repos` fixture) covering: intent and plan mounts equal the executor's, no SSH agent or
  repository URL in any docker argv, sibling-worktree protections for the new mounts, a static check that no entrypoint
  names a remote git verb, an SSH-origin worktree (`url.<bare>.insteadOf`) passing all stages, a stale `HOLON_REPO_URL`
  changing nothing, and named host fetch and push failures without credentials.
- Existing tests were migrated to the mounted, commit-only contract. Tests for deleted behaviour (clone, repair re-init,
  unpushed patch, credential helper, SSH mounts, URL resolution) were deleted.

### Step 6: docs, budgets and gates

- Docs now describe one worktree for all four stages and host-side fetch and push. `flow_module_budgets.json` gained
  `host_remote.py` and raised five budgets, with the reason recorded in its description. The move-proof snapshot
  declares the new and amended definitions.
- Known gap: `__main__` of the executor still prints a traceback for the unusable-repo `RuntimeError`; its first line is
  redacted but the traceback is not.

## Tests Run

| command                                                           | result                              |
| ----------------------------------------------------------------- | ----------------------------------- |
| baseline `uv run task test` (before changes)                      | 3583 passed, 30.08s                 |
| `uv run pytest apps/sandbox-executor/tests/flow` (affected paths) | 660 passed, 8.28s                   |
| `uv run task test` / full non-integration suite, final            | 3522 passed, 648 subtests, 30.63s   |
| `uv run task lint`                                                | passed (ruff, format, flow budgets) |
| `uv run task lint-docs`                                           | passed                              |

No repeated or timing runs were needed: the intent has no performance target. The test count fell from 3583 to 3522
because tests for deleted behaviour were removed, net of the new tests.
