# Plan for I-1794224634-plan-owned-decomposition-with-plan-branch-children--1-record-the-decomposition-decision-on-the-plan-ro

- **Plan ID:** P-1794225025-924685ae-claude-agent-claude-opus-5-5
- **Parent Intent ID:** I-1794224634-plan-owned-decomposition-with-plan-branch-children/\_
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-09T11:50:26.028Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None. Two clarifications of execution detail are made (not a reframe): the
  back-fill of an older planner's plan row happens inside the Stage 3 pre-dispatch decision read (not in Stage 2),
  because `pipeline.py` imports `_format_entropy` from `stages/plan.py`, `pipeline.py` is out of scope, and a Stage 2
  call into `plan_decision.py` would create an import cycle; and the move-proof snapshot
  (`tests/flow/fixtures/flow_py_definitions.json`) is amended with recorded reasons, which the intent does not name but
  the existing `test_move_proof.py` requires for any new or changed flow definition.
- **Exploration stance:** conservative, refactor_heavy. The work is a relocation of one rule plus a change of who
  records its output; the risk is in breaking the many existing hermetic tests and line budgets, not in unknowns, so
  every step exploits known code paths and keeps behaviour for in-budget plans byte-identical.
- **Safety priority level:** elevated
- **Priority Justification:** `holon-config/world/constraints.md` section 3 (append-only ledgers): the flow will now
  append a superseding row to `plans.jsonl` on a plan branch, which must be a pure append under `LedgerLock` and
  `assert_ledger_prefix`, never a rewrite of the planner's row. Section 1 (commit boundaries): the back-fill pushes only
  to the active plan branch, never to `main` or another intent's branch. The executor container and the flow engine are
  core modules (constraints section 4), so a deep model is used.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The parent plan already fixed the design for Steps 1-4 and 10; the code paths (`should_decompose`,
  `_append_superseding_execution_row`, the executor's skip-agent branch, `_run_decomposed_plan`'s `summary_row`
  contract) were read during planning. Learning value comes from the first real use of a flow-written plan row, which is
  measured by the tests, not by exploration.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.74  |
| entropy_pred        | 13.0  |
| impact_pred         | 80.0  |
| cost_pred           | 15.0  |
| learning_value_pred | 5.0   |
| ev_pred             | 42.80 |

## Plan Description & Strategy

Today the executor container (`entrypoint/executor.py`) owns `should_decompose`, re-decides the split at Stage 3 and
writes the `sub_intents` list into its `decomposed` summary row, which `stages/execute.py` hands to
`_run_decomposed_plan`. The flow also calls the same function before dispatch (`stages/plan.py::_plan_budget_decision`),
importing it from the entrypoint. This plan makes the plan row the single record of the decision:

1. Move the rule into a neutral `sandbox_executor/decomposition_rule.py` (no entrypoint imports), re-exported from
   `entrypoint/executor.py`, and give `DecompositionDecision` one serialisation (`to_plan_fields()` and
   `from_plan_row()`).
2. The planner container computes the decision on the formatted plan and writes it into the row it already appends.
3. A new `flow/stages/plan_decision.py` owns the flow side: it reads the recorded decision, back-fills one superseding
   plan row only when the row predates the new planner image, and hosts the moved `_plan_budget_decision` and
   `_over_budget_failure`.
4. The executor container reads the decision from the plan row (last matching row, so a back-filled row wins) and its
   `decomposed` summary carries a `plan_decision` reference and no `sub_intents`.
5. `stages/execute.py` passes the plan row's decision record (`sub_intents`, `entropy_budget`, `plan_branch`) to
   `_run_decomposed_plan`, whose contract is unchanged, so `stages/decomposition.py` is untouched.
6. Hermetic tests cover goal 8 (a) and (g); `docs/ledger_schema.md` documents the new fields.

Touch set (module granularity): `sandbox_executor/decomposition_rule.py` (new), `entrypoint/executor.py`,
`entrypoint/planner.py`, `flow/stages/plan_decision.py` (new), `flow/stages/plan.py` (shrinks), `flow/stages/execute.py`
(net zero lines), `flow/__init__.py` (re-export source only, net zero lines),
`apps/sandbox-executor/flow_module_budgets.json` (new entry at creation size, `stages/plan.py` lowered),
`tests/flow/fixtures/flow_py_definitions.json` (amended keys), `tests/flow/helpers.py`,
`tests/test_decomposition_rule.py` (new), `tests/flow/stages/test_plan_decision.py` (new), `tests/test_planner.py`,
`tests/test_executor.py`, `tests/flow/stages/test_decomposition.py`, `tests/flow/stages/test_execute.py`,
`docs/ledger_schema.md`. Explicitly not touched: `stages/decomposition.py`, `pipeline.py`, `checkpoint.py`,
`flow/github/`, `docs/architecture.md`, `docs/git_flow.md`, `docs/safety.md`.

---

## Step 1: Move the decomposition rule into the neutral module `sandbox_executor/decomposition_rule.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** A behaviour-preserving move guarded by an existing ~400-line regression corpus in
  `tests/test_executor.py`; small, low-risk and on the critical path of every later step, so splitting it off would only
  add a branch and a review cycle.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Give the flow engine and both containers one importable, entrypoint-free home for the rule and one
  serialisation of its result.
