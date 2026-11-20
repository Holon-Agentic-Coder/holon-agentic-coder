# Plan for I-1794452407-nofetch2

- **Plan ID:** P-1794452423-f3b616c7-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-12T03:00:24.372Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A
- **Exploration stance:** conservative. The fix mirrors an existing, merged pattern (`_fetched_branch_ref` in
  `review_target.py`), so the only open question is the audit's completeness.
- **Safety priority level:** standard
- **Priority Justification:** The change only removes a network call in a container and touches no ledger, secret or
  merge path. It stays within the `world/` rules (host owns network; merging stays human).

## Exploration

- **Proportion of steps that are exploratory:** 0.15
- **Justification:** One audit step (Step 2) is partly exploratory: it proves which fetch callers are reachable from the
  review-loop container. Everything else reuses a known pattern.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 5.5   |
| impact_pred         | 70.0  |
| cost_pred           | 10.0  |
| learning_value_pred | 5.0   |
| ev_pred             | 54.0  |

### Strategy Rationale

The intent is small and well-bounded: one fetch site (`_execution_effective_diff`, `flow/git_refs.py` lines ~335-373)
needs the same mounted-workspace rule that `review_target._fetched_branch_ref` already applies. I found these facts
while reading the code:

- `workspace_mounted()` in `entrypoint/execution_workspace.py` is the one existing check. `review_target.py` and
  `flow/review/loop.py` already import it, so reusing it adds no new import direction and no second check.
- `_fetch_refspecs_error` callers: `_execution_effective_diff` (review path, the bug),
  `review_target._fetched_branch_ref` (already guarded), `flow/stages/children.py:198` (host, Stage 3).
- `_fetch_branch_prefix_error` / `_fetch_branch_prefix` callers: `flow/child_progress.py:110`,
  `flow/stages/plan.py:132`, `flow/stages/execute.py:218`, `_snapshot_child_branches` in `git_refs.py`. These are host
  stage code. Step 2 verifies none is reachable from `entrypoint/review_loop.py` or `flow/review/loop.py` in a mounted
  container.
- Raw `git fetch` in `review_target._fetch_pr_head_sha`-style code (line ~219) is already guarded by
  `not workspace_mounted()`.

Overall metrics are a qualitative judgement driven by the bottleneck, Step 1 (the code change, p about 0.95), and the
audit-proof test in Step 4 (p about 0.88), which could uncover an extra caller. Total entropy is the sum of the step
entropies, `0.8 + 1.5 + 1.0 + 1.8 + 0.4 = 5.5`, well inside the budget of 15.0, so no sub-intents are needed. EV:
`EV = 0.88 * 70.0 + 0.5 * 5.0 - 0.3 * 1.0 - 10.0 = 61.6 + 2.5 - 0.3 - 10.0 = 53.8`, rounded to 54.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:** `world/ruleset.md` and `world/constraints.md` (the host owns
  remote operations; append-only ledgers; merging stays with the human maintainer); `docs/safety.md` (no secrets in
  output, redaction kept); `scripts/check_flow_module_budgets.py` with `flow_module_budgets.json` (per-module line
  budgets run by `uv run task lint`).
- **Potential violations or edge cases:** `git_refs.py` is at its budget of 373 lines, so any growth needs a recorded
  budget raise; a mounted container with no `origin` remote must keep the local-branch fallback; a circular import if
  `git_refs` imports from `entrypoint`.
- **Mitigations built into the plan:** keep the new code tiny (a helper returning the missing-ref message) and record a
  reason for any budget raise in the `description` of `flow_module_budgets.json`; verify the import with
  `uv run python -c "import sandbox_executor.flow"`; test the no-origin mounted case explicitly.
- **Residual risk accepted (and why):** the audit is static plus one entry-path test, so a fetch reached only through a
  rarely used branch could survive. The entry-path test patches `run_git` to fail on any `fetch`, which catches every
  path the test drives; accepted given the cost of a full end-to-end container run.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 5.5
- **Budget Compliance:** The strategy fits within budget.

## Plan Description & Strategy

Five steps on one execution branch: (1) apply the mounted rule to `_execution_effective_diff`; (2) audit the other fetch
callers and guard any that the review path reaches; (3) write the unit tests; (4) write the review-loop entry-path test
that fails on any `git fetch`; (5) update documentation and budgets, then run the suite. No sub-intents: the work is
small, tightly coupled, and one reviewer can hold it in mind.

