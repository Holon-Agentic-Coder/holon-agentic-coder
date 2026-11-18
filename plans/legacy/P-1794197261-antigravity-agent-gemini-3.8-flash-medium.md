# Plan for I-1794197246-dual-ev-calibration-with-and-without-bugs

- **Plan ID:** P-1794197261-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.5.3)
- **Created At:** 2026-11-09T04:07:42.530Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with focused helper extraction to maintain zero net line increase in
  `calibration.py`.
- **Safety priority level:** standard
- **Priority Justification:** Modifies calibration evaluation and reporting logic within sandbox-executor stage 5,
  requiring strict compliance with complexity gates and line budgets without changing external execution behavior.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** Implementation of dual EV metrics and helper refactoring has clear specifications and deterministic
  logic.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 2.10  |
| impact_pred         | 8.50  |
| cost_pred           | 3.20  |
| learning_value_pred | 4.00  |
| ev_pred             | 6.25  |

### Strategy Rationale

The intent requests computing and reporting Dual Actual EV metrics in Stage 5 calibration: Clean/Intrinsic EV
(`ev_clean`), Observed EV (`ev_with_bugs`), and Bug EV Drag (`bug_ev_drag`). The strategy accepts the intent as proposed
without reframing, as the requirements are clear, well-bounded, and provide substantial learning value and observability
into engine-level vs pipeline-level failures.

The architecture decouples dual EV calculation and markdown rendering into a focused helper module
`apps/sandbox-executor/src/sandbox_executor/calibration_ev.py` (< 200 lines). This enables `calibration.py` to maintain
a net line delta <= 0 and zero new `# noqa` suppressions, fulfilling repository modularity guidelines.

Metric derivation from step-level metrics:

- `p_success_pred = 0.95`: Bottleneck step is Step 2 (0.95) due to integrating dataclass fields and refactoring core
  calibration ingestion.
- `entropy_pred = 2.10`: Calculated as the direct sum of step entropies (`0.60 + 0.70 + 0.40 + 0.40 = 2.10`).
- `impact_pred = 8.50`: The maximum impact delivered across steps, establishing long-term observability into pipeline
  bug drag.
- `cost_pred = 3.20`: The direct sum of estimated step resource costs (`0.80 + 0.90 + 0.70 + 0.80 = 3.20`).
- `learning_value_pred = 4.00`: The epistemic gain from establishing dual-metric calibration and bug drag
  quantification.
- `ev_pred = 6.25`: Derived using the standard system EV formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Entropy - Cost`
  `EV = 0.95 * 8.50 + 0.5 * 4.00 - 0.3 * 2.10 - 3.20 = 8.075 + 2.00 - 0.63 - 3.20 = 6.245` (rounded to `6.25`).

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md#1`: Strict target Python version `==3.13.*` and workspace `uv` execution.
  - `holon-config/world/ruleset.md#2`: Strict static typing (`typing`), explanatory docstrings, imports discipline,
    clean architecture.
  - `holon-config/world/ruleset.md#3`: Testing constraints with `pytest==9.1.1` and hermetic test isolation.
  - `holon-config/world/constraints.md#1`: Git branch isolation and rebase discipline.
  - `holon-config/world/constraints.md#3` and `docs/safety.md#invariant-6`: Ledger immutability (append-only ledger;
    zero modification of historical records).
  - Repository complexity gate rules: `C901 <= 10`, `PLR0912 <= 12`, `PLR0913 <= 5`, `PLR0915 <= 50`, zero new `# noqa`
    annotations.
  - Sizing limits: `calibration_ev.py` < 200 lines, `calibration.py` net line delta <= 0.
- **Potential violations or edge cases:**
  - Growing `calibration.py` beyond its current line count.
  - Adding `# noqa` comments to pass ruff complexity gates.
  - Accidental modification of existing summary rows in `executions.jsonl`.
  - Regressions in existing calibration callers expecting `actual.ev` or `actual.p_success`.
- **Mitigations built into the plan:**
  - Extract ledger analysis and markdown blocks into `calibration_ev.py` to achieve negative net lines in
    `calibration.py`.
  - Design concise, single-purpose functions in `calibration_ev.py` to comfortably satisfy all ruff complexity metrics
    without suppressions.
  - Strict read-only parsing of `executions.jsonl` when ingesting historical run records.
  - Explicit backward compatibility assignments `actual.ev = actual.ev_clean` and
    `actual.p_success = actual.p_success_clean`.
