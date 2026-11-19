# Plan for I-1787928927-token-reduction-phase4

- **Plan ID:** P-1787928955-antigravity-agent-gemini-3.5-flash
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.5-flash (version: 1.1.22)
- **Created At:** 2026-08-28T14:55:55.896Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with a focus on implementing AST parsing and Ringer Architect/Executor subagent
  orchestration to reduce token overhead.
- **Safety priority level:** standard
- **Priority Justification:** This intent implements core utility features (AST indexing, memory DB, and subagent
  routing) entirely within sandboxed environments without requiring network access or altering root system invariants.

## Exploration

- **Proportion of steps that are exploratory:** 0.20
- **Justification:** The Ringer subagent delegation flow requires exploratory prototyping (Step 3) to test model
  response alignment and performance trade-offs under the new routing scheme.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 3.5   |
| impact_pred         | 75.0  |
| cost_pred           | 21.0  |
| learning_value_pred | 6.5   |
| ev_pred             | 44.95 |

### Strategy Rationale

The overall plan metrics were derived as follows:

- **p_success_pred**: 0.85. Sourced from the bottleneck Step 3 (Ringer delegation flow), which introduces the most
  complexity and coordination overhead between subagents.
- **entropy_pred**: 3.5. Set to the maximum step entropy (Step 3), reflecting the risks associated with multi-agent
  coordination.
- **impact_pred**: 75.0. Represents the peak expected impact of implementing a full token-reduction suite including AST
  parsing, episodic memory, and structural delegation.
- **cost_pred**: 21.0. Calculated as the sum of cost estimates for all individual steps (5.0 + 4.0 + 7.0 + 2.0 + 3.0).
- **learning_value_pred**: 6.5. Reflects the peak learning target reached across steps, plus integration value for
  Ringer orchestration.
- **ev_pred**: 44.95. Computed using the system-wide EV formula
  `EV = p_success_pred * impact_pred + μ * learning_value_pred - λ * entropy_pred - cost_pred` with `λ = 0.3` and
  `μ = 0.5` (`0.85 * 75.0 + 0.5 * 6.5 - 0.3 * 3.5 - 21.0 = 44.95`).

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` (Git Flow & Branch Constraints, Ledger Immutability, Sandbox Containment Tiers)
  - `holon-config/world/ruleset.md` (Python Runtime, Coding Conventions, Testing Constraints)
  - `docs/safety.md` (Sandboxing, Trust Levels, Entropy Budgets)
- **Potential violations or edge cases:**
  - Subagent routing logic bypasses the process/container sandbox containment checks or tries to execute unsafely.
  - AST parser encounters malformed python syntax and crashes during workspace indexing.
- **Mitigations built into the plan:**
  - Robust exception handling in AST parser to log parsing errors without halting the indexing process.
  - Enforced sandbox policies for all spawned Ringer subagents, strictly inheriting standard sandbox settings.
- **Residual risk accepted (and why):**
  - Minimal risk in episodic storage serialization; standard file locks are used to mitigate potential concurrent write
    issues.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 10.3
- **Budget Compliance:** The strategy fits within budget (Predicted Plan Entropy of 10.3 < 15.0 allocated).

## Plan Description & Strategy

This plan implements Phase 4 of the Token Reduction strategy by introducing targeted context retrieval mechanisms and
structured subagent delegation. Step 1 establishes the AST codebase symbol indexer to parse Python source code, extract
function/class symbols, and construct a symbol reference database (`symbol_index.json`). This enables agents to query
and retrieve only necessary code blocks, reducing input token size. Step 2 implements the OpenBrain episodic memory
database (`episodic_memory.jsonl`), storing execution logs, decisions, and trajectories as lessons to be retrieved
during planning. Step 3 builds the Ringer Architect/Executor subagent orchestration flow, separating implementation
blueprint design from execution, which lowers context length per agent run. Step 4 updates all developer documentation
and configurations to reference these features. Step 5 implements unit and integration tests to validate AST indexing
accuracy, database operations, and Ringer flow stability.

---

## Step 1: Implement AST Codebase Symbol Indexer

- **Sub‑intent recommendation:** NO
- **Reasoning:** Isolated utility class that parses ASTs and serializes metadata. Low risk and does not require a
  separate evaluation cycle.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Create `ast_indexer.py` to parse Python workspace files recursively, extract class/function symbols,
  locations, and docstrings, and serialize to `symbol_index.json`. Provide query functions.
- **Git branch:** `I-1787928927-token-reduction-phase4/step1-ast-symbol-indexer`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `apps/sandbox-executor/src/sandbox_executor/ast_indexer.py`.
- Implement recursive directory scanning for Python files inside `apps/sandbox-executor/src`.
- Use the standard Python `ast` library to parse each file into an AST tree.
- Extract details for all classes and functions: symbol name, type (class/function/method), arguments, docstring,
  starting line, ending line, and file path.
- Save the structured index data to `/home/holon/.holon-sandbox/workspace/holon-knowledge/kb/symbol_index.json`.
- Implement a retrieval API in `ast_indexer.py` containing:
  - `get_symbol_code(symbol_name: str) -> str`: parses the source file and extracts lines in the range
    `[start_line, end_line]`.
  - `search_symbols(query: str) -> list`: fuzzy/substring match on symbol name or docstring.
  - `rebuild_index()`: trigger a full workspace re-scan.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` (Coding Conventions), `docs/safety.md` (Sandboxing)
