# Plan for I-1787928920-token-reduction-phase3

- **Plan ID:** P-1787928942-antigravity-agent-gemini-3.5-flash
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.5-flash (version: 1.1.22)
- **Created At:** 2026-08-28T14:55:42.662Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with 1–2 sentence justification. A balanced exploration stance is chosen because
  token Jaccard similarity semantic matching is a new pattern for this codebase, requiring experimental tuning of
  tokenizer parameters and Jaccard threshold values.
- **Safety priority level:** standard
- **Priority Justification:** This task implements local SQLite storage and basic Jaccard similarity algorithms within a
  sandbox, without using unsafe system calls, external APIs, or modifying core containment mechanisms.

## Exploration

- **Proportion of steps that are exploratory:** 0.25
- **Justification:** Step 2 includes experimental evaluation of tokenizer configurations and threshold sensitivity,
  which is crucial for maximizing token reduction efficiency.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 3.5   |
| impact_pred         | 85.0  |
| cost_pred           | 9.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 69.4  |

### Strategy Rationale

The overall plan metrics were derived as follows:

- **p_success_pred**: 0.90. The success probability is determined by the bottleneck of Step 2 (0.92) and the combined
  risk of configuring the workspace and integrating the mitmproxy hook successfully.
- **entropy_pred**: 3.5. Derived from the maximum step-level entropy (3.5 in Step 2), which represents the risk of
  implementing custom semantic matching algorithms. This is well within the allocated 15.0 budget.
- **impact_pred**: 85.0. Successful implementation of the cache delivers a high-impact token-reduction capability for
  all downstream agent LLM calls.
- **cost_pred**: 9.0. Sum of the cost estimates for all individual steps (1.5 + 3.0 + 2.5 + 2.0 = 9.0).
- **learning_value_pred**: 6.0. The peak learning value associated with Step 2, representing the epistemic gain from
  testing Jaccard similarity matching efficacy on real-world request patterns.
- **ev_pred**: 69.4. Calculated using the default EV config constants: EV = 0.90 _ 85.0 + 0.5 _ 6.0 - 0.3 \* 3.5 - 9.0 =
  76.5 + 3.0 - 1.05 - 9.0 = 69.4 (rounded).

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` (Git Flow & Branch Constraints, Sandbox Containment Tiers)
  - `holon-config/world/ruleset.md` (Python Runtime, Testing Constraints)
- **Potential violations or edge cases:**
  - SQLite database locking during concurrent mitmproxy flow interceptions.
  - Adding mitmproxy as a dependency causing package conflicts or build failures.
- **Mitigations built into the plan:**
  - Standard SQLite connection timeout parameters and retry wrappers to avoid locks.
  - Declaring clean package versions in pyproject.toml and running automated uv sync tests.
- **Residual risk accepted (and why):**
  - Minimal read/write overhead to the SQLite database during high-throughput requests. This is acceptable as caching is
    intended for low-frequency agent LLM calls rather than high-performance web traffic.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 8.5
- **Budget Compliance:** The strategy fits within budget (Predicted Plan Entropy of 8.5 < 15.0 allocated).

## Plan Description & Strategy

This plan implements a disk-backed SQLite hybrid cache that supports exact prefix keying and token Jaccard similarity
semantic matching, integrated as a mitmproxy addon. In Step 1, we register the new package `apps/mitmproxy-cache` as a
workspace member and define dependencies (such as `mitmproxy` and dependencies needed for tests). In Step 2, we
implement the SQLite database wrapper and the hybrid matching logic (prefix query + tokenized Jaccard similarity). In
Step 3, we build the mitmproxy addon hook class that intercepts requests, checks cache hits, returns mocked responses,
and caches incoming fresh responses. In Step 4, we write full unit and integration tests to verify correctness, Jaccard
matching thresholds, and SQLite concurrent safety.

---

## Step 1: Configure Workspace and Declare Dependencies

- **Sub‑intent recommendation:** NO
- **Reasoning:** Basic repository configuration step with low complexity and zero algorithmic risk.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Declare `mitmproxy-cache` workspace app, update root pyproject.toml and lockfile to include
  mitmproxy.
- **Git branch:** `I-1787928920-token-reduction-phase3/step1-workspace-config`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create a new package directory `apps/mitmproxy-cache` in the workspace.
- Create `apps/mitmproxy-cache/pyproject.toml` declaring dependencies on `mitmproxy==11.0.0` (or matching latest python
  3.13 compatible version) and the local package metadata.
- Modify the root `pyproject.toml` under `[workspace]` members to include `apps/mitmproxy-cache`.
- Declare local path mappings for `mitmproxy-cache` in `[tool.uv.sources]`.
- Execute `uv sync` to update `uv.lock` and verify environment packages compile cleanly.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` (Runtime & Environment Specification)
- **Potential failure modes for this step:**
  - Dependency conflicts in python 3.13 with mitmproxy.
- **Guardrails and early‑abort checks:**
  - Abort if `uv sync` fails or reports dependency resolution errors.

### Success & Discard Criteria

- **Success:** `uv sync` completes successfully and `mitmproxy` is available in the virtual environment.
- **Discard:** Discard if dependency resolution fails or conflicts with core packages.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 1.0   |
| impact_pred         | 20.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 18.3  |

### Step Metrics Rationale

Simple configuration step utilizing standard package management tools with very high success probability.

---

## Step 2: Implement SQLite Hybrid Cache and Jaccard Matcher

- **Sub‑intent recommendation:** YES
- **Reasoning:** Contains the core Jaccard similarity and prefix matching logic which requires isolated testing and
  verification.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** A whitespace-and-punctuation tokenizer combined with SQLite prefix lookup and Jaccard
  similarity thresholding provides high-precision matching of semantic equivalents for LLM prompts under different
  formatting.