- **Residual risk accepted (and why):**
  - Minimal residual risk accepted: changes are constrained strictly to Stage 5 post-execution analysis and do not alter
    execution-time behaviour or git checkout safety.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 2.10
- **Budget Compliance:** The strategy fits within budget (2.10 <= 15.0).

## Plan Description & Strategy

This plan implements Dual Actual EV computation and reporting in Stage 5 calibration across four sequential steps:

1. **Helper Extraction:** Create `apps/sandbox-executor/src/sandbox_executor/calibration_ev.py` (< 200 lines) with
   dataclasses, dual-EV calculation logic, and ledger inspection routines.
2. **Dataclass Extension & Engine Integration:** Extend `ActualMetrics` and `CalibrationDeltas` in `calibration.py` with
   dual EV and bug drag fields, delegating calculation to `calibration_ev.py` and maintaining net line delta <= 0.
3. **Report Generation:** Update `format_markdown_report` to display both Clean EV, Observed EV, and Bug EV Drag in the
   summary table and mathematical derivations.
4. **Testing & Verification:** Add comprehensive unit tests in `test_calibration_dual_ev.py` covering clean runs,
   intermediate failure recovery, and complete failures, followed by full lint and test execution.

---

## Step 1: Create focused helper module `calibration_ev.py` for dual EV evaluation

- **Sub‑intent recommendation:** NO
- **Reasoning:** Modular helper extraction is well-scoped, self-contained (< 200 lines), and carries low blast radius
  within the sandbox executor.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Create `apps/sandbox-executor/src/sandbox_executor/calibration_ev.py` with dual EV calculation logic,
  ledger scanning helpers, and report formatting routines.
- **Git branch:** `I-1794197246-dual-ev-calibration-with-and-without-bugs/step-1-calibration-ev-helper`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create new Python module `apps/sandbox-executor/src/sandbox_executor/calibration_ev.py` with docstrings explaining
   its purpose and domain contracts.
2. Implement data structure `DualEVMetrics` containing typed fields:
   - `ev_clean: float`
   - `ev_with_bugs: float`
   - `p_success_clean: float`
   - `p_success_with_bugs: float`
   - `impact_clean: float`
   - `impact_with_bugs: float`
   - `bug_ev_drag: float`
3. Implement `evaluate_dual_ev_outcomes(records, execution_id, plan_branch, predicted_impact, cost_effort)`:
   - Filter summary records (`entry_type == 'summary'`) matching `execution_id` or `plan_branch`.
   - Track all matching summary records for both execution (`E-...`) and review (`REV-...`) phases.
   - For Clean EV (`ev_clean`): Find the final converged outcome record (highest revision / latest timestamp). If status
     is `'success'` or `'halted_for_human'` with `exit_code == 0`, mark `p_success_clean = 1.0` and
     `impact_clean = predicted_impact`. If final converged outcome ended in `status == 'failed'` or non-zero `exit_code`
     without subsequent recovery, mark `p_success_clean = 0.0` and `impact_clean = 0.0`. Compute
     `ev_clean = round((impact_clean * p_success_clean) - cost_effort, 2)`.
   - For Observed EV (`ev_with_bugs`): Inspect all matching summary records. If any intermediate or prior record has
     `status == 'failed'` or `exit_code != 0`, set `p_success_with_bugs = 0.0`, `impact_with_bugs = 0.0`, and compute
     `ev_with_bugs = round(0.0 - cost_effort, 2)`. If zero failure records exist across the entire execution history,
     set `p_success_with_bugs = p_success_clean`, `impact_with_bugs = impact_clean`, and `ev_with_bugs = ev_clean`.
   - For Bug EV Drag: Compute `bug_ev_drag = round(ev_clean - ev_with_bugs, 2)`.
4. Ensure `calibration_ev.py` stays under 200 physical lines, strictly follows cyclomatic complexity limits
   (`C901 <= 10`, `PLR0912 <= 12`, `PLR0913 <= 5`, `PLR0915 <= 50`), and contains zero `# noqa` annotations.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (If the core dual EV evaluation engine fails, downstream calibration and reporting cannot
  function).

### Safety & Constraint Considerations

- **Relevant rules:** `world/ruleset.md#2` (Coding Conventions, strict typing, docstring requirements),
  `docs/safety.md#invariant-6` (Ledger immutability - reads ledger records without mutating ledger files).
- **Potential failure modes for this step:**
  - Logic parsing summary rows fails to identify intermediate failures due to ledger format differences.
  - Sizing exceeds the 200-line budget.
