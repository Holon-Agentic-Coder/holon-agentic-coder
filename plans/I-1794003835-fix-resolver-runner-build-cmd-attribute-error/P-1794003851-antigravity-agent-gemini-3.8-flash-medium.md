# Plan for I-1794003835-fix-resolver-runner-build-cmd-attribute-error

- **Plan ID:** P-1794003851-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.5.2)
- **Created At:** 2026-11-06T22:24:11.864Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted focus on eliminating free-form test mocks, enforcing real interface
  contracts, and guaranteeing runtime resolver stability.
- **Safety priority level:** standard
- **Priority Justification:** The plan refactors internal runner interfaces, fallback invocation in the review loop
  resolver, and unit test assertions without altering external network configurations, security boundaries, or
  credentials.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** Well-scoped defect fix and unit test refactoring with deterministic contracts and known
  mock-reduction targets.

## Overall Plan Metrics

| metric              | value  |
| ------------------- | ------ |
| p_success_pred      | 0.96   |
| entropy_pred        | 2.10   |
| impact_pred         | 100.00 |
| cost_pred           | 4.50   |
| learning_value_pred | 6.00   |
| ev_pred             | 93.87  |

### Strategy Rationale

The intent is accepted as-is (`ACCEPT_AS_IS`). The defect surfaced when `_run_resolver_agent()` in
`apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py` attempted to call
`runner.build_command(prompt, model_name)` on an instance of `AntigravityAgentRunner`. While `StandardAgentRunner` and
`AntigravityAgentRunner` implemented `build_cmd` and `compose_cmd`, `build_command` was never defined on `AgentRunner`
or any of its subclasses. This missing method went undetected in CI because unit tests in
`apps/sandbox-executor/tests/test_review_loop.py` used unconstrained `MagicMock()` objects rather than real runner
instances or autospecs. Consequently, Stage 4 resolver containers crashed at runtime with
`AttributeError: 'AntigravityAgentRunner' object has no attribute 'build_command'`.

The selected strategy addresses both the defect and the testing gap across four focused steps:

1. **Implement `build_command` on Base `AgentRunner` & Resolver Fallback Robustness:** Define
   `build_command(self, prompt: str, model_name: str) -> list[str]` on `AgentRunner` that directly delegates to
   `self.compose_cmd(model_name, prompt)`. In `_run_resolver_agent()`, cleanly invoke
   `runner.build_command(prompt, model_name)` or fall back to `runner.compose_cmd(model_name, prompt)` to guarantee
   resilience even with alternative runner implementations.
2. **Minimise Mocking & Test Real Runner Contracts across Registry:** In
   `apps/sandbox-executor/tests/test_agent_runner.py`, iterate over all real registered runners in the `runners`
   registry dictionary without mocks and assert that `runner.build_command('prompt', 'model')` returns the exact argv
   list of `runner.compose_cmd('model', 'prompt')`.
3. **Eliminate Free-Form Mocks in Review Loop Resolver Tests:** In `apps/sandbox-executor/tests/test_review_loop.py`,
   replace free-form `MagicMock()` runner fixtures with real runner instances or `create_autospec(AgentRunner)`. Add
   explicit assertions that `_run_resolver_agent()` executes cleanly without `AttributeError` when provided real runner
   instances, including `AntigravityAgentRunner`.
4. **Verification & Quality Gate Execution:** Execute targeted unit tests first via
   `uv run pytest apps/sandbox-executor/tests/test_agent_runner.py apps/sandbox-executor/tests/test_review_loop.py`,
   execute the full test suite once via `uv run task test`, and verify code formatting and documentation hygiene via
   `uv run task lint` and `uv run task lint-docs`.

**Derivation of Overall Metrics:**

- `p_success_pred`: Derived with Step 3 as the bottleneck step (`p_success_pred = 0.96`). The compound probability
  across all steps is `0.98 * 0.97 * 0.96 * 0.99 = 0.903`, with an overall qualitative estimate of `0.96` based on
  localized logic modifications and deterministic interface contracts.
- `entropy_pred`: Derived by summing step entropies (`0.60 + 0.50 + 0.70 + 0.30 = 2.10`), which fits comfortably within
  the allocated entropy budget of `15.0`.
- `impact_pred`: Derived by summing step impacts (`35.00 + 25.00 + 30.00 + 10.00 = 100.00`), reflecting total
  elimination of runtime resolver container crashes and hardening CI against over-mocking defects.
