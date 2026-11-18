# Plan for I-1791288951-standardize-tooling-hygiene-and-scaffolding

- **Plan ID:** P-1791289002-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-10-06T12:16:42.332Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted empirical validation of git plumbing commands, container shell script
  resilience under `set -euo pipefail`, and taskipy task delegation semantics without violating repository invariants or
  modifying test files without explicit planning declarations.
- **Safety priority level:** elevated
- **Priority Justification:** Triggered by `holon-config/world/ruleset.md` §1 & §2 (Python runtime `==3.13.*`, workspace
  isolation under `apps/sandbox-executor`, strict PEP 8 formatting, explicit static typing with `typing` module,
  docstring maintenance), `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test file placement
  under `apps/sandbox-executor/tests/`, test changes explicitly declared in the planning step),
  `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: prefix-based branch isolation, rebase
  discipline, commit boundaries), `holon-config/world/constraints.md` §2 (Sandbox containment: subprocess and filesystem
  containment, no unwhitelisted subprocesses), `docs/safety.md` §1 & §2 (Git as safety boundary, sandboxing mandatory
  for execution), and Beans 0048, 0047, 0050, 0059, 0036.

## Exploration

- **Proportion of steps that are exploratory:** 0.50
- **Justification:** Steps 2, 3, and 4 incorporate balanced exploration to validate taskipy command delegation across
  shell environments, safe failure semantics in shell scripts under `set -euo pipefail` when external utilities (`jq`)
  or secret bundles are malformed, and git plumbing fallback behavior when `.git/config` is read-only.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.9   |
| impact_pred         | 94.0  |
| cost_pred           | 12.0  |
| learning_value_pred | 5.5   |
| ev_pred             | 76.96 |

### Strategy Rationale

The overall plan metrics were derived from individual step-level metrics as follows:

- **p_success_pred**: 0.92. Dictated by the bottleneck step (Step 4: 0.92), where git plumbing detection via
  `git config --local --get-all safe.directory` and fallback to per-invocation `-c safe.directory` must reliably handle
  realpath normalization and read-only repository environments without breaking git execution.
- **entropy_pred**: 0.9. Derived as the maximum single-step risk profile (Step 4: 0.9), where git command argument
  manipulation and configuration reads/writes are performed. The sum of predicted step entropies is 3.2
  (`0.3 + 0.6 + 0.7 + 0.9 + 0.4 + 0.3 = 3.2`), well within the allocated entropy budget of 15.0.
- **impact_pred**: 94.0. Resolves five prominent developer hygiene and runtime reliability defects (Beans 0048, 0047,
  0050, 0059, 0036), eliminating version pin oscillations, standardizing task automation on `uv` and `taskipy`, ensuring
  actionable error reporting during credential injection, securing git operations against ownership checks, and
  preserving scaffolded directories.
- **cost_pred**: 12.0. Calculated as the direct sum of individual step costs
  (`1.0 + 2.0 + 2.5 + 3.0 + 1.5 + 2.0 = 12.0`).
- **learning_value_pred**: 5.5. Epistemic gain from establishing standardized task runner conventions in `uv`
  workspaces, hardening container entrypoints against shell aborts on optional dependencies, and establishing git
  plumbing patterns for sandbox container execution.
- **ev_pred**: 76.96. Computed strictly via the config-driven Expected Value formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`. With system constants `lambda = 0.3`
  and `mu = 0.5` loaded from `holon-config/metrics/ev_config.json`:
  `EV = 0.92 * 94.0 + 0.5 * 5.5 - 0.3 * 0.9 - 12.0 = 86.48 + 2.75 - 0.27 - 12.0 = 76.96`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python target `==3.13.*`, workspace package
    management via `uv`).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8 compliance, static typing with `typing`
    module, complete docstrings).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test files placed in `tests/`, all test
    changes explicitly declared in the planning step).
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: prefix-based branch isolation, rebase
    discipline, commit boundaries).
  - `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: filesystem containment, no unwhitelisted
    subprocesses).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: zero modifications to historical ledger entries).
  - `docs/safety.md` §1 & §2 (Git as safety boundary, sandboxing mandatory for execution).