- **Guardrails and early‑abort checks:**
  - Enforce line-count assertion (< 200 lines) and complexity check before proceeding.

### Success & Discard Criteria

- **Success:** `calibration_ev.py` is created with valid types, correct dual EV derivation algorithm, passes
  `ruff check`, and stays strictly under 200 lines.
- **Discard:** Discard if cyclomatic complexity cannot be maintained under thresholds without `# noqa` or file exceeds
  line limits.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.60  |
| impact_pred         | 7.50  |
| cost_pred           | 0.80  |
| learning_value_pred | 3.50  |
| ev_pred             | 7.97  |

### Step Metrics Rationale

- Metric formula derivation: `EV = P(success) * Impact + mu * LearningValue - lambda * Entropy - Cost`
- `EV = 0.96 * 7.50 + 0.5 * 3.50 - 0.3 * 0.60 - 0.80 = 7.20 + 1.75 - 0.18 - 0.80 = 7.97`
- High probability (0.96) and low entropy (0.60) reflect the self-contained nature of creating a single new helper
  module from well-defined mathematical specifications.

---

## Step 2: Extend `ActualMetrics` and `CalibrationDeltas` and integrate dual EV in `calibration.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Updating dataclass fields and refactoring `calibration.py` callers is a direct code integration with
  backward compatibility guarantees.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Extend `ActualMetrics` and `CalibrationDeltas` dataclasses with dual EV fields, update
  `parse_actual_metrics` and `compute_calibration_deltas`, and ensure net line delta in `calibration.py` is <= 0.
- **Git branch:** `I-1794197246-dual-ev-calibration-with-and-without-bugs/step-2-integrate-dual-ev`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/calibration.py`:
   - Extend `ActualMetrics` with new float fields:
     - `ev_clean: float = 0.0`
     - `ev_with_bugs: float = 0.0`
     - `p_success_clean: float = 0.0`
     - `p_success_with_bugs: float = 0.0`
     - `impact_clean: float = 0.0`
     - `impact_with_bugs: float = 0.0`
     - `bug_ev_drag: float = 0.0`
   - Extend `CalibrationDeltas` with new float fields:
     - `delta_ev_clean: float = 0.0`
     - `delta_ev_with_bugs: float = 0.0`
     - `bug_ev_drag: float = 0.0`
2. Update `parse_actual_metrics()`:
   - Calculate `cost_effort = round(actual.cost + (lambda_p * actual.entropy) - (mu_p * actual.learning_value), 2)`.
   - Call `calibration_ev.evaluate_dual_ev_outcomes()` passing the loaded summary records, `execution_id`,
     `plan_branch`, `predicted.impact`, and `cost_effort`.
   - Assign the computed dual EV metrics to `actual.p_success_clean`, `actual.p_success_with_bugs`,
     `actual.impact_clean`, `actual.impact_with_bugs`, `actual.ev_clean`, `actual.ev_with_bugs`, and
     `actual.bug_ev_drag`.
   - Ensure backward compatibility invariant: explicitly assign `actual.p_success = actual.p_success_clean` and
     `actual.ev = actual.ev_clean`.
3. Update `compute_calibration_deltas()`:
   - Compute `delta_ev_clean = round(actual.ev_clean - predicted.ev, 2)`.
   - Compute `delta_ev_with_bugs = round(actual.ev_with_bugs - predicted.ev, 2)`.
   - Set `deltas.delta_ev = delta_ev_clean`, `deltas.delta_ev_clean = delta_ev_clean`,
     `deltas.delta_ev_with_bugs = delta_ev_with_bugs`, and `deltas.bug_ev_drag = actual.bug_ev_drag`.
4. Ensure Net Line Delta Discipline:
   - Extract the ledger scanning / parsing loop and/or markdown derivation sections from `calibration.py` into
     `calibration_ev.py`.
   - Verify `calibration.py` net line delta is <= 0 compared to `main` branch.
   - Verify zero new `# noqa` annotations are introduced.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Downstream reporting and existing tests depend on `ActualMetrics` and `CalibrationDeltas`).

### Safety & Constraint Considerations

- **Relevant rules:** `world/ruleset.md#2` (backward compatibility and imports discipline), `flow_module_budgets.json`
  line budgets.
- **Potential failure modes for this step:**
  - Breaking existing callers of `ActualMetrics` or `CalibrationDeltas` if positional arguments are used.
  - Exceeding net line delta limit in `calibration.py`.
