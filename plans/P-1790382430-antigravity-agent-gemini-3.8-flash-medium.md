# Plan for I-1790382419-automate-holon-flow-lifecycle-pipeline

- **Plan ID:** P-1790382430-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-09-26T00:27:10.428Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted empirical exploration of autonomous PR review loop consensus mechanics and checkpoint recovery strategies, while rigorously maintaining human-only merge constraints.
- **Safety priority level:** elevated
- **Priority Justification:** Triggered by `holon-config/world/constraints.md` §1 (Prefix-Based Isolation & Human Approval for Root Intents) and Bean 0034 (Human-Only PR Merging Rule), ensuring the automated pipeline halts unconditionally before merging to `main` or parent branches.

## Exploration

- **Proportion of steps that are exploratory:** 0.20
- **Justification:** Step 3 incorporates balanced exploration for autonomous reviewer loop consensus scoring and state checkpointing across stage transitions, while remaining within the strict safety bounds and entropy budget.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 2.8   |
| impact_pred         | 95.0  |
| cost_pred           | 20.0  |
| learning_value_pred | 5.5   |
| ev_pred             | 65.51 |

### Strategy Rationale

The overall plan metrics were derived from the individual step-level metrics as follows:

- **p_success_pred**: 0.88. Sourced from the integration bottleneck in Step 3, which coordinates plan execution, multi-agent review consensus loops, and post-execution calibration across git branch transitions.
- **entropy_pred**: 2.8. Calculated as the maximum step-level entropy (Step 3: 2.4) plus a 0.4 integration margin for end-to-end pipeline coordination and state checkpointing across stages. The total predicted sum of step entropies is 8.4, well below the allocated budget of 15.0.
- **impact_pred**: 95.0. Completes the foundational end-to-end orchestration engine (`sandbox_executor.flow`) that automates all five stages of the Holon lifecycle, transforming manual multi-step executions into an autonomous, observable, and reproducible pipeline.
- **cost_pred**: 20.0. Computed as the direct sum of individual step costs (3.5 + 4.0 + 5.5 + 2.5 + 4.5 = 20.0).
- **learning_value_pred**: 5.5. Delivers key epistemic gains by establishing standardized checkpointing schemas, formalizing autonomous PR review consensus convergence rules, and proving the programmatic integration of Bean 0034 and Bean 0038.
- **ev_pred**: 65.51. Calculated strictly using the canonical config-driven Expected Value formula:
  $$EV = P(\\text{success}) \\times \\text{Impact} + \\mu \\times \\text{LearningValue} - \\lambda \\times \\Delta S_{\\text{intent}} - \\text{Cost}$$
  With system constants $\\lambda = 0.3$ and $\\mu = 0.5$ from `holon-config/metrics/ev_config.json`:
  $$EV = 0.88 \\times 95.0 + 0.5 \\times 5.5 - 0.3 \\times 2.8 - 20.0 = 83.60 + 2.75 - 0.84 - 20.0 = 65.51$$

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python strict target `==3.13.*`, package management via `uv` workspace commands).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8 compliance, strict static typing with `typing`, docstrings for public classes/methods, no wildcard imports).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1` as test runner, unit and integration tests located in `apps/sandbox-executor/tests/`, no unauthorized test modification).
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: strict prefix-based branch isolation for intents, plans, executions, and calibrations; mandatory rebase discipline).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: zero modification policy on `holon-knowledge/ledger/*.jsonl`, append-only semantics).
  - `docs/safety.md` §1 & §5 (Git as safety boundary, Human Review boundary: Bean 0034 Human-Only PR Merging rule mandates that automated review loops halt upon consensus approval without merging to `main`).
  - Bean 0038 (Post-execution calibration creating `plans/P-*_calibration.md` on `/calibrated` branch).
- **Potential violations or edge cases:**
  - Accidental attempt by the review loop to merge the intent or execution branch into `main` autonomously (violates Bean 0034 and `docs/safety.md` Invariant 4).
  - Working tree pollution or unclean git state causing branch checkout or rebase failures during stage transitions.
  - Interrupted pipeline runs leaving ambiguous or corrupt checkpoint states in `.holon/flow/`.
  - Violating ledger immutability by rewriting or amending historical records during pipeline retries.
- **Mitigations built into the plan:**
  - Hardcoded safety barrier in Stage 4: the review loop executes consensus checks, posts review feedback, but explicitly yields execution with status `awaiting_human_merge` once consensus approval is reached, preventing autonomous merge commands.
  - Defensive git orchestration: verify clean working trees prior to branch checkout, use explicit ref updates, and isolate temporary staging in isolated sandbox workspaces.
  - Atomic checkpoint writing using temporary files and atomic renames to prevent corrupted stage recovery records.
  - Append-only ledger access using strictly append mode (`"a"`) and checking existing records by ID before appending to prevent duplicate entries on retry.
- **Residual risk accepted (and why):**
  - External agent CLI executions in Stages 2 and 3 depend on container/runner availability; accepted because fallback runners and mock handlers in tests guarantee predictable behavior across test and execution environments.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 8.4
- **Budget Compliance:** The strategy fits within budget (Predicted Plan Entropy sum of 8.4 < 15.0 allocated).

## Plan Description & Strategy

This plan implements `sandbox_executor.flow` within `apps/sandbox-executor` to provide a programmatic orchestration pipeline engine for the complete 5-stage Holon Flow lifecycle:
1. **Stage 1 (Intent Creation):** Validate intent payload schema, create isolated intent branch `I-{timestamp}-{slug}/_`, and append record to `holon-knowledge/ledger/intents.jsonl`.
2. **Stage 2 (Plan Generation):** Branch from intent to `I-.../P-{timestamp}-{agent}-{model}/_`, invoke plan generation agent/runner, validate generated plan markdown `plans/P-*.md`, and record in `holon-knowledge/ledger/plans.jsonl`.
3. **Stage 3 (Plan Execution):** Branch from plan to `I-.../P-.../E-{timestamp}-{agent}-{model}/_`, invoke sandbox execution agent, run pytest suite, generate execution summary `executions/E-*.md`, and record in `holon-knowledge/ledger/executions.jsonl`.
4. **Stage 4 (PR Review Loop):** Run autonomous multi-agent review iterations assessing diffs, test logs, and metric consistency. Halt upon consensus approval to strictly honor Bean 0034 (Human-Only PR Merging).
5. **Stage 5 (Post-Execution Calibration):** Run post-execution calibration engine (Bean 0038) on the execution branch, creating calibration analysis report at `plans/P-*_calibration.md` on `/calibrated` branch.

In addition, the pipeline features stage checkpointing (`.holon/flow/checkpoint-{intent_slug}.json`) enabling resilient resumption (`--from-stage`), structured JSON logging, CLI integration via `holon flow`, and an exhaustive unit and integration test suite achieving 100% test pass rate in `apps/sandbox-executor/tests/test_flow.py`.

---

## Step 1: Design and Implement Flow Engine Architecture & Stage Checkpointing in apps/sandbox-executor/src/sandbox_executor/flow.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Foundational core architecture with clear data boundaries, low complexity, and high internal cohesion.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Create `apps/sandbox-executor/src/sandbox_executor/flow.py` defining stage enumerations, data models, pipeline context, atomic checkpointing mechanism, and stage execution interfaces.
- **Git branch:** I-1790382419-automate-holon-flow-lifecycle-pipeline/step1-flow-core
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Define `FlowStage` enumeration with values: `INTENT`, `PLAN`, `EXECUTE`, `REVIEW`, `CALIBRATE`, and `COMPLETED`.
- Define `StageStatus` enumeration: `PENDING`, `RUNNING`, `SUCCESS`, `FAILED`, `HALTED_FOR_HUMAN`, `SKIPPED`.
- Define strongly-typed dataclasses for pipeline configuration and stage state:
  - `StageResult`: records stage name, status, start/end timestamps, output payload (branch names, artifact paths, ledger entries), and error details if any.
  - `FlowContext`: carries repository path, intent metadata, active branch names, selected agent/model parameters, runtime flags (dry run, auto calibration), and execution logs.
  - `FlowCheckpoint`: serializes the full pipeline state, completed stages, active context, and checkpoint timestamp.
- Implement atomic checkpoint persistence:
  - Default checkpoint storage in `.holon/flow/` under the repository root.
  - Write checkpoint state using temporary file creation and atomic rename (`os.replace`) to guard against process termination corruption.
  - Provide `save_checkpoint(context, filepath)` and `load_checkpoint(filepath)` functions.
- Implement the base `PipelineEngine` class:
  - Initialize with `FlowContext` and optional starting stage for resumption.
  - Provide lifecycle hooks: `before_stage`, `execute_stage`, `after_stage`, and `handle_stage_failure`.
  - Implement structured logging with timestamps, stage indicators, and secret/credential redaction using existing helpers.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (The flow data models and state management are required by all subsequent stages)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: typing, docstrings), `holon-config/world/constraints.md` §1 (Prefix-based isolation).
- **Potential failure modes for this step:**
  - Corrupt or incomplete checkpoint files during sudden process termination.
  - State mutation bugs during resumption from intermediate stages.
- **Guardrails and early‑abort checks:**
  - Use atomic write replacements for checkpoints.
  - Validate checkpoint schema and branch existence before resuming execution from a saved stage.

### Success & Discard Criteria

- **Success:** `sandbox_executor.flow` exports `FlowStage`, `FlowContext`, `StageResult`, and `PipelineEngine`, with checkpoint save/load roundtripping without data loss.
- **Discard:** Discard if state models fail to capture necessary git branch context or require circular dependencies with agent runners.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.6   |
| impact_pred         | 75.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 68.27 |

### Step Metrics Rationale

High success probability (0.95) and low entropy (1.6) as this step focuses on standard Python dataclasses, enumeration, and atomic file I/O patterns. Provides foundational impact (75.0) for the entire pipeline.
$$EV = 0.95 \times 75.0 + 0.5 \times 2.0 - 0.3 \times 1.6 - 3.5 = 71.25 + 1.0 - 0.48 - 3.5 = 68.27$$

---

## Step 2: Implement Stage 1 (Intent) and Stage 2 (Plan) Orchestration Logic

- **Sub‑intent recommendation:** NO
- **Reasoning:** Direct programmatic encapsulation of existing `intent_creator` and `planner` logic with predictable git operations.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Implement `run_intent_stage` and `run_plan_stage` methods in `sandbox_executor.flow`, orchestrating intent creation, branch initialization, plan generation, and ledger updates.
- **Git branch:** I-1790382419-automate-holon-flow-lifecycle-pipeline/step2-intent-plan-stages
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Implement `run_intent_stage(context: FlowContext) -> StageResult`:
  - Validate intent payload: ensure required fields (`slug`, `goal`, `description`, `target_branch`) are present and conform to `docs/intent/intent_markdown_spec.md`.
  - Format or verify branch prefix: generate `I-{timestamp}-{slug}` if not explicitly supplied.
  - Ensure local repository is clean and rebased on `origin/{target_branch}`.
  - Create and checkout intent branch `I-{timestamp}-{slug}/_`.
  - Append proposed intent record to `holon-knowledge/ledger/intents.jsonl` using strict append-only semantics.
  - Commit intent creation to git with standardized commit message and author identity.
  - Record stage output in `FlowContext` and update checkpoint.
- Implement `run_plan_stage(context: FlowContext) -> StageResult`:
  - Verify that the active branch is the intent branch `I-.../_`.
  - Construct plan ID `P-{timestamp}-{agent}-{model}` using sanitized model name heuristics.
  - Create and checkout plan branch `I-.../P-.../_`.
  - Prepare planner prompt including intent goal, constraints, and repository file structure.
  - Invoke planner agent runner (or container sandbox according to trust/entropy policy).
  - Verify generated plan file `plans/P-{plan_id}.md` exists, conforms to required markdown headers, and contains a valid metrics table.
  - Parse metrics from markdown table, compute Expected Value using config-driven constants ($\\lambda = 0.3, \\mu = 0.5$).
  - Append plan record to `holon-knowledge/ledger/plans.jsonl`.
  - Commit plan document and ledger update to git.
  - Record stage output in `FlowContext` and update checkpoint.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Plan stage output is the prerequisite for plan execution in Stage 3)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §1 (Prefix-based isolation), `holon-config/world/constraints.md` §3 (Ledger immutability).
- **Potential failure modes for this step:**
  - Branch collision if intent or plan timestamp is duplicated.
  - Planner agent produces malformed or empty plan file.
- **Guardrails and early‑abort checks:**
  - Verify uniqueness of branch name before checkout.
  - Validate plan document structure with fallback metrics heuristics if parsing encounters non-critical syntax variations.

### Success & Discard Criteria

- **Success:** Stage 1 produces a valid `I-.../_` branch and ledger entry; Stage 2 branches from intent, invokes planner, validates `plans/P-*.md`, and updates `plans.jsonl`.
- **Discard:** Discard if plan branch fails to isolate from intent branch or if ledger append operations corrupt existing JSONL lines.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.8   |
| impact_pred         | 80.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 2.5   |
| ev_pred             | 70.31 |

### Step Metrics Rationale

High probability of success (0.92) as this step builds upon established entrypoint patterns in `intent_creator.py` and `planner.py`.
$$EV = 0.92 \times 80.0 + 0.5 \times 2.5 - 0.3 \times 1.8 - 4.0 = 73.6 + 1.25 - 0.54 - 4.0 = 70.31$$

---

## Step 3: Implement Stage 3 (Execution), Stage 4 (PR Review Loop with Bean 0034), and Stage 5 (Calibration with Bean 0038)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Core pipeline execution and safety gating within a single module; sub-intent decomposition would fragment the unified state machine.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED
- **Hypothesis being tested:** An autonomous PR review consensus loop can evaluate code changes, run automated test passes, and produce review packages, while enforcing an unbreachable termination boundary at consensus approval to uphold Bean 0034.
- **Learning target:** Clean consensus criteria for autonomous review evaluation and robust integration between execution telemetry and post-execution calibration.
- **Maximum acceptable cost for this learning:** 5.5 cost units.

### Intent & Git Integration

- **Step Intent:** Implement `run_execute_stage`, `run_review_stage`, and `run_calibrate_stage` in `sandbox_executor.flow`, completing the full 5-stage lifecycle.
- **Git branch:** I-1790382419-automate-holon-flow-lifecycle-pipeline/step3-exec-review-calibrate
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Implement `run_execute_stage(context: FlowContext) -> StageResult`:
  - Derive execution branch `I-.../P-.../E-{timestamp}-{agent}-{model}/_` off the plan branch.
  - Rebase execution branch on parent plan branch per `holon-config/world/constraints.md` §1.
  - Invoke execution agent runner with plan instructions and intent context.
  - Run project test suite using `pytest` to capture test pass rate and stdout/stderr telemetry.
  - Record execution record in `executions/E-{exec_id}.md` and append entry to `holon-knowledge/ledger/executions.jsonl`.
  - Stage changes, commit execution results, and update checkpoint.
- Implement `run_review_stage(context: FlowContext) -> StageResult`:
  - Generate review package conforming to `docs/safety.md` §5 and `docs/git_flow.md` §4.2: intent goal, diff summary, test results, predicted vs actual metrics.
  - Orchestrate autonomous review iterations: evaluate diff against intent acceptance criteria and test coverage.
  - Assess review consensus: calculate review approval score based on test results (100% pass rate) and safety invariant checks.
  - **Enforce Bean 0034 (Human-Only PR Merging):** When consensus approval is reached, record consensus decision in context and flow log, set stage status to `HALTED_FOR_HUMAN`, and explicitly halt the pipeline without performing any git merge into `main` or the parent intent branch.
  - Log instructions for human reviewer approval command (`holon review approve <intent_branch>`).
- Implement `run_calibrate_stage(context: FlowContext) -> StageResult`:
  - Run post-execution calibration engine (Bean 0038) using `sandbox_executor.calibration.run_calibrate`.
  - Checkout `<execution_branch>/calibrated` branch.
  - Ingest predicted plan metrics and actual execution telemetry.
  - Calculate calibration deltas: Brier score components, error terms, actual EV realization, and entropy factor breakdown.
  - Generate and commit calibration report at `plans/P-{plan_id}_calibration.md` on the calibrated branch.
  - Update flow checkpoint with calibration results.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (Step 3 is the critical bottleneck coordinating execution, safety review gating, and calibration)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §1 (Prefix isolation), `docs/safety.md` §5 & Invariant 4 (Human review boundary: Bean 0034), Bean 0038 (Post-execution calibration).
- **Potential failure modes for this step:**
  - Autonomous review loop attempts to execute git merge to `main`.
  - Execution test failures or runtime exceptions leading to unhandled pipeline crashes.
  - Calibration engine cannot locate execution branch or plan metrics.
- **Guardrails and early‑abort checks:**
  - Hard guardrail: prohibit `git merge main` commands in review runner; execution stops once consensus approval is achieved.
  - Catch test execution failures gracefully, recording `FAILED` stage status and persisting checkpoint for diagnosis.

### Success & Discard Criteria

- **Success:** Execution stage runs agent and captures test telemetry; Review stage executes review loop and halts with `HALTED_FOR_HUMAN` upon consensus approval (never merging); Calibration stage produces `plans/P-*_calibration.md` on `/calibrated` branch.
- **Discard:** Discard if the review loop attempts an autonomous merge or if calibration report fails to match the Bean 0038 specification.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 2.4   |
| impact_pred         | 90.0  |
| cost_pred           | 5.5   |
| learning_value_pred | 5.0   |
| ev_pred             | 75.48 |

### Step Metrics Rationale

Bottleneck step with 0.88 success probability due to multi-stage coordination. Balanced exploration yields high learning value (5.0) in codifying autonomous PR review consensus and Bean 0034 safety boundaries.
$$EV = 0.88 \times 90.0 + 0.5 \times 5.0 - 0.3 \times 2.4 - 5.5 = 79.2 + 2.5 - 0.72 - 5.5 = 75.48$$

---

## Step 4: Integrate Pipeline CLI Subcommand (holon flow) in apps/sandbox-executor/src/sandbox_executor/cli.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard CLI argument parsing and dispatching extension adhering to established CLI patterns.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add `flow` subcommand to `apps/sandbox-executor/src/sandbox_executor/cli.py` to allow initiating and resuming the 5-stage pipeline from the command line.
- **Git branch:** I-1790382419-automate-holon-flow-lifecycle-pipeline/step4-cli-integration
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Register `flow` subparser in `apps/sandbox-executor/src/sandbox_executor/cli.py`:
  - Add positional/optional argument `intent_file`: path to JSON intent payload.
  - Add `--from-stage`: allows resuming an existing flow from a specific stage (`intent`, `plan`, `execute`, `review`, `calibrate`).
  - Add `--checkpoint`: optional custom path to a flow checkpoint file.
  - Add `--agent`: agent runner to employ (defaults to `antigravity`).
  - Add `--model`: model name for planner and executor (defaults to current model).
  - Add `--dry-run`: flag to validate stages, payloads, and branches without executing agents or git commits.
  - Add `--json`: output pipeline status and stage results in structured JSON format to stdout.
- Implement CLI handler dispatching to `sandbox_executor.flow.run_flow_pipeline`:
  - Parse CLI options into `FlowContext`.
  - Handle exceptions gracefully, printing user-friendly error diagnostics and returning non-zero exit codes on failure.
  - Support automatic terminal summary printing showing status of all 5 stages.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards).
- **Potential failure modes for this step:**
  - Argument parsing errors or conflicting flags (e.g. `--from-stage` without checkpoint or valid state).
- **Guardrails and early‑abort checks:**
  - Validate that intent payload exists when starting from Stage 1, or that valid checkpoint/branch exists when resuming from later stages.

### Success & Discard Criteria

- **Success:** `holon flow --help` displays all flags; invoking `holon flow` executes pipeline stages or resumes from checkpoint as requested.
- **Discard:** Discard if CLI additions break existing subcommands (`intent`, `plan`, `execute`, `calibrate`, `init`).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.2   |
| impact_pred         | 65.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 1.5   |
| ev_pred             | 59.64 |

### Step Metrics Rationale

High success probability (0.95) and minimal entropy (1.2) due to direct adherence to existing argparse patterns in `cli.py`.
$$EV = 0.95 \times 65.0 + 0.5 \times 1.5 - 0.3 \times 1.2 - 2.5 = 61.75 + 0.75 - 0.36 - 2.5 = 59.64$$

---

## Step 5: Implement Comprehensive Unit & Integration Test Suite in apps/sandbox-executor/tests/test_flow.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard testing procedure ensuring quality, coverage, and zero regression across all flow stages.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Create `apps/sandbox-executor/tests/test_flow.py` covering all 5 stages, checkpoint recovery, Bean 0034 human review halt, Bean 0038 calibration integration, error handling, and CLI invocation.
- **Git branch:** I-1790382419-automate-holon-flow-lifecycle-pipeline/step5-tests-validation
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `TestFlowDataModels`:
  - Test serialization and deserialization of `FlowContext`, `StageResult`, and `FlowCheckpoint`.
  - Test atomic checkpoint saving and corruption-resistant loading.
- Create `TestFlowStages`:
  - Mock git commands and file system operations using pytest `tmp_path` and `unittest.mock`.
  - Test Stage 1: payload validation, branch derivation, ledger append.
  - Test Stage 2: plan branch creation, mock planner invocation, markdown parsing, and EV calculation.
  - Test Stage 3: execution branch creation, agent execution simulation, test result capture.
  - Test Stage 4: review loop consensus calculation and strict verification that execution halts with `HALTED_FOR_HUMAN` without merging autonomously (Bean 0034 invariant).
  - Test Stage 5: calibration report generation on `/calibrated` branch (Bean 0038 invariant).
- Create `TestFlowResumptionAndErrorHandling`:
  - Test resumption with `--from-stage execute` using a pre-populated checkpoint.
  - Test stage failure handling: error capture, checkpoint preservation, and structured logging.
- Create `TestFlowCLI`:
  - Test CLI argument parsing for `holon flow`, `--dry-run`, `--json`, and `--from-stage`.
- Execute test suite using `uv run pytest apps/sandbox-executor/tests/test_flow.py` and verify 100% test pass rate with zero regressions.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3, Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (Testing Constraints: pytest, tests located in `tests/`, no test modification during execution).
- **Potential failure modes for this step:**
  - Mock leaks or residual files contaminating the active workspace.
  - Fragile test assertions depending on external git remotes or live agent binaries.
- **Guardrails and early‑abort checks:**
  - Strictly isolate all file operations to `tmp_path`.
  - Mock all subprocess calls to `git`, `docker`, and external agent CLIs.

### Success & Discard Criteria

- **Success:** 100% pass rate across all test cases in `apps/sandbox-executor/tests/test_flow.py` with zero regressions on existing tests.
- **Discard:** Discard if tests fail or if test pass rate falls below 100%.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 1.4   |
| impact_pred         | 85.0  |
| cost_pred           | 4.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 76.48 |

### Step Metrics Rationale

High success probability (0.94) and thorough coverage confirming compliance with all five stages, Bean 0034 human review boundary, and Bean 0038 calibration.
$$EV = 0.94 \times 85.0 + 0.5 \times 3.0 - 0.3 \times 1.4 - 4.5 = 79.9 + 1.5 - 0.42 - 4.5 = 76.48$$
