# Plan for I-1793334797-hac-ruff-complexity-gate-with-inline-baseline

- **Plan ID:** P-1793334817-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.4.10)
- **Created At:** 2026-10-30T04:33:38.557Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with focus on low-risk incremental adoption of ruff complexity lints and strict
  baseline ratcheting.
- **Safety priority level:** standard
- **Priority Justification:** The intent introduces lint checks, inline annotations, a test ratchet, and documentation.
  No privileged execution boundaries, security invariants, or external dependencies are altered.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The intent requires deterministic configuration of ruff, mechanical inline baseline comments, a
  counting ratchet test, and architecture documentation without uncertain or open-ended investigations.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 2.10  |
| impact_pred         | 80.0  |
| cost_pred           | 4.50  |
| learning_value_pred | 4.00  |
| ev_pred             | 72.07 |

### Strategy Rationale

The intent is accepted as-is (`ACCEPT_AS_IS`). Currently, `apps/sandbox-executor/src` does not gate function complexity,
allowing cyclomatic complexity (C901) and complex structure (PLR0911, PLR0912, PLR0913, PLR0915) to grow unchecked.
Introducing these rules with per-file-ignores would obscure complexity at the file level and cause frequent
configuration churn. Instead, establishing an inline `# noqa: <codes>` baseline directly on the `def` lines of existing
offenders makes complexity explicitly visible at each function declaration. A ratchet pytest ensures that the baseline
count cannot increase in the future, establishing a strict ratchet-down invariant where functions can only be simplified
over time.

The plan decomposes this intent into 5 ordered, coherent steps:

- Step 1 configures `pyproject.toml` to enforce `C901` (with `lint.mccabe.max-complexity = 10`), `PLR0911` (max 6
  returns), `PLR0912` (max 12 branches), `PLR0913` (max 5 arguments), and `PLR0915` (max 50 statements) at ruff defaults
  without adding new dependencies or per-file-ignores.
- Step 2 scans `apps/sandbox-executor/src` for current offenders and adds inline `# noqa: <codes>` comments on their
  `def` lines so `uv run ruff check apps/sandbox-executor/src` passes cleanly with zero violations.
- Step 3 implements `apps/sandbox-executor/tests/test_complexity_gate.py` to count all inline complexity `# noqa`
  directives in `apps/sandbox-executor/src` and assert that the count does not exceed the baseline.
- Step 4 documents the complexity thresholds, rationale, and ratchet mechanism in a dedicated section in
  `docs/architecture.md`.
- Step 5 executes verification through targeted tests, full test suite (`uv run task test`), ruff linting, and
  documentation formatting checks (`uv run task check`).

#### Metric Derivations & Bottleneck Identification

- **P(success):** `0.94`. Step 2 is the bottleneck (`p_success_pred = 0.95`) because applying inline `# noqa` comments
  across 56+ functions in multiple source files requires exact placement on the `def` line and proper combination of
  multiple violation codes (e.g. `C901, PLR0912, PLR0915`) without introducing formatting defects. Overall success
  probability is `0.94`, reflecting high confidence in deterministic tooling and compiler/linter feedback.
- **Entropy:** `2.10`. Aggregated as the exact sum of step entropies: `0.30 + 0.80 + 0.50 + 0.20 + 0.30 = 2.10`. This is
  well within the allocated entropy budget of `5.0`.
- **Impact:** `80.0`. Gating complexity permanently prevents codebase degradation and establishes an automated ratchet
  mechanism to reduce technical debt systematically.
- **Cost:** `4.50`. Derived as the exact sum of step costs: `0.50 + 1.80 + 1.20 + 0.40 + 0.60 = 4.50`.
- **Learning Value:** `4.00`. Provides concrete data on function complexity distributions across
  `apps/sandbox-executor/src` and establishes a reusable ratchet pattern for other subprojects.