- **Potential violations or edge cases:**
  - Inadvertently causing `role_dispatcher.sh` to exit with an error on missing `jq` when credentials are not strictly
    required, blocking container startup.
  - Introducing infinite recursion or shell syntax errors in `Makefile` targets when delegating to `uv run task`.
  - Normalization differences between symlinked paths and physical paths in `executor.py` when comparing
    `safe.directory` entries.
  - Accidental removal of pre-existing entries or comments when modifying `pyproject.toml` or `README.md`.
  - Idempotency violations in `scaffold.py` if `.gitkeep` files are created destructively or trigger git conflicts.
- **Mitigations built into the plan:**
  - Protect `jq` execution in `role_dispatcher.sh` with safe presence checks (`command -v jq`) and non-fatal stderr
    warnings, keeping the overall script non-crashing under `set -euo pipefail`.
  - Ensure `Makefile` targets delegate explicitly to `uv run task <target>` without cyclic dependencies.
  - Employ `os.path.realpath` for canonical comparison of all repository paths against `safe.directory` configuration.
  - Validate `.gitkeep` creation with existence checks (`if not os.path.exists`) to guarantee strict idempotency.
  - Enforce comprehensive test validation across both unit and hygiene test runners before considering the plan
    completed.
- **Residual risk accepted (and why):**
  - Environments with outdated `npx` caches may download the latest Prettier on first invocation; this is expected
    behavior under unpinned formatting workflows.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 3.2
- **Budget Compliance:** The strategy fits within budget (Predicted Plan Entropy sum of 3.2 is well below the 15.0
  allocated budget).

## Plan Description & Strategy

This plan implements all five Batch A improvements across `holon-agentic-coder`:

1. **Bean 0048 - Prettier Pin Removal & Markdown Hygiene Standardization:** Drop the hardcoded `@3.8.4` Prettier version
   pin from `Makefile` (targets `lint-docs` and `format-docs`), `.github/workflows/test-hygiene.yml`, and documentation
   in `.github/workflows/README.md`. Standardize all invocations on `npx prettier --check "**/*.md"` and
   `npx prettier --write "**/*.md"`.
2. **Bean 0047 - UV & Taskipy Task Standardization:** Configure `pyproject.toml` `[tool.taskipy.tasks]` with
   standardized tasks for `check`, `lint`, `lint-docs`, `format`, `test`, and `clean`. Update `Makefile` Python
   validation targets (`check`, `lint`, `lint-docs`, `test`, `format`) to delegate cleanly to `uv run task ...` or
   `uv run ...`, and update documentation in `README.md` and `.agents/AGENTS.md` (and `.agents/rules.md`).
3. **Bean 0050 - JQ Prerequisite & Credential Ingestion Resilience:** In
   `apps/sandbox-executor/entrypoint/role_dispatcher.sh`, verify `jq` availability when `SECRET_BUNDLE` is present. If
   `jq` is missing, emit an actionable warning to `stderr` specifying the bundle path and downstream symptoms rather
   than silently skipping credential delivery. Make `jq` parsing non-fatal under `set -euo pipefail` so malformed
   bundles do not abort container startup. Add assertions in `apps/sandbox-executor/tests/test_build_all_images.py` to
   verify `jq` is executable in built images. Document container requirements in
   `docs/executor/agent_credentials_requirements.md`.
4. **Bean 0059 - Safe.Directory Git Plumbing & Unwritable Fallback:** In
   `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`, replace brittle substring matching on
   `.git/config` with git plumbing (`git config --local --get-all safe.directory`), normalizing paths with
   `os.path.realpath`. Register safe directories via `git config --local --add safe.directory <path>`. If writing to
   local git config fails (e.g., read-only filesystem), fall back to adding `-c safe.directory=<path>` to git command
   arguments. Add regression tests in `apps/sandbox-executor/tests/test_executor.py`.
