# Plan for I-1794041607-add-holon-models-command-to-list-supported-models

- **Plan ID:** P-1794041623-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.5.2)
- **Created At:** 2026-11-07T08:53:44.260Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted empirical discovery of runner capabilities and CLI ergonomics.
- **Safety priority level:** standard
- **Priority Justification:** Modifies CLI commands, agent defaults, and diagnostic error output without secret leakage
  or network mutation. Adheres strictly to standard workspace safety and isolation rules.

## Exploration

- **Proportion of steps that are exploratory:** 0.25
- **Justification:** Centralising cross-runner model capabilities, effort coordination, and leak-free session detection
  requires probing existing runner definitions, harness markers, and environment configurations across the codebase.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 2.80  |
| impact_pred         | 88.00 |
| cost_pred           | 9.50  |
| learning_value_pred | 7.50  |
| ev_pred             | 76.13 |

### Strategy Rationale

The intent is accepted as-is (`ACCEPT_AS_IS`). Operators and autonomous agents currently experience invalid model
selection errors and effort parameter conflicts because there is no introspection command to discover supported models,
aliases, and effort settings across runners. Adding `holon models` (and alias `holon list-models`) resolves this
deficiency with zero external dependencies and immediate self-service discovery.

The strategy decomposes the intent into four focused, incremental steps:

1. **Centralised Model Registry & Capabilities (`agent_defaults.py`):** Define canonical models, aliases, supported
   effort levels, fallback tiers, automatic suffix-effort coordination, and leak-free auth/session presence detection.
2. **CLI Command Implementation (`cli/commands/models.py` & `cli/main.py`):** Implement the `holon models` command (with
   `holon list-models` alias), supporting `--agent` filtering, `--json` schema output, and a formatted ASCII/Markdown
   table display.
3. **Holon Doctor & Pre-flight Diagnostics Integration:** Update `holon doctor` and model resolution error paths to
   print available models and recommend running `holon models` when model resolution or validation fails.
4. **Comprehensive Real Tests (`tests/test_models_cli.py`):** Minimise mocks by exercising real CLI dispatch, real
   runner configuration tables, `--json` validation, error reporting, and doctor suggestions under real execution.

**Derivation of Overall Metrics:**

- `p_success_pred`: Derived with Step 3 and Step 4 as the bottleneck steps (`p_success_pred = 0.94`). The individual
  step probabilities (`0.95`, `0.96`, `0.94`, `0.94`) reflect well-bounded modules and clear existing patterns. Overall
  `p_success_pred = 0.94`.
- `entropy_pred`: Derived by summing step entropies (`0.80 + 0.70 + 0.60 + 0.70 = 2.80`), which is well below the
  allocated budget of `15.0`.
- `impact_pred`: Set to `88.00`, reflecting significant operational impact by eliminating model configuration errors
  across all supported agent runners.
- `cost_pred`: Derived by summing step costs (`2.50 + 2.20 + 1.80 + 3.00 = 9.50`).
- `learning_value_pred`: Set to `7.50`, capturing reusable patterns for cross-runner capability discovery, leak-free
  session introspection, and mock-free CLI testing.
- `ev_pred`: Derived using the config formula
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost` where `mu = 0.5` and `lambda = 0.3`:
  `EV = 0.94 * 88.00 + 0.5 * 7.50 - 0.3 * 2.80 - 9.50 = 82.72 + 3.75 - 0.84 - 9.50 = 76.13`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md#1`: Python runtime `==3.13.*`, dependencies managed strictly via `uv` at the
    repository root. Zero new external dependencies introduced.
  - `holon-config/world/ruleset.md#2`: Australian English spelling (centralised, normalise, categorise, minimise),
    strict static type annotations (`typing`), PEP 8 compliance, and comprehensive docstrings.
  - `holon-config/world/ruleset.md#3`: Testing via `pytest==9.1.1` (`uv run pytest`); tests placed in
    `apps/sandbox-executor/tests/test_models_cli.py`.
  - `holon-config/world/constraints.md#1` & `docs/safety.md#1`: Branch isolation under intent branch
    `I-1794041607-add-holon-models-command-to-list-supported-models`; merging to `main` remains strictly
    human-maintainer gated.
  - `holon-config/world/constraints.md#3`: Immutability of the three append-only ledgers (`intents.jsonl`,
    `plans.jsonl`, `executions.jsonl`).
  - `.agents/AGENTS.md`: Mandatory tool probing (`command -v`), no throwaway scripts, single root virtual environment
    (`uv run pytest`), target-native paths only, and strict minimisation of mocks.