- **Expected Value (EV):** `72.07`, calculated using `lambda = 0.3` and `mu = 0.5` from
  `holon-config/metrics/ev_config.json`:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.94 * 80.0 + 0.5 * 4.00 - 0.3 * 2.10 - 4.50 = 75.20 + 2.00 - 0.63 - 4.50 = 72.07`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md#1` (Python 3.13 runtime, uv workspace dependencies; no new dependencies added)
  - `holon-config/world/ruleset.md#2` (PEP 8 compliance, typing, docstrings)
  - `holon-config/world/ruleset.md#3` (Testing with pytest 9.1.1, test declarations in plan)
  - `holon-config/world/constraints.md#1` (Git branch isolation and commit boundaries)
  - `docs/safety.md#git-discipline` (Execution confined strictly to branch prefix)
  - `.agents/AGENTS.md` (Use `uv run pytest`, single root virtual environment, `uv run task check`, no throwaway
    scripts)
- **Potential violations or edge cases:**
  - Modifying code semantics when adding inline comments to multiline function headers.
  - Adding `# noqa` comments that unintentionally suppress other non-complexity ruff lints.
  - Using per-file-ignores in `pyproject.toml`, which is explicitly forbidden by acceptance criteria.
  - Inaccurate ratchet counting logic that fails to distinguish complexity `# noqa` from other `# noqa` directives (e.g.
    `# noqa: F401`).
- **Mitigations built into the plan:**
  - Step 1 verifies that no `per-file-ignores` are added to `pyproject.toml`.
  - Step 2 ensures `# noqa` comments specify exact rule codes (e.g. `# noqa: C901, PLR0912`) and are placed exclusively
    on `def` lines.
  - Step 3 implements AST-aware or line-specific regex matching in `test_complexity_gate.py` that specifically filters
    for `C901`, `PLR0911`, `PLR0912`, `PLR0913`, and `PLR0915`.
  - Step 5 verifies that the entire test suite passes and zero non-complexity lints are masked.
- **Residual risk accepted (and why):** Minimal residual risk of line length reformatting if `# noqa` comments push a
  `def` line over 120 characters; mitigated by running `ruff format` and `ruff check`.
- **Allocated Entropy Budget:** 5.0
- **Predicted Plan Entropy:** 2.10
- **Budget Compliance:** The strategy fits within budget (`2.10 <= 5.0`).

## Plan Description & Strategy

This plan establishes a strict complexity gate for `apps/sandbox-executor/src` using Ruff and an inline `# noqa`
baseline ratchet test.

Currently, the codebase has no guardrails against cyclomatic complexity or deep branching, allowing functions to expand
in complexity without automated warning. This intent implements:

1. Ruff configuration in `pyproject.toml` selecting `C901` (McCabe cyclomatic complexity with limit 10) and Pylint
   Refactor rules `PLR0911` (too many returns > 6), `PLR0912` (too many branches > 12), `PLR0913` (too many arguments >
   5), and `PLR0915` (too many statements > 50).
2. Targeted inline `# noqa: <codes>` annotations applied on the `def` lines of all current offending functions across
   `apps/sandbox-executor/src`, ensuring that existing code continues to function while surfacing technical debt
   directly in source context.
3. A dedicated ratchet test in `apps/sandbox-executor/tests/test_complexity_gate.py` that scans all Python source files
   in `apps/sandbox-executor/src`, parses inline `# noqa` markers matching the complexity rules, and asserts that the
   count does not exceed the baseline. If new code exceeds complexity limits or attempts to add an unratcheted `# noqa`,
   CI fails.
4. Architectural documentation in `docs/architecture.md` defining the thresholds, rationale, and ratchet policy.
5. Full repository verification ensuring all tests, linters, and Prettier checks pass cleanly.

---

## Step 1: Configure ruff complexity rules in pyproject.toml

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small declarative configuration update in `pyproject.toml` with zero runtime risk.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Configure ruff in `pyproject.toml` to select complexity rules (C901, PLR0911, PLR0912, PLR0913,
  PLR0915) with `mccabe.max-complexity = 10` and ruff defaults for PLR rules without adding per-file-ignores.