- **Git branch:** execution branch of this plan (`I-.../P-1794225025-.../E-.../_`)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `apps/sandbox-executor/src/sandbox_executor/decomposition_rule.py` with a module docstring. Move verbatim from
   `entrypoint/executor.py`: `DECISION_EXECUTE`, `DECISION_DECOMPOSED`, `DECISION_OVER_BUDGET`, `_SUB_INTENT_HEADINGS`,
   `_HEADING_RE`, `_LIST_ITEM_RE`, `_PLACEHOLDER_RE`, `_PLACEHOLDER_WRAPPERS`, `_FENCE_OPEN_RE`, `_FENCE_CLOSE_RE`,
   `_THEMATIC_BREAK_RE`, `_SETEXT_UNDERLINE_RE`, `_MAX_LIST_INDENT`, `DecompositionDecision`, `_normalise_heading`,
   `_normalise_placeholder_item`, `is_placeholder_item`, `_paragraph_state`, `_setext_level`, `parse_sub_intent_list`
   and `should_decompose`. Imports: `re`, `dataclasses`, `typing.Any`, and `DEFAULT_ENTROPY_BUDGET` plus
   `resolve_entropy_budget` from `sandbox_executor.budgets` only (never from `entrypoint`).
2. `should_decompose` uses the executor's private `_safe_float`; add a private `_as_float(value, default)` with the same
   semantics in the new module (do not import the entrypoint), and leave `_safe_float` in `executor.py` for its other
   callers.
3. `parse_sub_intent_list` keeps its existing `# noqa: C901, PLR0912, PLR0915` because it is moved, not written; no new
   suppression is introduced anywhere and the function body is not edited (the regression corpus is the proof).
4. Add to `DecompositionDecision`:
   - `to_plan_fields() -> dict[str, Any]`: returns `decomposition_decision`, `entropy`, `entropy_budget`, and
     `sub_intents` (a list of `{slug, description, goal}` copies) only when the decision is `decomposed`.
   - `from_plan_row(row) -> DecompositionDecision | None` (classmethod): returns `None` when the row has no
     `decomposition_decision` key (older planner image); raises a `ValueError` subclass `InvalidPlanDecisionError` when
     the key is present but the value is not one of the three constants, when `decomposed` carries no non-empty list of
     dicts each with string `slug`, `description` and `goal`, or when a non-decomposed row carries `sub_intents`;
     otherwise rebuilds the decision with `entropy` and `entropy_budget` coerced via `_as_float` and
     `resolve_entropy_budget(row, None)`.
   - Round-trip invariant: `from_plan_row(decision.to_plan_fields()) == decision` for every decision `should_decompose`
     can return.
5. In `entrypoint/executor.py`, delete the moved definitions and import them back from
   `sandbox_executor.decomposition_rule` (an explicit import list, re-exported so `executor.should_decompose`,
   `executor.parse_sub_intent_list`, `executor.is_placeholder_item`, `executor.DecompositionDecision` and
   `executor.DECISION_*` keep working for `tests/test_executor.py` and `tests/flow/helpers.py`). Keep
   `OVER_BUDGET_EXIT_CODE` in the executor (it is a container exit contract).
6. Run `uv run pytest apps/sandbox-executor/tests/test_executor.py -k "decompose or sub_intent or placeholder"` to prove
   the move changed nothing.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (every later step imports the new module), but `p_success_pred` is high.

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 2 (docstrings, typing, clean architecture); prompt rule
  "add no new imports from entrypoints into the host flow engine".
- **Potential failure modes for this step:** a moved private helper still referenced by the executor (NameError at
  import); a test referencing a private name such as `executor._MAX_LIST_INDENT` no longer found; ruff flagging the
  re-export as unused (F401).
- **Guardrails and early‑abort checks:** grep `tests/` for every moved private name and re-export the ones used; list
  re-exported names in the import so ruff treats them as used (or reference them in an existing `__all__` if the
  executor defines one); `python -c "import sandbox_executor.entrypoint.executor"` before running tests.

### Success & Discard Criteria

- **Success:** the decomposition regression corpus passes unchanged; `decomposition_rule.py` imports nothing from
  `sandbox_executor.entrypoint`; round-trip test passes.
- **Discard:** any existing corpus assertion has to be edited to pass (the move is no longer a move): stop and revert
  the step.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.5   |
| impact_pred         | 15.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 12.30 |

### Step Metrics Rationale

`EV = 0.95 * 15.0 + 0.5 * 1.0 - 0.3 * 1.5 - 2.0 = 14.25 + 0.50 - 0.45 - 2.0 = 12.30`. Mechanical move with an exhaustive
corpus; the only real risk is re-export bookkeeping.

---

## Step 2: Make the planner container record the decision on the plan row it appends

- **Sub‑intent recommendation:** NO
- **Reasoning:** A ~15-line change in one entrypoint plus one test file; it is the producer of the new record, so it
  must land with its readers in this branch.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** New plan rows carry `decomposition_decision`, `entropy`, `entropy_budget` and, when decomposed,
  `sub_intents`, so the plan branch is self-describing from Stage 2 onwards.
