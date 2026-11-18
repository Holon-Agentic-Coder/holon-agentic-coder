# Plan for I-1791983257-proxy-routing-and-codebase-audit

- **Plan ID:** P-1791983277-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-10-14T13:07:57.825Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with empirical validation of holon-coherence TCP/image/CLI auto-detection, proxy env
  propagation into review and execution stages, non-blocking graceful fallback to direct egress, and holistic repository
  auditing across architecture, security, bugs, and performance.
- **Safety priority level:** elevated
- **Priority Justification:** Triggered by `docs/safety.md` §1 & §2 (Git as safety boundary, sandboxing mandatory for
  execution), `holon-config/world/constraints.md` §2 (Sandbox containment tiers and escape detection: proxy
  configuration touches network boundaries and TLS trust stores `SSL_CERT_FILE` / `NODE_EXTRA_CA_CERTS`),
  `holon-config/world/ruleset.md` §1 & §2 (Python runtime `==3.13.*`, workspace isolation under `apps/sandbox-executor`,
  strict typing with `typing`, PEP 8 compliance, docstrings), and `holon-config/world/ruleset.md` §3 (Testing
  constraints: pytest 9.1.1, test placement in `apps/sandbox-executor/tests/`). Elevated safety ensures TLS certificates
  and proxy env variables cannot leak secrets or allow unverified network egress.

## Exploration

- **Proportion of steps that are exploratory:** 0.43
- **Justification:** Steps 1, 3, and 5 incorporate balanced exploratory probing and architectural auditing to
  empirically validate TCP connectivity checks against active sidecars, Docker image metadata discovery, dynamic
  certificate trust injection into subprocess environments, and systematic vulnerability assessment without pipeline
  regression.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.60  |
| impact_pred         | 95.0  |
| cost_pred           | 15.0  |
| learning_value_pred | 6.2   |
| ev_pred             | 75.32 |

### Strategy Rationale

The overall plan metrics were derived from individual step-level metrics as follows:

- **p_success_pred**: 0.92. Dictated by the bottleneck step (Step 5: 0.92), where a thorough, multi-pillar
  architectural, security, bug, and reliability audit of the entire `holon-agentic-coder` monorepo must be conducted and
  synthesized into a structured `docs/audit_report.md` without omission of critical system dynamics.
- **entropy_pred**: 0.60. Evaluated as the maximum single-step risk profile (Step 5: 0.60), where wide-scope static
  analysis and diagnostic sweeps identify systemic refactoring needs. The sum of predicted step entropies across all
  seven steps is 2.85 (`0.50 + 0.40 + 0.45 + 0.30 + 0.60 + 0.25 + 0.35 = 2.85`), well within the allocated entropy
  budget of 15.0.
- **impact_pred**: 95.0. Directly fulfills Beans 0089 and 0044: opportunistically routes all Holon pipeline flow stages
  (`intent`, `plan`, `execute`, `review`, `calibrate`) through the `holon-coherence` token optimization proxy when
  active; wires proxy environment variables (`HTTP_PROXY`, `HTTPS_PROXY`, `NODE_EXTRA_CA_CERTS`, `SSL_CERT_FILE`) and CA
  trust stores into agent runs and review loops; guarantees robust fallback to direct egress without crashing the
  pipeline; produces an exhaustive architectural audit report (`docs/audit_report.md`); and spins off actionable
  remediation beans in `.beans/` starting from `0092`.
- **cost_pred**: 15.0. Calculated as the direct sum of individual step costs
  (`2.4 + 2.0 + 1.8 + 2.2 + 3.5 + 1.5 + 1.6 = 15.0`).
- **learning_value_pred**: 6.2. High epistemic gain delivered from validating opportunistic proxy probing techniques
  (TCP, Docker image inspection, CLI discovery), hardening TLS CA bundle propagation under unprivileged sandbox
  containers, identifying hidden architectural boundaries and security bottlenecks, and establishing a roadmap for
  ongoing autonomous code quality.