- **Git branch:**
  `I-1793334797-hac-ruff-complexity-gate-with-inline-baseline/P-1793334817-antigravity-agent-gemini-3.8-flash-medium/E-{timestamp}-config-ruff/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Open `pyproject.toml` located at repository root.
2. Under `[tool.ruff.lint]`, update the `select` array to include the complexity rule identifiers: "C901", "PLR0911",
   "PLR0912", "PLR0913", and "PLR0915".
3. Add a new configuration section `[tool.ruff.lint.mccabe]` setting `max-complexity = 10`.
4. Validate that `PLR0911` (max 6 returns), `PLR0912` (max 12 branches), `PLR0913` (max 5 arguments), and `PLR0915` (max
   50 statements) remain at ruff default values without introducing extra overrides.
5. Explicitly ensure no `per-file-ignores` are configured for any complexity rules in `pyproject.toml`.
6. Verify file formatting adheres to standard TOML structure.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#1`, `docs/safety.md#git-discipline`
- **Potential failure modes for this step:** Syntax error in TOML configuration or accidental exclusion of other lint
  rules.
- **Guardrails and early‑abort checks:** Validate TOML parsing using `uv run ruff check --help` or dry-run inspection.

### Success & Discard Criteria

- **Success:** `pyproject.toml` cleanly declares `C901`, `PLR0911`, `PLR0912`, `PLR0913`, and `PLR0915` with
  `max-complexity = 10` and no per-file-ignores.
- **Discard:** Discard if ruff configuration requires adding external plugins or third-party dependencies.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.30  |
| impact_pred         | 70.0  |
| cost_pred           | 0.50  |
| learning_value_pred | 1.00  |
| ev_pred             | 69.21 |

### Step Metrics Rationale

Calculated via: `EV = 0.99 * 70.0 + 0.5 * 1.00 - 0.3 * 0.30 - 0.50 = 69.30 + 0.50 - 0.09 - 0.50 = 69.21`. This is a
standard declarative configuration update with minimal entropy (`0.30`) and high success probability (`0.99`).

---

## Step 2: Survey and annotate existing complexity offenders in apps/sandbox-executor/src

- **Sub‑intent recommendation:** NO
- **Reasoning:** Scoped refactor applying inline comments without altering control flow or function signatures.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add inline `# noqa: <codes>` comments on the `def` lines of all existing offending functions in
  `apps/sandbox-executor/src` so that `uv run ruff check apps/sandbox-executor/src` passes cleanly with zero violations.
- **Git branch:**
  `I-1793334797-hac-ruff-complexity-gate-with-inline-baseline/P-1793334817-antigravity-agent-gemini-3.8-flash-medium/E-{timestamp}-inline-baseline/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run `uv run ruff check apps/sandbox-executor/src` to identify all functions triggering C901, PLR0911, PLR0912,
   PLR0913, or PLR0915 violations.
2. Group violations by file and function definition line.
3. For each offending function definition, append an inline `# noqa: <codes>` comment directly on the `def` line (e.g.
   `# noqa: C901`, `# noqa: C901, PLR0912`, or `# noqa: C901, PLR0912, PLR0915`).
4. If a function header spans multiple lines, place the `# noqa` comment on the primary `def` line where ruff expects
   the suppression.
5. If an existing `# noqa` comment exists on that line for other lint codes, append the complexity codes separated by
   commas without removing existing codes.
6. Record the exact baseline count of annotated functions and rules across `apps/sandbox-executor/src`.
7. Re-run `uv run ruff check apps/sandbox-executor/src` to confirm zero remaining violations across the source tree.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (P(success) = 0.95; failure to annotate any offender leaves ruff red)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2`, `docs/safety.md#git-discipline`
- **Potential failure modes for this step:** Misplaced `# noqa` comments on closing parentheses rather than `def` lines,
  or syntax corruption during line editing.
- **Guardrails and early‑abort checks:** Ensure `uv run ruff check apps/sandbox-executor/src` reports 0 errors and git
  diff shows only comment additions.

### Success & Discard Criteria

- **Success:** `uv run ruff check apps/sandbox-executor/src` produces zero errors with every offender explicitly
  annotated on its `def` line.
- **Discard:** Discard if code logic or function execution semantics are altered during comment insertion.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.80  |
| impact_pred         | 75.0  |
| cost_pred           | 1.80  |
| learning_value_pred | 2.50  |
| ev_pred             | 70.46 |

