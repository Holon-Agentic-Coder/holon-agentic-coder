# Plan for I-1794214501-mount-host-worktree-into-sandbox-containers--1-add-host-provisioned-worktree-bind-mount-dispatc

- **Plan ID:** P-1794214810-8b2770b7-claude-agent-claude-opus-5-5
- **Parent Intent ID:** I-1794214501-mount-host-worktree-into-sandbox-containers/\_
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-09T09:00:11.153Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS (with one explicit sequencing guardrail, not a reframe)
- **Reframed intent (if applicable):** N/A. Guardrail: this sub-intent adds the host-side `workspace_mount` dispatch
  path and its tests, but does not make any production caller (Stage 1-5 dispatch, `holon plan|execute|intent`) pass a
  mount yet. The reason is that every container entrypoint still runs `cleanup_repo_dir` (which clears the contents of a
  mounted directory) and `git clone ... .`, or `git reset --hard` plus `git clean -fdx` (review loop), on its workspace.
  If a host worktree were mounted there before the container-side purge (a sibling sub-intent) lands, it would be wiped
  on the host. Stage 4 falls back to its existing clone-per-pass behaviour, which is what `HOLON_REVIEW_PERSIST=0`
  already does today.
- **Exploration stance:** conservative. Most of the work is deletion plus one small new module with a clear contract.
  The only real novelty is how the worktree's git directory is made resolvable, and that is tested hermetically without
  Docker.
- **Safety priority level:** elevated
- **Priority Justification:** `docs/safety.md` "Container sandbox: filesystem mounted read-only except for intent
  workspace" and `holon-config/world/constraints.md` section 2 (sandbox containment, no modification of read-only
  directories). A bind mount gives the container host-filesystem write access, so the mount set must be exactly the
  worktree plus its git common directory. Turning it on too early would destroy host data (see guardrail).

## Exploration

- **Proportion of steps that are exploratory:** 0.17 (1 of 6: Step 1 info-gathering/verification)
- **Justification:** The code paths are fully mapped (see Plan Description). The one uncertain point, git resolving a
  linked worktree's `.git` pointer inside the container, is handled with the identity-path mount of the common git
  directory, which is deterministic and unit-testable on the host.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.74  |
| entropy_pred        | 11.0  |
| impact_pred         | 78.0  |
| cost_pred           | 16.0  |
| learning_value_pred | 4.5   |
| ev_pred             | 40.0  |

### Strategy Rationale

Strategy: start with an inventory (Step 1), then add the new focused module (Step 2), then thread it through dispatch
(Step 3), then delete the named-volume machinery (Step 4), then update docs, budgets and snapshots (Step 5), then
validate (Step 6). Additive work comes before deletion, so the new dispatch tests pass before the old seams go away.
This keeps every intermediate commit runnable.

Aggregation:

- Entropy is the sum of the step entropies: `0.5 + 2.5 + 2.5 + 3.0 + 1.0 + 1.5 = 11.0`. That is within the 15.0 budget,
  so no `## Sub-intent list` is needed.
- p_success is a bottleneck-weighted product, not a straight product. Steps 3 and 4 are correlated, because both fail on
  the same misunderstanding of the dispatch seams. The straight product `0.97 * 0.90 * 0.88 * 0.85 * 0.93 * 0.92 = 0.58`
  treats them as independent and understates success. The bottleneck, Step 4 at 0.85, discounted for the snapshot and
  fixture tail (Steps 5-6), gives about 0.74.
- Impact is 78. This sub-intent unlocks the parent intent (lossless crashes, no volume orphans) and removes about 200
  lines of fragile code. It is not 90+ because the mount is dormant until the sibling container-side purge lands.
- Cost is the sum of the step costs: `1.0 + 3.0 + 3.5 + 4.5 + 2.0 + 2.0 = 16.0`.
- `EV = P * Impact + mu * LV - lambda * Delta_S - Cost = 0.74 * 78.0 + 0.5 * 4.5 - 0.3 * 11.0 - 16.0 = 57.72 + 2.25 - 3.30 - 16.0 = 40.67`,
  reported rounded as 40.0 to absorb the snapshot risk.

Strong assumptions:

