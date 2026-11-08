# Execution Record: E-1794141703-claude-agent-claude-fable-5

- **Plan ID:** P-1794136106-claude-agent-claude-fable-5
- **Intent ID:** I-1794136091-persist-review-loop-sandbox-across-iterations
- **Agent:** claude-agent/claude-fable-5
- **Started At:** 2026-11-08

## Summary

Implements persistent Stage 4 review-loop workspaces: one labelled Docker volume per review session is mounted at the
container workspace path for every reviewer-ensemble and resolver dispatch, so the first pass clones and every later
pass performs an incremental `git fetch` + `git checkout -B` after a hermetic reset, instead of a full network clone per
container. The volume is created lazily, torn down in a `finally`, and can be disabled with `HOLON_REVIEW_PERSIST=0`.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.95  |
| entropy_actual        | 9.0   |
| impact_actual         | 75.0  |
| cost_actual           | 12.0  |
| learning_value_actual | 5.0   |

Rationale: all five intent goals are delivered and verified (`success_actual = 0.95`, not 1.0 only because the Docker
integration test could be verified here solely as a graceful skip — its three assertions run in Docker-capable
environments). `entropy_actual = 9.0` sits under the predicted `11.0`: the dispatch threading landed without disturbing
any existing call site or seam, with one unplanned wrinkle (the complexity-noqa ratchet forced the `run_review_loop`
wrapper and `_dispatch_role` into forwarding shapes rather than new `# noqa` annotations). `cost_actual = 12.0 < 15.5`
as the probe step collapsed into reading (`no Docker daemon`). `impact_actual = 75.0`: every multi-iteration Stage 4
cycle now avoids one full network clone per pass (the current clone is not even `--depth 1`), qualitatively as planned;
`EV_actual = 0.95 * 75.0 + 0.5 * 5.0 - 0.3 * 9.0 - 12.0 = 71.25 + 2.50 - 2.70 - 12.00 = 59.05`.

## Steps Executed

### Step 1: Probe Docker named-volume ownership and sync semantics (INFO_GATHERING)

No Docker daemon is available in this execution sandbox (`docker: command not found`), so the probe took its documented
fallback: reading `apps/sandbox-executor/Dockerfile`, `tests/hermetic_fixtures.py` and the executor's
`HOLON_KEEP_WORKSPACE` reuse path, and encoding the residual uncertainty as defensive ownership/`safe.directory`
handling in Step 4. Findings against the three learning targets:

1. **Volume ownership does not work unaided.** The image creates the `holon` user with `useradd -m` (uid 1000, the
   image's first user) and never pre-creates `~/.holon-sandbox/workspace`, so Docker cannot copy ownership into an empty
   named volume mounted there: the mountpoint is created root-owned. `tests/hermetic_fixtures.py` documents the same
   fact for its seeded-remote volumes ("A freshly created volume is root-owned") and solves it with a one-shot root
   container that runs `chown -R 1000:1000`. **Decision:** the host-side session helper mirrors that proven pattern —
   after `docker volume create` it runs one `--rm --network none --user root --entrypoint chown` init container over the
   mount, using the same agent image the session will dispatch. A Dockerfile `mkdir` was considered and rejected: the
   chown init works against any image version, whereas copy-up ownership only helps images built after the change.
2. **`safe.directory` is not expected to be needed** (both passes run git as uid 1000 over files owned by uid 1000), but
   uid variance between images cannot be ruled out without a live daemon, so Step 4 adds a best-effort
   `git config --global --add safe.directory <repo_dir>` guard before reading the reused checkout's origin, and treats
   an unreadable origin as a mismatch (wipe and re-clone, fail safe).
3. **The fetch branch fires on `.git` presence.** `_has_checkout` only tests for `<repo_dir>/.git`, so a persistent
   volume carrying a previous pass's clone takes the fetch-and-checkout path with no further plumbing.

**Decided mount path:** the in-container sandbox default `/home/holon/.holon-sandbox/workspace`, held in one shared
constant (`agent_runner.SANDBOX_WORKSPACE_MOUNT`) used by the dispatcher; when a volume is mounted, the dispatcher also
pins `HOLON_TARGET_WORKTREE` to that constant so the entrypoint's `get_workspace_dir()` cannot drift from the mountpoint
(e.g. via a forwarded host value). **Decided volume naming:** deterministic per session,
`holon-review-ws-<sha256(review target)[:16]>`, labelled `holon.review-session=1`, so a resumed flow derives the same
name without checkpoint changes and a crashed loop's leaked volume is prunable by label and safely reusable (Step 4
makes reuse hermetic).

### Step 2: Per-session review workspace lifecycle in the host Stage 4 loop (IMPLEMENTATION)

- `agent_runner.py`: added the shared constants `SANDBOX_WORKSPACE_MOUNT` (`/home/holon/.holon-sandbox/workspace`) and
  `SANDBOX_WORKSPACE_UID_GID` (`1000:1000`).
