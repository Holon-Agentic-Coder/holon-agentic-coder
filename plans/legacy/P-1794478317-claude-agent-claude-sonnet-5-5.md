# Plan for I-1794478300-salvage

- **Plan ID:** P-1794478317-4eb07fff-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-12T10:11:57.946Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None. One design choice is made inside the intent: the salvage runs on the host,
  in Stage 3 before dispatch, not inside the container.
- **Exploration stance:** conservative. The pieces already exist (`_salvage_partial_work` in the executor,
  `failed_partial_head` and `_resumable_partial_branch` in `flow/stages/execute.py`, `_append_superseding_execution_row`
  in `flow/git_refs.py`). The plan reuses them and adds one focused module.
- **Safety priority level:** elevated
- **Priority Justification:** the change commits files the agent left behind and appends ledger rows, so it touches the
  append-only ledgers (`holon-config/world/constraints.md` section 3) and the Git Flow commit boundaries (section 1). It
  must never rewrite ledger history and must never commit to `main` or another intent's branch.

## Exploration

- **Proportion of steps that are exploratory:** 0.1
- **Justification:** only Step 1 is mildly exploratory. It checks which branch a dirty mounted worktree is on after a
  killed container, and that is the one real unknown. Everything else extends established patterns.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 6.2   |
| impact_pred         | 62.0  |
| cost_pred           | 14.0  |
| learning_value_pred | 5.0   |
| ev_pred             | 37.5  |

### Strategy Rationale

The failure is host-side. `validate_mounted_workspace` (`entrypoint/execution_workspace.py`) refuses a dirty mount, and
the container is not allowed to clean, reset or stash it. The container already salvages partial work when it fails
gracefully (`_salvage_partial_work`, which excludes the executions ledger and the notes file and records
`partial_head`). It cannot do this when it is killed, so the leftovers have to be committed by the host before the next
dispatch.

The plan adds a new module `flow/partial_salvage.py`, called from `run_execute_stage` when `resume_partial` is true, and
before `_resumable_partial_branch` is evaluated. This keeps `execute.py` (407 lines) from growing much and keeps the
`flow_module_budgets.json` change small. The module does four things:

1. Detect leftovers with `git status --porcelain`, excluding the three ledgers and the execution report or notes file.
2. Check out, or create, the failed execution branch of this plan. This is the branch the worktree is on if it is an
   `E-` branch of this plan, else the newest failed or unfinished `E-` branch.
3. Commit the leftovers with a labelled message
   (`execute: <exec_id> partial work (interrupted) for plan <plan_branch>`).
4. Append a superseding failure row carrying `partial_head` to `executions.jsonl`, so the existing
   `_resumable_partial_branch` lookup finds it.

Everything else is unchanged. The executor's `validate_mounted_workspace` still runs and now sees a clean tree. The
executor continues from `partial_head`, re-runs its checks, and the review loop reviews the final diff against the plan
tip. A clean resume returns immediately, so its behaviour is identical.

Metrics are derived from the bottleneck step (Step 2, the salvage module, `p=0.85`) multiplied by the integration step
(Step 3, `p=0.95`) and the test step (Step 4, `p=0.95`). That gives about 0.77 before allowing for Steps 1 and 5 being
low risk, so the plan is rated 0.82 on qualitative judgement. Entropy is the sum of the step entropies
(`0.8 + 2.4 + 1.2 + 1.2 + 0.6 = 6.2`), well under the 15.0 budget. No sub-intents are needed. Cost is the sum of the
step costs, discounted for shared context. EV follows `docs/metrics.md`:
`EV = 0.82 * 62.0 + 0.5 * 5.0 - 0.3 * 6.2 - 14.0 = 50.84 + 2.50 - 1.86 - 14.0 = 37.48`, recorded as 37.5.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 1: commits stay on the plan's own execution branch, never `main`.
  - `holon-config/world/constraints.md` section 3: ledgers are append-only. Salvage only appends a row.
  - `holon-config/world/ruleset.md` section 2 and 3: strict typing, docstrings, `uv run pytest`. Test files change only
    where this plan declares it (Step 4).
  - `docs/safety.md`: no secrets may be committed, and no network access happens in tests (hermetic fixtures).
  - `flow_module_budgets.json`: flow modules may not grow past their budgets, so new logic goes in a new module.