- **Git branch:** execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `entrypoint/planner.py::main`, move the existing `converge_prettier([plan_md_rel], ...)` call so it runs before
   the ledger append (it only touches the plan markdown), then read the formatted plan file back. Reason: prettier can
   re-wrap a long sub-intent item, which changes the parsed `description`; the recorded list must match the committed
   file that every reader parses.
2. Compute `decision = should_decompose(plan_entry, formatted_plan_content, intent_data)` (imported from
   `sandbox_executor.decomposition_rule`) after `plan_entry` is built, and
   `plan_entry.update(decision.to_plan_fields())`. `entropy` and `entropy_budget` are already on the row;
   `to_plan_fields()` rewrites them with the same values the rule compared (the planner's `entropy_budget` is already
   the resolved one), so no value changes for in-budget plans other than gaining the `decomposition_decision` key.
3. Do not change the planner's control flow for over-budget plans: a plan above budget without a list is still appended
   with `decomposition_decision: "over_budget"` and the flow's existing re-plan loop handles it.
4. Keep `main()` within its existing suppressions; if a helper is needed, add a small
   `_plan_decision_fields(plan_entry, plan_md_path, intent_data)` function under `C901 <= 10` rather than growing
   `main`.
5. Extend `tests/test_planner.py` (and `tests/test_planner_integration.py` only if it asserts the exact row keys): one
   test with an in-budget plan asserting `decomposition_decision == "execute"` and no `sub_intents`; one with a
   `## Sub-intent list` asserting `decomposed` and the `{slug, description, goal}` list equal to `parse_sub_intent_list`
   of the committed file.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO (the flow back-fill in Step 3 covers rows without the fields).

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` section 3 (append-only; the planner still appends exactly one
  row); `docs/safety.md` ledger write discipline (`atomic_ledger_append`).
- **Potential failure modes for this step:** reordering prettier changes a test that asserts call order; reading the
  file back fails when prettier is unavailable (the warning path).
- **Guardrails and early‑abort checks:** when prettier fails, the unformatted content already on disk is read, so the
  record still matches the committed file; keep the existing warning.

### Success & Discard Criteria

- **Success:** a planner run in the hermetic tests produces a row whose `from_plan_row()` equals `should_decompose()` on
  the committed plan file.
- **Discard:** the reorder breaks more than two planner tests that cannot be fixed without changing their intent.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.5   |
| impact_pred         | 15.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 12.05 |

### Step Metrics Rationale

`EV = 0.90 * 15.0 + 0.5 * 1.0 - 0.3 * 1.5 - 1.5 = 13.50 + 0.50 - 0.45 - 1.5 = 12.05`. Small producer change; the
prettier reorder is the one non-obvious detail and is explicitly handled.

---

## Step 3: Add `flow/stages/plan_decision.py`: read the recorded decision and back-fill an older plan row once

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the riskiest step (the flow writes to `plans.jsonl` for the first time), but it is a single
  focused module of about 150 lines that reuses an existing, tested worktree-append pattern; isolating it in a child
  intent would cut it from the readers it must agree with.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The flow reads the decision from the plan row instead of re-deciding it, and makes an older plan
  branch self-describing by appending exactly one superseding plan row.
- **Git branch:** execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `flow/stages/plan_decision.py` (module docstring: "Stage 2/3 boundary: the plan row's recorded decomposition
   decision"). It imports from `sandbox_executor.decomposition_rule`, `flow.context`, `flow.dispatch`, `flow.git_refs`
   (`_resolve_branch_ref`, `_read_ledger_rows`, `_git_show_text`, `_sync_local_execution_refs`, `EXECUTOR_GIT_ENV`),
   `flow.stages.plan` (`_format_entropy` only), `sandbox_executor.ledger` (`atomic_ledger_append`, `LedgerRewriteError`)
   and `sandbox_executor.git`; nothing from `sandbox_executor.entrypoint`.
2. Move `_over_budget_failure` from `stages/plan.py` verbatim (same AST, so the move proof keeps its digest).
   `_format_entropy` stays in `stages/plan.py` because the out-of-scope `pipeline.py` imports it from there.
3. Move `_plan_budget_decision(context) -> DecompositionDecision | None` and change its body:
   - Read the newest matching plan row and plan file from the plan branch exactly as today (`None` when the branch or
     row cannot be read, so the existing "leave it to the container" path for unreadable rows is unchanged).
   - `DecompositionDecision.from_plan_row(row)`; when it returns a decision, return it (no rule is re-applied).
   - When it returns `None` (older planner image), compute `should_decompose(row, plan_content, context.intent_data)`,
     then call `_append_superseding_plan_row(context, plan_branch, superseding)` where `superseding` is the old row plus
     `decision.to_plan_fields()`, `ledger_revision` = old value (default 1) + 1, a fresh `created_at` and
     `decision_recorded_by: "flow"`. Return the decision whether or not the write succeeded, logging a write failure
     (the decision itself is still the single rule applied to the same inputs); a `LedgerRewriteError` propagates.
   - When `from_plan_row` raises `InvalidPlanDecisionError`, return a sentinel the caller turns into a Stage 3 failure
     (implemented as a small `PlanDecisionError` exception caught in `run_execute_stage` via the helper in item 5), so a
     corrupt record is never silently re-decided.
4. `_append_superseding_plan_row(context, plan_branch, row) -> str | None`: same shape as
   `git_refs._append_superseding_execution_row` (temporary detached worktree on `refs/remotes/origin/<plan_branch>`,
   `atomic_ledger_append` to `plans.jsonl`, `validate_ledger_file_unique_ids` with `plan_id`/`intent_branch`, commit
   `plan: record the decomposition decision of <plan_id>` with `EXECUTOR_GIT_ENV`, push `HEAD:refs/heads/<plan_branch>`,
   remove the worktree in `finally`, then `_sync_local_execution_refs(context, plan_branch, sha)`, which is
   branch-agnostic despite its name). Returns `None` on success or a redacted error. `git_refs.py` is at its budget and
   is not edited.
5. `_decomposition_record(context, decision) -> dict[str, Any]`: returns
   `{"sub_intents": decision.sub_intents, "entropy_budget": decision.entropy_budget, "plan_branch": context.plan_branch}`,
   the exact keys `_run_decomposed_plan` reads from its `summary_row` argument today (contract unchanged).
6. Remove the moved functions and the now-unused imports (`DecompositionDecision`, `should_decompose`,
   `_resolve_branch_ref` if unused, `_read_ledger_rows` only if unused) from `stages/plan.py`; update its module
   docstring to drop "and the entropy budget decision that Stage 3 applies". Lower `stages/plan.py`'s budget in
   `flow_module_budgets.json` to its new size and add `stages/plan_decision.py` at its creation size (adding and
   lowering are allowed; nothing is raised).
7. Keep every function under `C901 <= 10`, `PLR0912 <= 12`, `PLR0913 <= 5` with no new `# noqa`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Stage 3 reads the decision only through this module).

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` section 3 (pure append; the planner's row is never rewritten,
  readers take the newest matching row); section 1 (push only to the active plan branch); `docs/safety.md` ledger
  `assert_ledger_prefix` guard.
- **Potential failure modes for this step:** the back-fill runs in a dry run or with push disabled; a second back-fill
  on resume duplicates rows; `validate_ledger_unique_ids` rejects a same-id row (it does not: same `plan_id` with the
  same `intent_branch` is allowed); a reader that takes the first matching row ignores the back-fill (calibration reads
  the first row, but its metric fields are identical in both rows, so it is unaffected).
- **Guardrails and early‑abort checks:** `run_execute_stage` already returns before the precheck for `dry_run` and
  `skip_push`, so the back-fill never runs there; idempotence comes from the field being present on the second read; a
  hermetic test asserts exactly one superseding row after two Stage 3 prechecks.

### Success & Discard Criteria

- **Success:** an older-style plan branch gains exactly one superseding row whose `from_plan_row()` equals
  `should_decompose()` on the same inputs; a new-style row triggers no write; no module exceeds its budget.
- **Discard:** the back-fill cannot be made a pure append on the plan branch (for example `assert_ledger_prefix` fails
  in the fixture repository): stop and record the finding instead of weakening the guard.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 3.5   |
| impact_pred         | 20.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 1.5   |
| ev_pred             | 12.60 |

### Step Metrics Rationale

`EV = 0.82 * 20.0 + 0.5 * 1.5 - 0.3 * 3.5 - 3.5 = 16.40 + 0.75 - 1.05 - 3.5 = 12.60`. `p_success` is held at 0.82 for
the risk that the plan-branch push interacts with fixture repositories in ways only a run will show. The highest-entropy
step because it introduces a new ledger writer.

---

## Step 4: Make the executor container read the decision from the plan row

- **Sub‑intent recommendation:** NO
- **Reasoning:** A localised edit of the skip-agent branch of `entrypoint/executor.py::main`; it must change together
  with Step 5 or the flow and container disagree.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The executor no longer decides the split or writes the list; its `decomposed` summary refers to the
  plan's recorded decision.
- **Git branch:** execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In the `plans.jsonl` lookup (around line 1590), keep the newest matching row instead of breaking on the first one, so
   a back-filled superseding row (Step 3) is the one read.
2. Replace `decision = should_decompose(plan_data, plan_content, intent_data)` with
   `DecompositionDecision.from_plan_row(plan_data)`, falling back to `should_decompose(...)` only when the row predates
   the field (a direct `holon execute` of an old plan outside the flow). Both are the same rule from
   `decomposition_rule.py`, so there is still one rule. An `InvalidPlanDecisionError` exits 1 with a clear message and
   writes no row (the flow has already failed such a plan before dispatch).
3. In the `DECISION_DECOMPOSED` branch, stop writing `exec_entry["sub_intents"]`; write
   `exec_entry["plan_decision"] = {"plan_id": plan_data["plan_id"], "plan_branch": plan_branch, "decision": "decomposed", "sub_intent_count": len(decision.sub_intents)}`.
   Reword the summary and commit message to "the plan records N sub-intent(s)". The `over_budget` row and the
   `OVER_BUDGET_EXIT_CODE` contract are unchanged.
4. Do not add branches to `main` beyond the replaced lines; if the fallback needs a branch, put it in a small
   `_recorded_decision(plan_data, plan_content, intent_data)` helper.
5. Extend `tests/test_executor.py`: a decomposed plan row with a recorded list produces a summary row with
   `plan_decision` and without `sub_intents` (goal 8 a); a plan row whose recorded decision is `execute` while its
   markdown contains a sub-intent list is executed (proves the container reads the record, not the file); an older row
   without the field still decides via the rule.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (goal 8 a is asserted here).

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` executor ledger rules; constraints section 3.
- **Potential failure modes for this step:** existing executor tests assert `sub_intents` on the summary row; the
  newest-row change alters which row is read when a plans ledger carries two different plans with colliding ids
  (excluded by the unique-id validation).