- **Potential failure modes for this step:**
  - Indexer crashes when encountering non-UTF-8 or syntactically invalid Python files.
  - Large files consume excessive CPU during parsing, violating sandbox resource limits.
- **Guardrails and early‑abort checks:**
  - Wrap file reading and parsing in `try-except` blocks. Skip files that throw `UnicodeDecodeError` or `SyntaxError`
    and log warning messages.
  - Enforce a timeout check inside directory walk to prevent infinite loops.

### Success & Discard Criteria

- Success: `ast_indexer.py` compiles, produces a valid `symbol_index.json` representing all Python files in the source
  tree, and queries return the correct line ranges.
- Discard: Discard if the AST parsing consumes more than 4GB RAM or halts sandbox container.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 2.5   |
| impact_pred         | 65.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 56.05 |

### Step Metrics Rationale

High success probability as the standard AST library is stable. Medium entropy due to filesystem scanning and
serialization tasks.

---

## Step 2: Implement OpenBrain Episodic Memory DB

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard database access library. Standard data storage rules apply, so no separate sub-intent is
  required.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Create `openbrain_db.py` to store agent execution trajectories and episodic history under
  `holon-knowledge/openbrain/episodic_memory.jsonl`.
- **Git branch:** `I-1787928927-token-reduction-phase4/step2-openbrain-memory-db`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `apps/sandbox-executor/src/sandbox_executor/openbrain_db.py`.
- Define an episodic memory schema storing: `timestamp`, `intent_id`, `plan_id`, `agent_role`, `trajectory` (list of
  tool calls, inputs, outputs), `outcome` (success/failure), and `tags` (key technologies or failure types).
- Implement an append-only writer that writes memory records as JSONL lines to
  `/home/holon/.holon-sandbox/workspace/holon-knowledge/openbrain/episodic_memory.jsonl`.
- Implement a retrieval API in `openbrain_db.py`:
  - `query_memories_by_tag(tag: str) -> list`
  - `query_memories_by_intent(intent_id: str) -> list`
  - `retrieve_relevant_experiences(current_goal: str) -> list` (fuzzy matches keywords in current goal against
    historical memories to retrieve lessons learned).

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` (Ledger Immutability)
- **Potential failure modes for this step:**
  - Writing malformed JSON structures to the episodic memory DB.
  - Concurrency issues when multiple subagents attempt to append to the log.
- **Guardrails and early‑abort checks:**
  - Validate episodic memory entries against a JSON schema prior to append.
  - Implement a basic file lock mechanism for file writes to prevent write corruption.

### Success & Discard Criteria

- Success: `openbrain_db.py` can append new episodic memories and correctly filter logs by tag or intent ID.
- Discard: Discard if database appends cause write lock deadlocks or schema violations.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 2.0   |
| impact_pred         | 60.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 4.5   |
| ev_pred             | 51.65 |

### Step Metrics Rationale

Simple database wrapper with low complexity. High success probability and provides stable utility for memory retrieval.

---

## Step 3: Implement Ringer Architect/Executor Subagent Delegation Flow

- **Sub‑intent recommendation:** YES
- **Reasoning:** Large and complex feature altering the routing and subagent planning pipeline. High risk of multi-agent
  coordination issues makes this ideal for a sub-intent.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Separating high-level architectural blueprint design (Architect subagent) from
  implementation execution (Executor subagent) reduces context length and lowers total token consumption per agent call.
- **Learning target:** Measure token consumption reduction and agent execution alignment when delegating through Ringer
  roles vs single-agent execution.
- **Maximum acceptable cost for this learning:** 7.0 (cost_pred)

### Intent & Git Integration

- **Step Intent:** Implement Ringer Architect/Executor agent orchestration logic in the planner/executor router, and
  write prompt templates.
- **Git branch:** `I-1787928927-token-reduction-phase4/step3-ringer-delegation`
- **Sub‑intent:** NEW

### Implementation Details (No code blocks, only logic/steps)

- Create prompt templates `/home/holon/.holon-sandbox/workspace/holon-config/prompts/architect.template.md` and
  `/home/holon/.holon-sandbox/workspace/holon-config/prompts/executor.template.md`.
  - The Architect template prompts the agent to analyze requirements and output a detailed step-by-step implementation
    blueprint.
  - The Executor template prompts the agent to take the blueprint and run shell commands or make file edits to complete
    it.
- Create `/home/holon/.holon-sandbox/workspace/apps/sandbox-executor/src/sandbox_executor/ringer_orchestrator.py` to
  manage the multi-agent execution loop.
- In `ringer_orchestrator.py`, invoke the Architect subagent using `invoke_subagent` and wait for the blueprint output.
- Log the generated blueprint to the episodic memory store (OpenBrain).
- Invoke the Executor subagent with the blueprint as input context, instructing it to apply modifications to the
  codebase.
- Return the final execution status to the main orchestrator.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` (Trust Levels, Sandbox Selection Policy), `holon-config/world/constraints.md`
  (Model Routing Constraints)
