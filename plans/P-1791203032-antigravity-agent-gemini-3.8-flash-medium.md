# Plan for I-1791203018-optimize-test-performance

- **Plan ID:** P-1791203032-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-10-05T12:23:52.927Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted empirical validation of boundary stride cut points, counter-example
  leak retention, and child suite deduplication without compromising hermetic safety or regression protection.
- **Safety priority level:** elevated
- **Priority Justification:** Triggered by `holon-config/world/ruleset.md` §1 & §2 (Python `==3.13.*`, workspace
  isolation under `apps/sandbox-executor`, PEP 8, typing, docstrings), `holon-config/world/ruleset.md` §3 (Testing
  Constraints: `pytest==9.1.1`, test locations under `apps/sandbox-executor/tests/`, test changes declared in planning),
  `holon-config/world/constraints.md` §1 (Git Flow: branch isolation, commit boundaries),
  `holon-config/world/constraints.md` §2 (Sandbox containment: subprocess and filesystem boundaries),
  `holon-config/world/constraints.md` §3 (Ledger immutability: zero modifications to historical ledger entries),
  `docs/safety.md` §1 & §2 (Git as safety boundary, sandboxing mandatory), and
  `apps/sandbox-executor/docs/hermetic_testing.md` §1-§3 (Mandatory rules: every role entrypoint test must pin
  `HOLON_REPO_DIR` to fixture; Sandbox Safety Rail: never run full pytest discovery from `~/.holon-sandbox/workspace`).

## Exploration

- **Proportion of steps that are exploratory:** 0.50
- **Justification:** Steps 1 and 2 incorporate balanced exploration to identify exact boundary cut offsets across 4
  multiline credential shapes while preserving adversarial leak assertions, and to establish clean child suite execution
  sentinels across hermetic fixtures and stress test harnesses without unintended process nesting.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.2   |
| impact_pred         | 95.0  |
| cost_pred           | 10.0  |
| learning_value_pred | 5.5   |
| ev_pred             | 79.79 |

### Strategy Rationale

The overall plan metrics were derived from individual step-level metrics as follows:

- **p_success_pred**: 0.92. Dictated by the bottleneck step (Step 2: 0.92), where deduplicating child suite runs and
  preventing grandchild recursion in `hermetic_fixtures.py` and `test_sandbox_hermetic_guard.py` must ensure the canary
  guard in Test - Unit and the stress workspace check in Test - Stress remain fully valid and hermetic.
- **entropy_pred**: 1.2. Derived as the maximum single-step risk profile (Step 2: 1.2), where child process environment
  manipulation and suite command options are adjusted. The sum of predicted step entropies is 3.5
  (`1.0 + 1.2 + 0.8 + 0.5 = 3.5`), well within the allocated entropy budget of 15.0.
- **impact_pred**: 95.0. Directly resolves the critical CI suite regression, slashing execution times from 10-15+
  minutes (which triggered CI timeouts) down to <60s for both Test - Unit and Test - Stress, while fully retaining
  adversarial regression protection (F-IT8-1).
- **cost_pred**: 10.0. Calculated as the direct sum of individual step costs (`2.5 + 3.0 + 2.0 + 2.5 = 10.0`).
- **learning_value_pred**: 5.5. Epistemic gain from establishing targeted boundary stride testing for large-stream
  redaction regexes and formalizing child process sentinel inheritance patterns for nested test execution.
- **ev_pred**: 79.79. Computed strictly via the config-driven Expected Value formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`. With system constants `lambda = 0.3`
  and `mu = 0.5` loaded from `holon-config/metrics/ev_config.json`:
  `EV = 0.92 * 95.0 + 0.5 * 5.5 - 0.3 * 1.2 - 10.0 = 87.40 + 2.75 - 0.36 - 10.0 = 79.79`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python target `==3.13.*`, workspace
    isolation under `apps/sandbox-executor`).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8 compliance, explicit static typing with
    `typing` module, docstring maintenance).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test files located under
    `apps/sandbox-executor/tests/`, test changes declared in planning).
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: prefix-based branch isolation, commit
    boundaries).
  - `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: strict filesystem containment, prevention of
    escape actions).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: zero modifications to historical ledger entries).
  - `docs/safety.md` §1 & §2 (Git as safety boundary, sandboxing mandatory for execution).
  - `apps/sandbox-executor/docs/hermetic_testing.md` §1-§3 (Rule 4: Validate via Canary Regression Guard; Rule 5: Prove
    Suite-Wide Safety in CI; §3 Sandbox Safety Rail: never run full test discovery from `~/.holon-sandbox/workspace`).
