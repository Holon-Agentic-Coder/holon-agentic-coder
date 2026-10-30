# Sandbox Planning Architecture Specification

This document defines the extended **Sandbox Planning** architecture for the Holon Agentic Coder, as aligned during
design reviews.

---

## 1. Security & Sandbox Escape Auditing

To prevent execution-time sandbox violations or malicious shell commands, the planning phase includes a pre-execution
audit gate:

- **The Security Auditor Agent:** Once a plan variant is proposed, a specialized Security Auditor Agent runs inside the
  sandbox environment.
- **Audit Scope:** The agent parses all proposed steps, dependencies, and shell commands, comparing them against the
  rules defined in `holon-config/rules/sandbox_policy.json` and the safety guidelines in `docs/safety.md`.
- **Action:** Plans with security policy violations are rejected immediately, and the planner must regenerate the
  variant.

---

## 2. Clean Architecture & Design Conventions

To ensure all proposed code changes conform to the project’s structure and boundaries:

- **Contextual Blueprints:** The system parses architectural boundaries and dependency constraints from
  `holon-config/world/ruleset.md` and active folder hierarchies.
- **Prompt Injection:** These boundaries are injected directly into the planner's prompt template so that the agent has
  explicit, real-time visibility into the code structure rules before writing the plan steps.

---

## 3. Dependency & Bottleneck Risk Mitigation

Bottleneck steps (e.g., database schema changes, core module refactoring) carry high risk:

- **Decomposition Guidance:** A step marked as a critical bottleneck (`Is Bottleneck: YES`) with a low predicted success
  probability (`P(success) < 0.7`) is a candidate for the plan's sub-intent list; the engine never blocks on it.
- **Decomposition via the Sub-intent List:** A bottleneck step is not decomposed automatically. To split it out, the
  planner lists it as an item of the plan's explicit `## Sub-intent list` (see §4); the flow then runs that item as a
  child intent with its own branch, sandbox and tests, nested under, cut from and merged automatically into the plan
  branch that listed it, before that plan's work is raised to its own target (see §4 for the current implementation).

---

## 4. Entropy Budgeting & Compliance

Entropy represents the disorder and complexity introduced by plans:

- **Step-Level Partitioning:** The planner distributes the overall intent's entropy budget across individual steps.
- **Hard Compliance Gate:** If the cumulative predicted step entropy exceeds the intent's allocated budget, the plan
  variant is flagged as non-compliant. The planner is forced to either reframe the strategy or decompose the intent
  further.
