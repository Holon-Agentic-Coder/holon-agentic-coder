# Plan for I-1791881582-holon-flow-runner-flags-and-docs

- **Plan ID:** P-1791882348-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-10-13T09:05:48.275Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with empirical validation of CLI runner arguments, stage skipping dispatch, lifecycle
  checkpoint persistence, and comprehensive documentation verification without regression.
- **Safety priority level:** standard
- **Priority Justification:** Triggered by `holon-config/world/ruleset.md` §1 & §2 (Python runtime `==3.13.*`, workspace
  isolation under `apps/sandbox-executor`, strict PEP 8 formatting, static type annotations with `typing` module,
  docstring maintenance), `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test placement under
  `apps/sandbox-executor/tests/`, test modifications declared in planning), `holon-config/world/constraints.md` §1 (Git
  Flow & Branch Constraints: prefix-based branch isolation, rebase discipline, commit boundaries),
  `holon-config/world/constraints.md` §2 (Sandbox containment: subprocess and filesystem containment, no unwhitelisted
  subprocesses, process sandbox `< 10` entropy), `holon-config/world/constraints.md` §3 (Ledger immutability:
  append-only ledgers, zero modifications to historical lines), `docs/safety.md` §1 & §2 (Git as safety boundary,
  sandboxing mandatory for execution), Bean 0034 (Human-Only PR Merging: pipeline halts with `HALTED_FOR_HUMAN` upon
  review consensus and never merges autonomously to main), Bean 0038 (Post-Execution Calibration: run calibration
  analysis on execution branch and commit to `/calibrated` branch), Bean 0040 (Holon Flow CLI runner flags), and Bean
  0041 (Holon Flow lifecycle documentation).

## Exploration

- **Proportion of steps that are exploratory:** 0.60
- **Justification:** Steps 1, 2, and 5 incorporate balanced exploration to empirically validate the stage skipping
  control flow, CLI argument plumbing and checkpoint deserialization robustness, and hermetic mock verification of token
  reduction and MITM sidecar configurations across diverse CLI execution paths.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.50  |
| impact_pred         | 95.0  |
| cost_pred           | 8.7   |
| learning_value_pred | 5.2   |
| ev_pred             | 84.00 |

### Strategy Rationale

The overall plan metrics were derived from individual step-level metrics as follows:

- **p_success_pred**: 0.95. Dictated by the bottleneck steps (Step 1 and Step 5: 0.95), where `FlowContext` and
  `PipelineEngine` stage dispatch must execute cleanly across skipped stages without breaking existing checkpoint
  loading, and all CLI test fixtures must execute hermetically in isolated environments.
- **entropy_pred**: 0.50. Evaluated as the maximum single-step risk profile (Step 1: 0.50), where core pipeline dispatch
  loops and state transitions are modified. The cumulative sum of predicted step entropies across all five steps is 1.70
  (`0.50 + 0.40 + 0.20 + 0.20 + 0.40 = 1.70`), well within the allocated entropy budget of 15.0.
- **impact_pred**: 95.0. Fulfills Beans 0040 and 0041 by unlocking full operational control of the 5-stage Holon Flow
  runner: complete CLI flag support (`--skip-review`, `--skip-calibrate`, `--max-review-iterations`, `--token-reduce`,
  `--mitm-web`), robust plumbing into `FlowContext` and `PipelineEngine`, comprehensive lifecycle documentation in
  `.agents/holon_flow.md`, `README.md`, and `apps/sandbox-executor/README.md`, and full CLI test coverage.
- **cost_pred**: 8.7. Calculated as the direct sum of individual step costs (`2.0 + 1.8 + 1.5 + 1.2 + 2.2 = 8.7`).
- **learning_value_pred**: 5.2. Epistemic gain from formalizing stage skipping lifecycles, documenting the 5-stage
  orchestration architecture, creating standard developer reference materials, and establishing hermetic testing
  patterns for composite pipeline CLI tools.
- **ev_pred**: 84.00. Computed strictly via the config-driven Expected Value formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`. With system constants `lambda = 0.3`
  and `mu = 0.5` loaded from `holon-config/metrics/ev_config.json`:
  `EV = 0.95 * 95.0 + 0.5 * 5.2 - 0.3 * 0.50 - 8.7 = 90.25 + 2.60 - 0.15 - 8.7 = 84.00`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python target `==3.13.*`, package
    management via `uv`).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8 compliance, static typing with `typing`
    module, docstring maintenance).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test files placed in
    `apps/sandbox-executor/tests/`, all test additions declared in the planning step).
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: prefix-based branch isolation, rebase
    discipline, commit boundaries).
  - `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: filesystem containment, no unwhitelisted
    subprocesses, process sandbox `< 10` entropy).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: zero modifications to historical ledger entries,
    append-only ledger operations).
  - `docs/safety.md` §1 & §2 (Git as safety boundary, sandboxing mandatory for execution).
  - Bean 0034 (Human-Only PR Merging: autonomous merge to main is strictly prohibited, pipeline halts at consensus with
    `HALTED_FOR_HUMAN`).
  - Bean 0038 (Post-Execution Calibration: run calibration analysis on execution branch and commit to `/calibrated`
    branch).
  - Beans 0040 & 0041 (Holon Flow runner flags and lifecycle documentation).
- **Potential violations or edge cases:**
  - Deserialization of historical checkpoints failing if missing new fields (`skip_review`, `skip_calibrate`,
    `max_review_iterations`, `mitm_web`).
  - Bypassing the Stage 4 review stage leading to premature calibration runs on unverified execution branches.
  - Interactive prompts or stalls during unattended automated execution runs.
  - Inconsistent formatting or broken links across newly created markdown documents triggering Prettier check failures.
  - Sidecar container setup (`mitm_web`) attempting network bindings outside hermetic test sandboxes.
- **Mitigations built into the plan:**
  - Provide explicit defaults in `FlowContext` and `from_dict()` so legacy checkpoints load seamlessly without schema
    errors.
  - Preserve all safety invariants: when review is skipped, execution branch must still be verified for clean rebase and
    passing tests before calibration proceeds.
  - Ensure all CLI flags operate in non-blocking unattended mode by default.
  - Enforce strict markdown formatting hygiene using Prettier convergence on `.agents/holon_flow.md`, `README.md`, and
    `apps/sandbox-executor/README.md`.
  - Mock docker and subprocess interactions completely in CLI tests to ensure hermetic execution within sandboxes.
- **Residual risk accepted (and why):**
  - Skipping review (`--skip-review`) skips automated diff evaluation; accepted as an intentional operator override for
    emergency fixes or rapid testing where manual inspection occurs out-of-band.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 0.50
- **Budget Compliance:** The strategy fits within budget (`0.50 <= 15.0`).

## Plan Description & Strategy

This plan implements Beans 0040 and 0041 across `holon-agentic-coder`:

1. **Core FlowContext & PipelineEngine Stage Flag Plumbing (`flow.py`):**
   - Extend `FlowContext` in `apps/sandbox-executor/src/sandbox_executor/flow.py` with fields:
     `skip_review: bool = False`, `skip_calibrate: bool = False`, `max_review_iterations: int = 3`, and
     `mitm_web: bool = False`. Update `to_dict()` and `from_dict()` with backward-compatible defaults.
   - Update `PipelineEngine.run()`: when `self.context.skip_review` is True, record
     `StageResult(stage=FlowStage.REVIEW, status=StageStatus.SKIPPED)` and advance to Calibrate stage; when
     `self.context.skip_calibrate` is True, record `StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SKIPPED)`.
   - Update the Bean 0034 human halt handler in `PipelineEngine.run()` so that automatic post-review calibration
     respects `not self.context.skip_calibrate`.
   - Plumb `max_review_iterations` into `run_review_stage` to bound review loop iterations.

2. **CLI Runner Flag Exposure & Dispatch Plumbing (`cli.py`):**
   - In `apps/sandbox-executor/src/sandbox_executor/cli.py`, add missing flags to `flow_parser`: `--skip-review`,
     `--skip-calibrate`, `--max-review-iterations`, `--token-reduce`, and `--mitm-web`.
   - In `if args.command == "flow":`, pass these arguments into `FlowContext` initialization and allow CLI overrides
     when resuming from existing checkpoints.
   - Update terminal output formatting to display `SKIPPED` stage statuses cleanly in summary tables.

3. **Comprehensive Lifecycle & Architecture Documentation (`.agents/holon_flow.md` &
   `apps/sandbox-executor/README.md`):**
   - Create `.agents/holon_flow.md` covering the 5-stage lifecycle architecture, intent schema and branch prefixing,
     plan generation, execution and test runs, PR review loop and Bean 0034 safety halts, Bean 0038 calibration reports,
     checkpointing, and complete CLI flag usage.
   - Create `apps/sandbox-executor/README.md` documenting package structure, subcommands (`flow`, `plan`, `execute`,
     `calibrate`, `ledger query`), development workflows, and testing guidelines.

4. **Root README.md Flow Documentation & Workflow Diagrams (`README.md`):**
   - Add a dedicated "Holon Flow Lifecycle Pipeline" section to `README.md` with a Mermaid flowchart, CLI usage
     examples, flag reference table, and links to `.agents/holon_flow.md`.

5. **Hermetic CLI Runner Unit & Integration Test Suite (`tests/test_cli.py` & `tests/test_flow.py`):**
   - Add unit tests verifying argument parsing for all 5 new flags in `apps/sandbox-executor/tests/test_cli.py`.
   - Add integration tests verifying stage skipping in `PipelineEngine`, checkpoint preservation, JSON output
     serialization, and error handling in `apps/sandbox-executor/tests/test_flow.py`.

---

## Step 1: Core FlowContext & PipelineEngine Stage Flag Plumbing

- **Sub‑intent recommendation:** NO
- **Reasoning:** Encapsulated within `sandbox_executor/flow.py` with low blast radius and immediate verification via
  unit tests.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED
- **Hypothesis being tested:** Plumbing optional skip flags and review iteration limits into `FlowContext` and
  `PipelineEngine` enables granular stage execution control while preserving atomic checkpoint serialization and
  backward compatibility.
- **Learning target:** Determine the optimal state transitions and ledger records when stages are skipped in unattended
  pipeline runs.
- **Maximum acceptable cost for this learning:** 2.0

### Intent & Git Integration

- **Step Intent:** Extend `FlowContext` and `PipelineEngine` in `sandbox_executor/flow.py` with `skip_review`,
  `skip_calibrate`, `max_review_iterations`, and `mitm_web`, and implement conditional stage execution logic.
- **Git branch:**
  `I-1791881582-holon-flow-runner-flags-and-docs/P-1791882348-antigravity-agent-gemini-3.8-flash-medium/E-1-flow-engine-flag-plumbing/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/flow.py`:
   - Update `FlowContext` dataclass:
     - Add `skip_review: bool = False`
     - Add `skip_calibrate: bool = False`
     - Add `max_review_iterations: int = 3`
     - Add `mitm_web: bool = False`
     - Ensure `token_reduce: bool = False` remains properly initialized.
     - Update `to_dict()` to include `skip_review`, `skip_calibrate`, `max_review_iterations`, and `mitm_web`.
     - Update `from_dict()` to read `skip_review`, `skip_calibrate`, `max_review_iterations`, and `mitm_web` with safe
       default fallbacks for legacy checkpoint files.
   - Update `PipelineEngine.run()`:
     - Before executing `FlowStage.REVIEW`, check `self.context.skip_review`:
       - If True, create a
         `StageResult(stage=FlowStage.REVIEW, status=StageStatus.SKIPPED, payload={"skipped_by_flag": True})`.
       - Log that Stage 4 was skipped due to `--skip-review`.
       - Store result via `self.after_stage(stage, result)`, call `self.save_checkpoint()`, and continue the stage loop.
     - Before executing `FlowStage.CALIBRATE`, check `self.context.skip_calibrate`:
       - If True, create a
         `StageResult(stage=FlowStage.CALIBRATE, status=StageStatus.SKIPPED, payload={"skipped_by_flag": True})`.
       - Log that Stage 5 was skipped due to `--skip-calibrate`.
       - Store result via `self.after_stage(stage, result)`, call `self.save_checkpoint()`, and continue the stage loop.
     - In the `StageStatus.HALTED_FOR_HUMAN` branch after `FlowStage.REVIEW`:
       - Update the post-review calibration trigger condition to:
         `if stage == FlowStage.REVIEW and self.context.auto_calibrate and not self.context.skip_calibrate:`.
       - If `self.context.skip_calibrate` is True, record Stage 5 as `StageStatus.SKIPPED` in `stage_results` and save
         checkpoint.
   - In `run_review_stage`:
     - Plumb `context.max_review_iterations` into `review_package["consensus"]["iterations"]`.
     - Record `max_review_iterations` in `review_package["consensus"]` payload.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (Downstream CLI runner flags and integration tests directly depend on engine flag support).

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (Docstrings, static typing),
  `holon-config/world/constraints.md` §1 (Branch isolation), `docs/safety.md` §5 (Bean 0034 Human halt preservation).
- **Potential failure modes for this step:** Deserialization exceptions when loading checkpoints created before flag
  additions; unintended bypassing of safety halts.
- **Guardrails and early‑abort checks:** Provide robust default values in `from_dict()`; ensure skipping review does not
  bypass branch rebase invariants.

### Success & Discard Criteria

- **Success:** `FlowContext` and `PipelineEngine` cleanly support stage skipping and iteration bounding, with tests
  verifying serialization and execution state changes.
- **Discard:** Discard if legacy checkpoint loading fails or if stage skipping corrupts stage results dictionary.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.50  |
| impact_pred         | 94.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 89.65 |

### Step Metrics Rationale

Calculated via: `EV = 0.95 * 94.0 + 0.5 * 5.0 - 0.3 * 0.50 - 2.0 = 89.30 + 2.50 - 0.15 - 2.0 = 89.65`. High success
probability for clean dataclass extensions and explicit control flow checks.

---

## Step 2: CLI Runner Flag Exposure & Dispatch Plumbing

- **Sub‑intent recommendation:** NO
- **Reasoning:** Adding CLI arguments and dispatch options within `sandbox_executor/cli.py` has low blast radius.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED
- **Hypothesis being tested:** Exposing standardized CLI flags on `holon flow` aligning with Bean 0040 provides complete
  operator and scriptability control over the 5-stage lifecycle.
- **Learning target:** Determine ergonomic terminal feedback and summary reporting when stages are skipped.
- **Maximum acceptable cost for this learning:** 1.8

### Intent & Git Integration

- **Step Intent:** Add `--skip-review`, `--skip-calibrate`, `--max-review-iterations`, `--token-reduce`, and
  `--mitm-web` to `flow_parser` in `apps/sandbox-executor/src/sandbox_executor/cli.py` and plumb to `FlowContext`.
- **Git branch:**
  `I-1791881582-holon-flow-runner-flags-and-docs/P-1791882348-antigravity-agent-gemini-3.8-flash-medium/E-2-cli-runner-flags/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/cli.py`:
   - In `flow_parser` argument definitions (lines 1005-1056):
     - Add `--skip-review`: `action="store_true"`, help: "Skip Stage 4 (PR review loop) and proceed to calibration."
     - Add `--skip-calibrate`: `action="store_true"`, help: "Skip Stage 5 (post-execution calibration report
       generation)."
     - Add `--max-review-iterations`: `type=int`, `default=3`, help: "Maximum number of autonomous review loop
       iterations in Stage 4 (default: 3)."
     - Add `--token-reduce`: `action="store_true"`, help: `_TOKEN_REDUCE_HELP`.
     - Add `--mitm-web`: `action="store_true"`, help: "Launch mitmweb dashboard on port 8081 for real-time traffic
       inspection."
   - In `if args.command == "flow":` dispatch logic:
     - When initializing fresh `FlowContext`:
       - Pass `skip_review=args.skip_review`.
       - Pass `skip_calibrate=args.skip_calibrate`.
       - Pass `auto_calibrate=not args.skip_calibrate`.
       - Pass `max_review_iterations=args.max_review_iterations`.
       - Pass `token_reduce=args.token_reduce`.
       - Pass `mitm_web=args.mitm_web`.
     - When resuming from existing checkpoint:
       - If `args.skip_review` was passed, override `context.skip_review = True`.
       - If `args.skip_calibrate` was passed, override `context.skip_calibrate = True` and
         `context.auto_calibrate = False`.
       - If `args.max_review_iterations` was passed, override
         `context.max_review_iterations = args.max_review_iterations`.
       - If `args.token_reduce` was passed, override `context.token_reduce = True`.
       - If `args.mitm_web` was passed, override `context.mitm_web = True`.
   - In the console summary printer:
     - Handle `res.status == StageStatus.SKIPPED` by displaying `SKIPPED` alongside `SUCCESS`, `FAILED`, and
       `HALTED_FOR_HUMAN`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (Docstrings, typing), `holon-config/world/constraints.md` §2
  (Sandbox containment).
- **Potential failure modes for this step:** Invalid or negative values for `--max-review-iterations`; flag collision
  with checkpoint settings.
- **Guardrails and early‑abort checks:** Validate `max_review_iterations >= 1`; gracefully override checkpoint defaults
  when CLI flags are explicitly provided.

### Success & Discard Criteria

- **Success:** `holon flow --help` displays all flags with clear help strings; CLI invocations correctly pass arguments
  into `run_flow_pipeline`.
- **Discard:** Discard if CLI argument parsing breaks existing invocations without new flags.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.40  |
| impact_pred         | 92.0  |
| cost_pred           | 1.8   |
| learning_value_pred | 4.5   |
| ev_pred             | 88.65 |

### Step Metrics Rationale

Calculated via: `EV = 0.96 * 92.0 + 0.5 * 4.5 - 0.3 * 0.40 - 1.8 = 88.32 + 2.25 - 0.12 - 1.8 = 88.65`. Standardized
argparse extensions with deterministic behavior.

---

## Step 3: Comprehensive Lifecycle & Architecture Documentation (`.agents/holon_flow.md` & `apps/sandbox-executor/README.md`)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure documentation creation within `.agents/` and `apps/sandbox-executor/` with zero runtime risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT
- **Hypothesis being tested:** N/A (EXPLOIT)
- **Learning target:** N/A (EXPLOIT)
- **Maximum acceptable cost for this learning:** N/A

### Intent & Git Integration

- **Step Intent:** Create `.agents/holon_flow.md` and `apps/sandbox-executor/README.md` fulfilling Bean 0041
  requirements.
- **Git branch:**
  `I-1791881582-holon-flow-runner-flags-and-docs/P-1791882348-antigravity-agent-gemini-3.8-flash-medium/E-3-lifecycle-documentation/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `.agents/holon_flow.md`:
   - Document the complete 5-stage lifecycle architecture:
     - Stage 1: Intent Creation (payload validation, branch prefix `I-{timestamp}-{slug}/_`, questions validation under
       Bean 0085, `intents.jsonl` logging).
     - Stage 2: Plan Generation (branch prefix `.../P-{timestamp}-{agent}-{model}/_`, markdown plan template, metric
       extraction, EV scoring, `plans.jsonl` logging).
     - Stage 3: Plan Execution (branch prefix `.../E-{timestamp}-{action}/_`, rebase discipline, test execution,
       `executions.jsonl` logging).
     - Stage 4: PR Review Loop (diff summary extraction, mergeability checks via `gh` or `git merge-tree`, Bean 0034
       human halt guarantee).
     - Stage 5: Post-Execution Calibration (Bean 0038 calibration analysis, `/calibrated` branch report, estimator
       updates).
   - Document all CLI flags: `--from-stage`, `--checkpoint`, `--agent`, `--model`, `--dry-run`, `--json`, `--repo-dir`,
     `--skip-push`, `--skip-review`, `--skip-calibrate`, `--max-review-iterations`, `--token-reduce`, `--mitm-web`.
   - Document checkpoint mechanics: atomic write replacement, path convention (`.holon/flow/checkpoint-{slug}.json`),
     resumption workflows.
   - Document PR review loop and Bean 0034 safety invariant: autonomous merge to `main` is strictly forbidden.
   - Document developer usage patterns, common failure modes, and recovery commands.
