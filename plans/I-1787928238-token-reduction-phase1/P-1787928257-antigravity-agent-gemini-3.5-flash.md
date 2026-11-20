# Plan for I-1787928238-token-reduction-phase1

- **Plan ID:** P-1787928257-antigravity-agent-gemini-3.5-flash
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.5-flash (version: 1.1.22)
- **Created At:** 2026-08-28T14:44:24Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** conservative with 1–2 sentence justification. We choose a conservative stance as the
  requirements are clear and the task is to establish baseline CA and proxy routing functionality without high-risk
  modifications to core orchestration logic.
- **Safety priority level:** standard
- **Priority Justification:** Standard level is appropriate because we are modifying only the CLI wrapper to mount certs
  and forward proxy environment variables without altering the core sandbox execution boundaries or privilege
  requirements.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** No exploratory steps are needed because generating self-signed certificates and mounting files via
  docker run is a standard, deterministic operation with minimal uncertainty.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 7.5   |
| impact_pred         | 80    |
| cost_pred           | 35    |
| learning_value_pred | 4.0   |
| ev_pred             | 36.75 |

### Strategy Rationale

We accept the intent as-is because it sets up the mandatory trust bootstrap foundation for MITM proxy interception. The
overall plan metrics are aggregated from individual steps: overall success probability is determined by the bottleneck
step (Step 2, which involves complex env var forwarding and Docker command modification); overall entropy is the sum of
step entropies; cost is the sum of step costs; impact and learning value are the maximum of step impacts and learning
values, respectively. The resulting EV is positive (36.75), indicating high feasibility and value.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `constraints.md#2` (Sandbox Containment Tiers) - Filesystem mounts must be read-only where possible, and we must not
    violate container sandboxing.
  - `ruleset.md#3` (Testing Constraints) - We must add proper tests and not modify unrelated code.
- **Potential violations or edge cases:**
  - Exposing host sensitive credentials or keys to the container sandbox.
  - Mounting directories outside the allowed ones or using write permissions where read-only is sufficient.
- **Mitigations built into the plan:**
  - The Root CA certificate is mounted strictly read-only (`:ro`).
  - Sensitive proxy credentials are kept secure and only standard environment variables are forwarded.
- **Residual risk accepted (and why):** None, as all code changes are isolated to CLI wrapper parameters and standard
  OpenSSL subprocessing on the host.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 7.5
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

The strategy is to implement Root CA certificate generation and validation on the host using the pre-installed `openssl`
command-line utility. Once generated, we mount this certificate into the container's standard CA location and set
proxy-related environment variables so that any containerized processes automatically trust the CA and route their TLS
connections through the intercepting proxy. Finally, we implement tests to verify these commands and variables are
constructed correctly.

---

## Step 1: Implement Root CA Generation Utility

- **Sub‑intent recommendation:** NO
- **Reasoning:** Simple, low-risk Python utility wrapper around standard openssl CLI, not reusable outside
  sandbox-executor scope.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Implement host-side Root CA certificate generation and verification in sandbox-executor.
- **Git branch:** I-1787928238-token-reduction-phase1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create or modify a module in `sandbox_executor` (e.g. `sandbox_executor/cert.py` or within `cli.py` directly) to
   define `ensure_root_ca()`.
2. Define `HOLON_CERTS_DIR` defaulting to `~/.holon/certs`.
3. If `ca.key` and `ca.crt` do not exist in that directory:
   - Invoke `openssl genrsa -out ca.key 4096` via subprocess.
   - Invoke `openssl req -new -x509 -days 3650 -key ca.key -out ca.crt -subj "/CN=Holon Root CA/O=Holon/OU=Sandbox"` via
     subprocess.
4. Verify files were created and return their absolute paths.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** safety.md#3 (Trust is earned, not granted), constraints.md#2 (Process Sandbox subprocess
  boundaries)
- **Potential failure modes for this step:**
  - `openssl` binary missing on the host.
  - Write permission denied in `~/.holon/certs`.
- **Guardrails and early‑abort checks:**
  - Check for `openssl` command availability using `shutil.which` before execution.
  - Trap process execution errors and log detailed error messages before raising a RuntimeError.

### Success & Discard Criteria