- **One Budget, Shown and Persisted:** The planner entrypoint resolves the budget once with
  `budgets.resolve_entropy_budget` (the intent's `entropy_budget`, else `DEFAULT_ENTROPY_BUDGET`, `15.0`), shows it to
  the agent as `{budget_from_intent}` and writes it as `entropy_budget` on the plan's `plans.jsonl` row. The executor
  enforces exactly that value.
- **Explicit Sub-intent List:** A plan that cannot fit the budget decomposes by adding a level-2 heading
  `## Sub-intent list` followed by top-level bullet or numbered items, one concrete child intent per item (indented
  nested bullets are details of the item above, never children of their own). The flow runs each item sequentially
  through the full pipeline as a child intent cut from and targeting the deciding plan branch (its `deciding_branch`);
  any level may decide a split, but in practice the plan does. Until the implementation follows, the current flow still
  cuts each child from and targets the parent intent branch and leaves each child PR merge to the maintainer (see
  [Git Flow](../git_flow.md) Stage 3 under the flow equivalence guarantee). Per-step sub‑intent recommendations alone
  never trigger decomposition, and the executor never fabricates child work.
- **Bounded Re-planning:** A plan above its budget without that list is sent back to the planner with a "Re-plan
  request" paragraph (passed through `HOLON_REPLAN_FEEDBACK`, never written to a ledger), at most `MAX_REPLAN_ATTEMPTS`
  (`2`) times, after which the Execute stage fails with a clear message.

---

## 5. Peer Review & Plan Selection

Once the competitive planner generates candidate variants:

- **Convergence Phase:** The orchestrator applies a mathematical convergence policy based on Expected Value (EV)
  improvement rates and variant limits.
- **The Evaluator Agent:** Once converged, a separate **Evaluator Agent** reviews the top candidate plan files. The
  Evaluator performs a final review of architectural alignment, security profiles, and cost metrics before selecting and
  locking in the winning plan.

---

## 6. Knowledge & Ruleset Consultation Gate

To avoid repeating past failures and ensure strict compliance with project governance:

- **Ledger Verification:** Before planning, the planner queries the Ledger for similar intents that failed. It
  identifies their failure modes to avoid repeating identical implementation mistakes.
- **KB & WB Retrieval:** The planner retrieves proven patterns and tactics from the Knowledge Base (KB) and universal
  reasoning invariants from the Wisdom Base (WB) to bootstrap the plan structure.
- **World Ruleset Compliance:** The planner must explicitly match the proposed steps against rules in
  `holon-config/world/ruleset.md` and `holon-config/world/constraints.md` to ensure language versions, dependency rules,
  and git flow restrictions are strictly obeyed.

---

## 7. Conditional Spike & Hypothesis Execution

For steps characterized by high uncertainty but high potential epistemic gain:

- **Spike Selection:** If a plan step has a low success probability ($P(success) < 0.6$) but high learning value
  ($LearningValue > 7.0$), the orchestrator conditionally spins up a **Spike / Hypothesis Test**.
- **Sandbox VM Isolation:** The spike runs in a highly isolated, ephemeral container/VM sandbox with zero network or
  host access, dedicated solely to testing the hypothesis (e.g., calling a mock API or running a prototype script).
- **Feedback Loop:** The results of the spike test are written to the ledger, and the main planner uses this new
  diagnostic data to refine the metrics and steps of the final plan before selection.

---

## 8. Continuous Calibration & Estimator Tuning

To ensure that predicted metrics ($P(success)$, $\Delta S$, $Cost$, $LearningValue$) remain calibrated:

- **Tuning Agent Execution:** A background **Calibration & Estimator Tuning Agent** regularly analyzes forensic records
  in the Ledger.
- **Error Minimization:** It compares predicted values to actual measured metrics post-execution and calculates
  calibration errors.
- **Weight Proposals:** If systematic bias is detected, the agent adaptively calculates optimal formula weights and
  proposes estimator revisions to `holon-config/metrics/` (subject to human approval), ensuring the decision engine
  remains calibrated over time.

---

## 9. Planning Agent Directory: Crucial vs. Optional Tiers

This section outlines the role and operational requirement tier for each specialized agent in the planning lifecycle.

### A. Crucial Agents (Mandatory Core Execution)

These agents must execute for every intent-planning lifecycle to enforce baseline safety, design, and mathematical
correctness:

1. **Planner Agent:**
   - **Role:** Generates candidate plan graphs with step-by-step metrics predictions.
   - **Status:** **CRUCIAL**.
2. **Evaluator Agent:**
   - **Role:** Applies the EV convergence policy, peer-reviews top plan files, and locks the winning plan branch.
   - **Status:** **CRUCIAL**.
3. **Security Auditor Agent:**
   - **Role:** Performs sandbox static audits and command verification against sandbox policies prior to execution.
   - **Status:** **CRUCIAL**.
4. **Calibration & Estimator Tuning Agent:**
   - **Role:** Analyzes ledger errors post-execution and proposes calibrated formula weights to keep EV calculations
     accurate.
   - **Status:** **CRUCIAL**.

### B. Optional Agents (Pluggable / Context-Specific)

These agents are invoked dynamically depending on the task's complexity, cost, uncertainty, or concurrency level:

1. **Conflict Predictor Agent:**
   - **Role:** Analyzes parallel git branch structures and file edit history to estimate conflict likelihood (CL) and
     sequence sibling tasks.
   - **Status:** **OPTIONAL** (Triggered during concurrent multi-agent executions).
2. **KB Integration & Template Seeding Agent:**
   - **Role:** Queries the Knowledge Base for similar past plans and inserts templates/failure mitigations into the
     prompt.
   - **Status:** **OPTIONAL** (Triggered if the intent matches active tags in the KB index).
3. **Spike / Hypothesis Tester Agent:**
   - **Role:** Executes rapid, isolated VM tests for uncertain but high-value steps to verify implementation
     feasibility.
   - **Status:** **OPTIONAL** (Triggered conditionally if step success rate is low and learning value is high).
4. **Complexity Routing Agent:**
   - **Role:** Pre-evaluates the intent text and workspace context to select the appropriate reasoning models (
     Flash/Medium/Deep).
   - **Status:** **OPTIONAL** (Invoked at the beginning of the planning lifecycle to optimise model costs).
5. **Resource & Cost Optimisation Agent (Budget Agent):**
   - **Role:** Forecasts API token cost and sandbox VM computation times to ensure plan complies with the project's
     financial budget.
   - **Status:** **OPTIONAL** (Triggered for high-complexity/deep-model routing plans).
6. **Rollback & Resilience Agent (Disaster Planner):**
   - **Role:** Mandates and verifies the insertion of rollback/cleanup steps for any plan steps that alter schema or
     write to database state.
   - **Status:** **OPTIONAL** (Triggered for plans that involve stateful modifications).
7. **Context Window Optimiser Agent (Attention Agent):**
   - **Role:** Prunes irrelevant codebase source files and documentation from the prompt context.
   - **Status:** **OPTIONAL** (Triggered for larger codebase scopes to control prompt sizes).
8. **Dependency & Licence Auditor Agent (Compliance Officer):**
   - **Role:** Audits proposed third-party packages for copyleft conflicts (e.g. GPL compatibility) and CVE safety.
   - **Status:** **OPTIONAL** (Triggered when the plan modifies `pyproject.toml` or environment lockfiles).
9. **Test Specification Generator Agent (QA Architect):**
   - **Role:** Independently outlines edge cases, test structures, and validation scripts for the plan.
   - **Status:** **OPTIONAL** (Triggered for high-novelty tasks or where test coverage is a critical metric).
10. **Documentation & Impact Alignment Agent (PR Writer):**
    - **Role:** Drafts pull request descriptions, release notes, and documentation updates mapping the plan's outcome to
      business metrics.
    - **Status:** **OPTIONAL** (Triggered for root intents merging into the canonical base branch).

---

## 10. References & Requirements

For details on the API key and credential configuration required to run each supported agent in the sandbox planning
phase, see the [Agent Credentials & API Key Requirements](agent_credentials_requirements.md).
