# Plan for I-1788176325-fix-mitm-telemetry-logging-and-sse-parsing

- **Plan ID:** P-1788176334-antigravity-agent-gemini-3.5-flash
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.5-flash (version: 1.1.22)
- **Created At:** 2026-08-31T11:38:54.895Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** conservative with focus on robust reassembly and parsing of server-sent event (SSE) streams to
  accurately record timing and token counts while avoiding buffering overhead for clients.
- **Safety priority level:** standard
- **Priority Justification:** This intent alters request/response intercept logic inside a standard sandbox without
  making external network calls, using existing file paths and standard event interfaces.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** Implementing token parsing and telemetry formatting is a standard software engineering task with no
  high-entropy exploration or spike needed.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 3.5   |
| impact_pred         | 80.0  |
| cost_pred           | 14.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 55.95 |

### Strategy Rationale

The overall plan metrics were derived from the individual step-level metrics as follows:

- **p_success_pred**: 0.85. Sourced from the bottleneck Step 2 (calculations and stream extraction), representing the
  risk of parsing nested SSE structures across three different provider families.
- **entropy_pred**: 3.5. Derived from the maximum step-level entropy (Step 2), representing potential complexity in
  handling incomplete JSON payloads and time delta calculations.
- **impact_pred**: 80.0. Reflects the value of obtaining accurate, wire-level telemetry data for all streaming LLM
  requests across all sandbox agents.
- **cost_pred**: 14.0. Computed as the sum of individual step costs (4.0 + 6.0 + 4.0).
- **learning_value_pred**: 6.0. Qualitative judgment based on introducing robust stream reassembly and parsing
  structures to the caching system.
- **ev_pred**: 55.95. Computed as `0.85 * 80.0 + 0.5 * 6.0 - 0.3 * 3.5 - 14.0 = 68.0 + 3.0 - 1.05 - 14.0 = 55.95` with
  `λ = 0.3` and `μ = 0.5`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` (Git Flow & Branch Constraints, Sandbox Containment Tiers)
  - `holon-config/world/ruleset.md` (Coding Conventions, Testing Constraints)
  - `docs/safety.md` (Sandboxing, Trust Levels, Entropy Budgets)
- **Potential violations or edge cases:**
  - High-resolution timing difference of exactly zero leading to division-by-zero during TPS computation.
  - JSON decoding exceptions when processing incomplete SSE chunks or non-standard responses from unexpected providers.
  - Stream truncation causing raw stream reassembly failure.
- **Mitigations built into the plan:**
  - Protected division operations with checks ensuring `ttft > 0` and `generation_time > 0`.
  - JSON parsing routines protected with try-except blocks, falling back to robust character-based token estimates if
    parsing fails.
- **Residual risk accepted (and why):**
  - Minor timing variations in TTFT due to proxy execution overhead; accepted because it represents true wire latency.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 8.0
- **Budget Compliance:** The strategy fits within budget (Predicted Plan Entropy of 8.0 < 15.0 allocated).

## Plan Description & Strategy

This plan resolves accurate streaming telemetry tracking inside `mitm_addon.py`. In Step 1, we set up hooks to intercept
event-stream response headers and dynamically wrap `flow.response.stream` to record chunks without blocking client-side
real-time streaming. In Step 2, we implement parsing for event-stream payloads to extract actual token counts, calculate
final timing metrics, and consolidate telemetry output into a single, deduplicated `📊 [TELEMETRY]` log statement. In
Step 3, we write comprehensive unit tests asserting correct telemetry tracking on mocked event stream flows.

---

## Step 1: SSE Stream Chunk Interception & Reassembly in `mitm_addon.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Straightforward interceptor setup in existing hooks without high complexity or risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Detect event-stream responses and assign a custom wrapper to `flow.response.stream` to accumulate SSE
  chunks as they stream.
- **Git branch:** `I-1788176325-fix-mitm-telemetry-logging-and-sse-parsing/step1-sse-interception`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `responseheaders(self, flow: Any) -> None` callback inside
  `apps/sandbox-executor/src/sandbox_executor/token_reduction/mitm_addon.py`, check the response headers to see if
  `Content-Type` contains `text/event-stream`.
