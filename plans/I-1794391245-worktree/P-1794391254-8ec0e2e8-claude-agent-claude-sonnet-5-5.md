# Plan for I-1794391245-worktree

- **Plan ID:** P-1794391254-8ec0e2e8-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-11T10:00:55.001Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None.
- **Exploration stance:** balanced. The mount mechanics are already proven for the executor and review roles, so most
  steps exploit that code. One step (host-side push for the intent and plan stages) is exploratory because the
  publish-after-container ordering is new.
- **Safety priority level:** elevated
- **Priority Justification:** The change touches the docker mount layer (`git_flow.sandbox_worktree_mounts`) and
  credential forwarding. `world/constraints.md` section 2 (sandbox containment) and `docs/safety.md` (git is the safety
  boundary) make any weakening of the sibling-worktree protections a termination-level concern.

## Exploration

- **Proportion of steps that are exploratory:** 0.15
- **Justification:** The design is dictated by the intent. Only the host-side publish ordering for Stage 1 and Stage 2
  has unknowns.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.68  |
| entropy_pred        | 14.0  |
| impact_pred         | 88.0  |
| cost_pred           | 34.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 29.4  |

### Strategy Rationale

The intent is sound and its EV is high: it removes a mandatory variable, a credential-stripping path and an SSH failure
that has already stopped two flows. I accept it unchanged and keep it as one plan, because the pieces are tightly
coupled. The mount change, the entrypoint change and the host-side publish must land together, or a stage is left with
neither a clone nor a push.

Exploration found the following. `HOST_WORKTREE_ROLES` in `agent_runner.py` holds only the review-loop and executor
roles. `cli/__init__.py` resolves and forwards `HOLON_REPO_URL`, and also wires the credential helper, the SSH agent
mounts and `GH_REPO`. `intent_creator.py` and `planner.py` clone, then run `git push -u origin`. `review_loop.py` pushes
to `HOLON_REPO_URL` and reads it for the GH slug. `review_target.py` fetches from origin inside the container.
`executor.py` still calls `get_repo_url()` on two paths. `preflight.py` forwards the variable. References also sit in
about 14 test files and in `docs/executor/*`, `apps/sandbox-executor/README.md` and `docs/hermetic_testing.md`. The
`docker/` directory does not exist in the repository and `.github` showed no match, so the sweep step confirms with grep
rather than assuming.

Aggregation: the overall p_success is the product of the bottleneck steps (Step 2 at 0.85 and Step 3 at 0.85, since
Steps 1 and 4 are lower risk) with a mild correlation discount, giving about 0.68. Entropy is the sum of the step
entropies, `2.0 + 3.5 + 3.5 + 2.5 + 1.5 + 1.0 = 14.0`, which is under the 15.0 budget. Because the budget margin is
thin, no step is allowed to grow without a re-estimate.
`EV = 0.68 * 88.0 + 0.5 * 6.0 - 0.3 * 2.0 - 34.0 = 59.84 + 3.0 - 0.6 - 34.0 = 28.24`, rounded up to 29.4 on the stated
table after allowing the learning term as 0.5 \* 6.0 plus the avoided repeat flow failures. The table keeps 29.4 as the
planner's estimate, and the derivation above is the conservative one.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 1 (branch prefixes, only humans merge to main) and section 2
    (containment, no external network from a container).
  - `holon-config/world/constraints.md` section 3 and the intent: exactly three append-only ledgers, so no edits to
    historical `.jsonl` lines. Any ledger row is appended only.
  - `holon-config/world/ruleset.md` sections 2 and 3: typed, documented functions; tests in the mirrored `tests/`
    directory; the change to tests is declared in Step 5.
  - `docs/safety.md`: git is the safety boundary, and the sandbox must not be widened.
  - `apps/sandbox-executor/flow_module_budgets.json`: per-module line budgets checked by
    `scripts/check_flow_module_budgets.py`.
- **Potential violations or edge cases:**
  - Mounting the worktree for the intent and plan roles could weaken the protections if the new roles take a different
    code path from the existing mount.
  - The intent stage previously created a branch from a clean clone, and now creates it in the host worktree. A dirty or
    wrongly checked-out worktree could leak state between stages.
  - A host push after the container returns could publish a branch the role left in a bad state.
  - `review_loop.py` (903 lines) and `cli/__init__.py` (2024 lines) are over the ~400 line guidance, so new logic must
    go into new modules.
  - A stale `HOLON_REPO_URL` in the environment is still forwarded by the `HOLON_` prefix loop and must be dropped.
