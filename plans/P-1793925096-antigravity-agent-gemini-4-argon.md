# Plan for I-1793925076-increase-default-review-iteration-stage-budget-to-2-hours

- **Plan ID:** P-1793925096-antigravity-agent-gemini-4-argon
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-4-argon (version: 1.5.1)
- **Created At:** 2026-11-06T00:31:37.520Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A
- **Exploration stance:** conservative with a targeted verification pass — the scope is a well-defined constant, test,
  and documentation update across four files with explicit acceptance criteria.
- **Safety priority level:** standard
- **Priority Justification:** The change adjusts the default stage budget constant `StageBudgets.review_iteration` from
  `900.0` seconds (15 minutes) to `7200.0` seconds (120 minutes) without weakening container isolation, ledger
  append-only invariants, or explicit ceiling enforcement (`HOLON_STAGE_BUDGET_REVIEW_ITERATION_MINUTES` remains fully
  configurable).

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** All target locations (`budgets.py`, `test_budgets.py`, `test_flow.py`, and `docs/budgets.md`) and
  expected values (`7200.0` seconds / `120` minutes) are deterministically specified in the intent goal.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 1.20  |
| impact_pred         | 75.00 |
| cost_pred           | 3.50  |
| learning_value_pred | 1.50  |
| ev_pred             | 68.89 |

### Strategy Rationale

The intent is accepted as-is (`ACCEPT_AS_IS`) with a conservative, pure-exploitation (`EXPLOIT`) strategy across three
sequential steps: (1) updating `StageBudgets.review_iteration` from `900.0` to `7200.0` seconds and its docstring in
`apps/sandbox-executor/src/sandbox_executor/budgets.py`, (2) updating the unit and flow test assertions in
`apps/sandbox-executor/tests/test_budgets.py` and `apps/sandbox-executor/tests/test_flow.py`, and (3) updating
`docs/budgets.md` and running the verification suite.

**Derivation of Overall Metrics:**

- `p_success_pred`: Derived as the product of sequential bottleneck steps (`0.99 * 0.98 * 0.99 = 0.960498`, rounded to
  `0.96`). Step 2 is the primary bottleneck (`0.98`) because `SteppingClock` step sizes in `test_flow.py` must be scaled
  alongside the budget assertions.
- `entropy_pred`: Sum of step entropies (`0.35 + 0.55 + 0.30 = 1.20`), well below the `15.0` entropy budget.
- `impact_pred`: Sum of step impacts (`40.00 + 25.00 + 10.00 = 75.00`), reflecting the operational value of preventing
  mid-flight Stage 4 review ceiling breaches during 3-agent ensemble reviews and resolver runs.
- `cost_pred`: Sum of step costs (`0.80 + 1.70 + 1.00 = 3.50`).
- `learning_value_pred`: Sum of step learning values (`0.50 + 0.50 + 0.50 = 1.50`), representing routine confirmation of
  stage-timer and clock-injection test seams.
- `ev_pred`: Computed from the overall predicted metrics using
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost` with `mu = 0.5` and `lambda = 0.3`:
  `EV = 0.96 * 75.00 + 0.5 * 1.50 - 0.3 * 1.20 - 3.50 = 72.00 + 0.75 - 0.36 - 3.50 = 68.89`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md#1`: Execute all `uv` and `pytest` commands strictly from the repository root `.venv`
    without adding new dependencies.
  - `holon-config/world/ruleset.md#2`: Preserve PEP 8 compliance, static typing, and accurate module docstrings in
    `budgets.py`.
  - `holon-config/world/ruleset.md#3`: Explicitly declare all test file modifications (`test_budgets.py` and
    `test_flow.py`) in the planning step (Step 2).
  - `holon-config/world/constraints.md#1` & `docs/safety.md#1`: Respect branch isolation; merging to `main` remains
    gated by the human maintainer.
  - `holon-config/world/constraints.md#3`: Preserve append-only immutability of the three ledgers (`intents.jsonl`,
    `plans.jsonl`, `executions.jsonl`).