Test-run discipline: run the affected test files first with `uv run pytest <paths>`, then the full suite once with
`uv run task test`. No repeated runs are needed because the intent has no timing or flakiness target.

---

## Step 1: Skip the fetch in `_execution_effective_diff` when the workspace is mounted

- **Sub‑intent recommendation:** NO
- **Reasoning:** A small change that mirrors an existing pattern; low risk and little reuse beyond this branch.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the execution-diff check use the host-fetched `refs/remotes/origin/` refs in a mounted role
  container, with no `git fetch` and no ssh.
- **Git branch:** I-1794452407-nofetch2/E-nofetch2-diff
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `flow/git_refs.py` import `workspace_mounted` from `sandbox_executor.entrypoint.execution_workspace` (the same
  import `review_target.py` uses). Confirm there is no import cycle.
- In `_execution_effective_diff`, inside the `_has_origin_remote` branch, run the fetch only when
  `not workspace_mounted()`. Keep the host path byte-for-byte as it is.
- In the mounted case, check that both `refs/remotes/origin/<plan>` and `refs/remotes/origin/<execution>` resolve (a
  `rev-parse --verify --quiet` through `run_git`, or reuse `_origin_has_branch` if it fits). When one is missing, return
  `(None, "<role> '<branch>' is not fetched from origin; the host must fetch it before dispatch")`, matching the wording
  `review_target._fetched_branch_ref` already uses, and name the missing branch (the plan branch first).
- Keep the compare and the `source` text unchanged so the detail still says "origin".
- To limit line growth, extract the missing-ref check into a small helper, and keep the function inside the ruff
  complexity limits without `# noqa`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `world/constraints.md` (host owns network operations); `scripts/check_flow_module_budgets.py`.
- **Potential failure modes for this step:**
  - Import cycle between `git_refs` and `entrypoint.execution_workspace`.
  - Line budget of 373 exceeded.
  - Mounted with no origin remote wrongly reported unverifiable.
- **Guardrails and early‑abort checks:** import smoke check; `uv run task lint` after the edit; the no-origin branch is
  untouched.

### Success & Discard Criteria

- **Success:** mounted path runs no fetch and compares pre-fetched refs; a missing ref gives the naming message.
- **Discard:** an import cycle that needs a structural move, in which case fall back to a local import inside the
  function like `review_target._fetched_branch_ref` does.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.8   |
| impact_pred         | 65.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 60.0  |

### Step Metrics Rationale

The pattern already exists and is tested in the same package. `EV = 0.95 * 65.0 + 0.5 * 1.0 - 0.3 * 0.8 - 2.0 = 60.0`.
It is the bottleneck because the intent's bug is fixed here.

---

## Step 2: Audit every other fetch caller reachable from the review-loop container

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mostly read-only tracing, with at most a few guards; splitting would cost more than it saves.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** After Step 1, no `git fetch` is reachable from `entrypoint/review_loop.py` in a mounted
  workspace; the remaining `_fetch_branch_prefix*` and `_fetch_refspecs_error` callers are host-stage code only.
- **Learning target:** A definitive list of fetch sites and, for each, whether the review path reaches it.
- **Maximum acceptable cost for this learning:** about 15 percent of the plan's total cost.

### Intent & Git Integration

- **Step Intent:** Prove, then enforce, that no fetch against origin remains on the review path.
- **Git branch:** I-1794452407-nofetch2/E-nofetch2-audit
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Grep for `"fetch"`, `_fetch_refspecs_error`, `_fetch_branch_prefix`, `_fetch_branch_prefix_error`,
  `_snapshot_child_branches` and `_resolve_review_commit` across `src/sandbox_executor`, including `github/`, `review/`
  (`resolver.py`, `ci_*`, `adjudication.py`, `ensemble.py`) and `entrypoint/review_loop.py`.
- Trace the call graph from `entrypoint/review_loop.py` and `flow/review/loop.py` entry points. Known candidates: the
  `_resolve_review_commit` call at `review/loop.py:655` (already guarded), `classify_review_diff` (via Step 1), and
  `review_target._fetch_pr_head_sha`-style code at about line 219 (already guarded). `child_progress.py`,
  `stages/plan.py`, `stages/execute.py` and `stages/children.py` are host stages and expected to be unreachable.
- Any reachable unguarded site gets the same rule by routing it through the one `workspace_mounted()` check: either in
  the shared helper (`_fetch_refspecs_error` returns a "host must fetch" message when mounted, which also covers every
  future caller) or at the call site. Prefer a guard in `_fetch_refspecs_error` only if no host stage that runs in a
  mounted container legitimately needs it; the audit decides, and the decision is written in the docs (Step 5).
