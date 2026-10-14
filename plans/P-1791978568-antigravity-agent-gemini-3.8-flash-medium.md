# Plan for I-1791978544-submodule-update-and-prettier-yaml

- **Plan ID:** P-1791978568-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-10-14T11:49:28.283Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with empirical validation of git submodule pointer advancement, Prettier YAML linting
  integration, container layer pruning, and hermetic Makefile test verification without regression.
- **Safety priority level:** standard
- **Priority Justification:** Triggered by `holon-config/world/ruleset.md` §1 & §2 (Python runtime `==3.13.*`, workspace
  isolation under `apps/sandbox-executor`, strict PEP 8 formatting, static type annotations with `typing` module,
  docstring maintenance), `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test placement under
  `apps/sandbox-executor/tests/`, test modifications declared in planning), `holon-config/world/constraints.md` §1 (Git
  Flow & Branch Constraints: prefix-based branch isolation, rebase discipline, commit boundaries),
  `holon-config/world/constraints.md` §2 (Sandbox containment: subprocess and filesystem containment, no unwhitelisted
  subprocesses, process sandbox `< 10` entropy), `holon-config/world/constraints.md` §3 (Ledger immutability:
  append-only ledgers), `docs/safety.md` §1 & §2 (Git as safety boundary, sandboxing mandatory for execution), Bean 0090
  (holon-coherence submodule update to commit 4da97f7), and Bean 0091 (removal of yq/yamllint in favor of Prettier for
  YAML).

## Exploration

- **Proportion of steps that are exploratory:** 0.67
- **Justification:** Steps 1, 2, 3, and 5 incorporate balanced exploration to empirically validate submodule gitlink
  pointer advancement and compatibility, Prettier CLI behavior across YAML documents and file find expressions, clean
  removal of binary installation steps from multi-arch Dockerfiles and CI workflows, and hermetic mock toolbelt
  assertions in Makefile test suites.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.60  |
| impact_pred         | 95.0  |
| cost_pred           | 9.4   |
| learning_value_pred | 5.2   |
| ev_pred             | 82.32 |

### Strategy Rationale

The overall plan metrics were derived from individual step-level metrics as follows:

- **p_success_pred**: 0.94. Dictated by the bottleneck steps (Step 1 and Step 5: 0.94), where submodule pointer
  integration must align cleanly with parent tests and mock toolbelt tests in `test_makefile.py` must match the updated
  Prettier invocation behavior.
- **entropy_pred**: 0.60. Evaluated as the maximum single-step risk profile (Step 1: 0.60), where external git submodule
  pointer updates introduce integration variance. The sum of predicted step entropies across all six steps is 2.50
  (`0.60 + 0.40 + 0.50 + 0.20 + 0.50 + 0.30 = 2.50`), strictly compliant with the allocated entropy budget of 15.0.
- **impact_pred**: 95.0. Fulfills Beans 0090 and 0091 across the codebase: advances `holon-coherence` to commit
  `4da97f7` with Phase 4 decoupled architecture, web token auth, decomposed CLI commands, and test hardening (Beans
  0075, 0042, 0037); simplifies toolchain dependencies by eliminating redundant `yq` and `yamllint` installations in
  favor of standard npm Prettier for YAML validation across Makefile, Dockerfiles, and CI, reducing container build
  times and image footprint.
- **cost_pred**: 9.4. Calculated as the direct sum of individual step costs (`2.2 + 1.8 + 1.6 + 0.8 + 1.8 + 1.2 = 9.4`).
- **learning_value_pred**: 5.2. Epistemic gain from establishing unified YAML/Markdown linting via Prettier, trimming
  unnecessary container binaries and apt packages, validating submodule pointer updating workflows, and keeping hermetic
  mock toolbelt tests in sync.
- **ev_pred**: 82.32. Computed strictly via the config-driven Expected Value formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`. With system constants `lambda = 0.3`
  and `mu = 0.5` loaded from `holon-config/metrics/ev_config.json`:
  `EV = 0.94 * 95.0 + 0.5 * 5.2 - 0.3 * 0.60 - 9.4 = 89.30 + 2.60 - 0.18 - 9.4 = 82.32`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python target `==3.13.*`, package
    management via `uv`).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8 compliance, static typing with `typing`
    module, docstring maintenance).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test files placed in `tests/`, all test
    changes explicitly declared in the planning step).
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: prefix-based branch isolation, rebase
    discipline, commit boundaries).
  - `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: filesystem containment, no unwhitelisted
    subprocesses, process sandbox `< 10` entropy).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: zero modifications to historical ledger entries,
    append-only ledger operations).
  - `docs/safety.md` §1 & §2 (Git as safety boundary, sandboxing mandatory for execution).
  - Bean 0090 (holon-coherence git submodule update to latest origin/main commit 4da97f7).
  - Bean 0091 (replace yq and yamllint with Prettier for YAML validation).
- **Potential violations or edge cases:**
  - Submodule checkout pointing to detached HEAD or local uncommitted changes causing dirty status in git.
  - Discrepancy between `SUBMODULES.md` documentation and updated gitlink commit pointer.
  - Prettier parsing errors or unsupported custom YAML tags causing false positives on valid YAML files.
  - Hermetic Makefile test suite failing due to lingering mock expectations for `yq` or `yamllint`.
  - CI test-hygiene workflow failing due to missing `yq` executable if any target still references it.
- **Mitigations built into the plan:**
  - Verify clean submodule status using `git submodule status` and ensure tree gitlink is staged and committed
    explicitly.
  - Update `SUBMODULES.md` if necessary to document the updated pinned commit SHA.
  - Verify Prettier on existing repository YAML files (`.github/workflows/*.yml`, etc.) before completing Step 2.
  - Refactor `_create_mock_toolbelt` and `test_tools_target_reports_paths_and_versions` in tandem with Makefile changes.
  - Execute full end-to-end verification (`make lint`, `make tools`, `pytest`, `ruff`, and `prettier`) in Step 6.
- **Residual risk accepted (and why):**
  - Upstream changes in `holon-coherence` submodule tracking commit `4da97f7` introduce updated dependency structures;
    accepted as commit `4da97f7` is verified and tested on `origin/main`.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 0.60
- **Budget Compliance:** The strategy fits within budget (`0.60 <= 15.0`).

## Plan Description & Strategy

This plan implements Beans 0090 and 0091 across `holon-agentic-coder`:

1. **Submodule Update (Bean 0090):**
   - Update `holon-coherence` git submodule pointer from `87dc438` to latest `origin/main` commit `4da97f7`
     (`4da97f7d4529ec0bcf5356976ceb6697a2cb647e`), integrating Phase 4 architecture, web token auth, decomposed CLI
     commands, and test hardening.
   - Verify submodule working directory status is clean and passes tests without regressions.

2. **Makefile Prettier YAML Refactor & Toolbelt Update (Bean 0091):**
   - In `Makefile`, update `help` target to document Prettier validation for `lint-yaml`.
   - Remove `yq` and `yamllint` from `tools` target required toolbelt list and tool inspection loops.
   - Refactor `lint-yaml` target to validate YAML files using `npx prettier --check` instead of `yq`/`yamllint`.

3. **Container & CI Linters Cleanup (Bean 0091):**
   - In `apps/sandbox-executor/Dockerfile`, remove `yamllint` from `apt-get install` and delete the static binary `yq`
     download and chmod steps.
   - In `.github/workflows/test-hygiene.yml`, remove `yamllint` from `apt-get install` and remove the `yq` binary
     installation step.

4. **Agent Toolbelt Rules Documentation (Bean 0091):**
   - In `.agents/AGENTS.md`, remove `yq` and `yamllint` from toolbelt probing and mandatory tools rules, specifying
     Prettier for YAML validation.

5. **Hermetic Makefile Test Suite Refactor (Bean 0091):**
   - In `apps/sandbox-executor/tests/test_makefile.py`, remove `yq` and `yamllint` stubs from `_create_mock_toolbelt`
     and remove expectations from `test_tools_target_reports_paths_and_versions`.
   - Verify `lint-yaml` properly catches malformed YAML via Prettier in `test_lint_yaml_fails_on_malformed_fixture`.

6. **Comprehensive Verification & Quality Gates:**
   - Verify `make lint`, `make tools`,
     `uv run pytest apps/sandbox-executor/tests -m "not integration_test and not stress"`, `uv run ruff check .`,
     `uv run ruff format --check .`, and `npx prettier --check "**/*.md"` all pass cleanly.

