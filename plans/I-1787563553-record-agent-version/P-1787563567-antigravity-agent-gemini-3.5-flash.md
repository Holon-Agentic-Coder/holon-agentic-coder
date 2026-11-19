# Plan for I-1787563553-record-agent-version

- **Plan ID:** P-1787563567-antigravity-agent-gemini-3.5-flash
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.5-flash
- **Created At:** 2026-08-24T09:26:07.120Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** conservative with 1–2 sentence justification. A conservative stance is chosen because this
  task involves modifying critical logging pathways and agent infrastructure modules where predictability, stability,
  and exact conformance to local coding standards are paramount.
- **Safety priority level:** standard
- **Priority Justification:** This task does not access external network APIs, introduce new library dependencies, or
  modify core security/containment features in the world ruleset.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** All implementation, logging, and documentation steps use well-understood processes and
  deterministic mechanisms.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 2.0   |
| impact_pred         | 60.0  |
| cost_pred           | 8.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 49.9  |

### Strategy Rationale

The overall plan metrics were derived as follows:

- **p_success_pred**: 0.95. The tasks are straightforward, using localized python wrappers and test suites. The risk of
  breaking changes is low.
- **entropy_pred**: 2.0. Derived as the maximum of step-level entropies (with a peak of 1.5 in Step 2). The overall
  complexity is very small, easily fitting within the 4.0 entropy budget.
- **impact_pred**: 60.0. The auditability of agent executions in plans and executions ledger files is significantly
  improved.
- **cost_pred**: 8.5. Computed as the sum of cost estimates for all individual steps (2.0 + 3.0 + 1.5 + 2.0).
- **learning_value_pred**: 3.0. Represents the peak learning target reached across steps (mostly routine tasks, but
  introduces a clean introspection pattern).
- **ev_pred**: 49.9. Derived using the standard formula
  `EV = p_success_pred * impact_pred + μ * learning_value_pred - λ * entropy_pred - cost_pred` with default weights
  `λ = 0.3` and `μ = 0.5` (`0.95 * 60.0 + 0.5 * 3.0 - 0.3 * 2.0 - 8.5 = 49.9`).

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` (Git Flow & Branch Constraints, Ledger Immutability)
  - `holon-config/world/ruleset.md` (Python Runtime, Coding Conventions, Testing Constraints)
- **Potential violations or edge cases:**
  - Raising `FileNotFoundError` or blocking indefinitely when launching subprocesses to query version numbers in
    environments where CLI binaries are not installed.
  - Adding version fields to `plans.jsonl` or `executions.jsonl` that fail ledger validation schemas.
- **Mitigations built into the plan:**
  - Timeout boundaries (2.0s) and try-catch blocks around the subprocess invocation.
  - Dockerfile-aligned fallback version dictionary to guarantee a default output version for each supported agent.
- **Residual risk accepted (and why):**
  - None. Subprocess executions are fully constrained and do not require network permissions or unsafe capabilities.
- **Allocated Entropy Budget:** 4.0
- **Predicted Plan Entropy:** 1.5
- **Budget Compliance:** The strategy fits within the budget (Predicted Plan Entropy of 1.5 < 4.0 allocated).

## Plan Description & Strategy

This plan resolves the intent of implementing agent versioning across the Holon sandbox execution environment. In Step
1, we implement agent CLI version resolution inside the `AgentRunner` module. In Step 2, we integrate this lookup into
`planner.py` and `executor.py` to record `agent_version` fields in the plans/executions ledgers and output records. In
Step 3, we update all templates and architectural documentation. In Step 4, we write new tests and run validation
verification.

---

## Step 1: Implement Agent CLI Version Resolution in AgentRunner

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, low-complexity python module change that does not introduce significant risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add `get_version()` helper method to standard agent runner wrappers to query active CLI versions.
- **Git branch:** `I-1787563553-record-agent-version/step1-implement-version-resolution`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Open `apps/sandbox-executor/src/sandbox_executor/agent_runner.py`.
- Define a base method `get_version(self) -> str` in the `AgentRunner` class.
- Add `get_version(self) -> str` implementation in the `StandardAgentRunner` class.
- Implement caching using a private variable `self._resolved_version`. If cached, return it.
- Invoke the agent's CLI binary in a subprocess with `--version`, `-v`, or `version` arguments.
- Use a regular expression `r"(\d+\.\d+\.\d+)"` to search the command stdout or stderr and extract the version string.
- Define a fallback version lookup map using the versions declared in `apps/sandbox-executor/Dockerfile` (e.g. `pi` ->
  `0.80.3`, `open-codex` -> `0.1.31`, `claude` -> `2.1.202`, `gemini` -> `0.49.0`, `opencode` -> `1.17.14`, `codex` ->
  `0.142.5`, `antigravity` -> `1.0.0`) to handle instances where the binary is not present or execution fails.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` (Coding Conventions)
- **Potential failure modes for this step:**
  - Subprocess invocation hangs or blocks execution indefinitely.
  - Subprocess throws `FileNotFoundError` if the binary does not exist on the current host.
