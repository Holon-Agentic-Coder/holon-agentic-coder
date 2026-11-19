# Plan for I-1794089603-pass-prompts-to-agent-runners-via-files

- **Plan ID:** P-1794089619-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.5.3)
- **Created At:** 2026-11-07T22:13:40.225Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None
- **Exploration stance:** balanced with focus on robust verification of POSIX argument limits and subprocess stream
  handling across runner types.
- **Safety priority level:** standard
- **Priority Justification:** Work modifies subprocess execution and CLI generation in sandbox executor without network
  or credential boundary changes.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** Well-defined refactor with deterministic POSIX kernel boundary mechanics and established runner
  argument protocols.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 2.80  |
| impact_pred         | 88.0  |
| cost_pred           | 6.50  |
| learning_value_pred | 5.20  |
| ev_pred             | 78.86 |

### Strategy Rationale

The intent is accepted as is (`ACCEPT_AS_IS`) without reframing, as passing prompts via files or stdin directly
addresses the physical Linux kernel `MAX_ARG_STRLEN` (128 KB / 131,072 bytes) limitation encountered when processing
large PR review diffs. Inlining multi-hundred kilobyte prompts into `execve` argument vectors causes catastrophic
`[Errno 7] Argument list too long` failures. Decomposing the solution into four structured steps (stream handling,
runner hierarchy refactoring, caller harmonisation, and boundary testing) isolates risks and provides complete immunity
against argument overflow.

Overall plan metrics are derived methodically from individual step estimates:

- **Success probability (`p_success_pred = 0.95`):** Determined by the bottleneck step, Step 3
  (`p_success_pred = 0.95`), which coordinates file creation and argument construction across three separate entrypoint
  modules (`planner.py`, `executor.py`, and `review_ensemble.py`).
- **Plan Entropy (`entropy_pred = 2.80`):** Derived as the linear sum of step-level entropies:
  `Delta_S = 0.60 + 0.90 + 0.70 + 0.60 = 2.80`. This falls well within the allocated budget of `15.0`.
- **Impact (`impact_pred = 88.0`):** Reflects the critical system-wide benefit of eliminating kernel argument length
  failures across all six supported agent runner backends.
- **Cost (`cost_pred = 6.50`):** Derived by summing execution and test costs: `Cost = 1.20 + 2.00 + 1.80 + 1.50 = 6.50`.
- **Learning Value (`learning_value_pred = 5.20`):** Epistemic value gained from mapping and testing diverse agent CLI
  streaming protocols under POSIX kernel constraints.
- **Expected Value (`ev_pred = 78.86`):** Evaluated strictly using the configured system formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.95 * 88.0 + 0.5 * 5.20 - 0.3 * 2.80 - 6.50 = 83.60 + 2.60 - 0.84 - 6.50 = 78.86`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md#1-git-flow--branch-constraints`: All changes occur on the intent-scoped branch
    without direct commits or merges to `main`. Merging remains the sole responsibility of the human maintainer.
  - `holon-config/world/constraints.md#2-sandbox-containment-tiers`: Low predicted entropy (`2.80 < 10.0`) permits
    execution within the standard hermetic process sandbox with complete network isolation.
  - `holon-config/world/constraints.md#3-ledger-immutability`: Append-only ledgers (`executions.jsonl`, `intents.jsonl`,
    `plans.jsonl`) are strictly respected; no rewriting or deletion of historical events.
  - `holon-config/world/ruleset.md#1-runtime--environment-specification`: Target Python runtime `==3.13.*` without
    external dependencies.
  - `holon-config/world/ruleset.md#2-coding-conventions--standards`: Full PEP 8 compliance, comprehensive type
    annotations, and explicit docstrings.
  - `holon-config/world/ruleset.md#3-testing-constraints`: All test execution strictly via `uv run pytest`; existing
    test assertion boundaries remain unaltered.
  - `docs/safety.md#Sandboxing-model`: Subprocess spawning is strictly restricted to approved internal agent binaries
    and test utilities.
- **Potential failure modes or edge cases:**
  - File descriptor leakage when streaming standard input from disk files.
  - Premature unlinking of prompt files prior to the execution of resume or reprompt follow-up turns.
  - CLI argument syntax mismatches across different agent CLIs when prompt strings are omitted.
  - Differences in host kernel page size altering `MAX_ARG_STRLEN` thresholds during test verification.