- **Potential violations or edge cases:**
  - In `apps/sandbox-executor/tests/test_flow.py`,
    `test_review_overrun_after_consensus_keeps_approval_and_runs_calibration` and
    `test_review_iteration_ceiling_stops_the_loop` use `SteppingClock` with step values (`30.5 * 60` and `1000`)
    calibrated for the old `900.0` s (`15 min`) default; if only the assertions were updated without scaling the
    `SteppingClock` steps past `2 * 7200` s and `7200` s respectively, those tests would no longer trigger the
    overrun/breach paths.
- **Mitigations built into the plan:**
  - Step 2 explicitly updates both the `SteppingClock` step increments and the expected summary/budget assertions in
    `test_flow.py`, then runs
    `uv run pytest apps/sandbox-executor/tests/test_budgets.py apps/sandbox-executor/tests/test_flow.py` before running
    the full suite once via `uv run task test`.
- **Residual risk accepted (and why):**
  - A genuinely hung Stage 4 review iteration without an environment override will wait up to 120 minutes before the
    stage ceiling fires; this is intentional and bounded by turn/stall supervision (`HOLON_TURN_TIMEOUT_SECONDS` and
    `HOLON_TURN_STALL_SECONDS`).
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 1.20
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

This plan increases the default `StageBudgets.review_iteration` ceiling from `900.0` seconds (15 minutes) to `7200.0`
seconds (120 minutes / 2 hours) so that a full Stage 4 review iteration (3-agent ensemble review, native resolver
container execution with unit tests, and GitHub CI probing) can complete without breaching the default stage ceiling
mid-flight. Because total predicted entropy is `1.20` (against an allocated budget of `15.0`), the work is executed as a
single linear 3-step plan without sub-intent decomposition.

---

## Step 1: Update default `review_iteration` stage budget constant and docstring in `budgets.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is a two-line deterministic constant and docstring update in a single module with negligible
  entropy (`0.35`) and near-certain execution success (`0.99`).
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Increase `StageBudgets.review_iteration` default from `900.0` seconds (15 minutes) to `7200.0`
  seconds (120 minutes / 2 hours) and update the module docstring in
  `apps/sandbox-executor/src/sandbox_executor/budgets.py`.
- **Git branch:**
  `I-1793925076-increase-default-review-iteration-stage-budget-to-2-hours/P-1793925096-antigravity-agent-gemini-4-argon/E-step1`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Open `apps/sandbox-executor/src/sandbox_executor/budgets.py`.
2. In the module-level docstring (line 12), update the stage budget summary from `each review iteration 15` to
   `each review iteration 120`.
3. In the `StageBudgets` frozen dataclass (line 65), change `review_iteration: float = 900.0` to
   `review_iteration: float = 7200.0`.
