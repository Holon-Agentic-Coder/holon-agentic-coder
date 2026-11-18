# Plan for I-1794182541-unique-plan-and-execution-ids

- **Plan ID:** P-1794182557-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.5.3)
- **Created At:** 2026-11-09T00:02:38.625Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None
- **Exploration stance:** balanced with focus on deterministic collision elimination, regex backward compatibility, and
  strict file-size guardrails.
- **Safety priority level:** standard
- **Priority Justification:** All proposed modifications operate strictly within the internal stage orchestration, ID
  construction, and ledger validation routines of `apps/sandbox-executor/` without modifying network boundaries, host
  execution policies, or repository security invariants (`docs/safety.md#Sandboxing-model`).

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** Deterministic structural enhancement and regression testing addressing whole-second identifier
  collision vulnerability across concurrent planning and execution dispatches.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 2.80  |
| impact_pred         | 88.0  |
| cost_pred           | 9.50  |
| learning_value_pred | 5.00  |
| ev_pred             | 74.88 |

### Strategy Rationale

The intent is accepted as is (`ACCEPT_AS_IS`). At present, plan identifiers (`P-{timestamp}-{agent}-{model}`) and
execution identifiers (`E-{timestamp}-{agent}-{model}`) rely exclusively on whole-second Unix epoch timestamps without
incorporating parent branch or intent disambiguators. When multiple intents are planned or executed concurrently—or
sequentially within the same clock second—using the same agent and model, they generate identical identifier strings.
This causes file collisions in `plans/P-*.md`, `plans/P-*_calibration.md`, `executions/E-*.md`, and breaks ledger
lookups in `holon-knowledge/ledger/plans.jsonl` and `holon-knowledge/ledger/executions.jsonl`.

The chosen strategy addresses this vulnerability while honouring all downstream regex and structural constraints:

1. **Deterministic Hex Disambiguator Placement:** Rather than appending a random token or changing the suffix, an
   8-character lowercase hexadecimal digest derived deterministically from
   `(parent_branch, time.time_ns(), os.getpid())` is placed between the timestamp sequence and the trailing
   `-{agent}-{safe_model}`:
   - Plan ID format: `P-{plan_seq}-{disambiguator}-{agent_name}-{safe_model}`
   - Execution ID format: `E-{exec_seq}-{disambiguator}-{safe_agent}-{safe_model}` This precisely preserves the trailing
     suffix `-{agent}-{safe_model}` required by branch discovery in `flow.py` (`_discover_child_branch`), matches the
     integer timestamp prefix in `_resumable_partial_branch` (`/E-(\d+)-[^/]+/_$`), and satisfies `_PARTIAL_BRANCH_RE`.
2. **Strict File-Size and Complexity Guardrails:** Anchor files `flow.py` (6,161 lines), `entrypoint/executor.py` (2,240
   lines), and `calibration.py` (1,684 lines) are already well above the 1,000-line review ensemble threshold. All new
   ID construction and ledger validation helper functions are placed in `entrypoint/planner.py` (568 lines) or
   `ledger.py` (869 lines). In `flow.py`, `executor.py`, and `calibration.py`, existing inline string formatting logic
   is replaced by concise imported helper calls, ensuring net line count delta remains `<= 0` and no `# noqa`
   suppressions are introduced.
3. **Comprehensive Ledger Validation & Backward Compatibility:** In `ledger.py`, a validation helper scans ledgers and
   raises `ValueError` if any duplicate `plan_id` or `execution_id` is recorded across distinct intents or parent
   branches. In `calibration.py`, `parse_predicted_metrics` and component parsers are verified to seamlessly resolve
   historical 4-segment IDs already recorded on `main` as well as new 5-segment disambiguated IDs.
4. **Dedicated Test Suite:** All new verification tests are placed in a dedicated file
   `apps/sandbox-executor/tests/test_unique_stage_ids.py` to prevent bloat in existing test modules.

Overall plan metrics are derived as follows:

- **Success Probability (`p_success_pred = 0.94`):** Governed by the bottleneck verification step, Step 4
  (`p_success_pred = 0.94`), which exercises full end-to-end integration, branch resolution, and historical backward
  compatibility.