- **Potential violations or edge cases:**
  - Leftover files may contain secrets or credentials (for example a `.env`). Mitigation: honour `.gitignore`, and pass
    the commit message and any warning through `redact_agent_secrets`.
  - Several failed execution branches exist, or none exists locally (container killed before the first commit).
  - The worktree is on the plan branch or `main` while dirty, so committing there would break the commit boundaries.
  - Leftover edits conflict with a checkout of another branch.
  - Ledger files are dirty and carry engine rows that must not be committed as partial work.
  - A second interruption while salvaging produces a second partial-work commit on the same branch.
- **Mitigations built into the plan:**
  - The branch guard allows commits only on `<plan_prefix>/E-*/_` branches. Anything else is a clear Stage 3 failure
    that names the branch.
  - Ledger and report paths are excluded with `:(exclude)` pathspecs, using target-native paths. If only those are
    dirty, nothing is committed and the existing behaviour continues, including the existing refusal text if validation
    still fails.
  - The ledger row is appended with the existing `_append_superseding_execution_row` helper under `LedgerLock`, never by
    rewriting, and `assert_ledger_prefix` stays in force.
  - No `reset`, `clean`, `stash` or amend is ever used.
- **Residual risk accepted (and why):** a half-written file may be committed as partial work. This is the intent's
  explicit goal. The executor re-runs its checks, and the review loop sees the final diff, so the work is vetted before
  the human merges.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 6.2
- **Budget Compliance:** The strategy fits within budget.

## Plan Description & Strategy

Add a host-side salvage step to Stage 3 resume that commits an interrupted run's uncommitted edits onto the failed
execution branch as a labelled partial-work commit and records it in `executions.jsonl`. Reuse the existing
`partial_head` mechanism, so the executor continues from the leftovers unchanged. Cover it with hermetic tests, and
document the behaviour in `docs/`. Test discipline: run the affected tests first with `uv run pytest <paths>`, then the
whole suite once with `uv run task test`. No repeated or timing runs are needed, because the intent has no performance
target.

---

## Step 1: Confirm the interrupted-run worktree state and the reusable helpers

- **Sub‑intent recommendation:** NO
- **Reasoning:** read-only and cheap. It fixes the one unknown (which branch the dirty worktree is on) before any code
  is written.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** after a killed container the mounted worktree is still on the local `E-...` branch
  created by `checkout -b` (with or without commits), and its ledger may hold no failure row yet.
- **Learning target:** the exact branch and ledger state in the three seen scenarios (dirty tracked, dirty untracked,
  engine files only), and the signatures of `_append_superseding_execution_row`, `_snapshot_child_branches`,
  `publish_new_child_branches` and `push_branch`.
- **Maximum acceptable cost for this learning:** under 10 percent of plan cost.

### Intent & Git Integration

- **Step Intent:** settle the branch-selection rule and the files to exclude.
- **Git branch:** I-1794478300-salvage/P-1794478317-4eb07fff-claude-agent-claude-sonnet-5-5/E-salvage-survey/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Read `flow/stages/execute.py`, `flow/git_refs.py`, `flow/host_remote.py`, `entrypoint/execution_workspace.py`, and the
  executor's use of `EXECUTIONS_LEDGER_REL` and the notes path (`notes_rel`).
- List the engine-owned paths: `holon-knowledge/ledger/{intents,plans,executions}.jsonl` and the execution report or
  notes file, all built with `os.path.join` so they stay target-native.
- Decide the branch rule: (a) if HEAD is on an `E-` branch of this plan, use it; (b) else the newest `E-` branch from
  the snapshot; (c) else no salvage, and the clear refusal stays.