- **ev_pred**: 75.32. Computed strictly via the config-driven Expected Value formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`. With system constants `lambda = 0.3`
  and `mu = 0.5` loaded from `holon-config/metrics/ev_config.json`:
  `EV = 0.92 * 95.0 + 0.5 * 6.2 - 0.3 * 0.60 - 15.0 = 87.40 + 3.10 - 0.18 - 15.0 = 75.32`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python target `==3.13.*`, package
    management via `uv`).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8 compliance, static typing with `typing`
    module, docstring maintenance).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test files placed in
    `apps/sandbox-executor/tests/`, all test changes explicitly declared in the planning step).
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: prefix-based branch isolation, rebase
    discipline, commit boundaries).
  - `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: filesystem containment, no unwhitelisted
    subprocesses, process sandbox `< 10` entropy).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: zero modifications to historical ledger entries,
    append-only ledger operations).
  - `docs/safety.md` §1 & §2 (Git as safety boundary, sandboxing mandatory for execution).
  - Bean 0089 (auto-route flow stages through holon-coherence proxy, explicit opt-out precedence, fallback to direct
    egress).
  - Bean 0044 (architecture, security, bug, and reliability audit of holon-agentic-coder, audit report, and spun-off
    task beans).
- **Potential violations or edge cases:**
  - `HOLON_TOKEN_REDUCE` explicit opt-out values (e.g. `'0'`, `'false'`, `'no'`, `'off'`) being shadowed by automatic
    socket or docker probes.
  - Zombie TCP connection attempts or long socket connect timeouts stalling pipeline execution when the proxy port is
    unreachable.
  - Subprocess execution environments in `flow.py` dropping proxy environment variables or failing TLS verification
    against external APIs due to incomplete CA certificate bundle mounting.
  - Host environment variable pollution if proxy environment variables are modified globally in `os.environ` rather than
    injected into isolated subprocess environments.
  - Audit report or new task bean markdown documents triggering Prettier line length or formatting oscillations.
- **Mitigations built into the plan:**
  - Enforce explicit opt-out precedence as the very first step in `_token_reduce_opt_in` / `detect_holon_coherence`
    before any socket or container probes execute.
  - Use short, non-blocking socket connect timeouts (`0.2s`) and immediate exception handling for connection refused or
    host unreachable conditions.
  - Isolate all proxy environment propagation to subprocess `env` dictionaries, leaving host `os.environ` untainted.
  - In unprivileged container sandboxes, ensure `NODE_EXTRA_CA_CERTS` points to the single cert and `SSL_CERT_FILE`
    points to the merged bundle as specified in `role_dispatcher.sh`.
  - Format all generated markdown documents with `npx prettier --write` and run automated checks (`make lint`,
    `make tools`, `pytest`, `ruff`, `prettier`) before completion.
- **Residual risk accepted (and why):**
  - Minor socket connect latency (up to 200ms) on cold starts when probing inactive proxy ports; accepted because 200ms
    is imperceptible compared to pipeline stage execution time.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 2.85
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

This plan implements Beans 0089 and 0044 across `holon-agentic-coder`:

1. **Auto-Detection & Opportunistic Wiring (Bean 0089):** Implement multi-tier auto-detection in
   `apps/sandbox-executor/src/sandbox_executor/cli.py` and `flow.py`. Respect explicit opt-out (`HOLON_TOKEN_REDUCE="0"`
   or `"false"`). Probe active listening sockets (`127.0.0.1:8080` or `HOLON_PROXY_PORT`), Docker daemon for
   `holon-coherence:latest` or `ghcr.io/holon-agentic-coder/holon-coherence:latest`, and system PATH for
   `holon-coherence` CLI. Automatically default `token_reduce=True` across all flow stages (`intent`, `plan`, `execute`,
   `review`, `calibrate`).

