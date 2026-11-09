# Plan for I-1794214501-mount-host-worktree-into-sandbox-containers--1-add-host-provisioned-worktree-bind-mount-dispatc

- **Plan ID:** P-1794222469-9841ab3e-claude-agent-claude-opus-5-5
- **Parent Intent ID:** I-1794214501-mount-host-worktree-into-sandbox-containers/\_
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-09T11:07:50.567Z

## Planner Autonomy Summary

- **Intent handling:** REFRAME (light, safety-driven sequencing; scope otherwise accepted as is)
- **Reframed intent (if applicable):**
  `{"keep": "worktree_mount.py, workspace_mount on run_docker_container and dispatch_role_agent, deletion of ReviewWorkspaceSession / review_session_volume_name / HOLON_REVIEW_PERSIST / root chown init container / SANDBOX_WORKSPACE_UID_GID and their tests, tests (a) and (f), lowered flow budgets", "change": "no production caller activates the host bind mount in this sub-intent; Stage 4 reverts to an ephemeral (unmounted) container workspace until the sibling sub-intent deletes the in-container clone, reset --hard and clean -fdx preamble and switches the callers on"}`
- **Exploration stance:** balanced. The deletion work is pure exploitation, but one cheap exploratory step pins down how
  git behaves when a linked worktree's admin directory is reached through a second bind mount, which is the one
  genuinely uncertain mechanism in the design.
- **Safety priority level:** elevated
- **Priority Justification:** The change gives role containers write access to a host directory for the first time
  (`docs/safety.md` "Container sandbox": filesystem read-only except the intent workspace; Invariant 2: sandboxing).
  Activating the mount while `entrypoint/review_loop.py` still runs `git reset --hard HEAD` and `git clean -fdx`, and
  while `entrypoint/executor.py` still calls `cleanup_repo_dir` on the workspace when `HOLON_TARGET_WORKTREE` is unset,
  would wipe the host's `flow-<slug>` worktree. That would be irreversible data loss on the host.

## Exploration

- **Proportion of steps that are exploratory:** 0.17 (1 of 6)
- **Justification:** Only Step 1 is exploratory. It is a hermetic, temp-directory probe of git's worktree/common-dir
  resolution and `git worktree prune` behaviour across two mount targets. It takes minutes and decides whether
  `worktree_mount.py` needs one, two or three mount entries plus protective git config. Everything else is a well-mapped
  deletion or a signature change.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.78  |
| entropy_pred        | 10.5  |
| impact_pred         | 82.0  |
| cost_pred           | 22.0  |
| learning_value_pred | 5.0   |
| ev_pred             | 41.31 |

### Strategy Rationale

The intent is the first child of a seven-point parent. It owns the host side: a new mount model, the dispatch signature
and the deletion of the Stage 4 named-volume machinery. The in-container side (deleting `_clone_or_fetch`,
`_reset_reused_checkout`, `_ensure_head`, `_origin_matches`, `_has_checkout`, tests (b) to (e) and (g)) is left to
sibling sub-intents.

Reading the code turned up a sequencing hazard the intent text does not mention. Both container entrypoints still treat
the workspace as disposable:

- `entrypoint/review_loop.py:177-188` runs `reset --hard` and `clean -fdx` on any existing checkout.
- `entrypoint/executor.py:1501-1512` clears the workspace contents when `HOLON_TARGET_WORKTREE` is unset.

If this sub-intent both added the bind mount and wired Stage 4 to use it, the first review pass after merge would delete
untracked files (`.venv`, `.subagent/`, scratch notes) and uncommitted edits in the host `flow-<slug>` worktree. That is
exactly the data loss the parent intent exists to prevent. The plan therefore delivers the capability fully and tests
it, but leaves it inert. `workspace_mount` defaults to `None` on every production path. Stage 4 dispatches mount
nothing, which is the historical clone-per-pass behaviour into the container's own ephemeral filesystem and is safe. As
defence in depth, `run_docker_container` also pins `HOLON_TARGET_WORKTREE` to the container workspace whenever a mount
is given, so the executor's cleanup branch can never fire on a bind mount. The sibling that purges the entrypoint
preamble switches the callers on. This costs nothing in scope, because the intent asks only for the dispatch to _take_
`workspace_mount`, and it removes the single catastrophic failure mode.