- **Mitigations built into the plan:**
  - Enforce strict `try...finally` blocks for all opened file handles in `run_turn`.
  - Maintain prompt file lifecycle until the entire authoring session (including follow-ups) concludes.
  - Provide explicit fallback to legacy argument composition (`compose_cmd`) when `prompt_file` is absent.
  - Construct synthetic test prompts (>130 KB) designed to exceed `MAX_ARG_STRLEN` across any standard 4 KB page Linux
    environment.
- **Residual risk accepted (and why):**
  - Minor residual risk of third-party agent CLI flag changes in upstream vendor tools; accepted because vendor wrappers
    are pinned and verified during image preflight.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 2.80
- **Budget Compliance:** The strategy fits within budget (`2.80 <= 15.0`).

## Plan Description & Strategy

This plan delivers a unified, cross-harness solution to prevent Linux `MAX_ARG_STRLEN` argument buffer overflow failures
across all supported agent runners (Antigravity, Claude, Pi, Gemini, OpenCode, Codex). In Linux, individual command-line
arguments are constrained to `MAX_ARG_STRLEN` (128 KB). When large PR review diffs or extensive task descriptions are
passed directly as argv arguments, the kernel's `execve` syscall immediately aborts with
`[Errno 7] Argument list too long`.

The strategy operates in four incremental, decoupled stages:

1. **Subprocess Stdin Streaming (`turns.py`):** Equip `run_turn` with `stdin_file` streaming support, allowing
   subprocesses to read arbitrary prompt payloads directly from disk file descriptors without memory bloat or argv
   exposure. Update `supervise_authoring_turn` to thread `stdin_file` and synchronize follow-up prompts to disk.
2. **Agent Runner Hierarchy & Protocol (`agent_runner.py`):** Establish distinct runner subclasses for each agent. For
   Antigravity, pass prompt files using the native `@prompt_file` syntax. For runners supporting stdin streaming
   (Claude, Pi, Gemini, OpenCode, Codex), configure command builders to omit inlined prompt strings from argv and signal
   stdin readiness.
3. **Stage Caller Harmonisation (`planner.py`, `executor.py`, `review_ensemble.py`):** Ensure all stage entrypoints
   write the complete prompt payload to disk and pass the prepared `prompt_file` to `runner.build_cmd()` and
   `supervise_authoring_turn()`.
4. **Verification & Kernel Boundary Tests (`test_agent_runner.py`, `test_turns.py`):** Verify command structure, test
   stdin streaming integrity, and simulate prompts exceeding 130 KB to prove immunity against
   `[Errno 7] Argument list too long` crashes.

---

## Step 1: Implement Subprocess Stdin Streaming in `turns.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Localised core library modification in `turns.py` with low complexity, low risk, and direct
  verification via unit tests.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update `run_turn` and `supervise_authoring_turn` in
  `apps/sandbox-executor/src/sandbox_executor/turns.py` to support streaming prompt inputs directly from disk files via
  subprocess standard input, avoiding POSIX kernel argument buffer saturation.
- **Git branch:** `I-1794089603-pass-prompts-to-agent-runners-via-files/step-1-turns-stdin-streaming`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add optional keyword argument `stdin_file: str | None = None` to `run_turn` signature.
2. In `run_turn`, when `stdin_file` is specified and exists, open the file in binary read mode (`rb`) and supply the
   file descriptor/stream to `popen_factory` via the `stdin` keyword parameter.
3. Guarantee that the opened file stream descriptor is cleanly closed in parent process space via a `try...finally`
   block immediately after `popen_factory` initialises the child process table.
4. When `stdin_file` is omitted or `None`, maintain existing behaviour by leaving `stdin` as default (`None`).
5. Update `supervise_authoring_turn` to accept optional `stdin_file: str | None = None` and
   `prompt_file: str | None = None`.
6. In `supervise_authoring_turn`, thread `stdin_file` through to each invocation of `run(...)`.
7. For authoring resume or reprompt follow-ups, ensure that if `prompt_file` is provided, the follow-up prompt text is
   written to `prompt_file` so the streamed stdin payload reflects the current follow-up instruction.
