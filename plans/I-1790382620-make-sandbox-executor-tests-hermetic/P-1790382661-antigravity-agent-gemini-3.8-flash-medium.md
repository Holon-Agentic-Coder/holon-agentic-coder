# Plan for I-1790382620-make-sandbox-executor-tests-hermetic

- **Plan ID:** P-1790382661-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-09-26T00:31:01.086Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted empirical validation of fixture isolation and canary guard tests to
  safeguard live sandbox environments against destructive workspace wiping.
- **Safety priority level:** critical
- **Priority Justification:** Triggered by docs/safety.md §2 (Mandatory sandboxing and blast radius containment),
  docs/safety.md Invariant 2 (Sandboxing integrity), and holon-config/world/constraints.md §2 (Sandbox Containment
  Tiers). The unhermetic test suite touched live sandbox directories and deleted the host executor's own workspace
  (~/.holon-sandbox/workspace), directly compromising execution containment and leading to orphan commit fallbacks.

## Exploration

- **Proportion of steps that are exploratory:** 0.50
- **Justification:** Steps 2 and 3 incorporate balanced exploration to test hypotheses regarding complete local network
  decoupling (mocking bare git repositories) and canary guard assertions simulating container environments without
  relying on blind mocks.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 2.2   |
| impact_pred         | 92.0  |
| cost_pred           | 11.5  |
| learning_value_pred | 5.5   |
| ev_pred             | 71.55 |

### Strategy Rationale

The overall plan metrics were derived from the individual step-level metrics as follows:

- **p_success_pred**: 0.88. Sourced from the integration bottleneck in Step 2 (isolating integration tests from real
  network and git remotes while retaining test fidelity in containerized and offline environments).
- **entropy_pred**: 2.2. Calculated from the maximum step-level entropy (Step 2: 2.0) plus a 0.2 integration margin for
  cross-module fixture consistency. The total predicted sum of step entropies is 5.6, comfortably fitting inside the
  allocated entropy budget of 15.0.
- **impact_pred**: 92.0. Resolves the critical root cause of the bean 0019 incident where tests wiped the live sandbox
  workspace, eliminating destructive fallback loops and orphan root commits across all present and future agent
  executions.
- **cost_pred**: 11.5. Computed as the direct sum of individual step costs (3.0 + 3.5 + 3.0 + 2.0 = 11.5).
- **learning_value_pred**: 5.5. Epistemic gain from establishing repeatable fixture pinning patterns, hermetic git
  remote mocking for Docker integration tests, and affirmative boundary guard assertions.