- Check `tests/hermetic_fixtures.py` and `tests/worktree_fixtures.py` for reusable repo builders.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** safety.md (no network in tests), constraints.md section 1.
- **Potential failure modes for this step:** wrong assumption about the dirty branch.
- **Guardrails and early‑abort checks:** if the dirty worktree can be on the plan branch, add the branch guard in
  Step 2.

### Success & Discard Criteria

- **Success:** a written branch rule and exclusion list that Step 2 implements.
- **Discard:** none expected; stop if more than 1.5x cost_pred.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.8   |
| impact_pred         | 10.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 4.0   |
| ev_pred             | 9.7   |

### Step Metrics Rationale

`EV = 0.97 * 10.0 + 0.5 * 4.0 - 0.3 * 0.8 - 1.5 = 9.70 + 2.00 - 0.24 - 1.5 = 9.96`, shown as 9.7 after a small
conservatism haircut. Low risk, and it removes the main design unknown.

---

## Step 2: Add the partial-work salvage module `flow/partial_salvage.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** one focused module with a small API. It is the bottleneck, but it is bounded (about 150 lines) and its
  risk is contained by the tests in Step 4.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** commit interrupted edits as labelled partial work and record them in the ledger.
- **Git branch:** I-1794478300-salvage/P-1794478317-4eb07fff-claude-agent-claude-sonnet-5-5/E-salvage-module/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- New module `apps/sandbox-executor/src/sandbox_executor/flow/partial_salvage.py` with a module docstring and typed,
  documented functions. It imports nothing from `entrypoint` except existing constants, which are already imported by
  other flow stages (no new entrypoint-to-flow imports are added).
- `leftover_paths(repo_dir, exclude)`: run `git status --porcelain -z --untracked-files=all` with `:(exclude)` pathspecs
  for the three ledgers and the report or notes file, and return the list of paths (tracked edits, deletions and
  untracked files not ignored by `.gitignore`).
- `salvage_leftovers(repo_dir, plan_branch, plan_prefix, existing_executions)`: returns a small result object (branch,
  sha or None, files). Steps: if there are no leftovers, return an empty result without touching git. Otherwise choose
  the branch by the Step 1 rule, apply the branch guard (`<plan_prefix>/E-*/_` only), switch to that branch if needed
  (git carries the leftovers across; on a conflict, fail with a clear message and change nothing), `git add` the
  non-excluded paths, and commit with the message `execute: <exec_id> partial work (interrupted) for plan <plan_branch>`
  using the executor identity.
- `record_salvage(repo_dir, branch, exec_id, sha, files)`: use `_append_superseding_execution_row` to append a `failure`
  summary row to `executions.jsonl` with `partial_head=sha`, a `salvaged` marker, and a file count (not contents), then
  commit that row separately as a ledger-only commit. This makes `failed_partial_head` find it. Nothing is rewritten,
  and engine-owned rows stay out of the partial-work commit.
- Publish the branch through the existing `push_branch` or `publish_new_child_branches` helper, wrapped in
  `remote_error`, so the failed branch reaches origin as it does today.
- Pass every message and warning through `redact_agent_secrets`. Use Australian English in messages and docs.
- Keep functions under the ruff limits (C901 <= 10, PLR0913 <= 5) with no `# noqa`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (p_success_pred 0.85 is at or above 0.6, so a sub-intent is not required)

### Safety & Constraint Considerations

- **Relevant rules:** constraints.md sections 1 and 3, safety.md (secrets and ledgers).
- **Potential failure modes for this step:** committing on the wrong branch; checkout refused because of overlapping
  edits; ledger row appended without a matching commit; secrets in leftovers.
- **Guardrails and early‑abort checks:** the branch guard; never use `reset`, `clean`, `stash` or amend; verify with
  `assert_ledger_prefix`; on any error return a Stage 3 failure and leave the worktree untouched.

### Success & Discard Criteria