- **Potential violations or edge cases:**
  - Over-optimizing the byte cuts in `test_executor.py` such that a boundary condition that previously proved regression
    F-IT8-1 is omitted, leading to vacuous test passes or failure of `self.assertGreater(superseded, 0)`.
  - Breaking the canary guard's ability to catch real unpinned workspace deletions by suppressing tests too aggressively
    in child suite execution.
  - Causing test discovery errors in child subprocesses by altering `pytest_suite_command` parameters in
    `hermetic_fixtures.py`.
  - Running verification commands directly in `/home/holon/.holon-sandbox/workspace` in violation of the Sandbox Safety
    Rail.
- **Mitigations built into the plan:**
  - Systematically map all syntax boundary points (block start, anchor prefix, colon/quote separators, pre-newline,
    newline, post-newline, indentations, secret boundaries) and assert `self.assertGreater(masked, 0)` and
    `self.assertGreater(superseded, 0)` for every shape.
  - Keep the child suite running the entire test directory in `TestSandboxHermeticGuard`, only deduplicating re-entrant
    recursion where a child run would spawn another child run.
  - Explicitly test negative-control canaries to verify that unpinned deletions are still detected.
  - Follow the Sandbox Safety Rail strictly during all test runs by using an isolated scratch directory.
- **Residual risk accepted (and why):**
  - Minor performance variance across different CI virtualization hardware: targeted boundary cuts drop iterations
    by >80% and eliminate redundant 80KB sweeps, ensuring that even under slower runners the suite remains well within
    the 60-second budget.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 3.5
- **Budget Compliance:** The strategy fits within budget (Predicted Plan Entropy sum of 3.5 is well below the 15.0
  allocated budget).

## Plan Description & Strategy

This plan resolves the massive CI test suite execution regression across `apps/sandbox-executor/tests/test_executor.py`,
`apps/sandbox-executor/tests/hermetic_fixtures.py`, `apps/sandbox-executor/tests/test_sandbox_hermetic_guard.py`, and
`apps/sandbox-executor/tests/test_workspace_survival_stress.py`:

1. **Targeted Boundary Stride & Hoisting Uncut Stream Checks in `test_executor.py`:** In
   `test_agent_output_anchor_on_a_preceding_line_is_redacted`, eliminate the continuous byte loop `range(len(cred))`
   (~240 iterations over 80KB streams). Replace it with boundary-critical cut points: block start, 1 byte into anchor,
   colon/quote separators, 1 byte before newline, at newline, 1 byte after newline, indentation cut, start of secret,
   mid-secret, end of secret, and block end. Move the uncut stream verification
   (`prepare_agent_output_block(stream, 100_000)`) outside the cut loop so it executes once per credential shape instead
   of 240 times. Preserve all counter-example leak assertions (`self.assertGreater(superseded, 0)`), masking assertions
   (`self.assertGreater(masked, 0)`), and window leak checks.
2. **Deduplicating & Streamlining Child Suite Execution in `hermetic_fixtures.py` and
   `test_sandbox_hermetic_guard.py`:** Eliminate recursive nesting and redundant grandchild suite execution. Update
   `test_sandbox_hermetic_guard.py` and `hermetic_fixtures.py` so that child processes recognize when they are already
   executing inside a parent test harness (e.g. checking `HOLON_STRESS_CHILD` or unifying child suite sentinels),
   preventing `test_sandbox_hermetic_guard.py` from spawning another full unit suite child inside the stress test's
   child suite. Maintain full conformance with `hermetic_testing.md` Rule 4 and Rule 5.
3. **Hermetic Safety, Negative Control & Adversarial Invariant Verification:** Verify that
   `test_negative_control_canary_is_actually_live` still terminates the canary as expected when unpinned, proving the
   guard remains non-vacuous. Validate that all 4 multiline credential shapes continue to catch regression F-IT8-1
   (superseded ordering leaks) under the targeted boundary cuts.