8. Maintain strict typing annotations across `turns.py` using standard `typing` and collections.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (Subprocess stdin streaming is required by all downstream stage callers and runner harnesses to
  prevent argument limit crashes)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md#Sandboxing-model`, `holon-config/world/ruleset.md#2-coding-conventions--standards`
- **Potential failure modes for this step:** Leaking file descriptors if opened handles are not closed; race conditions
  or file-not-found errors if `stdin_file` path is unreadable.
- **Guardrails and early‑abort checks:** Enforce `try...finally` cleanup of file streams; validate path readability
  prior to spawning process.

### Success & Discard Criteria

- **Success:** `run_turn` successfully launches child processes with `stdin` bound to an arbitrary disk file descriptor
  without errors or file handle leaks.
- **Discard:** Discard if subprocess spawning deadlocks on stdin pipe reads or breaks budget supervision timing loops.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.60  |
| impact_pred         | 85.0  |
| cost_pred           | 1.20  |
| learning_value_pred | 4.00  |
| ev_pred             | 83.92 |

### Step Metrics Rationale

Calculated as `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S - Cost`. With `P(success) = 0.98`,
`Impact = 85.0`, `mu = 0.5`, `LearningValue = 4.00`, `lambda = 0.3`, `Delta_S = 0.60`, and `Cost = 1.20`:
`EV = 0.98 * 85.0 + 0.5 * 4.00 - 0.3 * 0.60 - 1.20 = 83.30 + 2.00 - 0.18 - 1.20 = 83.92`. The step relies on standard
Python subprocess stream redirection, giving near-certain success and low entropy.

---

## Step 2: Refactor Agent Runner Class Hierarchy & Argument Protocols in `agent_runner.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Modular class refactoring within `agent_runner.py` maintaining strict interface backwards-compatibility
  for existing caller contracts.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Refactor `AgentRunner` and establish explicit subclasses for Antigravity, Claude, Pi, Gemini,
  OpenCode, and Codex in `apps/sandbox-executor/src/sandbox_executor/agent_runner.py`, passing prompt files via `@`
  prefix syntax for Antigravity and configuring stdin streaming flags for other harnesses.
- **Git branch:** `I-1794089603-pass-prompts-to-agent-runners-via-files/step-2-agent-runner-hierarchy`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `AgentRunner`, define a public attribute `uses_stdin: bool = False` indicating whether the runner expects its
   prompt stream over stdin rather than positional argv.
2. Extend `AgentRunner.build_cmd` signature to ensure `prompt_file: str | None = None` and
   `intent_file: str | None = None` are accommodated cleanly.
3. In `StandardAgentRunner`:
   - Set default `uses_stdin = True` when file-based prompt execution is activated.
   - In `build_cmd`, when `prompt_file` is provided, ensure `full_prompt` is written to `prompt_file` if not already
     present or out of sync.
   - When `prompt_file` is present and `uses_stdin` is `True`, construct the command line containing runtime flags (e.g.
     `-p`, `run`, `exec`) without inlining `full_prompt` into argv.
   - When `prompt_file` is absent, preserve backwards-compatible fallback by calling
     `compose_cmd(model_name, full_prompt)`.
4. In `AntigravityAgentRunner`:
   - Set `uses_stdin = False`.
   - In `build_cmd`, when `prompt_file` is provided, ensure `full_prompt` is written to `prompt_file` and append
     `f"@{prompt_file}"` as the prompt argument instead of the raw string.
5. Introduce specialised subclasses inheriting from `StandardAgentRunner`:
   - `PiAgentRunner` (binary `pi`, prefix `["-p"]`, `uses_stdin = True`)
   - `ClaudeAgentRunner` (binary `claude`, suffix `["-p"]`, bypass permission handling, `uses_stdin = True`)
   - `GeminiAgentRunner` (binary `gemini`, suffix `["-p"]`, `uses_stdin = True`)
   - `OpenCodeAgentRunner` (binary `opencode`, prefix `["run"]`, `uses_stdin = True`)
   - `CodexAgentRunner` (binary `codex`, prefix `["exec"]`, model flag `-m`, `uses_stdin = True`)
6. Update the `runners` registry dictionary to instantiate the respective subclass instances for `"pi"`, `"claude"`,
   `"gemini"`, `"opencode"`, `"codex"`, and `"antigravity"`.