- **Success:** given a dirty worktree on a plan `E-` branch, one partial-work commit and one ledger row exist and the
  tree is clean.
- **Discard:** the branch rule cannot be made safe, or cost exceeds 1.5x cost_pred. Then fall back to a clear refusal
  that names the commit-or-discard command.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.4   |
| impact_pred         | 40.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 30.0  |

### Step Metrics Rationale

`EV = 0.85 * 40.0 + 0.5 * 4.0 - 0.3 * 2.4 - 5.0 = 34.0 + 2.0 - 0.72 - 5.0 = 30.28`. This is the core of the feature and
the bottleneck. The risk is in git edge cases, which are bounded by the guards and the tests.

---

## Step 3: Wire the salvage into Stage 3 resume and update module budgets

- **Sub‑intent recommendation:** NO
- **Reasoning:** a small edit to `flow/stages/execute.py` with a few lines of glue.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** call the salvage before the partial-branch lookup, and only on a resume.
- **Git branch:** I-1794478300-salvage/P-1794478317-4eb07fff-claude-agent-claude-sonnet-5-5/E-salvage-wiring/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `run_execute_stage`, inside the existing `if resume_partial:` block, after `_snapshot_child_branches` and the base
  fetch, call `salvage_leftovers(...)` first. Log one line when a commit is made (branch, short sha, file count). On a
  salvage error, return `_stage_failure(...)` with the message. Refresh `existing_executions` if a new branch was
  created, so `publish_new_child_branches` still treats it correctly.
- Then run the unchanged `_resumable_partial_branch(...)`. It now finds the salvaged branch.
- Without `resume_partial`, or with a clean worktree, nothing changes. An engine-files-only dirty tree returns an empty
  result and falls through to today's behaviour.
- Keep `execute.py` near its present size. Move any helper that would add lines into `partial_salvage.py`. Add an entry
  for the new module in `apps/sandbox-executor/flow_module_budgets.json` at its creation size, and raise the
  `stages/execute.py` budget only by the lines actually added, with a sentence in the description. Run
  `scripts/check_flow_module_budgets.py` via `uv run task lint`.
- `run_execute_stage` already carries `noqa` markers; do not add more. If the added lines would break a limit, put the
  resume block into a helper function in `partial_salvage.py`.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md section 2, flow module budgets.
- **Potential failure modes for this step:** the stale `existing_executions` snapshot causes a duplicate push or a
  mis-detected new branch; the budget check fails.
- **Guardrails and early‑abort checks:** the test in Step 4 for a failed branch that already has partial commits;
  `uv run task lint`.

### Success & Discard Criteria

- **Success:** a resume over a dirty worktree no longer raises the mounted-workspace error and dispatches with the
  salvaged branch as the fourth argument.
- **Discard:** the stage's behaviour changes on a clean worktree; revert the glue.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.2   |
| impact_pred         | 20.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 17.0  |

### Step Metrics Rationale

`EV = 0.95 * 20.0 + 0.5 * 2.0 - 0.3 * 1.2 - 2.0 = 19.0 + 1.0 - 0.36 - 2.0 = 17.64`, shown as 17.0. Small and
well-contained glue.

---

## Step 4: Add hermetic tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** one new test file, with no network and no live agent. It declares its test-file change, as the ruleset
  requires.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** prove the five required scenarios and the ledger record.
- **Git branch:** I-1794478300-salvage/P-1794478317-4eb07fff-claude-agent-claude-sonnet-5-5/E-salvage-tests/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- New file `apps/sandbox-executor/tests/test_partial_salvage.py` (test file declared here). It uses the temporary git
  repositories from `worktree_fixtures.py` and `hermetic_fixtures.py`, so there is no network or credentials.