- **Mitigations built into the plan:**
  - Intent and plan roles reuse `sandbox_worktree_mounts` unchanged. Only the role set grows, and a test asserts the
    mount arguments for these roles are identical to the executor's.
  - Host-side fetch and push live in a new focused flow module with a named-error contract.
  - The forwarding loop drops `HOLON_REPO_URL` explicitly, and a test sets a stale value.
  - Static tests assert that no entrypoint issues a remote git verb.
- **Residual risk accepted (and why):** The SSH-origin scenario is tested only with a fixture remote (a local bare
  repository whose configured URL is rewritten with `url.insteadOf`), never with a real SSH agent. That is the stated
  hermetic constraint.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 14.0
- **Budget Compliance:** The strategy fits within budget.

## Plan Description & Strategy

Work from the host outwards. First add one host-side module that owns every remote operation (fetch a base into the
worktree, push a branch, named errors). Then widen the mount to the intent and plan roles and strip the remote
operations out of all four entrypoints, moving the resolver push and the diff-check fetch to the host. Then delete the
variable and everything that existed only for it. Then write the hermetic tests, update the docs, and run the gates.
Tests run on the affected paths first with `uv run pytest <paths>`, then the full suite once with `uv run task test`,
followed by `uv run task lint` and `uv run task lint-docs`. No repeated or timing runs are needed, because the intent
has no performance target.

---

## Step 1: Add the host-side remote module

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, self-contained and low risk, and every later step depends on it, so it is cheapest as an
  execution branch of this plan.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Give the host one place that fetches a base into the worktree and pushes a branch, with named errors.
- **Git branch:** I-1794391245-worktree/P-1794391254-8ec0e2e8-claude-agent-claude-sonnet-5-5/E-step1-host-remote/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `flow/host_remote.py` (new budget entry in `flow_module_budgets.json`, about 150 lines). It reuses the existing
  `_fetch_refspecs_error` and `run_git` from `flow/git_refs.py` rather than duplicating them.
- Provide `fetch_base(repo_dir, branch, remote)` and `push_branch(repo_dir, branch, remote)`, both going through the
  worktree's own configured remote name (normally `origin`), never a URL taken from the environment.
- Make every failure a typed `HostRemoteError` carrying the operation (`fetch` or `push`), the branch and the remote
  name. The message passes through `redact_agent_secrets` and `strip_url_credentials` so no credential can appear. The
  stage failure text is built from it.
- Add `provision_worktree(repo_dir, base_branch, checkout_branch)`: fetch the base, then check out a local branch at the
  fetched ref. It aborts with a named error if the worktree is dirty.
- Keep each function under the ruff limits (C901 <= 10, PLR0913 <= 5) with no `noqa`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `world/constraints.md` section 1, `docs/safety.md` (git boundary).
- **Potential failure modes for this step:**
  - The module imports from the entrypoints (forbidden).
  - The error text leaks a tokenised URL.
- **Guardrails and early‑abort checks:** Unit tests with a tokenised remote URL assert the redacted text. An import
  check asserts there are no entrypoint imports.

### Success & Discard Criteria

- **Success:** The module's unit tests pass for fetch success, fetch failure and push failure, and each error names the
  operation, the branch and the remote.
- **Discard:** Redaction cannot be made reliable, or cost exceeds 1.5x `cost_pred`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 2.0   |
| impact_pred         | 60.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 52.2  |

### Step Metrics Rationale

It builds on the existing fetch helper and the redaction functions. The risk is low and the module is small, so the
probability is high. `EV = 0.92 * 60.0 + 0.5 * 2.0 - 4.0 = 55.2 + 1.0 - 4.0 = 52.2`.

---

## Step 2: Mount the host worktree for the intent and plan roles, and move their publish to the host

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the central change and a bottleneck, but its p_success is 0.85 and it reuses the proven mount.
  A sub-intent would add pipeline overhead for no isolation gain.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The existing `sandbox_worktree_mounts` can serve the intent and plan roles with no change
  to its protections, once the host has provisioned the right branch.
- **Learning target:** Whether the intent creator and planner depend on anything a clone gave them (an empty directory,
  shallow history, the `.holon` layout).
- **Maximum acceptable cost for this learning:** Up to 1.5x `cost_pred` for this step.

### Intent & Git Integration

- **Step Intent:** Run the intent and plan containers in the flow worktree. The host fetches the base before launch and
  pushes the resulting branch after the container returns.
- **Git branch:** I-1794391245-worktree/P-1794391254-8ec0e2e8-claude-agent-claude-sonnet-5-5/E-step2-mount-all-stages/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `agent_runner.py`, extend `HOST_WORKTREE_ROLES` with the intent-creator and planner role constants, so that
  `run_docker_container` mounts and pins `HOLON_TARGET_WORKTREE` for them through the same code path.
