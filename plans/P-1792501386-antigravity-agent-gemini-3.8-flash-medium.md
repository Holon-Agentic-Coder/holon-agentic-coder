# Plan for I-1792501322-install-gh-in-agent-images-and-add-review-tests

- **Plan ID:** P-1792501386-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.4.2)
- **Created At:** 2026-10-20T13:03:06.723Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with focus on verifying non-root UID 1000 execution and multi-arch keyring caching
  across container layers.
- **Safety priority level:** standard
- **Priority Justification:** All proposed modifications operate within existing sandbox boundaries, adhering to
  non-root least privilege (`docs/safety.md#container-security`) and hermetic testing guidelines
  (`world/ruleset.md#hermetic-testing`).

## Exploration

- **Proportion of steps that are exploratory:** 0.25
- **Justification:** While Dockerfile package installation is well understood, validating non-root container environment
  behavior and diagnostic fallback paths under UID 1000 provides meaningful learning value without excess entropy.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 6.7   |
| impact_pred         | 88.0  |
| cost_pred           | 14.0  |
| learning_value_pred | 6.5   |
| ev_pred             | 64.68 |

### Strategy Rationale

The intent requests executing Bean 0131: installing the official GitHub CLI (`gh`) in the shared `holon-base` stage of
`apps/sandbox-executor/Dockerfile`, ensuring all derived agent images inherit it, updating review loop consensus unit
tests, and adding container availability tests.

Intent assessment confirms the goal has a high Expected Value (`EV = 64.68`), directly unblocking downstream automated
PR review workflows across all agent variants (antigravity, claude, gemini, opencode, pi, codex, orchestrator). No
reframing or decomposition into multi-branch sub-intents is required because each step is tightly bounded and verifiable
in a single cohesive branch.

Overall plan metrics derivation:

- Bottleneck identification: Step 3 (Container Availability & Non-Root Execution) represents the primary bottleneck
  (`p_success_pred = 0.90`) due to potential file permission or path resolution differences across multi-arch base
  images.
- Joint probability of success: `p_success_pred = 0.88`, calculated from the joint probability of sequential steps
  conditioned on early fail-fast unit tests.
- Cumulative entropy: `entropy_pred = 1.8 + 1.5 + 2.4 + 1.0 = 6.7`, well within the 15.0 budget.
- Cumulative cost: `cost_pred = 4.0 + 3.0 + 4.5 + 2.5 = 14.0`.
- Synergistic learning value: `learning_value_pred = 6.5`.
- Overall Expected Value: `EV = P(success) * Impact + mu * LearningValue - lambda * Entropy - Cost` with `mu = 0.5` and
  `lambda = 0.3`: `EV = 0.88 * 88.0 + 0.5 * 6.5 - 0.3 * 6.7 - 14.0 = 77.44 + 3.25 - 2.01 - 14.0 = 64.68`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `world/ruleset.md#hermetic-testing`: Unit test suites must not initiate live network calls or contact real GitHub
    APIs; tests must use mocks and fixtures.
  - `world/constraints.md#least-privilege`: All runtime container workloads must run as unprivileged user `holon` (UID
    1000, GID 1000). The `gh` binary and apt keys must have proper world-readable and world-executable permissions.
  - `docs/safety.md#multi-arch-compatibility`: Dockerfile packaging commands must support both `amd64` and `arm64`
    architectures dynamically.
- **Potential violations or edge cases:**
  - Package installation during image build failing if apt keyring directory lacks standard permissions or architecture
    string is hardcoded.
  - Review loop agent throwing unhandled exceptions if `gh` is absent or encounters network failures during PR consensus
    posting.
  - Test suites inadvertently attempting live container spin-ups in environments without Docker socket access.
- **Mitigations built into the plan:**
  - Using `[arch=$(dpkg --print-architecture) signed-by=...]` and storing the GPG keyring in
    `/etc/apt/keyrings/githubcli-archive-keyring.gpg` with `0644` permissions.
  - Unit tests in `test_review_loop.py` mock `shutil.which('gh')` and test both presence and absence branches with
    descriptive diagnostic messaging.
  - Container availability tests employ conditional skips (`pytest.mark.skipif`) when the Docker daemon is unavailable,
    falling back to hermetic layer inspection.
- **Residual risk accepted (and why):**
  - Upstream package repository changes: Standard APT pinning against official GitHub CLI repository mitigates key
    rotation risks.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 6.7
- **Budget Compliance:** The strategy fits within budget (6.7 <= 15.0).

## Plan Description & Strategy

The plan executes the installation of the official GitHub CLI into `holon-base` in `apps/sandbox-executor/Dockerfile`
and verifies downstream agent inheritance, review loop diagnostics, and non-root execution permissions across four
sequential, well-guarded steps.

---

## Step 1: Dockerfile Enhancement for Multi-Arch GitHub CLI Installation

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, direct configuration edit to `holon-base` in `Dockerfile` with immediate syntax validation and
  minimal blast radius.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Install official GitHub CLI (`gh`) into the `holon-base` stage using official Debian repository
  keyring and apt sources supporting multi-architecture builds.
