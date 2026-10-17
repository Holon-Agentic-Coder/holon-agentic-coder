# MITM Proxy Telemetry & TPS Metrics Implementation Plan

This document outlines the architectural design and execution plan for adding real-time streaming performance metrics
(**Time-To-First-Token / TTFT**, **Prefill TPS**, **Output TPS**, and **Decode Time**) directly into the MITM proxy
sidecar ([`mitm_addon.py`](apps/sandbox-executor/src/sandbox_executor/token_reduction/mitm_addon.py)).

---

## 🎯 Architectural Intent

By measuring streaming telemetry directly at the proxy layer:

1. **Agent Independence**: All containerized agents (`antigravity`, `claude`, `codex`, `pi`) automatically get 100%
   consistent streaming performance telemetry without modifying agent source code.
2. **Provider Agnostic**: Works identically across Anthropic, OpenAI, OpenRouter, Gemini, and local LLM endpoints.
3. **Objective A/B Benchmarking**: Measures true wire latency and network socket throughput, eliminating local agent UI
   rendering or file I/O overhead.

---

## 📊 Comprehensive Metrics Framework

| Category                     | Metric                     | Formula / Source                                                             | Description                                                            |
| :--------------------------- | :------------------------- | :--------------------------------------------------------------------------- | :--------------------------------------------------------------------- |
| **Latency & Timing**         | **TTFT**                   | $T_{\text{first\_byte}} - T_{\text{request\_start}}$                         | Time-To-First-Token / Prefill latency (ms)                             |
|                              | **Decode Time**            | $T_{\text{response\_end}} - T_{\text{first\_byte}}$                          | Active duration of token generation (sec)                              |
|                              | **Total Time**             | $T_{\text{response\_end}} - T_{\text{request\_start}}$                       | Total end-to-end request duration (ms)                                 |
|                              | **Proxy Overhead**         | $T_{\text{proxy\_internal}}$                                                 | Additional latency added by MITM proxy interception (ms)               |
| **Throughput & Speed**       | **Prefill TPS**            | $\frac{\text{Prompt Tokens}}{\text{TTFT (sec)}}$                             | Prompt processing throughput (tokens/sec)                              |
|                              | **Output TPS**             | $\frac{\text{Completion Tokens}}{\text{Decode Time (sec)}}$                  | Token generation throughput (tokens/sec)                               |
|                              | **Tail Prefill TPS**       | $\frac{\text{Uncached Prompt Tokens}}{\text{TTFT (sec)}}$                    | Effective throughput on new/uncached prompt tokens (tokens/sec)        |
| **Cache & Token Efficiency** | **Prompt Cache Hit Rate**  | $\frac{\text{Cached Input Tokens}}{\text{Total Prompt Tokens}} \times 100\%$ | Percentage of prompt tokens served from KV / disk / provider cache (%) |
|                              | **Token Savings Ratio**    | $\frac{\text{Pruned Tokens}}{\text{Original Tokens}} \times 100\%$           | Percentage of context tokens eliminated by cleaner policies (%)        |
|                              | **Cache Creation Tokens**  | `usage.cache_creation_input_tokens`                                          | Billed tokens spent creating new cache breakpoints                     |
|                              | **Cache Read Tokens**      | `usage.cache_read_input_tokens`                                              | Billed tokens read from existing prompt cache                          |
| **Payload & Bandwidth**      | **Wire Bytes Savings**     | $\text{Bytes}_{\text{agent}} - \text{Bytes}_{\text{cleaned}}$                | Network payload bandwidth saved by proxy cleaning (bytes)              |
|                              | **Compression Ratio**      | $\frac{\text{Bytes}_{\text{cleaned}}}{\text{Bytes}_{\text{agent}}}$          | Payload size ratio after context optimization                          |
| **Reasoning & Cost**         | **Reasoning Tokens**       | `usage.completion_tokens_details.reasoning_tokens`                           | Chain-of-thought reasoning tokens generated before content             |
|                              | **Estimated Request Cost** | $\sum (\text{Tokens}_i \times \text{Price}_i)$                               | Upstream API cost incurred for request ($)                             |
|                              | **Monetary Cost Savings**  | $\text{Cost}_{\text{baseline}} - \text{Cost}_{\text{reduced}}$               | Monetary savings achieved via caching & token reduction ($)            |

---

## 🛠️ Implementation Phases

### Phase 1: Flow Lifecycle Hook Extension (`mitm_addon.py`)

Extend `mitmproxy` addon hooks to capture precise high-resolution timestamps per flow:

- **`request(flow)`**: Capture $T_{\text{start}} = \text{time.perf\_counter()}$ upon outbound request initialization.
- **`responseheaders(flow)`**: Capture $T_{\text{first}} = \text{time.perf\_counter()}$ upon receipt of the first HTTP
  response header or SSE streaming chunk.
- **`response(flow)`**: Capture $T_{\text{end}} = \text{time.perf\_counter()}$ upon response completion.

### Phase 2: Provider Token & Usage Parsing

Extract token counts from final HTTP response payloads or SSE stream usage objects:

- **Anthropic**: Read `usage.input_tokens` and `usage.output_tokens` (or `cache_read_input_tokens`,
  `cache_creation_input_tokens`).
- **OpenAI / OpenRouter**: Read `usage.prompt_tokens` and `usage.completion_tokens` from final SSE chunk
  (`stream_options: {include_usage: true}`).
- **Gemini**: Read `usageMetadata.promptTokenCount` and `usageMetadata.candidatesTokenCount`.

### Phase 3: Metric Calculation & Header Injection

Compute performance metrics and inject standardised response headers into `flow.response.headers`:

- `X-Holon-TTFT-Ms`: Time-to-first-token in milliseconds.
- `X-Holon-Prefill-TPS`: Prompt processing speed (tokens/sec).
- `X-Holon-Output-TPS`: Generation speed (tokens/sec).
- `X-Holon-Decode-Time-Sec`: Generation duration in seconds.
- `X-Holon-Total-Time-Ms`: Total end-to-end request duration in milliseconds.

### Phase 4: Structured Telemetry Logging & SQLite Storage

- Log formatted telemetry lines to proxy stderr / stdout for live inspection:
  ```text
  📊 [TELEMETRY] Provider: OPENROUTER | TTFT: 342.5ms | Prefill: 494.15 t/s (169 tok) | Output: 38.33 t/s (46 tok in 1.20s) | Total: 1542.5ms
  ```
- Extend SQLite cache store schema (`llm_cache.db`) to record telemetry columns for historical A/B benchmarking
  analysis.

### Phase 5: A/B Benchmark Harness Integration

Update `todo/ab_benchmark_real_api.py` and `todo/ab_benchmark_holon_agent.py` to read `X-Holon-*` headers and log
output/prefill TPS in `todo/ab_real_api_results.json` and `todo/ab_real_api_results.md`.

---

## 📈 Expected Outcomes

1. Automated recording of Prefill TPS and Output TPS for all live network tests in `todo/`.
2. Standardised response headers returned to all sandbox agent containers.
3. Empirically verified latency and throughput benchmarks across arbitrary LLM APIs and arbitrary agents.