- **Potential violations or edge cases:**
  - Leaking credential values or secret tokens when reporting auth/session detection status: mitigated by checking
    presence only and returning opaque status strings (e.g. `Detected (session directory)` or `Detected (API key)`),
    never reading or printing token contents.
  - Model suffix mismatch with effort settings (e.g. `gemini-3.8-flash-medium` colliding with `--effort high`):
    mitigated by centralised coordination logic in `agent_defaults.py`.
  - Breaking existing CLI imports or entrypoint contracts: mitigated by keeping `cli/__init__.py` intact and providing
    compatible registration and exports in `cli/main.py`.
- **Mitigations built into the plan:**
  - Strict leak-free assertions verifying that environment values and token substrings never appear in table or JSON
    outputs.
  - Case-insensitive runner normalisation via `normalise_agent_id`.
  - Mock-free test harness executing real CLI argument parsing and table generation.
- **Residual risk accepted (and why):**
  - Minimal residual risk: All additions are additive CLI introspection utilities and diagnostic hints; existing
    execution flows and docker pre-flight probe contracts remain untouched.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 2.80
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

The strategy implements a robust, centralised capability registry and discovery command for Holon:

1. First, establish the canonical registry in `apps/sandbox-executor/src/sandbox_executor/agent_defaults.py`. Define
   models, aliases, effort levels, and fallback tiers for all runners: `antigravity-agent` (`gemini-3.8-flash`,
   `gemini-3.8-flash-medium`, `gemini-3.8-flash-high`), `claude-agent` (`claude-opus-5-5`, `claude-sonnet-4-6`,
   `claude-haiku-4-5` with `opus`, `sonnet`, `haiku`, `fable` aliases), `pi-agent`, `codex-agent`, `gemini-agent`,
   `opencode-agent`, and host-local models. Provide safe auth/session presence detection without token exposure.
2. Second, implement the `holon models` command and `holon list-models` alias in
   `apps/sandbox-executor/src/sandbox_executor/cli/commands/models.py`. Support runner filtering (`--agent`) and
   machine-readable output (`--json`), and output a well-aligned ASCII/Markdown table by default. Register parser and
   subcommand in `cli/main.py`.
3. Third, integrate diagnostic hints into `holon doctor` and `preflight.py` so that missing or invalid model errors
   clearly enumerate available models for the specified runner and recommend running `holon models`.
4. Fourth, develop comprehensive tests in `apps/sandbox-executor/tests/test_models_cli.py` verifying real CLI dispatch,
   table formatting, JSON schemas, case-insensitive runner filtering, error handling, and doctor integration without
   internal dictionary mocking. Validate clean passes for `uv run pytest` and `uv run task lint`.

---

## Step 1: Centralised Model Registry and Capabilities Definition in agent_defaults.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Step 1 is a well-contained, cohesive enhancement to `agent_defaults.py` that introduces standard data
  structures and helper functions without modifying external dependencies or exceeding risk thresholds.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED
- **Hypothesis being tested:** Centralising runner models, aliases, effort parameters, and safe auth presence detection
  in `agent_defaults.py` eliminates configuration drift and allows seamless cross-runner model discovery.
- **Learning target:** Verify how runner aliases coordinate with effort levels and establish robust presence detection
  across different runner credential paths without reading secret contents.
- **Maximum acceptable cost for this learning:** `cost_pred <= 3.0`

### Intent & Git Integration

- **Step Intent:** Define canonical model capabilities, aliases, effort levels, fallback tiers, normalisation logic, and
  leak-free auth/session presence detection in `apps/sandbox-executor/src/sandbox_executor/agent_defaults.py`.