4. **End-to-End Suite Benchmarking & Static Formatting Compliance:** Execute unit tests
   (`uv run pytest apps/sandbox-executor/tests -m 'not integration_test and not stress'`) and stress tests
   (`uv run pytest apps/sandbox-executor/tests -m 'stress'`) from an isolated scratch fixture in accordance with the
   Sandbox Safety Rail, asserting both finish in <60 seconds. Verify `uv lock --check`, `uv run ruff check .`,
   `uv run ruff format --check .`, and `npx --yes prettier@3.8.4 --check '**/*.md'`.

---

## Step 1: Targeted Boundary Stride & Hoisting Uncut Stream Checks in test_executor.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Localized optimization within a single test method in `apps/sandbox-executor/tests/test_executor.py`
  with zero external dependencies and immediate deterministic test feedback.
- **Step Type:** REFACTOR
- **Exploration level:** BALANCED
- **Hypothesis being tested:** Targeted boundary cut offsets across syntax boundaries (block start, anchor, separators,
  newline transitions, indentation, secret boundaries) provide identical adversarial leak detection as exhaustive
  continuous byte loops while reducing regex sweeps by >80%.
- **Learning target:** Verify the exact minimal set of boundary offsets needed to reliably trigger
  `self.assertGreater(superseded, 0)` and `self.assertGreater(masked, 0)` across all four multiline credential shapes.
- **Maximum acceptable cost for this learning:** `cost <= 3.0` (compute and development time for targeted offset
  verification).

### Intent & Git Integration

- **Step Intent:** Optimize `test_agent_output_anchor_on_a_preceding_line_is_redacted` in
  `apps/sandbox-executor/tests/test_executor.py` by hoisting uncut stream validation outside the cut loop and replacing
  continuous range loops with boundary-critical cut points.
- **Git branch:**
  `I-1791203018-optimize-test-performance/P-1791203032-antigravity-agent-gemini-3.8-flash-medium/E-targeted-boundary-stride`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Open `apps/sandbox-executor/tests/test_executor.py` and inspect method
  `test_agent_output_anchor_on_a_preceding_line_is_redacted`.
- Define a boundary offset calculation helper or deterministic sequence generator for each credential shape `cred` that
  yields:
  - Start of credential block: offset `0`.
  - 1 byte into the anchor key: offset `1`.
  - Key-value separator boundaries: offsets corresponding to punctuation characters such as `:` or `"`.
  - Pre-newline transition: offset `nl_idx - 1` for each newline character in `cred`.
  - At-newline transition: offset `nl_idx` for each newline character in `cred`.
  - Post-newline transition: offset `nl_idx + 1` for each newline character in `cred`.
  - Indentation boundaries: offsets corresponding to whitespace prefixes following newlines.
  - Secret start boundary: offset where `secret` begins in `cred`.
  - Secret middle boundary: offset at `secret_start + len(secret) // 2`.
  - Secret end boundary: offset at `secret_start + len(secret)`.
  - End of credential block: offset `len(cred) - 1`.
  - Filter and sort offsets to ensure uniqueness within `[0, len(cred) - 1]`.
- Hoist uncut stream validation outside the cut loop:
  - Before iterating over offsets, generate an uncut stream for the shape with ample budget (e.g. `100_000`).
  - Invoke `prepare_agent_output_block(uncut_stream, 100_000)` once per shape.
  - Assert that none of the canary windows are present in the uncut output, verifying redactor capability on uncut text
    once per shape rather than ~240 times.
- Within the targeted offset loop:
  - Maintain the exact stream construction logic: `head` filler, `cred`, newline, and calculated `tail_len`.
  - Assert the cut geometry `len(stream.encode("utf-8")) - tail_budget == head + k`.
  - Run `prepare_agent_output_block(stream, budget)` and verify truncation and absence of secret fragments.
  - Increment `masked` count when masking marker is present.
  - Run `_superseded_bound_then_redact(stream, budget)` and increment `superseded` count when fragments leak.
