# Execution Record: E-1794391353-95df8afb-claude-agent-claude-sonnet-5-5

## Summary

Implementing plan `P-1794391254-8ec0e2e8-claude-agent-claude-sonnet-5-5`: mount the host worktree for all four stages,
move every remote git operation to the host, and remove `HOLON_REPO_URL`. In progress.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: host-side remote module

- Added `flow/host_remote.py` (`HostRemoteError`, `fetch_base`, `push_branch`, `push_commit`, `remote_error`,
  `new_local_child_branches`, `publish_new_child_branches`), reusing `_fetch_refspecs_error` (now takes the remote name)
  and `run_git`. Errors carry the operation, branch and remote name and pass through `redact_agent_secrets`.
- Deviation: `provision_worktree` was dropped. The roles branch off the ref the host fetched (`start_branch_from_host_fetch`
  in `entrypoint/execution_workspace.py`, which reuses `validate_mounted_workspace` for the clean-tree check), so a host
  checkout step added nothing.

### Step 2: mount the worktree for the intent and plan roles

- `HOST_WORKTREE_ROLES` now holds all four roles, so `run_docker_container` mounts and pins the worktree through the same
  code path. The intent creator and planner no longer clone, clean or push; Stage 1 and Stage 2 fetch before dispatch and
  push after it (the plan stage pushes every new local `P-` branch, the executor stage every new `E-` branch).
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
  `agent_runner.py` is the one place that names the variable, so a stale value is dropped by the forwarding loop and
  the preflight probe.
- Sweep: `docker/` does not exist and `.github` has no match; `entrypoint/role_dispatcher.sh` runs no git.
