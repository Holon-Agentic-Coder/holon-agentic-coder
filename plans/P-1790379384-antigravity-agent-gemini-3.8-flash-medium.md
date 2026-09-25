# Plan for I-1790379374-add-holon-calibrate-command

- **Plan ID:** P-1790379384-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-09-25T23:36:24.939Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted empirical validation of telemetry parsing heuristics and fallback mechanisms to ensure robust multi-source calibration without brittle ledger dependencies.
- **Safety priority level:** standard
- **Priority Justification:** All changes are strictly contained to apps/sandbox-executor source modules and tests, operating within git branch isolation and requiring no external network access or sandbox escape triggers under docs/safety.md and holon-config/world/constraints.md.

## Exploration

- **Proportion of steps that are exploratory:** 0.25
- **Justification:** Step 1 incorporates balanced exploration to validate heuristic fallbacks for sparse execution telemetry (tokens, duration, patch size) across heterogeneous agent outputs, maximizing epistemic gain while keeping execution cost and entropy minimal.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 2.2   |
| impact_pred         | 85.0  |
| cost_pred           | 13.0  |
| learning_value_pred | 4.5   |
| ev_pred             | 65.09 |

### Strategy Rationale

The overall plan metrics were derived from the individual step-level metrics as follows:

- **p_success_pred**: 0.90. Sourced from the integration bottleneck between Step 1 (core calibration engine) and Step 2 (CLI and git orchestration), reflecting minor coordination risks in git branch handling and parsing varied telemetry formats.
- **entropy_pred**: 2.2. Calculated from the maximum step-level entropy (Step 1: 1.8) plus a 0.4 integration margin for branch management and CLI argument wiring. The total predicted sum of step entropies is 4.8, which safely complies with the allocated entropy budget of 15.0.
- **impact_pred**: 85.0. Implements the mission-critical automated post-execution calibration loop (`holon calibrate`), directly feeding into Holon's epistemic feedback mechanisms (Brier score evaluation, calibration drift tracking, and earned trust calculation) across all current and future agent runners.
- **cost_pred**: 13.0. Computed as the direct sum of individual step costs (4.5 + 3.0 + 3.5 + 2.0 = 13.0).
- **learning_value_pred**: 4.5. Epistemic gain from establishing dual-source parsing patterns (ledger JSONL + markdown table regex) and codifying telemetry heuristics (git diff patch sizes, duration calculations) into the core framework.
- **ev_pred**: 65.09. Calculated strictly using the canonical config-driven Expected Value formula:
  $$EV = P(\text{success}) \times \text{Impact} + \mu \times \text{LearningValue} - \lambda \times \Delta S_{\text{intent}} - \text{Cost}$$
  With system constants $\lambda = 0.3$ and $\mu = 0.5$ from `holon-config/metrics/ev_config.json`:
  $$EV = 0.90 \times 85.0 + 0.5 \times 4.5 - 0.3 \times 2.2 - 13.0 = 76.5 + 2.25 - 0.66 - 13.0 = 65.09$$

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python strict target `==3.13.*`, package management via `uv` workspace commands).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8 compliance, explicit static typing with `typing`, docstring requirements, no wildcard imports).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1` as test runner, unit tests located in `apps/sandbox-executor/tests/`, no unauthorized test modification).
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: prefix-based branch isolation, rebase discipline, commit boundaries).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: zero modification policy on `holon-knowledge/ledger/*.jsonl`, only append or read operations).
  - `docs/safety.md` §1 & §2 (Sandboxing, blast radius containment, git isolation as safety boundary).
- **Potential violations or edge cases:**
  - Accidental modification of immutable ledger entries (`executions.jsonl` or `plans.jsonl`) during calibration ingestion.
  - Working tree pollution or branch checkout failures when creating or checking out the `<execution_branch>/calibrated` branch.
  - Parsing errors caused by variations or minor formatting irregularities in plan markdown tables or missing telemetry fields in execution records.
  - Git commit failures if user identity (`user.name`, `user.email`) is not configured in the host environment or ephemeral container.
- **Mitigations built into the plan:**
  - Read-only streaming access to `holon-knowledge/ledger/` files with zero write or amend actions.
  - Robust dual-source parsing: primary extraction from ledger JSONL files, seamless deterministic fallback to regex table parsing from plan markdown.
  - Defensive git branch orchestration: verification of clean working tree, dynamic detection of remote vs local branch pointers, and fallback git committer configuration.
  - Isolated test fixtures using pytest `tmp_path` and mock git repositories to avoid any workspace contamination during test runs.
- **Residual risk accepted (and why):**
  - Historical execution records that omit explicit token counts or execution wall-time will rely on heuristic inference (git diff patch sizes and file timestamp deltas); accepted because post-execution analysis must remain operational even on legacy execution records.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 4.8
- **Budget Compliance:** The strategy fits within budget (Predicted Plan Entropy sum of 4.8 < 15.0 allocated).

## Plan Description & Strategy

This plan implements the automated post-execution calibration module `sandbox_executor.calibration` and the `holon calibrate <execution_branch>` CLI command within `apps/sandbox-executor`. Post-execution calibration is a cornerstone of Holon's epistemic integrity, closing the loop between pre-execution plan predictions and post-execution outcomes.

In Step 1, we implement `sandbox_executor.calibration` to handle predicted metrics extraction, execution telemetry ingestion, mathematical calibration deltas (absolute errors, Brier score components, EV realization), entropy factor breakdown, and markdown report generation conforming to the established `plans/P-{plan_id}_calibration.md` schema. In Step 2, we integrate the `holon calibrate` subcommand into `sandbox_executor.cli`, implementing branch checkout/creation (`<execution_branch>/calibrated`), structured markdown report generation, automatic git commit, and `--json` standard output support. In Step 3, we develop a comprehensive unit test suite in `apps/sandbox-executor/tests/test_calibration.py`, testing all calculation formulas, parsers, CLI flags, and git workflows. In Step 4, we verify end-to-end functionality, CLI backward compatibility, PEP 8/Ruff linting, and 100% test pass rate with `uv run pytest`.

---

## Step 1: Implement Core Calibration Analysis Engine in apps/sandbox-executor/src/sandbox_executor/calibration.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** A self-contained domain module with zero external library dependencies, low risk, and high internal cohesion.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED
- **Hypothesis being tested:** Telemetry metrics and predicted plan metrics can be reliably dual-sourced from ledger JSONL records and markdown files with deterministic fallback heuristics, ensuring robust calibration across varied agent executions.
- **Learning target:** Resilient extraction patterns and deterministic fallbacks for sparse execution telemetry (e.g., estimating duration and patch size from git diffs when ledger fields are absent).
- **Maximum acceptable cost for this learning:** 4.5 cost units.

### Intent & Git Integration

- **Step Intent:** Create `apps/sandbox-executor/src/sandbox_executor/calibration.py` containing data models, metric parsers, delta calculation formulas, entropy factor decomposition, and markdown report generation.
- **Git branch:** I-1790379374-add-holon-calibrate-command/step1-calibration-engine
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Define strongly-typed dataclasses for PredictedMetrics, ActualMetrics, CalibrationDeltas, EntropyFactors, and CalibrationReport.
- Implement predicted metrics parsing:
  - Extract plan_id from the execution branch or argument string.
  - Search `holon-knowledge/ledger/plans.jsonl` for a matching `plan_id` record and extract `p_success`, `entropy`, `cost`, `learning_value`, `impact`, and `ev`.
  - Provide a fallback parser for markdown plan files (`plans/P-{plan_id}.md`) using regular expressions to extract values from the `## Overall Plan Metrics` markdown table.
- Implement actual metrics and telemetry ingestion:
  - Search `holon-knowledge/ledger/executions.jsonl` for the matching execution record by `plan_branch` or `execution_id`.
  - Ingest execution telemetry: `duration`, `tokens`, `exit_code`, `test_pass_rate`, and `patch_size`.
  - When telemetry fields are sparse or missing in the ledger, inspect the execution file (`executions/E-*.md`) and execute `git diff --stat` against the plan branch to compute actual patch size (files changed, lines added, lines deleted) and infer execution success (`success_actual = 1.0` if status is success and exit code 0).
  - Compute actual entropy ($\Delta S_{\text{actual}}$) using the observable weights from `holon-config/metrics/entropy_config.json` ($u_1 \cdot \text{SSA} + u_2 \cdot \text{IRR} + u_3 \cdot \text{CL} + u_4 \cdot \text{SER} + u_5 \cdot \text{NOV}$).
- Implement mathematical calibration calculations:
  - Compute absolute error deltas: $p\_success\_error = |p\_success\_pred - p\_success\_actual|$, $entropy\_error = |entropy\_pred - entropy\_actual|$, $cost\_error = |cost\_pred - cost\_actual|$, $impact\_error = |impact\_pred - impact\_actual|$, and $learning\_value\_error = |learning\_value\_pred - learning\_value\_actual|$.
  - Compute actual Expected Value: $EV_{\text{actual}} = P(\text{success})_{\text{actual}} \times \text{Impact}_{\text{actual}} + \mu \times \text{LearningValue}_{\text{actual}} - \lambda \times \Delta S_{\text{actual}} - \text{Cost}_{\text{actual}}$ using $\lambda = 0.3$ and $\mu = 0.5$.
  - Compute $\Delta EV = EV_{\text{actual}} - EV_{\text{pred}}$.
  - Compute qualitative accuracy ratings (Exact, High, Moderate, Low) and bias directions (Slight Underconfidence, Overestimated Risk, Conservative Underestimate, etc.) matching existing calibration reports.
- Implement report formatting:
  - Generate structured markdown identical to `plans/P-{plan_id}_calibration.md` schema, including Executive Calibration Summary table, Mathematical Derivations & Calibration Errors, Entropy Factor Breakdown, and Calibration Assessment.
  - Implement a `to_dict()` or `as_dict()` method on the report dataclass to support clean JSON serialization for stdout.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (The core module must be completed and validated before CLI integration and testing can succeed)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards), `holon-config/world/constraints.md` §3 (Ledger Immutability).
- **Potential failure modes for this step:**
  - File parsing crashes on unexpectedly formatted markdown tables or incomplete JSONL lines.
  - Inaccurate mathematical formulas deviating from `docs/metrics.md`.
- **Guardrails and early‑abort checks:**
  - Enforce read-only file handling for ledgers.
  - Provide complete fallback chains with sensible defaults (e.g. impact = 50.0, cost = 5.0) when metadata is completely unreachable, logging warnings instead of unhandled exceptions.

### Success & Discard Criteria

- **Success:** `sandbox_executor.calibration` imports cleanly, successfully parses test plans and executions, accurately computes calibration deltas, and generates reports matching the expected markdown format.
- **Discard:** Abort if metric calculations fail to match the mathematical invariants specified in `docs/metrics.md` §1.4.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.8   |
| impact_pred         | 75.0  |
| cost_pred           | 4.5   |
| learning_value_pred | 4.0   |
| ev_pred             | 65.96 |

### Step Metrics Rationale

High success probability (0.92) due to clear mathematical specifications and existing calibration report reference files. Balanced exploration allows testing resilient heuristic ingestion patterns across sparse telemetry sources.

---

## Step 2: Implement holon calibrate Subcommand in apps/sandbox-executor/src/sandbox_executor/cli.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard CLI extension inside an existing module with well-established argparse patterns.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add `calibrate` subparser to `apps/sandbox-executor/src/sandbox_executor/cli.py` with arguments `execution_branch` and `--json`, orchestrating git branch creation, report generation, and commits.
- **Git branch:** I-1790379374-add-holon-calibrate-command/step2-cli-subcommand
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `sandbox_executor.cli.main()`, register the `calibrate` subparser under `subparsers.add_parser("calibrate")`.
- Add positional argument `execution_branch` representing the target execution branch name.
- Add optional flag `--json` (action="store_true") to output calibration metrics in JSON format to stdout.
- Implement the calibration execution handler in `cli.py` (or delegate to `sandbox_executor.calibration.run_calibrate_cli`):
  - Parse the plan ID and execution details from the `execution_branch`.
  - Validate that the execution branch exists in git.
  - Derive the calibrated branch name: if `execution_branch` ends with `/_`, replace it with `/calibrated`; otherwise append `/calibrated` (e.g. `I-.../P-.../E-.../calibrated`).
  - Checkout or create the calibrated branch using git commands (`git checkout -B <calibrated_branch> <execution_branch>`).
  - Execute calibration analysis via `sandbox_executor.calibration`.
  - Write the generated markdown calibration report to `plans/P-{plan_id}_calibration.md`.
  - Stage the report and commit to git on the calibrated branch with message `chore(calibration): add calibration report for P-{plan_id}`.
  - If `--json` flag is provided, output the structured calibration JSON to stdout without extraneous log messages.
  - If `--json` is not provided, print a formatted human-readable summary of the calibration results and the committed file path.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO (Follows direct implementation patterns from existing CLI subcommands)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §1 (Prefix-based branch isolation, commit boundaries), `docs/safety.md` §1 (Git as safety boundary).
- **Potential failure modes for this step:**
  - Branch checkout fails due to uncommitted working tree changes.
  - Git commit fails if git author/email is unset in the execution environment.
- **Guardrails and early‑abort checks:**
  - Check `git status --porcelain` before branch operations; refuse or warn if dirty working tree.
  - Ensure committer environment variables (`GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`) are provided with sensible defaults if unconfigured.

### Success & Discard Criteria

- **Success:** `holon calibrate <execution_branch>` executes smoothly, checks out the `/calibrated` branch, writes and commits `plans/P-{plan_id}_calibration.md`, and prints output or valid JSON when `--json` is supplied.
- **Discard:** Abort if git branch transitions violate prefix isolation or corrupt working directory state.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 1.2   |
| impact_pred         | 65.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 58.74 |

### Step Metrics Rationale

CLI wiring is straightforward and follows existing patterns in `sandbox_executor.cli`. Low risk and predictable behavior.

---

## Step 3: Implement Comprehensive Test Suite in apps/sandbox-executor/tests/test_calibration.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Dedicated test module providing unit and integration test coverage for calibration logic and CLI invocation.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Create `apps/sandbox-executor/tests/test_calibration.py` to achieve comprehensive coverage for `sandbox_executor.calibration` and `sandbox_executor.cli` calibration commands.
- **Git branch:** I-1790379374-add-holon-calibrate-command/step3-unit-tests
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `apps/sandbox-executor/tests/test_calibration.py` using standard `pytest` and `unittest.mock`.
- Test plan predicted metrics parsing:
  - Test parsing directly from `plans.jsonl` records.
  - Test parsing directly from markdown files (`plans/P-*.md`) containing the standard `## Overall Plan Metrics` table.
  - Test handling of missing plans and fallback defaults.
- Test execution telemetry ingestion:
  - Test extraction from `executions.jsonl` records with complete telemetry.
  - Test fallback telemetry calculation using simulated git diff stats and execution status.
- Test mathematical calibration derivations:
  - Test accuracy of absolute errors ($p\_success$, $entropy$, $cost$, $impact$, $learning\_value$).
  - Test EV calculation verification against manual calculation for both predicted and actual values.
  - Test entropy factor breakdown calculations (SSA, IRR, CL, SER, NOV).
  - Test qualitative rating and bias direction assignment.
- Test report formatting:
  - Verify generated markdown contains all required sections matching the reference calibration report format.
  - Verify JSON serialization outputs valid, well-structured JSON schema with all expected keys.
- Test CLI integration:
  - Mock git operations (`subprocess.run` calls for checkout, commit, diff) and verify proper branch naming (`/calibrated`).
  - Test `holon calibrate` CLI invocation with and without `--json`.
  - Verify error handling and exit codes when an invalid branch is supplied.
- Ensure 100% test pass rate when running `uv run pytest apps/sandbox-executor/tests/test_calibration.py`.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (Acceptance criterion strictly requires 100% test pass rate with `uv run pytest`)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (Testing Constraints: pytest runner, no modifying existing tests).
- **Potential failure modes for this step:**
  - Mock leakage or tests attempting real git commits to the active repository.
- **Guardrails and early‑abort checks:**
  - Mock all subprocess and git interactions or isolate in temporary directories (`tmp_path`).

### Success & Discard Criteria

- **Success:** All test cases in `apps/sandbox-executor/tests/test_calibration.py` pass cleanly with zero warnings or failures.
- **Discard:** Abort if test coverage reveals mathematical inconsistencies in calibration error definitions.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.0   |
| impact_pred         | 70.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 64.20 |

### Step Metrics Rationale

Writing comprehensive unit tests is standard exploit work that provides high reliability, validates the implementation, and prevents future regressions.

---

## Step 4: End-to-End CLI Verification, Formatting, and Static Type Validation

- **Sub‑intent recommendation:** NO
- **Reasoning:** Final integration and quality assurance step to ensure repo-wide compliance and zero regression.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Verify `./holon calibrate --help`, validate full test suite passes without regressions, and check code style with Ruff.
- **Git branch:** I-1790379374-add-holon-calibrate-command/step4-e2e-verification
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Test the root `./holon` wrapper script with `./holon calibrate --help` to confirm CLI discoverability and correct argument descriptions.
- Run `uv run pytest` across the entire workspace (`apps/sandbox-executor/tests/`) to ensure no regressions in existing CLI, planner, or executor tests.
- Run code quality validation using `ruff check apps/sandbox-executor` and `ruff format --check apps/sandbox-executor` to enforce strict PEP 8 and monorepo style standards.
- Verify static typing compliance across all new functions in `sandbox_executor.calibration`.
- Check git status to ensure working tree cleanliness and verify that no untracked or unwanted scratch files remain.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1 & §2 (Runtime, PEP 8, Docstrings, Typing).
- **Potential failure modes for this step:**
  - Ruff formatting or import ordering discrepancies.
  - Inadvertent breakage of existing CLI commands (`intent`, `plan`, `execute`, `init`).
- **Guardrails and early‑abort checks:**
  - Automated lint fixes with `ruff format` and targeted verification of all subcommands.

### Success & Discard Criteria

- **Success:** `./holon calibrate --help` displays clean documentation, all workspace tests pass with 100% success rate, and Ruff checks pass with zero errors.
- **Discard:** Abort if existing test suite fails or monorepo workspace resolution fails.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.8   |
| impact_pred         | 60.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 56.11 |

### Step Metrics Rationale

Final validation is low entropy, low cost, and ensures production readiness.
