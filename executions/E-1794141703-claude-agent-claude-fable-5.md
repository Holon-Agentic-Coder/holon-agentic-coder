# Execution Record: E-1794141703-claude-agent-claude-fable-5

- **Plan ID:** P-1794136106-claude-agent-claude-fable-5
- **Intent ID:** I-1794136091-persist-review-loop-sandbox-across-iterations
- **Agent:** claude-agent/claude-fable-5
- **Started At:** 2026-11-08

## Summary

Implements persistent Stage 4 review-loop workspaces: one labelled Docker volume per review session is
mounted at the container workspace path for every reviewer-ensemble and resolver dispatch, so the first
pass clones and every later pass performs an incremental `git fetch` + `git checkout -B` after a hermetic
reset, instead of a full network clone per container. The volume is created lazily, torn down in a
`finally`, and can be disabled with `HOLON_REVIEW_PERSIST=0`.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Probe Docker named-volume ownership and sync semantics (INFO_GATHERING)

No Docker daemon is available in this execution sandbox (`docker: command not found`), so the probe took
its documented fallback: reading `apps/sandbox-executor/Dockerfile`, `tests/hermetic_fixtures.py` and the
executor's `HOLON_KEEP_WORKSPACE` reuse path, and encoding the residual uncertainty as defensive
ownership/`safe.directory` handling in Step 4. Findings against the three learning targets:

1. **Volume ownership does not work unaided.** The image creates the `holon` user with `useradd -m`
   (uid 1000, the image's first user) and never pre-creates `~/.holon-sandbox/workspace`, so Docker
   cannot copy ownership into an empty named volume mounted there: the mountpoint is created root-owned.
   `tests/hermetic_fixtures.py` documents the same fact for its seeded-remote volumes ("A freshly created
   volume is root-owned") and solves it with a one-shot root container that runs `chown -R 1000:1000`.
   **Decision:** the host-side session helper mirrors that proven pattern — after `docker volume create`
   it runs one `--rm --network none --user root --entrypoint chown` init container over the mount, using
   the same agent image the session will dispatch. A Dockerfile `mkdir` was considered and rejected: the
   chown init works against any image version, whereas copy-up ownership only helps images built after
   the change.
2. **`safe.directory` is not expected to be needed** (both passes run git as uid 1000 over files owned by
   uid 1000), but uid variance between images cannot be ruled out without a live daemon, so Step 4 adds a
   best-effort `git config --global --add safe.directory <repo_dir>` guard before reading the reused
   checkout's origin, and treats an unreadable origin as a mismatch (wipe and re-clone, fail safe).
3. **The fetch branch fires on `.git` presence.** `_has_checkout` only tests for `<repo_dir>/.git`, so a
   persistent volume carrying a previous pass's clone takes the fetch-and-checkout path with no further
   plumbing.

**Decided mount path:** the in-container sandbox default `/home/holon/.holon-sandbox/workspace`, held in
one shared constant (`agent_runner.SANDBOX_WORKSPACE_MOUNT`) used by the dispatcher; when a volume is
mounted, the dispatcher also pins `HOLON_TARGET_WORKTREE` to that constant so the entrypoint's
`get_workspace_dir()` cannot drift from the mountpoint (e.g. via a forwarded host value).
**Decided volume naming:** deterministic per session, `holon-review-ws-<sha256(review target)[:16]>`,
labelled `holon.review-session=1`, so a resumed flow derives the same name without checkpoint changes and
a crashed loop's leaked volume is prunable by label and safely reusable (Step 4 makes reuse hermetic).

## Tests Run

(pending)