- **Guardrails and early‑abort checks:**
  - Verify that default values exist on all newly added dataclass fields.
  - Run line-count diff check `git diff --stat apps/sandbox-executor/src/sandbox_executor/calibration.py` to ensure net
    delta <= 0.

### Success & Discard Criteria

- **Success:** `ActualMetrics` and `CalibrationDeltas` correctly compute dual EV, existing calibration tests pass
  without regression, and `calibration.py` net line delta is <= 0.
- **Discard:** Discard if existing calibration behavior breaks or `calibration.py` increases in total lines.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.70  |
| impact_pred         | 8.00  |
| cost_pred           | 0.90  |
| learning_value_pred | 3.50  |
| ev_pred             | 8.24  |

### Step Metrics Rationale

- Metric formula derivation: `EV = P(success) * Impact + mu * LearningValue - lambda * Entropy - Cost`
- `EV = 0.95 * 8.00 + 0.5 * 3.50 - 0.3 * 0.70 - 0.90 = 7.60 + 1.75 - 0.21 - 0.90 = 8.24`
- Low entropy (0.70) reflects straightforward dataclass extension and delegating ledger evaluation logic to the helper
  module.

---

## Step 3: Update markdown calibration report generation for Dual EV and Bug EV Drag

- **Sub‑intent recommendation:** NO
- **Reasoning:** Updating markdown rendering logic is low risk and purely presentational within the calibration report
  generator.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update `format_markdown_report` (and helper in `calibration_ev.py`) to render clean EV, observed EV
  with bugs, and Bug EV Drag in the summary table and mathematical derivations.
- **Git branch:** `I-1794197246-dual-ev-calibration-with-and-without-bugs/step-3-markdown-rendering`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `calibration_ev.py` (or `format_markdown_report`):
   - Update Executive Calibration Summary table:
     - Render row for **Expected Value — Clean (without bugs)** showing predicted EV, clean actual EV (`ev_clean`),
       delta error, accuracy rating, and bias direction.
     - Render row for **Expected Value — Observed (with bugs included)** showing predicted EV, observed actual EV
       (`ev_with_bugs`), delta error, accuracy rating, and bias direction.
     - Render row for **Bug EV Drag** showing the drag penalty (`bug_ev_drag`).
   - Update Section 2 (Mathematical Derivations & Calibration Errors):
     - Render EV Clean derivation formula:
       `EV_clean = P(success_clean) * Impact_clean + mu * LearningValue - lambda * Entropy - Cost`
     - Render EV Observed derivation formula:
       `EV_with_bugs = P(success_with_bugs) * Impact_with_bugs + mu * LearningValue - lambda * Entropy - Cost`
     - Render Bug EV Drag formula: `Bug EV Drag = round(EV_clean - EV_with_bugs, 2)`
2. Ensure markdown hygiene:
   - Enclose all arithmetic and formulas within markdown backticks or standard LaTeX math blocks.
   - Avoid unescaped underscores in text to ensure compliance with CommonMark and Prettier formatting checks.
3. In `generate_calibration()`, ensure `report_dict` contains all required dual EV fields (`ev_clean`, `ev_with_bugs`,
   `p_success_clean`, `p_success_with_bugs`, `impact_clean`, `impact_with_bugs`, `bug_ev_drag`, `delta_ev_clean`,
   `delta_ev_with_bugs`).

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** NO (Formatting failure would only affect visual output, but correctness is required for automated
  report parsers).

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md#invariant-6`, Prettier markdown validation rules.
- **Potential failure modes for this step:**
  - CommonMark oscillation in Prettier due to unescaped math expressions.
  - Inconsistency between summary table values and mathematical derivation outputs.
- **Guardrails and early‑abort checks:**
  - Validate output with `npx prettier --check` on generated reports.

### Success & Discard Criteria

- **Success:** Calibration reports accurately render both Clean EV, Observed EV, and Bug EV Drag in summary table and
  derivation sections, passing Prettier checks.
- **Discard:** Discard if report formatting violates Prettier markdown standards or introduces regex parsing regressions
  in report consumers.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.40  |
| impact_pred         | 7.00  |
| cost_pred           | 0.70  |
| learning_value_pred | 3.00  |
| ev_pred             | 7.40  |

### Step Metrics Rationale

- Metric formula derivation: `EV = P(success) * Impact + mu * LearningValue - lambda * Entropy - Cost`
- `EV = 0.96 * 7.00 + 0.5 * 3.00 - 0.3 * 0.40 - 0.70 = 6.72 + 1.50 - 0.12 - 0.70 = 7.40`
- Low entropy (0.40) and high probability (0.96) reflect standard report string formatting with zero stateful impact.