- **Guardrails and early‑abort checks:**
  - Apply a strict timeout of 2.0 seconds on all subprocess calls.
  - Wrap the subprocess logic in try/except blocks to return the fallback version on any exception.

### Success & Discard Criteria

- **Success:** `get_version()` returns a valid version string (either resolved dynamically or via fallback) for all
  supported agents.
- **Discard:** Discard if subprocess execution behaves unpredictably or introduces memory leaks.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 1.2   |
| impact_pred         | 45.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 42.74 |

### Step Metrics Rationale

This step has a very high success probability and low entropy because it adds isolated, safe helper code with robust
fallbacks.

---

## Step 2: Record `agent_version` in Ledger Logging and Markdown Generation

- **Sub‑intent recommendation:** NO
- **Reasoning:** Directly integrates the Step 1 version lookup into existing log scripts.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add `agent_version` to plans.jsonl and executions.jsonl ledger payloads and markdown records.
- **Git branch:** `I-1787563553-record-agent-version/step2-ledger-markdown-logging`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Open `apps/sandbox-executor/src/sandbox_executor/entrypoint/planner.py`.
- Call `runner.get_version()` and add `"agent_version"` key to the `plan_entry` dictionary payload appended to
  `plans.jsonl`.
- Add `"{agent_version}": runner.get_version()` to the replacements dictionary in `planner.py` to allow template
  injection.
- Open `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`.
- Call `runner.get_version()` and insert `"agent_version"` key in the standard execution and decomposed execution entry
  payloads appended to `executions.jsonl`.
- Update the execution markdown log generator block in `executor.py` to write `- Agent Version: {agent_version}` under
  the agent name header.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` (Ledger Immutability)
- **Potential failure modes for this step:**
  - Appending malformed or empty payloads to ledger files.
- **Guardrails and early‑abort checks:**
  - Verify that the version string is a valid non-empty string before formatting.

### Success & Discard Criteria

- **Success:** Ledger files contain the correct `agent_version` field, and output markdown files are populated with the
  version.
- **Discard:** Discard if ledger operations fail or write invalid lines.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 1.5   |
| impact_pred         | 50.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 45.3  |

### Step Metrics Rationale

Modifies standard logging code, which slightly increases entropy risk but delivers the core impact of the intent.

---

## Step 3: Update Plan Markdown Templates and Documentation

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard documentation change without code modification.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add `agent_version` references to markdown templates, ledger specifications, and developer guides.
- **Git branch:** `I-1787563553-record-agent-version/step3-update-docs`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Open `holon-config/prompts/planner.template.md` and append `(version: {agent_version})` or add
  `- **Agent Version:** {agent_version}` to the top metadata fields list.
- Open `docs/ledger_schema.md` and document the presence of `agent_version` in the ledger schemas for both plan
  selection and execution entries.
- Open `docs/agents.md` and add information regarding version resolution mechanisms in the Executor and Planner runner
  workflows.
- Open `docs/architecture.md` to add description of how CLI versioning fits into the stateless engine and validation
  cycle.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** None
- **Potential failure modes for this step:**
  - Documentation links or headers breaking or diverging from code realities.
- **Guardrails and early‑abort checks:**
  - Perform visual review of modified documentation structure.

### Success & Discard Criteria

- **Success:** All modified documents compile and accurately describe the new behavior.
- **Discard:** Discard if documentation changes introduce confusion or violate formatting rules.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.8   |
| impact_pred         | 35.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 33.91 |

### Step Metrics Rationale

Extremely safe documentation-only step with negligible entropy.

---

## Step 4: Update and Add Unit Tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Basic unit test writing and test suite run.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Write and verify unit test suites to ensure version parsing works correctly under different
  conditions.
- **Git branch:** `I-1787563553-record-agent-version/step4-update-tests`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Open `apps/sandbox-executor/tests/test_agent_runner.py`.
- Implement `test_get_version_success` to mock successful execution of binary `--version` and assert that the output
  regex correctly extracts versions (e.g. `1.2.3`).
- Implement `test_get_version_fallback` to mock command execution errors and assert that the hardcoded
  Dockerfile-aligned fallback maps are returned correctly.
- Open `apps/sandbox-executor/tests/test_planner.py` and `apps/sandbox-executor/tests/test_executor.py` and update any
  MagicMock expectations for runners to stub `get_version` (e.g., set `mock_runner.get_version.return_value = "1.0.0"`).
- Run the full test suite using
  `PYTHONPATH=apps/sandbox-executor/src python3 -m unittest discover -s apps/sandbox-executor/tests` to verify
  everything compiles and passes.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` (Testing Constraints)
- **Potential failure modes for this step:**
  - Mock assertions breaking other tests in the file.
- **Guardrails and early‑abort checks:**
  - Isolate mocks to test scope, and clean up patches on tearDown.

### Success & Discard Criteria

- **Success:** Test execution returns 100% success for all 55+ test cases.
- **Discard:** Discard if tests fail or cannot be run cleanly.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 1.0   |
| impact_pred         | 40.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 37.5  |

### Step Metrics Rationale

High confidence testing step. Mock assertions verify code behavior without running live container operations.