- **Plan Entropy (`entropy_pred = 2.80`):** Derived as the direct sum of step-level entropies:
  `Delta_S = 0.60 + 0.80 + 0.60 + 0.80 = 2.80`. This consumes only a small fraction of the allocated `15.0` entropy
  budget.
- **Impact (`impact_pred = 88.0`):** Permanently eliminates concurrent planning and execution collision race conditions
  across the entire system.
- **Cost (`cost_pred = 9.50`):** Derived as the sum of step-level development, refactoring, and test execution costs:
  `Cost = 1.80 + 2.50 + 2.00 + 3.20 = 9.50`.
- **Learning Value (`learning_value_pred = 5.00`):** Delivers reusable stage identifier patterns and validation
  mechanisms applicable to future pipeline enhancements.
- **Expected Value (`ev_pred = 74.88`):** Calculated strictly according to system physics (`lambda = 0.3`, `mu = 0.5`):
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.94 * 88.0 + 0.5 * 5.00 - 0.3 * 2.80 - 9.50 = 82.72 + 2.50 - 0.84 - 9.50 = 74.88`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md#1-git-flow--branch-constraints`: All stage and branch operations strictly adhere
    to branch isolation (`I-.../P-.../E-.../_`). Merges to `main` remain exclusively reserved for human operators.
  - `holon-config/world/constraints.md#2-sandbox-containment-tiers`: Low predicted plan entropy (`2.80 < 10.0`)
    qualifies for execution within the hermetic process sandbox with complete network isolation.
  - `holon-config/world/constraints.md#3-ledger-immutability`: Ledgers (`intents.jsonl`, `plans.jsonl`,
    `executions.jsonl`) remain strictly append-only; historical entries are never modified or purged.
  - `holon-config/world/ruleset.md#1-runtime--environment-specification`: Target Python `==3.13.*` standard library
    (`hashlib`, `time`, `os`, `re`, `json`) without introducing third-party runtime dependencies.
  - `holon-config/world/ruleset.md#2-coding-conventions--standards`: PEP 8 compliance, comprehensive type annotations,
    Australian English spelling conventions, and descriptive docstrings.
  - `holon-config/world/ruleset.md#3-testing-constraints`: All unit tests executed strictly using `uv run pytest`;
    existing test assertions remain intact.
  - File sizing rules: Net line count delta in `flow.py`, `entrypoint/executor.py`, and `calibration.py` must be `<= 0`.
    No inline `# noqa` comments may be added.
- **Potential violations or edge cases:**
  - Existing branch matching regexes breaking if the disambiguator contains dashes or invalid branch characters.
  - Branch discovery failing if the preferred suffix `-{agent}-{model}` is displaced or malformed.
  - Breaking resolution of historical 4-segment IDs in `plans.jsonl` and existing calibration markdown files.
  - Permitting duplicate IDs across distinct intents or falsely rejecting valid retries under the same intent branch.
- **Mitigations built into the plan:**
  - Disambiguator is restricted to an 8-character lowercase hexadecimal string (`[0-9a-f]{8}`), guaranteeing branch name
    safety without illegal characters.
  - Disambiguator is positioned strictly between the numeric sequence and `-{agent}-{model}`, preserving both the
    leading `P-{seq}-` / `E-{seq}-` and the trailing suffix `-{agent}-{model}`.
  - Regexes and parsers in `calibration.py` support both 4-segment legacy identifiers and 5-segment disambiguated
    identifiers.
  - Ledger uniqueness validation specifically checks uniqueness across distinct parent intents/branches, allowing
    legitimate retries or sequential operations within the same branch context.
- **Residual risk accepted (and why):**
  - Negligible probability of SHA-256 8-character prefix collision (`1 / 16^8 ~= 2.3e-10`) across concurrent executions;
    accepted because combined seed includes parent branch, nanosecond timestamp, and process ID.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 2.80
- **Budget Compliance:** The strategy fits comfortably within budget (`2.80 <= 15.0`).