- **Git branch:** `I-1794041607-add-holon-models-command-to-list-supported-models/step-1-model-registry-capabilities`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/agent_defaults.py`, define a structured `ModelCapability` dataclass
   with fields:
   - `runner`: Canonical runner name (e.g. `antigravity-agent`, `claude-agent`, `pi-agent`, `codex-agent`,
     `gemini-agent`, `opencode-agent`, `local-model`).
   - `canonical_model`: Primary canonical model name (e.g. `gemini-3.8-flash-medium`, `claude-opus-5-5`, `gpt-5-codex`).
   - `aliases`: List of recognised model aliases (e.g. `['opus', 'claude-opus']`, `['flash', 'flash-medium']`).
   - `effort_levels`: List of supported effort settings (e.g. `['low', 'medium', 'high']` for antigravity; empty or
     `['none']` for runners without effort options).
   - `fallback_tier`: Tier classification string (e.g. `Primary (Cloud/CLI)`, `Secondary (Cloud/CLI)`,
     `Local Fallback`).
2. Populate the central registry table `RUNNER_CAPABILITIES`:
   - `antigravity`: Canonical models `gemini-3.8-flash`, `gemini-3.8-flash-medium`, `gemini-3.8-flash-high` with effort
     levels `low`, `medium`, `high`. Aliases: `flash`, `flash-medium`, `flash-high`.
   - `claude`: Canonical models `claude-opus-5-5`, `claude-sonnet-4-6`, `claude-haiku-4-5`. Aliases: `opus`, `sonnet`,
     `haiku`, `fable`. Effort levels: None.
   - `pi`: Canonical models `claude-opus-5-5`, `gpt-5-codex`. Aliases: `pi-default`. Effort levels: None.
   - `codex`: Canonical models `gpt-5-codex`. Aliases: `codex`. Effort levels: None.
   - `gemini`: Canonical models `gemini-3.8-flash`. Aliases: `gemini`. Effort levels: None.
   - `opencode`: Canonical models `anthropic/claude-opus-5-5`. Aliases: `opencode`. Effort levels: None.
   - `local`: Host-local models from `local_llm` envelope.
3. Implement
   `normalise_model_and_effort(agent: str, model: str | None, effort: str | None = None) -> tuple[str, str | None]`:
   - Inspect runner and model name to detect effort suffixes (e.g. `-medium`, `-high`, `-low` for Gemini models).
   - Coordinate the model name and effort parameter so they align cleanly without conflicting.
   - Map model aliases to their canonical model identifiers.
4. Implement safe presence detection
   `detect_runner_auth_status(agent: str, environ: Mapping[str, str] | None = None) -> str`:
   - Check Tier 1 secret bundle path (`HOLON_SECRET_BUNDLE_PATH` or `/run/secrets/holon_auth.json`).
   - Check Tier 2 environment keys (`HOLON_AGENT_KEY`, `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, etc.).
   - Check Tier 3 session paths / mounts (`~/.gemini`, `~/.config/claude`, `~/.codex`, `~/.config/pi/models.json`).
   - Check harness markers (`AGY_SESSION_ID`, `CLAUDECODE`, etc.).
   - Return descriptive status strings such as `"Detected (session directory)"`, `"Detected (API key)"`,
     `"Detected (harness marker)"`, `"Detected (local gateway)"`, or `"Missing / Not detected"`. Ensure no secret values
     or partial hashes are ever returned or logged.
5. Implement query helper functions `get_model_capabilities(agent: str | None = None) -> list[ModelCapability]` and
   `get_available_models_for_runner(agent: str) -> list[str]`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md#3`, `holon-config/world/ruleset.md#2`, `holon-config/world/constraints.md#1`.
- **Potential failure modes for this step:** Secret leakage via presence detection; inconsistent model alias resolution.
- **Guardrails and early‑abort checks:** Presence detection checks boolean file/env existence only; values are never
  stringified or formatted into output.

### Success & Discard Criteria

