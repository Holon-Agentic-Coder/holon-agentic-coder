# Plan for I-1793325098-ci-timing-gate-composite-action

- **Plan ID:** P-1793325374-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.4.10)
- **Created At:** 2026-10-30T01:56:15.471Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** conservative with focus on high reliability, deterministic refactoring, and zero CI
  regression.
- **Safety priority level:** standard
- **Priority Justification:** Refactors internal CI workflow steps and timing gate tests without modifying
  security-critical boundaries or privileged execution environments.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The task is a well-specified refactoring of existing GitHub Actions workflows and pytest assertions
  with deterministic requirements.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 2.70  |
| impact_pred         | 75.0  |
| cost_pred           | 3.20  |
| learning_value_pred | 1.50  |
| ev_pred             | 67.99 |

### Strategy Rationale

The intent is accepted as-is (`ACCEPT_AS_IS`). The goal is to eliminate duplicated timing gate steps across 5 GitHub
Actions workflows (`docker-build.yml`, `make.yml`, `test-hygiene.yml`, `test-integration.yml`, and `test-unit.yml`),
extract them into a clean local composite action at `.github/actions/ci-timing-gate/action.yml`, delete the dead
`.github/actions/docker-pull/` composite action, update the documentation in `.github/workflows/README.md`, and
implement verification tests in `apps/sandbox-executor/tests/test_ci_timing_gate.py`.

The plan decomposes this intent into 6 cohesive steps:

- Step 1 creates the composite action definition (`.github/actions/ci-timing-gate/action.yml`) with strict validation
  for `inputs.phase` and environment exports.
- Step 2 deletes the legacy `.github/actions/docker-pull/` action directory.
- Step 3 refactors all 5 workflows so that repository checkout occurs first and the timing gate start/check steps are
  wired cleanly.
- Step 4 updates the documentation in `.github/workflows/README.md`.
- Step 5 adds `test_every_workflow_wires_start_and_check_steps` to `apps/sandbox-executor/tests/test_ci_timing_gate.py`
  to prevent future regressions.
- Step 6 executes the test suite following strict test-run discipline and verifies workspace hygiene with
  `uv run task check`.

#### Metric Derivations & Bottleneck Identification

- **P(success):** `0.95`. Step 3 is the bottleneck (`p_success_pred = 0.95`) because modifying multiple YAML workflow
  files with precise step sequences (ensuring checkout precedes local action invocation and `if: always()` is preserved)
  requires careful coordinate editing.
- **Entropy:** `2.70`. Aggregated as the sum of step-level entropies: `0.50 + 0.20 + 0.90 + 0.20 + 0.60 + 0.30 = 2.70`.
  This is far below the allocated entropy budget of `15.0`.
- **Impact:** `75.0`. Standardising timing gate steps across all CI jobs eliminates repetitive boilerplate, ensures
  uniform budget enforcement, and removes dead code.
- **Cost:** `3.20`. Derived as the sum of predicted step costs: `0.60 + 0.20 + 1.00 + 0.30 + 0.60 + 0.50 = 3.20`.
- **Learning Value:** `1.50`. Modest learning value as composite action wiring and static AST/text validation are
  familiar, well-understood patterns.
- **Expected Value (EV):** `67.99`, calculated using system constants `lambda = 0.3` and `mu = 0.5`:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.95 * 75.0 + 0.5 * 1.50 - 0.3 * 2.70 - 3.20 = 71.25 + 0.75 - 0.81 - 3.20 = 67.99`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md#1` (Python 3.13 runtime, uv workspace management)
  - `holon-config/world/ruleset.md#3` (Pytest testing constraints, explicit test declaration)
  - `holon-config/world/constraints.md#1` (Git flow branch prefix isolation and commit boundaries)
  - `docs/safety.md#git-discipline` (Execution confined strictly to branch prefix)
  - `.agents/AGENTS.md` (Always execute `uv run pytest <args>`, use `uv run task check`, no throwaway scripts)
- **Potential violations or edge cases:**
  - Local composite actions (`./.github/actions/ci-timing-gate`) require checked-out repository code; calling the local
    action before `actions/checkout` causes immediate runner failure.
  - Omitting `if: always()` on the timing check step prevents timing budget evaluation and step summaries on failed
    runs.
  - Unintended edits to test files outside declared scope.
- **Mitigations built into the plan:**
  - Step 3 explicitly mandates moving `actions/checkout` to step 1 before any composite action call.
  - Step 5 implements static assertions in `test_ci_timing_gate.py` verifying that checkout precedes the start phase and
    that `if: always()` is present on all check phases across all 5 workflows.
  - Changes are strictly bounded to the 9 declared files in `scope`.
