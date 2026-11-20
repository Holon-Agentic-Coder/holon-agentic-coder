# Plan for I-1794224634-plan-owned-decomposition-with-plan-branch-children--2-nest-decomposed-children-under-the-plan-branch-m

- **Plan ID:** P-1794227872-152246bc-claude-agent-claude-opus-5-5
- **Parent Intent ID:** I-1794224634-plan-owned-decomposition-with-plan-branch-children/\_
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-09T12:37:52.944Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None. Two clarifications are recorded as implementation constraints rather than a
  reframe: (1) `stages/execute.py` cannot be edited, so `stages/decomposition.py` keeps `_run_decomposed_plan` with its
  current signature as a thin delegator into the new `stages/children.py` and `stages/integration.py`; (2) the
  parent-executor no-redispatch rule on resume is enforced by a budget-neutral hook in `flow/pipeline.py` that routes a
  checkpointed decomposed Stage 3 to `child_progress` instead of `run_execute_stage`.
- **Exploration stance:** balanced. The work is mostly exploitation of existing helpers (`create_or_update_pr`,
  `probe_ci_checks`, `_append_superseding_execution_row`, `repository_default_branch`, checkpoint save/load), with one
  early information-gathering step to confirm the tight module budgets and Stage 4 behaviour on a combined diff before
  code is written.
- **Safety priority level:** elevated
- **Priority Justification:** The intent introduces the first code path in which the flow engine itself merges pull
  requests. `holon-config/world/constraints.md` §1 (only human operators approve merges to `main`) and `docs/safety.md`
  (root intents are the only human merge boundary; sub-intents never merge directly to `main`) make a wrong-base merge a
  critical violation, so the refusal check is pure, fail-closed and proven by a dedicated test. Ledger immutability
  (constraints §3) also constrains how ledger-only merge conflicts may be resolved.

## Exploration

- **Proportion of steps that are exploratory:** 0.1
- **Justification:** One of ten steps (Step 1) is information gathering that de-risks the two budget-capped modules
  (`pipeline.py` 394/394, `flow/__init__.py` 362/362) and the Stage 4 behaviour on an integrated execution branch.
  Everything else is a specified implementation with hermetic tests, so further exploration would add cost without new
  learning.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.65  |
| entropy_pred        | 12.8  |
| impact_pred         | 88.0  |
| cost_pred           | 30.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 26.36 |

### Strategy Rationale

The intent is a coherent bundle (Steps 1 and 5 to 10 of the parent plan): the merge guard, the child loop, the
integration step and resume safety all share one data structure (per-child progress in the Stage 3 payload) and one test
fixture (a bare origin with a plan branch and a fake `gh`). Splitting it further would force each grandchild to stub the
others' contracts, add two to four extra review and calibration cycles, and would sit at sub-intent depth 2, the nesting
limit. The summed step entropy (12.8) fits the 15.0 budget, so the plan runs as one execution with no
`## Sub-intent list`.

Ordering is leaves first: pure helpers (`ledger_interleave`, the merge refusal check, the child progress decision
function) are written and unit-tested before the orchestration modules (`children`, `integration`) and the single
`pipeline.py` hook that wires resume. This keeps every intermediate commit green and lets the executor abort early if
the budget-capped modules cannot absorb the hook.

Aggregation: `entropy_pred` is the sum of step entropies
(`0.5 + 1.0 + 1.8 + 1.5 + 2.2 + 1.8 + 1.2 + 1.6 + 0.6 + 0.6 = 12.8`). `cost_pred` is the sum of step costs
(`2 + 2.5 + 4 + 3.5 + 4.5 + 3.5 + 2 + 4.5 + 1.5 + 2 = 30.0`). `p_success_pred` is not the raw product of step
probabilities (about 0.15), because failures in Steps 2 to 8 are locally repairable within the same execution; it is a
qualitative judgement anchored on the bottleneck pair, Step 5 (child loop split, 0.75) and Step 8 (end-to-end hermetic
tests, 0.70), discounted for the module-budget risk: `0.65`. `impact_pred` is 88 because this closes the only route for
decomposed work to reach `main` and fixes duplicate-PR resume bugs observed in the first decomposed run.
`learning_value_pred` is 6.0 (first automated merge path; first use of per-child progress).

`EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
`EV = 0.65 * 88.0 + 0.5 * 6.0 - 0.3 * 12.8 - 30.0 = 57.20 + 3.00 - 3.84 - 30.0 = 26.36`

Strong assumptions: (a) this intent's branch does not contain sibling 1's plan-row code, so tests build the decision
record (`sub_intents`, `entropy_budget`, `plan_branch`) directly as fixture data, and runtime code reads those keys from
the row it is given by `execute.py` (the executions summary row today, the plan row once sibling 1 lands) without caring
which ledger it came from; (b) Stage 1 already cuts a child's intent branch from `origin/{target_branch}`
(`stages/intent.py` and `entrypoint/intent_creator.py`), so recording the plan branch as `target_branch` is enough to
cut each child from the current plan branch tip, provided the previous child's merge has landed on origin first.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1: only human operators approve merges to `main`; agents never commit directly
    to `main` or to other intents' branches; rebase/bring-in-parent discipline before merges.
  - `holon-config/world/constraints.md` §3: ledgers are append-only; no historical line may be removed, rewritten or
    reordered.
  - `docs/safety.md` (sub-intent merge rules): sub-intents never merge directly to `main` and merge only into their
    deciding node's branch; the root intent's PR to `main` is the only human step.
  - `holon-config/world/ruleset.md` §2 and §3: typed, docstringed public functions; tests via `uv run pytest`; test
    changes declared in the plan (this plan declares new and updated test files in Steps 2 to 8).
  - Intent constraints: do not edit `stages/execute.py`, `stages/plan.py`, `entrypoint/` or `docs/ledger_schema.md`; no
    new dependencies; raise no module budget; Australian English.
- **Potential violations or edge cases:**
  - A merge into `main`, `master` or the repository default branch through a mis-recorded `target_branch`, a stale PR
    base, or a failed default-branch lookup that silently falls back to `main`.
  - Interleaving ledger rows reorders lines that already exist on the base, which would violate append-only.
  - Squash or rebase merges would drop child commits from the plan branch ancestry.
  - Resume re-dispatching the parent executor or a child, opening duplicate PRs or overwriting a child checkpoint.
  - Hermetic tests reaching the real `gh` with a write verb (blocked by `tests/gh_write_guard.py`, but a test must never
    depend on that guard to pass).
  - `pipeline.py` and `flow/__init__.py` are exactly at budget, so any net growth fails `uv run task lint`.
- **Mitigations built into the plan:**
  - A pure `merge_refusal_reason` function rejects `main`, `master`, the default branch, any base that differs from the
    expected plan branch, and any default-branch lookup failure (fail closed, no fallback to `main`); `merge_child_pr`
    calls it twice (before bringing the plan branch in and immediately before the merge), and Step 3 adds the explicit
    "base is `main` is refused and `gh pr merge` is never invoked" test.
  - Only `gh pr merge --merge --match-head-commit <sha>` is ever constructed; no `--squash`, `--rebase`, `--auto`,
    `--admin` or `--delete-branch`.
  - `ledger_interleave` keeps the common merge-base prefix byte-identical and only interleaves the two appended tails on
    `created_at`, so no existing line is removed or reordered; non-ledger conflicts abort the merge.
  - Resume decisions come from a pure function over recorded progress plus origin facts; any execution branch or open or
    merged PR forbids a fresh dispatch.
  - `gh` is reached only through an injectable runner, so tests pass fakes and never touch the network.
  - Step 7 removes the obsolete decomposed Stage 4/5 skip block in `pipeline.py` in the same edit that adds the resume
    hook, keeping the module at or under 394 lines; `flow/__init__.py` swaps the late-binding target rather than adding
    a line.
- **Residual risk accepted (and why):** The real GitHub merge path (branch protection, required reviews, merge-queue
  settings) is only exercised against fakes. Accepted because the guard is fail-closed (an unexpected `gh` response
  refuses the merge and fails Stage 3 with a clear message) and the first live decomposed run is itself observed by the
  maintainer.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 12.8
- **Budget Compliance:** The strategy fits within budget (`12.8 <= 15.0`), so it runs as one plan with no sub-intent
  list.

## Plan Description & Strategy

Touch set (module granularity):

- New: `flow/github/merge.py`, `flow/ledger_interleave.py`, `flow/child_progress.py`, `flow/stages/children.py`,
  `flow/stages/integration.py`.
- Edited: `flow/stages/decomposition.py` (shrinks to depth checks, child naming, summary formatting and a delegating
  `_run_decomposed_plan`), `flow/pipeline.py` (budget-neutral resume hook; removes the decomposed Stage 4/5 skip),
  `flow/__init__.py` (re-exports and late binding, budget-neutral), `apps/sandbox-executor/flow_module_budgets.json`
  (new entries at creation size; optionally lower `stages/decomposition.py` to its new size; raise nothing).
- Tests: new `tests/flow/github/test_merge.py`, `tests/flow/test_ledger_interleave.py`,
  `tests/flow/test_child_progress.py`, `tests/flow/stages/test_children.py`, `tests/flow/stages/test_integration.py`;
  updated `tests/flow/stages/test_decomposition.py`, `tests/flow/test_package_exports.py` and
  `tests/flow/test_pipeline.py` where they assert the old parent-intent-branch behaviour or the decomposed Stage 4/5
  skip.
- Docs: `docs/architecture.md`, `docs/git_flow.md`, `docs/safety.md`.
- Untouched by contract: `flow/stages/execute.py`, `flow/stages/plan.py`, `src/sandbox_executor/entrypoint/`,
  `docs/ledger_schema.md`.

End-to-end flow after this change: Stage 3 (unchanged dispatch in `execute.py`) adopts the parent execution branch and
hands the decision record to `_run_decomposed_plan`, which delegates to `children.run_children`. For each item it
records progress, builds the child intent with `target_branch` and `parent_intent_id` equal to the plan branch and the
branch name `{plan}--{n}-{slug}`, runs the child pipeline to its Stage 4 consensus halt and Stage 5 calibration, then
calls `merge_child_pr` to merge the child PR into the plan branch with a merge commit, and only then moves to child N
plus 1. After the last child, `integration.integrate_children` merges the plan branch into the parent execution branch,
appends a superseding executions summary row listing the children, and returns a successful decomposed Stage 3 result;
the pipeline then runs the normal Stage 4 (raises and reviews the combined PR into the intent's `target_branch`, halting
at consensus for the human when that is `main`) and Stage 5. On resume at Stage 3, the pipeline hook sees the
checkpointed decomposed payload and calls `child_progress`/`children.resume_children` instead of re-dispatching the
parent executor.

---

## Step 1: Confirm contracts, budgets and Stage 4 behaviour before writing code

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only and cheap; it exists to catch the two budget-capped modules and Stage 4 assumptions early.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** `pipeline.py` can host the resume hook net-neutrally by deleting the decomposed Stage 4/5
  skip and `_parent_was_decomposed`; Stage 4 raises a PR from `context.execution_branch` into
  `intent_data["target_branch"]` without special-casing ledger-heavy diffs; the hermetic fixtures can host a bare origin
  with a plan branch.
- **Learning target:** exact line headroom after the deletions, which existing tests assert the skip or the
  parent-intent-branch target, and how `tests/worktree_fixtures.py`/`tests/hermetic_fixtures.py` build origin repos.
- **Maximum acceptable cost for this learning:** 2 cost units (about 7 percent of the plan).

### Intent & Git Integration

- **Step Intent:** Establish facts that later steps rely on, without editing files.
- **Git branch:** current execution branch (no separate branch).
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Read `flow/pipeline.py` (`execute_stage`, `run`, `_parent_was_decomposed`), `flow/stages/review.py` (PR base and head
   selection), `flow/stages/calibrate.py` (what it needs from the plan and execution rows), `flow/checkpoint.py` (child
   checkpoints are keyed on the child branch when `parent_intent_id` is set), `flow/git_refs.py`
   (`_append_superseding_execution_row`, `_list_child_branches`, `_resolve_branch_ref`), `flow/github/ci.py`
   (`probe_ci_checks`, `_ci_green`), `flow/github/pr.py` (`check_pr_mergeability`, `create_or_update_pr`) and
   `git_flow.repository_default_branch` (falls back to `main`, so the merge guard must use its own fail-closed lookup).
2. Grep `tests/` for assertions on `parent_branch`, `"decomposed"` skip reasons, `format_decomposition_summary` text and
   `_decomposition.PipelineEngine`, and list the tests Step 8 must update.
3. Record the headroom: `pipeline.py` budget 394, `flow/__init__.py` budget 362, `stages/decomposition.py` budget 242.
4. Abort the plan (fail fast, report) if the Stage 4 review stage cannot target a non-`main` branch or if the pipeline
   hook would need more than the lines freed by the deleted skip.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §1; intent no-edit list.
- **Potential failure modes for this step:** missing a test that pins the old behaviour, discovered late in Step 10.
- **Guardrails and early‑abort checks:** no writes; abort criteria in item 4.

### Success & Discard Criteria

- **Success:** a written list (in the execution notes) of tests to update and the confirmed line headroom.
- **Discard:** cost exceeds 3 units, or the hook cannot fit the budget (then stop and report rather than raise a
  budget).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.5   |
| impact_pred         | 20.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 19.25 |

### Step Metrics Rationale

Read-only. `EV = 0.97 * 20.0 + 0.5 * 4.0 - 0.3 * 0.5 - 2.0 = 19.40 + 2.00 - 0.15 - 2.0 = 19.25`.

---

## Step 2: Add `flow/ledger_interleave.py` for ledger-only merge conflicts

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small pure module with a clear contract; reused by Steps 3 and 6.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Resolve conflicts confined to `holon-knowledge/ledger/*.jsonl` by keeping the merge-base rows as an
  unchanged prefix and interleaving both sides' appended rows on `created_at`.
- **Git branch:** current execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Pure function `interleave_ledger_rows(base_lines, ours_lines, theirs_lines)`: assert each side starts with the base
   lines (otherwise raise `ConflictResolutionError` reused from `ledger_reconcile`, because a non-append change is not
   ledger-only); take each side's tail; drop tail lines that are byte-identical to a line already kept; merge the two
   tails by a stable sort on parsed `created_at` (ISO-8601 string compare after normalising `Z`), placing rows without a
   parsable `created_at` after timed rows in their original side order (ours first).
2. Git-facing function `resolve_ledger_only_conflicts(repo_dir)`: list unmerged paths
   (`git diff --name-only --diff-filter=U`); if any path is outside the ledger directory return False (caller aborts the
   merge); otherwise read stages 1, 2 and 3 with `git show :1:path` etc., write the interleaved result, `git add` it,
   and return True.
3. Docstrings state the append-only guarantee; no new dependencies.
4. Tests `tests/flow/test_ledger_interleave.py`: base prefix preserved byte-for-byte; tails interleaved by `created_at`;
   duplicate rows de-duplicated; a non-ledger conflict returns False; a side that rewrote a base line raises; a real
   two-branch fixture repo resolves a conflicting `executions.jsonl`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (append-only ledgers).
- **Potential failure modes for this step:** reordering existing lines; masking a genuine non-ledger conflict.
- **Guardrails and early‑abort checks:** prefix assertion; non-ledger paths return False.

### Success & Discard Criteria

- **Success:** `uv run pytest apps/sandbox-executor/tests/flow/test_ledger_interleave.py` passes; module under 120
  lines.
- **Discard:** if the existing `reconcile_jsonl_ledger_conflict` already satisfies the contract, reuse it and keep this
  module as a thin `created_at` ordering wrapper.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.0   |
| impact_pred         | 35.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 30.40 |

### Step Metrics Rationale

Well-bounded pure logic. `EV = 0.92 * 35.0 + 0.5 * 2.0 - 0.3 * 1.0 - 2.5 = 32.20 + 1.00 - 0.30 - 2.5 = 30.40`.

---

## Step 3: Add `flow/github/merge.py` with the guarded `merge_child_pr` and the pure refusal check

- **Sub‑intent recommendation:** YES (advisory only; kept in this plan because it is under 2 entropy units and the child
  loop cannot be tested without it)
- **Reasoning:** Safety-critical and reusable, but its blast radius is contained by a pure, separately tested refusal
  check, and splitting would leave the child loop without a merge implementation.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Merge a child PR into the plan branch with a merge commit only when it is safe, and refuse anything
  that could land on `main` or a default branch.
- **Git branch:** current execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Pure `merge_refusal_reason(base_branch, expected_base, default_branch)` returning a reason string or None. It
   normalises `refs/heads/` and `origin/` prefixes and refuses when the base is empty, is `main` or `master`, equals
   `default_branch`, differs from `expected_base`, or when `default_branch` is None (lookup failed). The expected base
   itself being `main`, `master` or the default branch is also refused.
2. `lookup_default_branch(repo_dir, runner)` asks `gh repo view --json defaultBranchRef`; on any error or empty value
   returns None (fail closed; never the `repository_default_branch` fallback to `main`).
3. `merge_child_pr(repo_dir, pr_number, plan_branch, runner=...)` (at most five parameters, complexity within C901 10 by
   splitting into helpers): (a) `gh pr view` for `state`, `baseRefName`, `headRefName`, `headRefOid`, `mergeable`; if
   already `MERGED` with base equal to the plan branch, return an idempotent "already merged" result; (b) apply the
   refusal check; (c) bring the plan branch into the child head: fetch both, and if `origin/{plan}` is not an ancestor
   of the head, check out the head, `git merge --no-ff origin/{plan}`, resolve ledger-only conflicts with Step 2 (abort
   and refuse otherwise), push, and re-read the head SHA; (d) require CI green via `probe_ci_checks` and `_ci_green` on
   the final head and `mergeable == MERGEABLE`; (e) re-run the refusal check, then run
   `gh pr merge <n> --merge --match-head-commit <sha>`; (f) verify with `gh pr view` that state is `MERGED`.
4. Return a small typed result (dataclass or dict) with `merged`, `merge_commit`, `reason`; never raise for a refusal,
   so Stage 3 can report it.
5. Tests `tests/flow/github/test_merge.py` with a fake runner: refusal of base `main` (the required test; asserts no
   `pr merge` call), `master`, default branch, unexpected base, failed default-branch lookup; red or pending CI refuses;
   `CONFLICTING` refuses; the command uses `--merge` and never `--squash`/`--rebase`/`--delete-branch`; an already
   merged PR is idempotent; a stale child gets the plan branch merged in before merging.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES (children cannot build on each other without it)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §1; `docs/safety.md` sub-intent merge rules; parent goal 4.
- **Potential failure modes for this step:** fallback default branch hides a failed lookup; TOCTOU base change between
  check and merge; real `gh` reached in tests.
- **Guardrails and early‑abort checks:** double refusal check, `--match-head-commit`, injectable runner, fail closed.

### Success & Discard Criteria

- **Success:** all merge tests pass, including the base-`main` refusal; no `noqa` added.
- **Discard:** if CI-status probing cannot be reused without growing `ci.py`, call it as-is and keep the logic in
  `merge.py`; never weaken the refusal check to make a test pass.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 1.8   |
| impact_pred         | 75.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 58.96 |

### Step Metrics Rationale

First automated merge in the flow, hence higher entropy and learning value.
`EV = 0.82 * 75.0 + 0.5 * 4.0 - 0.3 * 1.8 - 4.0 = 61.50 + 2.00 - 0.54 - 4.0 = 58.96`.

---

## Step 4: Add `flow/child_progress.py` for per-child progress and origin reconciliation

- **Sub‑intent recommendation:** NO
- **Reasoning:** Medium-size data module whose pure decision function is the core of resume safety; tightly coupled to
  Step 5.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Record which children are pending, in progress, merged or failed in the parent Stage 3 payload,
  reconcile that against origin on resume, and decide each child's next action without ever re-dispatching.
- **Git branch:** current execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Progress entries live in the parent Stage 3 payload under `children_progress` (checkpointed with the stage result):
   `index`, `slug`, `intent_branch`, `execution_branch`, `pr_number`, `pr_url`, `status` (`pending`, `in_progress`,
   `review_done`, `merged`, `failed`), `merge_commit`.
2. `initial_progress(items, plan_branch)` builds pending entries with deterministic branch names (reusing
   `_child_intent_branch` from `decomposition.py`).
3. `reconcile_with_origin(repo_dir, progress, runner)`: for each entry, discover execution branches under the child
   intent prefix on origin (`_list_child_branches`/`_fetch_branch_prefix`) and the child PR via
   `gh pr list --head <exec> --state all --json number,state,baseRefName,url`; fill in missing facts and promote status
   (an `MERGED` PR makes the entry `merged`). Origin wins over the checkpoint when they disagree, and the disagreement
   is logged.
4. Pure `next_child_action(entry)` returns one of `skip` (merged), `merge` (PR open and the child's Stage 4/5 already
   completed), `resume` (intent branch, execution branch or PR exists, or a child checkpoint exists) or `dispatch`
   (nothing exists anywhere). `dispatch` is impossible when any execution branch or open or merged PR is recorded.
5. `child_checkpoint_path(context, intent_branch)` resolves the child's own checkpoint via `get_default_checkpoint_path`
   keyed on the child branch, matching `save_checkpoint`'s keying, so resume reads and writes only that child's file.
6. `has_recorded_decomposition(stage_result)` tells the pipeline hook (Step 7) that a checkpointed Stage 3 already
   decomposed, so the parent executor is never dispatched again.
7. Tests `tests/flow/test_child_progress.py`: the full decision table; reconciliation with fake origin facts; origin
   overrides a stale checkpoint; checkpoint paths are distinct per child.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (resume safety, goal 8 f)

### Safety & Constraint Considerations

- **Relevant rules:** parent goal 6; `docs/safety.md` containment of failures to the branch.
- **Potential failure modes for this step:** a missed PR lookup leads to `dispatch` and a duplicate PR; payload growth
  bloating checkpoints.
- **Guardrails and early‑abort checks:** a failed `gh` lookup yields `resume`, never `dispatch`; only small scalar
  fields are stored.

### Success & Discard Criteria

- **Success:** decision-table tests pass, including "exec branch exists, no PR" yields `resume`.
- **Discard:** if reconciliation needs more than about 180 lines, move origin queries into `git_refs.py` helpers only if
  that module stays within its budget; otherwise keep them here.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.78  |
| entropy_pred        | 1.5   |
| impact_pred         | 65.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 4.0   |
| ev_pred             | 48.75 |

### Step Metrics Rationale

New state representation with origin reconciliation.
`EV = 0.78 * 65.0 + 0.5 * 4.0 - 0.3 * 1.5 - 3.5 = 50.70 + 2.00 - 0.45 - 3.5 = 48.75`.

---

## Step 5: Split the child loop into `flow/stages/children.py` and nest children under the plan branch

- **Sub‑intent recommendation:** NO
- **Reasoning:** Central orchestration change; it depends on Steps 3 and 4 and is only testable with them, so a separate
  branch would add integration risk rather than remove it.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Run children strictly one after another from the plan branch tip, record the plan branch as each
  child's `target_branch` and `parent_intent_id`, and merge each child after its consensus and calibration.
- **Git branch:** current execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Move `_child_context`, `_child_review_pr` and the loop body out of `decomposition.py` into `stages/children.py`.
   `decomposition.py` keeps `_sub_intent_depth`, `_sub_intent_depth_error`, `_child_intent_branch`, the summary
   formatting and `_run_decomposed_plan(context, start_time, summary_row, base_payload)` (signature unchanged because
   `execute.py` imports it), which now validates items and depth and delegates to `children.run_children` and then
   `integration.integrate_children`.
2. The parent branch for children becomes the plan branch: `context.plan_branch`, else `summary_row["plan_branch"]`;
   fail Stage 3 clearly if neither is known (never fall back to the intent branch). Child branch naming stays
   `_child_intent_branch(plan_branch, n, slug)`, giving `{plan}--{n}-{slug}`. The child intent records
   `target_branch = plan_branch` and `parent_intent_id = plan_branch`, plus inherited `entropy_budget` and labels.
3. `run_children(context, start_time, items, payload)` iterates progress entries from Step 4: for `skip` it moves on;
   for `merge` it calls `merge_child_pr`; for `resume` it loads the child's checkpoint and runs
   `PipelineEngine(child_context, from_stage=checkpoint.current_stage)`; for `dispatch` it runs a fresh child pipeline.
   Before each child it fetches the plan branch so Stage 1 cuts from the latest tip.
4. A child is ready to merge when its results contain a Stage 4 `HALTED_FOR_HUMAN` consensus and Stage 5 succeeded or
   was skipped by flag; then `merge_child_pr(repo, pr_number, plan_branch)` runs. A refusal or failure marks the entry
   `failed`, saves the parent checkpoint and fails Stage 3 naming the child and reason. A child that itself decomposed
   is merged through its own combined PR, which targets this plan branch.
5. After every state change the entry is updated and the parent checkpoint is saved through the pipeline's save hook
   (passed in as a callable or done via `save_checkpoint(context)`), so a crash never loses progress.
6. `flow/__init__.py`: replace `_decomposition.PipelineEngine = PipelineEngine` with the binding for `children`, update
   re-exports of moved names, and keep the file at or under 362 lines; update `tests/flow/test_package_exports.py`.
7. Keep every function within C901 10, PLR0912 12, PLR0913 5 and PLR0915 50 without `noqa` (split into
   `_build_child_intent`, `_run_one_child`, `_merge_ready_child`, `_child_summary`).

### Dependencies & Criticality

- **Depends on:** Steps 3 and 4
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** parent goals 3 and 4; `holon-config/world/constraints.md` §1 (children never target `main`).
- **Potential failure modes for this step:** import cycle with `pipeline`; children still cut from the intent branch
  through a leftover fallback; `__init__.py` budget overflow.
- **Guardrails and early‑abort checks:** late binding mirrors the existing documented pattern; an explicit test that no
  child `target_branch` equals the intent branch or `main`; run `scripts/check_flow_module_budgets.py` after the move.

### Success & Discard Criteria

- **Success:** existing decomposition tests, updated for the plan-branch target, pass; budgets hold.
- **Discard:** if `__init__.py` cannot stay within budget, drop the re-exports of private moved names (tests import them
  from the submodule) rather than raise the budget.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.75  |
| entropy_pred        | 2.2   |
| impact_pred         | 80.0  |
| cost_pred           | 4.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 56.34 |

### Step Metrics Rationale

Largest move plus behaviour change.
`EV = 0.75 * 80.0 + 0.5 * 3.0 - 0.3 * 2.2 - 4.5 = 60.00 + 1.50 - 0.66 - 4.5 = 56.34`.

---

## Step 6: Add `flow/stages/integration.py` to integrate the plan branch and hand off to Stage 4

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, focused module that reuses existing helpers; its correctness is checked end to end in Step 8.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** After the last child merged, bring the plan branch into the parent execution branch and record a
  superseding summary row, so the normal Stage 4 and Stage 5 raise and review the combined PR.
- **Git branch:** current execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `integrate_children(context, start_time, payload)`: fetch `origin/{plan_branch}` and the parent execution branch;
   check out the execution branch; `git merge --no-ff origin/{plan_branch}` with a message naming the children; resolve
   ledger-only conflicts with Step 2, otherwise abort and fail Stage 3.
2. Append a superseding executions summary row on the execution branch via `_append_superseding_execution_row` (which
   pushes): the original execution id, status `decomposed`, `plan_branch`, and `children` (slug, intent branch,
   execution branch, PR number and URL, merge commit), with a fresh `created_at`. This touches only the ledger row
   format already used by superseding rows, so `docs/ledger_schema.md` is not edited.
3. Return a successful Stage 3 result with `decomposed: True`, `integrated: True`, `children` and `children_progress`,
   and `context.execution_branch` set to the parent execution branch, so Stage 4 raises the PR from the execution branch
   into `intent_data["target_branch"]`. The combined PR therefore carries the parent plan (ancestor), the parent
   execution record (on the execution branch) and every child plan and execution record (merged into the plan branch).
4. Idempotent on resume: if `origin/{plan_branch}` is already an ancestor of the execution branch and the superseding
   row exists, skip both actions.
5. Update `format_decomposition_summary` in `decomposition.py`: list each child PR as merged into the plan branch, and
   say the combined PR is raised by Stage 4 (no more "Pending (not yet automated)" text); failed runs still omit merge
   guidance.

### Dependencies & Criticality

- **Depends on:** Steps 2 and 5
- **Is Bottleneck:** YES (goal 8 e)

### Safety & Constraint Considerations

- **Relevant rules:** parent goal 5; `docs/safety.md` (root PR to `main` is the human step).
- **Potential failure modes for this step:** combined PR head missing child work; duplicate superseding rows on resume.
- **Guardrails and early‑abort checks:** ancestry check before and after the merge; idempotency check on the row.

### Success & Discard Criteria

- **Success:** fixture test shows the execution branch contains the parent plan file, the parent execution row and every
  child plan file, and Stage 4 halts for the human with base `main`.
- **Discard:** if `_append_superseding_execution_row` cannot carry `children`, write the row through the same ledger
  lock helper rather than editing `git_refs.py` past its budget.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.75  |
| entropy_pred        | 1.8   |
| impact_pred         | 75.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 4.0   |
| ev_pred             | 54.21 |

### Step Metrics Rationale

Reuses helpers but is the first route to `main` for decomposed work.
`EV = 0.75 * 75.0 + 0.5 * 4.0 - 0.3 * 1.8 - 3.5 = 56.25 + 2.00 - 0.54 - 3.5 = 54.21`.

---

## Step 7: Wire the budget-neutral resume hook into `flow/pipeline.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** A few lines in a budget-capped core module; risk is managed by removing the obsolete skip in the same
  edit.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Never re-dispatch the parent executor on resume, and let Stage 4 and Stage 5 run normally after a
  decomposed Stage 3.
- **Git branch:** current execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Delete the decomposed Stage 4/5 skip block in `run` and the now-unused `_parent_was_decomposed`.
2. In `execute_stage`, before the `resume_partial` branch: if the checkpointed Stage 3 result satisfies
   `child_progress.has_recorded_decomposition`, return `children.resume_children(self.context, previous_result)`, which
   reconciles with origin and continues the loop and integration without calling `run_execute_stage`.
3. Keep the decomposed post-hoc budget exemption.
4. Confirm `pipeline.py` stays at or under 394 lines; update `tests/flow/test_pipeline.py` where it asserted the skip.

### Dependencies & Criticality

- **Depends on:** Steps 4, 5 and 6
- **Is Bottleneck:** NO (the child loop still works on fresh runs without it, but goal 8 f would fail)

### Safety & Constraint Considerations

- **Relevant rules:** parent goal 6; module budget rule in the intent.
- **Potential failure modes for this step:** budget overflow; a non-decomposed Stage 3 resume taking the new path.
- **Guardrails and early‑abort checks:** the hook only fires when the checkpointed payload has `decomposed` and
  `children_progress`; budget script run immediately.

### Success & Discard Criteria

- **Success:** pipeline tests pass; budget check passes with no raised budget.
- **Discard:** if the hook does not fit, move the dispatch decision into `child_progress` and keep a one-line call.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 1.2   |
| impact_pred         | 60.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 47.14 |

### Step Metrics Rationale

Small edit, tight budget. `EV = 0.80 * 60.0 + 0.5 * 3.0 - 0.3 * 1.2 - 2.0 = 48.00 + 1.50 - 0.36 - 2.0 = 47.14`.

---

## Step 8: Add hermetic end-to-end tests for goal 8 b, c, d, e and f

- **Sub‑intent recommendation:** NO
- **Reasoning:** The tests are the acceptance proof for Steps 3 to 7 and share their fixtures; separating them would
  leave the implementation unverified on its branch.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove nesting, ordering, merge-commit merging, refusal of a `main` base, the combined PR and resume
  safety against fixture repositories with no network.
- **Git branch:** current execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Fixture: a bare origin plus a clone (reusing `tests/worktree_fixtures.py`/`hermetic_fixtures.py`) holding `main`, the
   parent intent branch, the plan branch with the plan file and a plans row carrying the decision record (`sub_intents`,
   `entropy_budget`, `plan_branch`) written directly as fixture data, and a parent execution branch.
2. Fake child pipeline: patch `sandbox_executor.flow.stages.children.PipelineEngine` (submodule patch target, per
   `test_patch_targets.py`) with a fake that cuts the child intent branch from `origin/{target_branch}`, commits a child
   plan file and a code file on a child execution branch, pushes, records a fake PR in an in-memory `gh` fake, saves its
   own checkpoint and returns Stage 4 `HALTED_FOR_HUMAN` and Stage 5 success.
3. Fake `gh` runner: `pr view`, `pr list`, `repo view` and `pr merge --merge` (performed as `git merge --no-ff` into the
   plan branch on origin through a scratch clone); it records every call so tests can assert no `pr create` was
   duplicated and no `--squash`/`--rebase` was used.
4. Tests in `tests/flow/stages/test_children.py`: (b) every child branch has the plan branch tip that existed when it
   was cut as an ancestor and its PR base is the plan branch; (c) child 2 is cut after child 1's merge commit and
   contains child 1's file; (d) the plan branch gets a two-parent merge commit per child, and a child whose fake PR base
   is `main` is refused with no `pr merge` call and Stage 3 fails naming the child.
5. Tests in `tests/flow/stages/test_integration.py`: (e) after the last child, the parent execution branch contains the
   parent plan file, the parent execution row, both child plan files and a superseding summary row listing the children;
   running Stage 4 with a fake review loop raises a PR with base `main` and the pipeline halts for the human.
6. Resume test (f): the second child's review fails; a resume from the parent checkpoint calls neither the parent
   executor dispatch nor a fresh child dispatch, continues child 2 from its own checkpoint, opens no second `pr create`,
   and leaves child 1's checkpoint file byte-identical.
7. Update `tests/flow/stages/test_decomposition.py` assertions that expected the parent intent branch as the child
   target or the old summary wording.

### Dependencies & Criticality

- **Depends on:** Steps 2 to 7
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (test changes declared here); hermetic testing guide;
  `tests/gh_write_guard.py`.
- **Potential failure modes for this step:** flaky git fixtures; fakes diverging from real `gh` JSON shapes.
- **Guardrails and early‑abort checks:** use `gh` JSON field names exactly as `pr.py` already parses them; no sleeps;
  all temp dirs via `tmp_path`.

### Success & Discard Criteria

- **Success:** `uv run pytest apps/sandbox-executor/tests/flow` passes with the new tests.
- **Discard:** if an end-to-end fixture takes more than 1.5x its cost estimate, keep the git-level assertions and
  replace the Stage 4 run with a direct `create_or_update_pr` call checked against the fake.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.70  |
| entropy_pred        | 1.6   |
| impact_pred         | 70.0  |
| cost_pred           | 4.5   |
| learning_value_pred | 4.0   |
| ev_pred             | 46.02 |

### Step Metrics Rationale

Multi-repo fixtures with fakes are the most fragile part.
`EV = 0.70 * 70.0 + 0.5 * 4.0 - 0.3 * 1.6 - 4.5 = 49.00 + 2.00 - 0.48 - 4.5 = 46.02`.

---

## Step 9: Update `docs/architecture.md`, `docs/git_flow.md` and `docs/safety.md`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Documentation of behaviour delivered in this plan; low risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Describe plan-branch nesting, automatic child merges, the combined PR and resume safety, and remove
  every interim note.
- **Git branch:** current execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `docs/architecture.md`: remove the interim notes (around the sub-intent merge rules and the deciding-node section)
   saying the flow still targets the parent intent branch and leaves merges to the maintainer; add module table rows for
   `flow/github/merge.py`, `flow/ledger_interleave.py`, `flow/child_progress.py`, `flow/stages/children.py`,
   `flow/stages/integration.py`; update the `decomposition.py` row and the late-binding note to name `children.py`.
2. `docs/git_flow.md`: rewrite the Stage 3 decomposition description: children cut from the plan branch tip one after
   another, `{plan}--{n}-{slug}` naming, merge commit into the plan branch after consensus and calibration, the combined
   PR from the parent execution branch, the human stop at `main`, and resume rules; remove the interim notes.
3. `docs/safety.md`: remove the interim sentences; document the merge refusal rules (merge commit only, CI green,
   mergeable, refusal of `main`, `master`, the default branch, unexpected base and failed lookups) and the ledger
   interleave guarantee.
4. Australian English; run `grep` for "not yet automated", "parent intent branch" and "Until the implementation follows"
   to confirm no interim wording remains.

### Dependencies & Criticality

- **Depends on:** Steps 3 to 7
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent no-edit list (`docs/ledger_schema.md` untouched).
- **Potential failure modes for this step:** prettier/markdownlint failures; stale anchors.
- **Guardrails and early‑abort checks:** `uv run task lint-docs` in Step 10.

### Success & Discard Criteria

- **Success:** docs describe only the new behaviour; lint-docs passes.
- **Discard:** NONE (docs must ship with the behaviour).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.6   |
| impact_pred         | 25.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 21.82 |

### Step Metrics Rationale

Routine edits. `EV = 0.92 * 25.0 + 0.5 * 1.0 - 0.3 * 0.6 - 1.5 = 23.00 + 0.50 - 0.18 - 1.5 = 21.82`.

---

## Step 10: Register module budgets and validate

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mandatory validation gate; cheap.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Record new modules at creation size, raise no budget, and pass the full gate.
- **Git branch:** current execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add `child_progress.py`, `ledger_interleave.py`, `github/merge.py`, `stages/children.py` and `stages/integration.py`
   to `flow_module_budgets.json` at their exact line counts; lower `stages/decomposition.py` (and `pipeline.py` if it
   shrank) to their new sizes; raise nothing.
2. Run `uv run pytest apps/sandbox-executor/tests/flow` and fix failures.
3. Run `uv run task test` once (no repetition: the intent sets no performance target).
4. Run `uv run task lint` (ruff complexity limits, module budget script) and `uv run task lint-docs`.
5. Record pass/fail counts and pytest-reported time in the execution record.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 9
- **Is Bottleneck:** YES (the plan only succeeds if the gate passes)

### Safety & Constraint Considerations

- **Relevant rules:** intent test-run discipline; module budget rule.
- **Potential failure modes for this step:** unrelated suite failures; a complexity limit hit in a new function.
- **Guardrails and early‑abort checks:** fix by splitting functions, never by `noqa` or raising a budget.

### Success & Discard Criteria

- **Success:** all four commands pass.
- **Discard:** if the full suite fails on tests unrelated to the touch set, record them with output and stop rather than
  editing unrelated code.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 0.6   |
| impact_pred         | 40.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 30.82 |

### Step Metrics Rationale

Gate risk mostly from lint complexity.
`EV = 0.80 * 40.0 + 0.5 * 2.0 - 0.3 * 0.6 - 2.0 = 32.00 + 1.00 - 0.18 - 2.0 = 30.82`.

---