- Keep post-loop assertions intact:
  - Assert `self.assertGreater(masked, 0)` for each shape.
  - Assert `self.assertGreater(superseded, 0)` for each shape to preserve the F-IT8-1 regression counter-example.
  - Assert `self.assertEqual(len(masked_shapes), len(shapes))`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (test assertion fidelity),
  `apps/sandbox-executor/docs/hermetic_testing.md` (hermetic standards).
- **Potential failure modes for this step:** Dropping an offset required to demonstrate the superseded leak, causing
  `self.assertGreater(superseded, 0)` to fail.
- **Guardrails and early‑abort checks:** Run the single test method in isolation to verify both `masked > 0` and
  `superseded > 0` hold for all 4 shapes before proceeding.

### Success & Discard Criteria

- **Success:** `test_agent_output_anchor_on_a_preceding_line_is_redacted` passes with all leak counter-examples affirmed
  and execution time reduced from >70s to <5s.
- **Discard:** If targeted offsets fail to trigger the superseded heuristic counter-example and cannot be reconciled
  within 15 targeted offsets per shape.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.0   |
| impact_pred         | 88.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 5.0   |
| ev_pred             | 83.30 |

### Step Metrics Rationale

- **p_success_pred (0.95):** The boundary points are well-defined by syntax boundaries of YAML and JSON; minimal risk of
  logic divergence.
- **entropy_pred (1.0):** Strictly scoped to test loop iteration values within a single test function; zero production
  code modification.
- **impact_pred (88.0):** Reduces execution time of the single slowest test in the entire repository by over 90%,
  immediately resolving the primary contributor to CI timeouts.
- **cost_pred (2.5):** Refactoring ~30 lines of test loop logic and verifying execution timings.
- **learning_value_pred (5.0):** Validates deterministic boundary stride testing patterns for multiline truncation
  assertions.
- **ev_pred (83.30):** Derived as `EV = 0.95 * 88.0 + 0.5 * 5.0 - 0.3 * 1.0 - 2.5 = 83.60 + 2.50 - 0.30 - 2.50 = 83.30`.

---

## Step 2: Deduplicating and Streamlining Child Suite Execution in hermetic_fixtures.py and test_sandbox_hermetic_guard.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Targeted containment refinement within `apps/sandbox-executor/tests/hermetic_fixtures.py` and
  `test_sandbox_hermetic_guard.py` preventing recursive suite execution while maintaining the integrity of canary
  guards.
- **Step Type:** REFACTOR
- **Exploration level:** BALANCED
- **Hypothesis being tested:** Propagating child execution sentinels and deduplicating the canary guard inside child
  processes eliminates redundant nested execution without weakening hermetic workspace protection.
- **Learning target:** Validate how child process environment variables interact between `TestSandboxHermeticGuard` and
  `TestSuiteWorkspaceSurvival` to prevent grandchild spawning.
- **Maximum acceptable cost for this learning:** `cost <= 3.5` (sub-process debugging and environment validation).

### Intent & Git Integration

- **Step Intent:** Optimize child suite invocation in `hermetic_fixtures.py` and prevent recursive execution of
  `TestSandboxHermeticGuard` during stress test child runs in `test_sandbox_hermetic_guard.py`.
- **Git branch:**
  `I-1791203018-optimize-test-performance/P-1791203032-antigravity-agent-gemini-3.8-flash-medium/E-deduplicate-child-suite`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Examine `apps/sandbox-executor/tests/test_sandbox_hermetic_guard.py`:
  - Review `setUp` method which currently only skips if `os.environ.get(CHILD_ENV_VAR) == "1"` where
    `CHILD_ENV_VAR = "HOLON_HERMETIC_GUARD_CHILD"`.
  - Update `setUp` to also check `os.environ.get("HOLON_STRESS_CHILD") == "1"` or a shared child sentinel (e.g.
    `HOLON_CHILD_SUITE_ACTIVE`), skipping execution if the process is already executing within a stress test child suite
    run.