- **Success:** `agent_defaults.py` exposes `RUNNER_CAPABILITIES`, `get_model_capabilities`,
  `normalise_model_and_effort`, and `detect_runner_auth_status` with full type annotations and Australian English
  docstrings.
- **Discard:** If implementation introduces circular dependencies or breaks existing `resolve_agent_runner` tests.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.80  |
| impact_pred         | 82.00 |
| cost_pred           | 2.50  |
| learning_value_pred | 7.00  |
| ev_pred             | 78.66 |

### Step Metrics Rationale

- `p_success_pred`: 0.95 due to clear data structures and existing resolver patterns in `agent_defaults.py`.
- `entropy_pred`: 0.80 reflecting low complexity addition of immutable capability records.
- `impact_pred`: 82.00 as it lays the foundation for unified model discovery.
- `cost_pred`: 2.50 resource expenditure for implementation and initial testing.
- `learning_value_pred`: 7.00 for establishing reusable cross-runner capability schemas and leak-free presence
  detection.
- `ev_pred`: `0.95 * 82.00 + 0.5 * 7.00 - 0.3 * 0.80 - 2.50 = 77.90 + 3.50 - 0.24 - 2.50 = 78.66`.

---

## Step 2: CLI Command Implementation holon models and Alias in cli/commands/models.py & cli/main.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Step 2 follows standard CLI command patterns already established in `sandbox_executor/cli/commands/`
  and requires no external architecture changes.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Implement `holon models` (and alias `holon list-models`) subcommand supporting `--agent`, `--json`,
  and ASCII/Markdown table display, registering it in `cli/main.py`.
- **Git branch:** `I-1794041607-add-holon-models-command-to-list-supported-models/step-2-models-cli-command`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `apps/sandbox-executor/src/sandbox_executor/cli/commands/models.py`:
   - Set `ORDER = 75` so discovery places it before `doctor` (`ORDER = 80`).
   - Implement `register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None`:
     - Register `models` subcommand with `aliases=["list-models"]`.
     - Add `--agent` option to filter output strictly to a specific runner (normalising user input).
     - Add `--json` option for machine-readable JSON output.
     - Set default runner function: `parser.set_defaults(_run=run)`.
2. Implement command execution in `run(args: argparse.Namespace) -> int`:
   - Normalise `--agent` parameter using `normalise_agent_id` when provided.
   - If an invalid runner is specified, print error message listing valid runners and return exit code 1.
   - Retrieve matching `ModelCapability` records via `get_model_capabilities(agent)`.
   - Populate live auth/session presence status for each runner using `detect_runner_auth_status`.
   - When `--json` is supplied:
     - Serialize records to JSON matching schema:
       `[{"runner": ..., "canonical_model": ..., "aliases": [...], "effort_levels": [...], "fallback_tier": ..., "auth_status": ...}, ...]`.
     - Print formatted JSON string to stdout and return 0.
   - When default table output is requested:
     - Format columns: `Runner`, `Canonical Model`, `Aliases`, `Supported Effort Levels`, `Fallback Tier`,
       `Auth / Session Detection`.
     - Calculate column widths dynamically to prevent wrapping or misaligned borders.
     - Print table to stdout and return 0.
3. Ensure `apps/sandbox-executor/src/sandbox_executor/cli/main.py` is present:
   - Provide clean top-level re-exports and CLI invocation entrypoint matching project layout conventions and intent
     scope requirements.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#1`, `holon-config/world/ruleset.md#2`, `docs/safety.md#1`.
- **Potential failure modes for this step:** Argument parsing collisions between alias and primary command; incorrect
  table padding on wide terminal outputs.
- **Guardrails and early‑abort checks:** Validate command registration dynamically using
  `sandbox_executor.cli.commands.discover()`.

### Success & Discard Criteria

- **Success:** `holon models` and `holon list-models` execute from the command line, formatting table output and valid
  JSON as requested.
- **Discard:** Discard if command breaks CLI discovery or fails argument parsing tests.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.70  |
| impact_pred         | 85.00 |
| cost_pred           | 2.20  |
| learning_value_pred | 6.50  |
| ev_pred             | 82.44 |