- **ev_pred**: 71.55. Calculated strictly using the canonical config-driven Expected Value formula:
  $$EV = P(\text{success}) \times \text{Impact} + \mu \times \text{LearningValue} - \lambda \times \Delta S_{\text{intent}} - \text{Cost}$$
  With system constants $\lambda = 0.3$ and $\mu = 0.5$ from `holon-config/metrics/ev_config.json`:
  $$EV = 0.88 \times 92.0 + 0.5 \times 5.5 - 0.3 \times 2.2 - 11.5 = 80.96 + 2.75 - 0.66 - 11.5 = 71.55$$

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python target `==3.13.*`, package
    isolation, workspace structure under `apps/sandbox-executor`).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8 compliance, explicit static typing with
    `typing`, docstring requirements, no wildcard imports).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: unit tests located in `apps/sandbox-executor/tests/`,
    changes to test files explicitly planned).
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: prefix-based branch isolation, rebase
    discipline, commit boundaries).
  - `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: strict filesystem containment, prevention of
    escape actions and unwhitelisted modifications).
  - `docs/safety.md` §2 & Invariant 2 (Sandboxing mandatory for execution, containment to intent workspace).
- **Potential violations or edge cases:**
  - Running test discovery (`unittest discover` or `pytest`) inside `/home/holon/.holon-sandbox/workspace` before the
    fix is applied, which would delete the live workspace out from under the active executor.
  - Lingering unpatched calls to `get_workspace_dir()` or `cleanup_repo_dir()` in overlooked test helper methods.
  - Tests attempting outbound network calls or SSH authentication to `github.com` inside sandboxed environments.
  - Accidental removal of existing behaviour assertions verifying intent creation, slug formatting, or ledger payloads.
- **Mitigations built into the plan:**
  - Mandatory sandbox safety rail: all verification runs must occur strictly in a temporary scratch copy
    (`/tmp/holon-fixture`) with `HOLON_REPO_DIR=/tmp/holon-test-fixture`, never touching
    `/home/holon/.holon-sandbox/workspace`.
  - Comprehensive audit of all modules in `apps/sandbox-executor/tests/` to guarantee that every test invoking `main()`
    or cleanup functions explicitly pins `HOLON_REPO_DIR` to a fixture.
  - Replacing all remote git interactions in integration tests with local bare git repositories (`git init --bare`) or
    mocks, removing `~/.ssh` mount dependencies.
  - Adding affirmative invariant assertions in `_rmtree` / `_clear_dir_contents` test cases ensuring calls are strictly
    confined to the fixture directory path.
  - Adding a dedicated guard test asserting that marker files in the workspace path remain intact after executing
    entrypoint tests under simulated container environment variables (`HOLON_ROLE=executor`, `HOLON_IN_SANDBOX=1`).
- **Residual risk accepted (and why):**
  - Docker daemon availability: Docker-based integration tests (`test_intent_creator_integration.py`,
    `test_planner_integration.py`) require a running Docker daemon; if unavailable in the test environment, tests
    gracefully skip or rely on unit-level mocks.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 5.6
- **Budget Compliance:** The strategy fits within budget (Predicted Plan Entropy sum of 5.6 is well below the 15.0
  allocated budget).

## Plan Description & Strategy

This plan resolves the catastrophic workspace wipe hazard in `apps/sandbox-executor/tests` where unpatched calls to
`get_workspace_dir()` and `cleanup_repo_dir()` in unit tests resolved to the live container workspace
(`~/.holon-sandbox/workspace`) and deleted it during test execution.

The plan is divided into four coordinated steps:

1. **Audit and Pin Role Entrypoint Unit Tests:** Update `test_intent_creator.py`, `test_planner.py`, and
   `test_executor.py` so that all entrypoint invocations pin `HOLON_REPO_DIR` to a `tempfile.TemporaryDirectory` fixture
   and patch both `get_workspace_dir` and `cleanup_repo_dir` wherever they are not already hermetically isolated.
2. **Eliminate Real Network and Git Remote Side Effects:** Refactor integration tests
   (`test_intent_creator_integration.py`, `test_planner_integration.py`) to eliminate all outbound calls to `github.com`
   or real git remotes, utilizing local bare git repositories as test remotes.
3. **Assert Invariants and Implement Canary Guard Tests:** Add explicit assertions verifying that cleanup functions only
   operate within fixture boundaries, and implement a dedicated guard test running with `HOLON_ROLE=executor` and
   `HOLON_IN_SANDBOX=1` verifying that workspace marker files survive test execution.
4. **Documentation and Isolated Scratch Verification:** Add documentation in
   `apps/sandbox-executor/docs/hermetic_testing.md` detailing the fixture pinning convention, and perform end-to-end
   verification strictly within a temporary scratch copy (`/tmp/holon-fixture`) per the sandbox safety rail.

---

## Step 1: Audit and Hermetically Pin Role Entrypoint Unit Tests in apps/sandbox-executor/tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Localized refactoring of unit test fixtures with zero external dependencies and high certainty of
  success.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Audit every unit test module in `apps/sandbox-executor/tests` (specifically `test_intent_creator.py`,
  `test_planner.py`, `test_executor.py`, and `test_agent_runner.py`) and ensure every test calling role entrypoints or
  cleanup logic sets `HOLON_REPO_DIR` to a per-test temporary directory fixture and patches `get_workspace_dir` and
  `cleanup_repo_dir`.
- **Git branch:** I-1790382620-make-sandbox-executor-tests-hermetic/step1-pin-test-fixtures
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Audit `apps/sandbox-executor/tests/test_intent_creator.py`:
  - Update all test methods (`test_intent_creator_main`, `test_intent_creator_generate_branch_on_the_fly`,
    `test_intent_creator_with_target_branch`, `test_intent_creator_sanitize_slug_spaces`) to create a per-test
    `tempfile.TemporaryDirectory` fixture.
  - Pin the environment variable `HOLON_REPO_DIR` to the fixture directory using
    `unittest.mock.patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir})`.
  - Add explicit patches for `sandbox_executor.entrypoint.intent_creator.get_workspace_dir` returning the fixture path.
  - Ensure `cleanup_repo_dir` is patched or scoped strictly to the temporary fixture directory.
  - Verify that all existing behaviour assertions (branch prefix `I-`, slug sanitisation, `develop` target branch
    handling, and ledger entries in `holon-knowledge/ledger/intents.jsonl`) remain strictly intact and verified against
    the fixture ledger.
- Audit `apps/sandbox-executor/tests/test_planner.py`:
  - Inspect `test_planner_main` and `test_planner_main_fail_fast`.
  - Verify `HOLON_REPO_DIR` is set to a temporary fixture directory and patch
    `sandbox_executor.entrypoint.planner.get_workspace_dir` returning the fixture directory.
  - Ensure template and ledger file paths resolve inside the temporary fixture.
- Audit `apps/sandbox-executor/tests/test_executor.py`:
  - Review all test methods invoking `executor.main()` (`test_main_success`, `test_main_failure`, `test_main_fallback`,
    `test_main_keep_workspace`, `test_main_keep_workspace_existing_git`, `test_main_cleanup_default_mount`, etc.).
  - Ensure any test running with `is_default_repo` (where `HOLON_REPO_DIR` is cleared) mocks `get_workspace_dir` or
    `expanduser` to point to a temporary test fixture directory instead of the real `~/.holon-sandbox/workspace` or
    `~/.holon/repo`.
- Audit `apps/sandbox-executor/tests/test_agent_runner.py`:
  - In `test_get_workspace_dir_sandbox` and `test_get_workspace_dir_default`, ensure environment patches and
    `os.path.expanduser` patches are strictly isolated and cannot leak into subsequent test executions.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (Eliminating unpatched entrypoints is the foundational fix for the workspace deletion hazard)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (Testing Constraints), `docs/safety.md` §2 (Sandboxing).
- **Potential failure modes for this step:**
  - Omitting an unmocked call in a test helper method resulting in directory leaks.
  - Accidentally breaking intent creation assertions while restructuring test fixtures.
- **Guardrails and early‑abort checks:**
  - Verify all test assertions pass against local mock files.
  - Never run verification in the live workspace; run only inside a scratch copy.

### Success & Discard Criteria

- **Success:** All unit tests in `test_intent_creator.py`, `test_planner.py`, and `test_executor.py` execute without
  calling `_rmtree` or `cleanup_repo_dir` on `/home/holon/.holon-sandbox/workspace`, passing 100% of their existing
  assertions.
- **Discard:** Abort if fixture modifications alter the expected output schema or CLI arguments of the entrypoints.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.2   |
| impact_pred         | 80.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 74.14 |

### Step Metrics Rationale

High probability (0.95) and low entropy (1.2) as unit test fixture pinning is a well-understood, deterministic pattern.
Impact is high (80.0) as it directly eliminates the primary cause of live workspace deletion.

---

## Step 2: Decouple Integration Tests from Network and Real Git Remotes

- **Sub‑intent recommendation:** NO
- **Reasoning:** Self-contained refactor within integration test modules, manageable within single execution branch.
- **Step Type:** REFACTOR
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Integration tests can validate Docker entrypoint workflows and git interactions entirely
  offline against temporary local bare git repositories without real SSH keys or external network access.
- **Learning target:** Clean patterns for simulating remote git operations (`clone`, `fetch`, `push`) in isolated
  sandbox containers using local file-based bare repositories.
- **Maximum acceptable cost for this learning:** 3.5 cost units.

### Intent & Git Integration

- **Step Intent:** Remove all real network and remote git side-effects in
  `apps/sandbox-executor/tests/test_intent_creator_integration.py` and
  `apps/sandbox-executor/tests/test_planner_integration.py`, replacing remote pushes and SSH key mounts with local
  temporary bare git repositories or mocks.
- **Git branch:** I-1790382620-make-sandbox-executor-tests-hermetic/step2-isolate-integration-remotes
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Refactor `apps/sandbox-executor/tests/test_intent_creator_integration.py`:
  - Remove dependencies on real `~/.ssh` directory mounts (`ssh_dir = os.path.expanduser("~/.ssh")`).
  - Create a temporary bare git repository using `git init --bare` inside a `tempfile.TemporaryDirectory` fixture to act
    as the mock `origin` remote.
  - Initialize the bare repository with an initial commit containing a seeded `holon-knowledge/ledger/` directory and
    default branch.
  - Mount this bare repository into the docker container as a volume or configure the test environment to clone directly
    from the local filesystem path.
  - Replace `git push origin --delete` and `git fetch origin` calls targeting external remotes with operations against
    the local bare fixture.
  - Ensure the test verifies the pushed intent in the local bare remote without any external network access.
- Refactor `apps/sandbox-executor/tests/test_planner_integration.py`:
  - Eliminate the `~/.ssh` host mount requirement and remove any outbound SSH commands.
  - Configure the Docker container environment to use the local bare repository remote or mock git remote URL.
  - Verify that the fail-fast assertions (agent failure on missing keys or commands) execute cleanly without attempting
    network calls or remote git push/delete operations.
- Ensure that both test modules gracefully handle scenarios where the Docker daemon is absent or inaccessible by
  skipping with an informative message.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Ensures the suite can run in offline, network-isolated container sandboxes under
  `holon-config/world/constraints.md` §2)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §2 (Complete network isolation in container sandboxes),
  `docs/safety.md` §1 (Sandbox escape & network prevention).
- **Potential failure modes for this step:**
  - Docker container volume mounting permission issues when mounting host temporary directories into container
    workspaces.
  - Git protocol differences when cloning from file paths versus SSH URLs.
- **Guardrails and early‑abort checks:**
  - Set `GIT_CONFIG_GLOBAL` and git committer settings inside the fixture to avoid relying on host user configuration.
  - Ensure volume mounts use appropriate read-write permissions and clean up automatically.

### Success & Discard Criteria

- **Success:** Integration tests execute with complete network isolation (`NO_NETWORK=1` or disabled docker networking),
  successfully testing clone and commit behaviors against local bare repositories without reaching `github.com` or
  reading `~/.ssh`.
- **Discard:** Abort if Docker volume mounting constraints prevent offline execution, falling back to subprocess-level
  git mocks.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 2.0   |
| impact_pred         | 75.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 4.5   |
| ev_pred             | 64.15 |

### Step Metrics Rationale

Bottleneck step with moderate entropy (2.0) and cost (3.5) due to container volume orchestration and local bare
repository configuration. Delivers high learning value (4.5) by establishing fully offline integration test harnesses.

---

## Step 3: Implement Invariant Boundary Assertions and Canary Guard Tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Focused safety hardening and regression test implementation; builds directly upon Step 1 and Step 2.
- **Step Type:** TEST
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Invariant assertions in cleanup wrappers and dedicated canary marker tests will reliably
  catch any future attempt to delete or alter files outside designated fixture paths.
- **Learning target:** Developing non-intrusive safety guardrails that detect path traversal or environment leakage
  during test runs before destructive disk operations take place.
- **Maximum acceptable cost for this learning:** 3.0 cost units.

### Intent & Git Integration

- **Step Intent:** Add affirmative invariant assertions ensuring `_rmtree` / `_clear_dir_contents` cannot be called on
  paths outside the per-test fixture directory, and create a dedicated guard test that runs the entrypoint test modules
  under simulated live executor conditions (`HOLON_ROLE=executor`, `HOLON_IN_SANDBOX=1`) to confirm the workspace
  remains untouched.
- **Git branch:** I-1790382620-make-sandbox-executor-tests-hermetic/step3-guardrail-invariants
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/tests/test_agent_runner.py` and `apps/sandbox-executor/tests/test_executor.py`:
  - Enhance cleanup unit tests to assert the invariant: wrap or spy on `_rmtree` and `_clear_dir_contents` to verify
    that any path passed for cleanup is strictly a descendant of the allocated test fixture directory.
  - Assert that calling cleanup functions with paths outside the fixture raises a safety violation or fails the test
    assertion.