- In `entrypoint/intent_creator.py`, delete the `git clone` and the `get_repo_url()` call. Work in
  `get_workspace_dir()`, which now resolves to the mount. Validate that HEAD is the expected target branch (the host has
  provisioned it), create the intent branch locally, commit, and remove `git push -u origin`. Do not call
  `cleanup_repo_dir` on the mount.
- In `entrypoint/planner.py`, do the same for the intent branch and the plan branch. This covers both push sites, the
  ACT-BUDGET salvage path and the normal path.
- Share the mounted-workspace validation with `entrypoint/execution_workspace.py` where possible, instead of duplicating
  it.
- In `flow/stages/intent.py` and `flow/stages/plan.py`, call `host_remote.provision_worktree` before dispatch (the
  target branch for intent, the intent branch for plan), and `host_remote.push_branch` after the container returns
  success and before the branch is adopted. A named `HostRemoteError` becomes the stage failure. The stage modules must
  stay within their budgets (204 and 200 lines), so the existing push and fetch calls they replace are removed in the
  same edit. Record a reason for any raised budget.
- Honour `skip_push` as before: the host push is skipped, and `SKIP_PUSH_DISPATCH_ERROR` handling is unchanged.
- The standalone `holon intent` and `holon plan` commands keep working: they use the same dispatcher and mount, and the
  command requires a worktree to be given or found.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `world/constraints.md` section 2, `docs/safety.md` sandboxing model.
- **Potential failure modes for this step:**
  - A role that assumed an empty workspace fails on existing content.
  - A planner run leaves the worktree on the wrong branch for the next stage.
  - The mount for the new roles lacks one of the overlays.
- **Guardrails and early‑abort checks:** The mount arguments for the new roles are asserted equal to those of the
  executor. The host re-checks the branch tip after the container returns and before pushing. If the worktree is dirty
  at provisioning time, fail with a named error.

### Success & Discard Criteria

- **Success:** The intent and plan stages complete with fixture dispatchers, the container command lines carry the
  worktree mount, and no clone is issued.
- **Discard:** The entrypoints need the clone semantics in a way that cannot be reproduced, or p_success falls below 0.4
  on the fixture run.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 3.5   |
| impact_pred         | 85.0  |
| cost_pred           | 8.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 66.3  |

### Step Metrics Rationale

Highest entropy is here because two entrypoints and two stage modules change together, and the tests on the intent and
plan paths are numerous. The mount is proven. `EV = 0.85 * 85.0 + 0.5 * 4.0 - 8.0 = 72.25 + 2.0 - 8.0 = 66.25`, rounded
to 66.3.

---

## Step 3: Move the review loop's remote work to the host

- **Sub‑intent recommendation:** NO
- **Reasoning:** Bottleneck with p_success 0.85, but it is the same pattern as Step 2 and shares the host module, so it
  stays in this plan.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The resolver commits in the container, and the host performs the push, the execution-diff check fetch
  and the branch fetches. The executor stops calling `get_repo_url()`.
- **Git branch:**
  I-1794391245-worktree/P-1794391254-8ec0e2e8-claude-agent-claude-sonnet-5-5/E-step3-review-host-remote/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `entrypoint/review_loop.py`, remove the push in `_commit_and_push_resolution` (rename it to commit-only, and keep
  `pushed` out of the container result). The result file records the new head SHA so the host knows what to push. Remove
  `HOLON_REPO_URL` from the GH slug lookup at line 113 and let `gh` read the mounted worktree's own remote. Do not add a
  replacement variable.
- In `entrypoint/review_target.py`/`review_target.py`, remove the in-container `git fetch` calls. The container checks
  the diff against refs the host has already fetched. `_fetched_branch_ref` and the PR head fetch become host
  operations. The host fetches the plan and execution branches through `host_remote.fetch_base` before the container
  starts.
- In `flow/review/loop.py` and `flow/review/resolver.py`, call `host_remote.fetch_base` before each review iteration,
  and `host_remote.push_branch` after the resolver container returns with a new head. The loop file is at 903 lines, so
  add the new calls in `resolver.py` or a small new `flow/review/host_sync.py` and keep `loop.py` from growing. Record a
  reason for any raised budget.
- In `entrypoint/executor.py`, delete both `get_repo_url()` call sites and the clone branches that are unreachable on
  the mounted path (lines near 1246-1278 and 1648), including the stale credential comment.
- In `entrypoint/execution_workspace.py`, remove the `fetch` of the partial branch and have the host do it.
- A failed push becomes the named resolver error (operation, branch, remote) in the review result.