- The intent's "Steps 1, 2" mean host provisioning/mounting plus the dispatch signature change. The in-container purge
  (`entrypoint/review_loop.py` `_clone_or_fetch` and related functions; tests (b)-(e) and (g)) belongs to sibling
  sub-intents.
- The `tests/flow/fixtures/flow_py_definitions.json` move-proof snapshot may be pruned or updated for deleted and
  changed definitions, as earlier intents did when they added `ReviewWorkspaceSession`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `constraints.md` §1: commits scoped to this sub-intent branch; nothing touches `main`.
  - `constraints.md` §2 and `safety.md` container sandbox: the mount set stays minimal and isolation flags are
    unchanged.
  - `constraints.md` §3: ledgers are append-only. The plan never edits `holon-knowledge/ledger/*.jsonl`.
  - `ruleset.md` §2: docstrings and typing on every public function, PEP 8.
  - `ruleset.md` §3: test changes are declared explicitly (Steps 2-4). Tests run with `uv run pytest`.
  - Flow module budgets (`apps/sandbox-executor/flow_module_budgets.json`) may only go down.
  - Ruff complexity limits apply, with no new `# noqa`.
  - No new dependencies. No imports from `entrypoint/` into `flow/`.
- **Potential violations or edge cases:**
  - Mounting a host worktree into a container whose entrypoint wipes its workspace would delete host files.
  - The git common directory mounted read-write exposes other worktrees' refs and metadata to the container.
  - Host UID other than 1000 would make the bind mount unwritable for the image's `holon` user. Git could also report
    "dubious ownership".
  - `run_docker_container` already carries `noqa: C901, PLR0911, PLR0912, PLR0913, PLR0915`. Adding a branch must not
    require a new suppression.
- **Mitigations built into the plan:**
  - No production caller passes `workspace_mount` in this sub-intent. A test asserts that default flow and Stage 4
    dispatch kwargs carry no `workspace_mount` and no named volume.
  - The mount args are built in `worktree_mount.py` (a pure function), and `run_docker_container` only extends with
    them. This keeps the complexity of `run_docker_container` flat.
  - `resolve_workspace_mount` refuses a bare repository, a non-worktree, a path equal to `/` or `$HOME`, and a missing
    or unreadable common git directory.
  - UID mismatch and `safe.directory` are documented as follow-ups for the sibling sub-intent that activates the mount.
    This plan does not activate it, so they cannot cause harm here.
- **Residual risk accepted (and why):** The move-proof snapshot or other hash fixtures may need more pruning than
  expected. That is low-severity churn, contained to test fixtures and declared up front.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 11.0
- **Budget Compliance:** The strategy fits within budget (11.0 ≤ 15.0); executed as one plan, no sub-intent list.

## Plan Description & Strategy

Current state (verified by reading the code):

- `flow/review/workspace.py` (168 lines, budget 168) holds `ReviewWorkspaceSession` (`docker volume create`, the root
  `chown` init container using `SANDBOX_WORKSPACE_UID_GID`, and teardown), `review_session_volume_name`,
  `review_persist_enabled`/`HOLON_REVIEW_PERSIST` and `REVIEW_SESSION_VOLUME_LABEL`. It also holds the unrelated
  `reviewers_sandboxed` and `HOST_REVIEW_REFUSAL`, which stay.
- `flow/review/loop.py`: `run_review_loop` is a wrapper that exists only to own the session lifecycle. It forwards
  `workspace_session` into `_run_review_loop_iterations`, which passes `workspace_volume=` at lines ~368 and ~648.
- `flow/review/ensemble.py` and `flow/review/resolver.py` take `workspace_volume` and forward it as
  `review_workspace_volume`.
- `flow/dispatch.py` `_dispatch_role` mentions `review_workspace_volume` in its docstring.
- `cli/__init__.py`: `run_docker_container` and `dispatch_role_agent` take `review_workspace_volume`. When it is set
  they add `-v <volume>:SANDBOX_WORKSPACE_MOUNT` and pin `HOLON_TARGET_WORKTREE`.
