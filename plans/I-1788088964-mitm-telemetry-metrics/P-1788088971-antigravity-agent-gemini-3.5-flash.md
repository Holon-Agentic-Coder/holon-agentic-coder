# Plan for I-1788088964-mitm-telemetry-metrics

- **Plan ID:** P-1788088971-antigravity-agent-gemini-3.5-flash
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.5-flash (version: 1.1.22)
- **Created At:** 2026-08-30T11:22:51.644Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** conservative with a focus on implementing robust, well-tested telemetry metrics tracking in
  the MITM proxy without external runtime risk.
- **Safety priority level:** standard
- **Priority Justification:** This intent alters request/response intercept logic inside a standard sandbox without
  making external network calls, using existing file paths and SQLite structures.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The telemetry and caching modifications are standard API metrics enhancements with no high-entropy
  exploration or spike needed.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 3.0   |
| impact_pred         | 80.0  |
| cost_pred           | 13.0  |
| learning_value_pred | 5.0   |
| ev_pred             | 56.6  |

### Strategy Rationale

The overall plan metrics were derived as follows:

- **p_success_pred**: 0.85. Sourced from the bottleneck Step 2 (calculating TPS and headers injection), representing the
  key risk of parsing JSON and handling edge cases safely.
- **entropy_pred**: 3.0. Sourced from the maximum step-level entropy (Step 2), representing the potential complexity of
  parsing various providers' token structures.
- **impact_pred**: 80.0. The implementation of Phase 3 telemetry headers provides strong feedback to callers on cache
  hits, latency, and LLM throughput metrics.
- **cost_pred**: 13.0. Sum of the cost of individual steps (4.0 + 6.0 + 3.0).
- **learning_value_pred**: 5.0. Reflects standard optimization logic and testing pattern setup.
- **ev_pred**: 56.6. Computed as `0.85 * 80.0 + 0.5 * 5.0 - 0.3 * 3.0 - 13.0 = 56.6` with `λ = 0.3` and `μ = 0.5`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` (Git Flow & Branch Constraints, Sandbox Containment Tiers)
  - `holon-config/world/ruleset.md` (Coding Conventions, Testing Constraints)
  - `docs/safety.md` (Sandboxing, Trust Levels, Entropy Budgets)
- **Potential violations or edge cases:**
  - Standard JSON decoding or key lookup exceptions in `mitm_addon.py` when processing non-standard responses from
    unexpected providers.
  - Zero-division exceptions when calculating TPS if latency/time delta is exactly zero.
- **Mitigations built into the plan:**
  - Safe extraction logic utilizing fallback values and robust exception wrapping around payload parsed parameters.
  - Strict conditional guard check on divisor time deltas to prevent division-by-zero errors.
- **Residual risk accepted (and why):**
  - Minor drift in token count estimation when responses lack token metadata; accepted since the fallback word-based
    estimation behaves as a reliable heuristic.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 7.5
- **Budget Compliance:** The strategy fits within budget (Predicted Plan Entropy of 7.5 < 15.0 allocated).

## Plan Description & Strategy

This plan implements Phase 3 telemetry & streaming metrics calculation in `mitm_addon.py`. In Step 1, we set up request
and response header hooks to capture timestamps (`request_start_time`, `response_headers_time`) and declare variables
for tracking hit rate metrics. In Step 2, we implement parsing logic to extract token counts for Anthropic, OpenAI, and
Gemini responses, calculate TTFT, Prefill TPS, Output TPS, Total Time, and Cache Hit Rate, and inject them as headers to
the response. In Step 3, we update pytest unit tests to assert the correct behavior of the telemetry headers.

---

## Step 1: Telemetry Hooks and Hit-Rate State Setup

- **Sub‑intent recommendation:** NO
- **Reasoning:** Basic setup step mapping request timestamps and tracking stats in-memory.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add timestamp recording to request and response-headers stages and initialize in-memory stats in
  `mitm_addon.py`.
- **Git branch:** `I-1788088964-mitm-telemetry-metrics/step1-telemetry-setup`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Modify `MitmproxyAddon.__init__` in `apps/sandbox-executor/src/sandbox_executor/token_reduction/mitm_addon.py` to
  initialize `self.total_requests = 0` and `self.cache_hits = 0`.
- In `MitmproxyAddon.request`, increment `self.total_requests`. On a cache hit, increment `self.cache_hits` and
  calculate the hit rate (`self.cache_hits / self.total_requests`).
- In `MitmproxyAddon.request`, store the high-resolution start time of the request using
  `flow.request_start_time = time.perf_counter()`.
- Add `responseheaders(self, flow: Any) -> None` callback to `MitmproxyAddon` to store the headers arrival time using
  `flow.response_headers_time = time.perf_counter()`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` (Coding Conventions)
- **Potential failure modes for this step:**
  - Addon fails to load in mitmproxy because of missing callbacks or incorrect arguments.
- **Guardrails and early‑abort checks:**
  - Use standard mitmproxy event hook signatures to avoid initialization crashes.

### Success & Discard Criteria

- Success: `mitm_addon.py` parses successfully, and request/response headers hooks correctly set timing attributes on
  the flow object.
