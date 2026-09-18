# Plan for I-1787928862-token-reduction-phase2

- **Plan ID:** P-1787928877-antigravity-agent-gemini-3.5-flash
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.5-flash (version: 1.1.22)
- **Created At:** 2026-08-28T14:54:38.037Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with 1–2 sentence justification. While the core request of deduplicating JSON
  messages is straightforward, finding the optimal heuristics for automatic Anthropic cache control breakpoint injection
  requires minor exploration of the API limits and conversation density.
- **Safety priority level:** standard
- **Priority Justification:** The intent only involves writing clean helper functions and unit tests within the sandbox
  executor, posing no risk to external systems or database structures.

## Exploration

- **Proportion of steps that are exploratory:** 0.25
- **Justification:** Step 2 involves exploration/experimentation to determine the best heuristics for automatic
  breakpoint injection.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 7.7   |
| impact_pred         | 65.0  |
| cost_pred           | 18.0  |
| learning_value_pred | 4.5   |
| ev_pred             | 35.24 |

### Strategy Rationale

The overall plan metrics were derived from the individual step-level metrics as follows:

- **p_success_pred:** 0.82. This is the product of the probabilities of success of all steps: 0.98 _ 0.90 _ 0.95 \* 0.98
  = 0.82. The implementation of the core deduplication and cache control logic in Step 2 acts as the bottleneck.
- **entropy_pred:** 7.7. Aggregated as the sum of predicted step entropies: 1.0 + 3.5 + 2.0 + 1.2 = 7.7.
- **impact_pred:** 65.0. Taken as the maximum impact of the steps, since the primary benefit is realized by the
  implementation of the core cleaning tool.
- **cost_pred:** 18.0. Aggregated as the sum of individual step costs: 2.0 + 8.0 + 5.0 + 3.0 = 18.0.
- **learning_value_pred:** 4.5. Represents the combined epistemic value of implementing the cache breakpoint heuristics
  and documenting edge case patterns.
- **ev_pred:** 35.24. Calculated directly using the overall metrics under EV physics: EV = 0.82 _ 65.0 + 0.5 _ 4.5 - 0.3
  \* 7.7 - 18.0 = 35.24.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` (Commit boundaries and prefix-based branch isolation)
  - `holon-config/world/ruleset.md` (Strict Python version check, typing guidelines, and imports discipline)
- **Potential violations or edge cases:**
  - Standard library compatibility when working with nested JSON list and dict structures.
  - Sub-process isolation constraints under the process sandbox limit.
- **Mitigations built into the plan:**
  - Comprehensive unit testing of the context cleaner with complex nested message history examples.
  - Using standard library json, hashing, and regex capabilities to avoid introducing complex external dependencies.
- **Residual risk accepted (and why):** None. The design is self-contained and operates purely as a CPU-bound data
  parsing utility.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 7.7
- **Budget Compliance:** The strategy fits within budget.

## Plan Description & Strategy

This plan specifies the implementation of a reusable `ContextCleaner` utility to optimize context windows and prompt
caching. The first phase consists of gathering specifications for Anthropic's Messages API context formatting. The
second phase builds the core utility, parsing the messages list to identify duplicate tool results and applying prompt
caching tags. The third phase adds a robust test suite, and the fourth phase exposes the utility in the sandbox executor
namespace.

---

## Step 1: Research, Design, and Interface Definition

- **Sub‑intent recommendation:** NO
- **Reasoning:** Simple documentation and API contract design step with low complexity and zero code modifications.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Gather requirements and design the class interface for `ContextCleaner`.
- **Git branch:** `I-1787928862-token-reduction-phase2/step1-design`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Review the Anthropic API documentation on prompt caching structure (e.g. cache_control: {"type": "ephemeral"}).
- Design the class `JSONContextCleaner` and its public API: `deduplicate_tool_outputs`, `inject_cache_breakpoints`, and
  `clean_context`.
- Draft a markdown design specification containing sample inputs and output payloads showing redacted duplicate outputs
  and injected cache tags.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md`, `holon-config/world/ruleset.md`
- **Potential failure modes for this step:**
  - Misaligned interface design that doesn't map directly to the API format of the agents.
- **Guardrails and early‑abort checks:**
  - Ensure the designed interface handles both simple string content and structured message content blocks.

### Success & Discard Criteria

- **Success:** Design document and interface specification written and approved.
- **Discard:** Rerouting needed if Anthropic API requirements have changed.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 1.0   |
| impact_pred         | 20.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 18.05 |

### Step Metrics Rationale