- **Potential failure modes for this step:**
  - Executor subagent deviates from the Architect blueprint or fails to follow safety constraints inside the sandbox.
  - Prompt structure increases agent confusion, leading to infinite delegation loops.
- **Guardrails and early‑abort checks:**
  - Enforce a strict max subagent spawn limit (e.g. depth limit of 3) to prevent runaway recursive calls.
  - Validate that the Executor subagent does not have permission to modify files outside the sandbox scope.

### Success & Discard Criteria

- Success: The Architect subagent successfully outputs a valid implementation blueprint, and the Executor subagent
  executes it, passing all validations.
- Discard: Discard if the multi-agent execution increases token consumption compared to a single-agent baseline, or if
  subagents enter infinite communication loops.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 3.5   |
| impact_pred         | 75.0  |
| cost_pred           | 7.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 58.7  |

### Step Metrics Rationale

High impact step. Slightly lower success probability and higher entropy due to the complexity of orchestrating multiple
subagent interactions and token usage measurements.

---

## Step 4: Update Documentation and Config Files

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard documentation and configuration change.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update Developer Guides and Agent Architecture documentation with AST indexer, OpenBrain, and Ringer
  details.
- **Git branch:** `I-1787928927-token-reduction-phase4/step4-docs-config`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Open `docs/architecture.md` and insert descriptions of the AST indexer, OpenBrain memory DB, and Ringer subagent
  delegation flow.
- Open `docs/agents.md` and add details about the Architect and Executor roles.
- Create `/home/holon/.holon-sandbox/workspace/holon-config/schemas/episodic_memory.schema.json` to define validation
  schemas for episodic memory DB records.
- Ensure that the new modules conform to static verification configurations.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** None
- **Potential failure modes for this step:**
  - Documentation structure becomes cluttered or misaligned with actual implementation.
- **Guardrails and early‑abort checks:**
  - Verify that links between markdown files are valid and all paths exist.

### Success & Discard Criteria

- Success: All modified documents render correctly and accurately document the new subagent delegation flow, memory
  schema, and AST symbol parser.
- Discard: None.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.8   |
| impact_pred         | 40.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 37.96 |

### Step Metrics Rationale

Very low risk and cost. Standard documentation task.

---

## Step 5: Implement Unit and Integration Tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard test implementation following coding standards.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add test files verifying AST indexing logic, episodic database read/write queries, and mock Ringer
  orchestration.
- **Git branch:** `I-1787928927-token-reduction-phase4/step5-testing-validation`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `apps/sandbox-executor/tests/test_ast_indexer.py`. Add tests verifying recursive walk, AST symbol extraction
  (class name, functions, docstrings, line numbers), and retrieval APIs.
- Create `apps/sandbox-executor/tests/test_openbrain_db.py`. Add tests for appending records, reading files, and
  searching memories using schema validation.
- Create `apps/sandbox-executor/tests/test_ringer_orchestrator.py`. Mock subagent calls and assert that Architect
  returns a plan and Executor runs the mock plan.
- Execute tests using `uv run pytest apps/sandbox-executor/tests/` and confirm coverage is above 85% for new files.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` (Testing Constraints)
- **Potential failure modes for this step:**
  - Test files write permanent mock files to the host or fail to clean up testing directories.
- **Guardrails and early‑abort checks:**
  - Use `pytest` tmpdir fixtures to isolate test file creation and cleanup automatically.

### Success & Discard Criteria

- Success: All test cases pass successfully.
- Discard: Discard if integration tests fail or cannot mock the subagent interface reliably.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.5   |
| impact_pred         | 50.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 45.55 |

### Step Metrics Rationale

Standard verification step. High probability of success and low entropy, ensuring code quality before merge.