---

## Step 4: Add comprehensive unit tests in `test_calibration_dual_ev.py` and run full validation

- **Sub‑intent recommendation:** NO
- **Reasoning:** Adding a dedicated unit test suite provides fast, targeted verification and isolates new test fixtures
  from existing test files.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Implement complete unit test coverage for dual EV calculations, bug drag detection, and report
  rendering in `apps/sandbox-executor/tests/test_calibration_dual_ev.py`, then run the full validation suite.
- **Git branch:** `I-1794197246-dual-ev-calibration-with-and-without-bugs/step-4-dual-ev-tests`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create test file `apps/sandbox-executor/tests/test_calibration_dual_ev.py`.
2. Implement unit test cases:
   - Test Clean Runs:
     - Synthetic ledger with single `E-...` record where `status == 'success'` and `exit_code == 0`.
     - Synthetic ledger where `status == 'halted_for_human'` and `exit_code == 0`.
     - Assert that `ev_clean == ev_with_bugs`, `bug_ev_drag == 0.0`, `p_success_clean == 1.0`, and
       `p_success_with_bugs == 1.0`.
   - Test Runs with Intermediate Pipeline / Review Bugs:
     - Synthetic ledger with an early `REV-...` or `E-...` record having `status == 'failed'` or non-zero `exit_code`,
       followed by a subsequent converged recovery record with `status == 'success'`.
     - Assert that `ev_clean > 0`, `ev_with_bugs < 0` (equaling `-cost_effort`), and
       `bug_ev_drag == round(ev_clean - ev_with_bugs, 2)`.
   - Test Converged Failure Runs:
     - Synthetic ledger where final converged record has `status == 'failed'`.
     - Assert that `p_success_clean == 0.0`, `ev_clean == -cost_effort`, `ev_with_bugs == -cost_effort`, and
       `bug_ev_drag == 0.0`.
   - Test Report Formatting:
     - Invoke `format_markdown_report()` with dual EV test payload.
     - Assert presence of `Expected Value — Clean (without bugs)`, `Expected Value — Observed (with bugs included)`, and
       `Bug EV Drag`.
   - Test Code Gate Compliance:
     - Assert physical line count of `apps/sandbox-executor/src/sandbox_executor/calibration_ev.py` is < 200 lines.
     - Assert net line delta of `apps/sandbox-executor/src/sandbox_executor/calibration.py` is <= 0.
     - Assert zero new `# noqa` additions.
3. Test Execution Discipline:
   - Run affected tests first:
     `uv run pytest apps/sandbox-executor/tests/test_calibration_dual_ev.py apps/sandbox-executor/tests/test_calibration.py`.
   - Run full unit test suite once: `uv run task test`.
   - Run linter and formatting tasks: `uv run task check` (`uv lock --check`, `git diff uv.lock`, `ruff check`,
     `ruff format --check`, prettier check).

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** YES (Final gatekeeper confirming full functional correctness and system invariants).

### Safety & Constraint Considerations

- **Relevant rules:** `world/ruleset.md#3` (Testing Constraints: `pytest==9.1.1`), `docs/safety.md` (hermetic
  execution).
- **Potential failure modes for this step:**
  - Mocked ledger JSONL records deviating from realistic schema.
  - Flaky test timing or worktree path leakage.
- **Guardrails and early‑abort checks:**
  - Use `hermetic_fixtures` or temporary directories for isolated ledger files.

### Success & Discard Criteria

- **Success:** All test cases in `test_calibration_dual_ev.py` pass cleanly, `uv run task test` passes, and
  `uv run task check` succeeds with zero warnings or errors.
- **Discard:** Discard if any test failure occurs or regressions in existing calibration tests are detected.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.40  |
| impact_pred         | 8.50  |
| cost_pred           | 0.80  |
| learning_value_pred | 4.00  |
| ev_pred             | 9.33  |

### Step Metrics Rationale

- Metric formula derivation: `EV = P(success) * Impact + mu * LearningValue - lambda * Entropy - Cost`
- `EV = 0.97 * 8.50 + 0.5 * 4.00 - 0.3 * 0.40 - 0.80 = 8.245 + 2.00 - 0.12 - 0.80 = 9.33`
- High probability (0.97) and high impact (8.50) because rigorous automated testing ensures long-term regression safety
  for calibration across all future intents.
