# Plan for I-1792992678-entropy-budget-handoff-and-no-human-halt-in-execute

- **Plan ID:** P-1792992691-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-26T05:31:32.435Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A. One design refinement is made explicit (not a reframe): the executor stops
  writing child intent rows itself; it records the parsed sub-intent list on its `executions.jsonl` summary row, and the
  flow creates each child intent through the normal Stage 1 intent-creator. This avoids duplicate `intents.jsonl` rows
  when each child runs the full pipeline, and keeps "never append placeholder rows" true by construction.
- **Exploration stance:** conservative. The defect is fully diagnosed (divergent `15.0` and `5.0` literals, the missing
  `entropy_budget` field on the plan row, the fabricated fallback list in `should_decompose()`, and the Stage 3
  `HALTED_FOR_HUMAN` branch in `flow.py`), so exploitation of known fixes dominates.
- **Safety priority level:** elevated
- **Priority Justification:** The change touches the autonomy boundary of the flow (`docs/safety.md` §5 "Human review is
  the final gate") and append-only ledgers (`holon-config/world/constraints.md` §3). The plan must remove a pre-merge
  halt without touching the review-stage consensus halt, which is the merge boundary that keeps `main` human-gated.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** Root cause and target behaviour are specified precisely by the intent; the remaining uncertainty
  (how child pipelines are wired in `flow.py`) is engineering risk handled by mocks and tests, not by exploration.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 4.6   |
| impact_pred         | 85.0  |
| cost_pred           | 14.0  |
| learning_value_pred | 4.0   |
| ev_pred             | 54.6  |

### Strategy Rationale

The work is one coherent fix along a single data path (intent budget, plan row, executor check, flow reaction), so it is
executed as one plan with seven ordered steps on one execution branch rather than split into child intents. Splitting
would itself require the decomposition machinery that this intent repairs, and the bootstrap constraint (the executor
running this plan is the unfixed one) means the plan must stay at or below an entropy of 5.0 and contain no explicit
decomposition section at all.

Aggregation: entropy is the sum of step entropies, `0.4 + 0.6 + 0.9 + 1.3 + 0.8 + 0.4 + 0.2 = 4.6`, which is inside the
5.0 budget. Probability of success is driven by the bottleneck, Step 4 (flow changes in a 4,575 line module with many
existing tests), at 0.82; the other steps are high-confidence, so the overall value is a qualitative judgement slightly
below the bottleneck, 0.80. Impact is 85 because the defect blocked a real flow and every over-5.0 plan currently halts
or fabricates work. Cost is the sum of step costs, `1.0 + 1.5 + 2.5 + 4.5 + 2.5 + 1.5 + 0.5 = 14.0`. Learning value is
4.0 (it tests whether sequential child pipelines are robust under the mocked dispatcher).

`EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`

`EV = 0.80 * 85.0 + 0.5 * 4.0 - 0.3 * 4.6 - 14.0 = 68.0 + 2.0 - 1.38 - 14.0 = 54.62`

Strong assumptions: the existing `FlowContext.dispatcher` injection is sufficient to mock all three role containers for
child pipelines in tests; `parse_metrics()` keeps reading the first metrics table (the overall one) as the plan entropy.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1: only humans merge to `main`; child intents target their immediate parent
    branch, never `main`.
  - `holon-config/world/constraints.md` §3: ledgers are append-only; the new `entropy_budget` field is additive on new
    rows only, historic rows are never rewritten (fallback handles them).
  - `holon-config/world/ruleset.md` §2 and §3: typed, docstringed public functions; tests in the matching `tests/`
    files; test-file changes are declared here (Step 5).
  - `docs/safety.md` §4 and §5: entropy remains a safety signal (over-budget plans are re-planned or failed, never
    silently executed); the merge to `main` remains the human gate.
  - Intent constraints: Australian English, no new dependencies, `uv run task test` and `uv run task lint`, no auxiliary
    ledgers, no ledger layout change, no forbidden capitalised hyphenated token in this file.
- **Potential violations or edge cases:**
  - Removing the Stage 3 halt by accident also weakening the Stage 4 consensus halt in `PipelineEngine.run()`.
  - Unbounded recursion if a child plan also emits a sub-intent list (child of a child of a child...).
  - Concurrent child runs racing on ledger appends.
  - A test that greps the source for the old placeholder strings matching itself.
  - Historic `plans.jsonl` rows without `entropy_budget`.
- **Mitigations built into the plan:**
  - The run-loop branch for `HALTED_FOR_HUMAN` at `FlowStage.REVIEW` is left byte-for-byte unchanged; a regression test
    pins it.
  - A bounded child depth constant and a bounded re-plan attempt constant; children run in a plain sequential loop.
  - The placeholder-text test assembles the forbidden phrases from fragments at runtime.
  - Budget resolution order is plan row, then intent row, then the shared default.
- **Residual risk accepted (and why):** Child pipelines are only exercised through mocks (the intent forbids real
  containers in tests); a real multi-child run is first observed in production flow, which is acceptable because each
  child is still isolated in its own branch and stops at its own review consensus halt.
- **Allocated Entropy Budget:** 5.0
- **Predicted Plan Entropy:** 4.6
- **Budget Compliance:** The strategy fits within budget.

## Plan Description & Strategy

Fix the entropy-budget handoff end to end and make Stage 3 fully autonomous:

1. One shared default constant and one resolver for the entropy budget.
2. The planner shows and persists the same budget (`entropy_budget` on the `plans.jsonl` row).
3. The executor decides using that budget, never fabricates child work, and reports one of three outcomes: execute,
   decomposed (with the plan's own explicit sub-intent list), or over budget (no list).
4. The flow reacts without a human: decomposed runs each child sequentially through the full pipeline; over budget
   returns to Stage 2 with bounded re-plan attempts, then fails with a clear message.
5. Tests, docs, and validation.

All work happens on a single execution branch off this plan branch, suggested name
`I-1792992678-entropy-budget-handoff-and-no-human-halt-in-execute/P-1792992691-claude-agent-claude-opus-5-5/E-{timestamp}-fix-budget-handoff/_`.

---

## Step 1: Introduce one shared entropy-budget default and resolver

- **Sub‑intent recommendation:** NO
- **Reasoning:** A small, low-risk constant and pure helper; it is the foundation for Steps 2 to 4 and belongs on the
  same execution branch.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Replace the divergent `15.0` (planner) and `5.0` (executor) literals with a single source of truth.
- **Git branch:** shared execution branch (see Plan Description)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/src/sandbox_executor/budgets.py` (already the single source of truth for budgets), add a
  short section for entropy budgets: a module constant `DEFAULT_ENTROPY_BUDGET = 15.0` (the value the planner already
  shows agents, so existing agent-facing behaviour is unchanged), and a typed, docstringed pure function
  `resolve_entropy_budget(plan_row, intent_row)` returning a float.
- Resolution order: a finite, non-negative `entropy_budget` on the plan row; else the same on the intent row; else
  `DEFAULT_ENTROPY_BUDGET`. Values that are missing, `None`, non-numeric, NaN or negative fall through to the next
  source (reuse `math.isfinite`, already imported there).
- Add a second constant `MAX_REPLAN_ATTEMPTS = 2` and `MAX_SUB_INTENT_DEPTH = 2` beside it (used in Step 4), each with a
  one-line Australian English comment.
- Update the module docstring to mention entropy budgets in one sentence. No new dependencies or modules.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (typing, docstrings); intent goal 1.
- **Potential failure modes for this step:** an import cycle if `budgets.py` imported executor or flow modules.
- **Guardrails and early‑abort checks:** the helper takes plain dicts and imports nothing new; `uv run task lint`.

### Success & Discard Criteria

- **Success:** both entrypoints can import one constant and one resolver; a grep of `src/` finds no remaining hard-coded
  `15.0` or `5.0` entropy-budget literal.
- **Discard:** cost exceeds `1.5 * 1.0`, or an import cycle cannot be avoided (then place the helper in `ledger.py`).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.4   |
| impact_pred         | 15.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 14.13 |

### Step Metrics Rationale

`EV = 0.95 * 15.0 + 0.5 * 2.0 - 0.3 * 0.4 - 1.0 = 14.25 + 1.0 - 0.12 - 1.0 = 14.13`. Pure function, trivially testable,
one file touched.

---

## Step 2: Planner shows and persists the same budget

- **Sub‑intent recommendation:** NO
- **Reasoning:** Two small edits in `planner.py`, one template note and one schema doc row; low risk, tightly coupled to
  Step 3.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The budget the planning agent sees is exactly the budget written to `plans.jsonl` and later enforced.
- **Git branch:** shared execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `entrypoint/planner.py` compute `entropy_budget = resolve_entropy_budget({}, intent_data)` once, near the template
  replacements.
- Use it for the `{budget_from_intent}` placeholder (formatted with `str()` of the float) instead of the `"15.0"`
  literal.
- Add `"entropy_budget": entropy_budget` to `plan_entry` (the `plans.jsonl` row) before the append; the row stays a
  single append, no layout change.
- If the flow passes re-plan feedback (Step 4) via a transient `replan_feedback` key in the intent JSON file, append it
  to the prompt as a short "Re-plan request" paragraph asking for a plan within the budget or an explicit sub-intent
  list. The key is never written to any ledger.
- In `holon-config/prompts/planner.template.md`, add a short note under the safety section explaining the decomposition
  contract: a plan that cannot fit the budget must include a level-2 heading `## Sub-intent list` followed by bullet or
  numbered items, one concrete child intent per item; otherwise the plan must fit the budget.
- In `docs/ledger_schema.md`, document `entropy_budget` (float, optional on historic rows, resolution order from Step
  1. in the plans row table.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (append-only, additive field only); intent goal 1 and 7.
- **Potential failure modes for this step:** template wording that coincides with the legacy decomposition heading;
  `test_planner.py` assertions on exact row keys.
- **Guardrails and early‑abort checks:** run `uv run pytest apps/sandbox-executor/tests/test_planner.py` after the
  change; the new heading wording is singular and lower-case after the first word.

### Success & Discard Criteria

- **Success:** a planner run under mocks writes a row with `entropy_budget` equal to the intent's value (16.0 in the
  regression fixture) and the prompt shows the same number.
- **Discard:** cost exceeds `1.5 * 1.5`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.6   |
| impact_pred         | 18.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 16.06 |

### Step Metrics Rationale

`EV = 0.93 * 18.0 + 0.5 * 2.0 - 0.3 * 0.6 - 1.5 = 16.74 + 1.0 - 0.18 - 1.5 = 16.06`. Small diff; a slight entropy bump
for the template and docs files touched.

---

## Step 3: Executor uses the persisted budget and never fabricates child work

- **Sub‑intent recommendation:** NO
- **Reasoning:** Core of the defect, but confined to `should_decompose()` and its caller in `entrypoint/executor.py`;
  well covered by existing unit tests that will be rewritten in Step 5.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The executor compares plan entropy against the same budget the planner used, decomposes only on an
  explicit sub-intent list in the plan, and reports over-budget plans without writing any `intents.jsonl` row.
- **Git branch:** shared execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Change `should_decompose(plan_data, plan_content, intent_data=None)` to return a small typed result: a decision
  string, one of `execute`, `decomposed` or `over_budget`, plus the parsed list (a frozen dataclass or `NamedTuple`,
  documented). Keep the name so call sites and docs stay recognisable.
- Budget: `resolve_entropy_budget(plan_data, intent_data)`.
- Parsing: only a markdown heading of level 2 or 3 whose text, lower-cased with hyphens, underscores and spaces
  collapsed, equals `sub intent list`, `sub intents` or `sub_intents` (so the legacy capitalised heading keeps working
  without the plan needing to spell it). Collect bullet and numbered items until the next heading. Ignore the per-step
  `Sub‑intent recommendation` bullets, because they are not under such a heading.
- Each item becomes `{slug, description, goal}` from the item text only; skip empty items; de-duplicate slugs with a
  numeric suffix.
- Decision: a non-empty list gives `decomposed` (regardless of entropy); entropy above the budget with no list gives
  `over_budget`; otherwise `execute`. Delete the hard-coded fallback list entirely.
- Caller in `main()`: load `intent_data` before calling `should_decompose()` (move the call below the intents lookup,
  which it already is). Replace the fallback `plan_data` default `"entropy_budget": 5.0` with no budget key so the
  resolver applies.
  - `decomposed`: stop appending to `intents.jsonl`; write only the `executions.jsonl` summary row with
    `status: "decomposed"`, `entropy_budget`, and `sub_intents` (the parsed items); stage only `executions.jsonl`.
  - `over_budget`: write a summary row with `status: "over_budget"`, `entropy`, `entropy_budget` and a clear Australian
    English message, commit and push it as today so the flow can read it, and do not run the agent; exit code 0 so the
    flow, not the container, decides what happens next.
  - `execute`: unchanged.
- Update the action record text and the commit message so they no longer imply intents were created.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 2; `holon-config/world/constraints.md` §3; `docs/safety.md` §4.
- **Potential failure modes for this step:** the empty-diff guard or pre-push sync path assumes a decomposed row;
  `superseding_failure_row` expects specific keys; the `if not decompose_needed` tail must also cover `over_budget`.
- **Guardrails and early‑abort checks:** keep a single boolean `skip_agent` derived from the decision for the existing
  tail logic; run `uv run pytest apps/sandbox-executor/tests/test_executor.py` before moving on.

### Success & Discard Criteria

- **Success:** plan entropy 12.6 under a row or intent budget of 16 returns `execute`; entropy 12.6 under budget 5 with
  no list returns `over_budget` and writes zero `intents.jsonl` rows; an explicit list returns exactly those items; the
  placeholder phrases are gone from `src/`.
- **Discard:** cost exceeds `1.5 * 2.5`, or the executor tail cannot be adapted without restructuring `main()` (then
  stop and record in the execution notes).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 0.9   |
| impact_pred         | 25.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 20.73 |

### Step Metrics Rationale

`EV = 0.88 * 25.0 + 0.5 * 3.0 - 0.3 * 0.9 - 2.5 = 22.0 + 1.5 - 0.27 - 2.5 = 20.73`. Moderate risk from the long `main()`
with shared tail logic; high impact because this is where the fabrication happens.

---

## Step 4: Flow drives decomposition and re-planning autonomously

- **Sub‑intent recommendation:** NO
- **Reasoning:** The riskiest step (bottleneck), but splitting it off would need the very decomposition path being
  fixed; it is kept here with strong guardrails and mock-only tests rather than as a child intent.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Stage 3 never returns `HALTED_FOR_HUMAN`; decomposed plans run each child sequentially through the
  full pipeline; over-budget plans return to Stage 2 a bounded number of times and then fail clearly.
- **Git branch:** shared execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `run_execute_stage()` (`flow.py`, around the `container_status == "decomposed"` branch) delete the halting branch
  and its log string.
- Add a pre-dispatch budget check: read the plan row from the plan branch ledger (`_read_ledger_rows` on the plan ref)
  and the plan file, call the executor's `should_decompose()` with `context.intent_data`; if the decision is
  `over_budget`, skip dispatching the executor and return a `StageResult` with `status=FAILED`,
  `payload["over_budget"] = True`, `entropy` and `entropy_budget`. The post-dispatch handling of an `over_budget`
  summary row (defence in depth) produces the same result.
- Re-plan loop in `PipelineEngine.run()`: when the Execute result has `payload["over_budget"]` and
  `context.replan_attempts < MAX_REPLAN_ATTEMPTS`, increment the counter (new `FlowContext` field, checkpointed with a
  default of 0 so old checkpoints load), set `context.intent_data["replan_feedback"]` to a message stating the entropy,
  the budget, and the request for a smaller plan or an explicit sub-intent list, clear `plan_branch`, and re-run Stage 2
  then Stage 3 in place. When attempts are exhausted, keep the stage `FAILED` with the error "Execute stage failed: plan
  entropy X exceeds the entropy budget Y after N re-plan attempts and the plan lists no sub-intent list." The feedback
  key is removed from the intent payload before Stage 1 of any child and before checkpointing.
- Decomposed path: for a `decomposed` summary row, build one child `FlowContext` per item, copying agent, model, flags
  and dispatcher, with `intent_data` containing slug, description, goal, `parent_intent_id` (the parent intent branch),
  `target_branch` set to the parent intent branch (children merge into their immediate parent), `entropy_budget`
  inherited from the parent budget, and `sub_intent_depth = parent depth + 1`. Run them with `PipelineEngine` in a plain
  `for` loop, one at a time, no threads or pools.
- A child succeeds when its run ends at its own Stage 4 consensus halt (or Stage 5 success) without any `FAILED` stage;
  the first failing child stops the loop and Stage 3 fails, naming the child. If `sub_intent_depth` would exceed
  `MAX_SUB_INTENT_DEPTH`, fail instead of recursing.
- When all children succeed, Stage 3 returns `SUCCESS` with `payload["decomposed"] = True` and per-child summaries
  (intent branch, final stage, status). The parent's Stage 4 is then skipped for the parent itself with status `SKIPPED`
  and payload reason `decomposed`, because review and calibration happened per child; this avoids the empty-diff guard
  firing on a ledger-only parent execution.
- Leave `PipelineEngine.run()`'s `HALTED_FOR_HUMAN` handling for `FlowStage.REVIEW` and its Stage 5 calibration
  unchanged; only narrow the generic halt branch with a comment that Stage 4 is the only stage that may halt.
- Update the `run_execute_stage()` docstring to describe the three outcomes, in Australian English.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 3 and the two answered questions; `docs/safety.md` §1 and §5;
  `holon-config/world/constraints.md` §1 (children never target `main`).
- **Potential failure modes for this step:** unbounded re-plan or recursion; child logs not reaching the parent;
  checkpoint schema drift; accidental changes to the review halt.
- **Guardrails and early‑abort checks:** bounded counters from Step 1; child logs are appended to the parent log with a
  child prefix; checkpoint loader tolerates missing new fields; run the full `test_flow.py` after each sub-change.

### Success & Discard Criteria

- **Success:** no code path in Stage 3 returns `HALTED_FOR_HUMAN`; under a mocked dispatcher, an over-budget plan is
  re-planned exactly `MAX_REPLAN_ATTEMPTS` times then fails; a two-item list runs two child pipelines in order; the
  Stage 4 consensus halt tests still pass untouched.
- **Discard:** cost exceeds `1.5 * 4.5`, or the child pipeline cannot be driven without real containers in tests (then
  ship Steps 1 to 3 plus the re-plan loop, fail decomposed plans with a clear message instead of halting, and record the
  child-pipeline work as a follow-up intent in the execution notes).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 1.3   |
| impact_pred         | 30.0  |
| cost_pred           | 4.5   |
| learning_value_pred | 5.0   |
| ev_pred             | 22.21 |

### Step Metrics Rationale

`EV = 0.82 * 30.0 + 0.5 * 5.0 - 0.3 * 1.3 - 4.5 = 24.6 + 2.5 - 0.39 - 4.5 = 22.21`. Highest entropy and cost because
`flow.py` is large and heavily tested; the learning value is the highest because nested pipeline orchestration has not
been exercised before.

---

## Step 5: Tests for budget handoff, no fabrication and no Stage 3 halt

- **Sub‑intent recommendation:** NO
- **Reasoning:** Test changes are explicitly declared here as the ruleset requires; they are the acceptance gate for
  Steps 1 to 4 and must land with them.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Encode every acceptance criterion of intent goal 4 in pytest, using fixtures and mocks only.
- **Git branch:** shared execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- `tests/test_budgets.py`: resolver order (plan row, intent row, default), invalid values fall through, the default
  equals the single constant.
- `tests/test_planner.py`: with a mocked runner, the `plans.jsonl` row carries `entropy_budget` equal to the intent's
  16.0, the prompt shows the same value, and a missing intent budget yields `DEFAULT_ENTROPY_BUDGET`; the transient
  `replan_feedback` appears in the prompt and never in the row.
- `tests/test_executor.py`: rewrite the existing `should_decompose` tests. Entropy 12.6 with budget 16 gives `execute`;
  entropy 12.6 with no row budget falls back to the intent budget of 16 and gives `execute`; over budget with no list
  gives `over_budget` and an empty list; an explicit list (bulleted and numbered, under the new heading and the legacy
  heading built from string fragments) gives exactly those items. An integration-style `main()` test under the existing
  mocks asserts that the over-budget path appends zero rows to `intents.jsonl` and that the decomposed path also appends
  zero rows there while recording `sub_intents` on the execution summary row.
- `tests/test_flow.py`, using the existing `dispatcher` injection and temporary git fixtures:
  - a parametrised test that drives `run_execute_stage()` through decomposed, over-budget and normal summary rows and
    asserts the status is never `StageStatus.HALTED_FOR_HUMAN`;
  - the regression case of intent budget 16 and plan entropy 12.6 dispatching the executor normally;
  - an over-budget plan without a list re-plans exactly `MAX_REPLAN_ATTEMPTS` times (counted on the mocked planner
    dispatches), then fails with the clear message, and no `intents.jsonl` rows are written;
  - a two-item list runs two child pipelines in order, asserting the recorded dispatch order and that a child never
    starts before the previous one finished (a simple in-progress flag in the mock);
  - the existing Stage 4 consensus-halt and Stage 5 calibration tests remain unmodified and passing.
- Source hygiene test: walk `apps/sandbox-executor/src/` and assert neither placeholder goal phrase occurs, with the
  phrases assembled from fragments so the test file does not match itself.
- Remove or rewrite existing assertions that expect the fabricated fallback items or the Stage 3 halt. Use pytest style
  for new tests; existing `unittest.TestCase` classes stay but are run only via pytest.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3; intent goal 4 and 7;
  `apps/sandbox-executor/docs/hermetic_testing.md` (no real gh, remotes or containers; `gh_write_guard.py` and
  `hermetic_fixtures.py` remain active).
- **Potential failure modes for this step:** flaky ordering assertions; fixtures accidentally reaching a real remote.
- **Guardrails and early‑abort checks:** reuse `hermetic_fixtures.py`; set `HOLON_SKIP_PUSH` in executor tests; run via
  `uv run pytest` only.

### Success & Discard Criteria

- **Success:** `uv run task test` passes with the new tests, and each acceptance bullet of intent goal 4 maps to at
  least one named test.
- **Discard:** cost exceeds `1.5 * 2.5`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 0.8   |
| impact_pred         | 15.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 11.96 |

### Step Metrics Rationale

`EV = 0.88 * 15.0 + 0.5 * 3.0 - 0.3 * 0.8 - 2.5 = 13.2 + 1.5 - 0.24 - 2.5 = 11.96`. The flow tests need careful mock
wiring for nested pipelines, which is the main source of risk.

---

## Step 6: Documentation of the shared budget and the autonomous flow

- **Sub‑intent recommendation:** NO
- **Reasoning:** Documentation only, low risk; it must describe the behaviour shipped in Steps 1 to 4.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Docs describe one shared entropy budget, the decomposition rules, and that the only human step is the
  merge.
- **Git branch:** shared execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- `docs/budgets.md`: a section on the entropy budget: `DEFAULT_ENTROPY_BUDGET`, resolution order, where it is persisted,
  and the re-plan bound.
- `docs/planning_and_evaluation.md` and `docs/planner/planning_architecture_specification.md`: the planner shows and
  persists the budget; how to write an explicit sub-intent list (heading, one item per child).
- `docs/executor/execution_architecture_specification.md`: the three executor outcomes; no fabricated child work; no
  `intents.jsonl` rows from the executor.
- `docs/git_flow.md`: children run sequentially and target the immediate parent branch; the flow has no pre-merge human
  halt except the Stage 4 consensus stop, after which merging is the only human step.
- `docs/ledger_schema.md`: already updated in Step 2; also document the `over_budget` execution status and the
  `sub_intents` field on decomposed rows.
- Sweep the other docs flagged by grep (for example `docs/faq.md`, `docs/agents.md`, `docs/core_concepts.md`) for
  statements that Stage 3 halts for a human, and correct them in Australian English.
- Format with the repository prettier configuration (`uv run task lint-docs` or `npx prettier --write` on the touched
  files only).

### Dependencies & Criticality

- **Depends on:** Steps 1 to 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 5 and 7; `docs/safety.md` §5 wording stays accurate (human gate at merge).
- **Potential failure modes for this step:** prettier oscillation on formulas; contradicting `docs/safety.md`.
- **Guardrails and early‑abort checks:** keep formulas in code spans; run prettier check twice to confirm stability.

### Success & Discard Criteria

- **Success:** prettier check passes; no doc claims Stage 3 halts for human review.
- **Discard:** cost exceeds `1.5 * 1.5`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.4   |
| impact_pred         | 6.0   |
| cost_pred           | 1.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 4.58  |

### Step Metrics Rationale

`EV = 0.95 * 6.0 + 0.5 * 1.0 - 0.3 * 0.4 - 1.5 = 5.7 + 0.5 - 0.12 - 1.5 = 4.58`. Many files but purely descriptive.

---

## Step 7: Validate lint, tests and formatting

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical verification gate at the end of the branch.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the branch is mergeable under the repository's own gates.
- **Git branch:** shared execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Run `uv run task lint` (ruff check, ruff format check, lock check) and fix findings.
- Run `uv run task test`; fix failures in the code from Steps 1 to 4, not by weakening assertions.
- Run the repository markdown check on touched markdown files.
- Grep `apps/sandbox-executor/src/` for the placeholder phrases and for `5.0` or `15.0` entropy literals; grep `flow.py`
  to confirm the only `HALTED_FOR_HUMAN` result constructor left is in the review stage.
- Record results in the execution notes file.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 6
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 7 (never unittest runner, never `PYTHONPATH`).
- **Potential failure modes for this step:** pre-existing unrelated failures.
- **Guardrails and early‑abort checks:** compare against a baseline run on the plan branch; report pre-existing failures
  rather than masking them.

### Success & Discard Criteria

- **Success:** lint, tests and markdown checks all pass.
- **Discard:** pre-existing failures unrelated to this change are documented, not fixed.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.2   |
| impact_pred         | 4.0   |
| cost_pred           | 0.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 3.62  |

### Step Metrics Rationale

`EV = 0.92 * 4.0 + 0.5 * 1.0 - 0.3 * 0.2 - 0.5 = 3.68 + 0.5 - 0.06 - 0.5 = 3.62`. Low entropy verification step.

---