2. **Environment & CA Trust Propagation (Bean 0089):** Ensure proxy environment variables (`HTTP_PROXY`, `HTTPS_PROXY`,
   `http_proxy`, `https_proxy`, `NO_PROXY`, `no_proxy`, `NODE_EXTRA_CA_CERTS`, `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`,
   `CURL_CA_BUNDLE`) and root CA trust bundles are propagated into agent execution subprocesses and the PR review loop.

3. **Graceful Fallback to Direct Egress (Bean 0089):** Implement non-fatal error handling: if the proxy fails TCP
   readiness probes or cannot be reached at runtime, emit an actionable diagnostic warning and continue execution with
   direct egress without pipeline crash.

4. **Comprehensive Unit Testing (Bean 0089):** Add comprehensive test coverage in `apps/sandbox-executor/tests/`
   verifying auto-detection, explicit opt-out precedence, TCP fallback on unreachability, and environment propagation.

5. **Codebase Architecture & Security Audit (Bean 0044):** Conduct an exhaustive architectural, security, bug, and
   reliability audit of `holon-agentic-coder`. Produce `docs/audit_report.md` detailing findings and concrete
   remediation steps across Architecture & Cohesion, Security & Isolation, Bugs & Edge Cases, and Performance &
   Reliability.

6. **Dedicated Remediation Beans (Bean 0044):** Spin off dedicated task beans in `.beans/` starting from `0092` to
   capture the audit's concrete remediation proposals as actionable, tracked units of work.

7. **End-to-End Verification & Quality Gates:** Execute all test and linting targets (`uv run pytest`,
   `uv run ruff check`, `uv run ruff format --check`, `npx prettier --check`, `make lint`, `make tools`) ensuring 100%
   clean repository state.

---

## Step 1: Implement holon-coherence proxy auto-detection, opt-out precedence, and opportunistic wiring in cli.py and flow.py (Bean 0089)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Scoped modification to detection functions in `cli.py` and `flow.py`; low risk, rapid verification via
  unit tests.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Multi-tier probing (TCP port -> Docker image -> CLI binary) provides reliable
  zero-configuration enablement of token reduction without impacting explicitly opted-out environments or incurring
  significant probe latency.
- **Learning target:** Verify fastest probe sequence order and timeout parameters that minimize overhead while avoiding
  false positives.
- **Maximum acceptable cost for this learning:** 2.4 normalized cost units.

### Intent & Git Integration

- **Step Intent:** Implement auto-detection and opportunistic wiring for holon-coherence proxy across `cli.py` and
  `flow.py`, strictly respecting explicit opt-out flags.
- **Git branch:**
  `I-1791983257-proxy-routing-and-codebase-audit/P-1791983277-antigravity-agent-gemini-3.8-flash-medium/E-1791983300-auto-detection/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/cli.py`, introduce
   `_EXPLICIT_OPT_OUT_VALUES = {"0", "false", "no", "off"}` alongside existing truthy values.
2. Refactor `_token_reduce_opt_in(token_reduce: bool)` into an expanded detection hierarchy
   `detect_holon_coherence(token_reduce: bool = False) -> bool`:
   - Tier 1 (Explicit Opt-Out): Check `os.getenv("HOLON_TOKEN_REDUCE", "").strip().lower()`. If present in
     `_EXPLICIT_OPT_OUT_VALUES`, return `False` immediately, regardless of command-line flags or local services.
   - Tier 2 (Explicit Opt-In): If `token_reduce` is True, or `HOLON_TOKEN_REDUCE` is truthy, or
     `HOLON_PASSIVE_MONITORING` is truthy, return `True`.
   - Tier 3 (TCP Port Probe): Determine target proxy host and port from `HOLON_PROXY_URL`, or `127.0.0.1` and
     `int(os.getenv("HOLON_PROXY_PORT", "8080"))`. Attempt a quick non-blocking TCP socket connection with a 0.2-second
     timeout. If successful, return `True`.
   - Tier 4 (Docker Image Probe): If `docker` CLI exists on host PATH, probe if either `holon-coherence:latest` or
     `ghcr.io/holon-agentic-coder/holon-coherence:latest` is present locally via `docker image inspect` or
     `docker images -q`. If found, return `True`.
   - Tier 5 (CLI Binary Probe): Check if `holon-coherence` executable exists on PATH via
     `shutil.which("holon-coherence")`. If found, return `True`.
   - If all tiers evaluate negative, return `False`.