- **Success:** `ensure_root_ca()` returns valid file paths to generated Root CA certificate and private key.
- **Discard:** subprocess execution fails repeatedly or `openssl` cannot be found on host.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 2.5   |
| impact_pred         | 60    |
| cost_pred           | 10    |
| learning_value_pred | 3.0   |
| ev_pred             | 47.75 |

### Step Metrics Rationale

This step uses standard commands and has a very high success probability (0.95) and low entropy (2.5) since `openssl` is
already confirmed to be present on the system and files are generated in standard agent state paths.

---

## Step 2: CLI Wrapper Integration for Mounts and Proxy Environment Variables

- **Sub‑intent recommendation:** NO
- **Reasoning:** Straightforward integration of volume mounts and environment variable forwarding in CLI execution
  method.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Modify the Docker execution wrapper to mount the CA certificate and forward proxy environment
  variables.
- **Git branch:** I-1787928238-token-reduction-phase1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Import the Root CA utility in `sandbox_executor/cli.py`.
2. Within `run_docker_container()`, invoke `ensure_root_ca()` to get `ca.crt` path.
3. Append a read-only Docker volume mount: `-v <ca.crt_path>:/etc/ssl/certs/holon-ca.crt:ro`.
4. Append container environment variables:
   - `REQUESTS_CA_BUNDLE=/etc/ssl/certs/holon-ca.crt`
   - `CURL_CA_BUNDLE=/etc/ssl/certs/holon-ca.crt`
   - `SSL_CERT_FILE=/etc/ssl/certs/holon-ca.crt`
   - `NODE_EXTRA_CA_CERTS=/etc/ssl/certs/holon-ca.crt`
5. Read proxy environment variables (`HTTP_PROXY`, `http_proxy`, `HTTPS_PROXY`, `https_proxy`, `NO_PROXY`, `no_proxy`)
   from the host environment and forward them to the container command args if present.
6. Support `HOLON_` prefixed proxy variable overrides (`HOLON_HTTP_PROXY`, `HOLON_HTTPS_PROXY`, `HOLON_NO_PROXY`) and
   map them to their corresponding standard proxy environment variables inside the container.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** constraints.md#2 (Container Sandbox constraints)
- **Potential failure modes for this step:**
  - Docker daemon configuration prevents mounting from `~/.holon/certs`.
  - Misformatted proxy URL syntax breaking docker command parsing.
- **Guardrails and early‑abort checks:**
  - Use `os.path.abspath` to ensure absolute path mounts.
  - Keep the mount read-only (`:ro`) to prevent sandbox processes from tampering with the certificate.

### Success & Discard Criteria

- **Success:** CLI correctly builds and prints the Docker run command containing the certificate mount and
  forwarded/overridden proxy variables.
- **Discard:** CLI execution fails or command argument lists are malformed.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 3.0   |
| impact_pred         | 80    |
| cost_pred           | 15    |
| learning_value_pred | 4.0   |
| ev_pred             | 58.1  |

### Step Metrics Rationale

Step 2 is the core delivery of the intent with high impact (80) and slightly higher entropy (3.0) due to string
formatting and environment mapping, but still has a very high success probability (0.90).

---

## Step 3: Implement Unit and Integration Tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard testing procedure for CLI arguments and environment parsing.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add test cases in `apps/sandbox-executor/tests/test_cli.py` to assert correct cert generation and
  docker command assembly.
- **Git branch:** I-1787928238-token-reduction-phase1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add `test_ensure_root_ca` in `test_cli.py` mocking file checks and `subprocess.run` to verify openssl commands are
   invoked correctly.
2. Add `test_run_docker_container_with_proxy_and_ca` mocking `ensure_root_ca` and `subprocess.run`. Assert that:
   - Volume mount `-v` points to the certificate path.
   - CA bundle variables are present in docker args.
   - Proxy env variables and `HOLON_` proxy overrides are correctly forwarded as `-e` arguments.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md#3 (Testing Constraints)
- **Potential failure modes for this step:**
  - Mock collision with other CLI tests.
- **Guardrails and early‑abort checks:**
  - Clean up any temporary directories used in tests.

### Success & Discard Criteria

- **Success:** All new and existing tests pass.
- **Discard:** Test failures that cannot be resolved within budget.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 2.0   |
| impact_pred         | 70    |
| cost_pred           | 10    |
| learning_value_pred | 3.0   |
| ev_pred             | 57.4  |

### Step Metrics Rationale

Testing has high success probability and low risk/entropy (2.0) while providing substantial regression protection.

---