Aggregation:

- `entropy_pred` is the sum of step entropies: `1.0 + 2.5 + 3.0 + 2.5 + 0.8 + 0.7 = 10.5`, under the 15.0 budget.
- `cost_pred` is the sum of step costs: `2.0 + 5.0 + 5.5 + 5.0 + 1.5 + 3.0 = 22.0`.
- `p_success_pred` is set by the bottleneck, Step 3. That step edits `run_docker_container`, an already-suppressed
  `C901/PLR0912/PLR0915` function of about 250 lines in a 2026-line `cli/__init__.py`, and has to merge new
  `GIT_CONFIG_*` entries with the existing token entries without breaking credential scoping. Its p is 0.80. Small
  residual risk in Steps 2 and 4 (hidden coupling in `test_review_dispatch.py` and `flow/__init__.py` exports) pulls the
  overall figure slightly below the bottleneck, to 0.78.
- `impact_pred` is 82: this unblocks every remaining sibling and deletes about 250 lines of fragile code and tests.
- `learning_value_pred` is 5.0, mostly from Step 1.
- `EV = 0.78 * 82.0 + 0.5 * 5.0 - 0.3 * 10.5 - 22.0 = 63.96 + 2.50 - 3.15 - 22.0 = 41.31`.

Strong assumptions:

1. The image user is uid/gid 1000 and the typical host developer uid is also 1000 (true in this workspace). Other uids
   are reported, not fixed (see Step 2).
2. The flow-engine budget ratchet only covers `sandbox_executor/flow/`. A new top-level
   `sandbox_executor/worktree_mount.py` therefore needs no budget entry, which keeps it outside the flow engine as
   required ("no new imports from entrypoints into the host flow engine").

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1 (commit boundaries scoped to this branch; never `main`).
  - `holon-config/world/constraints.md` §2 (Container sandbox: complete network isolation; escape actions forbidden).
  - `holon-config/world/constraints.md` §3 (exactly three append-only ledgers; nothing here writes a ledger).
  - `holon-config/world/ruleset.md` §2 (typed, docstringed public API; no wildcard imports; clean architecture).
  - `holon-config/world/ruleset.md` §3 (pytest tests in `tests/` mirroring the source module; test deletions are
    declared in this plan).
  - `docs/safety.md` "Container sandbox" (read-only filesystem except the intent workspace) and Invariant 2
    (Sandboxing).
  - `apps/sandbox-executor/flow_module_budgets.json` ratchet (budgets may only fall).
  - Repository ruff limits (`C901 <= 10`, `PLR0912 <= 12`, `PLR0913 <= 5`, `PLR0915 <= 50`); no new `# noqa`.
- **Potential violations or edge cases:**
  - Destructive in-container preambles wiping a host bind mount (see Strategy Rationale).
  - The git common directory has to be mounted read-write for commits. That exposes all of the repository's refs and
    objects, not only the worktree.
  - `git worktree prune` or auto-gc inside the container would see every _other_ worktree registered in the common
    directory as missing (their paths do not exist in the container) and could drop their registrations on the host.
  - Docker silently creating a root-owned host directory when a `-v` source path does not exist.
  - A host worktree owned by a uid other than 1000 being unwritable by the container user, or tripping git's
    `safe.directory` check.
  - The new mount arguments leaking a token or proxy URL through `ps`. Not applicable, because paths are not secrets,
    but redaction stays on every error path.
- **Mitigations built into the plan:**
  - The mount is inert on every production path in this sub-intent. `HOLON_TARGET_WORKTREE` is pinned whenever a mount
    is passed.
  - `--mount type=bind,source=...,target=...` is used instead of `-v`, so Docker refuses a missing source instead of
    creating it.
  - The common directory is mounted at its identical absolute path, so the worktree's `.git` file needs no rewrite. The
    host worktree is also mounted at its own host path, so the admin directory's `gitdir` back-pointer stays valid. Both
    are confirmed in Step 1.
  - Runtime-only git config (`gc.auto=0`, `gc.worktreePruneExpire=never`, `safe.directory` for the two container paths)
    is passed through `GIT_CONFIG_COUNT` entries appended after the existing credential entries. It never touches
    `~/.gitconfig` on the host.
  - `resolve_worktree_mount` refuses bare repositories, detached or unregistered directories, the default-branch
    worktree, non-absolute paths, and any source that would shadow container paths under `/home/holon`.
  - Every network-isolation flag is untouched, and tests assert the isolation arguments are byte-identical with and
    without a mount.