## Plan Description & Strategy

This plan resolves the plan and execution identifier collision vulnerability by introducing a deterministic,
collision-resistant stage disambiguator.

The strategy encompasses four cohesive steps:

1. **Step 1: ID Construction and Ledger Uniqueness Helpers:** Implement `build_stage_disambiguator()`,
   `build_plan_id()`, `build_execution_id()`, and `validate_ledger_unique_ids()` in `entrypoint/planner.py` and
   `ledger.py`.
2. **Step 2: Flow and Entrypoint Integration with Zero Line Growth:** Integrate the helper functions into `planner.py`,
   `executor.py`, and `flow.py` dry-run fallbacks, replacing inline string interpolation and ensuring net line count
   delta `<= 0` in all files over 1,000 lines.
3. **Step 3: Calibration Backward Compatibility and Branch Matching:** Ensure `calibration.py` resolves both legacy
   4-segment identifiers and new 5-segment disambiguated identifiers with zero net line delta.
4. **Step 4: Dedicated Unit Testing and Quality Assurance:** Construct
   `apps/sandbox-executor/tests/test_unique_stage_ids.py` covering disambiguator generation, branch discovery
   compatibility, regex conformance, ledger collision detection, and backward compatibility.

---

## Step 1: Implement Stage ID Builders and Ledger Uniqueness Validation

- **Sub‑intent recommendation:** NO
- **Reasoning:** Core modular logic implemented entirely within existing files that are well below the 1,000-line limit
  (`entrypoint/planner.py` and `ledger.py`).
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add deterministic disambiguator builders and stage ID formatters in `entrypoint/planner.py` and
  ledger uniqueness validation functions in `ledger.py`.
- **Git branch:**
  `I-1794182541-unique-plan-and-execution-ids/P-1794182557-antigravity-agent-gemini-3.8-flash-medium/E-stage-id-builders`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/entrypoint/planner.py`:
   - Define `build_stage_disambiguator(parent_ref: str) -> str`:
     - Normalise `parent_ref` by stripping trailing `/_` and `/`.
     - Seed a SHA-256 hash with `parent_ref`, high-resolution `time.time_ns()`, and `os.getpid()`.
     - Return the first 8 characters of the lowercase hexadecimal digest.
   - Define `build_plan_id(plan_seq: int, parent_branch: str, agent: str, model: str) -> str`:
     - Sanitise `agent` and `model` using existing sanitisation helpers.
     - Obtain `disambiguator = build_stage_disambiguator(parent_branch)`.
     - Return `f"P-{plan_seq}-{disambiguator}-{safe_agent}-{safe_model}"`.
   - Define `build_execution_id(exec_seq: int, parent_branch: str, agent: str, model: str) -> str`:
     - Sanitise `agent` and `model`.
     - Obtain `disambiguator = build_stage_disambiguator(parent_branch)`.
     - Return `f"E-{exec_seq}-{disambiguator}-{safe_agent}-{safe_model}"`.
2. In `apps/sandbox-executor/src/sandbox_executor/ledger.py`:
   - Define
     `validate_ledger_unique_ids(records: Iterable[dict[str, Any]], id_key: str = "plan_id", parent_key: str = "intent_branch") -> None`:
     - Iterate over ledger entries.
     - Track a mapping of seen identifiers to their associated parent branch or intent identifier.
     - If an identifier already exists in the mapping with a differing parent branch or intent, raise `ValueError`
       detailing the duplicate identifier collision across distinct intents.
     - Allow repeated entries if the parent branch or intent matches (supporting idempotent retries or status updates).
   - Define
     `validate_ledger_file_unique_ids(ledger_path: str, id_key: str = "plan_id", parent_key: str = "intent_branch") -> None`:
     - Open `ledger_path` if it exists, parse non-empty lines as JSON, and delegate to `validate_ledger_unique_ids()`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2-coding-conventions--standards`,
  `holon-config/world/constraints.md#3-ledger-immutability`.