This step is a low-entropy documentation task. The probability of success is extremely high (0.98) due to the presence
of complete API reference documents.

---

## Step 2: Implement ContextCleaner Core Logic

- **Sub‑intent recommendation:** YES
- **Reasoning:** Contains the core algorithmic logic of the intent and carries the highest implementation risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Dynamic cache control placement at strategic message indices significantly improves
  caching hit rates without breaking the API schema.
- **Learning target:** Finding the optimal heuristic to distribute the maximum of 4 cache breakpoints in a multi-turn
  conversation.
- **Maximum acceptable cost for this learning:** 8.0 units (cost_pred)

### Intent & Git Integration

- **Step Intent:** Write the code for `JSONContextCleaner` in
  `apps/sandbox-executor/src/sandbox_executor/context_cleaner.py`.
- **Git branch:** `I-1787928862-token-reduction-phase2/step2-implementation`
- **Sub‑intent:** NEW

### Implementation Details (No code blocks, only logic/steps)

- Create `apps/sandbox-executor/src/sandbox_executor/context_cleaner.py`.
- Implement `deduplicate_tool_outputs` which hashes tool result blocks and replaces repeating outputs with a reference
  placeholder.
- Implement `inject_cache_breakpoints` which iterates over message content blocks (specifically looking at the last few
  user messages) and appends cache_control annotations.
- Enforce strict type annotations and docstrings for all helper and public methods.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` (Coding Conventions & Standards), `docs/safety.md`
- **Potential failure modes for this step:**
  - Recursion errors or key errors when parsing heavily nested block structures.
- **Guardrails and early‑abort checks:**
  - Wrap JSON traversal code in defensive type checks to ensure it does not crash on malformed JSON messages.

### Success & Discard Criteria

- **Success:** Code files created, compiling without syntax errors, and conforming to style guidelines.
- **Discard:** Discard if code requires non-standard library packages not listed in `pyproject.toml`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 3.5   |
| impact_pred         | 65.0  |
| cost_pred           | 8.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 51.45 |

### Step Metrics Rationale

Step 2 is the core implementation phase, carrying standard coding risks (p_success=0.90, entropy=3.5). The learning
value is relatively high (4.0) because of the dynamic cache annotation heuristic.

---

## Step 3: Implement Unit Tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard test suite implementation with low complexity and high reusability.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Implement comprehensive unit tests in `apps/sandbox-executor/tests/test_context_cleaner.py`.
- **Git branch:** `I-1787928862-token-reduction-phase2/step3-testing`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `apps/sandbox-executor/tests/test_context_cleaner.py`.
- Write unit tests using pytest verifying deduplication correctness.
- Write tests confirming correct placement and formatting of cache control headers on the Anthropic message structure.
- Run the test suite within the process sandbox to verify correctness.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` (Testing Constraints)
- **Potential failure modes for this step:**
  - Failing to cover edge cases, leading to false negatives during actual execution runs.
- **Guardrails and early‑abort checks:**
  - Include tests that run mock empty, string-based, and malformed lists through the cleaner.

### Success & Discard Criteria

- **Success:** All unit tests pass successfully under `pytest` with coverage above 90%.
- **Discard:** Discard and revise if tests find core structural design flaws in the cleaner.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 2.0   |
| impact_pred         | 45.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 38.15 |

### Step Metrics Rationale

Standard testing steps have high success likelihood (0.95) and moderate entropy (2.0) as they only add test files.

---

## Step 4: System Integration & Documentation

- **Sub‑intent recommendation:** NO
- **Reasoning:** Simple integration and documentation step with low risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Expose the `JSONContextCleaner` in the package entry point and update documentation.
- **Git branch:** `I-1787928862-token-reduction-phase2/step4-integration`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Expose the cleaner class inside `apps/sandbox-executor/src/sandbox_executor/__init__.py`.
- Document usage instructions in `README.md` or a dedicated architecture markdown file.
- Perform a final run of all sandbox-executor tests to ensure no regressions were introduced.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md`
- **Potential failure modes for this step:**
  - Cyclic imports or namespace collision within the sandbox-executor workspace.
- **Guardrails and early‑abort checks:**
  - Perform static analysis checks (e.g. pylint or mypy) if configured.

### Success & Discard Criteria

- **Success:** Package exports configured cleanly and tests pass.
- **Discard:** Abort if integration introduces regressions to executor or planner.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 1.2   |
| impact_pred         | 30.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 26.54 |

### Step Metrics Rationale

Simple formatting and exposure step. High success rate (0.98) and very low entropy (1.2).

---