- **Residual risk accepted (and why):** Read-write exposure of the git common directory to a networkless container. It
  is the minimum the parent intent requires (containers must commit and push in place), and it is bounded by Invariant
  1: git is the safety boundary and pushes still go only to the role's branch. Host uid mismatch on non-1000 Linux hosts
  is logged as a warning rather than solved. Solving it (`--user` remapping) would change HOME ownership for the agent
  session mounts and belongs to a later intent.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 10.5
- **Budget Compliance:** The strategy fits within budget (10.5 of 15.0). It runs as a single plan with no sub-intent
  list.

## Plan Description & Strategy

Touch set, at module granularity:

- **New:** `apps/sandbox-executor/src/sandbox_executor/worktree_mount.py`,
  `apps/sandbox-executor/tests/test_worktree_mount.py`.
- **Modified source:**
  - `sandbox_executor/cli/__init__.py` (`run_docker_container`, `dispatch_role_agent`)
  - `sandbox_executor/agent_runner.py` (drop `SANDBOX_WORKSPACE_UID_GID`)
  - `sandbox_executor/flow/review/workspace.py` (keep only the reviewer sandbox guard)
  - `sandbox_executor/flow/review/loop.py` (drop the session wrapper)
  - `sandbox_executor/flow/review/ensemble.py` and `sandbox_executor/flow/review/resolver.py` (`workspace_volume`
    becomes `workspace_mount`)
  - `sandbox_executor/flow/__init__.py` (exports)
  - `sandbox_executor/flow/dispatch.py` (docstring)
- **Modified tests:** `tests/test_cli.py`, `tests/test_review_dispatch.py`.
- **Deleted tests:** `tests/test_review_workspace_integration.py`.
- **Config and docs:** `apps/sandbox-executor/flow_module_budgets.json`, `apps/sandbox-executor/README.md`,
  `docs/git_flow.md`.

Order: probe git behaviour, build the mount model, plumb it through dispatch, delete the volume machinery, clean up docs
and budgets, then validate once. Steps 2 to 4 are each committed separately on the execution branch so a reviewer can
bisect them.

---

## Step 1: Probe linked-worktree git resolution across split mount targets

- **Sub‑intent recommendation:** NO
- **Reasoning:** A 15-minute hermetic probe in a temp directory. It is not reusable on its own, but it fixes the mount
  layout that Steps 2 and 3 encode.
- **Step Type:** EXPLORATION
- **Exploration level:** EXPLORATORY

- **Hypothesis being tested:** Mounting the linked worktree at the container workspace path, and the git common
  directory plus the worktree at their identical host paths, is enough for `git rev-parse HEAD`, `git status`,
  `git commit` and `git push` to a local bare remote to work. A second linked worktree registered in the same common
  directory survives `git worktree prune` only when `gc.worktreePruneExpire=never` is set.
- **Learning target:**
  - Whether the admin directory's `gitdir` back-pointer must resolve (two worktree targets) or whether one is enough.
  - Whether `safe.directory` must list the container workspace path, the host path, or both.
  - Whether `GIT_CONFIG_COUNT` config suppresses prune.
- **Maximum acceptable cost for this learning:** 2.0 cost units (about 15 minutes). If the probe is inconclusive,
  default to the conservative superset: both worktree targets plus all three protective config entries.

### Intent & Git Integration

- **Step Intent:** Turn the one uncertain mechanism into a measured fact before writing code.
- **Git branch:** the execution branch for this plan (no commit; findings go into Step 2's docstrings and the execution
  report)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In a pytest `tmp_path`-style scratch directory outside the repository, create a bare "remote", a main clone and two
   linked worktrees (`flow-a`, `flow-b`) with `git worktree add`.
2. Simulate the container's view without Docker. Copy or symlink the paths so that the worktree is visible at a second
   path and its `.git` file still names the common directory. Alternatively, if a Docker daemon is available, run the
   review-loop image with `--network none` and the candidate `--mount` set.
3. Run `rev-parse HEAD`, `status`, a commit and a push to the bare remote from the second path, with and without the
   back-pointer path present.