3. In `apps/sandbox-executor/src/sandbox_executor/flow.py`:
   - Update `FlowContext.__post_init__` (or initialization in `run_flow_pipeline`) so that when `token_reduce` is not
     explicitly set to False via CLI opt-out, `FlowContext.token_reduce` defaults to the result of
     `detect_holon_coherence()`.
   - Ensure the resolved `token_reduce` boolean is consistently propagated across all flow stages: `intent`, `plan`,
     `execute`, `review`, and `calibrate`.
4. Ensure all public functions have strict type annotations and explanatory docstrings following
   `holon-config/world/ruleset.md`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (Downstream stages depend on accurate proxy detection state)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1 & §2, `docs/safety.md` §1, Bean 0089.
- **Potential failure modes for this step:** Socket probe hanging indefinitely on firewalled network endpoints; Docker
  daemon probe stalling when Docker socket is unresponsive.
- **Guardrails and early‑abort checks:** Wrap socket probe in `socket.create_connection` with `timeout=0.2`; wrap Docker
  inspect calls in `subprocess.run` with `timeout=2.0` and fallback to `False` on timeout or exception.

### Success & Discard Criteria

- **Success:** Calling `detect_holon_coherence()` returns False when `HOLON_TOKEN_REDUCE=0`, returns True when proxy is
  listening on 8080, and correctly defaults `token_reduce=True` in `FlowContext`.
- **Discard:** Probe latency exceeds 500ms under standard local conditions or causes false opt-ins when opted out.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.50  |
| impact_pred         | 85.0  |
| cost_pred           | 2.4   |
| learning_value_pred | 4.5   |
| ev_pred             | 79.60 |

### Step Metrics Rationale

`EV = 0.94 * 85.0 + 0.5 * 4.5 - 0.3 * 0.50 - 2.4 = 79.90 + 2.25 - 0.15 - 2.4 = 79.60`. High success probability as
socket and CLI detection patterns are well-understood; provides epistemic value by standardizing auto-detection across
CLI and flow pipelines.

---

## Step 2: Propagate proxy environment variables and CA certificate trust store into agent execution and review loop (Bean 0089)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Straightforward environment mapping and subprocess argument wiring within `flow.py` and `cli.py`.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Ensure proxy environment variables and certificate trust stores are systematically propagated into
  subprocess environments during execution and review stages.
- **Git branch:**
  `I-1791983257-proxy-routing-and-codebase-audit/P-1791983277-antigravity-agent-gemini-3.8-flash-medium/E-1791983320-env-propagation/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/cli.py`, verify `_build_proxy_envs` generates all required environment
   keys:
   - `HTTP_PROXY`, `HTTPS_PROXY`, `http_proxy`, `https_proxy`, `NO_PROXY`, `no_proxy`.
   - `NODE_EXTRA_CA_CERTS`: points to host or container CA path depending on execution mode (augmenting Node trust
     store).
   - `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`: points to merged CA bundle path.
2. In `apps/sandbox-executor/src/sandbox_executor/flow.py`:
   - In `run_execute_stage`: when `context.token_reduce` is True, invoke
     `get_token_reduction_mounts_and_envs(token_reduce=True)` to obtain proxy environment variables. Inject these
     variables into the environment dictionary passed to `subprocess.run` (e.g. for test execution or agent invocation).
   - In `run_review_stage`: ensure that any network-dependent review calls (such as GitHub API interaction via `gh`,
     remote PR status queries, or agent review evaluators) receive the proxy environment dictionary in their subprocess
     environment.
   - For container sandbox executions in `run_docker_container`: ensure the existing `tr_mounts` and `tr_envs` are
     passed into the docker command arguments when `context.token_reduce` is active.