- `agent_runner.py:163`: defines `SANDBOX_WORKSPACE_UID_GID`.
- `flow/__init__.py`: exports `ReviewWorkspaceSession`, `review_session_volume_name` and `review_persist_enabled`.
- Tests:
  - `tests/test_review_dispatch.py`, roughly lines 290-560: session, volume, `HOLON_REVIEW_PERSIST` and failing-create
    tests.
  - `tests/test_review_workspace_integration.py`: the whole file is volume plus chown integration.
  - `tests/test_cli.py:2350-2430`: two `review_workspace_volume` tests.
  - `tests/flow/fixtures/flow_py_definitions.json`: snapshot entries for the session.
- Docs: `apps/sandbox-executor/README.md` around lines 200-216, and `docs/git_flow.md` around lines 366-370.
- Reusable host helpers: `git_flow.is_working_tree`, `git_flow.is_bare_repository`, `git_flow.ensure_worktree`,
  `git_flow.target_worktree_problem`.

Touch set (module granularity):

- NEW `apps/sandbox-executor/src/sandbox_executor/worktree_mount.py`
- `sandbox_executor/cli/__init__.py`
- `sandbox_executor/flow/dispatch.py`
- `sandbox_executor/flow/review/{workspace,loop,ensemble,resolver}.py`
- `sandbox_executor/flow/__init__.py`
- `sandbox_executor/agent_runner.py`
- `apps/sandbox-executor/flow_module_budgets.json`
- README and `docs/git_flow.md`
- Tests:
  - NEW `tests/test_worktree_mount.py`
  - `tests/test_cli.py`
  - `tests/test_review_dispatch.py`
  - DELETE `tests/test_review_workspace_integration.py`
  - `tests/flow/fixtures/flow_py_definitions.json`

`worktree_mount.py` sits at the package root, next to `git_flow.py`, not inside `flow/`. `cli` imports it, and so can
later flow stages, without any entrypoint-to-flow import.

---

## Step 1: Inventory and pin every reference to the volume machinery

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only grep plus a baseline test run. Cheap, and it removes surprises in the deletion steps.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** All references are the ones listed in the Plan Description. No CI config, Makefile,
  Dockerfile or `entrypoint/role_dispatcher.sh` references `HOLON_REVIEW_PERSIST`, `holon.review-session` or
  `SANDBOX_WORKSPACE_UID_GID`.
- **Learning target:** The exact deletion surface, and whether `flow_py_definitions.json` is regenerated by a helper or
  edited by hand.
- **Maximum acceptable cost for this learning:** 1.0 cost unit (about 10 minutes).

### Intent & Git Integration

- **Step Intent:** Produce a closed list of files and symbols to change.
- **Git branch:** current plan/execution branch (no new branch)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Grep the repository (src, tests, docs, README, Makefile, `.github/`, `holon-config/`,
   `apps/sandbox-executor/entrypoint/`, Dockerfile) for these terms:
   - `ReviewWorkspaceSession`, `review_session_volume_name`, `review_persist_enabled`
   - `REVIEW_SESSION_VOLUME_LABEL`, `holon.review-session`, `HOLON_REVIEW_PERSIST`
   - `SANDBOX_WORKSPACE_UID_GID`
   - `review_workspace_volume`, `workspace_volume`, `workspace_session`, `holon-review-ws-`
2. Read `tests/flow/move_proof.py` and `tests/flow/test_move_proof.py` to learn how snapshot entries are retired.
3. Run the affected tests once to get a baseline:
   `uv run pytest apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_review_dispatch.py apps/sandbox-executor/tests/flow -q`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §1 (scoped changes)
- **Potential failure modes for this step:** The baseline is already red for unrelated reasons.
- **Guardrails and early‑abort checks:** If the baseline fails, record the failing node IDs as pre-existing and do not
  attempt to fix them in this plan.

### Success & Discard Criteria

- **Success:** A written list of every reference, and a green baseline (or recorded pre-existing failures).
- **Discard:** References are found in unexpected subsystems (for example the executor entrypoint relies on
  `review_workspace_volume`). In that case, pause and re-scope.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.5   |
| impact_pred         | 10.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 9.55  |

### Step Metrics Rationale

`EV = 0.97 * 10.0 + 0.5 * 2.0 - 0.3 * 0.5 - 1.0 = 9.70 + 1.00 - 0.15 - 1.0 = 9.55`. The step is read-only, with
near-certain success.

---

## Step 2: Add `worktree_mount.py` (host validation/provisioning and mount arguments) with test (a)

