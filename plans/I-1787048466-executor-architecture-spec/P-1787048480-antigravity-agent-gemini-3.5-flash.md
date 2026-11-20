# Plan for I-1787048466-executor-architecture-spec

**Plan ID:** P-1787048480-antigravity-agent-gemini-3.5-flash **Parent Intent ID:** NONE **Agent:**
antigravity-agent/gemini-3.5-flash **Created At:** 2026-08-18T10:21:20.254Z

## Planner Autonomy Summary

- Intent handling: ACCEPT_AS_IS
- Reframed intent (if applicable): NONE
- Exploration stance: conservative with a 1–2 sentence justification: This is a documentation-only intent focusing on
  codifying existing sandbox execution patterns. A conservative approach is chosen as there are no unknown codebase
  modifications, minimizing entropy and cost.
- Safety priority level: standard
- Priority Justification: As a pure documentation task, there are no execution activities or code changes, meaning it
  carries minimal safety risks under `docs/safety.md` and `holon-config/world/constraints.md`.

## Exploration

- Proportion of steps that are exploratory: 0.0
- Justification: All steps are straightforward documentation tasks mapping existing logic from the codebase into
  specifications, requiring zero exploratory steps.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 3.2   |
| impact_pred         | 45.0  |
| cost_pred           | 10.0  |
| learning_value_pred | 2.0   |
| ev_pred             | 32.79 |

### Strategy Rationale

The overall plan metrics were derived as follows:

- **p_success_pred**: 0.95. This is calculated as the joint probability of success of all steps (0.98 _ 0.98 _ 0.98 =
  0.941, rounded to 0.95), reflecting the sequential dependency of the documentation tasks.
- **entropy_pred**: 3.2. This is the sum of the step-level predicted entropies (1.2 + 1.2 + 0.8 = 3.2), which represents
  the total potential complexity introduced across all branch activities.
- **impact_pred**: 45.0. This is the maximum impact of the individual steps (max(45.0, 40.0, 20.0)), since the primary
  value of the intent is determined by the most critical architectural specification document (Step 1).
- **cost_pred**: 10.0. This is the sum of individual step costs (4.0 + 4.0 + 2.0 = 10.0), representing total token and
  time cost.
- **learning_value_pred**: 2.0. This is a qualitative aggregate representation of the epistemic gain, since the steps
  are non-exploratory and document existing logic.
- **ev_pred**: 32.79. Calculated directly using the overall metrics under the standard formula: EV = 0.95 _ 45.0 + 0.5 _
  2.0 - 0.3 \* 3.2 - 10.0 = 32.79.

The strategy is direct implementation (EXPLOIT) of documentation steps. Since the functionality is already implemented
in `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py` and
`apps/sandbox-executor/src/sandbox_executor/agent_runner.py`, we do not need exploratory spikes. There is no risk of
escape or failure in these documentation steps, making success highly probable.

## Safety & Constraint Alignment

- Key world ruleset constraints that affect this plan:
  - `holon-config/world/constraints.md` (Git Flow & Branch Constraints)
  - `docs/safety.md` (Specifically Git isolation and human review gates for parent intents)
- Potential violations or edge cases:
  - Attempting to modify code files instead of only creating/editing the specified documentation files.
  - Using incorrect path formatting in the documentation that doesn't resolve.
- Mitigations built into the plan:
  - The plan is restricted solely to editing documentation files
    (`docs/executor/execution_architecture_specification.md`, `docs/executor/agent_credentials_requirements.md`, and
    `docs/sandbox/execute_plan.md`). No Python or shell code changes are proposed.
- Residual risk accepted (and why): Minimal formatting discrepancy or dead links, which will be easily caught by
  manual/visual inspection.
- Allocated Entropy Budget: 15.0
- Predicted Plan Entropy: 3.2
- Budget Compliance: The strategy fits within budget.

## Plan Description & Strategy

This plan specifies the creation of two new documentation files under `docs/executor/` and the update of an existing
document in `docs/sandbox/`. The goals of these updates are to align design specifications for the executor role with
the current implemented state in the codebase, detailing its architecture, lifecycle, credentials mapping, and safety
features.

---

## Step 1: Write Sandbox Executor Architecture Specification

**Sub‑intent recommendation:** NO **Reasoning:** The step is low risk and documentation-focused, presenting minimal
complexity and no direct code changes, so it does not require a separate sub-intent. **Step Type:** DOCUMENTATION
**Exploration level:** EXPLOIT

### Intent & Git Integration

**Step Intent:** Create the execution architecture specification document at
`docs/executor/execution_architecture_specification.md`. **Git branch:**
I-1787048466-executor-architecture-spec/P-1787048480-antigravity-agent-gemini-3.5-flash/E-write-exec-spec **Sub‑intent**
NONE

### Implementation Details (No code blocks, only logic/steps)

- Create the target directory docs/executor/ if it does not exist.
- Document the high-level architecture of the Sandbox Executor, including its relationship with the Orchestrator,
  Router, and Sandboxes (Process, Container, VM).
- Document the step-by-step execution lifecycle flow: cloning/fetching, repo cleaning, checkout of execution branch,
  plan and intent reading, decomposition check, agent runner building, execution, log capturing, git commit/push, and
  cleanup.