- Discard: Discard if the addon fails to load or violates mitmproxy lifecycle APIs.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 2.0   |
| impact_pred         | 40.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 35.4  |

### Step Metrics Rationale

Simple initialization and timing capture. Very high success probability and low risk.

---

## Step 2: Implement Metrics Calculation and Headers Injection

- **Sub‑intent recommendation:** YES
- **Reasoning:** Implementation of parser routines for different LLM responses, and calculation logic that handles
  multiple edge cases, making this suitable for a dedicated evaluation branch.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Implement token count extraction, calculate telemetry metrics, and inject telemetry headers into
  responses.
- **Git branch:** `I-1788088964-mitm-telemetry-metrics/step2-metrics-injection`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `mitm_addon.py`, write a helper function
  `extract_token_counts(resp_data: dict[str, Any], provider: str) -> tuple[int, int]` to extract
  `(input_tokens, output_tokens)`.
  - For Anthropic: look up `usage.input_tokens` and `usage.output_tokens`.
  - For OpenAI: look up `usage.prompt_tokens` and `usage.completion_tokens`.
  - For Gemini: look up `usageMetadata.promptTokenCount` and `usageMetadata.candidatesTokenCount`.
  - Provide a fallback estimation if parsing fails: prompt characters / 4 for input, response characters / 4 for output.
- In `MitmproxyAddon.request`, if there is a cache hit, compute the hit rate, format, and inject the following headers
  onto `flow.response.headers`:
  - `X-Cache-Hit-Rate`: hit rate formatted to 4 decimal places.
  - `X-TTFT`: `"0.0000"`
  - `X-Prefill-TPS`: `"0.0000"`
  - `X-Output-TPS`: `"0.0000"`
  - `X-Total-Time`: `"0.0000"`
- In `MitmproxyAddon.response`, if it is a cache miss and the status is 200, extract input/output tokens from
  request/response JSON bodies.
- Compute the timing metrics:
  - `ttft = flow.response_headers_time - flow.request_start_time`.
  - `total_time = time.perf_counter() - flow.request_start_time`.
  - `generation_time = total_time - ttft`.
  - `prefill_tps = input_tokens / ttft` (if `ttft > 0` else `0.0`).
  - `output_tps = output_tokens / generation_time` (if `generation_time > 0` else `0.0`).
  - `hit_rate = self.cache_hits / self.total_requests`.
- Format all metrics to 4 decimal places and inject them into `flow.response.headers`:
  - `X-Cache-Hit-Rate`
  - `X-TTFT`
  - `X-Prefill-TPS`
  - `X-Output-TPS`
  - `X-Total-Time`

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` (Coding Conventions)
- **Potential failure modes for this step:**
  - Division by zero if time differences are zero.
  - JSON decoding exceptions or type errors when accessing nested dict attributes.
- **Guardrails and early‑abort checks:**
  - Protect divisions with checks (e.g., `ttft > 0` and `generation_time > 0`).
  - Wrap dict lookups in `try-except` blocks or use `.get()` with defaults.

### Success & Discard Criteria

- Success: Responses from cache hits and cache misses contain the required telemetry headers formatted correctly.
- Discard: Discard if parsing or math operations cause uncaught exceptions that halt proxy execution.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 3.0   |
| impact_pred         | 70.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 52.6  |

### Step Metrics Rationale

This step contains the core calculations and provider-specific payload parsing. The success probability is 0.85 due to
the complexity of nested dictionary structures across different provider responses.

---

## Step 3: Add Telemetry Header Assertions to Unit Tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard testing step updating existing unit test files.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update `test_token_reduction.py` to mock timings and verify telemetry headers are injected correctly.
- **Git branch:** `I-1788088964-mitm-telemetry-metrics/step3-telemetry-tests`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Modify unit tests inside `apps/sandbox-executor/tests/test_token_reduction.py` (specifically tests targeting
  `MitmproxyAddon`).
- Mock request start time and response header/end timestamps to control the metrics output.
- Assert that cache hit flows contain headers with values of `"0.0000"`.
- Assert that cache miss flows contain calculated values for `X-TTFT`, `X-Prefill-TPS`, `X-Output-TPS`, and
  `X-Total-Time`.
- Test header values with valid input/output tokens in request/response bodies for Anthropic, OpenAI, and Gemini.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` (Testing Constraints)
- **Potential failure modes for this step:**
  - Tests fail due to incorrect timestamp calculations or mock object interface mismatches.
- **Guardrails and early‑abort checks:**
  - Follow existing mock patterns for `FakeFlow`, `FakeRequest`, and `FakeResponse`.

### Success & Discard Criteria

- Success: `pytest apps/sandbox-executor/tests/test_token_reduction.py` passes all unit tests successfully.
- Discard: Discard if the new tests introduce syntax or runner errors that cannot be resolved within 1.5x expected cost.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 2.5   |
| impact_pred         | 50.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.5   |
| ev_pred             | 43.0  |

### Step Metrics Rationale

Testing implementation ensures correctness of code changes. High success probability with standard mock methods.

---