### Step Metrics Rationale

- `p_success_pred`: 0.96 due to well-established subcommand patterns in `sandbox_executor/cli/commands/`.
- `entropy_pred`: 0.70 reflecting predictable CLI dispatch and formatting logic.
- `impact_pred`: 85.00 directly delivering the required user-facing command.
- `cost_pred`: 2.20 resource expenditure.
- `learning_value_pred`: 6.50 for establishing dynamic table formatting patterns across runner models.
- `ev_pred`: `0.96 * 85.00 + 0.5 * 6.50 - 0.3 * 0.70 - 2.20 = 81.60 + 3.25 - 0.21 - 2.20 = 82.44`.

---

## Step 3: Holon Doctor & Pre-flight Diagnostics Integration

- **Sub‑intent recommendation:** NO
- **Reasoning:** Step 3 integrates the model registry into existing doctor and resolver error handling with minimal
  blast radius.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED
- **Hypothesis being tested:** Providing model enumeration and directing operators to `holon models` upon model
  resolution failure significantly reduces time-to-recovery during setup errors.
- **Learning target:** Evaluate error messaging clarity and verify that JSON doctor output preserves structured
  environment diagnostics when model errors occur.
- **Maximum acceptable cost for this learning:** `cost_pred <= 2.5`

### Intent & Git Integration

- **Step Intent:** Integrate model registry diagnostics with `holon doctor` and `preflight.py` to print available models
  and suggest running `holon models` when model resolution or validation fails.
- **Git branch:** `I-1794041607-add-holon-models-command-to-list-supported-models/step-3-doctor-integration`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Update `MissingModelError` in `apps/sandbox-executor/src/sandbox_executor/agent_defaults.py`:
   - When raising `MissingModelError`, query `get_available_models_for_runner(agent)`.
   - Include the available models for that runner in the error message.
   - Explicitly add recommendation:
     `"Run 'holon models --agent <runner>' to inspect supported models and effort levels."`
2. Update `resolve_model` in `apps/sandbox-executor/src/sandbox_executor/agent_defaults.py`:
   - Validate whether explicit model or alias is recognized for the runner.
   - When an unrecognised model is passed, raise `MissingModelError` or `ValueError` indicating the invalid model,
     enumerating valid choices, and recommending `holon models`.
3. In `apps/sandbox-executor/src/sandbox_executor/cli/__init__.py` (`run_doctor`):
   - Ensure that when model resolution fails, the error message in text output prints available models and the
     recommendation to run `holon models`.
   - In JSON output (`--json`), include available models and diagnostic hint under the structured error payload.
4. In `apps/sandbox-executor/src/sandbox_executor/preflight.py`:
   - Ensure probe hints for unknown or unrecognised model errors advise running `holon models` to verify runner
     compatibility.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (P_success = 0.94; key integration point connecting registry with doctor diagnostics)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2`, `docs/safety.md#4`.
- **Potential failure modes for this step:** Modifying exception text could break existing test assertions expecting
  legacy regex patterns.
- **Guardrails and early‑abort checks:** Review existing regexes in `test_agent_defaults.py` and `test_cli.py` to ensure
  backwards compatibility of exception messages while appending model lists and diagnostic hints.

### Success & Discard Criteria

- **Success:** Running `holon doctor` with missing or invalid model outputs available models for that runner and
  recommends running `holon models`.
- **Discard:** Discard if existing pre-flight or doctor test suites fail regressions.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.60  |
| impact_pred         | 78.00 |
| cost_pred           | 1.80  |
| learning_value_pred | 6.00  |
| ev_pred             | 74.34 |

### Step Metrics Rationale

- `p_success_pred`: 0.94 reflecting slight risk of assertion mismatch in existing tests.
- `entropy_pred`: 0.60 as changes are strictly additive hints and diagnostic strings.
- `impact_pred`: 78.00 improving operator diagnostic experience.
- `cost_pred`: 1.80 resource expenditure.
- `learning_value_pred`: 6.00 for understanding doctor integration boundaries.
- `ev_pred`: `0.94 * 78.00 + 0.5 * 6.00 - 0.3 * 0.60 - 1.80 = 73.32 + 3.00 - 0.18 - 1.80 = 74.34`.