7. Preserve existing credential validation logic, environment mappings, and version query routines.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Downstream stages require the updated runner argument signatures and stdin indicators)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2-coding-conventions--standards`,
  `docs/safety.md#Core-safety-principles`
- **Potential failure modes for this step:** Breaking legacy callers relying on `compose_cmd` or `build_command`
  appending prompt strings directly; misinterpreting CLI flags when prompt strings are omitted.
- **Guardrails and early‑abort checks:** Retain `compose_cmd` and `build_command` contract without modification; run
  existing runner mapping checks.

### Success & Discard Criteria

- **Success:** All runner instances generate command lines where large prompt strings are replaced with file references
  (`@prompt_file`) or prepared for stdin streaming, while `build_command` preserves legacy behaviour.
- **Discard:** Discard if runner CLI argument orders violate vendor CLI parser specifications.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.90  |
| impact_pred         | 90.0  |
| cost_pred           | 2.00  |
| learning_value_pred | 5.00  |
| ev_pred             | 86.63 |

### Step Metrics Rationale

Calculated as `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S - Cost`. With `P(success) = 0.96`,
`Impact = 90.0`, `mu = 0.5`, `LearningValue = 5.00`, `lambda = 0.3`, `Delta_S = 0.90`, and `Cost = 2.00`:
`EV = 0.96 * 90.0 + 0.5 * 5.00 - 0.3 * 0.90 - 2.00 = 86.40 + 2.50 - 0.27 - 2.00 = 86.63`. Slightly higher entropy due to
updating multiple subclass definitions and registry entries.

---

## Step 3: Harmonise Stage Callers Across Planner, Executor, and Review Ensemble

- **Sub‑intent recommendation:** NO
- **Reasoning:** Integration step connecting runner invocation and stdin streaming across existing entrypoints with low
  blast radius.
- **Step Type:** INTEGRATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update `planner.py`, `executor.py`, and `review_ensemble.py` to ensure `full_prompt` is written to
  `prompt_file` before execution and that `stdin_file` and `prompt_file` are consistently passed to
  `supervise_authoring_turn`.
- **Git branch:** `I-1794089603-pass-prompts-to-agent-runners-via-files/step-3-stage-caller-harmonisation`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/entrypoint/planner.py`:
   - Ensure the constructed `full_prompt` is explicitly written to `prompt_file` on disk before building the runner
     command.
   - When calling `supervise_authoring_turn`, pass
     `stdin_file=prompt_file if getattr(runner, "uses_stdin", False) else None` and `prompt_file=prompt_file`.
2. In `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`:
   - Validate that `prompt_file` contains `full_prompt`.
   - In `supervise_authoring_turn` invocation, pass
     `stdin_file=prompt_file if getattr(runner, "uses_stdin", False) else None` and `prompt_file=prompt_file`.
3. In `apps/sandbox-executor/src/sandbox_executor/review_ensemble.py`:
   - In `_supervise_reviewer_turn`, inspect `runner.uses_stdin` and pass
     `stdin_file=prompt_file if getattr(runner, "uses_stdin", False) else None` and `prompt_file=prompt_file` into
     `supervise_authoring_turn`.
4. Guarantee that temporary prompt files are kept throughout the turn and any resume/reprompt iterations, and cleanly
   unlinked in `finally:` blocks upon stage completion.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** NO (Follows direct implementation patterns established in previous steps)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md#3-ledger-immutability`, `docs/safety.md#Sandboxing-model`
- **Potential failure modes for this step:** Premature deletion of prompt files before follow-up turns complete;
  unhandled `None` values for `prompt_file`.
- **Guardrails and early‑abort checks:** Keep file lifecycle inside `try...finally` scopes; ensure path validation
  before file writes.

### Success & Discard Criteria

- **Success:** Planner, executor, and review ensemble entrypoints successfully execute turns without inlining prompt
  text into process arguments.
- **Discard:** Discard if any entrypoint raises file access errors or fails to pass prompt data to the runner.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.70  |
| impact_pred         | 88.0  |
| cost_pred           | 1.80  |
| learning_value_pred | 4.50  |
| ev_pred             | 83.84 |

### Step Metrics Rationale