2. Create `apps/sandbox-executor/README.md`:
   - Document package architecture and submodules (`flow.py`, `cli.py`, `calibration.py`, `agent_runner.py`,
     `ledger.py`, `token_reduction/`).
   - Detail CLI subcommands: `flow`, `plan`, `execute`, `calibrate`, `ledger query`.
   - Provide development workflows and testing instructions (`uv run pytest`, `uv run task check`).

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §1 (Documentation integrity), `holon-config/world/ruleset.md` §2 (Documentation
  standards).
- **Potential failure modes for this step:** Prettier formatting inconsistencies across markdown files.
- **Guardrails and early‑abort checks:** Format documents with code spans for arithmetic expressions; run Prettier
  convergence checks.

### Success & Discard Criteria

- **Success:** Both markdown documents are well-structured, comprehensive, and pass prettier lint checks.
- **Discard:** Discard if documentation omits any of the 5 new flags or contradicts Bean 0034/0038 behaviors.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.20  |
| impact_pred         | 90.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 4.0   |
| ev_pred             | 88.64 |

### Step Metrics Rationale

Calculated via: `EV = 0.98 * 90.0 + 0.5 * 4.0 - 0.3 * 0.20 - 1.5 = 88.20 + 2.00 - 0.06 - 1.5 = 88.64`. Low cost, minimal
entropy documentation creation.