- **Git branch:** `I-1792501322-install-gh-in-agent-images-and-add-review-tests`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Navigate to the `holon-base` build stage within `apps/sandbox-executor/Dockerfile`.
2. Ensure prerequisites (`curl`, `gpg`, `ca-certificates`) are installed in the base dependency layer.
3. Create the directory `/etc/apt/keyrings` with standard `0755` permissions if not already present.
4. Download the official GitHub CLI GPG key, de-armor it, and place it at
   `/etc/apt/keyrings/githubcli-archive-keyring.gpg` with world-readable `0644` permissions.
5. Add the signed official GitHub CLI APT repository to `/etc/apt/sources.list.d/github-cli.list` using dynamic
   architecture parameter `arch=$(dpkg --print-architecture)`.
6. Run `apt-get update` and install `gh` with `--no-install-recommends`.
7. Clean up APT caches (`/var/lib/apt/lists/*`) and temporary downloaded assets in the same RUN layer to keep image
   layers lean.
8. Validate that all downstream stages (`agent-antigravity`, `agent-claude`, `agent-gemini`, `agent-opencode`,
   `agent-pi`, `agent-codex`, `holon-orchestrator`) derive from `holon-base` without overriding or deleting
   `/usr/bin/gh`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (Downstream container tests depend on base image layer definition)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md#multi-arch-compatibility`, `world/constraints.md#layer-hygiene`
- **Potential failure modes for this step:** Network timeout fetching keyring during build, architecture mismatch on
  ARM64, uncleaned apt cache bloating layers.
- **Guardrails and early‑abort checks:** Docker build syntax check and layer linting before running full container
  builds.

### Success & Discard Criteria

- **Success:** `apps/sandbox-executor/Dockerfile` cleanly defines `gh` installation in `holon-base` with multi-arch APT
  source and GPG keyring configuration.
- **Discard:** Build syntax validation fails or introduces incompatible binary dependencies that break base Debian
  layer.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.8   |
| impact_pred         | 85.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 78.21 |

### Step Metrics Rationale

`EV = P(success) * Impact + mu * LearningValue - lambda * Entropy - Cost`
`EV = 0.95 * 85.0 + 0.5 * 4.0 - 0.3 * 1.8 - 4.0 = 80.75 + 2.00 - 0.54 - 4.0 = 78.21`. High probability of success due to
standard APT repository integration pattern; low entropy as standard practices are used.

---

## Step 2: Review Loop Unit Tests for GitHub CLI Detection & Fallback Diagnostics

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard unit test expansion in existing test suites (`test_review_loop.py` and `test_flow.py`) with
  complete hermetic isolation.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Implement hermetic unit tests verifying `post_pr_review_consensus` detection of `gh` availability via
  `shutil.which` and descriptive diagnostic messaging on absence or execution failure.
- **Git branch:** `I-1792501322-install-gh-in-agent-images-and-add-review-tests`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Inspect `post_pr_review_consensus` implementation in `apps/sandbox-executor/src/` to identify the `gh` execution and
   resolution logic.
2. Update `apps/sandbox-executor/tests/test_review_loop.py` with test cases:
   - When `shutil.which('gh')` returns a valid path: assert review loop proceeds to execute PR comment or consensus
     review command with mocked subprocess.
   - When `shutil.which('gh')` returns `None`: assert review loop logs or returns a clear diagnostic message indicating
     `gh` is unavailable in the environment without raising an unhandled exception.
   - When `gh` execution returns a non-zero exit code: assert error output is captured and surfaced with diagnostic
     context.
3. Update `apps/sandbox-executor/tests/test_flow.py` to ensure overall review workflow cleanly handles the presence and
   absence of `gh` without crashing orchestrator flows.
4. Ensure all new tests use hermetic mocks for `shutil.which` and `subprocess.run`, avoiding external calls.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `world/ruleset.md#hermetic-testing`, `docs/safety.md#error-handling`
- **Potential failure modes for this step:** Tests making accidental live process calls or mock leakage across test
  cases.
- **Guardrails and early‑abort checks:** Hermetic fixtures strictly isolate environment and mock boundaries.

### Success & Discard Criteria

- **Success:** All unit tests in `test_review_loop.py` and `test_flow.py` pass hermetically, validating `gh` detection
  and diagnostic reporting.
- **Discard:** Test suite regressions or non-hermetic network dependencies detected.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 1.5   |
| impact_pred         | 80.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.5   |
| ev_pred             | 75.10 |

### Step Metrics Rationale

`EV = P(success) * Impact + mu * LearningValue - lambda * Entropy - Cost`
`EV = 0.96 * 80.0 + 0.5 * 3.5 - 0.3 * 1.5 - 3.0 = 76.80 + 1.75 - 0.45 - 3.0 = 75.10`. Unit test authoring in existing
pytest harnesses carries very low risk and high reliability.