- **Sub‑intent recommendation:** NO
- **Reasoning:** A single focused module of about 120 lines with pure functions, plus a small `git` subprocess surface
  already covered by `git_flow` helpers. It is reusable by later siblings but small enough to do inline.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Give the host one place that turns a target worktree path into a validated, immutable mount
  description and its `docker run` arguments.
- **Git branch:** current plan/execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `sandbox_executor/worktree_mount.py` with a module docstring explaining the host/container division of
   responsibilities.
2. Define a frozen dataclass `WorkspaceMount` with these fields:
   - `host_worktree`: absolute real path of the worktree top level
   - `git_common_dir`: absolute real path of the repository's common git directory
   - `container_workspace`: defaults to `agent_runner.SANDBOX_WORKSPACE_MOUNT`
3. Add `resolve_workspace_mount(target_worktree, *, slug=None, base_branch="origin/main")`:
   - If `git_flow.is_working_tree(target_worktree)` is false and a `slug` is given, provision through
     `git_flow.ensure_worktree(target_worktree, slug=slug, target_branch=base_branch)`. Otherwise raise
     `WorkspaceMountError`, using the message from `git_flow.target_worktree_problem`.
   - Refuse a bare repository. Refuse a top level equal to `/` or to the user's home directory.
   - Run `git rev-parse --show-toplevel` and `git rev-parse --git-common-dir`, using `git.run_git` or the existing
     helper so the command stays target-native. Make both paths absolute and real, and check that the common directory
     exists.
4. Add `workspace_mount_args(mount)`. It returns exactly two `-v` pairs:
   - `host_worktree:container_workspace`
   - `git_common_dir:git_common_dir`, an identity path, so the linked worktree's `.git` file
     (`gitdir: <common>/worktrees/<name>`) and its relative `commondir` resolve inside the container without network
     access For a main (non-linked) worktree, the common directory sits inside the top level. In that case return only
     the first pair.
5. Add `workspace_mount_env(mount)`. It returns `{"HOLON_TARGET_WORKTREE": container_workspace}`, so the entrypoint's
   `get_workspace_dir()` can never drift from the mountpoint (this replaces the old volume pin).
6. Keep each function well within C901 ≤ 10 and PLR0913 ≤ 5. Use Australian English in docstrings and messages.
7. NEW `tests/test_worktree_mount.py`, hermetic. Each case builds a tmp repo with a linked worktree and calls only local
   `git` (no Docker, no network). Test (a) cases:
   - An existing linked worktree resolves to the right `host_worktree` and `git_common_dir`.
   - The `.git` pointer file's target lies under the mounted common directory, which proves it is resolvable.
   - The args contain exactly the two binds with the identity path for the common directory.
   - An absent `flow-<slug>` path with a slug is provisioned through `ensure_worktree` and then mounted.
   - A plain directory, a bare repository and `/` raise `WorkspaceMountError`.
   - For a main worktree, a single bind is returned.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `safety.md` container sandbox (minimal writable mounts); `ruleset.md` §2-3
- **Potential failure modes for this step:**
  - The `--git-common-dir` output is relative. The function must resolve it against the worktree.
  - Symlinked tmp dirs on macOS (`/private/var`) cause path mismatches.
- **Guardrails and early‑abort checks:**
  - Always use `os.path.realpath` on both sides.
  - Tests compare realpaths.
  - Abort if provisioning would need network access in tests. Seed a local origin from the existing
    `tests/worktree_fixtures.py` instead.

### Success & Discard Criteria

- **Success:** `uv run pytest apps/sandbox-executor/tests/test_worktree_mount.py` is green. Ruff is clean, with no
  `noqa`.
- **Discard:** `ensure_worktree` cannot be reused without importing `cli/commands`. In that case, drop provisioning from
  this module (validation only) and note that `ensure_flow_worktree` already provisions before any stage.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 2.5   |
| impact_pred         | 25.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 19.25 |

### Step Metrics Rationale

`EV = 0.90 * 25.0 + 0.5 * 3.0 - 0.3 * 2.5 - 3.0 = 22.50 + 1.50 - 0.75 - 3.0 = 20.25`. The table shows 19.25 to reflect
the path-normalisation risk. Learning value comes from proving the identity-path git directory mount on real linked
worktrees.