- **Residual risk accepted (and why):** Minor residual risk of YAML formatting differences across workflows; mitigated
  by `npx prettier --check` and `test_ci_timing_gate.py` static inspection.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 2.70
- **Budget Compliance:** The strategy fits within budget (2.70 <= 15.0).

## Plan Description & Strategy

This plan executes the refactoring of CI timing gate operations across the repository into a reusable local GitHub
Actions composite action.

Previously, all five workflows (`docker-build.yml`, `make.yml`, `test-hygiene.yml`, `test-integration.yml`,
`test-unit.yml`) embedded inline bash commands to capture `CI_TIMING_START` and invoke the timing gate scripts at the
end of each job. This duplicated 10 steps of configuration boilerplate across the repository. Furthermore,
`.github/actions/docker-pull/action.yml` remained in the repository as unused legacy code.

Under this plan:

1. `.github/actions/ci-timing-gate/action.yml` is created as a local composite action. It accepts inputs `phase`
   (required: `start` | `check`) and `max-seconds` (optional, default: 300). In the `start` phase, it validates input
   and records `CI_JOB_START_EPOCH=$(date +%s)` to `$GITHUB_ENV`. In the `check` phase, it runs
   `python3 "${GITHUB_WORKSPACE}/scripts/check_ci_runtime_budget.py" --start-epoch "${CI_JOB_START_EPOCH}" --max-seconds "${{ inputs.max-seconds }}" --step-summary "${GITHUB_STEP_SUMMARY}"`.
2. `.github/actions/docker-pull/` is safely removed from the repository.
3. All five workflows are updated so that repository checkout occurs first, followed by the composite action in `start`
   phase, and concluded with the composite action in `check` phase with `if: always()`.
4. `.github/workflows/README.md` is updated to document the new composite action and remove references to `docker-pull`.
5. `apps/sandbox-executor/tests/test_ci_timing_gate.py` is augmented with
   `test_every_workflow_wires_start_and_check_steps` to assert that all five workflows conform to the new structure.
6. The entire test suite and hygiene linters are verified via
   `uv run pytest apps/sandbox-executor/tests/test_ci_timing_gate.py`, `uv run task test`, and `uv run task check`.

The plan requires no sub-intents, consumes only 2.70 entropy units against a 15.0 budget, and has an Expected Value of
67.99.

---

## Step 1: Create local composite action `.github/actions/ci-timing-gate/action.yml`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, deterministic, self-contained configuration file implementing the composite action specification
  without external side-effects.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Implement `.github/actions/ci-timing-gate/action.yml` defining the composite action with required
  input `phase` and optional input `max-seconds`.
- **Git branch:**
  `I-1793325098-ci-timing-gate-composite-action/P-1793325374-antigravity-agent-gemini-3.8-flash-medium/E-{timestamp}-ci-timing-gate/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create directory `.github/actions/ci-timing-gate` if it does not exist.
2. Author `.github/actions/ci-timing-gate/action.yml` specifying `name: 'CI Timing Gate'` and
   `description: 'Starts or checks runtime timing budget for CI jobs'`.
3. Configure composite action `inputs`:
   - Declare `phase`: required, string description explaining it accepts either 'start' or 'check'.
   - Declare `max-seconds`: optional, default '300', string description indicating maximum runtime budget in seconds.
4. Define `runs` with `using: 'composite'`.
5. Implement phase validation step running via `shell: bash`:
   - Check if `${{ inputs.phase }}` is strictly equal to 'start' or 'check'.
   - If invalid, output error message and exit with returncode 1.
6. Implement `start` phase step running via `shell: bash` with condition checking `inputs.phase == 'start'`:
   - Compute current timestamp using `date +%s` and write `CI_JOB_START_EPOCH=<timestamp>` to `$GITHUB_ENV`.
7. Implement `check` phase step running via `shell: bash` with condition checking `inputs.phase == 'check'`:
   - Invoke
     `python3 "${GITHUB_WORKSPACE}/scripts/check_ci_runtime_budget.py" --start-epoch "${CI_JOB_START_EPOCH}" --max-seconds "${{ inputs.max-seconds }}" --step-summary "${GITHUB_STEP_SUMMARY}"`.
8. Provide fallback / compatibility helper in `scripts/check_ci_runtime_budget.py` if missing, parsing the provided CLI
   flags (`--start-epoch`, `--max-seconds`, `--step-summary`) to calculate runtime, write summary markdown, and enforce
   runtime budget exit status.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (Downstream workflows and test assertions directly rely on this composite action definition)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#1`, `holon-config/world/constraints.md#1`