---

## Step 3: Container Availability & Non-Root Execution Verification

- **Sub‑intent recommendation:** NO
- **Reasoning:** Bounded container-level verification test that validates permission inheritance across agent images
  without warranting sub-intent overhead.
- **Step Type:** TEST
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Installing `gh` at `/usr/bin/gh` via apt in `holon-base` guarantees that non-root user
  `holon` (UID 1000) can execute `which gh` and `gh --version` across all downstream agent container targets without
  file permission or environment PATH degradation.
- **Learning target:** Verify whether any agent-specific layers (e.g. customized user profiles, PATH overrides, or
  stripped permissions) interfere with `gh` availability for UID 1000.
- **Maximum acceptable cost for this learning:** `cost <= 5.0`

### Intent & Git Integration

- **Step Intent:** Add test cases in `apps/sandbox-executor/tests/test_container_images.py` (or `test_review_loop.py`)
  verifying `which gh` and `gh --version` succeed as non-root user `holon` (UID 1000).
- **Git branch:** `I-1792501322-install-gh-in-agent-images-and-add-review-tests`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Review `apps/sandbox-executor/tests/test_container_images.py` and `test_build_all_images.py` for existing container
   inspection and test patterns.
2. Add a parameterized test checking agent targets: `agent-antigravity`, `agent-claude`, `agent-gemini`,
   `agent-opencode`, `agent-pi`, `agent-codex`, and `holon-orchestrator`.
3. In unit/mock mode: verify image inspect configuration contains non-root UID 1000 user directive and that `gh` is
   positioned in default system binary paths (`/usr/bin/gh`).
4. In container execution mode (guarded by docker environment check): run
   `docker run --rm -u 1000:1000 <image> sh -c "which gh && gh --version"` and assert exit code is 0 with valid version
   string output.
5. Implement graceful skip markers (`@pytest.mark.skipif(not docker_available)`) to guarantee tests remain hermetic in
   non-daemon CI environments.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (Validates the primary functional contract of the intent)

### Safety & Constraint Considerations

- **Relevant rules:** `world/constraints.md#least-privilege`, `docs/safety.md#container-security`
- **Potential failure modes for this step:** Docker daemon unavailability causing unhandled errors, UID 1000 permission
  denial on `/usr/bin/gh` or config directories.
- **Guardrails and early‑abort checks:** Fallback to image inspection when container runtime is unprivileged or
  unavailable.

### Success & Discard Criteria

- **Success:** Non-root execution of `which gh` and `gh --version` passes cleanly across all built agent targets.
- **Discard:** Container execution fails due to root-only permission restrictions or missing PATH bindings.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 2.4   |
| impact_pred         | 82.0  |
| cost_pred           | 4.5   |
| learning_value_pred | 6.0   |
| ev_pred             | 71.58 |

### Step Metrics Rationale

`EV = P(success) * Impact + mu * LearningValue - lambda * Entropy - Cost`
`EV = 0.90 * 82.0 + 0.5 * 6.0 - 0.3 * 2.4 - 4.5 = 73.80 + 3.00 - 0.72 - 4.5 = 71.58`. Slightly higher entropy (`2.4`)
reflecting the exploration of multi-stage container permission nuances under UID 1000.

---

## Step 4: Quality Gate & Hermetic Verification

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard pre-commit and CI verification step confirming no regressions.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Run repository linter, code formatter checks, and full pytest suite to verify all unit and container
  tests pass cleanly.
- **Git branch:** `I-1792501322-install-gh-in-agent-images-and-add-review-tests`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Execute `make lint` to check shell scripts, Dockerfiles, and repo formatting standards.
2. Execute `uv run ruff check .` across the workspace to assert code style and import cleanliness.
3. Execute `uv run pytest` targeting `apps/sandbox-executor/tests/` to confirm all existing and newly added tests pass
   without failure or warnings.
4. Verify no untracked artifacts, cache remnants, or temporary files remain in the workspace.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `world/ruleset.md#clean-ci`, `docs/safety.md#verification-integrity`
- **Potential failure modes for this step:** Ruff formatting discrepancies or latent test timeouts.
- **Guardrails and early‑abort checks:** Stop execution immediately upon any linter or test failure to prevent faulty
  commits.

### Success & Discard Criteria

- **Success:** All linter checks (`make lint`, `uv run ruff check .`) and `uv run pytest` pass with 0 exit code.
- **Discard:** Persistent test failures or lint regressions that cannot be resolved within cost budget.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 1.0   |
| impact_pred         | 75.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 71.70 |

### Step Metrics Rationale

`EV = P(success) * Impact + mu * LearningValue - lambda * Entropy - Cost`
`EV = 0.98 * 75.0 + 0.5 * 2.0 - 0.3 * 1.0 - 2.5 = 73.50 + 1.00 - 0.30 - 2.5 = 71.70`. Deterministic verification step
with high success probability and minimal entropy.