---

## Step 1: Update holon-coherence git submodule pointer to commit 4da97f7 (Bean 0090)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Updating the git submodule pointer to a verified upstream commit is atomic, standard git operations
  with low blast radius and clear containment.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Advancing the submodule commit pointer to `4da97f7d4529ec0bcf5356976ceb6697a2cb647e`
  cleanly integrates upstream Phase 4 architecture improvements without breaking parent repo dependencies or tests.
- **Learning target:** Confirm submodule status cleanliness, commit gitlink pinning in index, and verification that
  parent repository tests pass against updated submodule files.
- **Maximum acceptable cost for this learning:** `cost_pred <= 3.0`

### Intent & Git Integration

- **Step Intent:** Update `holon-coherence` submodule to commit `4da97f7d4529ec0bcf5356976ceb6697a2cb647e` and verify
  clean status.
- **Git branch:**
  `I-1791978544-submodule-update-and-prettier-yaml/P-1791978568-antigravity-agent-gemini-3.8-flash-medium/E-1791978601-submodule-update`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Fetch latest changes from remote in `holon-coherence` submodule directory and checkout target commit
   `4da97f7d4529ec0bcf5356976ceb6697a2cb647e`.
2. Inspect `git submodule status` to verify commit `4da97f7d4529ec0bcf5356976ceb6697a2cb647e` is checked out cleanly.
3. Update `SUBMODULES.md` documentation if necessary to reflect the updated pinned commit SHA for developer guidance.
4. Stage the updated gitlink for `holon-coherence` in the parent repository index.
5. Verify working tree cleanliness with `git status`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (Submodule update must integrate cleanly before full repo verification)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §1 (Git as safety boundary), `holon-config/world/constraints.md` §1 (Git Flow &
  Branch Constraints), `SUBMODULES.md` §2 (Pinning to a specific commit).