### Step Metrics Rationale

Calculated via: `EV = 0.95 * 75.0 + 0.5 * 2.50 - 0.3 * 0.80 - 1.80 = 71.25 + 1.25 - 0.24 - 1.80 = 70.46`. This step
handles 56+ functions across multiple files, representing the plan's bottleneck. Entropy is slightly higher (`0.80`) due
to multi-file touchpoints, but success probability remains high (`0.95`).

---

## Step 3: Implement baseline ratchet pytest in apps/sandbox-executor/tests/test_complexity_gate.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Hermetic test file implementation following standard pytest patterns within existing test structure.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add `apps/sandbox-executor/tests/test_complexity_gate.py` that counts all inline `# noqa` markers for
  C901 and PLR0911-0915 across `apps/sandbox-executor/src` and asserts that the count does not exceed the recorded
  baseline.
- **Git branch:**
  `I-1793334797-hac-ruff-complexity-gate-with-inline-baseline/P-1793334817-antigravity-agent-gemini-3.8-flash-medium/E-{timestamp}-ratchet-test/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create a new test file at `apps/sandbox-executor/tests/test_complexity_gate.py`.
2. Define a constant representing the recorded baseline count of complexity `# noqa` directives established in Step 2.
3. Implement a helper function to walk all Python (`.py`) files inside `apps/sandbox-executor/src`.
4. Implement parsing logic (using regex or AST token scanning) to inspect lines with function definitions (`def `) and
   extract inline `# noqa` directives matching `C901`, `PLR0911`, `PLR0912`, `PLR0913`, or `PLR0915`.
5. Implement `test_complexity_noqa_baseline_ratchet`:
   - Compute the current total count of complexity `# noqa` occurrences in `apps/sandbox-executor/src`.
   - Assert that `current_count <= BASELINE_COUNT`, outputting a descriptive error message indicating that adding new
     complex functions or unapproved `# noqa` directives is forbidden.
   - Assert that if `current_count < BASELINE_COUNT`, a suggestion is made to ratchet down the baseline constant in
     `test_complexity_gate.py`.
6. Implement a unit test verifying that the counting logic correctly identifies complexity `# noqa` comments while
   ignoring unrelated `# noqa` comments (such as `# noqa: F401` or `# noqa: E501`).
7. Implement a failure-mode test asserting that an artificially injected complexity `# noqa` comment causes the ratchet
   assertion to fail.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3`, `.agents/AGENTS.md` (pytest discipline)
- **Potential failure modes for this step:** Brittle parsing regex missing multiline function declarations or counting
  non-complexity `# noqa` comments.
- **Guardrails and early‑abort checks:** Validate test locally with
  `uv run pytest apps/sandbox-executor/tests/test_complexity_gate.py`.

### Success & Discard Criteria

- **Success:** `apps/sandbox-executor/tests/test_complexity_gate.py` passes cleanly on current baseline and fails if
  baseline is exceeded.
- **Discard:** Discard if the ratchet test relies on network or non-deterministic file discovery.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.50  |
| impact_pred         | 80.0  |
| cost_pred           | 1.20  |
| learning_value_pred | 2.00  |
| ev_pred             | 76.45 |

### Step Metrics Rationale

Calculated via: `EV = 0.96 * 80.0 + 0.5 * 2.00 - 0.3 * 0.50 - 1.20 = 76.80 + 1.00 - 0.15 - 1.20 = 76.45`. Adds an
automated enforcement gate with high impact (`80.0`) and low entropy (`0.50`).

---

## Step 4: Document complexity thresholds and ratchet mechanism in docs/architecture.md

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure documentation update in `docs/architecture.md` with zero runtime risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add a dedicated section to `docs/architecture.md` documenting the complexity gate thresholds,
  rationale, inline baseline strategy, and ratchet enforcement.