---

## Step 4: Root README.md Flow Documentation & Workflow Diagrams (`README.md`)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Documentation update to root `README.md` with zero runtime risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT
- **Hypothesis being tested:** N/A (EXPLOIT)
- **Learning target:** N/A (EXPLOIT)
- **Maximum acceptable cost for this learning:** N/A

### Intent & Git Integration

- **Step Intent:** Document `holon flow` in root `README.md` with lifecycle flowchart, runner flag reference, and safety
  boundaries.
- **Git branch:**
  `I-1791881582-holon-flow-runner-flags-and-docs/P-1791882348-antigravity-agent-gemini-3.8-flash-medium/E-4-root-readme-update/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `README.md`:
   - Under Developer Workflows § "Automated Lifecycle Orchestration (`holon flow`)":
     - Add a Mermaid flowchart visualizing the 5-stage lifecycle: Stage 1 (Intent) -> Stage 2 (Plan) -> Stage 3
       (Execute) -> Stage 4 (Review) -> Stage 5 (Calibrate).
     - Illustrate the conditional branch bypasses for `--skip-review` and `--skip-calibrate`.
     - Illustrate the Bean 0034 human halt boundary where review consensus halts for operator review without merging to
       main.
     - Add an options reference table for `holon flow` documenting all arguments and flags.
     - Add usage examples for fresh execution, checkpoint resumption, and automated token reduction.
     - Provide cross-references to `.agents/holon_flow.md` and `apps/sandbox-executor/README.md`.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §1 (Documentation integrity), `holon-config/world/ruleset.md` §2.
- **Potential failure modes for this step:** Formatting inconsistencies with existing README structure.
- **Guardrails and early‑abort checks:** Preserve existing sections and comments; run Prettier convergence checks.

### Success & Discard Criteria

- **Success:** Root `README.md` includes clear `holon flow` documentation and passes Prettier check.
- **Discard:** Discard if changes alter unrelated documentation sections.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.20  |
| impact_pred         | 88.0  |
| cost_pred           | 1.2   |
| learning_value_pred | 4.0   |
| ev_pred             | 86.98 |

### Step Metrics Rationale

Calculated via: `EV = 0.98 * 88.0 + 0.5 * 4.0 - 0.3 * 0.20 - 1.2 = 86.24 + 2.00 - 0.06 - 1.2 = 86.98`. Low cost, minimal
entropy documentation update.

---

## Step 5: Hermetic CLI Runner Unit & Integration Test Suite (`tests/test_cli.py` & `tests/test_flow.py`)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Hermetic test suite added to `apps/sandbox-executor/tests/` to guarantee 100% test pass rate.
- **Step Type:** TEST
- **Exploration level:** BALANCED
- **Hypothesis being tested:** Comprehensive hermetic unit and CLI tests covering all new flags, flag combinations,
  checkpoint state preservation, and JSON outputs ensure zero regressions.
- **Learning target:** Validate mocking strategies for `--mitm-web` and `--token-reduce` sidecar setups during automated
  flow testing.
- **Maximum acceptable cost for this learning:** 2.2

### Intent & Git Integration

- **Step Intent:** Add comprehensive test coverage in `test_cli.py` and `test_flow.py` for all 5 new flags and engine
  plumbing.
- **Git branch:**
  `I-1791881582-holon-flow-runner-flags-and-docs/P-1791882348-antigravity-agent-gemini-3.8-flash-medium/E-5-cli-test-coverage/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_flow.py`:
   - In `TestFlowDataModels`:
     - Test `FlowContext` serialization and deserialization with `skip_review`, `skip_calibrate`,
       `max_review_iterations`, `mitm_web`.
     - Test that loading a checkpoint file lacking new keys safely defaults to standard values.
   - In `TestPipelineEngine`:
     - Test `--skip-review`: verify Stage 4 is marked as `SKIPPED`, its payload records skipped status, and execution
       transitions directly to Stage 5.
     - Test `--skip-calibrate`: verify Stage 5 is marked as `SKIPPED`.
     - Test combined `--skip-review` and `--skip-calibrate`: verify both stages are marked `SKIPPED`.
     - Test that review loop iterations record `max_review_iterations`.
     - Test that when review halts for human (`HALTED_FOR_HUMAN`), Stage 5 is skipped if `skip_calibrate=True`.
2. In `apps/sandbox-executor/tests/test_cli.py`:
   - Add test class `TestHolonFlowCLI`:
     - Test CLI argument parsing for `flow` with `--skip-review`.
     - Test CLI argument parsing with `--skip-calibrate`.
     - Test CLI argument parsing with `--max-review-iterations 5`.
     - Test CLI argument parsing with `--token-reduce` and `--mitm-web`.
     - Test combined flag parsing (`--dry-run --skip-review --skip-calibrate --token-reduce`).
     - Test `--json` output contains skipped stage results and custom options.
     - Test CLI error exit codes on failed stages vs exit code 0 on success or human halt.
3. Run full workspace test suite (`uv run pytest`) and lint suite (`uv run task check`) to ensure 100% pass rate.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3, Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (Testing constraints: pytest==9.1.1, hermetic tests),
  `docs/safety.md` §2.
- **Potential failure modes for this step:** Tests depending on live docker daemon or modifying workspace state outside
  `tmp_path`.
- **Guardrails and early‑abort checks:** Use `tmp_path` and `unittest.mock`; avoid live subprocess or network calls.

### Success & Discard Criteria

- **Success:** 100% test pass rate across new and existing tests; zero lint/formatting errors.
- **Discard:** Discard if tests fail or if test fixtures mutate real repository files.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.40  |
| impact_pred         | 94.0  |
| cost_pred           | 2.2   |
| learning_value_pred | 5.0   |
| ev_pred             | 89.48 |

### Step Metrics Rationale

Calculated via: `EV = 0.95 * 94.0 + 0.5 * 5.0 - 0.3 * 0.40 - 2.2 = 89.30 + 2.50 - 0.12 - 2.2 = 89.48`. Comprehensive
testing delivering robust verification and preventing regressions.