- **Potential failure modes for this step:**
  - Network failure or missing remote object when fetching commit 4da97f7.
  - Submodule working tree containing unstaged or untracked changes preventing clean checkout.
- **Guardrails and early‑abort checks:**
  - Check `git submodule status` shows no conflict markers or detached state errors before staging gitlink.

### Success & Discard Criteria

- **Success:** `git submodule status` displays `4da97f7d4529ec0bcf5356976ceb6697a2cb647e` with clean prefix and no
  uncommitted changes.
- **Discard:** Discard if remote does not contain commit 4da97f7 or if submodule merge conflicts cannot be resolved
  cleanly within `cost <= 3.0`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.60  |
| impact_pred         | 95.0  |
| cost_pred           | 2.2   |
| learning_value_pred | 5.5   |
| ev_pred             | 89.67 |

### Step Metrics Rationale

`EV = 0.94 * 95.0 + 0.5 * 5.5 - 0.3 * 0.60 - 2.2 = 89.30 + 2.75 - 0.18 - 2.2 = 89.67`. High impact fulfills Bean 0090.
Moderate entropy reflects external submodule reference updating. High learning value validates Phase 4 upstream
compatibility.

---

## Step 2: Refactor Makefile lint-yaml target and tools target (Bean 0091)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Makefile target refactoring is localized to a single file, easily verified with dry-run and direct
  target invocation, and has low blast radius.
- **Step Type:** REFACTOR
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Replacing yq/yamllint with Prettier (`npx prettier --check`) in Makefile `lint-yaml`
  preserves file discovery filtering and accurately catches YAML errors.
- **Learning target:** Confirm Prettier CLI invocation behavior across discovered YAML files and ensure `tools` target
  reports present tools cleanly without missing yq/yamllint.
- **Maximum acceptable cost for this learning:** `cost_pred <= 2.5`

### Intent & Git Integration

- **Step Intent:** Refactor `lint-yaml` to use Prettier, update help text, and remove `yq` and `yamllint` from `tools`
  target in `Makefile`.