- Cases:
  1. Dirty tracked file (for example an edited `tests/conftest.py`): one partial-work commit on the `E-` branch, the
     tree is clean, the message is labelled, and `failed_partial_head` returns the new sha.
  2. Dirty untracked file (for example a new `tests/network_guard.py`): committed and recorded.
  3. No leftovers: no commit, no ledger row, no branch change, and an unchanged result object.
  4. A failed branch that already has partial commits: the new commit lands on top, earlier commits are intact (no
     rewrite), and the latest row's `partial_head` is the new head.
  5. Only engine-owned files dirty (ledger rows and the notes or report file): no commit and no new ledger row.
  6. Mixed engine and real edits: the commit includes only the real edits, and the ledger prefix bytes are unchanged
     (`assert_ledger_prefix`).
  7. Guard: a dirty worktree on the plan branch or `main` is refused with a clear message and left untouched.
  8. Stage wiring: with `_dispatch_role` stubbed, `run_execute_stage(resume_partial=True)` passes the salvaged branch as
     the fourth argument, and a clean worktree passes exactly what it passed before.
- Run `uv run pytest apps/sandbox-executor/tests/test_partial_salvage.py` first, then the neighbouring stage and
  mounted-workspace tests (`test_host_worktree_sandbox.py`, `test_ledger_append_only.py`, `test_executor.py`), then the
  whole suite once with `uv run task test`.

### Dependencies & Criticality

- **Depends on:** Steps 2 and 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** safety.md (hermetic tests), ruleset.md section 3.
- **Potential failure modes for this step:** tests rely on the developer's global git config; slow temporary repos.
- **Guardrails and early‑abort checks:** set identity through repo-local config in fixtures; make the network guard
  fixtures apply.

### Success & Discard Criteria

- **Success:** all new tests pass, and the full suite passes via `uv run task test`.
- **Discard:** a failure in an unrelated area. Investigate before editing anything outside the touch set.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 1.2   |
| impact_pred         | 20.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 15.0  |

### Step Metrics Rationale

`EV = 0.93 * 20.0 + 0.5 * 3.0 - 0.3 * 1.2 - 4.0 = 18.6 + 1.5 - 0.36 - 4.0 = 15.74`, shown as 15.0. The tests carry most
of the assurance for acceptance criteria 1 to 4.

---

## Step 5: Document the behaviour and run the lint gates

- **Sub‑intent recommendation:** NO
- **Reasoning:** short documentation edits, with the gates as the check.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** describe resume salvage and its audit trail, then make lint pass.
- **Git branch:** I-1794478300-salvage/P-1794478317-4eb07fff-claude-agent-claude-sonnet-5-5/E-salvage-docs/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Update `docs/git_flow.md` and `docs/executor/execution_architecture_specification.md` (and `docs/ledger_schema.md` for
  the new `salvaged` field on the executions row) to say that a Stage 3 resume with a dirty mounted worktree commits the
  leftovers onto the failed execution branch as partial work. Document the excluded engine-owned files, the labelled
  message, the ledger row, that the executor re-runs checks, and that the review loop sees the final diff. Note that the
  three ledgers are unchanged in number and that merging stays with the human.
- Use Australian English, and keep paths target-native.
- Run `uv run task lint` and `uv run task lint-docs`, and fix formatting (prettier wraps at 120 columns). Confirm
  `check_flow_module_budgets.py` passes.

### Dependencies & Criticality

- **Depends on:** Steps 2 to 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md (docstrings and docs), flow module budgets.
- **Potential failure modes for this step:** docs lint formatting oscillation; a budget failure.
- **Guardrails and early‑abort checks:** write formulas in code spans; rerun lint-docs until stable.

### Success & Discard Criteria

- **Success:** `uv run task lint`, `uv run task lint-docs` and `uv run task test` all pass.
- **Discard:** none; this step must pass.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.6   |
| impact_pred         | 10.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 8.0   |

### Step Metrics Rationale

`EV = 0.95 * 10.0 + 0.5 * 1.0 - 0.3 * 0.6 - 1.5 = 9.5 + 0.5 - 0.18 - 1.5 = 8.32`, shown as 8.0. Low risk, and it closes
the acceptance criteria.

---