- Create a dedicated regression guard test module `apps/sandbox-executor/tests/test_sandbox_hermetic_guard.py`:
  - Create a temporary directory representing a simulated host sandbox workspace (`/tmp/simulated-sandbox/workspace`)
    containing a canary marker file (`canary.txt`).
  - Run the entrypoint test suites (`test_intent_creator`, `test_planner`, `test_executor`) in an environment configured
    with `HOLON_ROLE=executor`, `HOLON_IN_SANDBOX=1`, and `USER=holon`, with the simulated sandbox path configured as
    the default workspace.
  - Assert that after the complete test execution, the simulated sandbox workspace directory still exists.
  - Assert that the canary marker file (`canary.txt`) is still present and its contents are unmodified.
  - Verify that no orphan commits or repository re-initialization routines were triggered.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §2 (Sandbox containment), `holon-config/world/constraints.md` §2 (Sandbox escape
  actions).
- **Potential failure modes for this step:**
  - Canary guard tests interfering with other concurrent test runs if global environment variables are not strictly
    scoped.
- **Guardrails and early‑abort checks:**
  - Execute the simulated test run in an isolated subprocess with custom `env` dictionary to avoid polluting the runner
    process.

### Success & Discard Criteria

- **Success:** The guard test confirms the canary file survives 100% of entrypoint test invocations under active
  container environment conditions, and boundary invariant assertions pass.
