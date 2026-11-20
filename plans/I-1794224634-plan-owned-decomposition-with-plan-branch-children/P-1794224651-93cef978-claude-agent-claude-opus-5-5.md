# Plan for I-1794224634-plan-owned-decomposition-with-plan-branch-children

- **Plan ID:** P-1794224651-93cef978-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-09T11:44:11.623Z

## Planner Autonomy Summary

- **Intent handling:** SPLIT
- **Reframed intent (if applicable):** Not reframed. The goal is kept whole, but delivered as two child intents with
  disjoint touch sets and an explicit hand-over contract, because the children will be run by today's decomposition
  mechanism (the very one this intent fixes): each child is cut from the intent branch before its sibling has merged.
- **Exploration stance:** balanced. Most work is exploitation of known modules (`flow/stages/*`, `flow/github/*`,
  `entrypoint/executor.py`), but the merge-into-plan-branch and resume paths are new behaviour in a hermetic harness
  that has never exercised a real `gh pr merge`, so one exploratory information-gathering step is included.
- **Safety priority level:** elevated
- **Priority Justification:** The intent gives the flow engine, for the first time, the ability to merge a pull request
  itself. `docs/safety.md` section 1 ("Only human-approved intents merge to `main`") and section 5 ("Human review is the
  final gate"), and `holon-config/world/constraints.md` section 1 ("Only human operators can approve merges to the
  canonical `main` branch") make any bug in the merge guard a direct breach of the human boundary. Section 3 of the
  constraints (ledger immutability) also applies because ledger conflicts are resolved automatically.

## Exploration

- **Proportion of steps that are exploratory:** 0.1
- **Justification:** One of ten steps (Step 1) is pure information gathering to pin down existing contracts (how
  calibration lands in the PR, how `FlowContext.stage_results` round-trips through a checkpoint, how `gh` is faked in
  hermetic tests, and that `ledger_reconcile` does not yet interleave on `created_at`). Everything else is exploitation
  with tests; extra exploration would only add entropy to an already over-budget change.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.55  |
| entropy_pred        | 18.6  |
| impact_pred         | 85.0  |
| cost_pred           | 36.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 8.17  |

## Plan Description & Strategy

The flow already has the right rule (`should_decompose` in `entrypoint/executor.py`), the right naming
(`_child_intent_branch`) and the right building blocks (`create_or_update_pr`, `probe_ci_checks`,
`check_pr_mergeability`, `_append_superseding_execution_row`, `save_checkpoint` keyed on the child branch). What is
wrong is where the decision is stored, which branch the children grow from, and the missing tail of the lifecycle
(merge, combine, resume). The plan therefore:

1. Moves the decomposition rule into a neutral, non-entrypoint module so that the planner container, the executor
   container and the host flow all import the same function, and records its verdict on the plan's own `plans.jsonl` row
   (decision, entropy, entropy budget, `sub_intents`). The planner container writes these fields in the single row it
   already appends; Stage 2 back-fills a superseding plan row only when an older planner image left them out.
2. Makes Stage 3 read that verdict instead of recomputing it; the executor container records a `decomposed` summary row
   that references the plan's decision but carries no sub-intent list.
3. Cuts each child from the plan branch, names it under the plan (`{plan}--{n}-{slug}`), records the plan branch as its
   `target_branch` and `parent_intent_id`, and, after the child's Stage 4 consensus and Stage 5 calibration, merges its
   PR into the plan branch with a merge commit through a new guarded merge module that refuses any default-branch base.
   Child N plus 1 is cut only after child N merged.
4. After the last child, merges the plan branch into the parent execution branch (which already carries the parent plan
   and the parent execution record), appends a children summary row there, and lets the normal Stage 4 and Stage 5 run
   on that branch, so the combined PR (execution branch into the intent's `target_branch`) carries the parent plan, the
   parent execution record and every child's plan and execution record, and stops at the human merge boundary for a root
   intent.
5. Records per-child progress (pending, in progress, merged) in the parent Stage 3 payload, which the parent checkpoint
   already serialises, and makes a resumed decomposed flow continue the existing child from its own checkpoint, never
   re-dispatching the parent executor or a child that already has an execution branch or PR.

Head-branch choice for the combined PR: the parent **execution branch updated from the plan branch** (goal 5 allows
either). This keeps the existing invariant that every PR the flow raises is from an execution branch, so Stage 4
(`run_review_stage`) and Stage 5 (`run_calibrate_stage`) are reused unchanged instead of growing a second PR path; the
plan branch stays the children's integration surface.

Module placement respects `apps/sandbox-executor/flow_module_budgets.json`: `stages/decomposition.py` (242/242),
`stages/plan.py` (247/247), `stages/execute.py` (396/396), `pipeline.py` (394/394) and `checkpoint.py` (210/210) are all
at their budgets, so new behaviour goes into new focused modules, registered at creation size, and existing modules may
only stay level or shrink. No budget is raised.

Because the estimated entropy (18.6) exceeds the budget (15.0), the work is listed as two child intents at the end of
this plan. They are ordered and scoped so that each is independently mergeable even though today's flow cuts the second
from the intent branch before the first has merged.

---

## Step 1: Pin down the existing contracts the children rely on

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only, cheap and shared by both children; each child's planner repeats the relevant part.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The building blocks needed for merge, combine and resume already exist (calibration
  fast-forwards the calibrated branch into the execution branch, `FlowContext.to_dict` round-trips Stage 3 payloads,
  child checkpoints are keyed on the unique child branch), so no new infrastructure beyond focused modules is needed.
- **Learning target:** Exact shape of the `gh` fakes (`tests/gh_write_guard.py`, `tests/review_fakes.py`,
  `tests/flow/helpers.py`), whether `ledger_reconcile` can interleave rows on `created_at` (it currently performs an
  append-only union with a prefix side, so a helper is needed), and how Stage 4 chooses the PR base (`target_branch`
  from `intent_data`).
- **Maximum acceptable cost for this learning:** 1.5 cost units; abort exploration as soon as the four facts are known.

### Intent & Git Integration

- **Step Intent:** Confirm the contracts and record them in each child's plan so neither child guesses.
- **Git branch:** none (read-only, performed inside each child's planning pass)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Read `flow/stages/calibrate.py` around the `merge --ff-only` of `context.calibrated_branch` to confirm the calibration
  report is in the child PR before the merge step runs.
- Read `flow/context.py` `to_dict`/`from_dict` to confirm a list of dicts under `stage_results["execute"].payload` is
  preserved through `save_checkpoint` and `load_checkpoint` (the child-progress record relies on it).
- Read `flow/github/ci.py` (`probe_ci_checks`, `_ci_green`) and `flow/github/pr.py` (`check_pr_mergeability`,
  `create_or_update_pr`) for reusable signals; note `create_or_update_pr` already updates an existing PR for the same
  head, which is the duplicate-PR guard to rely on.
- Read the existing decomposition tests in `tests/flow/test_pipeline.py` and `tests/flow/helpers.py` to reuse their
  fixture repositories (bare `origin`, fake dispatcher) instead of inventing new ones.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 3 (test location), `docs/safety.md` section 1.
- **Potential failure modes for this step:** stale assumptions copied from this plan instead of re-read.
- **Guardrails and early‑abort checks:** read-only; no file is modified.

### Success & Discard Criteria

- **Success:** The four facts are written into the child plan's own step list.
- **Discard:** If calibration turns out not to land in the child PR, stop and add an explicit "merge calibrated branch
  into the execution branch" sub-step to Step 6 rather than assuming it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.3   |
| impact_pred         | 10.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 9.41  |

### Step Metrics Rationale

`EV = 0.95 * 10.0 + 0.5 * 3.0 - 0.3 * 0.3 - 1.5 = 9.50 + 1.50 - 0.09 - 1.50 = 9.41`. Low impact on its own but it
removes the main source of wrong assumptions for Steps 5 to 8.

---

## Step 2: Move the decomposition rule into one neutral module

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical move with re-exports; it is the precondition for "one rule, read from the plan row" and
  stops the planner container from importing the executor entrypoint.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make `should_decompose`, `parse_sub_intent_list`, `is_placeholder_item`, `DecompositionDecision` and
  the `DECISION_*` constants live in `apps/sandbox-executor/src/sandbox_executor/decomposition_rule.py`, imported by the
  planner container, the executor container and the flow.
- **Git branch:** child intent 1 execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `sandbox_executor/decomposition_rule.py` (outside `flow/`, so not under the flow line budgets, and outside
  `entrypoint/`) holding the rule and its parsing helpers verbatim, with module and function docstrings.
- In `entrypoint/executor.py` replace the definitions with imports from the new module and keep the names importable
  from `entrypoint.executor` (re-export) so existing tests and `tests/flow/fixtures/flow_py_definitions.json` move-proof
  checks keep passing; update the move-proof fixture if it records definition locations.
- In `flow/stages/plan.py` and `flow/stages/execute.py` import from `sandbox_executor.decomposition_rule` instead of
  `entrypoint.executor` (this removes flow-to-entrypoint imports; none are added).
- Add a `to_plan_fields()` helper on `DecompositionDecision` returning the plan-row fields (`decomposition_decision`,
  `entropy`, `entropy_budget`, and `sub_intents` only when decomposed), and a `from_plan_row(row)` reader returning a
  `DecompositionDecision` or `None` when the row predates the fields. Both sides of the hand-over therefore share one
  serialisation.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** Code placement rules in the intent (no new entrypoint imports into the flow engine),
  `holon-config/world/ruleset.md` section 2 (docstrings, typing).
- **Potential failure modes for this step:** a patch target in tests (`tests/flow/test_patch_targets.py`,
  `lookup_patch.py`) still patches `entrypoint.executor.should_decompose` and silently stops applying.
- **Guardrails and early‑abort checks:** run
  `uv run pytest apps/sandbox-executor/tests/flow/test_patch_targets.py apps/sandbox-executor/tests/flow/test_move_proof.py apps/sandbox-executor/tests/test_executor.py`
  right after the move.

### Success & Discard Criteria

- **Success:** One definition of `should_decompose` exists in the repository; all existing tests pass unchanged except
  for patch-target paths.
- **Discard:** If the move breaks more than a handful of patch targets, keep the definition in `entrypoint/executor.py`
  and only add the two plan-row helpers there (the rule is still single).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.5   |
| impact_pred         | 25.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 20.55 |

### Step Metrics Rationale

`EV = 0.90 * 25.0 + 0.5 * 2.0 - 0.3 * 1.5 - 2.5 = 22.50 + 1.00 - 0.45 - 2.50 = 20.55`. Entropy comes from touching the
largest module in the package (`entrypoint/executor.py`, 2236 lines) and its patch targets.

---

## Step 3: Record the decision on the plan row at Stage 2

- **Sub‑intent recommendation:** NO
- **Reasoning:** Core of goal 1; moderate risk because it changes a ledger row shape, but small and well-tested in
  isolation.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The plan branch is self-describing: its `plans.jsonl` row carries `decomposition_decision`
  (`execute`, `decomposed` or `over_budget`), `entropy`, `entropy_budget` and, when decomposed, `sub_intents` (each
  `{slug, description, goal}`).
- **Git branch:** child intent 1 execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `entrypoint/planner.py`, after the plan file is written and its metrics parsed, call
  `should_decompose(row, plan_content, intent_data)` and merge `to_plan_fields()` into the one plan row it already
  appends. No extra row in the normal case, so the ledger stays append-only with a single write.
- Create `flow/stages/plan_decision.py` (new, registered in `flow_module_budgets.json` at creation size) with:
  `recorded_plan_decision(context)` reading the plan row from the plan branch and returning the recorded decision; and
  `ensure_plan_decision(context, plan_row, plan_content)` which, when the row lacks `decomposition_decision` (an older
  planner image), appends a superseding plan row (`ledger_revision` incremented, same `plan_id`, `created_at` now) on
  the plan branch with the fields from the same rule, commits and pushes it, mirroring
  `_append_superseding_execution_row`.
- Move `_plan_budget_decision` and `_over_budget_failure` out of `stages/plan.py` into `plan_decision.py` (re-export or
  update the two callers), so `plan.py` shrinks enough to absorb the one call to `ensure_plan_decision` at the end of
  `run_plan_stage` and add `decomposition_decision` to the Stage 2 payload. `plan.py` must end at or below 247 lines;
  lower its budget if it shrinks.
- Update `docs/ledger_schema.md`: the Stage 2 plans row gains `decomposition_decision`, `entropy`, `entropy_budget`
  (already present) and `sub_intents`; describe the superseding back-fill row and that historic rows are never
  rewritten; change the Stage 3 decomposed row description (Step 4).
- Tests in new `tests/flow/stages/test_plan_decision.py`: fixture repository with a plan branch whose plan lists a
  `## Sub-intent list` asserts the row carries the decision and `sub_intents`; a within-budget plan records `execute`
  and no `sub_intents` (goal 8 g); an older row without fields gets exactly one superseding row appended, and earlier
  lines are byte-identical (append-only).

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES (goal 1 and the hand-over contract to child intent 2)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` section 3 (append-only ledgers), intent constraint "keep
  exactly the three append-only ledgers".
- **Potential failure modes for this step:** the back-fill row races with the planner container push; a superseding row
  is mis-read as a second plan by code that takes the first row for a `plan_id`.
- **Guardrails and early‑abort checks:** every reader takes the last row for a `plan_id` (already true in
  `run_plan_stage` and `_plan_budget_decision`); grep for readers that take the first row and fix or abort;
  `tests/test_ledger_append_only.py` must pass.

### Success & Discard Criteria

- **Success:** Plan rows carry the decision; ledger append-only tests pass; `plan.py` is at or under budget.
- **Discard:** If the planner container cannot be changed without rebuilding images in tests, keep only the flow-side
  back-fill (always one superseding row) and record that choice in `docs/ledger_schema.md`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 2.2   |
| impact_pred         | 55.0  |
| cost_pred           | 4.5   |
| learning_value_pred | 4.0   |
| ev_pred             | 41.94 |

### Step Metrics Rationale

`EV = 0.82 * 55.0 + 0.5 * 4.0 - 0.3 * 2.2 - 4.5 = 45.10 + 2.00 - 0.66 - 4.50 = 41.94`. Entropy reflects a ledger shape
change on two writers (planner container and flow back-fill).

---

## Step 4: Make Stage 3 only execute the recorded decision

- **Sub‑intent recommendation:** NO
- **Reasoning:** Completes goal 2 inside the same child as Step 3 so the rule is never applied twice in any merged
  state.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The executor container and the flow read the decision from the plan row; the executor's decomposed
  summary row references the plan decision and carries no `sub_intents`.
- **Git branch:** child intent 1 execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `entrypoint/executor.py` `main`, replace the direct `should_decompose(...)` call with `from_plan_row(plan_data)`,
  falling back to `should_decompose` only when the row predates the fields (same function, so one rule). For a
  decomposed plan, write the summary row with `status: "decomposed"`,
  `plan_decision: {plan_id, decision, entropy, entropy_budget, sub_intent_count}` and no `sub_intents` key; the commit
  message and action record say "acknowledged the plan's recorded decomposition" instead of "recorded the sub-intent
  list".
- In `flow/stages/execute.py`, the pre-dispatch check uses `recorded_plan_decision(context)`; on `decomposed`, call
  `_run_decomposed_plan` with a decision record built from the plan row (`sub_intents`, `entropy_budget`, `plan_branch`)
  instead of the executor summary row. The keyword contract of that record is the hand-over to child intent 2 and must
  not change: a dict with keys `sub_intents`, `entropy_budget` and `plan_branch`. The edit must keep `execute.py` at or
  below 396 lines (it replaces lines rather than adding).
- Keep the over-budget path identical (the defence-in-depth branch now reads the plan row decision too).
- Tests (goal 8 a) in new `tests/flow/stages/test_execute_plan_decision.py` and in `tests/test_executor.py`: the
  executor's decomposed summary row has no `sub_intents` and carries `plan_decision`; the flow runs exactly the plan
  row's list even when a crafted executor row carries a different list; a within-budget plan dispatches the executor and
  runs the normal path (goal 8 g).

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 2 ("one rule, read from the plan row, never two"); line budgets.
- **Potential failure modes for this step:** in-flight flows whose executor rows still carry `sub_intents` (historic
  executions) resume against the new code.
- **Guardrails and early‑abort checks:** a legacy test keeps a historic executor row with `sub_intents` and asserts the
  flow ignores it in favour of the plan row (or the rule applied to the plan file when the plan row is historic).

### Success & Discard Criteria

- **Success:** No code path reads `sub_intents` from `executions.jsonl`; targeted tests pass; budgets unchanged.
- **Discard:** If `execute.py` cannot stay within 396 lines, move the decomposed hand-over into `plan_decision.py`
  rather than raising the budget.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 2.2   |
| impact_pred         | 50.0  |
| cost_pred           | 4.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 36.34 |

### Step Metrics Rationale

`EV = 0.80 * 50.0 + 0.5 * 3.0 - 0.3 * 2.2 - 4.5 = 40.00 + 1.50 - 0.66 - 4.50 = 36.34`. The executor entrypoint's `main`
is long and already suppresses complexity rules, so edits there carry moderate entropy.

---

## Step 5: Add a guarded child-PR merge and a created_at ledger interleave

- **Sub‑intent recommendation:** YES
- **Reasoning:** It is the first code path in which the flow merges a PR itself, so it is the highest-safety unit; it is
  pure library code with no stage wiring and is reusable, which makes it the natural first half of child intent 2.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** One function merges a child PR into its plan branch with a merge commit, only when safe, and refuses
  any default-branch base.
- **Git branch:** child intent 2 execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `flow/github/merge.py` (new, budget entry at creation size) with
  `merge_child_pr(repo_dir, pr_number, head_branch, expected_base, proxy_env)` returning an outcome dict (`merged`,
  `refused`, `reason`, `merge_sha`).
- Order of checks: read `baseRefName`, `headRefName`, `state`, `mergeable` and `headRefOid` with `gh pr view --json`;
  refuse when the base equals `main`, `master` or the repository default branch (`gh repo view --json defaultBranchRef`;
  if that lookup fails, refuse, never assume), when the base differs from `expected_base`, or when the base contains no
  `/P-` segment (not a plan branch). The refusal check runs before any other `gh` write and is a separate pure function
  `refusal_reason(base, expected_base, default_branch)` so it can be unit tested exhaustively.
- If the PR is `BEHIND` or `CONFLICTING`, bring the plan branch into the child execution branch: fetch,
  `git merge --no-ff origin/<plan branch>` in the flow's target worktree, and resolve conflicts only when every
  conflicted path is a ledger under `holon-knowledge/ledger/`, using a new `interleave_ledger_rows_by_created_at` in a
  new module `flow/ledger_interleave.py` (parse both sides with `extract_valid_ledger_rows`, keep the common prefix,
  then merge the two tails ordered by `created_at` with a stable tie-break of ours-before-theirs, dedupe identical
  lines). Any non-ledger conflict aborts the merge and returns `refused` with the paths. Push, then wait for CI.
- Require CI green via `probe_ci_checks` and `_ci_green`, and `mergeable == MERGEABLE`; then
  `gh pr merge <n> --merge --match-head-commit <sha>` (never `--squash`, `--rebase` or `--admin`). Verify afterwards
  with `git merge-base --is-ancestor <child head> origin/<plan branch>`.
- Tests in new `tests/flow/github/test_merge.py` (goal 8 d): a fake `gh` (reuse `tests/gh_write_guard.py` style) and a
  fixture repository with a bare origin; assert a merge commit with two parents lands on the plan branch and the child
  head is an ancestor; assert refusal for base `main`, for base equal to the fake default branch, for an unexpected base
  and when the default-branch lookup fails, and that no `gh pr merge` call was recorded in those cases; assert a
  ledger-only conflict is interleaved by `created_at` and a code conflict is refused. Tests for the interleave helper
  live in `tests/flow/test_ledger_interleave.py`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (goals 4 and 5 depend on it)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` sections 1 and 5; `holon-config/world/constraints.md` sections 1 (only humans
  merge to `main`, rebase discipline) and 3 (ledger immutability); intent constraint "the combined pull request into
  main is merged only by the human maintainer".
- **Potential failure modes for this step:** a refusal check that compares a short name against a full ref
  (`refs/heads/main`); a ledger interleave that reorders or drops historic rows; a hermetic test that accidentally
  reaches real GitHub.
- **Guardrails and early‑abort checks:** normalise branch names before comparison; the interleave asserts the result
  contains every input row and keeps the common prefix byte-identical; tests run under the existing hermetic guard
  (`tests/test_sandbox_hermetic_guard.py`, `tests/gh_write_guard.py`).

### Success & Discard Criteria

- **Success:** Merge and refusal tests pass; the module has no `noqa` complexity suppressions.
- **Discard:** If CI-green probing cannot be faked hermetically within 1.5x the step cost, inject the CI probe as a
  parameter and test the merge with a stub rather than widening the fake.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 2.0   |
| impact_pred         | 55.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 41.40 |

### Step Metrics Rationale

`EV = 0.80 * 55.0 + 0.5 * 4.0 - 0.3 * 2.0 - 4.0 = 44.00 + 2.00 - 0.60 - 4.00 = 41.40`. New, isolated module, so entropy
is moderate; impact is high because it is the safety-critical gate.

---

## Step 6: Nest children under the plan branch and merge each before cutting the next

- **Sub‑intent recommendation:** YES
- **Reasoning:** The bottleneck rewrite of the child loop; it is grouped with Steps 5, 7, 8 and 9 in child intent 2
  because they all touch the same child runner and would conflict as siblings.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Each child is created from the plan branch, named under it, targets it, and is merged into it before
  the next child is cut.
- **Git branch:** child intent 2 execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Split `flow/stages/decomposition.py` (242/242): keep the summary formatting and depth helpers there, and move the
  child loop into a new `flow/stages/children.py` (budget entry at creation size). Keep `PipelineEngine` bound late as
  today (`flow/__init__.py` sets it; `tests/flow/test_package_exports.py` checks it) and update that binding to the new
  module.
- The child loop consumes the decision record contract from Step 4 (`sub_intents`, `entropy_budget`, `plan_branch`).
  `parent_branch` becomes `context.plan_branch`; `_child_intent_branch(plan_branch, index, slug)` keeps the
  deterministic `{plan without /_}--{n}-{slug}` naming, so a child is `I-.../P-...--1-slug/_`; the child payload records
  `target_branch` and `parent_intent_id` equal to the plan branch.
- Make Stage 1 for a child cut the child intent branch from `origin/<plan branch>` tip (pass a `base_branch` in the
  child's `intent_data`, read by `stages/intent.py` when creating the branch; if `intent.py` cannot absorb it within its
  204-line budget, create the branch in `children.py` before the child pipeline runs and let Stage 1 adopt it).
- After a child pipeline ends with Stage 4 `HALTED_FOR_HUMAN` (consensus) and Stage 5 success, call `merge_child_pr`
  with `expected_base` the plan branch; on success fetch the plan branch and record the merge SHA. Only then cut the
  next child, so child N plus 1 contains child N. A refusal or failed merge fails Stage 3 naming the child, after
  recording its progress (Step 8).
- Remove the "merge each child PR into the parent intent branch" and "not yet automated" summary text from
  `format_decomposition_summary`; list each child with its intent branch, execution branch, PR and merge state.
- Tests in new `tests/flow/stages/test_children.py` with fixture repositories and a fake dispatcher that commits a file
  per child: every child branch has the parent plan tip as an ancestor and its PR base is the plan branch (goal 8 b);
  child 2's branch contains child 1's file and was created after child 1's merge commit (goal 8 c).

### Dependencies & Criticality

- **Depends on:** Step 5; consumes the Step 4 contract (but tests feed the decision record directly, so child intent 2
  does not need child intent 1's code on its branch)
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` section 1 (prefix isolation, rebase before merge);
  `docs/git_flow.md` section 1.3; flow line budgets.
- **Potential failure modes for this step:** a child that itself decomposes (depth 2) has no PR of its own until its
  combined PR exists; Step 7 makes that combined PR the child's PR so the same merge applies; Stage 1 intent creation
  cutting from the wrong base silently.
- **Guardrails and early‑abort checks:** an assertion before dispatching each child that `origin/<plan branch>` is an
  ancestor of the new child branch; a depth-2 test uses the existing `MAX_SUB_INTENT_DEPTH` refusal unchanged.

### Success & Discard Criteria

- **Success:** Goal 8 b and c tests pass; no module exceeds its budget.
- **Discard:** If cumulative cost reaches 1.5x of Steps 5 to 6 combined, stop child intent 2 after Step 6 and record
  Steps 7 and 8 as a follow-up intent; the result is still strictly better than today.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.70  |
| entropy_pred        | 3.0   |
| impact_pred         | 70.0  |
| cost_pred           | 5.5   |
| learning_value_pred | 5.0   |
| ev_pred             | 45.10 |

### Step Metrics Rationale

`EV = 0.70 * 70.0 + 0.5 * 5.0 - 0.3 * 3.0 - 5.5 = 49.00 + 2.50 - 0.90 - 5.50 = 45.10`. Highest-entropy step: it
restructures a recursive engine path across a module split and the Stage 1 base branch.

---

## Step 7: Raise the combined pull request from the parent execution branch

- **Sub‑intent recommendation:** YES
- **Reasoning:** Gives decomposed flows a route to `main`; reuses Stage 4 and Stage 5 rather than a new PR path, but
  changes the pipeline's skip rule, so it belongs with the child runner in child intent 2.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** After the last child merged, the parent execution branch is updated from the plan branch and the
  normal review loop and calibration run on the combined diff, ending at the human merge boundary for a root intent.
- **Git branch:** child intent 2 execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `flow/stages/integration.py` (new, budget entry at creation size) with
  `prepare_combined_head(context, execute_result)`: fetch, merge `origin/<plan branch>` into the parent execution branch
  with `--no-ff` (ledger-only conflicts via the Step 5 interleave), then append a superseding executions summary row
  (`status: "decomposed"`, `plan_decision` reference, `children` list of
  `{slug, intent_branch, execution_branch, pr_url, pr_number, merge_sha}`) using `_append_superseding_execution_row`,
  and push. Assert that the parent plan file, the parent execution row and each child's `plans/P-*.md` and executions
  row are present on the resulting head; fail Stage 3 if not.
- In `pipeline.py`, replace the `_parent_was_decomposed` skip of Stage 4 and Stage 5 with a call to
  `prepare_combined_head` once Stage 3 succeeded decomposed; Stage 4 then opens the PR from the execution branch into
  `intent_data["target_branch"]` (`main` for a root intent, the grandparent plan branch for a depth-1 parent, which the
  grandparent's child runner then merges through Step 5). Keep `pipeline.py` at or under 394 lines by moving the removed
  skip helper out.
- The flow never merges the combined PR when its base is a default branch: `merge_child_pr` would refuse it, and the
  pipeline does not call it for a root intent. Stage 4 halts for the human exactly as for a non-decomposed root.
- `format_decomposition_summary` lists every child PR and the combined PR (taken from the parent Stage 4 payload).
- Test (goal 8 e) in new `tests/flow/stages/test_integration.py`: fixture repository with two merged children; assert
  the combined PR head contains the parent plan, the parent execution record and both child plans and execution records,
  the PR base is `main`, the result is `HALTED_FOR_HUMAN`, and no merge call targeted `main`.

### Dependencies & Criticality

- **Depends on:** Steps 5 and 6
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` "Human review boundaries" item 1; intent constraint that only the maintainer
  merges into `main`.
- **Potential failure modes for this step:** Stage 4's empty-diff or mergeability guards misjudging an execution branch
  whose own commits are ledger-only; the review loop treating merge commits oddly.
- **Guardrails and early‑abort checks:** the integration test runs Stage 4 with the existing review fakes so the
  empty-diff guard is exercised against the merged head.

### Success & Discard Criteria

- **Success:** Goal 8 e passes; a within-budget plan's pipeline path is byte-for-byte unchanged (existing pipeline tests
  pass untouched).
- **Discard:** If Stage 4 cannot accept the merged execution branch without changes to `stages/review.py`, switch the
  head to the plan branch with a dedicated PR call in `integration.py` (the alternative goal 5 allows), and record why.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.68  |
| entropy_pred        | 2.6   |
| impact_pred         | 65.0  |
| cost_pred           | 4.5   |
| learning_value_pred | 5.0   |
| ev_pred             | 41.42 |

### Step Metrics Rationale

`EV = 0.68 * 65.0 + 0.5 * 5.0 - 0.3 * 2.6 - 4.5 = 44.20 + 2.50 - 0.78 - 4.50 = 41.42`. Reusing Stage 4 and Stage 5 keeps
entropy below a dedicated PR path, but the interaction with the review guards is untested territory.

---

## Step 8: Make a resumed decomposed flow continue, never repeat

- **Sub‑intent recommendation:** YES
- **Reasoning:** Fixes the observed duplicate-PR failure; it shares the child runner with Steps 6 and 7, so it stays in
  child intent 2.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The parent checkpoint records which children are merged, in progress or pending, and a resume
  continues the in-progress child from its own checkpoint without re-dispatching anything that already exists.
- **Git branch:** child intent 2 execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `flow/child_progress.py` (new, budget entry at creation size) owning the progress record stored in the parent
  Stage 3 payload under `children` (each `{index, slug, intent_branch, execution_branch, pr_number, state}` with `state`
  one of `pending`, `in_progress`, `merged`, `failed`). The child runner saves the parent checkpoint after every state
  change, so `checkpoint.py` does not grow (the payload already round-trips through `FlowContext.to_dict`).
- On a parent resume at Stage 3 with a prior decomposed payload whose `execution_branch` exists on origin, `pipeline.py`
  calls the child runner's resume entry instead of `run_execute_stage`, so the parent executor is never re-dispatched.
- For each child: `merged` is skipped (verified by ancestry of its head in the plan branch); `in_progress` or `failed`
  loads that child's checkpoint (`get_default_checkpoint_path` keyed on the child branch), rebuilds its `FlowContext`
  from it and runs `PipelineEngine(child, from_stage=<checkpoint current_stage>, checkpoint_path=<same path>)`; a child
  with an execution branch under its plan, or with an open or merged PR for its head
  (`gh pr list --head <branch> --state all`), is never sent back to Stage 1 to 3. Missing or unreadable child checkpoint
  with existing branches fails Stage 3 loudly instead of guessing.
- Child checkpoints are written only by that child's own engine; the runner asserts the checkpoint path it resumes is
  the one it reads, and never constructs a fresh context for an existing child branch.
- Test (goal 8 f) in new `tests/flow/stages/test_children_resume.py`: three children, child 2's Stage 4 fails; resume;
  assert child 1 is not re-run, child 2 continues at Stage 4 with its original execution branch, `create_or_update_pr`
  updates and does not create (fake `gh` records exactly one PR per head), child 1's and child 3's checkpoint files are
  byte-identical to before the resume except where child 3 legitimately starts, and the parent executor was dispatched
  once in total.

### Dependencies & Criticality

- **Depends on:** Steps 6 and 7
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 6; `docs/safety.md` failure containment (execution failure, rebase conflict).
- **Potential failure modes for this step:** old-format parent checkpoints without `children` progress; resuming a
  parent whose child merged on GitHub but whose checkpoint still says `in_progress`.
- **Guardrails and early‑abort checks:** progress is reconciled against origin (ancestry and PR state) before acting,
  origin wins; a checkpoint without progress derives it from origin branches under the plan prefix
  (`_list_child_branches`) and never re-dispatches a branch that exists.

### Success & Discard Criteria

- **Success:** Goal 8 f passes; checkpoint schema version unchanged (new data lives in the stage payload).
- **Discard:** If deriving progress from origin for legacy checkpoints exceeds the step cost by 1.5x, fail such resumes
  with a clear message instead of supporting them.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.65  |
| entropy_pred        | 3.0   |
| impact_pred         | 65.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 39.35 |

### Step Metrics Rationale

`EV = 0.65 * 65.0 + 0.5 * 6.0 - 0.3 * 3.0 - 5.0 = 42.25 + 3.00 - 0.90 - 5.00 = 39.35`. Lowest success probability: state
reconciliation across two checkpoint levels and origin is error-prone, and learning value is highest here.

---

## Step 9: Rewrite the decomposition docs to the new behaviour

- **Sub‑intent recommendation:** NO
- **Reasoning:** Documentation only, but it must remove every "until the implementation follows" interim note, so it
  ships in child intent 2 with the behaviour it describes.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** `docs/architecture.md`, `docs/git_flow.md` (and the matching interim notes in `docs/safety.md`)
  describe plan-owned decisions, plan-branch children, automated child merges and the combined PR.
- **Git branch:** child intent 2 execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- `docs/architecture.md`: remove the interim paragraph in "Branch topology and authority boundaries" and the matching
  note near line 41; add the new modules (`plan_decision.py`, `children.py`, `integration.py`, `child_progress.py`,
  `ledger_interleave.py`, `github/merge.py`) to the module table with one-line purposes; update the late-binding note.
- `docs/git_flow.md`: update sections 1.3 and the Stage 3 entry under the flow equivalence guarantee: children are cut
  from the plan branch after the previous child merged, PRs target the plan branch and are merged with a merge commit by
  the engine, which refuses default-branch bases; the combined PR is raised from the parent execution branch; resume
  behaviour.
- `docs/safety.md`: remove the three interim sentences (sections 5 and "Human review boundaries") and state the merge
  guard.
- Australian English throughout ("behaviour", "normalise"); run `uv run task lint-docs`.

### Dependencies & Criticality

- **Depends on:** Steps 6 to 8
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 7 and constraint on Australian English.
- **Potential failure modes for this step:** prettier oscillation on formulas or underscores; stale cross-references.
- **Guardrails and early‑abort checks:** grep docs for "parent intent branch" and "not yet automated" and "Until the
  implementation follows" after editing; zero hits expected outside historic plans.

### Success & Discard Criteria

- **Success:** `uv run task lint-docs` passes and the greps are clean.
- **Discard:** none; documentation is required for acceptance.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.2   |
| impact_pred         | 25.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 20.39 |

### Step Metrics Rationale

`EV = 0.88 * 25.0 + 0.5 * 1.5 - 0.3 * 1.2 - 2.0 = 22.00 + 0.75 - 0.36 - 2.00 = 20.39`.

---

## Step 10: Validate each child: affected tests, full suite once, lint and budgets

- **Sub‑intent recommendation:** NO
- **Reasoning:** Final gate repeated at the end of each child; no timing behaviour is measured, so no repeated runs.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the acceptance criteria hermetically and keep the repository gates green.
- **Git branch:** each child intent's execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Child intent 1:
  `uv run pytest apps/sandbox-executor/tests/flow/stages/test_plan_decision.py apps/sandbox-executor/tests/flow/stages/test_execute_plan_decision.py apps/sandbox-executor/tests/test_executor.py apps/sandbox-executor/tests/test_planner.py apps/sandbox-executor/tests/test_ledger_append_only.py`.
- Child intent 2: `uv run pytest apps/sandbox-executor/tests/flow` (covers merge, interleave, children, integration,
  resume, package exports, module budgets).
- Then the full suite once with `uv run task test`, then `uv run task lint` (ruff complexity limits, no new `noqa`,
  `scripts/check_flow_module_budgets.py`) and `uv run task lint-docs`.
- Confirm `flow_module_budgets.json` only gained entries for new modules at creation size and lowered entries for
  modules that shrank; no entry was raised.

### Dependencies & Criticality

- **Depends on:** Steps 2 to 4 (child intent 1); Steps 5 to 9 (child intent 2)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent constraint "tests strictly via 'uv run pytest'"; hermetic testing guide
  (`apps/sandbox-executor/docs/hermetic_testing.md`).
- **Potential failure modes for this step:** a test that shells out to real `gh` or the network.
- **Guardrails and early‑abort checks:** the hermetic guard tests must stay green; any network access fails the step.

### Success & Discard Criteria

- **Success:** All commands exit 0; pass counts reported in the execution record.
- **Discard:** Two consecutive full-suite failures with unrelated root causes: stop and record them rather than widening
  scope.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 0.6   |
| impact_pred         | 30.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 22.82 |

### Step Metrics Rationale

`EV = 0.80 * 30.0 + 0.5 * 2.0 - 0.3 * 0.6 - 2.0 = 24.00 + 1.00 - 0.18 - 2.00 = 22.82`.

---

## Sub-intent list

1. Record the decomposition decision on the plan row and make Stage 3 execute only that recorded decision (Steps 1 to 4
   and 10 of the parent plan).
   - Move `should_decompose`, `parse_sub_intent_list`, `DecompositionDecision` and the `DECISION_*` constants into a new
     neutral module `sandbox_executor/decomposition_rule.py`, re-exported from `entrypoint/executor.py`; add
     `to_plan_fields()` and `from_plan_row()` so writer and readers share one serialisation.
   - The planner container (`entrypoint/planner.py`) writes `decomposition_decision`, `entropy`, `entropy_budget` and,
     when decomposed, `sub_intents` (`{slug, description, goal}`) into the plan row it already appends; a new
     `flow/stages/plan_decision.py` back-fills one superseding plan row only when an older planner image omitted them,
     and takes `_plan_budget_decision` and `_over_budget_failure` out of `stages/plan.py`.
   - The executor container reads the decision from the plan row; its decomposed summary row carries a `plan_decision`
     reference and no `sub_intents`. `stages/execute.py` passes the plan row's decision record (keys `sub_intents`,
     `entropy_budget`, `plan_branch`, unchanged contract) to `_run_decomposed_plan`.
   - Do not touch `stages/decomposition.py`, `pipeline.py`, `checkpoint.py`, `flow/github/`, `docs/architecture.md`,
     `docs/git_flow.md` or `docs/safety.md` (they belong to the second sub-intent, which is cut without this one).
   - Update `docs/ledger_schema.md`; add hermetic tests for goal 8 a and g; no line budget may be raised.
   - Validate with the targeted `uv run pytest` paths, then `uv run task test` once, `uv run task lint` and
     `uv run task lint-docs`.
2. Nest decomposed children under the plan branch, merge each child PR into it, raise the combined PR and resume safely
   (Steps 1 and 5 to 10 of the parent plan).
   - New `flow/github/merge.py`: guarded `merge_child_pr` (merge commit only, CI green and mergeable, brings the plan
     branch into the child first) with a pure refusal check that rejects `main`, `master`, the repository default
     branch, an unexpected base or a failed default-branch lookup; new `flow/ledger_interleave.py` interleaving
     ledger-only conflicts on `created_at`.
   - Split the child loop out of `stages/decomposition.py` into new `flow/stages/children.py`: cut each child from the
     plan branch tip after the previous child merged, keep `{plan}--{n}-{slug}` naming, record the plan branch as
     `target_branch` and `parent_intent_id`, and merge each child after Stage 4 consensus and Stage 5 calibration.
   - New `flow/stages/integration.py`: after the last child, merge the plan branch into the parent execution branch,
     append a superseding executions summary row listing the children, and let the normal Stage 4 and Stage 5 raise and
     review the combined PR into the intent's `target_branch`, halting for the human when that is `main`.
   - New `flow/child_progress.py`: per-child progress in the parent Stage 3 payload, reconciled against origin on
     resume; never re-dispatch the parent executor or a child that has an execution branch or an open or merged PR;
     resume an existing child from its own checkpoint.
   - Consume the decision record contract (`sub_intents`, `entropy_budget`, `plan_branch`) directly in tests, so this
     sub-intent does not depend on the first one's code being present on its branch; do not edit `stages/execute.py`,
     `stages/plan.py`, `entrypoint/` or `docs/ledger_schema.md`.
   - Update `docs/architecture.md`, `docs/git_flow.md` and `docs/safety.md`, removing every interim note about cutting
     children from or targeting the parent intent branch.
   - Add hermetic tests for goal 8 b, c, d, e and f, including the test proving refusal of a PR whose base is `main`;
     register new modules in `flow_module_budgets.json` at creation size and raise no budget; validate with
     `uv run pytest apps/sandbox-executor/tests/flow`, then `uv run task test` once, `uv run task lint` and
     `uv run task lint-docs`.

---

### Strategy Rationale

**Why SPLIT.** The intent covers nine goals across two containers, five stage modules and the GitHub layer. Summing the
step entropies gives `S = 0.3 + 1.5 + 2.2 + 2.2 + 2.0 + 3.0 + 2.6 + 3.0 + 1.2 + 0.6 = 18.6`, above the 15.0 budget, so
the plan cannot honestly be executed as one pass. Splitting into more than two children was rejected after reading the
code: today's flow (the behaviour this intent fixes) cuts every child from the intent branch before the previous child
has merged, so siblings cannot build on each other. The split was therefore drawn along the only seam with disjoint
touch sets: decision ownership (planner, executor, `plan.py`, `execute.py`, `ledger_schema.md`) versus the child
lifecycle (`decomposition.py`, new runner, merge, integration and progress modules, `pipeline.py`, architecture and
git-flow docs). The two meet through one unchanged dict contract (`sub_intents`, `entropy_budget`, `plan_branch`), which
already exists today because `_run_decomposed_plan` reads exactly those keys from the executor row. Each child can be
reviewed and merged on its own, in either order, with at most a trivial `flow_module_budgets.json` conflict. Further
splitting the lifecycle child (merge primitive versus runner) would create siblings where the second needs the first's
code, which today's flow cannot provide.

**Child entropy.** Child intent 1 is about `0.15 + 1.5 + 2.2 + 2.2 + 0.3 = 6.35`; child intent 2 is about
`0.15 + 2.0 + 3.0 + 2.6 + 3.0 + 1.2 + 0.3 = 12.25`. Both are within budget, so neither is expected to decompose again
(depth 2 is still allowed if child intent 2's own planner disagrees).

**Overall metric derivation.**

- `entropy_pred` = sum of step entropies = 18.6.
- `cost_pred` = sum of step costs: `1.5 + 2.5 + 4.5 + 4.5 + 4.0 + 5.5 + 4.5 + 5.0 + 2.0 + 2.0 = 36.0`.
- `p_success_pred` = bottleneck-based judgement, not a product: the chain of bottleneck steps 6, 7 and 8 (0.70, 0.68,
  0.65) is correlated (same runner, same fixtures), so the joint estimate is about 0.55 rather than the independent
  product `0.70 * 0.68 * 0.65 = 0.31`; child intent 1 (about 0.8) is not the bottleneck.
- `impact_pred` = 85.0: decomposed flows become finishable and safe to resume, which unblocks every future over-budget
  intent; not 100 because a root PR still needs the human and the mechanism is unproven end to end.
- `learning_value_pred` = 6.0: first engine-owned merges and two-level resume reconciliation.
- `ev_pred`: `EV = 0.55 * 85.0 + 0.5 * 6.0 - 0.3 * 18.6 - 36.0 = 46.75 + 3.00 - 5.58 - 36.00 = 8.17`. Positive but
  modest, dominated by cost; the summed step EVs (about 319) overstate the whole because the step impacts overlap.

**Strong assumptions.** Calibration already fast-forwards the report into the child's execution branch (seen at
`flow/stages/calibrate.py` line 290); Stage 4 accepts an execution branch that contains merge commits; hermetic `gh`
fakes can model `pr view`, `pr list`, `pr merge` and `repo view`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 1: only humans approve merges to `main`; sub-intents merge into their
    immediate parent; rebase (here: bring the parent in) before merging.
  - `holon-config/world/constraints.md` section 3: append-only ledgers.
  - `docs/safety.md` section 1 (Git is the safety boundary), section 5 and "Human review boundaries" item 1.
  - `holon-config/world/ruleset.md` sections 2 and 3: typing, docstrings, tests in `tests/`.
  - Intent constraints: no new dependencies, Australian English, `uv run pytest` only, flow line budgets, three ledgers
    only, target-native paths.
- **Potential violations or edge cases:**
  - The engine merging a PR into `main` through a bug in base comparison or a mis-set `target_branch`.
  - Automatic ledger conflict resolution reordering or dropping historic rows.
  - The plan's own two children being run by today's flow: they target this intent's branch, are not merged
    automatically, and a resume of this decomposed flow could reproduce the duplicate-PR bug before child intent 2
    lands.
  - A superseding plan row (back-fill) misread as a second plan.
- **Mitigations built into the plan:**
  - A pure, exhaustively tested refusal function runs before any `gh` write; default-branch lookup failure means refuse;
    the root combined PR is never passed to the merge function; a dedicated test proves refusal of base `main`.
  - Merge commits only (`--merge --match-head-commit`), never squash, rebase or admin; ancestry verified afterwards.
  - Interleave keeps the common prefix byte-identical and asserts every input row survives; code conflicts abort.
  - Children of this plan have disjoint touch sets and a fixed dict contract, so each can be merged by the maintainer
    into the intent branch in either order.
  - Operator note for this run: do not `--resume` this decomposed flow while child intent 2 is unmerged; if a child
    fails, re-run that child alone rather than resuming the parent.
- **Residual risk accepted (and why):** End-to-end behaviour against real GitHub (merge queue, branch protection on plan
  branches) is only covered by fakes; accepted because hermetic tests are mandated and the first real decomposed run
  after merge will be observed by the maintainer at the combined PR.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 18.6
- **Budget Compliance:** The strategy exceeds the budget as a single plan (18.6 against 15.0), so it carries a
  `## Sub-intent list` of two children, each within budget (about 6.35 and 12.25).