- **Guardrails and early‑abort checks:** update only the assertions that encode the old contract and name the reason in
  the test docstring; run `uv run pytest apps/sandbox-executor/tests/test_executor.py` before Step 5.

### Success & Discard Criteria

- **Success:** no executor code path writes `sub_intents`; `grep -n "sub_intents" entrypoint/executor.py` shows reads
  only.
- **Discard:** more than a handful of unrelated executor tests break, signalling a hidden reader of the old row shape;
  pause and locate it before continuing.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 2.0   |
| impact_pred         | 15.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 10.85 |

### Step Metrics Rationale

`EV = 0.88 * 15.0 + 0.5 * 0.5 - 0.3 * 2.0 - 2.0 = 13.20 + 0.25 - 0.60 - 2.0 = 10.85`. Surgical change in a large file;
risk is old-contract assertions elsewhere in the 4500-line test file.

---

## Step 5: Wire Stage 3 to execute only the recorded decision, and keep the package proofs green

- **Sub‑intent recommendation:** NO
- **Reasoning:** `stages/execute.py` is exactly at its 396-line budget, so this is a careful net-zero edit plus
  bookkeeping in two fixture files; it is small but must land atomically with Steps 3 and 4.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** `run_execute_stage` hands `_run_decomposed_plan` the plan row's decision record, never the executor's
  summary row, and the flow package's export, patch-target, move-proof and budget tests stay green.