- `cost_pred`: Derived by summing step resource costs (`1.20 + 1.10 + 1.40 + 0.80 = 4.50`).
- `learning_value_pred`: Derived by summing step learning values (`1.80 + 1.70 + 2.00 + 0.50 = 6.00`), capturing
  institutional knowledge regarding real-contract testing and mock minimisation.
- `ev_pred`: Derived using the system configuration formula
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost` where `mu = 0.5` and `lambda = 0.3`:
  `EV = 0.96 * 100.00 + 0.5 * 6.00 - 0.3 * 2.10 - 4.50 = 96.00 + 3.00 - 0.63 - 4.50 = 93.87`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md#1`: Python runtime `==3.13.*`, dependencies managed strictly via `uv` at the
    repository root. Zero new dependencies introduced.
  - `holon-config/world/ruleset.md#2`: Australian English spelling, PEP 8 compliance, strict typing annotations, and
    explanatory docstrings for all modified methods.
  - `holon-config/world/ruleset.md#3`: Testing via `pytest==9.1.1` (`uv run pytest`); unit test updates scoped strictly
    to `test_agent_runner.py` and `test_review_loop.py`.
  - `holon-config/world/constraints.md#1` & `docs/safety.md#1`: Git isolation under intent branch
    `I-1794003835-fix-resolver-runner-build-cmd-attribute-error`; merging to `main` remains strictly human-maintainer
    gated.
  - `holon-config/world/constraints.md#3`: Append-only immutability of the three ledgers (`intents.jsonl`,
    `plans.jsonl`, `executions.jsonl`).
- **Potential violations or edge cases:**
  - Inconsistent argument order between `build_command(prompt, model_name)` and `compose_cmd(model_name, prompt)`
    causing malformed command lines; mitigated by base class delegation and direct unit test assertions.
  - Custom runners or mocks lacking `build_command` in third-party extensions; mitigated by defensive fallback
    invocation in `_run_resolver_agent()`.
  - Over-constraining mocks in tests leading to brittle unit tests; mitigated by testing real registered runner
    instances directly.
- **Mitigations built into the plan:**
  - Implementing `build_command` directly on the base class `AgentRunner` so all subclasses inherit the method.
  - Adding defensive fallback in `_run_resolver_agent()` checking for `build_command` before falling back to
    `compose_cmd`.
  - Iterating over all real registered runners in `test_agent_runner.py` without mocking to ensure contract fidelity.
  - Replacing unconstrained `MagicMock()` with real runner instances or `create_autospec(AgentRunner)`.
- **Residual risk accepted (and why):**
  - Minimal residual risk: Adding an inherited delegation method and replacing free-form mocks with real contracts is a
    low-risk, high-safety refactoring that aligns with architectural invariants.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 2.10
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

The plan resolves the Stage 4 resolver container crash caused by missing `build_command` on agent runners and eliminates
the testing anti-pattern (over-mocking) that allowed the defect to escape into production:

1. **Step 1:** Define `build_command(self, prompt: str, model_name: str) -> list[str]` on `AgentRunner` in
   `apps/sandbox-executor/src/sandbox_executor/agent_runner.py` delegating to `self.compose_cmd(model_name, prompt)`.
   Update `_run_resolver_agent()` in `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py` to cleanly
   invoke `runner.build_command(prompt, model_name)` or fall back to `runner.compose_cmd(model_name, prompt)`.
2. **Step 2:** Refactor `apps/sandbox-executor/tests/test_agent_runner.py` to iterate over all registered runners in
   `runners` dictionary without mocks, asserting that `runner.build_command('prompt', 'model')` identically matches
   `runner.compose_cmd('model', 'prompt')`.
3. **Step 3:** Refactor `apps/sandbox-executor/tests/test_review_loop.py` to eliminate free-form `MagicMock()` objects
   in resolver execution tests, replacing them with real runner instances or `create_autospec(AgentRunner)`, and
   asserting that `_run_resolver_agent()` executes cleanly without `AttributeError`.
4. **Step 4:** Execute the verification suite with
   `uv run pytest apps/sandbox-executor/tests/test_agent_runner.py apps/sandbox-executor/tests/test_review_loop.py`,
   `uv run task test`, `uv run task lint`, and `uv run task lint-docs`.

---

## Step 1: Implement `build_command` on base `AgentRunner` and enhance resolver agent fallback invocation in `review_loop.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Localized implementation in core runner and entrypoint modules with low entropy (`0.60`), high
  probability of success (`0.98`), and minimal blast radius.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Define `build_command(self, prompt: str, model_name: str) -> list[str]` on `AgentRunner` delegating
  to `self.compose_cmd(model_name, prompt)`, and update `_run_resolver_agent()` in `review_loop.py` to cleanly invoke
  `build_command` or fall back to `compose_cmd`.