4. Run `git worktree prune` from the second path, with and without `GIT_CONFIG_COUNT` setting
   `gc.worktreePruneExpire=never`, and check whether `flow-b`'s registration survives.
5. Record the minimal mount set and config entries in the execution report. They become test fixtures in Step 2.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO (the conservative superset is a safe fallback)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §2 (no external network); `docs/testing/testing_guide.md` /
  `apps/sandbox-executor/docs/hermetic_testing.md` (hermetic fixtures, `--network none`).
- **Potential failure modes for this step:** No Docker daemon in the execution sandbox. Probing against the real
  repository's `.git` by mistake.
- **Guardrails and early‑abort checks:** Work only under a fresh temp directory. Never run `git worktree prune` against
  the workspace repository. Abort the probe and fall back to the superset after 15 minutes.

### Success & Discard Criteria

- **Success:** A recorded minimal mount set and config list, each backed by an observed git outcome.
- **Discard:** Cost above `1.5 * 2.0 = 3.0` units, or Docker unavailable and the simulation inconclusive. Adopt the
  conservative superset and move on.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.0   |
| impact_pred         | 15.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 14.5  |

### Step Metrics Rationale

`EV = 0.92 * 15.0 + 0.5 * 6.0 - 0.3 * 1.0 - 2.0 = 13.80 + 3.00 - 0.30 - 2.00 = 14.50`. Learning value is high relative
to cost because the prune behaviour is the main way a bind-mounted container can silently damage _other_ host worktrees,
and the result can be reused for the executor stages in sibling intents.

---

## Step 2: Add `worktree_mount.py` with validation, provisioning and mount arguments (test a)

- **Sub‑intent recommendation:** NO
- **Reasoning:** A focused new module of about 150 lines with pure-function tests. Moderate risk and reusable, but small
  enough to stay in this plan.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Give the host one owner for "which host worktree is mounted where, and how its git directory stays
  resolvable".
- **Git branch:** the execution branch for this plan (commit 1 of 3)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `apps/sandbox-executor/src/sandbox_executor/worktree_mount.py` with a module docstring explaining the
   host/container split of responsibilities (Australian English).
2. Define a frozen dataclass `WorktreeMount` with fields `host_worktree` (realpath), `git_common_dir` (realpath) and
   `container_workspace` (defaults to `agent_runner.SANDBOX_WORKSPACE_MOUNT`).
3. Add `resolve_worktree_mount(host_worktree, *, slug=None, target_branch="origin/main") -> WorktreeMount`:
   - If the path is absent and a slug is given, provision it through the existing `git_flow.ensure_worktree` helper. If
     the path is the default-branch worktree, use `git_flow.ensure_flow_worktree_off_default`. No new git plumbing.
   - Validate with `git_flow.is_working_tree`, `git_flow.is_bare_repository` and `git_flow.target_worktree_problem`. On
     failure, raise `WorktreeMountError` (a `ValueError` subclass) with an actionable, redacted message.
   - Read `git rev-parse --path-format=absolute --git-dir` and `--git-common-dir` through
     `sandbox_executor.git.run_git`. Require the worktree to be attached, meaning the git directory is the common
     directory or sits under its `worktrees/` subdirectory.
   - Refuse non-absolute paths, and any source or target that is, or falls under, `/home/holon`, so a mount can never
     shadow container-owned paths.
   - On Linux, if the worktree owner's uid is not 1000, log a single warning naming the limitation. Do not fail.
4. Add `docker_mount_args(mount) -> list[str]`, returning
   `--mount type=bind,source=<host_worktree>,target=<container_workspace>` plus the target set Step 1 fixed (default
   superset: the worktree again at its host path, and the common directory at its host path). Skip the common-directory
   entry when it already lies inside the worktree, as in a non-linked main checkout.
5. Add `git_config_entries(mount) -> list[tuple[str, str]]`, returning the runtime-only protective config from Step 1
   (`gc.auto=0`, `gc.worktreePruneExpire=never`, `safe.directory` for the container workspace and the host path).
6. Keep every function within the ruff limits. The module must not import from `sandbox_executor.flow` or
   `sandbox_executor.entrypoint`.