---

## Step 3: Make `run_docker_container` / `dispatch_role_agent` take `workspace_mount` (replacing `review_workspace_volume`)

- **Sub‑intent recommendation:** NO
- **Reasoning:** A signature swap in two functions plus the flow dispatcher docstring. The risk is the many
  `test_cli.py` assertions on the docker argv, which are contained.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Role containers can be dispatched with a host bind-mounted worktree, and no longer with a named
  volume.
- **Git branch:** current plan/execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `cli/__init__.py`, replace the `review_workspace_volume: str | None` parameter of `run_docker_container` with
   `workspace_mount: WorkspaceMount | None = None`. The parameter count stays the same, so no new PLR0913 pressure.
2. Replace the env pin with `env_to_forward.update(workspace_mount_env(workspace_mount))` when the mount is set.
3. Replace the volume `-v` with `docker_cmd.extend(workspace_mount_args(workspace_mount))` at the same position (after
   the result-file mount, before the session mounts).
4. Rewrite the comment and docstring to describe the host-provisioned bind mount. Keep all isolation flags (network,
   `--rm`, env-file) unchanged.
5. In `dispatch_role_agent`, swap the keyword the same way and forward it only when it is not None. Update the
   docstring.
6. In `flow/dispatch.py`, change the `_dispatch_role` docstring example from `review_workspace_volume` to
   `workspace_mount`. The behaviour (dropping None extras) is unchanged.
7. Tests in `tests/test_cli.py`:
   - Replace the two `review_workspace_volume` tests (around lines 2350-2430) with
     `test_workspace_mount_bind_mounts_worktree_and_git_dir_and_pins_the_workspace`. It asserts exactly the binds from
     `workspace_mount_args`, `HOLON_TARGET_WORKTREE` pinned to `SANDBOX_WORKSPACE_MOUNT` overriding a forwarded host
     value, and no `volume` subcommand or named-volume source. This is the dispatch half of test (a).
   - Add a test that `dispatch_role_agent(..., workspace_mount=m)` forwards the mount to `run_docker_container`.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES (Step 4 removes the old seam that Steps 3 and 4 together replace)

### Safety & Constraint Considerations

- **Relevant rules:** `safety.md` container sandbox; Code Placement (no growth of the noqa'd function's complexity)
- **Potential failure modes for this step:**
  - A new branch pushes `run_docker_container` past an existing limit.
  - Someone wires `context.target_worktree` into `workspace_mount` by default, which would wipe the host worktree via
    the entrypoint's `cleanup_repo_dir`.
- **Guardrails and early‑abort checks:**
  - Use one `if workspace_mount:` block that replaces the two existing `if review_workspace_volume:` blocks one-for-one.
  - Explicitly do NOT derive a mount from `target_worktree` in `dispatch_role_agent` or `_dispatch_role`.

### Success & Discard Criteria

- **Success:**
  `uv run pytest apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_worktree_mount.py` is green.
  `grep review_workspace_volume src/` is empty.
- **Discard:** Unrelated `test_cli.py` argv snapshots break in more than 5 places. In that case, re-check that the mount
  position is unchanged before continuing.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 2.5   |
| impact_pred         | 25.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 18.75 |

### Step Metrics Rationale

`EV = 0.88 * 25.0 + 0.5 * 1.0 - 0.3 * 2.5 - 3.5 = 22.00 + 0.50 - 0.75 - 3.5 = 18.25`, rounded up slightly because the
seam is one-for-one. The change touches a 2026-line module with a heavily asserted argv.

---

## Step 4: Delete the Stage 4 named-volume machinery and its tests; add test (f)

- **Sub‑intent recommendation:** NO
- **Reasoning:** This step is mostly deletion across four flow modules plus exports. It is the highest-entropy step, but
  it is still bounded and reversible on the branch.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Remove `ReviewWorkspaceSession`, `review_session_volume_name`, `review_persist_enabled` and
  `HOLON_REVIEW_PERSIST`, the root chown init container, `REVIEW_SESSION_VOLUME_LABEL` and `SANDBOX_WORKSPACE_UID_GID`,
  with no fallback left behind.