- `flow.py`: added `review_persist_enabled()` (kill switch `HOLON_REVIEW_PERSIST`, truthy by default; `0/false/no/off`
  disables persistence entirely), `review_session_volume_name()` (deterministic `holon-review-ws-<digest>` name) and
  `ReviewWorkspaceSession` (lazy labelled `docker volume create` + root `chown` init container on first dispatch,
  injectable docker runner seam, warn-only teardown via `docker volume rm -f`; any creation failure disables the session
  and dispatches fall back to clone-per-pass).
- `run_review_loop` became a thin wrapper that owns the session lifecycle: it creates a session only when the loop will
  genuinely dispatch role containers (not a dry run, not `reviewers_sandboxed()`, persist enabled, no injected
  dispatcher or review seams) and tears it down in a `finally` covering refusals, no-convergence, budget exhaustion and
  exceptions. The loop body moved unchanged to `_run_review_loop_iterations` with a new keyword-only `workspace_session`
  parameter.
- Threaded the volume name through `_dispatch_review_ensemble`, `_invoke_resolver_pass`, `_dispatch_review_resolver` and
  `_dispatch_role` as an optional parameter passed only when set, so every existing call site, injected dispatcher and
  test seam stays behaviour-identical.

### Step 3: Workspace volume mount plumbing in the docker dispatch builder (IMPLEMENTATION)

- `cli/__init__.py`: `dispatch_role_agent` and `run_docker_container` accept an optional `review_workspace_volume`. When
  set, `run_docker_container` adds exactly one `-v <volume>:<SANDBOX_WORKSPACE_MOUNT>` mount (named volume, never a host
  bind) and pins `HOLON_TARGET_WORKTREE` to the same constant inside the container environment so the entrypoint's
  `get_workspace_dir()` can never resolve outside the mounted volume (Step 1 decision). All isolation flags (`--rm`,
  `--cap-drop=ALL`, `--security-opt=no-new-privileges`, metadata sinkholes, env-file secrets, tmpfs/session mounts and
  teardown) are untouched; without a volume the argv is byte-identical to before.

### Step 4: Hermetic in-container incremental sync (`_clone_or_fetch`) (IMPLEMENTATION)

- `entrypoint/review_loop.py`: the reuse branch now (1) adds a best-effort `safe.directory` entry (Step 1's defensive
  fallback), (2) verifies the checkout's `origin` against `HOLON_REPO_URL` with normalised comparison (trailing `/` and
  `.git` insensitive) — an unreadable or mismatched origin is wiped (`_wipe_workspace`, contents only, since the
  directory is a volume mountpoint) and re-cloned, never fetched from; with no `HOLON_REPO_URL` an operator-supplied
  checkout is trusted as before — (3) enforces the hermetic boundary via `_reset_reused_checkout` (abort rebase/merge
  best effort, then `git reset --hard HEAD` and `git clean -fdx`, discarding reviewer scratch, resolver leftovers and
  any uncommitted in-container ledger rows), and (4) runs the existing incremental
  `git fetch origin +refs/heads/<b>:refs/remotes/origin/<b>` + `git checkout -B <b> origin/<b>` + `_verify_head`
  sequence unchanged. All git calls stay routed through `_checked`, so failures raise `ReviewPreparationError` and take
  the existing one-failure-row, exit-1 path.
- Declared test change: `tests/test_review_loop.py`'s `FakeGitCommands` gained an `origin_url` rule (default matching
  `_run_entrypoint`'s `HOLON_REPO_URL`) so the existing reuse-path test keeps passing with no assertion weakened.

### Step 5: Unit tests for session lifecycle, mount plumbing and hermetic sync (TEST)

All additive; no existing assertion was deleted or loosened.

- `tests/test_review_dispatch.py`: a 2-iteration convergence cycle (reviewers object → resolver applies and pushes →
  reviewers approve) through the injected dispatcher seam passes the **same** deterministic volume name to all three
  dispatches, creates the labelled volume once (plus its one root ownership init run) and removes it exactly once;
  teardown also runs exactly once when the loop raises mid-iteration; `HOLON_REVIEW_PERSIST=0` dispatches with no volume
  kwarg and no docker call; in-container mode (`install_fake_reviewers`) never touches docker; a failing
  `docker volume create` falls back to clone-per-pass dispatch; `review_persist_enabled` value table and deterministic
  volume naming; `SANDBOX_WORKSPACE_MOUNT == get_workspace_dir()` under container env. The host-ledger assertions
  confirm per-iteration ACT-REV rows, ACT-RES rows and the summary row stay host-written.