3. Guarantee that host `os.environ` is never mutated directly; all environment modifications must be scoped to
   subprocess execution dictionaries.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §2, `docs/safety.md` §2, Bean 0089.
- **Potential failure modes for this step:** Passing host CA file paths to containerized processes expecting internal
  container paths; leaking sensitive credentials in proxy logs.
- **Guardrails and early‑abort checks:** Differentiate between host-executed tools (which use host CA path and host
  proxy URL) and containerized executions (which use container CA path and `host.docker.internal`).

### Success & Discard Criteria

- **Success:** Subprocesses executed during `execute` and `review` stages receive `HTTP_PROXY`, `HTTPS_PROXY`,
  `NODE_EXTRA_CA_CERTS`, and `SSL_CERT_FILE` in their environment dictionaries when `token_reduce=True`.
- **Discard:** Subprocess fails to start due to unreadable CA paths or corrupted environment variables.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.40  |
| impact_pred         | 80.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 3.8   |
| ev_pred             | 75.78 |

### Step Metrics Rationale

`EV = 0.95 * 80.0 + 0.5 * 3.8 - 0.3 * 0.40 - 2.0 = 76.00 + 1.90 - 0.12 - 2.0 = 75.78`. High confidence implementation
reusing existing CA bundle generation and proxy environment builders.

---

## Step 3: Implement graceful fallback to direct egress on proxy unreachability or readiness probe failure (Bean 0089)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Localized defensive error handling ensuring robustness under intermittent network conditions.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Non-fatal degradation to direct egress on TCP probe timeout prevents agent pipeline
  blockage when the token proxy terminates unexpectedly.
- **Learning target:** Determine appropriate retry and logging thresholds for proxy readiness probes to distinguish
  temporary startup delay from hard failures.
- **Maximum acceptable cost for this learning:** 1.8 normalized cost units.

### Intent & Git Integration

- **Step Intent:** Implement non-fatal fallback: if holon-coherence proxy fails TCP readiness probe or cannot be
  reached, log diagnostic warning and continue with direct egress without pipeline crash.
- **Git branch:**
  `I-1791983257-proxy-routing-and-codebase-audit/P-1791983277-antigravity-agent-gemini-3.8-flash-medium/E-1791983340-graceful-fallback/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/cli.py`, review `_attach_external_proxy()` and
   `get_token_reduction_mounts_and_envs()`.
2. Ensure that when `_wait_for_proxy` fails or raises an `OSError` / `socket.timeout`:
   - Log a diagnostic warning:
     `logger.warning("holon-coherence proxy probe failed at %s:%s. Proceeding with DIRECT egress fallback.", host, port)`.
   - Return empty mounts `[]` and empty env dictionary `{}`.
3. In `apps/sandbox-executor/src/sandbox_executor/flow.py`:
   - If proxy environment setup fails or raises during stage execution, catch the exception, append a diagnostic entry
     to `FlowContext.logs`, log warning, and proceed with an empty proxy environment.
   - Never raise unhandled exceptions that abort the flow stage when the proxy is offline or unreachable.
4. Verify that pipeline checkpoints persist the fallback status in stage metadata for full post-run traceability.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §4 (Failure containment), Bean 0089.
- **Potential failure modes for this step:** Silent fallback masking legitimate network configuration errors without
  diagnostic log output.
- **Guardrails and early‑abort checks:** Ensure warning log message explicitly outputs the target endpoint, failed probe
  type, and notice of direct egress.

### Success & Discard Criteria

- **Success:** When `127.0.0.1:8080` is closed, running flow pipeline logs warning and finishes execution successfully
  with direct egress.
- **Discard:** Unhandled socket exception crashes the pipeline or hangs for longer than 3 seconds.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.45  |
| impact_pred         | 82.0  |
| cost_pred           | 1.8   |
| learning_value_pred | 4.2   |
| ev_pred             | 76.43 |

### Step Metrics Rationale

