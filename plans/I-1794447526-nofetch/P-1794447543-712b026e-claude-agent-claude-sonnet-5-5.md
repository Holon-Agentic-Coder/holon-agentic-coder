# Plan for I-1794447526-nofetch

- **Plan ID:** P-1794447543-712b026e-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-12T01:39:03.901Z

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.8   |
| entropy_pred        | 5.0   |
| impact_pred         | 70.0  |
| cost_pred           | 14.0  |
| learning_value_pred | 4.0   |
| ev_pred             | 44.0  |

### Strategy Rationale

The intent is accepted as is. Reading the code showed the following.

- `review_target._fetched_branch_ref` fetches through `flow.git_refs._fetch_refspecs_error` whenever an origin remote
  exists. `_pr_head_ref` also runs a raw `git fetch origin <sha>`. Both are on the review diff path
  (`flow/review/loop.py` calls `classify_review_diff`).
- `entrypoint/execution_workspace.workspace_mounted()` already detects a mounted role container
  (`HOLON_TARGET_WORKTREE == SANDBOX_WORKSPACE_MOUNT`). Reusing it avoids a new signal.
- `git_flow.fetch_origin_branch` and `host_remote.fetch_base` already fetch on the host, and `execution_worktree` in
  `cli/commands/execute.py` is the precedent for a host pre-dispatch fetch.
- `cli/commands/review.py::_run_container_review` is the single place that dispatches the review-loop container, so it
  is the natural place for the host pre-dispatch fetch.

The work is small and tightly coupled, so it is one plan with no sub-intents.

Overall metrics are derived from the steps. Step 1 (the review path change) is the bottleneck. The overall
`p_success_pred` is about the product of the steps, `0.93 * 0.88 * 0.9 * 0.95 = 0.70`, nudged up because the steps are
independent and low risk. `entropy_pred` is the sum of the step entropies (5.0). `cost_pred` is the sum of the step
costs. `ev_pred` is `0.8 * 70.0 + 0.5 * 4.0 - 0.3 * 1.0 - 14.0 = 56.0 + 2.0 - 0.3 - 14.0 = 43.7`, rounded to 44.0 in the
table.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:** `holon-config/world/ruleset.md` and `constraints.md` (three
  append-only ledgers, no merge to `main` by agents, target-native paths), `docs/safety.md` (role containers never reach
  the remote; the host owns network operations), and `flow_module_budgets.json` (per-module line budgets).
- **Potential violations or edge cases:**
  - `git_refs.py` is at its 373-line budget.
  - A PR head SHA that is absent locally in the container.
  - Repositories with no origin remote (local-only tests).
  - A PR target whose head or base branch differs from the plan, execution and base branches that the host fetches.
- **Mitigations built into the plan:**
  - Keep the new code out of `git_refs.py` and out of budgeted modules where possible.
  - Fail closed: a missing ref gives UNVERIFIED with a clear refusal.
  - The host fetch derives branches from the resolved target, so PR head and base branches are covered.
  - Hermetic tests with a fake `run_git` that fails on any `fetch`.
- **Residual risk accepted (and why):** A SHA-only PR head not present in the mounted repository is reported as
  unresolved instead of fetched. The host fetches the head branch first, which normally brings the SHA.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 5.0
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

First make the review diff path fetch-free when the workspace is mounted. Then add the host pre-dispatch fetch of the
plan, execution and base branches. Then add tests and documentation. Run the affected tests first, then the full suite
once with `uv run task test`, then `uv run task lint` and `uv run task lint-docs`. No repeated or timing runs are
needed.

---

## Step 1: Add a mounted-aware ref resolver and refusal message

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, local change in one module, low risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Give `review_target.py` one place that decides fetch or no-fetch.
- **Git branch:** I-1794447526-nofetch/E-step1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `review_target.py`, add a helper that decides if the code runs in a mounted role container. It calls
  `workspace_mounted()` from `entrypoint/execution_workspace.py`. If importing that creates an entrypoint-to-flow import
  cycle, instead read `HOLON_TARGET_WORKTREE` against `agent_runner.SANDBOX_WORKSPACE_MOUNT` locally. No new entrypoint
  imports go into the host flow engine.
- Change `_fetched_branch_ref`. When mounted, skip `_has_origin_remote` and `_fetch_refspecs_error`. Resolve with
  `_resolve_branch_ref(repo_dir, branch, prefer_remote=True)`. If an origin remote exists and no
  `refs/remotes/origin/<branch>` ref resolves, return a refusal naming the role and branch and saying the host must
  fetch it before dispatch. Without an origin remote, keep the existing local-branch fallback.