- **Git branch:**
  `I-1793334797-hac-ruff-complexity-gate-with-inline-baseline/P-1793334817-antigravity-agent-gemini-3.8-flash-medium/E-{timestamp}-docs/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Open `docs/architecture.md`.
2. Add a new dedicated section titled "Complexity Gate and Ratchet Baseline".
3. Document the specific ruff complexity rules and thresholds:
   - McCabe Cyclomatic Complexity (`C901`): max complexity 10.
   - Pylint Return Count (`PLR0911`): max 6 return statements.
   - Pylint Branch Count (`PLR0912`): max 12 branches.
   - Pylint Argument Count (`PLR0913`): max 5 arguments.
   - Pylint Statement Count (`PLR0915`): max 50 statements.
4. Explain the architectural rationale for choosing inline `# noqa` baselines on `def` lines over `per-file-ignores` in
   `pyproject.toml` (function-level granularity, explicit technical debt visibility, avoiding blanket file exemptions).
5. Document the ratchet enforcement in `test_complexity_gate.py` which prevents complexity baseline expansion and
   requires ratcheting down when functions are refactored.
6. Verify formatting passes Prettier markdown checks.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2`, `.agents/AGENTS.md` (prettier validation)
- **Potential failure modes for this step:** Prettier formatting oscillations or unescaped markdown characters.
- **Guardrails and early‑abort checks:** Run `npx prettier --check docs/architecture.md`.

### Success & Discard Criteria

- **Success:** `docs/architecture.md` includes clear, accurate documentation of the complexity gate and passes Prettier
  check.
- **Discard:** Discard if documentation contradicts configured thresholds in `pyproject.toml` or
  `test_complexity_gate.py`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.20  |
| impact_pred         | 65.0  |
| cost_pred           | 0.40  |
| learning_value_pred | 1.00  |
| ev_pred             | 64.39 |

### Step Metrics Rationale

Calculated via: `EV = 0.99 * 65.0 + 0.5 * 1.00 - 0.3 * 0.20 - 0.40 = 64.35 + 0.50 - 0.06 - 0.40 = 64.39`. Documentation
update with low cost (`0.40`), minimal entropy (`0.20`), and high probability of success (`0.99`).

---

## Step 5: Full test suite verification and repository hygiene checks

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard verification and lint gating step executing pre-existing task runner commands.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Verify all acceptance criteria: clean ruff check, ratchet pytest passing, full test suite passing
  with zero failures, and clean prettier checks.
- **Git branch:**
  `I-1793334797-hac-ruff-complexity-gate-with-inline-baseline/P-1793334817-antigravity-agent-gemini-3.8-flash-medium/E-{timestamp}-verification/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run targeted ruff check: `uv run ruff check apps/sandbox-executor/src` to assert zero violations.
2. Run targeted test: `uv run pytest apps/sandbox-executor/tests/test_complexity_gate.py` to assert ratchet passes.
3. Run the full repository test suite once using `uv run task test`
   (`pytest -n auto --dist worksteal -m "not integration_test and not stress"`).
4. Run task hygiene checks: `uv run task check` (runs lock check, git diff uv.lock, ruff check, ruff format check, and
   prettier doc check).
5. Ensure zero errors across all checks and verify clean git status before declaring completion.

### Dependencies & Criticality

- **Depends on:** Steps 1, 2, 3, 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `.agents/AGENTS.md` (test-run discipline, task runner `task check`), `docs/safety.md`
- **Potential failure modes for this step:** Regression in unrelated unit tests or unstaged formatting issues.
- **Guardrails and early‑abort checks:** Ensure all commands run strictly from repository root in single root virtual
  environment.

### Success & Discard Criteria

- **Success:** All test suites pass cleanly, ruff checks pass with zero violations, and `task check` reports clean
  status.
- **Discard:** Discard if any test failure occurs and cannot be resolved within scope.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.30  |
| impact_pred         | 70.0  |
| cost_pred           | 0.60  |
| learning_value_pred | 1.00  |
| ev_pred             | 68.41 |

### Step Metrics Rationale

Calculated via: `EV = 0.98 * 70.0 + 0.5 * 1.00 - 0.3 * 0.30 - 0.60 = 68.60 + 0.50 - 0.09 - 0.60 = 68.41`. Final gate
step confirming end-to-end coherence and compliance across the entire workspace.