- Document the path-traversal safety check mechanism (\_check_forbidden_root) and its exact path constraints
  (ALLOWED_PARENTS, ALLOWED_EXACT).
- Document the secret redaction mechanism (SECRET_FLAGS, command line argument filtering, and git diff masking).
- Document the workspace retention policy (HOLON_KEEP_WORKSPACE) and its behavior in container/local environments.

### Dependencies & Criticality

**Depends on:** NONE **Is Bottleneck:** YES

### Safety & Constraint Considerations

- Relevant rules: docs/safety.md#sandboxing-model, holon-config/world/constraints.md#2-sandbox-containment-tiers
- Potential failure modes for this step:
  - Omitting critical path-traversal details.
  - Misrepresenting the role-dispatching logic.
- Guardrails and early‑abort checks:
  - Cross-check document content against actual code in
    apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py.

### Success & Discard Criteria

**Success:** File docs/executor/execution_architecture_specification.md is created and conforms to clean architecture
specs. **Discard:** Threshold where cost exceeds 1.5x cost_pred.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 1.2   |
| impact_pred         | 45.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 40.24 |

### Step Metrics Rationale

The documentation is direct and well-defined by the current implementation in
apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py, resulting in a very high probability of success and
low entropy.

---

## Step 2: Write Sandbox Executor Agent Credentials & API Key Requirements

**Sub‑intent recommendation:** NO **Reasoning:** This step is low-risk and involves documenting credentials mapping
logic that is already implemented, so it does not require a sub-intent. **Step Type:** DOCUMENTATION **Exploration
level:** EXPLOIT

### Intent & Git Integration

**Step Intent:** Create the agent credentials requirements document at
`docs/executor/agent_credentials_requirements.md`. **Git branch:**
I-1787048466-executor-architecture-spec/P-1787048480-antigravity-agent-gemini-3.5-flash/E-write-credentials-spec
**Sub‑intent** NONE

### Implementation Details (No code blocks, only logic/steps)

- Document the 3-Tier Fallback Contract for sandbox credential resolution: Tier 1 (Ephemeral Secret Bundle at
  HOLON_SECRET_BUNDLE_PATH / /run/secrets/holon_auth.json), Tier 2 (Environment variables mapping to HOLON_AGENT_KEY or
  HOLON_AGENT_OSS_MODE), and Tier 3 (Session directory mounts).
- Document the exact mapping and requirements for all supported agents (pi, open-codex, claude, gemini, opencode, codex,
  antigravity).
- Detail the path traversal protection applied when unpacking files from the secret bundle.

### Dependencies & Criticality

**Depends on:** NONE **Is Bottleneck:** NO

### Safety & Constraint Considerations

- Relevant rules: docs/safety.md#trust-model, docs/planner/agent_credentials_requirements.md
- Potential failure modes for this step:
  - Misdocumenting the session directories or mapping variables.
- Guardrails and early‑abort checks:
  - Cross-check against actual mapping code in apps/sandbox-executor/src/sandbox_executor/agent_runner.py.

### Success & Discard Criteria

**Success:** File docs/executor/agent_credentials_requirements.md is created and correctly details all agent
integrations. **Discard:** Threshold where cost exceeds 1.5x cost_pred.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 1.2   |
| impact_pred         | 40.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 35.34 |

### Step Metrics Rationale

The credentials mapping logic is fully established in the codebase, leading to a straightforward documentation step with
high predictability.

---

## Step 3: Update Guide for Running Execution via Docker

**Sub‑intent recommendation:** NO **Reasoning:** This is a minor modification of an existing guide to link it with the
new specifications, which represents very low risk. **Step Type:** DOCUMENTATION **Exploration level:** EXPLOIT

### Intent & Git Integration

**Step Intent:** Update `docs/sandbox/execute_plan.md` to cross-reference the new specifications and explain the
execution environment role. **Git branch:**
I-1787048466-executor-architecture-spec/P-1787048480-antigravity-agent-gemini-3.5-flash/E-update-exec-guide
**Sub‑intent** NONE

### Implementation Details (No code blocks, only logic/steps)

- Open docs/sandbox/execute_plan.md.
- Add references to docs/executor/execution_architecture_specification.md and
  docs/executor/agent_credentials_requirements.md in appropriate sections.
- Elaborate on how `./holon` host CLI maps the 3-Tier Fallback Contract.

### Dependencies & Criticality

**Depends on:** Step 1, Step 2 **Is Bottleneck:** NO

### Safety & Constraint Considerations

- Relevant rules: docs/sandbox/execute_plan.md
- Potential failure modes for this step:
  - Breaking existing links or formatting in execute_plan.md.
- Guardrails and early‑abort checks:
  - Verify all links remain valid.

### Success & Discard Criteria

**Success:** docs/sandbox/execute_plan.md updated with correct links and additional context. **Discard:** Threshold
where cost exceeds 1.5x cost_pred.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.8   |
| impact_pred         | 20.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 17.61 |

### Step Metrics Rationale

Minor link and content updates to an existing markdown file are extremely safe and cheap to perform.

---