5. **Bean 0036 - Scaffold .gitkeep Persistence:** In `apps/sandbox-executor/src/sandbox_executor/scaffold.py`, ensure
   `holon init` scaffolds `holon-knowledge/ledger/.gitkeep` and `intents/.gitkeep`. Update
   `apps/sandbox-executor/tests/test_init.py` to assert both files exist and verify idempotent re-runs.
6. **Verification:** Verify all unit tests pass (`uv run pytest -m "not integration_test and not stress"`), Ruff checks
   and formatting pass (`uv run ruff check .`, `uv run ruff format --check .`), and markdown hygiene checks pass
   (`npx prettier --check "**/*.md"`).

---

## Step 1: Prettier Pin Removal & Markdown Hygiene Standardization (Bean 0048)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Scoped configuration and documentation edit with zero architectural risk, low cost, and immediate
  deterministic verification.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Drop the `@3.8.4` version pin from Prettier invocations across the Makefile, CI workflows, and
  workflow documentation, standardizing on unpinned `npx prettier --check "**/*.md"` and
  `npx prettier --write "**/*.md"`.
- **Git branch:**
  `I-1791288951-standardize-tooling-hygiene-and-scaffolding/P-1791289002-antigravity-agent-gemini-3.8-flash-medium/E-prettier-pin-removal/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `Makefile`, locate lines 87 and 96 (`lint-docs` and `format-docs`). Replace
  `npx --yes prettier@3.8.4 --check "**/*.md"` with `npx prettier --check "**/*.md"`, and
  `npx --yes prettier@3.8.4 --write "**/*.md"` with `npx prettier --write "**/*.md"`.
- In `.github/workflows/test-hygiene.yml`, update step `Check markdown formatting with Prettier` to invoke
  `npx prettier --check "**/*.md"` without the `@3.8.4` pin.
- In `.github/workflows/README.md`, update references to Prettier in line 14 and any other locations from
  `npx --yes prettier@3.8.4 --check "**/*.md"` to `npx prettier --check "**/*.md"`.
- Execute `npx prettier --check "**/*.md"` to confirm all modified files adhere to the formatting standard.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §1 (Git isolation), `holon-config/world/ruleset.md` §2 (Coding Conventions &
  Standards).
- **Potential failure modes for this step:** Prettier syntax variations between versions could format markdown
  differently.
- **Guardrails and early‑abort checks:** Run `npx prettier --check "**/*.md"` immediately after modification to confirm
  zero oscillation.

### Success & Discard Criteria

- **Success:** `Makefile`, `.github/workflows/test-hygiene.yml`, and `.github/workflows/README.md` contain no `@3.8.4`
  references and unpinned `npx prettier` checks pass.
- **Discard:** Discard if unpinned Prettier fails on existing markdown files and cannot format them idempotently.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.3   |
| impact_pred         | 75.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 73.91 |

### Step Metrics Rationale

- High `p_success_pred` (0.98) and low `entropy_pred` (0.3) because this is a mechanical string substitution in
  configuration and documentation files.
- `cost_pred` is 1.0 due to minimal edits and immediate verification.
- Derivation: `EV = 0.98 * 75.0 + 0.5 * 3.0 - 0.3 * 0.3 - 1.0 = 73.50 + 1.50 - 0.09 - 1.0 = 73.91`.

---

## Step 2: UV & Taskipy Task Standardization & Documentation (Bean 0047)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Centralizes developer task runner configurations within `pyproject.toml` and delegates Makefile targets
  without altering underlying test logic.
- **Step Type:** CONFIG
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Declaring unified taskipy tasks in `pyproject.toml` allows `Makefile` targets to delegate
  directly to `uv run task <target>`, creating parity between Makefile users and pure `uv` CLI users.
- **Learning target:** Verify how taskipy chains sub-tasks (e.g. `check` calling `lint` and `lint-docs`) and handles
  quoting across shells.
- **Maximum acceptable cost for this learning:** 2.5

### Intent & Git Integration

- **Step Intent:** Configure `[tool.taskipy.tasks]` in `pyproject.toml` with tasks for `check`, `lint`, `lint-docs`,
  `format`, `test`, and `clean`, update Makefile Python validation targets to delegate cleanly to `uv` and `taskipy`,
  and document `uv` as the primary developer workflow in `README.md` and `.agents/AGENTS.md`.
- **Git branch:**
  `I-1791288951-standardize-tooling-hygiene-and-scaffolding/P-1791289002-antigravity-agent-gemini-3.8-flash-medium/E-uv-taskipy-standardization/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `pyproject.toml`, expand `[tool.taskipy.tasks]` to include:
  - `clean`: maintain existing cache and venv cleaning command.
  - `lint`: run `uv lock --check && git diff --exit-code uv.lock && ruff check . && ruff format --check .`.
  - `lint-docs`: run `npx prettier --check "**/*.md"`.
  - `check`: run `task lint && task lint-docs`.
  - `format-code`: run `ruff check --fix . && ruff format .`.
  - `format-docs`: run `npx prettier --write "**/*.md"`.
  - `format`: run `task format-code && task format-docs`.
  - `test`: run `pytest -m "not integration_test and not stress"`.