- **Potential failure modes for this step:** Shell quoting or escaping issues in composite action YAML or missing
  validation logic for unexpected input values.
- **Guardrails and early‑abort checks:** Ensure exact string matches for `CI_JOB_START_EPOCH` and
  `scripts/check_ci_runtime_budget.py`.

### Success & Discard Criteria

- **Success:** `.github/actions/ci-timing-gate/action.yml` is created, syntactically valid YAML, defines
  `CI_JOB_START_EPOCH`, and references `scripts/check_ci_runtime_budget.py`.
- **Discard:** Discard if composite action syntax errors cannot be resolved or if action requirements conflict with
  GitHub Actions schema.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.50  |
| impact_pred         | 15.0  |
| cost_pred           | 0.60  |
| learning_value_pred | 0.20  |
| ev_pred             | 14.05 |

### Step Metrics Rationale

Calculated via code span formula:
`EV = 0.98 * 15.0 + 0.5 * 0.20 - 0.3 * 0.50 - 0.60 = 14.70 + 0.10 - 0.15 - 0.60 = 14.05`. High success probability as
the action structure is fully prescribed by the acceptance criteria.

---

## Step 2: Remove unused `.github/actions/docker-pull/` composite action directory

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure deletion of dead legacy action directory `.github/actions/docker-pull/`, minimal risk, negligible
  entropy, and zero reusability.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Delete `.github/actions/docker-pull/action.yml` and clean up the unused directory.
- **Git branch:**
  `I-1793325098-ci-timing-gate-composite-action/P-1793325374-antigravity-agent-gemini-3.8-flash-medium/E-{timestamp}-ci-timing-gate/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Verify that no workflows or repository scripts reference `.github/actions/docker-pull/action.yml`.
2. Delete `.github/actions/docker-pull/action.yml`.
3. Remove the containing directory `.github/actions/docker-pull`.
4. Verify directory deletion is staged cleanly in git without leaving residual files or untracked artefacts.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md#1`
- **Potential failure modes for this step:** Accidental deletion of adjacent actions under `.github/actions/`.
- **Guardrails and early‑abort checks:** Restrict file removal strictly to `.github/actions/docker-pull/`.

### Success & Discard Criteria

- **Success:** `.github/actions/docker-pull/action.yml` and its parent folder are completely removed from the filesystem
  and git tracking.
- **Discard:** Discard if active workflows still require docker-pull logic.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.20  |
| impact_pred         | 5.0   |
| cost_pred           | 0.20  |
| learning_value_pred | 0.10  |
| ev_pred             | 4.74  |

### Step Metrics Rationale

Calculated via code span formula: `EV = 0.99 * 5.0 + 0.5 * 0.10 - 0.3 * 0.20 - 0.20 = 4.95 + 0.05 - 0.06 - 0.20 = 4.74`.
Clean deletion with near-zero failure risk.

---

## Step 3: Update all 5 workflows to checkout first and wire `.github/actions/ci-timing-gate`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Cohesive refactoring of 5 GitHub Actions workflow files to use the composite action; bounded scope and
  low complexity.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Ensure repository checkout is the initial step and replace duplicated timing gate start/check steps
  with `./.github/actions/ci-timing-gate` in `docker-build.yml`, `make.yml`, `test-hygiene.yml`, `test-integration.yml`,
  and `test-unit.yml`.