- Change `_pr_head_ref`. When mounted, never run `git fetch origin <sha>`. Return the SHA only if the commit exists
  locally.
- Host (unmounted) behaviour stays byte-for-byte as it is.
- Keep the functions within the ruff complexity limits.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` (the host owns network operations).
- **Potential failure modes for this step:**
  - Import cycle.
  - A mounted detection that is wrong in tests.
  - Refusal wording that tests cannot pin.
- **Guardrails and early‑abort checks:** Run `uv run pytest tests/test_review_target.py` after the change.

### Success & Discard Criteria

- **Success:** In the mounted case no `git fetch` is invoked and pre-fetched refs resolve.
- **Discard:** More than 1.5x cost, or the change needs edits outside the scope.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 1.5   |
| impact_pred         | 60.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 52.0  |

### Step Metrics Rationale

The change is a narrow branch in two functions with a clear precedent (`workspace_mounted`). `ev_pred` is
`0.93 * 60.0 + 0.5 * 2.0 - 4.0 = 55.8 + 1.0 - 4.0 = 52.8`, rounded to 52.0 to allow for review overhead.

---

## Step 2: Remove the remaining in-container fetch on the review path

- **Sub‑intent recommendation:** NO
- **Reasoning:** Audit step; bounded to the review diff path.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** `_fetched_branch_ref` and `_pr_head_ref` are the only fetches on the review diff path.
- **Learning target:** Find any other `git fetch` or ssh use reachable from `classify_review_diff` (for example
  `flow/git_refs.py` `_resolve_review_commit` with `fetch=True`, or `flow/review/` callers).
- **Maximum acceptable cost for this learning:** 2 cost units.

### Intent & Git Integration

- **Step Intent:** Make sure no other call reachable from the Stage 4 diff check fetches in a mounted container.
- **Git branch:** I-1794447526-nofetch/E-step2
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Trace callers of `_fetch_refspecs_error`, `_resolve_review_commit(fetch=True)` and raw `run_git(["fetch", ...])` under
  `flow/review/` and `review_target.py`.
- For each hit that runs on the review path inside the container, gate it on the same mounted check from Step 1, or pass
  `fetch=False`. Leave `flow/stages/children.py` and `flow/github/merge.py` alone (host-side, out of scope).
- If `git_refs.py` needs a change, keep it net-zero lines, or raise its budget in `flow_module_budgets.json` with a
  recorded reason in the file's description.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `flow_module_budgets.json`, scope limits in the intent.
- **Potential failure modes for this step:** Scope creep into host stages; budget overrun.
- **Guardrails and early‑abort checks:** Run `uv run task lint` (it runs the module budget check).

### Success & Discard Criteria

- **Success:** A written list of fetch sites on the path, each gated or shown to be host-only.
- **Discard:** A required change falls outside the intent scope; defer it as a follow-up intent.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.0   |
| impact_pred         | 55.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 49.0  |

### Step Metrics Rationale

Mostly reading and a small gate, but it decides whether the fix is complete, so the learning value is high.
`0.88 * 55.0 + 0.5 * 5.0 - 2.0 = 48.4 + 2.5 - 2.0 = 48.9`.

---

## Step 3: Host pre-dispatch fetch of plan, execution and base branches

- **Sub‑intent recommendation:** NO
- **Reasoning:** One call site with an existing helper and precedent.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Fetch the three branches on the host before the review-loop container starts.
- **Git branch:** I-1794447526-nofetch/E-step3
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `cli/commands/review.py`, add a small function called from `_run_container_review` before
  `cli.run_docker_container`. It takes the reviewed worktree and the target. It derives the head or execution branch (a
  PR is already resolved to its head branch in `_container_review_worktree`), the plan branch (the execution branch
  without its `/E-...` segment, as the existing guard does) and the base branch, then calls
  `git_flow.fetch_origin_branch(worktree, branch)` once per distinct branch, three in the normal case.
- A `RuntimeError` from the fetch is printed through `cli.redact_cli_error`, exits non-zero, and the container is never
  dispatched.
- Reuse the existing helper. If the review command module grows too large, put the function in a new focused module
  `cli/commands/review_fetch.py`.
- Skip the fetch for a repository without an origin remote, so local-only flows keep working.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md`; the host ssh stays on the host and no credential enters the container.
- **Potential failure modes for this step:**
  - Wrong plan-branch derivation.
  - Fetching a branch that does not exist for an intent-only target.
  - Double fetch.