Calculated as `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S - Cost`. With `P(success) = 0.95`,
`Impact = 88.0`, `mu = 0.5`, `LearningValue = 4.50`, `lambda = 0.3`, `Delta_S = 0.70`, and `Cost = 1.80`:
`EV = 0.95 * 88.0 + 0.5 * 4.50 - 0.3 * 0.70 - 1.80 = 83.60 + 2.25 - 0.21 - 1.80 = 83.84`. Moderate entropy from updating
three entrypoint modules, with high expected value.

---

## Step 4: Robust Verification and Linux Argument Limit Tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Dedicated test engineering ensuring zero regression and proving kernel boundary immunity (>130 KB
  prompt inputs).
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add thorough unit and boundary tests in `tests/test_agent_runner.py` and `tests/test_turns.py`
  verifying that prompt inputs exceeding Linux `MAX_ARG_STRLEN` (128 KB) execute cleanly via files or stdin without
  triggering `[Errno 7] Argument list too long`.
- **Git branch:** `I-1794089603-pass-prompts-to-agent-runners-via-files/step-4-tests-verification`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_agent_runner.py`:
   - Update `test_standard_runner_cmd` to test command generation when `prompt_file` is supplied:
     - Verify `antigravity` produces `agy ... -p @/path/to/prompt.md`.
     - Verify `claude`, `pi`, `gemini`, `opencode`, and `codex` produce commands without the prompt string in argv and
       set `uses_stdin = True`.
   - Add new test `test_large_prompt_argument_overflow_prevention`:
     - Construct a synthetic prompt larger than 130 KB (>133,120 bytes), which strictly exceeds Linux `MAX_ARG_STRLEN`
       (131,072 bytes).
     - Verify that invoking `runner.build_cmd` with `prompt_file` succeeds without argv bloat.
     - Verify that attempting to invoke `subprocess.run` with the raw 130 KB string as a single argv argument fails with
       `OSError` (`E2BIG` / `[Errno 7] Argument list too long`) under POSIX, whereas invoking with the generated command
       and `stdin_file` succeeds.
2. In `apps/sandbox-executor/tests/test_turns.py`:
   - Add unit tests for `run_turn` with `stdin_file`:
     - Test that `popen_factory` receives an active file reader for `stdin`.
     - Test that the file handle is properly closed and not leaked.
     - Test streaming a 140 KB prompt file through `run_turn` to a mock or simulated reader process, asserting complete
       payload delivery and zero buffer overflow.
   - Add unit tests for `supervise_authoring_turn` with `stdin_file` and `prompt_file`:
     - Test initial turn passes `stdin_file`.
     - Test that follow-up reprompt turns re-write the follow-up text to `prompt_file`.
3. Execute the full test suite and lint tasks:
   - Run `uv run pytest apps/sandbox-executor/tests/test_agent_runner.py apps/sandbox-executor/tests/test_turns.py`.
   - Run `uv run task check` and `uv run task lint`.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** YES (Final quality gate verifying all acceptance criteria and kernel boundary immunity)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3-testing-constraints`, `docs/safety.md#Core-safety-principles`
- **Potential failure modes for this step:** Test flakiness due to temporary file creation or differences in platform
  `MAX_ARG_STRLEN` behaviours.
- **Guardrails and early‑abort checks:** Use hermetic temporary directories and mock assertions; guard OS-level `E2BIG`
  checks with platform conditional logic where appropriate.

### Success & Discard Criteria

- **Success:** All new and existing tests pass cleanly with `uv run pytest`, verifying that prompts >130 KB never
  trigger `[Errno 7]` and lint tasks pass without warnings.
- **Discard:** Discard if tests fail or introduce flaky behaviours in CI test runners.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.60  |
| impact_pred         | 85.0  |
| cost_pred           | 1.50  |
| learning_value_pred | 5.50  |
| ev_pred             | 83.52 |

### Step Metrics Rationale

Calculated as `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S - Cost`. With `P(success) = 0.97`,
`Impact = 85.0`, `mu = 0.5`, `LearningValue = 5.50`, `lambda = 0.3`, `Delta_S = 0.60`, and `Cost = 1.50`:
`EV = 0.97 * 85.0 + 0.5 * 5.50 - 0.3 * 0.60 - 1.50 = 82.45 + 2.75 - 0.18 - 1.50 = 83.52`. High confidence, low risk, and
strong epistemic verification of kernel boundary limits.

---