- In `Makefile`, update targets:
  - `check`: delegate to `uv run task check`.
  - `lint`: delegate to `uv run task lint`.
  - `lint-docs`: delegate to `uv run task lint-docs`.
  - `format`: delegate to `uv run task format`.
  - `test`: delegate to `uv run task test`.
- In `README.md`, update section 5 ("Developer Makefile Workflows") and development workflow instructions to document
  `uv run task <name>` alongside `make <name>` as primary entrypoints.
- In `.agents/AGENTS.md` (and ensure `.agents/rules.md` is aligned or links appropriately), document standard
  `uv run task` invocations for checking, linting, and testing.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1 (Package Management: `uv` workspace commands, dependencies
  declared in `pyproject.toml`).
- **Potential failure modes for this step:** Taskipy command syntax parsing errors on Windows or shells that handle
  nested quotes differently.
- **Guardrails and early‑abort checks:** Test `uv run task check`, `uv run task lint`, and `uv run task test` directly
  from bash.

### Success & Discard Criteria

- **Success:** All taskipy tasks execute successfully via `uv run task <name>` and `make <name>` delegates cleanly to
  them.
- **Discard:** Discard if task delegation causes recursion or sub-process hangs.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.6   |
| impact_pred         | 88.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 83.92 |

### Step Metrics Rationale

- `p_success_pred` (0.95) is high given taskipy is already a declared dev dependency (`taskipy==1.14.1`).
- `entropy_pred` is low (0.6) as changes are confined to `pyproject.toml`, `Makefile`, and documentation.
- Derivation: `EV = 0.95 * 88.0 + 0.5 * 5.0 - 0.3 * 0.6 - 2.0 = 83.60 + 2.50 - 0.18 - 2.0 = 83.92`.

---

## Step 3: JQ Prerequisite Validation & Credential Ingestion Resilience (Bean 0050)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Scoped shell script guard and unit test assertion with well-defined contracts and localized impact.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Providing an explicit stderr warning when `SECRET_BUNDLE` exists but `jq` is missing, and
  isolating `jq` extraction failures from terminating the script under `set -euo pipefail`, prevents downstream
  container boot crashes while surfacing clear diagnosis.
- **Learning target:** Verify shell error handling patterns that safely capture `jq` parse errors without violating
  `pipefail` traps.
- **Maximum acceptable cost for this learning:** 3.0

### Intent & Git Integration

- **Step Intent:** Update `role_dispatcher.sh` to emit an actionable warning to stderr if a secret bundle is present but
  `jq` is missing, make extraction non-fatal if JSON parsing fails under `set -euo pipefail`, add `jq` executable
  assertions in `test_build_all_images.py`, and document container requirements in
  `docs/executor/agent_credentials_requirements.md`.