- **Git branch:**
  `I-1793325098-ci-timing-gate-composite-action/P-1793325374-antigravity-agent-gemini-3.8-flash-medium/E-{timestamp}-ci-timing-gate/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. For `.github/workflows/docker-build.yml`:
   - Verify existence or establish `.github/workflows/docker-build.yml` (mirroring `build-images.yml` configuration).
   - Place `actions/checkout@v7` as the first step in the job.
   - Insert step `name: CI Timing Gate (Start)` using `./.github/actions/ci-timing-gate` with `with: phase: start`.
   - At job completion, append step `name: CI Timing Gate (Check <= 5 min)` using `./.github/actions/ci-timing-gate`
     with `if: always()` and `with: phase: check`.
2. For `.github/workflows/make.yml`:
   - Reorder steps so `actions/checkout@v7` runs prior to timing gate initialization.
   - Replace legacy start step with step referencing `./.github/actions/ci-timing-gate` with `with: phase: start`.
   - Replace end-of-job timing step with step referencing `./.github/actions/ci-timing-gate` with `if: always()` and
     `with: phase: check`.
3. For `.github/workflows/test-hygiene.yml`:
   - Reorder steps to place `actions/checkout@v7` (with submodules: true) as the very first step.
   - Replace inline start epoch record with `./.github/actions/ci-timing-gate` using `phase: start`.
   - Replace end-of-job timing gate step with `./.github/actions/ci-timing-gate` using `phase: check` and
     `if: always()`.
4. For `.github/workflows/test-integration.yml`:
   - Place `actions/checkout@v7` (with submodules: true) as the first step.
   - Wire `./.github/actions/ci-timing-gate` with `phase: start`.
   - Wire `./.github/actions/ci-timing-gate` with `phase: check` and `if: always()`.
5. For `.github/workflows/test-unit.yml`:
   - Place `actions/checkout@v7` (with submodules: true) as the first step.
   - Wire `./.github/actions/ci-timing-gate` with `phase: start`.
   - Wire `./.github/actions/ci-timing-gate` with `phase: check` and `if: always()`.
6. Inspect all 5 workflows to ensure valid YAML structure, indentation, and step sequencing.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Core acceptance criteria for workflow orchestration)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md#1`, `docs/safety.md#git-discipline`
- **Potential failure modes for this step:** Local composite actions require checked-out files; executing the local
  action before checkout will crash the runner. Missing `if: always()` on the check step would cause aborted timing
  evaluations on job failure.
- **Guardrails and early‑abort checks:** Ensure every check step carries `if: always()` and checkout strictly precedes
  `phase: start`.

### Success & Discard Criteria

- **Success:** All 5 workflow files checkout the repository first, invoke `./.github/actions/ci-timing-gate` with
  `phase: start`, and invoke it at the end with `phase: check` and `if: always()`.
- **Discard:** Discard if workflow syntax fails validation or workflow permissions prevent action execution.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.90  |
| impact_pred         | 25.0  |
| cost_pred           | 1.00  |
| learning_value_pred | 0.40  |
| ev_pred             | 22.68 |

### Step Metrics Rationale

Calculated via code span formula:
`EV = 0.95 * 25.0 + 0.5 * 0.40 - 0.3 * 0.90 - 1.00 = 23.75 + 0.20 - 0.27 - 1.00 = 22.68`. Slight entropy from updating
five separate workflow definitions.

---

## Step 4: Update `.github/workflows/README.md` documentation

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure documentation update confined to `.github/workflows/README.md`.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Document `.github/actions/ci-timing-gate/action.yml` and remove references to deprecated
  `.github/actions/docker-pull/`.
- **Git branch:**
  `I-1793325098-ci-timing-gate-composite-action/P-1793325374-antigravity-agent-gemini-3.8-flash-medium/E-{timestamp}-ci-timing-gate/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Open `.github/workflows/README.md` and navigate to the `## Composite Actions` section.
2. Remove the bullet describing `.github/actions/docker-pull/`.
3. Add a dedicated subsection/bullet documenting `.github/actions/ci-timing-gate/action.yml`:
   - Explain its purpose: encapsulates job timing gate initialization (`phase: start`) and runtime budget enforcement
     (`phase: check`).
   - Document inputs: `phase` (required: 'start' | 'check') and `max-seconds` (optional, default: 300).
   - Document requirement: calling workflows must execute `actions/checkout` before invoking the action, and check steps
     must use `if: always()`.
4. Update the workflow summary list to include `docker-build.yml`.
5. Verify document structure and terminology alignment.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2`
- **Potential failure modes for this step:** Prettier formatting check failures or broken Markdown syntax.
- **Guardrails and early‑abort checks:** Format cleanly without unescaped underscores in formulas.

### Success & Discard Criteria

- **Success:** `.github/workflows/README.md` accurately documents the composite action, removes docker-pull, and
  conforms to repository documentation standards.
- **Discard:** Discard if documentation conflicts with actual composite action parameters.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.20  |
| impact_pred         | 5.0   |
| cost_pred           | 0.30  |
| learning_value_pred | 0.10  |
| ev_pred             | 4.64  |

### Step Metrics Rationale

Calculated via code span formula: `EV = 0.99 * 5.0 + 0.5 * 0.10 - 0.3 * 0.20 - 0.30 = 4.95 + 0.05 - 0.06 - 0.30 = 4.64`.
Low risk, standard documentation update.

---

## Step 5: Implement test assertions in `apps/sandbox-executor/tests/test_ci_timing_gate.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Adding unit tests within the existing test module to verify workflow wiring and composite action
  invariants.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add `test_every_workflow_wires_start_and_check_steps` to
  `apps/sandbox-executor/tests/test_ci_timing_gate.py` ensuring static verification of workflows and action files.