- **Potential failure modes for this step:**
  - Non-deterministic characters or uppercase characters in the disambiguator violating git branch naming conventions.
  - False positive validation errors on legitimate retry entries under the same parent intent.
- **Guardrails and early‑abort checks:**
  - Sanitise all inputs, constrain digest strictly to `[0-9a-f]{8}`, and normalise parent branch strings before
    comparison.

### Success & Discard Criteria

- **Success:** Disambiguator generation produces clean 8-character hex strings, ID builders format compliant 5-segment
  identifiers, and ledger validation reliably rejects cross-intent duplicate IDs.
- **Discard:** Discard if SHA-256 hashing introduces performance bottlenecks or fails sanitisation checks.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.60  |
| impact_pred         | 88.0  |
| cost_pred           | 1.80  |
| learning_value_pred | 4.50  |
| ev_pred             | 86.51 |

### Step Metrics Rationale

High probability (`0.98`) and low entropy (`0.60`) as this step introduces purely additive, self-contained utility
functions. `EV = 0.98 * 88.0 + 0.5 * 4.50 - 0.3 * 0.60 - 1.80 = 86.24 + 2.25 - 0.18 - 1.80 = 86.51`.

---

## Step 2: Integrate Disambiguated IDs into Planner, Executor, and Flow Orchestration

- **Sub‑intent recommendation:** NO
- **Reasoning:** Targeted integration replacing string interpolation with helper calls while strictly preserving line
  budgets in files over 1,000 lines.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update `entrypoint/planner.py`, `entrypoint/executor.py`, and `flow.py` dry-run fallbacks to invoke
  the unified ID builders, maintaining net line delta `<= 0` in `executor.py` and `flow.py`.
- **Git branch:**
  `I-1794182541-unique-plan-and-execution-ids/P-1794182557-antigravity-agent-gemini-3.8-flash-medium/E-integrate-stage-ids`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/entrypoint/planner.py`:
   - In `main()`, replace inline `plan_id = f"P-{plan_seq}-{agent_name}-{safe_model}"` and `plan_branch` construction
     with: `plan_id = build_plan_id(plan_seq, intent_branch_prefix, agent_name, safe_model)`
     `plan_branch = f"{intent_branch_prefix}/{plan_id}/_"`
2. In `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`:
   - Import `build_execution_id` from `sandbox_executor.entrypoint.planner`.
   - In `main()`, replace inline `exec_id = f"E-{exec_seq}-{safe_agent}-{safe_model}"` and `exec_branch` construction
     with: `exec_id = build_execution_id(exec_seq, plan_branch_prefix, safe_agent, safe_model)`
     `exec_branch = f"{plan_branch_prefix}/{exec_id}/_"`
   - Ensure the import and invocation are written compactly so the net line count of `executor.py` does not increase
     (`net line delta <= 0`).
   - Confirm that `_PARTIAL_BRANCH_RE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*/E-\d+-[A-Za-z0-9._-]+/_$")`
     naturally accommodates the disambiguator segment within `[A-Za-z0-9._-]+`.
3. In `apps/sandbox-executor/src/sandbox_executor/flow.py`:
   - In `run_plan_stage()`, update the synthetic dry-run fallback (around line 2476) to import and call `build_plan_id`.
   - In `run_execute_stage()`, update the synthetic dry-run fallback (around line 2984) to import and call
     `build_execution_id`.
   - Ensure that `_discover_child_branch` suffix matching (`b.removesuffix("/_").endswith(preferred_suffix)`) and
     `_resumable_partial_branch` sequence extraction (`r"/E-(\d+)-[^/]+/_$"`) continue to operate seamlessly without
     modifying regex logic.
   - Maintain net line delta `<= 0` in `flow.py`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Connects ID generation to actual branch creation and execution dispatch)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md#Git-discipline`, file-size guardrails requiring net line delta `<= 0` on files
  exceeding 1,000 lines.
- **Potential failure modes for this step:**
  - Accidental expansion of `flow.py` or `executor.py` line counts triggering review ensemble size rejections.
  - Suffix mismatch in child branch discovery if the disambiguator is placed in the wrong position.