4. Verify that `load_budgets({})` automatically resolves `stages.review_iteration` to `7200.0`, that
   `budgets.stage_seconds("review")` and `budgets.stage_seconds("review_iteration")` both return `7200.0`, and that the
   warning fallback string in `load_budgets` (`getattr(defaults, name) / 60.0`) automatically formats `120 min`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2` (PEP 8 and docstring accuracy), `docs/safety.md` (stage budget
  enforcement).
- **Potential failure modes for this step:**
  - Leaving the module docstring out of sync with `StageBudgets.review_iteration`.
  - Accidentally altering adjacent stage defaults (`intent`, `plan`, `execute`, `calibration`) or `MAX_BUDGET_SECONDS`.
- **Guardrails and early‑abort checks:**
  - Inspect `git diff apps/sandbox-executor/src/sandbox_executor/budgets.py` to confirm only line 12 and line 65 are
    modified.

### Success & Discard Criteria

- **Success:** `StageBudgets().review_iteration == 7200.0`, `load_budgets({}).stage_seconds("review") == 7200.0`, and
  `load_budgets({}).stage_seconds("review_iteration") == 7200.0`.
- **Discard:** Cost exceeds `1.5 * cost_pred` (`1.20`) or any unrelated stage budget constant is altered.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.35  |
| impact_pred         | 40.00 |
| cost_pred           | 0.80  |
| learning_value_pred | 0.50  |
| ev_pred             | 38.95 |

### Step Metrics Rationale

`p_success_pred` is `0.99` and `entropy_pred` is `0.35` because only two lines in
`apps/sandbox-executor/src/sandbox_executor/budgets.py` change and all downstream consumers read
`StageBudgets.review_iteration` dynamically. `impact_pred` is `40.00` because this directly prevents premature Stage 4
mid-flight ceiling breaches during 3-agent ensemble reviews, resolver runs, and CI checks. Using
`EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`,
`EV = 0.99 * 40.00 + 0.5 * 0.50 - 0.3 * 0.35 - 0.80 = 39.60 + 0.25 - 0.105 - 0.80 = 38.945` (rounded to `38.95`).

---

## Step 2: Update unit and flow test assertions in `test_budgets.py` and `test_flow.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** The test updates are tightly coupled to Step 1 and must land in the same execution branch so the test
  suite passes atomically.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Explicitly update the unit test assertions in `apps/sandbox-executor/tests/test_budgets.py` and the
  Stage 4 budget tests in `apps/sandbox-executor/tests/test_flow.py` to expect `7200` seconds (`120 min`) for `review` /
  `review_iteration`.
- **Git branch:**
  `I-1793925076-increase-default-review-iteration-stage-budget-to-2-hours/P-1793925096-antigravity-agent-gemini-4-argon/E-step2`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Open `apps/sandbox-executor/tests/test_budgets.py`:
   - In `TestBudgets.test_defaults` (line 43), update `self.assertEqual(budgets.stage_seconds("review"), 900)` to
     `self.assertEqual(budgets.stage_seconds("review"), 7200)` and also assert
     `self.assertEqual(budgets.stage_seconds("review_iteration"), 7200)`.
2. Open `apps/sandbox-executor/tests/test_flow.py`:
   - In `TestStageBudgets.test_every_stage_reports_elapsed_against_budget` (lines 2040 and 2048), change
     `expected_budgets["review"]` from `900` to `7200`, and update the regex assertion from
     `r"review: halted \(\d+\.\d min / 15 min\)"` to `r"review: halted \(\d+\.\d min / 120 min\)"`.
   - In `TestStageBudgets.test_review_overrun_after_consensus_keeps_approval_and_runs_calibration` (lines 2073, 2094,
     and 2097), update the comment from `2 x 15 min` to `2 x 120 min`, update the `SteppingClock` step from `30.5 * 60`
     to `240.5 * 60` so elapsed time exceeds `2 * 7200` seconds under the injected clock, and update the assertion
     `assert review.budget_seconds == 2 * 900` to `assert review.budget_seconds == 2 * 7200`.
   - In `TestStageBudgets.test_review_iteration_ceiling_stops_the_loop` (lines 2242 and 2245), update
     `SteppingClock(1000)` to `SteppingClock(8000)` (or `7500`) so a single tick exceeds the `7200` s (`120 min`)
     ceiling, and update `assert res["iteration_summaries"]` to match the corresponding formatted elapsed minutes
     against `120 min` (e.g., `["iteration 1 review: failed (133.3 min / 120 min)"]` for `SteppingClock(8000)`).