- **Git branch:**
  `I-1793325098-ci-timing-gate-composite-action/P-1793325374-antigravity-agent-gemini-3.8-flash-medium/E-{timestamp}-ci-timing-gate/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_ci_timing_gate.py`, add `test_every_workflow_wires_start_and_check_steps`.
2. Locate repository root relative to the test file path (`Path(__file__).parents[3]`).
3. For each of the 5 expected workflows (`docker-build.yml`, `make.yml`, `test-hygiene.yml`, `test-integration.yml`,
   `test-unit.yml`):
   - Assert file existence under `.github/workflows/`.
   - Parse or inspect workflow YAML text:
     - Verify reference to local composite action `./.github/actions/ci-timing-gate`.
     - Verify invocation with `phase: start`.
     - Verify invocation with `phase: check`.
     - Verify `if: always()` condition is present on the check step.
     - Verify `actions/checkout` appears before the `start` timing gate step.
4. Read `.github/actions/ci-timing-gate/action.yml`:
   - Assert action definition exists.
   - Assert presence of `CI_JOB_START_EPOCH`.
   - Assert invocation of `scripts/check_ci_runtime_budget.py`.
5. Verify `.github/actions/docker-pull/action.yml` does not exist.
6. Verify existing timing gate tests continue to pass without regression.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** YES (Directly verifies intent acceptance criteria)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3`, `docs/safety.md#invariant-2`
- **Potential failure modes for this step:** Brittle parsing if string matching is too strict regarding whitespace or
  formatting variations.
- **Guardrails and early‑abort checks:** Use robust YAML loading or normalized string tokens to inspect workflow
  structure.

### Success & Discard Criteria

- **Success:** Test `test_every_workflow_wires_start_and_check_steps` passes and accurately catches missing or malformed
  steps.
- **Discard:** Discard if test introduces flakes or false rejections.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.60  |
| impact_pred         | 15.0  |
| cost_pred           | 0.60  |
| learning_value_pred | 0.40  |
| ev_pred             | 13.82 |

### Step Metrics Rationale

Calculated via code span formula:
`EV = 0.96 * 15.0 + 0.5 * 0.40 - 0.3 * 0.60 - 0.60 = 14.40 + 0.20 - 0.18 - 0.60 = 13.82`. Standard pytest testing of
declarative structures.

---

## Step 6: Execute test suite and verify repository hygiene

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard verification and linting step; low risk, non-exploratory.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Verify all tests pass cleanly and repository hygiene is fully compliant with `taskipy` tasks.
- **Git branch:**
  `I-1793325098-ci-timing-gate-composite-action/P-1793325374-antigravity-agent-gemini-3.8-flash-medium/E-{timestamp}-ci-timing-gate/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Follow repository Test-Run Discipline:
   - Run the affected tests first: `uv run pytest apps/sandbox-executor/tests/test_ci_timing_gate.py`.
   - Run the full test suite once: `uv run task test`.
2. Verify static hygiene:
   - Run `uv run task check` (covering ruff checks, lockfile verification, and prettier doc linting).
   - If any markdown or python formatting needs alignment, execute `uv run task format` and re-verify.
3. Verify git status matches the declared scope precisely:
   - `.github/actions/ci-timing-gate/action.yml` (added)
   - `.github/actions/docker-pull/` (removed)
   - `.github/workflows/docker-build.yml` (added/updated)
   - `.github/workflows/make.yml` (modified)
   - `.github/workflows/test-hygiene.yml` (modified)
   - `.github/workflows/test-integration.yml` (modified)
   - `.github/workflows/test-unit.yml` (modified)
   - `.github/workflows/README.md` (modified)
   - `apps/sandbox-executor/tests/test_ci_timing_gate.py` (modified)

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3, Step 4, Step 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3`, `AGENTS.md`
- **Potential failure modes for this step:** Stale virtual environment or formatting drift.
- **Guardrails and early‑abort checks:** Use `uv run task clean` if virtual environment requires refresh.

### Success & Discard Criteria

- **Success:** Zero test failures in `test_ci_timing_gate.py` and `task test`, zero lint/doc errors.
- **Discard:** Discard if unrelated test suites fail or regressions are introduced.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.30  |
| impact_pred         | 10.0  |
| cost_pred           | 0.50  |
| learning_value_pred | 0.30  |
| ev_pred             | 9.36  |

### Step Metrics Rationale

Calculated via code span formula:
`EV = 0.98 * 10.0 + 0.5 * 0.30 - 0.3 * 0.30 - 0.50 = 9.80 + 0.15 - 0.09 - 0.50 = 9.36`. Deterministic test run and
hygiene pass.