- Examine `apps/sandbox-executor/tests/hermetic_fixtures.py`:
  - Review `simulated_container_env` and `pytest_suite_command`.
  - Ensure `simulated_container_env` sets sentinels indicating an active child suite environment to prevent nested child
    runners from spawning downstream child processes.
  - Review `pytest_suite_command`: ensure that child suite execution can optionally ignore or deselect
    `test_sandbox_hermetic_guard.py` when running nested suites (or rely on the sentinel check in `setUp`) so that
    pytest does not waste time re-collecting or re-executing child processes inside child processes.
  - Verify that `CHILD_TIMEOUT_SECONDS` remains adequate while the underlying execution is significantly faster.
- Ensure that `TestSandboxHermeticGuard.test_canary_workspace_survives_entrypoint_tests` in Test - Unit continues to run
  the full suite directory under simulated container conditions as required by `hermetic_testing.md` Rule 4.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (p_success_pred = 0.92; if child environment sentinels fail or cause unintended skips in the
  primary unit suite, the plan fails).

### Safety & Constraint Considerations

- **Relevant rules:** `apps/sandbox-executor/docs/hermetic_testing.md` Rule 4 & 5 (Canary guard and stress survival
  invariants must remain fully enforced).
- **Potential failure modes for this step:** Accidentally skipping `TestSandboxHermeticGuard` in the top-level unit test
  run instead of only in child runs.
- **Guardrails and early‑abort checks:** Assert that `test_canary_workspace_survives_entrypoint_tests` executes and
  passes when run in the primary unit suite, and skips only when child sentinels are set.

### Success & Discard Criteria

- **Success:** Stress suite execution (`test_workspace_survival_stress.py`) completes cleanly without spawning a
  grandchild unit suite run, and the canary guard in `test_sandbox_hermetic_guard.py` runs cleanly in the unit suite.
- **Discard:** If child deduplication disables canary protection in top-level unit testing.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.2   |
| impact_pred         | 92.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 5.5   |
| ev_pred             | 84.03 |

### Step Metrics Rationale

- **p_success_pred (0.92):** Bottleneck step due to subprocess environment inheritance and sentinel validation across
  both unit and stress suites.
- **entropy_pred (1.2):** Touches shared fixtures in `hermetic_fixtures.py` and sentinel logic in test classes; low
  complexity, contained surface area.
- **impact_pred (92.0):** Cuts out entire redundant nested test suites from the stress test job, preventing duplicate
  test execution and timeouts.
- **cost_pred (3.0):** Subprocess testing and environment variable tracing across multiple runners.
- **learning_value_pred (5.5):** Clarifies multi-tier test harness architecture and re-entrancy prevention patterns.
- **ev_pred (84.03):** Derived as `EV = 0.92 * 92.0 + 0.5 * 5.5 - 0.3 * 1.2 - 3.0 = 84.64 + 2.75 - 0.36 - 3.00 = 84.03`.

---

## Step 3: Verification of Hermetic Safety, Negative Control & Adversarial Invariants

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure verification and assertion validation step ensuring zero regression of security and hermetic
  guarantees.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Verify that adversarial leak counter-examples in `test_executor.py` and negative-control canary
  destruction in `test_sandbox_hermetic_guard.py` remain non-vacuous and operational.
- **Git branch:**
  `I-1791203018-optimize-test-performance/P-1791203032-antigravity-agent-gemini-3.8-flash-medium/E-verify-invariants`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Follow `apps/sandbox-executor/docs/hermetic_testing.md` §3 Sandbox Safety Rail:
  - Create an isolated temporary test fixture directory.
  - Copy repository test files to the isolated fixture.
  - Set `HOLON_REPO_DIR` to the temporary directory.
- Verify `test_negative_control_canary_is_actually_live` in `test_sandbox_hermetic_guard.py`:
  - Run the negative control test.
  - Confirm that the unpinned call to `cleanup_repo_dir` actively destroys the simulated canary, proving the test guard
    is not passing vacuously.
- Verify adversarial regression detection in `test_executor.py`:
  - Run `test_agent_output_anchor_on_a_preceding_line_is_redacted`.
  - Assert that `superseded` leak count is strictly greater than 0 for all shapes: `yaml key line`,
    `pretty json key line`, `wrapped bearer header`, and `nested yaml`.
  - Assert that `masked` count is strictly greater than 0 for all shapes.
  - Confirm that if the superseded ordering is restored, the test immediately fails, proving F-IT8-1 detection power is
    preserved.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `apps/sandbox-executor/docs/hermetic_testing.md` Rule 1-4, §3 Sandbox Safety Rail.