- **Git branch:** current plan/execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `flow/review/workspace.py`:
   - Delete everything except `HOST_REVIEW_REFUSAL` and `reviewers_sandboxed`.
   - Drop the now-unused imports (`hashlib`, `os`, `subprocess`, `Callable`, the agent_runner constants, redaction,
     agent_defaults helpers other than `harness_detection_disabled`).
   - Retitle the module docstring to "the sandbox guard for reviewer turns".
2. `flow/review/loop.py`:
   - Remove the session-owning wrapper logic. Rename `_run_review_loop_iterations` back to `run_review_loop` and delete
     the old wrapper, or keep a minimal alias only if `inspect`-based callers need it. Prefer full removal.
   - Drop the `workspace_session` parameter and both `workspace_volume=` keyword arguments.
   - Drop the `inspect` import if it becomes unused.
   - Fix the docstring reference to the wrapper.
3. `flow/review/ensemble.py`: delete the `workspace_volume` parameter, its docstring paragraph and the `volume_kwargs`
   spreading.
4. `flow/review/resolver.py`: delete the `workspace_volume` field on the resolver input dataclass, the parameter, the
   docstring and the `volume_kwargs`. Fix the call at line ~313.
5. `flow/__init__.py`: remove the three exports and their `__all__` entries.
6. `agent_runner.py`: delete `SANDBOX_WORKSPACE_UID_GID` and its comment.
7. Tests:
   - `tests/test_review_dispatch.py`: delete the recording docker runner fixture and the session, volume, persist and
     failing-create tests (around lines 290-560). Keep the sandboxed-ensemble test, minus its volume assertion.
   - DELETE `tests/test_review_workspace_integration.py` entirely. It only exercises the volume plus chown path.
   - Leave `tests/hermetic_fixtures.py`'s seeded-remote volume alone. It is test infrastructure for remote fixtures, not
     Stage 4 machinery. Note this in the execution report.
