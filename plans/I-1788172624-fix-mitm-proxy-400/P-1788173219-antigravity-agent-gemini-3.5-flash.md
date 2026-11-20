# Plan for I-1788172624-fix-mitm-proxy-400

- **Plan ID:** P-1788173219-antigravity-agent-gemini-3.5-flash
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.5-flash (version: 1.1.22)
- **Created At:** 2026-08-31T10:46:59.680Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** conservative with a focus on narrowing Gemini provider detection and adding payload key guards
  to prevent unhandled TypeError exceptions in the proxy.
- **Safety priority level:** standard
- **Priority Justification:** The plan involves standard string matching and dictionary checking in Python files within
  the container/process sandbox, without network calls or external modifications.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The task uses standard, deterministic coding practices (string matching, None checks) and unit
  testing, requiring no exploratory spikes.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 2.0   |
| impact_pred         | 85.0  |
| cost_pred           | 10.0  |
| learning_value_pred | 3.0   |
| ev_pred             | 67.4  |

### Strategy Rationale

The overall plan metrics were derived as follows:

- **p_success_pred**: 0.90. Qualitatively estimated from the high success probability of individual steps (Step 1 and
  Step 2 are 0.95+), since the implementation has no complex dependency cycles.
- **entropy_pred**: 2.0. The maximum step-level entropy is 1.5 (Steps 1 & 3). A small buffer of 0.5 is added to account
  for potential integration test anomalies.
- **impact_pred**: 85.0. High impact, as it prevents unhandled TypeError exceptions in the MITM proxy for any non-Gemini
  Google API call, ensuring caller agents can query services like userinfo or loadCodeAssist unhindered.
- **cost_pred**: 10.0. Sum of the cost predictions of all steps (3.0 + 2.0 + 3.0 + 2.0).
- **learning_value_pred**: 3.0. Moderate learning value, representing basic defensive coding patterns and test coverage.
- **ev_pred**: 67.4. Calculated using the formula `EV = P(success)*Impact + mu*LearningValue - lambda*Entropy - Cost`
  with `λ = 0.3` and `μ = 0.5`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` (Git Flow & Branch Constraints, Sandbox Containment Tiers)
  - `holon-config/world/ruleset.md` (Coding Conventions, Testing Constraints)
  - `docs/safety.md` (Sandboxing, Trust Levels, Entropy Budgets)
- **Potential violations or edge cases:**
  - Standard JSON decoding or key lookup exceptions in `mitm_addon.py` when processing non-standard responses from
    unexpected providers.
  - None-type values on payload fields when reading incoming requests.
- **Mitigations built into the plan:**
  - Strong check in the Gemini payload cleaner (`_clean_gemini`) to verify if the contents list exists and is list-typed
    before attempting processing.
- **Residual risk accepted (and why):**
  - None. The scope of changes is very narrow and fully contained within unit test coverage.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 5.0
- **Budget Compliance:** The strategy fits within budget (Predicted Plan Entropy of 5.0 is well below the allocated
  budget of 15.0).

## Plan Description & Strategy

This plan resolves the HTTP 400 Bad Request issues on generic googleapis.com endpoints by updating the MITM proxy
interceptor. In Step 1, we restrict the Gemini provider URL heuristic in `detect_provider` inside `mitm_addon.py` to
specifically look for `generativelanguage.googleapis.com` instead of matching all `googleapis.com` domains. This ensures
generic endpoints are classified as `unknown` and bypassed safely. In Step 2, we introduce a guard clause in
`JSONContextCleaner._clean_gemini` inside `payload_cleaner.py` to prevent TypeError exceptions when the `contents` key
is missing or is not a list. In Step 3, we add unit tests inside `test_token_reduction.py` to verify both of these
behaviors under mock scenarios. In Step 4, we run the test suite to confirm that everything compiles and passes cleanly.

---

## Step 1: Modify Gemini Provider Detection in MITM Addon

- **Sub‑intent recommendation:** NO
- **Reasoning:** Simple modification of string matching logic inside a single file with low risk.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Narrow the Gemini provider URL heuristic detection in `mitm_addon.py` to match
  `generativelanguage.googleapis.com`.
- **Git branch:** `I-1788172624-fix-mitm-proxy-400/step1-gemini-url-heuristic`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Open `apps/sandbox-executor/src/sandbox_executor/token_reduction/mitm_addon.py`.
- Locate `detect_provider` method.
- Update the check for gemini URL from matching `googleapis.com` or `gemini` generally to require
  `generativelanguage.googleapis.com` specifically.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` (Coding Conventions)