- **Guardrails and early‑abort checks:**
  - Verify line count with `wc -l` before and after modifications.
  - Verify suffix matching with explicit unit tests.

### Success & Discard Criteria

- **Success:** Dispatched plans and executions receive 5-segment disambiguated identifiers, child branch discovery
  successfully adopts branches, and line counts of `flow.py` and `executor.py` do not increase.
- **Discard:** Discard if branch discovery fails to identify child branches or if file length caps are breached.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.80  |
| impact_pred         | 90.0  |
| cost_pred           | 2.50  |
| learning_value_pred | 5.00  |
| ev_pred             | 85.26 |

### Step Metrics Rationale

High impact (`90.0`) as it modifies live dispatch paths. Controlled entropy (`0.80`) through compact integration.
`EV = 0.95 * 90.0 + 0.5 * 5.00 - 0.3 * 0.80 - 2.50 = 85.50 + 2.50 - 0.24 - 2.50 = 85.26`.

---

## Step 3: Ensure Calibration Backward Compatibility and Branch Matching

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standardisation of branch and ID parsing in `calibration.py` with zero net line delta.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Ensure `parse_predicted_metrics()` and `parse_branch_components()` in `calibration.py` seamlessly
  match both historical 4-segment IDs and new 5-segment IDs across `plans.jsonl` and markdown files.
- **Git branch:**
  `I-1794182541-unique-plan-and-execution-ids/P-1794182557-antigravity-agent-gemini-3.8-flash-medium/E-calibration-stage-ids`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/calibration.py`:
   - Inspect `parse_predicted_metrics(plan_id: str, target_worktree: str = ".")`:
     - Normalise incoming `plan_id` by stripping trailing `/_` and leading path segments if a full branch was provided.
     - In the ledger matching loop (`plans.jsonl`), ensure the matching condition robustly evaluates:
       `record.get("plan_id") == plan_id or record.get("plan_id") == plan_id_segment or plan_id in record.get("plan_branch", "")`
     - Confirm that historical 4-segment IDs (`P-{ts}-{agent}-{model}`) and new 5-segment IDs
       (`P-{ts}-{disambiguator}-{agent}-{model}`) match accurately without ambiguity.
   - Inspect `parse_branch_components()`:
     - Verify that splitting on `/` correctly extracts any segment starting with `P-` into `components["plan_id"]` and
       any segment starting with `E-` into `components["execution_id"]`, regardless of segment count.
   - Consolidate redundant string slicing where possible to ensure net line count delta in `calibration.py` is `<= 0`.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md#Human-review-boundaries`, file-size guardrails for `calibration.py`
  (`<= 1,684 lines`).
- **Potential failure modes for this step:**
  - Failing to match legacy entries in `holon-knowledge/ledger/plans.jsonl` causing calibration fallback to markdown or
    defaulting to zero metrics.
- **Guardrails and early‑abort checks:**
  - Run existing calibration tests against historical ledger entries before committing.

### Success & Discard Criteria

- **Success:** Calibration reports correctly locate predicted metrics for both 4-segment and 5-segment plan identifiers
  from both `plans.jsonl` and markdown artefacts, with net line delta `<= 0` in `calibration.py`.
- **Discard:** Discard if historical ledger matching breaks.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.60  |
| impact_pred         | 85.0  |
| cost_pred           | 2.00  |
| learning_value_pred | 4.50  |
| ev_pred             | 81.67 |

### Step Metrics Rationale

Low entropy (`0.60`) and high success probability (`0.96`) as changes refine existing pattern matching.
`EV = 0.96 * 85.0 + 0.5 * 4.50 - 0.3 * 0.60 - 2.00 = 81.60 + 2.25 - 0.18 - 2.00 = 81.67`.

---

## Step 4: Implement Dedicated Unit Tests in `test_unique_stage_ids.py` and Verify

- **Sub‑intent recommendation:** NO
- **Reasoning:** Dedicated test file implementation isolating all new tests from existing large test suites.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Create `apps/sandbox-executor/tests/test_unique_stage_ids.py` to thoroughly test disambiguator
  generation, branch discovery compatibility, regex conformance, ledger validation, and backward compatibility.