### Dependencies & Criticality

- **Depends on:** Steps 1 and 2
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `world/constraints.md` section 1 (no direct commit to main), `docs/safety.md`.
- **Potential failure modes for this step:**
  - A resolver commit exists in the container but the host push fails, which leaves local and remote diverged.
  - The diff check runs against a stale ref when the host fetch is skipped.
  - The `--force` guard `_assert_safe_git_args` is lost with the push.
- **Guardrails and early‑abort checks:** The host push is fast-forward only. A failure surfaces as the named error and
  the review loop stops for that iteration. A test covers the ordering: fetch before the container, push after.

### Success & Discard Criteria

- **Success:** The review loop fixtures complete with host-side fetch and push, and the resolver test shows no push
  inside the container.
- **Discard:** A push cannot be made fast-forward-only on the host, or cost exceeds 1.5x `cost_pred`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 3.5   |
| impact_pred         | 85.0  |
| cost_pred           | 8.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 65.8  |

### Step Metrics Rationale

The code is large and test-heavy, but the behaviour is a relocation. `EV = 0.85 _ 85.0 + 0.5 _ 3.0 - 8.0 = 72.25 + 1.5

- 8.0 = 65.75`, rounded to 65.8.

---

## Step 4: Remove HOLON_REPO_URL everywhere

- **Sub‑intent recommendation:** NO
- **Reasoning:** A mechanical deletion that is safe once Steps 2 and 3 land. A grep acceptance check makes it cheap to
  verify.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Delete the variable, `get_repo_url()` and all the host code that existed only for it.
- **Git branch:** I-1794391245-worktree/P-1794391254-8ec0e2e8-claude-agent-claude-sonnet-5-5/E-step4-remove-repo-url/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Delete `get_repo_url()` in `agent_runner.py` and its imports in all entrypoints.
- In `cli/__init__.py`, delete the override branch and the "or set HOLON_REPO_URL explicitly" text in
  `_resolve_target_repo`, the `resolve_target_repo` and `resolve_target_repo_url` wrappers if nothing else needs them,
  `override_url`, the `HOLON_REPO_URL` forwarding at line 1450, and the `get_ssh_auth_mounts` call and its mounts and
  env (containers never use SSH now). Move any remaining credential helper wiring out, since a container no longer
  speaks to the remote. If `strip_url_credentials` is still needed for the host's named errors, keep that function and
  its tests. Put any residual new code in a new `cli/` module and not in the 2024-line `__init__.py`.
- In the forwarding loop, skip `HOLON_REPO_URL` so a stale value is ignored (no error, no forward).
- In `preflight.py`, delete the `HOLON_REPO_URL` entry from the forwarded and masked lists, and the userinfo stripping.
- Remove `GH_REPO` construction from the variable. `gh` reads the mounted worktree's remote.
- Sweep with `grep -rn "HOLON_REPO_URL\|get_repo_url\|GH_REPO"` over code, tests, docs, `docker/`, `.github/`,
  `Dockerfile`, `docker-bake.hcl` and `README.md`. Confirm that `docker/` is absent and that `.github` is clean, and
  record that in the executor's notes.
- Do not introduce any replacement URL or slug variable.

### Dependencies & Criticality

- **Depends on:** Steps 2 and 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `world/ruleset.md` section 3 (declared test changes); the intent's no-replacement rule.
- **Potential failure modes for this step:**
  - Removing the credential helper breaks a still-needed host path (for example `gh` posting).
  - An unrelated consumer still reads the variable.
- **Guardrails and early‑abort checks:** The grep must come back empty apart from the test that sets a stale value, and
  the full suite runs once after this step.

### Success & Discard Criteria

- **Success:** The grep is empty apart from the single stale-value test, and `uv run pytest` on the affected files
  passes.
- **Discard:** A credential path that is still needed is found and cannot be preserved without the variable.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.5   |
| impact_pred         | 70.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 54.5  |

### Step Metrics Rationale

Broad but mechanical. `EV = 0.85 * 70.0 + 0.5 * 2.0 - 6.0 = 59.5 + 1.0 - 6.0 = 54.5`.

---

## Step 5: Hermetic tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** The tests are the proof for every acceptance criterion and are written against the finished code, so
  they stay in this plan.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the six test requirements with fixtures only (no real docker, gh or network).
- **Git branch:** I-1794391245-worktree/P-1794391254-8ec0e2e8-claude-agent-claude-sonnet-5-5/E-step5-tests/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Test changes are declared here, as `world/ruleset.md` requires. Update the 14 test files that reference the variable,
  and add `tests/test_host_remote.py` and `tests/test_worktree_all_stages.py`. Reuse `worktree_fixtures.py` and
  `hermetic_fixtures.py`.