- Do not add a second mounted-workspace check; no new helper that duplicates `workspace_mounted`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `world/constraints.md`; scope limit in the intent (only fetch handling on the review path).
- **Potential failure modes for this step:**
  - Guarding a helper that a mounted authoring role (intent creator or planner) legitimately needs to fetch with.
  - Scope creep into host stage code.
- **Guardrails and early‑abort checks:** only change sites the review path reaches; leave host-stage callers alone and
  list them in the docs as out of scope.

### Success & Discard Criteria

- **Success:** a written list of every fetch site with reachability; every reachable one honours `workspace_mounted()`.
- **Discard:** if guarding the shared helper changes mounted behaviour for non-review roles, guard at the call site
  instead.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.9   |
| entropy_pred        | 1.5   |
| impact_pred         | 55.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 50.0  |

### Step Metrics Rationale

Grep results already suggest the other callers are host stages. The residual uncertainty is a hidden reachable path.
`EV = 0.9 * 55.0 + 0.5 * 5.0 - 0.3 * 1.5 - 2.0 = 49.5 + 2.5 - 0.45 - 2.0 = 49.55`, rounded to 50.

---

## Step 3: Unit tests for the execution-diff mounted and host paths

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small hermetic tests alongside existing ones.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Pin the mounted, missing-ref and host behaviours of `_execution_effective_diff`.
- **Git branch:** I-1794452407-nofetch2/E-nofetch2-tests
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Find the existing tests for `_execution_effective_diff` (likely `tests/test_review_target.py`, `test_review_loop.py`
  or `test_git.py`) and reuse their hermetic fixtures (`hermetic_fixtures.py`, `worktree_fixtures.py`,
  `review_fakes.py`). Add tests next to them; if a file would grow past about 400 lines, add a new focused module.