- **Git branch:**
  `I-1791288951-standardize-tooling-hygiene-and-scaffolding/P-1791289002-antigravity-agent-gemini-3.8-flash-medium/E-jq-prerequisite-credential-resilience/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/entrypoint/role_dispatcher.sh`:
  - Inspect `SECRET_BUNDLE` (`${HOLON_SECRET_BUNDLE_PATH:-/run/secrets/holon_auth.json}`).
  - If `[ -f "$SECRET_BUNDLE" ]`, check whether `command -v jq &>/dev/null` succeeds.
  - If `jq` is not found, print an explicit warning to stderr: log the bundle path, note that `jq` is required to parse
    secret bundles, and warn that agent authentication will be skipped or degraded.
  - If `jq` is available, wrap the bundle parsing in a subshell or guarded execution (e.g.
    `if parsed_val=$(jq ... 2>/dev/null); then ... fi`) so that malformed JSON or invalid syntax does not trigger
    `set -e` or `pipefail` failure.
- In `apps/sandbox-executor/tests/test_build_all_images.py`:
  - Declare a new unit test function `test_dockerfile_installs_jq` or assert that built container environments contain
    `/usr/bin/jq` (or `jq` on `PATH`) and that it executes `--version` successfully.
  - Test the shell logic of `role_dispatcher.sh` with a mock missing `jq` and assert the expected stderr warning is
    produced without non-zero exit.
- In `docs/executor/agent_credentials_requirements.md`:
  - Update Tier 1 Secret Bundle documentation to state that container images must have `jq` installed and explain the
    fallback behavior and stderr warning emitted when `jq` is absent or the bundle is malformed.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §2 (Sandboxing is mandatory for execution), `holon-config/world/ruleset.md` §3
  (Testing Constraints: test changes declared in planning).
- **Potential failure modes for this step:** Shell script syntax errors in `role_dispatcher.sh` causing container
  entrypoint crash.
- **Guardrails and early‑abort checks:** Test `role_dispatcher.sh` execution directly using bash sub-processes with both
  valid, malformed, and missing secret bundle files.

### Success & Discard Criteria

- **Success:** Missing `jq` produces an actionable stderr warning, malformed bundles do not abort execution under
  `set -euo pipefail`, and image tests assert `jq` presence.
- **Discard:** Discard if shell script modifications disrupt legitimate credential propagation.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.7   |
| impact_pred         | 90.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 5.5   |
| ev_pred             | 84.64 |

### Step Metrics Rationale

- `p_success_pred` (0.94) is high because shell credential handling logic is straightforward to test in isolation.
- `entropy_pred` is 0.7 due to shell environment sensitivity under `set -euo pipefail`.
- Derivation: `EV = 0.94 * 90.0 + 0.5 * 5.5 - 0.3 * 0.7 - 2.5 = 84.60 + 2.75 - 0.21 - 2.5 = 84.64`.

---

## Step 4: Safe.Directory Git Plumbing & Unwritable Fallback (Bean 0059)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Core git execution logic in executor.py; changes are self-contained, highly testable with unit mocks
  and local repositories, and bounded in complexity.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Querying git safe directories via `git config --local --get-all safe.directory` with path
  canonicalization (`os.path.realpath`) eliminates substring match false positives, and falling back to
  `-c safe.directory=<path>` per command preserves functionality when local `.git/config` is read-only.
- **Learning target:** Understand git plumbing return codes when local configuration has zero entries versus multiple
  entries, and verify per-invocation `-c` flag ordering.
- **Maximum acceptable cost for this learning:** 3.5

### Intent & Git Integration

- **Step Intent:** Replace manual string searching of `.git/config` with `git config --local --get-all safe.directory`
  (normalizing paths with `os.path.realpath`), use `git config --local --add safe.directory <path>` to register safe
  directories, fall back to per-invocation `-c safe.directory=<path>` when git config writes fail, and add regression
  tests in `test_executor.py`.