`EV = 0.93 * 82.0 + 0.5 * 4.2 - 0.3 * 0.45 - 1.8 = 76.26 + 2.10 - 0.135 - 1.8 = 76.425 -> 76.43`. Ensures robust
resilience across disparate developer setups and headless CI environments.

---

## Step 4: Add comprehensive unit test suite for proxy auto-detection, opt-out precedence, fallback, and environment propagation (Bean 0089)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard unit test expansion within `apps/sandbox-executor/tests/`; fast feedback, high reusability.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add thorough unit tests in `apps/sandbox-executor/tests/test_token_reduction.py` and `test_flow.py`
  covering all Bean 0089 functional behaviors.
- **Git branch:**
  `I-1791983257-proxy-routing-and-codebase-audit/P-1791983277-antigravity-agent-gemini-3.8-flash-medium/E-1791983360-unit-tests/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_token_reduction.py`:
   - `test_detect_holon_coherence_explicit_opt_out`: Set `HOLON_TOKEN_REDUCE` to `"0"`, `"false"`, `"no"`, `"off"` and
     assert detection returns False, even when socket probe is mocked to return True.
   - `test_detect_holon_coherence_tcp_listening`: Mock socket connect to succeed; assert detection returns True when
     opt-out is not set.
   - `test_detect_holon_coherence_docker_image`: Mock socket to fail, mock `subprocess.run` to simulate
     `holon-coherence:latest` present; assert detection returns True.
   - `test_detect_holon_coherence_cli_binary`: Mock socket and docker to fail, mock `shutil.which` to find
     `holon-coherence`; assert detection returns True.
   - `test_detect_holon_coherence_all_negative`: Mock socket, docker, and CLI to fail; assert detection returns False.
   - `test_proxy_unreachable_fallback_direct_egress`: Mock TCP probe failure, verify
     `get_token_reduction_mounts_and_envs` returns `([], {})` and emits diagnostic warning.
2. In `apps/sandbox-executor/tests/test_flow.py`:
   - `test_flow_context_token_reduce_auto_detection`: Verify `FlowContext` defaults `token_reduce=True` when proxy is
     detected and preserves `False` when opted out.
   - `test_flow_stage_environment_propagation`: Verify subprocess environments in `execute` and `review` stages contain
     `HTTP_PROXY`, `HTTPS_PROXY`, `NODE_EXTRA_CA_CERTS`, and `SSL_CERT_FILE` when `token_reduce=True`.
3. Run `uv run pytest apps/sandbox-executor/tests/test_token_reduction.py apps/sandbox-executor/tests/test_flow.py` to
   confirm all assertions pass cleanly.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (pytest standard runner, no modification to existing passing
  tests), Bean 0089.
- **Potential failure modes for this step:** Test cases opening real host sockets or attempting real Docker daemon
  connections during unit test execution.
- **Guardrails and early‑abort checks:** Fully mock `socket.create_connection`, `subprocess.run`, and `shutil.which`
  using `unittest.mock.patch` to maintain complete test hermeticity.

### Success & Discard Criteria

- **Success:** All new unit tests pass cleanly in under 5 seconds with zero real host network or daemon calls.
- **Discard:** Flaky socket timeouts or test regressions in existing unit tests.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.30  |
| impact_pred         | 85.0  |
| cost_pred           | 2.2   |
| learning_value_pred | 4.0   |
| ev_pred             | 81.31 |

### Step Metrics Rationale

`EV = 0.96 * 85.0 + 0.5 * 4.0 - 0.3 * 0.30 - 2.2 = 81.60 + 2.00 - 0.09 - 2.2 = 81.31`. Strong test coverage provides
lasting regression prevention and validates Bean 0089 compliance.

---

## Step 5: Conduct comprehensive architecture, security, bug, and reliability audit of holon-agentic-coder and author docs/audit_report.md (Bean 0044)