- Mounted, refs present: set `HOLON_TARGET_WORKTREE` to the mount point via monkeypatch, use a temporary local repo with
  an `origin` remote pointing at a local path (no network) and pre-created `refs/remotes/origin/` refs. Replace
  `run_git` (or the module's call) with a recorder that fails the test on any `fetch` and on any argv mentioning ssh.
  Assert the result and that the compare used the tracking refs.
- Mounted, plan ref missing and execution ref missing: assert `(None, message)` where the message names the branch and
  contains "host must fetch".
- Host (env unset): assert the fetch is invoked with the plan and execution refspecs and the compare follows; a failed
  fetch still gives the "fetching ... failed" result.
- Mounted without an origin remote keeps the local fallback.
- No real docker, gh, remote or model call.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/testing/testing_guide.md`; `apps/sandbox-executor/docs/hermetic_testing.md`;
  `test_sandbox_hermetic_guard.py`.
- **Potential failure modes for this step:**
  - A test accidentally reaches the network through a real `origin`.
  - Environment leak between tests.
- **Guardrails and early‑abort checks:** use `monkeypatch.setenv`, local-path remotes only, and run the hermetic guard
  test.

### Success & Discard Criteria

- **Success:** the new tests pass via `uv run pytest <files>`, and fail if Step 1 is reverted.
- **Discard:** none; fix the fixtures.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.0   |
| impact_pred         | 50.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 46.0  |

### Step Metrics Rationale

Well-trodden test style. `EV = 0.95 * 50.0 + 0.5 * 2.0 - 0.3 * 1.0 - 2.0 = 47.5 + 1.0 - 0.3 - 2.0 = 46.2`.

---

## Step 4: Review-loop entry-path test that fails on any git fetch in a mounted workspace

- **Sub‑intent recommendation:** NO
- **Reasoning:** One test, but it is the proof of the audit; keep it with the code it guards.
- **Step Type:** TEST
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Driving the review-loop entry path (an execution branch review that reaches
  `classify_review_diff`, base and head resolution and the reviewed-commit lookup) in a mounted workspace never invokes
  `git fetch`.
- **Learning target:** Whether any untested caller still fetches; if so, the test exposes it for a fix in Step 2.
- **Maximum acceptable cost for this learning:** one extra iteration of Step 2 (about 2 cost units).

### Intent & Git Integration

- **Step Intent:** Make a future fetch on the review path a test failure rather than a production refusal.
- **Git branch:** I-1794452407-nofetch2/E-nofetch2-entry
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Extend `tests/test_review_workspace_integration.py` (it already drives `entrypoint.review_loop._prepare_workspace`) or
  `test_review_loop.py`, whichever already stubs the model, gh and docker, and reuse its fakes from `review_fakes.py`.
- Build a temporary repo with an `origin` remote (local path), host-fetched tracking refs for the plan, execution and
  base branches, and set `HOLON_TARGET_WORKTREE` to the sandbox mount point.
- Wrap `sandbox_executor.git.run_git` and the `subprocess` layer under it with a guard that raises on any argv whose git
  subcommand is `fetch` (also `pull`, `ls-remote`, `clone`) and on any ssh invocation. Patch where the modules bind the
  name (`flow.git_refs`, `review_target`, `flow.review.loop`) so the guard sees every call.
- Walk the entry path from the review-loop entrypoint through target validation, `classify_review_diff`, base and head
  resolution and the reviewed-commit lookup, stopping before any model or gh call (stubbed). Assert the guard recorded
  no fetch and the diff verdict is verified.
- Add a negative control: with the env unset (host) the same walk records a fetch, proving the guard works.

### Dependencies & Criticality

- **Depends on:** Steps 1 and 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** hermetic testing rules; `test_sandbox_hermetic_guard.py`.
- **Potential failure modes for this step:**
  - Guard patched at the wrong binding, so a fetch slips through (false pass).
  - Entry path needs heavy stubbing and the test becomes brittle.
- **Guardrails and early‑abort checks:** the negative control proves the guard detects a fetch; keep the walk to the
  pre-model section of the flow.

### Success & Discard Criteria

- **Success:** the test passes with the fix, fails when the Step 1 guard is removed, and the negative control fails the
  guard on the host path as expected.
- **Discard:** if the full entry path cannot be stubbed hermetically within budget, narrow to `classify_review_diff`
  plus the base and head resolution calls and say so in the docs.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.8   |
| impact_pred         | 55.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 45.0  |

### Step Metrics Rationale

The riskiest step: heavy stubbing and the possibility of exposing another caller. High learning value because it
converts the audit into a permanent guard.
`EV = 0.85 * 55.0 + 0.5 * 6.0 - 0.3 * 1.8 - 3.0 = 46.75 + 3.0 - 0.54 - 3.0 = 46.2`, rounded down to 45 for stubbing
uncertainty.

---

## Step 5: Documentation, line budgets, lint and the full suite

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical and small.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Document the behaviour change, record any budget raise with a reason, and verify the gates.
- **Git branch:** I-1794452407-nofetch2/E-nofetch2-docs
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Find the docs that name the existing mounted no-fetch behaviour (grep `docs/` and `apps/sandbox-executor/docs/` for
  "host must fetch", "mounted", "no ssh", `_fetched_branch_ref`; likely `docs/git_flow.md`, `docs/architecture.md`,
  `docs/executor/execution_architecture_specification.md`). Add the execution-diff check and the audit result (which
  callers are host-only) in Australian English (behaviour, recognised, and so on).
- If `git_refs.py` or `review/loop.py` grew, raise its entry in `apps/sandbox-executor/flow_module_budgets.json` by the
  smallest number of lines and append the reason to the file's `description`, in the style of the existing entries.
- Run `uv run pytest` on the touched test files, then the full suite once with `uv run task test`, then
  `uv run task lint` and `uv run task lint-docs`. Fix any prettier or markdown issue.
- Touch no ledger and add no files under `holon-knowledge/ledger` beyond the executor's own append-only flow; keep
  exactly three ledgers. Do not merge; leave merging to the human maintainer.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `scripts/check_flow_module_budgets.py`; docs lint; append-only ledgers.
- **Potential failure modes for this step:**
  - Budget gate fails on an unrecorded raise.
  - Docs lint complains about formatting or spelling.
- **Guardrails and early‑abort checks:** run `uv run task lint` before the full suite so budget problems surface early.

### Success & Discard Criteria

- **Success:** `uv run task test`, `uv run task lint` and `uv run task lint-docs` all pass.
- **Discard:** none; failures are fixed in place.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.4   |
| impact_pred         | 40.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 37.0  |

### Step Metrics Rationale

Low risk, mechanical. `EV = 0.95 * 40.0 + 0.5 * 1.0 - 0.3 * 0.4 - 1.0 = 38.0 + 0.5 - 0.12 - 1.0 = 37.38`.

---

## Sub-intent list

None