- **Git branch:**
  `I-1791978544-submodule-update-and-prettier-yaml/P-1791978568-antigravity-agent-gemini-3.8-flash-medium/E-1791978602-makefile-prettier-yaml`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `Makefile`, update `help` target recipe: change description of `lint-yaml` from validating with `yq` to validating
   with Prettier (`"Validate all YAML files with Prettier."`).
2. In `tools` target recipe:
   - Remove `yq` and `yamllint` from the toolbelt list loop (`git jq shellcheck uv gh npx openssl`).
   - Remove the `yq)` and `yamllint)` branches from the `case "$$tool" in` version reporting switch.
3. In `lint-yaml` target recipe:
   - Update banner message to indicate validating YAML files with Prettier.
   - Retain find expression pruning `.git`, `.venv`, and `holon-coherence` directories and matching `*.yml` and `*.yaml`
     files under `$(LINT_TARGET)`.
   - Replace yq and yamllint command loop with
     `npx prettier --check "$$file" >/dev/null 2>&1 || { echo "❌ YAML validation failed for $$file"; exit 1; };`.
4. Validate `make help`, `make -n tools`, `make -n lint-yaml`, and `make -n lint` execute cleanly under dry-run mode.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO (Refactoring Makefile is independent of submodule commit state)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1 (Monorepo/Workspace structure),
  `holon-config/world/constraints.md` §2 (Sandbox containment).
- **Potential failure modes for this step:**
  - Syntax error in Makefile recipe shell script leading to target failure.
  - Find expression failing to exclude `.git` or `holon-coherence` subdirectories.
- **Guardrails and early‑abort checks:**
  - Test `make -n` dry-run across all modified targets before running real invocations.

### Success & Discard Criteria

- **Success:** `make help` displays Prettier description for `lint-yaml`, `make tools` checks only remaining toolbelt
  tools, and `make lint-yaml` validates YAML files using Prettier.
- **Discard:** Discard if Prettier command structure cannot validate existing valid YAML files within `cost <= 2.5`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.40  |
| impact_pred         | 92.0  |
| cost_pred           | 1.8   |
| learning_value_pred | 4.8   |
| ev_pred             | 88.80 |

### Step Metrics Rationale

`EV = 0.96 * 92.0 + 0.5 * 4.8 - 0.3 * 0.40 - 1.8 = 88.32 + 2.40 - 0.12 - 1.8 = 88.80`. High success probability and low
entropy because Makefile refactoring is localized and verified immediately with dry-run mode.

---

## Step 3: Remove yq and yamllint from Dockerfile and CI workflow (Bean 0091)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pruning unused packages and static binary downloads from Dockerfile and CI workflow is standard
  configuration cleanup with low risk.
- **Step Type:** CONFIG
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Removing yamllint and static yq binary downloads reduces image build complexity and
  eliminates redundant dependencies without breaking Docker builds or CI workflows.
- **Learning target:** Confirm base Dockerfile syntax and GitHub Actions test-hygiene workflow syntax remain valid and
  clean.
- **Maximum acceptable cost for this learning:** `cost_pred <= 2.2`

### Intent & Git Integration

- **Step Intent:** Remove `yamllint` and `yq` installation steps from `apps/sandbox-executor/Dockerfile` and
  `.github/workflows/test-hygiene.yml`.