- If an event-stream is detected, initialize `flow.sse_chunks = []` on the flow instance.
- Define a streaming chunk wrapper callback: `sse_stream_wrapper(chunk: bytes) -> bytes`.
- Within the wrapper, check if the chunk is non-empty and append it to `flow.sse_chunks`.
- Return the chunk unchanged to allow immediate client streaming.
- Assign the streaming chunk wrapper to `flow.response.stream`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2` (Coding Conventions)
- **Potential failure modes for this step:**
  - Streaming wrapper function raising exceptions or corrupting the response payload sent to the agent.
- **Guardrails and early‑abort checks:**
  - Wrap streaming setup logic inside a safety check validating headers and flow response properties.

### Success & Discard Criteria

- **Success:** `mitmproxy` starts up, triggers the responseheaders hook, and assigns `flow.response.stream` correctly on
  event-stream responses.
- **Discard:** Discard if the stream assignment causes socket errors or hangs.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 2.0   |
| impact_pred         | 40.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 33.7  |

### Step Metrics Rationale

This step configures standard mitmproxy streaming hook options. Probability of success is very high, and risk is
minimal.

---

## Step 2: SSE Token Extraction & Consolidated Telemetry Logging in `mitm_addon.py`

- **Sub‑intent recommendation:** YES
- **Reasoning:** Involves complex parsing across multiple nested dictionary configurations for different providers, with
  a high impact on overall system calculations.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Parse accumulated SSE chunks to extract token counts, calculate final telemetry speed metrics, and
  output a single deduplicated telemetry log.
- **Git branch:** `I-1788176325-fix-mitm-telemetry-logging-and-sse-parsing/step2-token-extraction-logging`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `mitm_addon.py`, write helper function
  `extract_sse_token_counts(resp_text: str, req_data: dict[str, Any], provider: str) -> tuple[int, int]`.
- Iterate through SSE data lines in `resp_text`. Extract json from lines starting with `data:`.
- For Anthropic: extract `input_tokens` from `message_start` and `output_tokens` from `message_delta` events, or
  accumulate `delta.text` length and divide by 4.
- For OpenAI: extract `input_tokens` and `output_tokens` from the usage chunk, or accumulate `choices[0].delta.content`
  length and divide by 4.
- For Gemini: extract `input_tokens` and `output_tokens` from `usageMetadata`, or accumulate candidate parts text length
  and divide by 4.
- Update `extract_token_counts` to accept `resp_data: dict | str` and route string payloads to
  `extract_sse_token_counts`.
- In `response(self, flow: Any) -> None`, detect if it is an event stream response. If so, reconstruct
  `resp_text = b"".join(flow.sse_chunks).decode("utf-8", errors="ignore")` and call
  `extract_token_counts(req_data, resp_text, provider)`.
- Compute timing and TPS metrics. Ensure no divisions by zero occur.
- Write a single unified `logger.info("📊 [TELEMETRY] ...")` log line formatting the provider, TTFT, Prefill TPS, Output
  TPS, and Total Time. Remove any other logging outputs to keep it to a single deduplicated message.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2` (Coding Conventions)
- **Potential failure modes for this step:**
  - Division by zero if `ttft` or `generation_time` is zero.
  - Parsing exceptions on malformed event data.
- **Guardrails and early‑abort checks:**
  - Protect divisions with checks (e.g. `ttft > 0`).
  - Protect JSON decoding of data blocks with try-except blocks.

### Success & Discard Criteria

- **Success:** Token counts are correctly extracted from streamed payloads, timing metrics are populated, and a single
  consolidated telemetry log is output.
- **Discard:** Discard if parsing is inaccurate or if log noise increases.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 3.5   |
| impact_pred         | 80.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 63.95 |

### Step Metrics Rationale

Contains the core business and formatting logic, which has the highest probability of edge cases and exceptions during
implementation.

---

## Step 3: Add Unit Tests for Telemetry Logging and SSE Stream Parsing in `test_token_reduction.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard test implementation using mocked flows to assert metrics calculation correctness.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add test coverage verifying token extraction from simulated SSE stream flows and formatting of the
  single telemetry log.
- **Git branch:** `I-1788176325-fix-mitm-telemetry-logging-and-sse-parsing/step3-sse-tests`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Open `apps/sandbox-executor/tests/test_token_reduction.py` and write tests simulating text/event-stream flows.
- Mock request start time and response header/end timestamps.
- Feed list of mock SSE bytes (representing Anthropic message events or OpenAI usage chunks) through the MitmproxyAddon
  lifecycle hooks.
- Assert that `extract_token_counts` returns correct values and headers are populated.
- Assert that the logs contain exactly one telemetry line with correct formatting.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3` (Testing Constraints)
- **Potential failure modes for this step:**
  - Flaky tests if timestamps are not mocked deterministically.
- **Guardrails and early‑abort checks:**
  - Pin the exact performance counter timestamps using pytest monkeypatch.

### Success & Discard Criteria

- **Success:** `pytest` executes successfully and all new test cases pass.
- **Discard:** Discard if the test environment fails to mock mitmproxy context objects successfully.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 2.5   |
| impact_pred         | 50.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 42.25 |

### Step Metrics Rationale

Straightforward test implementation ensuring coverage and validating parsing logic under regression testing.

---