3. Run the targeted test files first via
   `uv run pytest apps/sandbox-executor/tests/test_budgets.py apps/sandbox-executor/tests/test_flow.py` from the
   repository root.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3` (test modifications must be explicitly declared in the planning
  step; execute via `uv run pytest` from root).
- **Potential failure modes for this step:**
  - Leaving `SteppingClock(step=30.5 * 60)` or `SteppingClock(1000)` unchanged in `test_flow.py`, which would fail to
    trigger the `2 * 7200` s (`240 min`) overrun or the `7200` s (`120 min`) iteration ceiling breach.
- **Guardrails and early‑abort checks:**
  - Run `uv run pytest apps/sandbox-executor/tests/test_budgets.py apps/sandbox-executor/tests/test_flow.py` immediately
    after editing to verify all assertions pass deterministically.

### Success & Discard Criteria

- **Success:** `uv run pytest apps/sandbox-executor/tests/test_budgets.py apps/sandbox-executor/tests/test_flow.py`
  passes with 0 failures.
- **Discard:** Cost exceeds `1.5 * cost_pred` (`2.55`) or any test outside the declared scope is modified.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.55  |
| impact_pred         | 25.00 |
| cost_pred           | 1.70  |
| learning_value_pred | 0.50  |
| ev_pred             | 22.89 |

### Step Metrics Rationale

`p_success_pred` is `0.98` and `entropy_pred` is `0.55` because all four test locations in `test_budgets.py` and
`test_flow.py` are known and use deterministic injected `FakeClock`/`SteppingClock` instances. `impact_pred` is `25.00`
as it maintains regression coverage for the 120-minute Stage 4 review iteration ceiling. Using
`EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`,
`EV = 0.98 * 25.00 + 0.5 * 0.50 - 0.3 * 0.55 - 1.70 = 24.50 + 0.25 - 0.165 - 1.70 = 22.885` (rounded to `22.89`).

---

## Step 3: Update `review_iteration` default in `docs/budgets.md` and run full verification gates

- **Sub‑intent recommendation:** NO
- **Reasoning:** Updating the two references in `docs/budgets.md` and running the standard lint, doc-lint, and unit test
  tasks is low-cost, low-entropy verification that completes the intent.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update `docs/budgets.md` to reflect the 120-minute `review_iteration` default and verify the entire
  workspace passes `uv run task test`, `uv run task lint`, and `uv run task lint-docs`.
- **Git branch:**
  `I-1793925076-increase-default-review-iteration-stage-budget-to-2-hours/P-1793925096-antigravity-agent-gemini-4-argon/E-step3`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Open `docs/budgets.md`:
   - In the `## Budgets and defaults` table (line 25), change the `Review iteration` row default from `15 min` to
     `120 min`.
   - In `## Stage summary lines` (line 227), update the example log snippet from `iteration N review: ok (... / 15 min)`
     to `iteration N review: ok (... / 120 min)`.
2. Ensure Australian English spelling and formatting conventions are preserved throughout `docs/budgets.md`.
3. From the repository root, run the full unit test suite once via `uv run task test`.
4. Run `uv run task lint` and `uv run task lint-docs` from the repository root to verify Python formatting/linting and
   Markdown Prettier checks pass cleanly.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#1` (single root virtual environment),
  `holon-config/world/constraints.md#3` (append-only ledgers untouched except for execution logging).
- **Potential failure modes for this step:**
  - Markdown table alignment or line wrapping in `docs/budgets.md` failing `uv run task lint-docs`
    (`npx prettier --check`).
- **Guardrails and early‑abort checks:**
  - If `uv run task lint-docs` reports formatting differences in `docs/budgets.md`, run `uv run task format` and
    re-verify `uv run task check`.

### Success & Discard Criteria

- **Success:** `docs/budgets.md` documents `120 min` for `HOLON_STAGE_BUDGET_REVIEW_ITERATION_MINUTES`, and
  `uv run task test`, `uv run task lint`, and `uv run task lint-docs` all exit with code 0.
- **Discard:** Cost exceeds `1.5 * cost_pred` (`1.50`).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.30  |
| impact_pred         | 10.00 |
| cost_pred           | 1.00  |
| learning_value_pred | 0.50  |
| ev_pred             | 9.06  |

### Step Metrics Rationale

`p_success_pred` is `0.99` and `entropy_pred` is `0.30` because the documentation edit is limited to two lines in
`docs/budgets.md` followed by standard verification gates. `impact_pred` is `10.00` for keeping operator-facing budget
documentation accurate. Using `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`,
`EV = 0.99 * 10.00 + 0.5 * 0.50 - 0.3 * 0.30 - 1.00 = 9.90 + 0.25 - 0.09 - 1.00 = 9.06`.

---