- **Sub‑intent recommendation:** NO
- **Reasoning:** In-depth analytical and documentation task; high impact and learning value across the monorepo.
- **Step Type:** DOCUMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Systematic multi-pillar codebase audit will expose latent coupling points, security
  boundaries, and edge-case failure modes across sandbox execution and flow orchestration.
- **Learning target:** Comprehensive inventory of technical debt, architectural hotspots, and security hardening
  opportunities.
- **Maximum acceptable cost for this learning:** 3.5 normalized cost units.

### Intent & Git Integration

- **Step Intent:** Execute a thorough repository audit and produce `docs/audit_report.md` detailing findings and
  concrete remediation steps across Architecture & Cohesion, Security & Isolation, Bugs & Edge Cases, and Performance &
  Reliability.
- **Git branch:**
  `I-1791983257-proxy-routing-and-codebase-audit/P-1791983277-antigravity-agent-gemini-3.8-flash-medium/E-1791983380-audit-report/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Perform deep static analysis and architecture inspection across all codebase areas:
   - Pillar 1: Architecture & Cohesion (monorepo packaging, `apps/sandbox-executor` vs root orchestration, coupling
     between `flow.py`, `cli.py`, `agent_runner.py`, and `calibration.py`, interface boundaries).
   - Pillar 2: Security & Isolation (container runtime privilege boundaries, non-root `holon` user, CA key vs public
     certificate separation under `~/.holon/proxy-ca`, environment variable sanitization, secrets handling in logs,
     sandbox egress restrictions).
   - Pillar 3: Bugs & Edge Cases (rebase conflict resolution loops, unattended stage error recovery, subprocess timeout
     bounds, ledger concurrency write safety, JSONL parsing resilience).
   - Pillar 4: Performance & Reliability (token proxy latency, container cold-start overhead, test execution duration,
     image layer caching, checkpoint atomic write resilience).
2. Author `docs/audit_report.md` structured as follows:
   - Executive Summary and System Overview.
   - Audit Methodology & Scope.
   - Findings Matrix: Item ID, Title, Pillar, Severity (Critical, High, Medium, Low), Affected Component, Description.
   - Detailed Findings & Analysis per Pillar.
   - Concrete Remediation Proposals and Strategic Roadmap.
3. Validate markdown formatting and syntax using Prettier.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (Audit findings directly feed into Step 6 task beans)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md`, `holon-config/world/ruleset.md`, Bean 0044.
- **Potential failure modes for this step:** Incomplete audit overlooking critical security or isolation risks;
  unstructured recommendations lacking concrete technical remediation steps.
- **Guardrails and early‑abort checks:** Ensure every finding includes exact file paths, risk severity justification,
  and actionable implementation steps.

### Success & Discard Criteria

- **Success:** `docs/audit_report.md` is complete, covers all four required pillars with actionable remediation steps,
  and passes markdown validation.
- **Discard:** Superficial findings that duplicate existing documentation without uncovering concrete architectural or
  security insights.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.60  |
| impact_pred         | 95.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 7.5   |
| ev_pred             | 87.47 |

### Step Metrics Rationale

`EV = 0.92 * 95.0 + 0.5 * 7.5 - 0.3 * 0.60 - 3.5 = 87.40 + 3.75 - 0.18 - 3.5 = 87.47`. High impact and learning value;
establishes foundational quality roadmap for the autonomous coding platform.

---

## Step 6: Spin off dedicated task beans in control plane (.beans/) starting from 0092 (Bean 0044)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Scoped artifact creation in control plane; establishes discrete, prioritized task items for downstream
  execution.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Create `.beans/` directory and spin off dedicated task beans starting from `0092` corresponding to
  audit remediation priorities.