- **Potential failure modes for this step:** Incorrect string match causing valid Gemini calls to return `unknown`.
- **Guardrails and early‑abort checks:** Ensure that URLs containing `generativelanguage.googleapis.com` are still
  correctly mapped to `gemini`.

### Success & Discard Criteria

- **Success:** Gemini provider is correctly identified for `generativelanguage.googleapis.com` endpoints but returned as
  `unknown` for other Google endpoints.
- **Discard:** Discard if the change breaks basic provider routing logic.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.5   |
| impact_pred         | 80.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 73.55 |

### Step Metrics Rationale

This step is a straightforward modification of a string detection helper, which has a very high success rate and low
entropy.

---

## Step 2: Implement Missing Key Guard in Gemini Payload Cleaner

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard defensive coding guard addition to a single method.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add guard to `_clean_gemini` to gracefully handle payloads that lack a `contents` key or have a
  non-list `contents` key.
- **Git branch:** `I-1788172624-fix-mitm-proxy-400/step2-cleaner-contents-guard`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Open `apps/sandbox-executor/src/sandbox_executor/token_reduction/payload_cleaner.py`.
- Locate `_clean_gemini` method.
- Add a guard clause at the start of the method checking if `contents` is `None` or not an instance of `list`.
- Return `payload` unchanged if the check is true.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` (Coding Conventions)
- **Potential failure modes for this step:** Over-aggressive guarding returning unmodified payloads for valid requests.
- **Guardrails and early‑abort checks:** The guard is conditional only on the absence or invalid type of `contents` key,
  which is safe.

### Success & Discard Criteria

- **Success:** Payloads without a `contents` list are bypassed without triggering any TypeError exceptions.
- **Discard:** Discard if the guard clause triggers for valid Gemini request payloads.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 1.0   |
| impact_pred         | 80.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 77.1  |

### Step Metrics Rationale

Adding a type and existence check has extremely low entropy and high success probability.

---

## Step 3: Add Unit Tests for Generic googleapis.com and Missing Keys

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard unit testing to verify the changes.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Create test cases validating that generic googleapis.com URLs are unaffected and missing keys are
  handled gracefully.
- **Git branch:** `I-1788172624-fix-mitm-proxy-400/step3-verification-tests`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Open `apps/sandbox-executor/tests/test_token_reduction.py`.
- Define a new test function `test_mitm_interceptor_generic_googleapis_unaffected`.
- Assert that generic Google APIs like `loadCodeAssist`, `setUserSettings`, `userinfo`, and `play log` return
  `"unknown"` from `detect_provider`.
- Assert that `JSONContextCleaner` processes a payload lacking `contents` key without mutating it or throwing
  exceptions.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` (Testing Constraints)
- **Potential failure modes for this step:** Broken test logic or mocks failing to isolate the behavior correctly.
- **Guardrails and early‑abort checks:** Use simple, self-contained assertions.

### Success & Discard Criteria

- **Success:** Test suite passes locally when asserting the new behavior.
- **Discard:** Discard if the test mocks break existing interceptor tests.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.5   |
| impact_pred         | 75.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 69.3  |

### Step Metrics Rationale

Writing unit tests is a standard, low-risk process with predictable outcomes.

---

## Step 4: Run Tests to Verify Compliance

- **Sub‑intent recommendation:** NO
- **Reasoning:** Verification step via test runner.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Run pytest to verify all unit tests pass.
- **Git branch:** `I-1788172624-fix-mitm-proxy-400/step4-verify-tests`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Navigate to `apps/sandbox-executor`.
- Execute test runner (e.g. `pytest`) to verify all tests, including the new assertions, pass successfully.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` (Testing Constraints)
- **Potential failure modes for this step:** Test suite execution failure due to external dependencies or environment
  issues.
- **Guardrails and early‑abort checks:** Run only local unit tests.

### Success & Discard Criteria

- **Success:** All test files run and report 100% success.
- **Discard:** Discard if unrelated test failures block verification.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.0   |
| impact_pred         | 70.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 65.2  |

### Step Metrics Rationale

Running tests is a read-only process with high success probability and extremely low entropy.