- **Git branch:** execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `stages/execute.py`:
   - import `DECISION_DECOMPOSED`, `DECISION_OVER_BUDGET` from `sandbox_executor.decomposition_rule` (removing those
     names from the `entrypoint.executor` import; `OVER_BUDGET_EXIT_CODE` and `_sanitize_string` stay there, so no new
     entrypoint import is added);
   - import `_decomposition_record`, `_over_budget_failure`, `_plan_budget_decision` from `flow.stages.plan_decision`
     (the name `_plan_budget_decision` stays bound in `execute`'s namespace, so the existing patches
     `sandbox_executor.flow.stages.execute._plan_budget_decision` in the tests keep working);
   - catch `PlanDecisionError` around the precheck and fail the stage before dispatch;
   - replace `return _run_decomposed_plan(context, start_time, summary_row, base_payload)` with: when the precheck
     recorded `decomposed`,
     `_run_decomposed_plan(context, start_time, _decomposition_record(context, precheck), base_payload)`; otherwise fail
     with "the executor reported a decomposed plan but the plan row records '<decision>'" (one rule: a disagreement is
     an error, never resolved in the container's favour);
   - rewrite the `decomposed` bullet of the docstring ("the plan row records an explicit sub-intent list; the executor
     records a summary that refers to it; each item then runs ...") with no net growth, keeping the file at or under 396
     lines (shorten the over-budget bullet if needed).
2. `flow/__init__.py`: import `_over_budget_failure` and `_plan_budget_decision` from `flow.stages.plan_decision` and
   `_format_entropy`, `run_plan_stage` from `flow.stages.plan`, keeping `__all__` unchanged and the file at or under 362
   lines. New helpers are not added to the package re-exports (they are not `flow.py` names).
3. `tests/flow/fixtures/flow_py_definitions.json`: add the digests of the new definitions in `plan_decision.py`
   (`_append_superseding_plan_row`, `_decomposition_record`, `PlanDecisionError` and any module constant) and refresh
   `_plan_budget_decision`, each listed under `amended_definitions` with "E-...
   (I-1794224634-...--1-record-the-decomposition-decision-on-the-plan-ro): the plan row records the decomposition
   decision; the flow reads it and back-fills older rows". Compute the digests with `tests/flow/move_proof.py`'s
   `definition_digests` on the new module, never by hand. `_over_budget_failure` must keep its digest.
4. `tests/flow/helpers.py::BudgetRoleAgents`: the fake planner writes `decision.to_plan_fields()` on its row (mirroring
   the new planner image) and the fake executor reads `from_plan_row` (falling back to `should_decompose`) and writes
   `plan_decision` instead of `sub_intents`, so the existing decomposition integration tests exercise the new contract.
5. Update `tests/flow/stages/test_decomposition.py` and `tests/flow/stages/test_execute.py` only where they build a
   container summary row with `sub_intents` and expect the flow to use it; direct `_run_decomposed_plan` calls keep
   passing a `{sub_intents, entropy_budget, plan_branch}` dict because that contract is unchanged.

### Dependencies & Criticality

- **Depends on:** Steps 3, 4
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `apps/sandbox-executor/flow_module_budgets.json` ("may shrink but never grow"); prompt rule "no
  new imports from entrypoints into the host flow engine"; intent rule "do not touch `stages/decomposition.py`,
  `pipeline.py`, `checkpoint.py`, `flow/github/`".
- **Potential failure modes for this step:** `execute.py` ends one or two lines over budget; ruff reflows an import onto
  more lines; `test_patch_targets.py` flags a patch of a name now defined in another module; the move proof reports an
  "extra" definition.
- **Guardrails and early‑abort checks:** run `uv run python scripts/check_flow_module_budgets.py` (or the lint task that
  wraps it) and
  `uv run pytest apps/sandbox-executor/tests/flow/test_move_proof.py apps/sandbox-executor/tests/flow/test_patch_targets.py apps/sandbox-executor/tests/flow/test_package_exports.py apps/sandbox-executor/tests/flow/test_module_budgets.py`
  immediately after this step; if `execute.py` cannot be kept within budget, move the mismatch check into
  `_decomposition_record` (returning an error string) rather than raising the budget.

### Success & Discard Criteria

- **Success:** `git diff --stat` shows no change to `stages/decomposition.py`, `pipeline.py`, `checkpoint.py`,
  `flow/github/`; all four package-proof tests pass; no budget value increased.
- **Discard:** keeping `execute.py` within budget would require moving unrelated code: stop and report instead of
  raising the budget.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.0   |
| impact_pred         | 20.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 14.40 |

### Step Metrics Rationale

`EV = 0.85 * 20.0 + 0.5 * 1.0 - 0.3 * 2.0 - 2.5 = 17.00 + 0.50 - 0.60 - 2.5 = 14.40`. High impact because this is the
point where "execute only the recorded decision" becomes true; the line budget at its limit is the main risk.

---

## Step 6: Add hermetic tests for goal 8 (a) and (g)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests are the acceptance evidence for this sub-intent and reuse existing fixture repositories
  (`tests/flow/helpers.py`, `worktree_fixtures.py`); they must ship with the code they prove.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove that the plan row carries the decision and the list, that the executor writes neither, that
  older rows are back-filled exactly once, and that an in-budget plan behaves exactly as before.
- **Git branch:** execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `tests/test_decomposition_rule.py` (new): `to_plan_fields`/`from_plan_row` round-trip for all three decisions;
   `from_plan_row` returns `None` without the key; raises `InvalidPlanDecisionError` for an unknown decision, a
   decomposed row with an empty or malformed list, and an execute row carrying `sub_intents`; the module imports nothing
   from `sandbox_executor.entrypoint` (AST check of its imports);
   `entrypoint.executor.should_decompose is decomposition_rule.should_decompose` (re-export identity).
2. `tests/flow/stages/test_plan_decision.py` (new), using a bare origin plus clone fixture with a plan branch:
   - goal 8 (a), flow side: a new-style decomposed row is returned as recorded and the plan branch tip is unchanged (no
     write);
   - back-fill: an old-style row (no `decomposition_decision`) gets exactly one superseding row on origin with
     `ledger_revision` 2, `decision_recorded_by: "flow"` and the rule's fields; a second call writes nothing; the
     planner's original line is a byte prefix of the new ledger;
   - a corrupt recorded decision raises `PlanDecisionError` and writes nothing.
3. Flow integration (in `tests/flow/stages/test_decomposition.py`, using `BudgetRoleAgents`):
   - goal 8 (a): a decomposed plan runs its children from the plan row's list; the execution branch's summary row has
     `plan_decision` and no `sub_intents`; the children's slugs equal the plan row's list;
   - disagreement: when the fake executor reports `decomposed` but the plan row records `execute`, Stage 3 fails with
     the mismatch message and runs no child.
4. goal 8 (g): an in-budget plan with no list (`entropy` at or below the budget) records
   `decomposition_decision: "execute"`, the executor implements it, its summary row has neither `plan_decision` nor
   `sub_intents`, no superseding plan row is written, and the Stage 3 payload matches the pre-change keys. Add one
   variant with an old-style row to show the only difference is the single back-filled row.
5. All tests are hermetic: no network, temp directories only, `gh` stubbed via the existing guards; no test edits
   assertions of unrelated behaviour.

### Dependencies & Criticality

- **Depends on:** Steps 1-5
- **Is Bottleneck:** NO (failures point to code fixes, not plan failure).

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 3 (tests in `tests/` matching the source module; changes
  to test files declared in the plan, as done here); `apps/sandbox-executor/docs/hermetic_testing.md`.
- **Potential failure modes for this step:** fixture repository push to a bare origin needs the plan branch to exist on
  origin; flakiness from wall-clock `created_at`.
- **Guardrails and early‑abort checks:** reuse `SteppingClock` where timestamps are compared; assert on parsed rows, not
  raw lines, except for the byte-prefix assertion.

### Success & Discard Criteria

- **Success:** new tests pass under `-n auto --dist worksteal` and fail when Step 4's `sub_intents` removal or Step 3's
  read-first logic is reverted (checked once by temporarily reverting locally, not committed).
- **Discard:** a test needs network or a real `gh`: rewrite it against the existing stubs instead.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.5   |
| impact_pred         | 10.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 6.55  |

### Step Metrics Rationale

`EV = 0.85 * 10.0 + 0.5 * 1.0 - 0.3 * 1.5 - 2.0 = 8.50 + 0.50 - 0.45 - 2.0 = 6.55`. Familiar fixtures; cost is mostly
the fixture plumbing for the back-fill push.

---

## Step 7: Document the new plan-row fields in `docs/ledger_schema.md`

- **Sub‑intent recommendation:** NO
- **Reasoning:** One doc file, required by the intent; trivial and low risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The ledger schema describes what the planner and the flow now write to `plans.jsonl`, and what the
  executor's `decomposed` summary carries.
- **Git branch:** execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Stage 2 row of the flow stage record matrix: add `decomposition_decision`, `entropy`, `entropy_budget` and, when
   decomposed, `sub_intents` to the planner's row; replace "The flow reads the row back and never writes one" with: the
   flow appends one superseding row (`ledger_revision` + 1, `decision_recorded_by: "flow"`) only when the plan row lacks
   `decomposition_decision` (an older planner image), in a temporary worktree pushed to the plan branch; readers take
   the newest matching row.
2. Add a "Plan row decomposition decision" paragraph after the `entropy_budget` paragraph: field types, `sub_intents`
   items `{slug, description, goal}`, that the rule lives in `sandbox_executor/decomposition_rule.py`, and that historic
   rows are never rewritten.
3. Stage 3 row and the decomposed-summary description: the `decomposed` summary carries `plan_decision` (`plan_id`,
   `plan_branch`, `decision`, `sub_intent_count`) and no `sub_intents`; historic summaries with `sub_intents` stay valid
   and are no longer read by the flow. Adjust the sentence at the "In Stages 1-3 ... writes only the Stage 3 superseding
   empty-diff row" line to mention the plan back-fill.
4. Australian English; run `uv run task lint-docs` (prettier and markdown lint) on the file.

### Dependencies & Criticality

- **Depends on:** Steps 3, 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent scope (only `docs/ledger_schema.md`; `docs/architecture.md`, `docs/git_flow.md`,
  `docs/safety.md` are not touched).
- **Potential failure modes for this step:** the wide matrix table reflows under prettier and the lint-docs check
  oscillates.
- **Guardrails and early‑abort checks:** run prettier twice and check the second pass is a no-op.

### Success & Discard Criteria

- **Success:** `uv run task lint-docs` passes and the doc names every new field.
- **Discard:** none expected.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.5   |
| impact_pred         | 5.0   |
| cost_pred           | 0.5   |
| learning_value_pred | 0.0   |
| ev_pred             | 4.10  |

### Step Metrics Rationale

`EV = 0.95 * 5.0 + 0.5 * 0.0 - 0.3 * 0.5 - 0.5 = 4.75 + 0.00 - 0.15 - 0.5 = 4.10`.

---

## Step 8: Validate with targeted tests, the full suite once, and both lint tasks

- **Sub‑intent recommendation:** NO
- **Reasoning:** The intent prescribes the exact validation sequence; no timing behaviour is measured, so no repeated
  runs.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Demonstrate the change is complete and nothing else regressed.
- **Git branch:** execution branch of this plan
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Targeted:
   `uv run pytest apps/sandbox-executor/tests/test_decomposition_rule.py apps/sandbox-executor/tests/test_executor.py apps/sandbox-executor/tests/test_planner.py apps/sandbox-executor/tests/flow/stages/test_plan_decision.py apps/sandbox-executor/tests/flow/stages/test_execute.py apps/sandbox-executor/tests/flow/stages/test_decomposition.py apps/sandbox-executor/tests/flow/test_move_proof.py apps/sandbox-executor/tests/flow/test_patch_targets.py apps/sandbox-executor/tests/flow/test_package_exports.py apps/sandbox-executor/tests/flow/test_module_budgets.py -n auto --dist worksteal`.
2. Full suite once: `uv run task test`. Report pass/fail counts and the pytest-reported time.
3. `uv run task lint` (ruff, complexity gate, flow module budgets) and `uv run task lint-docs`.
4. Confirm `git diff --stat` touches only the declared touch set, and
   `git diff apps/sandbox-executor/flow_module_budgets.json` shows only a lowered `stages/plan.py` value and the new
   `stages/plan_decision.py` entry.

### Dependencies & Criticality

- **Depends on:** Steps 1-7
- **Is Bottleneck:** YES (the acceptance gate).

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 9 (tests strictly via `uv run pytest`; lint and lint-docs pass); test-run discipline
  in the planner contract.
- **Potential failure modes for this step:** an unrelated pre-existing flaky test fails in the full run.
- **Guardrails and early‑abort checks:** if one unrelated test fails, re-run only that test once to classify it and
  record it in the execution notes; do not loop the full suite.

### Success & Discard Criteria

- **Success:** all four commands exit 0.
- **Discard:** a lint failure that can only be fixed with a `# noqa` or a raised budget: stop and report.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.5   |
| impact_pred         | 5.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 0.0   |
| ev_pred             | 3.35  |

### Step Metrics Rationale

`EV = 0.90 * 5.0 + 0.5 * 0.0 - 0.3 * 0.5 - 1.0 = 4.50 + 0.00 - 0.15 - 1.0 = 3.35`.

---

### Strategy Rationale

The intent is accepted as written: it is the prerequisite half of the parent plan (the second sub-intent, which moves
children under the plan branch and adds the merge and resume logic, cannot be cut without a plan-owned decision). The
strategy keeps one rule in one neutral module, makes the plan row its only record, and leaves the `_run_decomposed_plan`
contract untouched so `stages/decomposition.py` and `pipeline.py` stay out of scope.

Two execution details go beyond the literal intent text, both forced by the code read during planning:

- The back-fill runs in the Stage 3 pre-dispatch read (`_plan_budget_decision`), not inside `run_plan_stage`, because
  `pipeline.py` (out of scope) imports `_format_entropy` from `stages/plan.py`, so `_format_entropy` must stay there and
  `plan_decision.py` imports it; a Stage 2 call back into `plan_decision.py` would be an import cycle. Plans from the
  new planner image are self-describing at the end of Stage 2 anyway; only older rows are back-filled, before any
  executor runs.
- `tests/flow/test_move_proof.py` fails on any flow definition missing from its snapshot, so new and changed definitions
  are recorded in `amended_definitions` with a reason, the established mechanism for deliberate changes.

Aggregation of the overall metrics:

- `p_success_pred = 0.74`: the critical-path steps (1, 3, 4, 5, 8) are partly correlated (they fail together on the same
  hidden test or budget), so the overall figure sits between the bottleneck step (Step 3, 0.82) and the independent
  product (`0.95 * 0.90 * 0.82 * 0.88 * 0.85 * 0.85 * 0.95 * 0.90 = 0.40`). Qualitative judgement: 0.74.
- `entropy_pred = 13.0`: sum of step entropies `1.5 + 1.5 + 3.5 + 2.0 + 2.0 + 1.5 + 0.5 + 0.5 = 13.0`, within the budget
  of 15.0.
- `impact_pred = 80.0`: qualitative, not the sum of step impacts (which is 105): this sub-intent removes the
  two-deciders defect and unblocks the second sub-intent, which carries the rest of the parent's value.
- `cost_pred = 15.0`: sum of step costs `2.0 + 1.5 + 3.5 + 2.0 + 2.5 + 2.0 + 0.5 + 1.0 = 15.0`.
- `learning_value_pred = 5.0`: sum of step learning values (6.0) discounted for overlap; the main lesson is whether a
  flow-written plan row coexists with the planner's ledger contract.
- `EV = 0.74 * 80.0 + 0.5 * 5.0 - 0.3 * 13.0 - 15.0 = 59.20 + 2.50 - 3.90 - 15.0 = 42.80`.

Bottleneck: Step 3 (new ledger writer on the plan branch), with Step 5's net-zero edit of `stages/execute.py` at its
line budget as the second risk. Strong assumption: `_sync_local_execution_refs` is branch-agnostic (read during
planning: it only updates `refs/remotes/origin/<branch>` and fast-forwards the local branch), so `git_refs.py` needs no
edit.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 3 (append-only ledgers): the flow's plan-row back-fill is a pure append
    under `atomic_ledger_append`; the planner's row is never edited.
  - `holon-config/world/constraints.md` section 1 (commit boundaries): the back-fill pushes only to the active plan
    branch; no step touches `main` or another intent's branch.
  - `holon-config/world/constraints.md` section 4: core-module refactor, so a deep model executes it.
  - `holon-config/world/ruleset.md` sections 2 and 3: typed, documented public functions; tests under `tests/` mirroring
    the source; every test-file change is declared in Steps 2, 4, 5 and 6.
  - `apps/sandbox-executor/flow_module_budgets.json`: no budget raised; one lowered, one added at creation size.
  - Intent scope fence: `stages/decomposition.py`, `pipeline.py`, `checkpoint.py`, `flow/github/`,
    `docs/architecture.md`, `docs/git_flow.md` and `docs/safety.md` are not modified.
- **Potential violations or edge cases:**
  - The back-fill accidentally running in a dry run or with push disabled (would write a ledger the operator did not ask
    for).
  - Two prechecks on resume writing two superseding rows.
  - A reader that takes the first matching plan row ignoring the back-fill (`calibration.py`), or the executor doing so
    before Step 4's fix.
  - The moved `parse_sub_intent_list` carrying its existing `# noqa` into a new file, which a reviewer could read as a
    new suppression.
  - `stages/execute.py` growing past 396 lines.
- **Mitigations built into the plan:**
  - The precheck runs after the existing `dry_run` and `skip_push` early returns in `run_execute_stage`.
  - Idempotence by construction (the field is present on the second read) plus a test asserting one superseding row.
  - Superseding rows copy the metric fields unchanged, so first-row readers see identical metrics; the executor is
    switched to newest-row reads.
  - The suppression is moved, not added, and the function body is byte-identical (the regression corpus proves it); this
    is stated in the execution notes for reviewers.
  - A named fallback (move the mismatch check into `_decomposition_record`) keeps `execute.py` within budget.
- **Residual risk accepted (and why):** A second plan row per older plan branch slightly enlarges `plans.jsonl`; this is
  accepted because append-only supersession (with `ledger_revision`) is already the ledger's documented pattern for
  execution rows, and it only occurs once per pre-existing plan.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 13.0
- **Budget Compliance:** The strategy fits within budget (13.0 <= 15.0), so it is executed as one plan and has no
  sub-intent list.