- **Discard:** Abort if running the guard test modifies any actual user environment files.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.8   |
| impact_pred         | 85.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 75.46 |

### Step Metrics Rationale

High impact (85.0) and high learning value (5.0) by transforming implicit trust into verifiable, automated invariant
enforcement that prevents future regression of the bean 0019 incident.

---

## Step 4: Document Hermetic Testing Rules and Verify via Isolated Scratch Execution

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard documentation and final verification step; low complexity, zero risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add documentation in `apps/sandbox-executor/docs/hermetic_testing.md` detailing the fixture pinning
  rule for role entrypoint tests, and verify the entire test suite from a scratch copy per the sandbox safety rail.
- **Git branch:** I-1790382620-make-sandbox-executor-tests-hermetic/step4-docs-and-verification
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `apps/sandbox-executor/docs/hermetic_testing.md` (or add a dedicated section in
  `apps/sandbox-executor/docs/testing.md` / `apps/sandbox-executor/tests/README.md`):
  - Document the root cause of the live workspace deletion hazard (how `get_workspace_dir()` defaults to
    `~/.holon-sandbox/workspace` under container environments).
  - Codify the mandatory rule: every test invoking `main()`, `cleanup_repo_dir()`, or git operations must explicitly pin
    `HOLON_REPO_DIR` to a `tempfile.TemporaryDirectory` fixture and patch workspace discovery.
  - Provide clear guidelines on using local bare git repositories for integration testing instead of external remotes.