- **Git branch:**
  `I-1791288951-standardize-tooling-hygiene-and-scaffolding/P-1791289002-antigravity-agent-gemini-3.8-flash-medium/E-safe-directory-git-plumbing/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`:
  - Locate `run_cmd` (around line 560-577). Replace manual reading of `os.path.join(cwd, ".git", "config")` with a call
    to git plumbing: query configured safe directories via `git config --local --get-all safe.directory`.
  - Normalize both the configured entries and the target repository directory using `os.path.realpath`.
  - If the directory (or `*`) is already registered in local config, do not inject redundant `-c safe.directory` flags.
    If it is not registered, or if the repository is unwritable, fall back to prepending `-c safe.directory=<path>` to
    the git command arguments.
  - In `_probe_git_repo` (lines 606-623), replace manual string checking of `.git/config` with the same git plumbing
    check (`git config --local --get-all safe.directory`).
  - In `repair_repo` (lines 748-764), use `git config --local --add safe.directory <path>`. If the command fails or
    writing to `.git/config` raises an exception (e.g. read-only permissions), catch the error, log a warning to stderr,
    and do not attempt to append strings directly to `.git/config`. Instead, ensure the executor's subsequent commands
    rely on the per-invocation `-c safe.directory=<path>` fallback.
- In `apps/sandbox-executor/tests/test_executor.py`:
  - Add unit tests verifying:
    1. `_probe_git_repo` detects `safe.directory` using plumbing rather than string matching.
    2. Path normalization with symlinks/relative paths matches the canonical realpath.
    3. `repair_repo` registers safe directory via `git config --local --add`.
    4. Unwritable `.git/config` falls back to per-invocation `-c safe.directory=<path>` without crashing.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (If git plumbing or command invocation breaks, git commands in the executor will fail)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints),
  `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: static typing, docstrings),
  `holon-config/world/ruleset.md` §3 (Testing Constraints: test changes declared in planning).
- **Potential failure modes for this step:** Modifying `cmd_args` incorrectly could break git commands that already have
  `-c` options or change positional argument parsing.
- **Guardrails and early‑abort checks:** Ensure `-c` flags are only injected when `cmd_args[0] == "git"` and
  `-c safe.directory` is not already present. Run `test_executor.py` immediately.

### Success & Discard Criteria

- **Success:** All safe directory detections use git plumbing, unwritable config triggers seamless per-invocation `-c`
  fallback, and regression tests in `test_executor.py` pass.
- **Discard:** Discard if git command invocations suffer argument parsing errors or regression in existing executor
  tests.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.9   |
| impact_pred         | 92.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 84.37 |

### Step Metrics Rationale

- `p_success_pred` (0.92) reflects that `executor.py` is the bottleneck step requiring careful handling of git command
  arguments across various probe and repair code paths.
- `entropy_pred` is 0.9, the highest single-step entropy in the plan.
- Derivation: `EV = 0.92 * 92.0 + 0.5 * 6.0 - 0.3 * 0.9 - 3.0 = 84.64 + 3.00 - 0.27 - 3.0 = 84.37`.

---

## Step 5: Scaffold .gitkeep Persistence & Directory Initialization (Bean 0036)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Localized change to file scaffolding list in `scaffold.py` with direct assertions in `test_init.py`.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Ensure `holon init` in `scaffold.py` scaffolds `holon-knowledge/ledger/.gitkeep` and
  `intents/.gitkeep`, asserting their presence and idempotency across multiple runs in `test_init.py`.
- **Git branch:**
  `I-1791288951-standardize-tooling-hygiene-and-scaffolding/P-1791289002-antigravity-agent-gemini-3.8-flash-medium/E-scaffold-gitkeep-persistence/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/src/sandbox_executor/scaffold.py`:
  - In section 2 (`Scaffolding holon-knowledge/`), add `holon-knowledge/ledger/.gitkeep` to the list of scaffolded
    files, ensuring the ledger directory preserves its git presence even when jsonl files are gitignored or clean.
  - In section 2 or as a dedicated step, ensure directory `intents/` is created under `target_dir` and
    `intents/.gitkeep` is created.
  - Use `_write_file` or `if not os.path.exists(...)` checks so that re-running `holon init` (with or without `--force`)
    does not truncate, overwrite, or re-modify existing files.
- In `apps/sandbox-executor/tests/test_init.py`:
  - Update `expected_knowledge_files` in `test_init_scaffolds_default_python_directories_and_files` to include
    `holon-knowledge/ledger/.gitkeep` and `intents/.gitkeep`.
  - Add explicit assertions that `intents/` directory and `intents/.gitkeep` exist after `init_project`.
  - Update idempotency tests (`test_idempotency_invariant_never_truncates_ledgers`) to verify `.gitkeep` files remain
    intact and untouched on repeated runs.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (Ledger Immutability: ledger files must never be
  truncated), `holon-config/world/ruleset.md` §3 (Testing Constraints: test changes declared in planning).
- **Potential failure modes for this step:** Creating `.gitkeep` in `holon-knowledge/ledger/` could interfere with
  ledger parsing if an agent attempts to parse all files in ledger as JSONL.
- **Guardrails and early‑abort checks:** Verify that ledger readers specifically target `*.jsonl` files (e.g.
  `intents.jsonl`, `plans.jsonl`, `executions.jsonl`) and ignore `.gitkeep`. Run `test_init.py` and `test_executor.py`.

### Success & Discard Criteria

- **Success:** `holon init` creates both `.gitkeep` files, re-runs are completely idempotent, and all test assertions
  pass.
- **Discard:** Discard if ledger persistence or scaffold behavior breaks existing project initialization invariants.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.4   |
| impact_pred         | 82.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 3.5   |
| ev_pred             | 79.67 |

### Step Metrics Rationale

- `p_success_pred` (0.97) is very high as directory and file scaffolding logic is simple and isolated.
- `entropy_pred` is low (0.4) because changes are purely additive.
- Derivation: `EV = 0.97 * 82.0 + 0.5 * 3.5 - 0.3 * 0.4 - 1.5 = 79.54 + 1.75 - 0.12 - 1.5 = 79.67`.

---

## Step 6: End-to-End Test Suite, Linting, & Multi-Tool Verification

- **Sub‑intent recommendation:** NO
- **Reasoning:** Validation phase executing established automated test and hygiene suites.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Execute all unit tests, static lint checks, formatting checks, and markdown hygiene checks across the
  repository to verify that all Batch A changes integrate cleanly.
- **Git branch:**
  `I-1791288951-standardize-tooling-hygiene-and-scaffolding/P-1791289002-antigravity-agent-gemini-3.8-flash-medium/E-verification/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Run unit test suite: `uv run pytest -m "not integration_test and not stress"`.