- **Git branch:**
  `I-1794003835-fix-resolver-runner-build-cmd-attribute-error/P-1794003851-antigravity-agent-gemini-3.8-flash-medium/E-step1-build-command-base-runner`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/src/sandbox_executor/agent_runner.py`:
  - Locate base class `AgentRunner`.
  - Add docstring and signature for `build_command(self, prompt: str, model_name: str) -> list[str]`.
  - Inside `build_command`, delegate directly to `self.compose_cmd(model_name, prompt)`.
  - Ensure signature uses strict static typing annotations adhering to PEP 8 and project conventions.
  - Verify that all runner subclasses (`StandardAgentRunner`, `AntigravityAgentRunner`, etc.) inherit `build_command`
    seamlessly without requiring individual overrides.
- In `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py`:
  - Locate
    `_run_resolver_agent(repo_dir: str, agent_name: str, model_name: str, prompt: str, dispositions_path: str) -> list[dict[str, Any]]`.
  - Inspect the invocation of runner command construction.
  - Replace direct `runner.build_command(prompt, model_name)` with robust invocation:
    - Check if `runner` implements callable `build_command`; if so, invoke `runner.build_command(prompt, model_name)`.
    - If `build_command` is absent, check if `runner` implements callable `compose_cmd`; if so, invoke
      `runner.compose_cmd(model_name, prompt)`.
    - If neither method is available, raise an informative `AttributeError` indicating that the runner does not satisfy
      the command construction contract.
  - Ensure no unexpected exceptions occur when dispatching commands for registered runners.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#1`, `holon-config/world/ruleset.md#2`, `docs/safety.md#1`
- **Potential failure modes for this step:** Inconsistent argument ordering between `build_command(prompt, model_name)`
  and `compose_cmd(model_name, prompt)`; mitigated by base class delegation and unit test validation.
- **Guardrails and early‑abort checks:** Statically verify type annotations and check method callability prior to
  invocation.

### Success & Discard Criteria

- **Success:** `AgentRunner` and `AntigravityAgentRunner` provide `build_command(prompt, model_name)` returning valid
  argv lists matching `compose_cmd(model_name, prompt)`, and `_run_resolver_agent()` handles both methods cleanly.
- **Discard:** Discard if cost exceeds `1.5 * cost_pred` (`1.80`) or breaks backwards compatibility with existing runner
  invocation patterns.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.60  |
| impact_pred         | 35.00 |
| cost_pred           | 1.20  |
| learning_value_pred | 1.80  |
| ev_pred             | 33.82 |

### Step Metrics Rationale

- `p_success_pred`: Very high (`0.98`) because method delegation on base classes and fallback checks are standard,
  low-complexity Python operations.
- `entropy_pred`: Low (`0.60`) as changes are contained within existing helper methods.
- `impact_pred`: Significant (`35.00`) because it directly fixes the runtime container crash root cause.
- `cost_pred`: Minimal (`1.20`) requiring straightforward code additions.
- `learning_value_pred`: Moderate (`1.80`) establishing robust fallback patterns for agent runners.
- `ev_pred`: Calculated via code span:
  `EV = 0.98 * 35.00 + 0.5 * 1.80 - 0.3 * 0.60 - 1.20 = 34.30 + 0.90 - 0.18 - 1.20 = 33.82`.

---

## Step 2: Test real runner contracts across registry in `test_agent_runner.py` without mocks

- **Sub‑intent recommendation:** NO
- **Reasoning:** Focused test hardening verifying real registered runners without external dependencies or mocks.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Iterate over all real registered runners in `runners` dictionary in
  `apps/sandbox-executor/tests/test_agent_runner.py` asserting that `runner.build_command('prompt', 'model')` returns
  the exact argv list of `runner.compose_cmd('model', 'prompt')` without using mocks.