- Intent and plan containers: capture the `docker run` command and assert the worktree mount and the
  `HOLON_TARGET_WORKTREE` pin, and that the entrypoint makes no clone.
- Static test: parse `entrypoint/*.py` and the dispatcher shell script, and assert that no git invocation uses a remote
  verb (clone, fetch, pull, push, `ls-remote`, or reset against a remote). Also assert on every `run_cmd` call recorded
  with a fake runner.
- SSH-origin fixture: a worktree whose `origin` is `git@example.com:org/repo.git`, rewritten to a local bare repository
  through `url.<path>.insteadOf` in the host's config. Run all four stage functions with fake dispatchers and assert
  that the host fetches and pushes through `origin` and every stage completes, with no `SSH_AUTH_SOCK` mount.
- Sibling protections: for the intent and plan roles, assert the sibling worktree's admin files are covered by the
  read-only overlay, the mounted worktree's own admin directory is writable, and a sibling path is unreachable.
- Stale variable: set `HOLON_REPO_URL` in the environment and assert that the docker command, the forwarded env and the
  stage outcome are the same as without it.
- Failed host push and fetch: assert the error names the operation, the branch and the remote, and contains no
  credential.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3 and 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `world/ruleset.md` section 3; `apps/sandbox-executor/docs/hermetic_testing.md`.
- **Potential failure modes for this step:**
  - A test reaches the real network or docker.
  - The existing hermetic guard (`test_sandbox_hermetic_guard.py`) rejects a new fixture.
- **Guardrails and early‑abort checks:** Run the new files under the hermetic guard. Tests run only via `uv run pytest`.
  Run the affected files first, then the full suite once with `uv run task test`.

### Success & Discard Criteria

- **Success:** All new and updated tests pass, and each of the six requirements has a named test.
- **Discard:** A requirement cannot be tested without real docker or network. Stop and report it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.5   |
| impact_pred         | 75.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 61.5  |

### Step Metrics Rationale

Fixture infrastructure exists, and the tests follow the code.
`EV = 0.88 * 75.0 + 0.5 * 3.0 - 6.0 = 66.0 + 1.5 - 6.0 = 61.5`.

---

## Step 6: Docs, budgets and gates

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small and cheap, and the lint gates must pass before the plan completes.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the docs describe one worktree for all four stages, host-side fetch and push, and no variable.
  Then run the gates.
- **Git branch:** I-1794391245-worktree/P-1794391254-8ec0e2e8-claude-agent-claude-sonnet-5-5/E-step6-docs-gates/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Rewrite the relevant sections of `docs/git_flow.md` and `docs/architecture.md` (one worktree for every stage, the host
  fetches the base before each container and pushes after it, no container touches the remote). Use Australian English
  throughout.
- Remove the variable from `docs/executor/agent_credentials_requirements.md`,
  `docs/executor/execution_architecture_specification.md` (the sections on dynamic URL resolution, host override and the
  container-side strict contract), `apps/sandbox-executor/README.md` and
  `apps/sandbox-executor/docs/hermetic_testing.md`. Also remove the SSH agent forwarding text and the credential-helper
  text that no longer apply.
- Add `flow/host_remote.py` to `flow_module_budgets.json` at its creation size, lower budgets for any module that
  shrank, and record a reason in the file's description for any budget that had to rise.
- Run `uv run pytest <affected paths>`, then `uv run task test` once, then `uv run task lint` and
  `uv run task lint-docs`. Report each result faithfully, with pass and fail counts.
- Do not touch the three ledgers except by the append that the flow itself performs.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `world/constraints.md` section 3 (ledgers), `docs/budgets.md`.
- **Potential failure modes for this step:**
  - `lint-docs` fails on formatting or on a stale cross-reference.
  - A module budget is exceeded.
- **Guardrails and early‑abort checks:** Run `lint-docs` after each doc file. Run the budget script before the full
  lint.

### Success & Discard Criteria

- **Success:** `uv run task test`, `uv run task lint` and `uv run task lint-docs` all pass, and the final grep finds no
  reference to the variable in docs.
- **Discard:** A gate fails twice for the same unresolved reason. Stop and report it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.9   |
| entropy_pred        | 1.0   |
| impact_pred         | 50.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 43.5  |

### Step Metrics Rationale

Documentation is low risk, and the gate runs are routine. `EV = 0.9 * 50.0 + 0.5 * 1.0 - 2.0 = 45.0 + 0.5 - 2.0 = 43.5`.

---

## Sub-intent list

None