- **Git branch:**
  `I-1791978544-submodule-update-and-prettier-yaml/P-1791978568-antigravity-agent-gemini-3.8-flash-medium/E-1791978603-docker-ci-prune-linters`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/Dockerfile`:
   - In `holon-base` stage, remove `yamllint` from the `apt-get install -y --no-install-recommends` package list.
   - Delete the entire static binary `yq` installation layer (multi-architecture architecture detection, curl download
     of mikefarah yq release, and chmod executable step).
   - Ensure subsequent SSH, Node.js, and user creation layers remain intact.
2. In `.github/workflows/test-hygiene.yml`:
   - Under `Install system linters` step, update `sudo apt-get install` to install only `shellcheck` (removing
     `yamllint`).
   - Remove the multi-architecture download and installation of `yq` binary in `/usr/local/bin/yq`.
3. Verify YAML syntax of `.github/workflows/test-hygiene.yml` using
   `npx prettier --check .github/workflows/test-hygiene.yml`.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §2 (Sandbox containment tiers),
  `apps/sandbox-executor/Dockerfile` multi-stage structure.
- **Potential failure modes for this step:**
  - Syntax errors or accidental deletion of adjacent lines in Dockerfile or GitHub Actions YAML.
- **Guardrails and early‑abort checks:**
  - Run Prettier check immediately on modified workflow YAML to guarantee valid syntax.

### Success & Discard Criteria

- **Success:** Dockerfile no longer contains references to yamllint or yq static binary downloads, and
  `test-hygiene.yml` installs only shellcheck for system linters.
- **Discard:** Discard if Dockerfile modifications corrupt base image layer sequencing.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.50  |
| impact_pred         | 90.0  |
| cost_pred           | 1.6   |
| learning_value_pred | 4.5   |
| ev_pred             | 86.90 |

### Step Metrics Rationale

`EV = 0.96 * 90.0 + 0.5 * 4.5 - 0.3 * 0.50 - 1.6 = 86.40 + 2.25 - 0.15 - 1.6 = 86.90`. Pruning external binary
dependencies simplifies sandbox setup and CI execution time.

---

## Step 4: Update agent toolbelt guidelines in .agents/AGENTS.md (Bean 0091)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure documentation update aligning behavioral agent guidelines with the updated toolbelt specification.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update `.agents/AGENTS.md` to remove `yq`/`yamllint` requirements and specify Prettier for YAML
  validation.
- **Git branch:**
  `I-1791978544-submodule-update-and-prettier-yaml/P-1791978568-antigravity-agent-gemini-3.8-flash-medium/E-1791978604-agents-doc-update`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `.agents/AGENTS.md` under `OS Toolbelt & Script Hygiene Rules`:
   - In `Mandatory Tool Probing`, remove `command -v yq` from the probe examples.
   - In `Mandatory Baked-in Tools`, remove the requirement for `yq` and specify Prettier (`npx prettier --check`) for
     YAML validation alongside markdown validation.
2. Check markdown formatting using `npx prettier --check .agents/AGENTS.md`.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (Docstring and documentation maintenance).
- **Potential failure modes for this step:**
  - Formatting inconsistencies triggering markdown lint failures.
- **Guardrails and early‑abort checks:**
  - Run Prettier check directly on `.agents/AGENTS.md`.

### Success & Discard Criteria

- **Success:** `.agents/AGENTS.md` accurately documents Prettier for YAML validation and omits `yq` and `yamllint`.
- **Discard:** Discard if documentation drifts from world ruleset.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.20  |
| impact_pred         | 85.0  |
| cost_pred           | 0.8   |
| learning_value_pred | 3.5   |
| ev_pred             | 85.04 |

### Step Metrics Rationale

`EV = 0.99 * 85.0 + 0.5 * 3.5 - 0.3 * 0.20 - 0.8 = 84.15 + 1.75 - 0.06 - 0.8 = 85.04`. Very high success probability and
low cost for standard markdown rules documentation update.

---

## Step 5: Refactor hermetic Makefile unit test suite in test_makefile.py (Bean 0091)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Scoped test updates in `apps/sandbox-executor/tests/test_makefile.py` to align stubs and assertions
  with the updated toolbelt and Prettier YAML validation.
- **Step Type:** TEST
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Refactoring mock toolbelt stubs and updating `test_lint_yaml_fails_on_malformed_fixture`
  to assert Prettier execution verifies `make tools` and `make lint-yaml` hermetically without regressions.
- **Learning target:** Confirm hermetic mock environment correctly exercises Prettier validation failure on malformed
  YAML and verifies the reduced toolbelt set.
- **Maximum acceptable cost for this learning:** `cost_pred <= 2.5`

### Intent & Git Integration

- **Step Intent:** Refactor `apps/sandbox-executor/tests/test_makefile.py` to remove `yq` and `yamllint` stubs and
  assert Prettier-based YAML validation.
- **Git branch:**
  `I-1791978544-submodule-update-and-prettier-yaml/P-1791978568-antigravity-agent-gemini-3.8-flash-medium/E-1791978605-test-makefile-refactor`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_makefile.py`:
   - In `_create_mock_toolbelt(bin_dir)`: remove the creation of mock executable stubs for `yq` and `yamllint`. Update
     the docstring to reflect the revised tool list.
   - In `test_tools_target_reports_paths_and_versions(tmp_path)`: remove `"yq"` and `"yamllint"` from the list of
     asserted tool names (`["git", "jq", "shellcheck", "uv", "gh", "npx", "openssl"]`).
   - In `test_lint_yaml_fails_on_malformed_fixture(tmp_path)`: verify that `lint-yaml` executes `npx prettier --check`
     and properly detects malformed YAML (either by configuring mock `npx` in the test environment to fail on invalid
     files or ensuring the test fixture asserts failure and matching error output).