- **Git branch:**
  `I-1794003835-fix-resolver-runner-build-cmd-attribute-error/P-1794003851-antigravity-agent-gemini-3.8-flash-medium/E-step2-test-real-runner-contracts`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/tests/test_agent_runner.py`:
  - Import the `runners` registry dictionary from `sandbox_executor.agent_runner`.
  - Author a new test function `test_real_registered_runners_build_command_contract()`.
  - Iterate through every registered runner in `runners.items()` without applying any mock wrappers or monkeypatching on
    the runner instances.
  - For each real runner, execute `cmd_build = runner.build_command("test-prompt", "test-model")`.
  - Execute `cmd_compose = runner.compose_cmd("test-model", "test-prompt")`.
  - Assert strict equality: `assert cmd_build == cmd_compose`.
  - Assert that the returned command line is a list of strings containing the executable binary, model flag, model name,
    and prompt.
  - Specifically verify that `AntigravityAgentRunner`, `StandardAgentRunner`, and all agent mappings (`pi`, `claude`,
    `gemini`, `opencode`, `codex`, `antigravity-agent`) satisfy the contract without throwing `AttributeError`.
  - Verify that runtime environment evaluation (such as `HOLON_AGENT_EFFORT` and `HOLON_AGENT_SKIP_PERMISSIONS`)
    operates identically across both invocations.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3`, `docs/safety.md#2`
- **Potential failure modes for this step:** Real runner validation failing due to missing environment keys during test
  execution; mitigated by testing `compose_cmd` and `build_command` which assemble argv without requiring remote API
  credentials.
- **Guardrails and early‑abort checks:** Ensure no real subprocess execution or network requests are triggered during
  unit test execution.

### Success & Discard Criteria

- **Success:** All registered runners in `runners` dictionary pass contract assertions asserting `build_command` matches
  `compose_cmd` identically without any runner mocks.
- **Discard:** Discard if cost exceeds `1.5 * cost_pred` (`1.65`) or tests require external live credentials.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.50  |
| impact_pred         | 25.00 |
| cost_pred           | 1.10  |
| learning_value_pred | 1.70  |
| ev_pred             | 23.85 |

### Step Metrics Rationale

- `p_success_pred`: High (`0.97`) as contract comparison across local runner objects is deterministic and hermetic.
- `entropy_pred`: Low (`0.50`) with zero modification to production business logic.
- `impact_pred`: Substantial (`25.00`) by enforcing registry-wide contract compliance.
- `cost_pred`: Minimal (`1.10`) requiring standard unit test additions.
- `learning_value_pred`: Moderate (`1.70`) providing a pattern for contract testing without mocks.
- `ev_pred`: Calculated via code span:
  `EV = 0.97 * 25.00 + 0.5 * 1.70 - 0.3 * 0.50 - 1.10 = 24.25 + 0.85 - 0.15 - 1.10 = 23.85`.

---

## Step 3: Eliminate free-form runner mocks in `test_review_loop.py` and verify resolver agent real contracts

- **Sub‑intent recommendation:** NO
- **Reasoning:** Refactoring test suite to replace unconstrained `MagicMock` instances with real runners and
  `create_autospec(AgentRunner)`.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** In `apps/sandbox-executor/tests/test_review_loop.py`, replace free-form mock runner objects with real
  runner instances or `create_autospec(AgentRunner)` to guarantee contract fidelity and verify resolver agent execution.
- **Git branch:**
  `I-1794003835-fix-resolver-runner-build-cmd-attribute-error/P-1794003851-antigravity-agent-gemini-3.8-flash-medium/E-step3-refactor-review-loop-mocks`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/tests/test_review_loop.py`:
  - Locate `TestResolverAgentExecution` class (around lines 2253-2292).
  - In `test_run_resolver_agent_builds_command_and_invokes_run_turn`:
    - Replace unconstrained `mock_runner = MagicMock()` with either a real runner instance obtained via
      `get_runner("antigravity-agent")` or `create_autospec(AgentRunner, instance=True)`.
    - When using a real runner instance, configure the patch on `get_runner` to return the real runner directly.
    - Keep `run_turn` mocked with `fake_run_turn` to prevent invoking external sub-processes, while asserting that the
      command passed to `run_turn` matches the exact output of the real runner's `build_command`.
  - In `test_run_resolver_agent_raises_on_non_zero_exit_code`:
    - Replace `mock_runner = MagicMock()` with a real runner instance (`get_runner("antigravity-agent")` or
      `create_autospec(AgentRunner, instance=True)`).
    - Assert that `_run_resolver_agent()` invokes `build_command()` without raising `AttributeError` and cleanly raises
      `RuntimeError` on non-zero exit code.
  - Add a dedicated test verifying fallback robustness:
    - Instantiate an `AgentRunner` test double that implements only `compose_cmd` (and does not implement
      `build_command`).
    - Verify that `_run_resolver_agent()` catches the absence of `build_command` and seamlessly falls back to calling
      `compose_cmd(model_name, prompt)`.
  - Add an explicit contract fidelity test:
    - Pass an object lacking both methods to `_run_resolver_agent()` and verify that it fails closed with an informative
      `AttributeError`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Critical to preventing over-mocking defects from slipping past CI)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3`, `docs/safety.md#1`