- **Git branch:**
  `I-1791983257-proxy-routing-and-codebase-audit/P-1791983277-antigravity-agent-gemini-3.8-flash-medium/E-1791983400-spinoff-beans/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create directory `.beans/` in repository root if it does not already exist.
2. Define standardized bean structure: frontmatter/header with bean ID, title, status (`proposed`), priority, category,
   components, context, problem statement, proposed remediation, acceptance criteria, and verification commands.
3. Spin off dedicated task beans starting from 0092:
   - `.beans/0092-sandbox-network-firewalling-and-egress-filtering.md`: Tighten Docker sandbox egress to prevent
     unwhitelisted outbound connections when proxy is inactive.
   - `.beans/0093-atomic-ledger-locking-and-concurrent-write-safety.md`: Implement file-locking and atomic append
     mechanisms for `executions.jsonl`, `intents.jsonl`, and `plans.jsonl`.
   - `.beans/0094-pipeline-checkpoint-schema-versioning-and-migration.md`: Add versioning and backward-compatible schema
     migrations for `FlowCheckpoint` state files.
   - `.beans/0095-agent-runner-session-state-and-credential-isolation.md`: Enhance session directory cleanup and secure
     scrubbing of agent API tokens in unprivileged runner processes.
4. Ensure all bean documents comply with CommonMark and pass Prettier check.

### Dependencies & Criticality

- **Depends on:** Step 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (Ledger immutability: task beans in `.beans/` must not
  alter ledger files), Bean 0044.
- **Potential failure modes for this step:** Bean numbering collisions or malformed markdown schemas.
- **Guardrails and early‑abort checks:** Confirm bean numbers start strictly from `0092` and are monotonically
  increasing.

### Success & Discard Criteria

- **Success:** `.beans/` contains well-formed task bean documents starting from 0092, providing clear scopes and
  acceptance criteria for remediation work.
- **Discard:** Beans lack concrete technical specifications or fail markdown linting.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.25  |
| impact_pred         | 88.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 4.8   |
| ev_pred             | 86.19 |

### Step Metrics Rationale

`EV = 0.97 * 88.0 + 0.5 * 4.8 - 0.3 * 0.25 - 1.5 = 85.36 + 2.40 - 0.075 - 1.5 = 86.185 -> 86.19`. Transforms audit
findings into actionable backlog items for continuous autonomous development.

---

## Step 7: End-to-end repository verification, formatting, and quality gates (Bean 0089, Bean 0044)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard end-to-end validation step ensuring zero regressions across all repository quality gates.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Execute full verification suite (`pytest`, `ruff`, `prettier`, `make lint`, `make tools`) and ensure
  all quality gates pass cleanly.
- **Git branch:**
  `I-1791983257-proxy-routing-and-codebase-audit/P-1791983277-antigravity-agent-gemini-3.8-flash-medium/E-1791983420-verification-gates/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run unit test suite: `uv run pytest apps/sandbox-executor/tests -m "not integration_test and not stress"` and verify
   all 549+ tests pass.
2. Run linter checks: `uv run ruff check .` and format checks: `uv run ruff format --check .`.
3. Run markdown formatting validation: `npx prettier --check "**/*.md"` across all documentation, plans, and beans.
4. Run Makefile targets: `make lint` and `make tools`.
5. Check `git status` to verify no untracked artifacts or uncommitted changes remain.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3, Step 4, Step 5, Step 6
- **Is Bottleneck:** YES (Final gate for intent completion)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1, §2, §3, `docs/safety.md` §1.
- **Potential failure modes for this step:** Prettier formatting failure on new markdown docs; lint regressions in
  modified Python source.
- **Guardrails and early‑abort checks:** Automatically run `npx prettier --write` on newly added markdown files before
  final verification check.

### Success & Discard Criteria

- **Success:** All test, lint, format, and Makefile verification commands return exit code 0.
- **Discard:** Any test failure or unformatted markdown file fails verification gate.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.35  |
| impact_pred         | 90.0  |
| cost_pred           | 1.6   |
| learning_value_pred | 3.5   |
| ev_pred             | 85.55 |

### Step Metrics Rationale

`EV = 0.95 * 90.0 + 0.5 * 3.5 - 0.3 * 0.35 - 1.6 = 85.50 + 1.75 - 0.105 - 1.6 = 85.545 -> 85.55`. Validates complete
alignment with repository invariants and intent goals.