- **Learning target:** Optimize Jaccard similarity performance over different prompt payload formats and check SQLite
  concurrent access safety.
- **Maximum acceptable cost for this learning:** 3.5 cost units

### Intent & Git Integration

- **Step Intent:** Create SQLite cache backend with exact prefix keying and token Jaccard similarity semantic matcher.
- **Git branch:** `I-1787928920-token-reduction-phase3/step2-implement-cache-matcher`
- **Sub‑intent:** NEW

### Implementation Details (No code blocks, only logic/steps)

- Create a module `apps/mitmproxy-cache/src/mitmproxy_cache/cache.py`.
- Define an `SQLiteCache` class that initializes an SQLite database connection and sets up tables for `cache_entries`
  (storing method, URL, headers, exact request key/prefix, request body, response status, response headers, response
  body, serialized tokens set, and timestamp).
- Implement exact prefix keying matching: retrieve matches where request URL and exact request key match or match a
  prefix of cached keys.
- Implement tokenization helper: clean punctuation, lowercase, split by whitespace, and return a set of unique token
  strings.
- Implement Jaccard similarity semantic matching: for a given request, compute the token set, query database entries
  matching URL/method, calculate Jaccard intersection over union in Python, and return the hit if the similarity exceeds
  a defined threshold (e.g. 0.85).
- Include database connection locking safety with retry logic and WAL (Write-Ahead Logging) mode.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` (Coding Conventions)
- **Potential failure modes for this step:**
  - High latency during Jaccard calculation if the database contains too many cached requests.
- **Guardrails and early‑abort checks:**
  - Apply database indexes on request method and URL to narrow down search candidates.
  - Implement a query limit on candidate records to avoid loading full tables into memory.

### Success & Discard Criteria

- **Success:** SQLite cache successfully reads and writes, correctly returning matches for both exact prefix queries and
  semantic queries matching >85% Jaccard similarity.
- **Discard:** Discard if database locks block execution or Jaccard computation latency exceeds 50ms.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 3.5   |
| impact_pred         | 75.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 67.9  |

### Step Metrics Rationale

This step is the core algorithmic component. The balanced stance and learning targets account for the risk of semantic
match quality and performance tuning.

---

## Step 3: Implement mitmproxy Addon Integration

- **Sub‑intent recommendation:** NO
- **Reasoning:** Straightforward integration of the cache class implemented in Step 2 with mitmproxy hooks.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Create mitmproxy addon script that hooks request/response events to serve cached hits and record new
  entries.
- **Git branch:** `I-1787928920-token-reduction-phase3/step3-addon-integration`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create a module `apps/mitmproxy-cache/src/mitmproxy_cache/addon.py`.
- Define `TokenReductionCacheAddon` implementing mitmproxy hooks.
- Inside `request(self, flow: http.HTTPFlow)` hook:
  - Check if the request matches targeted hosts/endpoints (e.g. LLM APIs).
  - Query `SQLiteCache` for exact prefix or semantic matching cache hit.
  - If a cached response exists, interrupt flow by setting
    `flow.response = http.Response.make(status_code, body, headers)` and log a hit.
- Inside `response(self, flow: http.HTTPFlow)` hook:
  - If the flow was not served from cache and response status is 200, clean up/tokenize request body and write
    request-response pair to `SQLiteCache`.
- Include config parameters for database path, Jaccard threshold, and host filters.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` (Coding Conventions)
- **Potential failure modes for this step:**
  - Mitmproxy intercepting incorrect flows or corrupting HTTP request/response payloads.
- **Guardrails and early‑abort checks:**
  - Wrap hooks in try/except blocks; if any cache operation throws an exception, log the error and allow flow to proceed
    without caching (fail-open strategy).

### Success & Discard Criteria

- **Success:** The mitmproxy addon successfully intercepts traffic, responds from cache on matching URLs/bodies, and
  records non-cached responses correctly.
- **Discard:** Discard if the addon corrupts headers or body formats of bypassed requests.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 2.5   |
| impact_pred         | 80.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 4.0   |
| ev_pred             | 73.9  |

### Step Metrics Rationale

Standard mitmproxy addon development with clean fail-open architecture ensures high success probability and low risk.

---

## Step 4: Write Unit and Integration Tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard testing step required by repositories' coding standards.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Implement test suite checking SQLite operations, Jaccard similarity, and mitmproxy intercept logic.
- **Git branch:** `I-1787928920-token-reduction-phase3/step4-verify-tests`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create test suite under `apps/mitmproxy-cache/tests/`.
- In `tests/test_cache.py`:
  - Assert that exact prefix keying matches correctly.
  - Assert that token Jaccard similarity matches prompts with slight rephrasings (e.g. "hello world" vs "hello there
    world").
  - Assert that dissimilar prompts do not match (below threshold).
- In `tests/test_addon.py`:
  - Mock mitmproxy `HTTPFlow`, request, and response objects.
  - Assert `request()` intercepts and returns mock responses for cache hits.
  - Assert `response()` inserts new entries on successful bypasses.
- Run tests using `pytest` to guarantee 100% pass rate.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` (Testing Constraints)
- **Potential failure modes for this step:**
  - Mock flows mismatching real mitmproxy flow execution schemas.
- **Guardrails and early‑abort checks:**
  - Use official mitmproxy test helper classes if available, or write resilient mocks matching target flow properties.

### Success & Discard Criteria

- **Success:** Test suite discovers all tests passing successfully, checking both database state and addon hooks.
- **Discard:** Discard if tests fail or if test environment cannot resolve dependencies.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.5   |
| impact_pred         | 60.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 56.0  |

### Step Metrics Rationale

High confidence testing step. Mock assertions verify code behavior without running live container operations.
