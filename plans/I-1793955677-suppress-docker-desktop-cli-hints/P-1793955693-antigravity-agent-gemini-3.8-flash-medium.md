# Plan for I-1793955677-suppress-docker-desktop-cli-hints

- **Plan ID:** P-1793955693-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.5.2)
- **Created At:** 2026-11-06T09:01:33.778Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** conservative with 1–2 sentence justification.
- **Safety priority level:** standard
- **Priority Justification:** Standard environment variable propagation and hermetic testing without security risks.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The intent requires straightforward environment variable configuration across subprocess calls and
  hermetic test verification.

## Overall Plan Metrics

| metric              | value  |
| ------------------- | ------ |
| p_success_pred      | 0.92   |
| entropy_pred        | 2.00   |
| impact_pred         | 100.00 |
| cost_pred           | 4.00   |
| learning_value_pred | 7.00   |
| ev_pred             | 90.90  |

### Strategy Rationale

The intent is accepted as-is (`ACCEPT_AS_IS`). The goal is to eliminate promotional and diagnostic hints (such as Docker
Desktop's Gordon suggestions, `"What's next: Debug this container error with Gordon -> docker ai ..."`) from polluting
output logs and telemetry when containers exit with non-zero exit codes. This is achieved by ensuring
`DOCKER_CLI_HINTS='false'` is systematically exported and injected across all execution environments.

The selected strategy decomposes the task into four cohesive steps:

1. **Subprocess Environment Propagation (`turns.py` & `flow.py`):** Standardise the environment mapping passed to
   subprocesses in `turns.py` (specifically in `run_turn()`) and `flow.py` to ensure `DOCKER_CLI_HINTS='false'` is
   guaranteed in all spawned child processes.
2. **CLI Entrypoints, Runner Scripts & Container Helpers:** Export `DOCKER_CLI_HINTS='false'` in host CLI entrypoints
   (`holon`), container runner scripts (`role_dispatcher.sh`), Dockerfile base environments, and container execution
   helpers (`_run_docker`, `run_docker_container` in `cli/__init__.py`).
3. **Hermetic Unit Test Harness (`tests/test_turns.py`):** Author comprehensive hermetic unit tests that verify
   environment dictionary builders, `run_turn` parameter handling, and container runner helpers without requiring a live
   Docker daemon or network access.
4. **Verification & Quality Gate Execution:** Execute affected tests via
   `uv run pytest apps/sandbox-executor/tests/test_turns.py`, run the complete test suite once via `uv run task test`,
   and verify code and documentation formatting via `uv run task lint` and `uv run task lint-docs`.

**Derivation of Overall Metrics:**

- `p_success_pred`: Derived with Step 3 as the bottleneck step (`p_success_pred = 0.95`). The compound probability
  across all steps is `0.96 * 0.97 * 0.95 * 0.98 = 0.867`, with an overall qualitative estimate of `0.92` based on high
  step independence and hermetic test design.
- `entropy_pred`: Derived by summing step entropies (`0.60 + 0.50 + 0.70 + 0.20 = 2.00`), which is well below the
  allocated entropy budget of `15.0`.
- `impact_pred`: Derived by summing step impacts (`35.00 + 30.00 + 25.00 + 10.00 = 100.00`), reflecting clean,
  unpolluted logging and telemetry across automated review loops and execution turns.
- `cost_pred`: Derived by summing step resource costs (`1.20 + 1.00 + 1.30 + 0.50 = 4.00`).
- `learning_value_pred`: Derived by summing step learning values (`2.00 + 1.50 + 3.00 + 0.50 = 7.00`), capturing
  reusable patterns for hermetic environment sanitisation.
- `ev_pred`: Derived using the config formula
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost` where `mu = 0.5` and `lambda = 0.3`:
  `EV = 0.92 * 100.00 + 0.5 * 7.00 - 0.3 * 2.00 - 4.00 = 92.00 + 3.50 - 0.60 - 4.00 = 90.90`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md#1`: Python runtime `==3.13.*`, dependencies managed strictly via `uv` at the
    repository root. Zero new dependencies introduced.
  - `holon-config/world/ruleset.md#2`: Australian English spelling, PEP 8 compliance, strict typing annotations, and
    comprehensive docstrings for modified functions.
  - `holon-config/world/ruleset.md#3`: Testing via `pytest==9.1.1` (`uv run pytest`); new test file
    `apps/sandbox-executor/tests/test_turns.py` explicitly declared.
  - `holon-config/world/constraints.md#1` & `docs/safety.md#1`: Branch isolation under intent branch
    `I-1793955677-suppress-docker-desktop-cli-hints`; merging to `main` remains strictly human-maintainer gated.
  - `holon-config/world/constraints.md#3`: Immutability of the three append-only ledgers (`intents.jsonl`,
    `plans.jsonl`, `executions.jsonl`).
- **Potential violations or edge cases:**
  - Callers supplying custom `env` dictionaries in `run_turn` or `flow.py` that omit `DOCKER_CLI_HINTS`; mitigated by
    shallow copying the input mapping and setting `"DOCKER_CLI_HINTS": "false"`.
  - Non-string environment variable keys or values causing `TypeError` in `subprocess`; mitigated by strict type
    hygiene.
  - Flaky tests attempting to invoke a real Docker daemon; mitigated by mocking `popen_factory` and `subprocess.run` to
    guarantee hermetic test execution.
- **Mitigations built into the plan:**
  - Defensive copying of environment mappings to prevent mutating caller state.
  - Fully hermetic unit tests using `unittest.mock`.
- **Residual risk accepted (and why):**
  - Minimal residual risk: `DOCKER_CLI_HINTS=false` is a standard, officially supported Docker CLI configuration flag.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 2.00
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

The plan establishes complete suppression of Docker Desktop promotional hints and Gordon suggestions:

1. **Step 1:** In `apps/sandbox-executor/src/sandbox_executor/turns.py` and `flow.py`, ensure all subprocess
   environments include `DOCKER_CLI_HINTS='false'`.
2. **Step 2:** In CLI wrappers (`holon`), runner scripts (`role_dispatcher.sh`), Dockerfile base environments, and
   container helpers (`cli/__init__.py`), export `DOCKER_CLI_HINTS='false'`.
3. **Step 3:** In `apps/sandbox-executor/tests/test_turns.py`, implement hermetic unit tests verifying subprocess
   environment mapping builders and hint suppression on non-zero exit codes.
4. **Step 4:** Execute the verification suite with `uv run pytest apps/sandbox-executor/tests/test_turns.py`,
   `uv run task test`, `uv run task lint`, and `uv run task lint-docs`.

---

## Step 1: Subprocess execution environment builder and propagation in `turns.py` and `flow.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Straightforward Python environment dictionary modification with minimal risk, low entropy (`0.60`), and
  high probability of success (`0.96`).
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Ensure all subprocess execution environments created or invoked in `turns.py` and `flow.py` guarantee
  `DOCKER_CLI_HINTS='false'`.
- **Git branch:**
  `I-1793955677-suppress-docker-desktop-cli-hints/P-1793955693-antigravity-agent-gemini-3.8-flash-medium/E-step1-turns-flow-env`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/turns.py`:
   - Inspect `run_turn()` where `popen_factory` is invoked with `env`.
   - Implement or ensure an environment mapping helper
     `build_turn_env(env: Mapping[str, str] | None = None) -> dict[str, str]`.
   - If `env` is None, copy `os.environ` and inject `"DOCKER_CLI_HINTS": "false"`.
   - If `env` is provided, create a dictionary copy and ensure `"DOCKER_CLI_HINTS": "false"` is set.
   - Pass this normalised environment mapping to `popen_factory(cmd, ..., env=effective_env, ...)`.
   - Ensure Australian English docstrings and complete type annotations for all new/modified signatures.
2. In `apps/sandbox-executor/src/sandbox_executor/flow.py`:
   - Inspect subprocess execution points (such as `probe_ci_checks`, PR creation/editing commands, and container launch
     helpers).
   - Ensure any environment dictionaries passed to `subprocess.run()` (e.g. `gh_env`, `commit_env`, proxy environments)
     include `"DOCKER_CLI_HINTS": "false"`.
   - Guard against overwriting custom caller environments while guaranteeing `DOCKER_CLI_HINTS` is consistently
     suppressed.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2`, `docs/safety.md#2`.
- **Potential failure modes for this step:**
  - Mutation of the caller's input dictionary if passed by reference. Mitigated by always constructing a shallow copy
    via `dict(env)`.
  - Non-string keys or values causing `TypeError` when passed to `subprocess`. Mitigated by strict type validation and
    string casting if needed.
- **Guardrails and early‑abort checks:**
  - Verify that `effective_env["DOCKER_CLI_HINTS"] == "false"` is always satisfied.

### Success & Discard Criteria

- **Success:** `run_turn` and `flow.py` subprocess calls always pass `DOCKER_CLI_HINTS='false'` in their subprocess
  `env` mapping.
- **Discard:** Discard if modifications break existing environment variable forwarding (e.g. `PATH`, `PYTHONPATH`, or
  proxy variables).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.60  |
| impact_pred         | 35.00 |
| cost_pred           | 1.20  |
| learning_value_pred | 2.00  |
| ev_pred             | 33.22 |

### Step Metrics Rationale

The implementation is contained within `turns.py` and `flow.py`. Cost is minimal (`1.20`), success probability is high
(`0.96`), entropy is modest (`0.60`), and learning value (`2.00`) reflects standardisation of execution environment
sanitisation across the workflow. Metric calculation:
`EV = 0.96 * 35.00 + 0.5 * 2.00 - 0.3 * 0.60 - 1.20 = 33.60 + 1.00 - 0.18 - 1.20 = 33.22`.

---

## Step 2: Configure CLI entrypoints, runner scripts, and container helpers

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard configuration and parameter passing with low entropy (`0.50`), high probability of success
  (`0.97`), and zero external dependencies.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Ensure `DOCKER_CLI_HINTS='false'` is set across CLI entrypoints (`holon`), container runner scripts
  (`role_dispatcher.sh`), Dockerfile base environments, and container execution helpers (`_run_docker`,
  `run_docker_container`).
- **Git branch:**
  `I-1793955677-suppress-docker-desktop-cli-hints/P-1793955693-antigravity-agent-gemini-3.8-flash-medium/E-step2-cli-runner-config`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/cli/__init__.py`:
   - In `_run_docker(*args: str)`: ensure the `subprocess.run(["docker", *args], ...)` call passes
     `env={**os.environ, "DOCKER_CLI_HINTS": "false"}`.
   - In `run_docker_container()`: ensure `subprocess.run(docker_cmd, env=docker_env)` passes `DOCKER_CLI_HINTS='false'`
     in the host execution environment.
   - In `run_docker_container()`: append `["-e", "DOCKER_CLI_HINTS=false"]` to `docker_cmd` so that any docker commands
     executed inside the container also run with hints disabled.
2. In the repository root CLI wrapper `holon`:
   - Add `export DOCKER_CLI_HINTS="false"` before executing the Python module.
3. In `apps/sandbox-executor/entrypoint/role_dispatcher.sh`:
   - Export `DOCKER_CLI_HINTS="false"` during container bootstrap.
4. In `apps/sandbox-executor/Dockerfile`:
   - Add `ENV DOCKER_CLI_HINTS=false` in the base stage so all derived images have hints disabled by default.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#1`, `docs/safety.md#2`.
- **Potential failure modes for this step:**
  - Shell syntax errors or unbound variable errors under `set -euo pipefail`. Mitigated by using standard portable POSIX
    export syntax `export DOCKER_CLI_HINTS="false"`.
- **Guardrails and early‑abort checks:**
  - Verify that running `./holon --help` or `_run_docker` executes with `DOCKER_CLI_HINTS` populated.

### Success & Discard Criteria

- **Success:** All container runners, root entrypoints, and container execution helpers pass and export
  `DOCKER_CLI_HINTS='false'`.
- **Discard:** Discard if changes cause container startup or secret bundle ingestion regressions.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.50  |
| impact_pred         | 30.00 |
| cost_pred           | 1.00  |
| learning_value_pred | 1.50  |
| ev_pred             | 28.70 |

### Step Metrics Rationale

This step covers configuration across CLI wrappers and container launch helpers. Risk is very low (`0.50` entropy) and
success probability is high (`0.97`). Metric calculation:
`EV = 0.97 * 30.00 + 0.5 * 1.50 - 0.3 * 0.50 - 1.00 = 29.10 + 0.75 - 0.15 - 1.00 = 28.70`.

---

## Step 3: Implement hermetic unit tests in `apps/sandbox-executor/tests/test_turns.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Hermetic test authoring using mock processes and assertions. Self-contained within
  `tests/test_turns.py` with moderate entropy (`0.70`) and high probability of success (`0.95`).
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Author dedicated hermetic unit tests verifying that subprocess environment builders and `run_turn`
  consistently inject `DOCKER_CLI_HINTS='false'`, and that non-zero container exit error reporting suppresses
  promotional hints.
- **Git branch:**
  `I-1793955677-suppress-docker-desktop-cli-hints/P-1793955693-antigravity-agent-gemini-3.8-flash-medium/E-step3-hermetic-unit-tests`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `apps/sandbox-executor/tests/test_turns.py` adhering to `holon-config/world/ruleset.md#3`.
2. Implement test case `test_run_turn_injects_docker_cli_hints_default`:
   - Mock `popen_factory` via `unittest.mock.MagicMock`.
   - Call `run_turn(["echo", "hello"], env=None)`.
   - Inspect kwargs passed to `popen_factory`: assert that `env` is a dictionary containing
     `"DOCKER_CLI_HINTS": "false"`.
3. Implement test case `test_run_turn_preserves_and_merges_custom_env`:
   - Call `run_turn(["echo", "hello"], env={"MY_VAR": "value123"})`.
   - Inspect kwargs passed to `popen_factory`: assert that `env["MY_VAR"] == "value123"` and
     `env["DOCKER_CLI_HINTS"] == "false"`.
4. Implement test case `test_cli_docker_helpers_propagate_cli_hints`:
   - Mock `subprocess.run` inside `sandbox_executor.cli`.
   - Invoke `_run_docker("ps")` and `run_docker_container()`.
   - Assert `env` kwarg passed to `subprocess.run` contains `"DOCKER_CLI_HINTS": "false"`.
   - Assert `-e DOCKER_CLI_HINTS=false` is included in the container invocation command-line arguments.
5. Implement test case `test_non_zero_exit_error_logging_cleanliness`:
   - Simulate a failed container execution producing non-zero exit code (e.g. exit code 1 or 127).
   - Verify that output handling and error formatting do not emit Docker Desktop Gordon promotional suggestions
     (`"What's next: Debug this container error with Gordon"`).
6. Ensure all tests run completely hermetically without requiring a live Docker daemon or network access.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (Acceptance criteria require hermetic unit tests passing via `uv run pytest`).

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3`, `docs/safety.md#2`.
- **Potential failure modes for this step:**
  - Tests leaking into live system environment or attempting to connect to a real Docker socket. Mitigated by strictly
    using mocks for `popen_factory` and `subprocess.run`.
- **Guardrails and early‑abort checks:**
  - Verify tests execute hermetically and cleanly under `uv run pytest apps/sandbox-executor/tests/test_turns.py`.

### Success & Discard Criteria

- **Success:** All new tests in `apps/sandbox-executor/tests/test_turns.py` pass hermetically with zero failures or
  skipped assertions.
- **Discard:** Discard if tests require root privileges, Docker daemon access, or exceed execution time budgets.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.70  |
| impact_pred         | 25.00 |
| cost_pred           | 1.30  |
| learning_value_pred | 3.00  |
| ev_pred             | 23.74 |

### Step Metrics Rationale

Authoring unit tests verifies the suppression mechanism and guards against regressions. Step 3 is the critical
validation gate (bottleneck step, `p_success_pred = 0.95`). Learning value is `3.00` as it establishes reusable hermetic
patterns for subprocess environment validation. Metric calculation:
`EV = 0.95 * 25.00 + 0.5 * 3.00 - 0.3 * 0.70 - 1.30 = 23.75 + 1.50 - 0.21 - 1.30 = 23.74`.

---

## Step 4: Verification, formatting, and linting suite execution

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard regression testing and formatting check with near-zero risk, low entropy (`0.20`), and high
  probability of success (`0.98`).
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Verify all new and existing tests pass hermetically, and ensure codebase strictly satisfies
  formatting, type-checking, and doc linting standards.
- **Git branch:**
  `I-1793955677-suppress-docker-desktop-cli-hints/P-1793955693-antigravity-agent-gemini-3.8-flash-medium/E-step4-verification-linting`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Execute the affected hermetic test file:
   - Run `uv run pytest apps/sandbox-executor/tests/test_turns.py`.
2. Execute the full unit test suite once:
   - Run `uv run task test`.
3. Check code formatting and static typing:
   - Run `uv run task lint` (verifies `uv.lock`, `ruff check`, and `ruff format`).
4. Check documentation formatting:
   - Run `uv run task lint-docs` (runs Prettier check on Markdown files).
5. Ensure Australian English spelling throughout any modified docstrings and comments.
6. Verify that the three append-only ledgers (`intents.jsonl`, `plans.jsonl`, `executions.jsonl`) remain strictly
   untouched.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#1`, `holon-config/world/ruleset.md#2`,
  `holon-config/world/constraints.md#3`.
- **Potential failure modes for this step:**
  - Formatting discrepancies in docstrings or test files failing `ruff` or `prettier`. Mitigated by running
    `uv run task format` if any minor style adjustments are required.
- **Guardrails and early‑abort checks:**
  - Strict adherence to zero ledger mutation and single root `.venv` usage.

### Success & Discard Criteria

- **Success:** `uv run pytest apps/sandbox-executor/tests/test_turns.py`, `uv run task test`, `uv run task lint`, and
  `uv run task lint-docs` all return exit code 0.
- **Discard:** Discard if any regression is detected in existing sandbox execution tests.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.20  |
| impact_pred         | 10.00 |
| cost_pred           | 0.50  |
| learning_value_pred | 0.50  |
| ev_pred             | 9.49  |

### Step Metrics Rationale

This step validates the changes against the standard test and quality gates. Cost is minimal (`0.50`), entropy is low
(`0.20`), and probability of success is high (`0.98`). Metric calculation:
`EV = 0.98 * 10.00 + 0.5 * 0.50 - 0.3 * 0.20 - 0.50 = 9.80 + 0.25 - 0.06 - 0.50 = 9.49`.