7. Add `apps/sandbox-executor/tests/test_worktree_mount.py`. It builds a hermetic local bare remote, main clone and
   linked worktree in `tmp_path` (reusing `tests/worktree_fixtures.py` where it fits), with no network. It asserts the
   following (test a):
   - An existing linked worktree resolves to the right realpaths.
   - An absent `flow-<slug>` path is provisioned through the helpers and then mounted.
   - Bare repositories, plain directories and the default-branch worktree are refused.
   - The mount arguments contain the worktree target and a common-directory target whose `.git` file `gitdir:` value
     resolves under the mounted common directory.
   - No `-v` form and no `volume` token appears.
   - Paths under `/home/holon` are refused.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Steps 3 and 4 consume `WorktreeMount`), p = 0.85

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` "Container sandbox"; `holon-config/world/ruleset.md` §2 (types, docstrings);
  hermetic testing rules.
- **Potential failure modes for this step:**
  - Accidentally reusing `ensure_worktree` semantics that recreate an existing worktree.
  - Path-realpath mismatches on macOS (`/private/var`).
- **Guardrails and early‑abort checks:**
  - Assert in tests that an existing worktree's inode and an uncommitted file inside it survive
    `resolve_worktree_mount`.
  - Compare realpaths on both sides.

### Success & Discard Criteria

- **Success:** `uv run pytest apps/sandbox-executor/tests/test_worktree_mount.py` passes, `ruff check` is clean with no
  new `noqa`, and the module is at most about 200 lines.
- **Discard:** The provisioning helpers cannot be reused without modification beyond about 20 lines. Stop and record a
  follow-up intent rather than growing `git_flow.py` (already 470 lines).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.5   |
| impact_pred         | 30.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 21.25 |

### Step Metrics Rationale

`EV = 0.85 * 30.0 + 0.5 * 3.0 - 0.3 * 2.5 - 5.0 = 25.50 + 1.50 - 0.75 - 5.00 = 21.25`. This is new code with real git
subprocess tests, so it carries moderate entropy, but its well-tested helpers keep p high.

---

## Step 3: Replace `review_workspace_volume` with `workspace_mount` in `run_docker_container` and `dispatch_role_agent`

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the riskiest edit, because it touches the single docker dispatch path and the credential
  `GIT_CONFIG_*` block. It stays inline because splitting it from Step 4 would leave a half-renamed keyword across two
  branches.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the dispatch path take a validated `WorktreeMount` and emit bind mounts, with no named volume
  and every isolation flag unchanged.
- **Git branch:** the execution branch for this plan (commit 2 of 3)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `sandbox_executor/cli/__init__.py`:
   - Change `run_docker_container`'s `review_workspace_volume: str | None` parameter to
     `workspace_mount: WorktreeMount | None`, and update its docstring.
   - When a mount is given, pin `HOLON_TARGET_WORKTREE` to `mount.container_workspace`, keeping the existing pinning
     logic. This prevents the executor's workspace cleanup branch from ever running on a bind mount.
   - Replace the `-v <volume>:<workspace>` line with `docker_mount_args(mount)`.
2. Where the token's `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_n` entries are built (around line 1468), append the
   `git_config_entries(mount)` entries after the credential entries and recompute the count. With no token, the mount
   entries start at index 0. Extract this into a small helper (for example
   `_git_config_env(helper_entries, mount_entries)`) so the already-suppressed function does not grow in branches. If
   the extraction lowers complexity, do not widen the existing `noqa`. Never add one.
3. In `dispatch_role_agent`, rename the keyword to `workspace_mount: WorktreeMount | None`, forward it only when not
   None, and update the Args docstring.
4. Leave `get_agent_session_mounts`, `build_network_isolation_args`, the env-file handling and the redaction calls
   untouched.
5. In `tests/test_cli.py`, replace the two `review_workspace_volume` tests (around lines 2350 and 2396) with
   `workspace_mount` equivalents. They assert:
   - The `--mount type=bind` entries appear exactly once each.
   - `HOLON_TARGET_WORKTREE` is pinned even when the host forwards one.
   - The `GIT_CONFIG_COUNT` total and indices are contiguous, with and without a token.
   - The network-isolation argument slice is identical with and without a mount.
   - No argument contains `volume` or `chown`.
   - With `workspace_mount=None`, the command is byte-identical to today's non-volume command.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES, p = 0.80

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §2 (network isolation); `docs/safety.md` Invariant 2; the
  existing credential-handling comments (secrets via a 0600 env-file, never argv).
- **Potential failure modes for this step:**
  - Off-by-one `GIT_CONFIG_COUNT` silently dropping the credential helper reset, which would break push auth or scope.
  - A mount path containing a comma, which breaks `--mount` parsing.
- **Guardrails and early‑abort checks:**
  - A test asserts entry 0 is still the helper reset when a token is present.
  - `docker_mount_args` refuses paths containing commas (validated in Step 2).
  - Abort if any existing credential-scoping test in `test_cli.py` changes expectation.

### Success & Discard Criteria

- **Success:**
  - `uv run pytest apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_worktree_mount.py` passes.
  - No credential test needed editing.
  - `grep review_workspace_volume` over `src/` and `tests/` returns nothing.
- **Discard:** Any credential-scoping test has to be changed to pass, or cost exceeds `1.5 * 5.5 = 8.25` units. Stop and
  re-plan the config merge.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 3.0   |
| impact_pred         | 25.0  |
| cost_pred           | 5.5   |
| learning_value_pred | 2.5   |
| ev_pred             | 14.85 |

### Step Metrics Rationale

`EV = 0.80 * 25.0 + 0.5 * 2.5 - 0.3 * 3.0 - 5.5 = 20.00 + 1.25 - 0.90 - 5.50 = 14.85`. This is the bottleneck: a large,
already over-complex function on the credential path. Entropy is the highest in the plan, because a mistake here affects
every role container, not only Stage 4.

---

## Step 4: Delete the Stage 4 named-volume machinery and its tests (test f)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical deletion with a clearly enumerated reference list. Low risk once Step 3 has renamed the
  dispatch keyword.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Remove `ReviewWorkspaceSession`, `review_session_volume_name`, `review_persist_enabled` and
  `HOLON_REVIEW_PERSIST`, `REVIEW_SESSION_VOLUME_LABEL`, the root chown init container and `SANDBOX_WORKSPACE_UID_GID`,
  with no fallback left behind.
- **Git branch:** the execution branch for this plan (commit 3 of 3)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `flow/review/workspace.py`:
   - Delete everything except `HOST_REVIEW_REFUSAL` and `reviewers_sandboxed`, along with the now-unused `hashlib`,
     `os`, `subprocess`, `Callable`, `FALLBACK_AGENT`, `normalise_agent_id`, `redact_agent_secrets` and `agent_runner`
     imports.
   - Update the module docstring. The file drops from 168 lines to about 30.
2. `flow/review/loop.py`:
   - Remove the `run_review_loop` session wrapper's volume logic. `run_review_loop` becomes a thin pass-through, or is
     folded so the public name keeps its signature minus `workspace_session`.
   - Drop the `workspace_volume=workspace_session.volume_name() ...` arguments at the two call sites (around lines 368
     and 648).
   - Remove the `inspect` import if it becomes unused.
3. `flow/review/ensemble.py` and `flow/review/resolver.py`:
   - Rename the `workspace_volume` parameter and the `ResolverInput.workspace_volume` field to
     `workspace_mount: WorktreeMount | None = None`, forwarded as the `workspace_mount` dispatch keyword.
   - Rewrite the docstrings to say the host-provisioned worktree mount is activated by the sibling sub-intent once the
     in-container preamble is gone. Nothing sets it in this sub-intent.
4. `flow/__init__.py`: remove the `ReviewWorkspaceSession`, `review_session_volume_name` and `review_persist_enabled`
   imports and `__all__` entries.
5. `flow/dispatch.py`: update the `_dispatch_role` docstring example from `review_workspace_volume` to
   `workspace_mount`.
6. `agent_runner.py`: delete `SANDBOX_WORKSPACE_UID_GID` and its comment, and keep `SANDBOX_WORKSPACE_MOUNT`.
7. Test deletions (declared here per `holon-config/world/ruleset.md` §3):
   - In `tests/test_review_dispatch.py`, delete the docker-recording session helper (around lines 295-313) and the tests
     at about 387, 437, 467, 521 and 546 (shared session volume, teardown on raise, kill switch, creation-failure
     fallback, persist toggle and volume names).
   - Keep `test_sandbox_workspace_mount_matches_the_container_workspace_default`.
   - Delete `tests/test_review_workspace_integration.py` entirely: it exercises only the volume plus chown plus
     incremental-fetch path.
   - Keep `tests/hermetic_fixtures.py` (its chown is for seeded-remote volumes used by other integration tests). Verify
     with grep that its exports still have users.
8. Add test (f) in `tests/test_review_dispatch.py`. It runs `run_review_loop` in host engine mode with a recording
   `subprocess.run`/docker seam and a fake dispatcher, then asserts:
   - No `docker volume` command and no `--user root` or `chown` container is ever launched.
   - No dispatch receives a volume-style keyword.
   - Importing `ReviewWorkspaceSession` or `review_session_volume_name` from `sandbox_executor.flow` raises
     `ImportError`.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO (failures are local and mechanical)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (declared test changes); flow budget ratchet; `docs/safety.md`
  Invariant 2 (`reviewers_sandboxed` guard must survive).
- **Potential failure modes for this step:**
  - Accidentally deleting `reviewers_sandboxed` or `HOST_REVIEW_REFUSAL`, which are still used by the sandbox guard.
  - Breaking callers that inject `workspace_session` in other tests.
- **Guardrails and early‑abort checks:**
  - `grep -rn "workspace_session\|review_persist\|REVIEW_SESSION_VOLUME\|SANDBOX_WORKSPACE_UID_GID"` over `src/` and
    `tests/` must return nothing.
  - The existing reviewer-refusal tests must pass unchanged.

### Success & Discard Criteria

- **Success:**
  - `uv run pytest apps/sandbox-executor/tests/test_review_dispatch.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_review_ensemble.py apps/sandbox-executor/tests/test_review_resolver_failure.py`
    passes.
  - The grep guard is empty.
- **Discard:** More than three unrelated test modules depend on the session object. Pause and add them to the declared
  deletion list in the execution report before continuing.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 2.5   |
| impact_pred         | 22.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 13.04 |

### Step Metrics Rationale

`EV = 0.82 * 22.0 + 0.5 * 1.5 - 0.3 * 2.5 - 5.0 = 18.04 + 0.75 - 0.75 - 5.00 = 13.04`. Deletion lowers system entropy
long term, but `loop.py` is 960 lines with two suppressed functions, so there is some risk of collateral breakage.

---

## Step 5: Update docs and lower flow module budgets

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, deterministic documentation and config edits.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Leave no reference that suggests the named-volume path still exists, and ratchet the budgets down to
  the new sizes.
- **Git branch:** the execution branch for this plan (folded into commit 3)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `apps/sandbox-executor/README.md` (around lines 200-216): replace the persistent review workspace volume paragraph
   and the `HOLON_REVIEW_PERSIST=0` note. The new text says Stage 4 containers currently use an ephemeral in-container
   checkout, and that a host-provisioned worktree bind mount (`sandbox_executor.worktree_mount`) is available on the
   dispatch path and enabled by the follow-up sub-intent.
2. `docs/git_flow.md` (around line 368): remove the `HOLON_REVIEW_PERSIST` and leaked-volume pruning sentences in the
   same way.
3. Repo-wide search over docs, CI configuration, the Makefile, the Dockerfile and `entrypoint/role_dispatcher.sh` for
   `HOLON_REVIEW_PERSIST`, `holon-review-ws`, `holon.review-session`, `ReviewWorkspaceSession` and
   `review_session_volume_name`. Update every hit. Leave historical plan and execution records under `plans/` and
   `executions/` untouched, because they are records.
4. `apps/sandbox-executor/flow_module_budgets.json`: set `review/workspace.py`, `review/loop.py`, `review/ensemble.py`,
   `review/resolver.py`, `__init__.py` and `dispatch.py` to their new measured line counts. Never raise any value.
5. Use Australian English throughout (for example "initialise", "behaviour").

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** flow budget ratchet (budgets may only fall); `uv run task lint-docs` (prettier).
- **Potential failure modes for this step:**
  - Prettier reflow failures.
  - A budget set below the real count (lint fails) or left above it (a missed ratchet).
- **Guardrails and early‑abort checks:** Run `python scripts/check_flow_module_budgets.py` and `npx prettier --check` on
  the edited markdown immediately.

### Success & Discard Criteria

- **Success:** The repo-wide grep returns only historical `plans/` and `executions/` hits, the budget script passes, and
  every touched budget is lower than before.
- **Discard:** Not applicable (deterministic). Escalate only if a budget would have to rise.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.8   |
| impact_pred         | 6.0   |
| cost_pred           | 1.5   |
| learning_value_pred | 0.5   |
| ev_pred             | 3.91  |

### Step Metrics Rationale

`EV = 0.90 * 6.0 + 0.5 * 0.5 - 0.3 * 0.8 - 1.5 = 5.40 + 0.25 - 0.24 - 1.50 = 3.91`. Low impact on its own, but it is
required by the parent's purge and "do not raise any budget" constraints.

---

## Step 6: Validate lint, docs and the test suite once

- **Sub‑intent recommendation:** NO
- **Reasoning:** A standard validation gate. No timing measurement is involved, so tests run once.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the branch is green before handing it to review.
- **Git branch:** the execution branch for this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run the affected tests first:
   - `uv run pytest apps/sandbox-executor/tests/test_worktree_mount.py`
   - `uv run pytest apps/sandbox-executor/tests/test_cli.py`
   - `uv run pytest apps/sandbox-executor/tests/test_review_dispatch.py`
   - `uv run pytest apps/sandbox-executor/tests/test_review_loop.py`
   - `uv run pytest apps/sandbox-executor/tests/test_review_ensemble.py`
   - `uv run pytest apps/sandbox-executor/tests/test_review_resolver_failure.py`
   - `uv run pytest apps/sandbox-executor/tests/test_flow_target_worktree.py`
   - `uv run pytest apps/sandbox-executor/tests/test_flow_default_branch_worktree.py`
   - `uv run pytest apps/sandbox-executor/tests/test_complexity_gate.py`
2. Run `uv run task lint`. This covers ruff check, ruff format check, the `uv.lock` check and the flow budget script.
   Then run `uv run task lint-docs`.
3. Run the full suite once with `uv run task test`, which already uses `-n auto --dist worksteal`. Report its pass/fail
   counts and pytest-reported time.
4. Confirm no new dependency appears in any `pyproject.toml` or `uv.lock`, and that no ledger file changed.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3, 4, 5
- **Is Bottleneck:** NO (it detects failures; it does not cause them)

### Safety & Constraint Considerations

- **Relevant rules:** Test-Run Discipline (one full run); `holon-config/world/ruleset.md` §3 (pytest via uv);
  `holon-config/world/constraints.md` §3 (ledgers untouched).
- **Potential failure modes for this step:**
  - Unrelated flaky tests.
  - Docker-dependent tests skipping. Expected and acceptable, because the integration file is deleted and no new Docker
    test is added.
- **Guardrails and early‑abort checks:** On a failure, fix within the touched modules only. A failure outside the touch
  set is reported, not patched.

### Success & Discard Criteria

- **Success:** All three commands exit 0, with the full-suite counts and time recorded in the execution report.
- **Discard:** More than two fix-and-rerun cycles on the same failure. Stop and report.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 0.7   |
| impact_pred         | 8.0   |
| cost_pred           | 3.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 4.33  |

### Step Metrics Rationale

`EV = 0.88 * 8.0 + 0.5 * 1.0 - 0.3 * 0.7 - 3.0 = 7.04 + 0.50 - 0.21 - 3.00 = 4.33`. Cost is dominated by the full suite
run. Entropy is minimal because the step changes nothing on its own.

---

## Sub-intent list

None. The plan fits the 15.0 entropy budget (predicted 10.5) and runs as one plan. The in-container preamble purge, the
caller activation of `workspace_mount` and tests (b) to (e) and (g) belong to the parent's sibling sub-intents, not to
children of this one.

## Handoff Notes for Sibling Sub-intents

- Activating the mount means three changes. First, delete the destructive preamble in `entrypoint/review_loop.py` and
  the workspace cleanup in `entrypoint/executor.py`. Second, have `flow/review/loop.py` call
  `resolve_worktree_mount(context.target_worktree, slug=...)` once per session and pass the result as `workspace_mount`
  to the ensemble and resolver dispatches. Third, do the same for Stages 1 to 3 through `flow/dispatch.py`.
- Never activate the mount before the entrypoint purge has merged into the parent branch.
- The ephemeral `flow-<slug>-rev<index>` reviewer worktrees can reuse `resolve_worktree_mount` unchanged, because it
  accepts any attached linked worktree.