8. Test (f), added to `tests/test_review_dispatch.py`. Run `run_review_loop` through the real `dispatch_role_agent`,
   with `cli.run_docker_container` and `subprocess.run` monkeypatched to recorders. Assert:
   - No recorded argv contains `volume` as a docker subcommand.
   - No `--user root` or `--entrypoint chown` container is launched.
   - No dispatch kwarg contains `review_workspace_volume` or `workspace_mount` (the dormancy guardrail).
   - `sandbox_executor.flow` no longer exposes `ReviewWorkspaceSession`.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` §3 (test deletions declared here explicitly); intent point 5 (delete, don't
  deprecate)
- **Potential failure modes for this step:**
  - Hidden callers of `run_review_loop(workspace_session=...)` in `cli/commands/review.py` or `flow/stages/review.py`.
  - The `inspect.bind_partial` removal changes the argument-forwarding behaviour.
  - Move-proof snapshot mismatches.
- **Guardrails and early‑abort checks:**
  - Before editing, run `grep -rn "workspace_session\|run_review_loop(" src tests`.
  - After the rename, run
    `uv run pytest apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_review_dispatch.py apps/sandbox-executor/tests/test_review_resolver_failure.py apps/sandbox-executor/tests/test_review_ensemble.py`.

### Success & Discard Criteria

- **Success:** The listed review tests are green. A grep of `src/`, `tests/`, `docs/` and the README for the deleted
  symbols returns nothing.
- **Discard:** Removing the wrapper breaks more than about 10 review-loop tests through signature binding. In that case,
  keep a thin `run_review_loop` that forwards verbatim, without the session, and continue.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 3.0   |
| impact_pred         | 30.0  |
| cost_pred           | 4.5   |
| learning_value_pred | 1.5   |
| ev_pred             | 20.10 |

### Step Metrics Rationale

`EV = 0.85 * 30.0 + 0.5 * 1.5 - 0.3 * 3.0 - 4.5 = 25.50 + 0.75 - 0.90 - 4.5 = 20.85`, discounted to 20.10 for the
snapshot tail. This is the bottleneck step because it is the largest cross-module change.

---

## Step 5: Update docs, lower flow budgets, refresh the move-proof snapshot

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical follow-through that is enforced by `uv run task lint` and `lint-docs`.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Leave no stale reference to the volume machinery. Shrunk modules get tighter budgets.
- **Git branch:** current plan/execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `apps/sandbox-executor/README.md`, around lines 200-216: replace the persistent-volume paragraph. State that role
   dispatch can bind-mount a host-provisioned worktree plus its git common directory (`worktree_mount.py`). State that
   Stage 4 currently clones per pass until the container-side purge lands. Remove `HOLON_REVIEW_PERSIST` and the prune
   command.
2. `docs/git_flow.md`, around lines 366-370: the same edit. Do not mention removed env vars except to say there is no
   persistence switch any more.
3. `flow_module_budgets.json`: set `review/workspace.py`, `review/loop.py`, `review/ensemble.py` and
   `review/resolver.py` to their new `wc -l` sizes. Leave `dispatch.py` at or below 99. Never raise a budget.
   `worktree_mount.py` is outside `flow/`, so it gets no entry.
4. `tests/flow/fixtures/flow_py_definitions.json`:
   - Remove the entries for deleted definitions (`ReviewWorkspaceSession*`, `review_session_volume_name`,
     `review_persist_enabled`, `<const>:REVIEW_SESSION_VOLUME_LABEL` and similar).
   - Update the digests of changed definitions, following the procedure found in Step 1. If the move-proof test only
     checks that entries still exist, removal is enough.
5. Run `uv run task lint` and `uv run task lint-docs`. Fix any prettier or markdown findings.

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** Flow budget rule ("may shrink but never grow"); `constraints.md` §3 (no ledger edits)
- **Potential failure modes for this step:**
  - The move-proof test asserts that every snapshot key exists in the package, so a deleted key fails it.
  - The docs linter rejects wording.
- **Guardrails and early‑abort checks:** Diff `flow_module_budgets.json` and confirm that every changed value decreased.

### Success & Discard Criteria

- **Success:** `uv run task lint`, `uv run task lint-docs` and `uv run pytest apps/sandbox-executor/tests/flow` are all
  green.
- **Discard:** Updating the snapshot needs regeneration semantics that conflict with its purpose (proving moves). In
  that case, remove only the deleted keys and record the decision in the execution report.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 1.0   |
| impact_pred         | 10.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 7.25  |

### Step Metrics Rationale

`EV = 0.93 * 10.0 + 0.5 * 0.5 - 0.3 * 1.0 - 2.0 = 9.30 + 0.25 - 0.30 - 2.0 = 7.25`.

---

## Step 6: Validate — affected tests, then the full suite once

- **Sub‑intent recommendation:** NO
- **Reasoning:** A standard verification gate. No timing measurement is involved, so there are no repeated runs.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the branch is green before review.
- **Git branch:** current plan/execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run the affected tests first:
   `uv run pytest apps/sandbox-executor/tests/test_worktree_mount.py apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_review_dispatch.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_review_ensemble.py apps/sandbox-executor/tests/test_review_resolver_failure.py apps/sandbox-executor/tests/flow`.
2. Run the full suite once with `uv run task test`. Report the pass/fail counts and the pytest-reported time.
3. Run `uv run task lint` and `uv run task lint-docs`. This includes `scripts/check_flow_module_budgets.py` and the
   complexity gate.
4. Run a final grep of the repository for the deleted symbols and for `docker volume create`, expecting only the
   `tests/hermetic_fixtures.py` seeded-remote hits, which are explicitly out of scope.
5. Commit with a canonical message ending in the attribution trailer.

### Dependencies & Criticality

- **Depends on:** Steps 2-5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** Test-Run Discipline; `ruleset.md` §3
- **Potential failure modes for this step:** Unrelated flaky tests; Docker-requiring tests that are skipped
  hermetically.
- **Guardrails and early‑abort checks:** Do not re-run to "fix" flakes. Report them with node IDs.

### Success & Discard Criteria

- **Success:** The full suite has no new failures compared with the Step 1 baseline. Lint and lint-docs are green.
- **Discard:** There are more than 3 new failures outside the touch set. In that case, stop and report rather than
  widening scope.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.5   |
| impact_pred         | 8.0   |
| cost_pred           | 2.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 5.16  |

### Step Metrics Rationale

`EV = 0.92 * 8.0 + 0.5 * 0.5 - 0.3 * 1.5 - 2.0 = 7.36 + 0.25 - 0.45 - 2.0 = 5.16`.

---

## Sub-intent list

None