- **Potential failure modes for this step:** Running tests directly from `~/.holon-sandbox/workspace` without an
  isolated fixture.
- **Guardrails and early‑abort checks:** Never invoke full discovery from the active workspace; always use an isolated
  scratch fixture.

### Success & Discard Criteria

- **Success:** Negative control affirmatively destroys canary, and adversarial test confirms leak detection on all 4
  credential shapes.
- **Discard:** Any indication that canary protection or adversarial regression detection has become vacuous.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.8   |
| impact_pred         | 85.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 4.5   |
| ev_pred             | 79.91 |

### Step Metrics Rationale

- **p_success_pred (0.94):** Invariant assertions are deterministic and already codified.
- **entropy_pred (0.8):** Verification only, zero code changes.
- **impact_pred (85.0):** Guarantees that performance optimizations did not compromise security invariants.
- **cost_pred (2.0):** Execution of targeted test methods within isolated scratch environment.
- **learning_value_pred (4.5):** Documents invariant validation protocol for performance refactors.
- **ev_pred (79.91):** Derived as `EV = 0.94 * 85.0 + 0.5 * 4.5 - 0.3 * 0.8 - 2.0 = 79.90 + 2.25 - 0.24 - 2.00 = 79.91`.

---

## Step 4: End-to-End Suite Benchmarking & Static Formatting Compliance

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard end-to-end benchmarking, linting, formatting, and markdown validation across the entire
  workspace.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Benchmark unit and stress test suites to verify each completes in under 60 seconds, and ensure all
  lock, lint, formatting, and markdown checks pass cleanly.
- **Git branch:**
  `I-1791203018-optimize-test-performance/P-1791203032-antigravity-agent-gemini-3.8-flash-medium/E-benchmarking-and-lint`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Perform unit test benchmarking from an isolated scratch copy complying with Sandbox Safety Rail:
  - Execute `uv run pytest apps/sandbox-executor/tests -m 'not integration_test and not stress'`.
  - Measure total runtime and verify that execution finishes in under 60 seconds (target <30s).
- Perform stress test benchmarking:
  - Execute `uv run pytest apps/sandbox-executor/tests -m 'stress'`.
  - Measure total runtime and verify that execution finishes in under 60 seconds (target <45s).
- Run package and dependency validation:
  - Run `uv lock --check` to ensure lockfile integrity.
- Run static linting and formatting checks:
  - Run `uv run ruff check .` and ensure zero errors or warnings.
  - Run `uv run ruff format --check .` and ensure all python files comply with formatting rules.
- Run markdown hygiene validation:
  - Run `npx --yes prettier@3.8.4 --check '**/*.md'` to verify formatting across all documentation and plan files.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1 & §2 (Python 3.13, PEP 8, ruff), Prettier markdown formatting
  requirements.
- **Potential failure modes for this step:** Prettier formatting failure due to unescaped math expressions or syntax
  discrepancies.
- **Guardrails and early‑abort checks:** Ensure all math derivations are wrapped in code spans to avoid CommonMark
  parser oscillations.

### Success & Discard Criteria

- **Success:** Both unit and stress test suites execute in <60 seconds, and `uv lock --check`, `ruff check`,
  `ruff format --check`, and `prettier --check` all pass with exit code 0.
- **Discard:** Either suite takes >60 seconds or any static check fails.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.5   |
| impact_pred         | 90.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 4.0   |
| ev_pred             | 85.75 |

### Step Metrics Rationale

- **p_success_pred (0.96):** Straightforward test execution and linters with established configs.
- **entropy_pred (0.5):** Read-only verification and minor formatting adjustments.
- **impact_pred (90.0):** Final confirmation of the intent goal: both suites completing in under 1 minute.
- **cost_pred (2.5):** Running full test suites and linting tools.
- **learning_value_pred (4.0):** Final empirical verification of suite performance characteristics.
- **ev_pred (85.75):** Derived as `EV = 0.96 * 90.0 + 0.5 * 4.0 - 0.3 * 0.5 - 2.5 = 86.40 + 2.00 - 0.15 - 2.50 = 85.75`.