---

## Step 4: Comprehensive Real Tests in test_models_cli.py & Test Suite Verification

- **Sub‑intent recommendation:** NO
- **Reasoning:** Step 4 provides mock-free test coverage and end-to-end verification, ensuring high reliability without
  requiring independent branch decomposition.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Implement comprehensive real tests in `tests/test_models_cli.py` verifying real command dispatch,
  table output, `--agent` filtering, `--json` schema validation, doctor integration, and error handling without internal
  dictionary mocking.
- **Git branch:** `I-1794041607-add-holon-models-command-to-list-supported-models/step-4-real-tests-and-lint`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `apps/sandbox-executor/tests/test_models_cli.py`:
   - Follow project testing standards: no throwaway scripts, minimise mocks, real CLI dispatch.
   - Test real CLI dispatch via `cli.main(["models"])` and `cli.main(["list-models"])` capturing stdout/stderr.
   - Test formatted table display: verify column headers (`Runner`, `Canonical Model`, `Aliases`,
     `Supported Effort Levels`, `Fallback Tier`, `Auth / Session Detection`) and row separators.
   - Test `--agent` filtering:
     - Filter by `antigravity`: verify output contains `gemini-3.8-flash` variants and effort levels.
     - Filter by `claude`: verify output contains Claude models and aliases (`opus`, `sonnet`, `haiku`, `fable`).
     - Filter by `pi`: verify output contains pi runner models.
     - Test case-insensitivity (e.g. `--agent Claude`, `--agent ANTIGRAVITY`).
   - Test `--json` machine-readable output:
     - Parse JSON output and validate that each entry conforms to the schema (`runner`, `canonical_model`, `aliases`,
       `effort_levels`, `fallback_tier`, `auth_status`).
   - Test credential/session presence detection without secret leakage:
     - Verify status reports detection without revealing token strings or keys.
     - Assert that secret tokens injected into environment never appear in stdout or JSON.
   - Test error handling:
     - Test invalid `--agent unknown-runner` returns non-zero exit code and informative error message.
   - Test doctor integration:
     - Verify `holon doctor` suggests running `holon models` when model resolution fails.
2. Run test verification:
   - Run unit tests: `uv run pytest apps/sandbox-executor/tests/test_models_cli.py`.
   - Run related tests:
     `uv run pytest apps/sandbox-executor/tests/test_agent_defaults.py apps/sandbox-executor/tests/test_cli.py`.
   - Run entire test suite: `uv run task test`.
   - Run linters: `uv run task lint`, `uv run task lint-docs`, and `uv run task check`.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** YES (P_success = 0.94; enforces acceptance criteria and mock-free verification)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3`, `.agents/AGENTS.md`.
- **Potential failure modes for this step:** Subtle linting issues (ruff format or prettier markdown checks).
- **Guardrails and early‑abort checks:** Run `uv run task check` immediately after test authoring.

### Success & Discard Criteria

- **Success:** All tests in `test_models_cli.py` pass cleanly with real CLI dispatch; `uv run task test` and
  `uv run task lint` pass without errors or warnings.
- **Discard:** Discard if mocks are used for internal dictionaries or if linters fail.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.70  |
| impact_pred         | 88.00 |
| cost_pred           | 3.00  |
| learning_value_pred | 7.50  |
| ev_pred             | 83.26 |

### Step Metrics Rationale

- `p_success_pred`: 0.94 ensuring strict compliance with all acceptance criteria and lint gates.
- `entropy_pred`: 0.70 reflecting full verification scope.
- `impact_pred`: 88.00 cementing end-to-end reliability.
- `cost_pred`: 3.00 for comprehensive test runs and linting.
- `learning_value_pred`: 7.50 for empirical verification of mock-free CLI test patterns.
- `ev_pred`: `0.94 * 88.00 + 0.5 * 7.50 - 0.3 * 0.70 - 3.00 = 82.72 + 3.75 - 0.21 - 3.00 = 83.26`.