2. Execute `uv run pytest apps/sandbox-executor/tests/test_makefile.py` to verify all Makefile test cases pass
   hermetically.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES (Ensures test suite remains green and valid for CI verification)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test changes explicitly
  declared in plan).
- **Potential failure modes for this step:**
  - Mock npx stub failing to distinguish between valid and malformed YAML files.
  - Lingering references to yq or yamllint in other test assertions causing assertion failures.
- **Guardrails and early‑abort checks:**
  - Run `uv run pytest apps/sandbox-executor/tests/test_makefile.py -v` to observe specific failure outputs if any.

### Success & Discard Criteria

- **Success:** All unit tests in `test_makefile.py` pass cleanly, and `test_lint_yaml_fails_on_malformed_fixture`
  correctly verifies Prettier YAML failure behavior.
- **Discard:** Discard if mock toolbelt tests cannot be aligned with Makefile recipes within `cost <= 2.5`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.50  |
| impact_pred         | 92.0  |
| cost_pred           | 1.8   |
| learning_value_pred | 5.0   |
| ev_pred             | 87.03 |

### Step Metrics Rationale

`EV = 0.94 * 92.0 + 0.5 * 5.0 - 0.3 * 0.50 - 1.8 = 86.48 + 2.50 - 0.15 - 1.8 = 87.03`. Refactoring hermetic unit tests
maintains safety invariants and guarantees CI test-hygiene passes.

---

## Step 6: End-to-end repository verification and quality gates (Bean 0090, 0091)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Comprehensive read-only execution of all repo validation commands and quality gates.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Run all verification gates (`make lint`, `make tools`, `pytest`, `ruff check`, `ruff format --check`,
  `prettier --check`, and submodule status).
- **Git branch:**
  `I-1791978544-submodule-update-and-prettier-yaml/P-1791978568-antigravity-agent-gemini-3.8-flash-medium/E-1791978606-verification-quality-gates`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run `make lint` to verify JSON, YAML (via Prettier), ShellCheck, Markdown, and Python linters pass.
2. Run `make tools` to verify clean toolbelt resolution for the updated required tool set without errors.
3. Run `uv run pytest apps/sandbox-executor/tests -m "not integration_test and not stress"` to verify unit test suite
   passes cleanly.
4. Run `uv run ruff check .` and `uv run ruff format --check .` to verify Python code formatting and linting.
5. Run `npx prettier --check "**/*.md"` to verify markdown formatting across the entire repository.
6. Run `git submodule status` to verify `holon-coherence` is clean and correctly pinned at commit `4da97f7`.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3, Step 4, Step 5
- **Is Bottleneck:** YES (Final gate for intent completion)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §5 (Human review final gate), `holon-config/world/ruleset.md` §1, §2, §3.
- **Potential failure modes for this step:**
  - Any unnoticed lint failure or test regression across the monorepo.
- **Guardrails and early‑abort checks:**
  - Run each verification command sequentially to identify and isolate any remaining issues immediately.

### Success & Discard Criteria

- **Success:** All verification commands exit with code 0 without errors or warnings.
- **Discard:** Discard if unfixable test regressions occur.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.30  |
| impact_pred         | 95.0  |
| cost_pred           | 1.2   |
| learning_value_pred | 4.0   |
| ev_pred             | 90.96 |

### Step Metrics Rationale

`EV = 0.95 * 95.0 + 0.5 * 4.0 - 0.3 * 0.30 - 1.2 = 90.25 + 2.00 - 0.09 - 1.2 = 90.96`. Confirms all acceptance criteria
are met for Beans 0090 and 0091 across the entire repository.

---