- Run static analysis: `uv run ruff check .`.
- Run formatting check: `uv run ruff format --check .`.
- Run markdown formatting check: `npx prettier --check "**/*.md"`.
- Run task delegation verification: `uv run task check` and `make check`.
- Verify git status to confirm no untracked artifacts, cache directories, or unintended modifications remain.

### Dependencies & Criticality

- **Depends on:** Steps 1, 2, 3, 4, 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1, §2, §3, `docs/safety.md` §1.
- **Potential failure modes for this step:** Transient formatting issues in newly generated documentation or modified
  docstrings.
- **Guardrails and early‑abort checks:** Run `uv run ruff check --fix .` and `npx prettier --write "**/*.md"` if minor
  formatting discrepancies emerge.

### Success & Discard Criteria

- **Success:** All test suites, linting checks, taskipy commands, and markdown formatting validations exit with return
  code 0.
- **Discard:** Discard if regressions are detected that cannot be resolved within the scope of Batch A.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.3   |
| impact_pred         | 90.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 85.41 |

### Step Metrics Rationale

- High `p_success_pred` (0.95) and low `entropy_pred` (0.3) for an execution check step that synthesizes previous steps.
- Derivation: `EV = 0.95 * 90.0 + 0.5 * 4.0 - 0.3 * 0.3 - 2.0 = 85.50 + 2.00 - 0.09 - 2.0 = 85.41`.