- **Guardrails and early‑abort checks:** Tests assert one call with exactly the three branches; failure stops the stage
  before dispatch.

### Success & Discard Criteria

- **Success:** Fetch is called once with the three branches before dispatch, and a failure prevents dispatch.
- **Discard:** The plan branch cannot be derived reliably; then fetch only the resolvable branches and record this.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.9   |
| entropy_pred        | 1.5   |
| impact_pred         | 65.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 55.0  |

### Step Metrics Rationale

The precedent in `execute.py` lowers risk; deriving the plan branch for every target form adds some uncertainty.
`0.9 * 65.0 + 0.5 * 3.0 - 4.0 = 58.5 + 1.5 - 4.0 = 56.0`.

---

## Step 4: Hermetic tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests follow directly from Steps 1 to 3 and share fixtures.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Pin the new behaviour with tests that use no docker, gh, git remote or model.
- **Git branch:** I-1794447526-nofetch/E-step4
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `tests/test_review_target.py`:
  - Mounted case: monkeypatch `HOLON_TARGET_WORKTREE` to the mount path and make `run_git` and the fetch helper raise on
    any `fetch`. Assert refs under `refs/remotes/origin/` resolve and the verdict is VERIFIED or EMPTY.
  - Missing ref: assert UNVERIFIED with a detail naming the branch and saying the host must fetch it.
  - Host case: unset the variable and assert the fetch is still called.
  - PR head SHA: assert no `git fetch origin <sha>` when mounted.
- In `tests/test_review_dispatch.py` or `tests/test_cli.py`: patch `git_flow.fetch_origin_branch` and
  `cli.run_docker_container`. Assert one fetch per branch (the three), done before dispatch. Make the fetch raise and
  assert dispatch is not called and the error is reported.
- Use local temp repositories or fakes only, with no real ssh. Reuse the existing fixtures and `leak_assertions`.
- Run `uv run pytest tests/test_review_target.py tests/test_review_dispatch.py tests/test_cli.py` first.

### Dependencies & Criticality

- **Depends on:** Steps 1, 2, 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `test_sandbox_hermetic_guard.py`, `docs/testing/testing_guide.md`.
- **Potential failure modes for this step:** The hermetic guard rejects a real git call; env leakage between tests.
- **Guardrails and early‑abort checks:** Use `monkeypatch.setenv` and `delenv` so state is restored.

### Success & Discard Criteria

- **Success:** All new tests pass, and they fail if the gate is removed.
- **Discard:** Cost over 1.5x `cost_pred`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.5   |
| impact_pred         | 50.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 45.0  |

### Step Metrics Rationale

Mechanical test work with good fixtures. `0.95 * 50.0 + 0.5 * 2.0 - 3.0 = 47.5 + 1.0 - 3.0 = 45.5`.

---

## Step 5: Documentation and full verification

- **Sub‑intent recommendation:** NO
- **Reasoning:** Docs edit plus a single full-suite and lint run.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Document the new rule and verify the whole change.
- **Git branch:** I-1794447526-nofetch/E-step5
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Find docs that name the review diff check or host fetches (for example `docs/git_flow.md`, `docs/safety.md`,
  `docs/review_consensus.md`, `docs/architecture.md`) with `grep` and update them in Australian English. State that a
  role container never fetches, that the host fetches the plan, execution and base branches before dispatch, and what
  the refusal says when a ref is missing.
- Run `uv run pytest` on the affected tests, then `uv run task test` once, then `uv run task lint` and
  `uv run task lint-docs`. No repeated runs are needed, because no timing is being measured.
- If a flow module budget was raised, record the reason in the description of `flow_module_budgets.json`.
- Do not touch the ledgers beyond the flow's own appends, and do not merge.

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** The three append-only ledgers; merging stays with the human maintainer.
- **Potential failure modes for this step:** Prettier or markdown lint failures; a budget check failure.
- **Guardrails and early‑abort checks:** Run `uv run task lint-docs` after each docs edit.

### Success & Discard Criteria

- **Success:** The full suite, lint and lint-docs pass.
- **Discard:** A failure unrelated to this change is reported and not worked around.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.5   |
| impact_pred         | 40.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 37.0  |

### Step Metrics Rationale

Low risk and cheap. `0.95 * 40.0 + 0.5 * 1.0 - 1.0 = 38.0 + 0.5 - 1.0 = 37.5`.

---