- **Git branch:**
  `I-1794182541-unique-plan-and-execution-ids/P-1794182557-antigravity-agent-gemini-3.8-flash-medium/E-test-unique-stage-ids`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create new test module `apps/sandbox-executor/tests/test_unique_stage_ids.py`:
   - Test `test_disambiguator_uniqueness_across_rapid_invocations()`:
     - Generate multiple disambiguators sequentially with identical parent branch inputs.
     - Assert that all generated disambiguator values are unique and exactly 8 lowercase hexadecimal characters.
   - Test `test_disambiguator_uniqueness_across_different_branches()`:
     - Generate disambiguators with differing parent branches.
     - Assert that generated disambiguators differ.
   - Test `test_stage_id_builders_structure()`:
     - Invoke `build_plan_id` and `build_execution_id`.
     - Assert structure conforms to `P-{ts}-{disambiguator}-{agent}-{model}` and
       `E-{ts}-{disambiguator}-{agent}-{model}`.
     - Assert that trailing `-agent-model` suffix is preserved exactly.
   - Test `test_flow_branch_discovery_suffix_matching()`:
     - Create mock branch names with 5-segment IDs and verify `b.removesuffix("/_").endswith(preferred_suffix)` matches.
     - Test that candidate branches with distinct agents/models are correctly excluded.
   - Test `test_partial_branch_regex_matching()`:
     - Test that `_PARTIAL_BRANCH_RE` matches 5-segment execution branch paths.
     - Test that `_resumable_partial_branch` regex `r"/E-(\d+)-[^/]+/_$"` extracts the integer timestamp sequence
       accurately.
   - Test `test_ledger_unique_ids_validation()`:
     - Assert `validate_ledger_unique_ids` succeeds on ledgers with distinct plan IDs.
     - Assert `validate_ledger_unique_ids` raises `ValueError` when identical `plan_id` or `execution_id` appears under
       distinct `intent_branch` records.
     - Assert `validate_ledger_unique_ids` permits identical IDs under the same `intent_branch` (idempotent retries).
   - Test `test_calibration_backward_compatibility()`:
     - Test `parse_predicted_metrics` resolves both a 4-segment historical record and a 5-segment record from a mock
       `plans.jsonl`.
2. Execute test suites:
   - Run affected tests: `uv run pytest apps/sandbox-executor/tests/test_unique_stage_ids.py`.
   - Run related stage tests:
     `uv run pytest apps/sandbox-executor/tests/test_planner.py apps/sandbox-executor/tests/test_calibration.py`.
   - Run task checks: `uv run task check` (`uv run task lint` and `uv run task lint-docs`).

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** YES (Final gatekeeper verifying all architectural, regex, and ledger guarantees)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3-testing-constraints`, zero harness leaks (no references to
  `.beans/`, `todo/`, `.agents/`, or bean numbers).
- **Potential failure modes for this step:**
  - Mock environment discrepancies failing to simulate realistic git branch hierarchies.
  - Flaky tests caused by insufficient nanosecond clock resolution on certain platforms.
- **Guardrails and early‑abort checks:**
  - Include process ID in disambiguator hash seed to ensure uniqueness even under low timer resolution.

### Success & Discard Criteria

- **Success:** All unit tests in `test_unique_stage_ids.py` pass cleanly, existing tests pass without regressions, task
  check passes, and zero file-size or complexity warnings occur.
- **Discard:** Discard if test failures indicate regex incompatibilities with `flow.py`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.80  |
| impact_pred         | 88.0  |
| cost_pred           | 3.20  |
| learning_value_pred | 5.50  |
| ev_pred             | 82.03 |

### Step Metrics Rationale

Bottleneck verification step. Higher cost (`3.20`) and learning value (`5.50`) reflecting comprehensive verification
across multiple integration points.
`EV = 0.94 * 88.0 + 0.5 * 5.50 - 0.3 * 0.80 - 3.20 = 82.72 + 2.75 - 0.24 - 3.20 = 82.03`.