- Strictly adhere to the Sandbox Safety Rail during verification:
  - Export `HOLON_REPO_DIR=/tmp/holon-test-fixture`.
  - Copy the repository tree to `/tmp/holon-fixture`.
  - Execute targeted test modules from the scratch copy using:
    `PYTHONPATH=apps/sandbox-executor/src python3 -m unittest tests.test_intent_creator`
    `PYTHONPATH=apps/sandbox-executor/src python3 -m unittest tests.test_planner`
    `PYTHONPATH=apps/sandbox-executor/src python3 -m unittest tests.test_executor`
    `PYTHONPATH=apps/sandbox-executor/src python3 -m unittest tests.test_sandbox_hermetic_guard`
  - Execute full test discovery exclusively from the `/tmp/holon-fixture` directory using
    `python3 -m unittest discover -s tests`.
  - Never run full test discovery directly inside `/home/holon/.holon-sandbox/workspace`.
  - Verify code formatting and linting compliance across `apps/sandbox-executor`.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1 & §2 (PEP 8, docstrings, typing), Sandbox Safety Rail.
- **Potential failure modes for this step:**
  - Accidentally running test discovery in the live workspace instead of the `/tmp` scratch copy before verifying
    safety.
- **Guardrails and early‑abort checks:**
  - Verify current working directory is `/tmp/holon-fixture` before launching unittest discovery.

### Success & Discard Criteria

- **Success:** Documentation is published, 100% of tests pass in the scratch copy, live workspace marker files remain
  intact, and lint checks pass cleanly.
- **Discard:** Abort if any test fails in the scratch copy or if code quality checks fail.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.6   |
| impact_pred         | 65.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 62.52 |

### Step Metrics Rationale

Exploit step with very high success probability (0.98) and minimal entropy (0.6). Finalizes documentation and delivers
end-to-end proof of hermeticity while strictly honoring the sandbox safety rail.