- **Potential failure modes for this step:** Unintended test interactions with environment variables or temporary file
  paths; mitigated by hermetic tmp_path fixtures and mock turn results.
- **Guardrails and early‑abort checks:** Ensure `run_turn` remains mocked so external processes are not spawned during
  unit tests.

### Success & Discard Criteria

- **Success:** Resolver agent execution tests validate against real runner contracts (using real runners or
  `create_autospec(AgentRunner)`) and pass without `AttributeError`.
- **Discard:** Discard if cost exceeds `1.5 * cost_pred` (`2.10`) or mock replacement causes regressions in other review
  loop test cases.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.70  |
| impact_pred         | 30.00 |
| cost_pred           | 1.40  |
| learning_value_pred | 2.00  |
| ev_pred             | 28.19 |

### Step Metrics Rationale

- `p_success_pred`: Bottleneck step (`0.96`) because replacing loose mocks with real contracts often exposes hidden
  assumptions in test fixtures.
- `entropy_pred`: Moderate (`0.70`) reflecting refactoring across several test methods in a critical entrypoint test
  file.
- `impact_pred`: High (`30.00`) directly addressing the test gap highlighted in the intent rationale.
- `cost_pred`: Moderate (`1.40`) due to fixture rewiring and fallback test authoring.
- `learning_value_pred`: Significant (`2.00`) establishing project-wide standards for autospec and mock minimisation.
- `ev_pred`: Calculated via code span:
  `EV = 0.96 * 30.00 + 0.5 * 2.00 - 0.3 * 0.70 - 1.40 = 28.80 + 1.00 - 0.21 - 1.40 = 28.19`.

---

## Step 4: Execute test suite and quality gates

- **Sub‑intent recommendation:** NO
- **Reasoning:** Deterministic verification step to validate all acceptance criteria and lint rules across the
  workspace.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Execute affected unit tests with `uv run pytest`, execute the entire test suite with
  `uv run task test`, and execute formatting and lint checks with `uv run task lint` and `uv run task lint-docs`.
- **Git branch:**
  `I-1794003835-fix-resolver-runner-build-cmd-attribute-error/P-1794003851-antigravity-agent-gemini-3.8-flash-medium/E-step4-verification-quality-gates`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Execute affected unit tests first:
  - Run `uv run pytest apps/sandbox-executor/tests/test_agent_runner.py apps/sandbox-executor/tests/test_review_loop.py`
    to verify runner contract assertions and resolver loop behaviour.
- Execute the full test suite once:
  - Run `uv run task test` to ensure zero regressions across any workspace packages or runner integrations.
- Execute static analysis and documentation lint gates:
  - Run `uv run task lint` to verify lockfile integrity, git diff status, ruff check, and ruff format compliance.
  - Run `uv run task lint-docs` to ensure Prettier checks on markdown documentation pass without style violations.
- Record execution actions and outcomes in the ledger adhering to append-only constraints.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#1`, `holon-config/world/ruleset.md#2`,
  `holon-config/world/ruleset.md#3`, `docs/safety.md#2`
- **Potential failure modes for this step:** Test suite timeout or lint check formatting mismatch.
- **Guardrails and early‑abort checks:** Stop immediately if any test fails and resolve root cause without altering
  ledger history.

### Success & Discard Criteria

- **Success:** 100% of unit tests pass and all task runner quality checks (`check`, `lint`, `lint-docs`, `test`) exit
  with code 0.
- **Discard:** Discard if test failures cannot be resolved within `1.5 * cost_pred` (`1.20`).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.30  |
| impact_pred         | 10.00 |
| cost_pred           | 0.80  |
| learning_value_pred | 0.50  |
| ev_pred             | 9.26  |

### Step Metrics Rationale

- `p_success_pred`: Very high (`0.99`) as verification runs over already-validated code and tests.
- `entropy_pred`: Low (`0.30`) involving purely read-only test and lint execution.
- `impact_pred`: Moderate (`10.00`) confirming workspace-wide stability and release readiness.
- `cost_pred`: Low (`0.80`) covering compute time for standard CI checks.
- `learning_value_pred`: Modest (`0.50`) confirming pipeline reproducibility.
- `ev_pred`: Calculated via code span:
  `EV = 0.99 * 10.00 + 0.5 * 0.50 - 0.3 * 0.30 - 0.80 = 9.90 + 0.25 - 0.09 - 0.80 = 9.26`.