- `tests/test_review_loop.py` (entrypoint layer, `FakeGitCommands`): matching checkout runs
  `rebase --abort`/`merge --abort` then `reset --hard` → `clean -fdx` → `fetch` → `checkout -B` in order with no clone;
  mismatched origin wipes the mountpoint contents (really deleted) and re-clones without ever fetching the branch from
  the wrong remote; a failing hermetic reset takes the fail-closed path (one ACT-REV failure row, exit 1). Two
  **real-git** tests against local bare remotes prove goals 2 and 3 end to end at the function level: the second
  `_prepare_workspace` keeps the clone (a `.git` marker survives), picks up a resolver-style push incrementally, and
  discards a planted untracked file and an uncommitted ledger row; and an origin mismatch wipes a foreign checkout and
  re-clones from the configured remote.
- `tests/test_cli.py`: with a review workspace volume, `run_docker_container`'s argv equals the baseline plus exactly
  one `-v <volume>:<SANDBOX_WORKSPACE_MOUNT>` pair and the pinned `HOLON_TARGET_WORKTREE` env entry (removing those two
  pairs restores the baseline byte for byte, which guards every isolation flag); a forwarded host
  `HOLON_TARGET_WORKTREE` is overridden by the pin.

### Step 6: Hermetic Docker integration test for multi-pass workspace reuse (TEST)

Added `tests/test_review_workspace_integration.py` (marked `integration_test`): seeds a named-volume bare remote via
`hermetic_fixtures.create_seeded_remote`, initialises a session workspace volume exactly as `ReviewWorkspaceSession`
does (labelled create + one-shot root `chown` init), and drives only the entrypoint's preparation phase
(`_prepare_workspace`) in two real `--network none` container runs. It asserts the three designed behaviours: a `.git`
marker planted after run 1 survives run 2 (no re-clone), run 2's HEAD is the commit a helper container pushed between
the runs (incremental fetch), and a planted junk file plus an uncommitted ledger row are gone (hermetic boundary). All
volumes are reclaimed with `addCleanup`. No Docker daemon exists in this execution sandbox, so the test was verified to
**skip gracefully** here (`1 skipped`) and to stay deselected from the unit suite
(`-m "not integration_test and not stress"` → `1 deselected`); its full three-assertion run lands in Docker-capable
environments, with the same logic covered hermetically at unit level by Step 5's real-git tests.

### Step 7: Documentation and the full verification gate (DOCUMENTATION)

- `docs/git_flow.md`: added a **Persistent review workspace** paragraph to the Stage 4 section (volume lifecycle,
  hermetic reset, incremental sync, host-written ledger rows unchanged, `--rm` isolation per pass intact,
  `HOLON_REVIEW_PERSIST=0` opt-out, label-based pruning of leaked volumes).
- `apps/sandbox-executor/README.md`: added a **Persistent review workspace** section before the reviewer ensemble
  reference, including the troubleshooting note that a leaked volume is prunable via
  `docker volume prune --filter label=holon.review-session`.
- Resolved the complexity-noqa ratchet (`test_complexity_gate.py`) without adding any new suppression: `run_review_loop`
  forwards verbatim to `_run_review_loop_iterations` (which keeps the original annotation with identical rule codes),
  `_dispatch_role` forwards optional extras via `**extra`, and the resolver's workspace volume travels inside
  `ResolverInput` as host-side dispatch context.
- Ran `uv run task format`-equivalent fixes (`ruff check --fix`, `ruff format`, `npx prettier --write`) on the touched
  files, then the full gate (below). Australian English throughout the new prose; arithmetic in code spans.

## Tests Run

All runs from the repository root with real output; the full suite runs `-n auto --dist worksteal` via
`uv run task test`. No performance target is set by the intent, so no repeated timing runs were made; the wall-clock win
is reported qualitatively (one network clone and container cold-start removed per pass after the first).

| command                                                                                                                                                                                     | result                                       | pytest time |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- | ----------- |
| `uv run pytest apps/sandbox-executor/tests/test_review_loop.py tests/test_review_dispatch.py tests/test_flow.py tests/test_cli.py -n auto --dist worksteal` (paths abbreviated; final tree) | 855 passed, 0 failed (+53 subtests)          | `5.40s`     |
| `uv run pytest apps/sandbox-executor/tests/test_review_workspace_integration.py -m integration_test`                                                                                        | 1 skipped (no Docker daemon, by design)      | `0.02s`     |
| `uv run task test` (run 1, during Step 7, surfaced the complexity ratchet)                                                                                                                  | 3063 passed, 2 failed                        | `30.88s`    |
| `uv run task test` (run 2, after the ratchet fix)                                                                                                                                           | 3065 passed, 0 failed                        | `30.43s`    |
| `uv run task test` (run 3, final tree after formatting)                                                                                                                                     | 3065 passed, 0 failed                        | `30.28s`    |
| `uv run task lint`                                                                                                                                                                          | `All checks passed!`                         | n/a         |
| `uv run task lint-docs` (`npx prettier --check "**/*.md"`)                                                                                                                                  | `All matched files use Prettier code style!` | n/a         |

Median full-suite time across the three `uv run task test` runs: `30.43s`. The three full-suite runs were fix-verify
iterations of the single planned gate, not repeated timing measurements.
