# Plan for I-1791553778-add-gh-and-openssl-prerequisite-checks

- **Plan ID:** P-1791553791-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-10-09T13:49:51.110Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted empirical validation of non-interactive GitHub CLI authentication
  status probing and hermetic mock execution of Makefile recipes in pytest subshells without compromising repository
  invariants.
- **Safety priority level:** standard
- **Priority Justification:** Triggered by `holon-config/world/ruleset.md` §1 & §2 (Python runtime `==3.13.*`, workspace
  isolation under `apps/sandbox-executor`, strict PEP 8 formatting, explicit static typing with `typing` module,
  docstring maintenance), `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test file placement
  under `apps/sandbox-executor/tests/`, test changes explicitly declared in the planning step),
  `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: prefix-based branch isolation, rebase
  discipline, commit boundaries), `holon-config/world/constraints.md` §2 (Sandbox containment: subprocess and filesystem
  containment, no unwhitelisted subprocesses, process sandbox `< 10` entropy), `docs/safety.md` §1 & §2 (Git as safety
  boundary, sandboxing mandatory for execution), and Bean 0079.

## Exploration

- **Proportion of steps that are exploratory:** 0.50
- **Justification:** Steps 1 and 3 incorporate balanced exploration to empirically validate headless non-interactive
  `gh auth status` failure behavior and establish hermetic, environment-isolated test patterns for Makefile targets.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.8   |
| impact_pred         | 90.0  |
| cost_pred           | 6.5   |
| learning_value_pred | 5.0   |
| ev_pred             | 78.56 |

### Strategy Rationale

The overall plan metrics were derived from individual step-level metrics as follows:

- **p_success_pred**: 0.92. Dictated by the bottleneck step (Step 3: 0.92), where hermetic subprocess invocation of
  `make` targets with synthesized temporary PATH environments, isolated binary shims, and dry-run flag validation must
  reliably execute across heterogeneous developer platforms without flakiness or host contamination.
- **entropy_pred**: 0.8. Derived as the maximum single-step risk profile (Step 3: 0.8), where subprocess test fixtures,
  directory manipulation, and environment variable overwrites are evaluated. The sum of predicted step entropies is 1.8
  (`0.5 + 0.3 + 0.8 + 0.2 = 1.8`), well within the allocated entropy budget of 15.0.
- **impact_pred**: 90.0. Fully satisfies Bean 0079 by establishing deterministic prerequisite verification for the
  GitHub CLI (`gh`) and OpenSSL (`openssl`), preventing downstream developer friction during release bootstrapping and
  Root CA generation in `ca_generator.py` for token-reduction MITM proxies, and establishing hermetic regression test
  coverage in `apps/sandbox-executor/tests/test_makefile.py`.
- **cost_pred**: 6.5. Calculated as the direct sum of individual step costs (`1.5 + 1.0 + 3.0 + 1.0 = 6.5`).
- **learning_value_pred**: 5.0. Epistemic gain from formalizing headless non-interactive authentication validation
  patterns for CLI utilities and documenting hermetic Makefile recipe unit testing patterns in the test suite.
- **ev_pred**: 78.56. Computed strictly via the config-driven Expected Value formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`. With system constants `lambda = 0.3`
  and `mu = 0.5` loaded from `holon-config/metrics/ev_config.json`:
  `EV = 0.92 * 90.0 + 0.5 * 5.0 - 0.3 * 0.8 - 6.5 = 82.80 + 2.50 - 0.24 - 6.5 = 78.56`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python target `==3.13.*`, package
    management via `uv`).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8 compliance, static typing with `typing`
    module, complete docstrings).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test files placed in `tests/`, all test
    changes explicitly declared in the planning step).
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: prefix-based branch isolation, rebase
    discipline, commit boundaries).
  - `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: filesystem containment, no unwhitelisted
    subprocesses, process sandbox `< 10` entropy).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: zero modifications to historical ledger entries).
  - `docs/safety.md` §1 & §2 (Git as safety boundary, sandboxing mandatory for execution).
- **Potential violations or edge cases:**
  - Blocking interactive prompts when running `gh auth status` in headless container or CI environments without active
    terminals.
  - Hard failure in `check-prerequisites` on missing `gh` authentication when only tool presence is strictly required
    for local compilation, creating unnecessary developer friction.
  - Flaky test behavior in `test_makefile.py` if tests rely on host system tools (`gh`, `openssl`, `docker`) rather than
    hermetic mocked paths or isolated shims.
  - Accidental side effects during `make -n` (dry run) execution if recipes contain unescaped dynamic shell operations.
  - Divergent platform installation instructions across Linux distributions and macOS Homebrew environments.
- **Mitigations built into the plan:**
  - Structure `gh auth status` check with redirection `>/dev/null 2>&1` and non-zero exit handling as an advisory
    warning (`WARNINGS=$((WARNINGS + 1))`), ensuring unauthenticated sessions do not halt prerequisite verification.
  - Implement OS-sensitive installation messages (`brew install gh` for Darwin, `sudo apt install gh` for Linux;
    `brew install openssl` for Darwin, `sudo apt install openssl` for Linux).
  - Construct tests in `test_makefile.py` with mock PATH shims using temporary directories and fake shell scripts to
    test presence, absence, version parsing, and exit codes hermetically without touching the host operating system.
  - Verify dry-run flags (`make -n`) execute cleanly without triggering active process spawns or state changes.
  - Enforce comprehensive test validation across `uv run pytest apps/sandbox-executor/tests/test_makefile.py`,
    `uv run task test`, `uv run task check`, and `npx prettier --check "**/*.md"`.
- **Residual risk accepted (and why):**
  - Package manager commands on non-Debian Linux distributions (e.g., `dnf`, `pacman`) may differ slightly from the
    Debian `apt` guidance; accepted as Debian/Ubuntu is the standard container and Linux baseline for Holon.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 1.8
- **Budget Compliance:** The strategy fits within budget (Predicted Plan Entropy sum of 1.8 is well below the 15.0
  allocated budget).

## Plan Description & Strategy

This plan implements Bean 0079 across `holon-agentic-coder`:

1. **Step 1: GitHub CLI (`gh`) Prerequisite Validation & Non-Blocking Auth Status Probe:** In `Makefile`
   `check-prerequisites` target, add validation for `gh`. Verify tool existence with `command -v gh`. If absent, output
   platform-appropriate installation instructions (`brew install gh` for macOS Darwin, `sudo apt install gh` for Linux)
   and increment `ERRORS`. If present, report the detected version (`gh --version | head -n 1`). Subsequently probe
   `gh auth status >/dev/null 2>&1`; if unauthenticated, emit an advisory warning indicating unauthenticated status with
   remediation guidance (`gh auth login`) and increment `WARNINGS` without aborting. Update `make help` and target
   documentation comments.
2. **Step 2: OpenSSL (`openssl`) Prerequisite & Version Validation:** In `Makefile` `check-prerequisites` target, add
   validation for `openssl` (required by `ca_generator.py` for Root CA certificate generation during token-reduction TLS
   interception). Check tool existence with `command -v openssl`. If absent, output platform-appropriate installation
   instructions (`brew install openssl` for macOS, `sudo apt install openssl` for Linux) and increment `ERRORS`. If
   present, report the detected version (`openssl version`). Update target documentation and comments.
3. **Step 3: Hermetic Unit Test Suite for Makefile Targets and Prerequisite Probes:** In
   `apps/sandbox-executor/tests/test_makefile.py`, create a hermetic test suite adhering to `pytest==9.1.1` and repo
   standards. Assert that `make help` outputs expected targets and prerequisite tool descriptions. Test that dry-run
   mode (`make -n <target>`) succeeds with exit code 0 across all core targets (`help`, `check-prerequisites`,
   `check-docker`, `build-images`, `install-docker`, `install-homebrew`, `prerequisites`). Implement hermetic test
   fixtures utilizing isolated temporary directories in `PATH` with executable dummy scripts to simulate all
   permutations: `gh` missing, `gh` present but unauthenticated, `gh` authenticated, `openssl` missing, `openssl`
   present, and fully satisfied prerequisites.
4. **Step 4: Verification, Test Execution, Code Quality, and Documentation Hygiene:** Verify that the newly created test
   file passes cleanly (`uv run pytest apps/sandbox-executor/tests/test_makefile.py`), run the full test suite
   (`uv run task test` / `uv run pytest apps/sandbox-executor/tests`), verify formatting and type discipline
   (`uv run task check`), and validate markdown hygiene across the repository (`npx prettier --check "**/*.md"`).

---

## Step 1: GitHub CLI (`gh`) Prerequisite Validation & Non-Blocking Auth Status Probe (Bean 0079)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Scoped Makefile target extension with negligible complexity, low cost, and clear deterministic shell
  behavior.
- **Step Type:** CONFIG
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Invoking `gh auth status >/dev/null 2>&1` in non-interactive shell environments reliably
  indicates authentication state via process returncode without blocking on credential prompts or raising unhandled
  signals.
- **Learning target:** Confirm the non-zero exit behavior of `gh auth status` under unauthenticated and missing-token
  conditions in containerized or headless environments.
- **Maximum acceptable cost for this learning:** 1.5

### Intent & Git Integration

- **Step Intent:** Add GitHub CLI (`gh`) presence verification, version output, and advisory authentication checking to
  `Makefile` `check-prerequisites`, updating target help text and failure instructions accordingly.
- **Git branch:**
  `I-1791553778-add-gh-and-openssl-prerequisite-checks/P-1791553791-antigravity-agent-gemini-3.8-flash-medium/E-gh-prerequisite-check/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `Makefile`, locate the `help` target and update the description for `check-prerequisites` to explicitly enumerate
  `gh` alongside existing tools.
- In `Makefile`, locate the `check-prerequisites` target recipe block.
- Add formatted section checking for `gh` CLI presence using `command -v gh >/dev/null 2>&1`.
- If `gh` is missing: print red error message reporting `GitHub CLI (gh) not found`, output actionable installation
  guidance conditioned on `DETECTED_OS` (`brew install gh` for Darwin, `sudo apt install gh` for Linux or other
  platforms), and increment `ERRORS`.
- If `gh` is present: capture the first line of `gh --version 2>/dev/null` and print green confirmation message.
- Following version output, execute `gh auth status >/dev/null 2>&1`. If the command exits with non-zero status: print a
  yellow advisory warning indicating `gh is not authenticated`, recommend running `gh auth login`, and increment
  `WARNINGS` so unauthenticated status is advisory rather than a fatal prerequisite error.
- Ensure all recipe logic respects `MAKEFLAGS` dry-run checking (`findstring n,$(filter-out --%,$(MAKEFLAGS)))`).

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §1 (Git isolation), `holon-config/world/ruleset.md` §2 (Coding conventions),
  `holon-config/world/constraints.md` §2 (Process sandbox containment).
- **Potential failure modes for this step:** If `gh auth status` attempts to prompt for input or hang on terminal reads,
  make execution would stall.
- **Guardrails and early‑abort checks:** Redirection to `/dev/null` on standard input/output/error and evaluation of
  returncode prevents interactive terminal prompts.

### Success & Discard Criteria

- **Success:** Running `make check-prerequisites` checks for `gh`, reports version when installed, alerts on
  unauthenticated status with an advisory warning, or fails with platform-specific install instructions when missing.
- **Discard:** Discard if `gh auth status` causes `make` to freeze or fails critically on unauthenticated local
  environments.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.5   |
| impact_pred         | 82.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 4.5   |
| ev_pred             | 78.50 |

### Step Metrics Rationale

- High `p_success_pred` (0.95) and modest `entropy_pred` (0.5) reflect standard Makefile recipe enhancement with
  defensive returncode handling.
- `cost_pred` is 1.5 due to quick shell testing and verification.
- Derivation: `EV = 0.95 * 82.0 + 0.5 * 4.5 - 0.3 * 0.5 - 1.5 = 77.90 + 2.25 - 0.15 - 1.5 = 78.50`.

---

## Step 2: OpenSSL (`openssl`) Prerequisite & Version Validation (Bean 0079)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Straightforward prerequisite probe addition to existing Makefile block with negligible risk and zero
  architectural divergence.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add OpenSSL (`openssl`) presence check, version reporting, and failure guidance to `Makefile`
  `check-prerequisites` to guarantee Root CA generation readiness for `ca_generator.py`.
- **Git branch:**
  `I-1791553778-add-gh-and-openssl-prerequisite-checks/P-1791553791-antigravity-agent-gemini-3.8-flash-medium/E-openssl-prerequisite-check/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `Makefile`, locate the `help` target and ensure `openssl` is included in the descriptive text for
  `check-prerequisites`.
- In `Makefile`, locate the `check-prerequisites` target recipe block.
- Add formatted section checking for `openssl` CLI presence using `command -v openssl >/dev/null 2>&1`.
- If `openssl` is missing: print red error message reporting `openssl not found`, output actionable installation
  guidance conditioned on `DETECTED_OS` (`brew install openssl` for Darwin, `sudo apt install openssl` for Linux), state
  that OpenSSL is required for token reduction Root CA generation (`ca_generator.py`), and increment `ERRORS`.
- If `openssl` is present: capture output of `openssl version 2>/dev/null` and print green confirmation message.
- Update Makefile comments explaining the requirement for OpenSSL in relation to Bean 0079 and `ca_generator.py`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §1 (Git isolation), `holon-config/world/ruleset.md` §2 (Standards), Bean 0079.
- **Potential failure modes for this step:** Incompatible version output flags on older OpenSSL or LibreSSL releases.
- **Guardrails and early‑abort checks:** Use standard `openssl version` which is universally supported across OpenSSL
  1.1, OpenSSL 3.x, and LibreSSL distributions.

### Success & Discard Criteria

- **Success:** Running `make check-prerequisites` verifies `openssl`, displays the version when present, and reports
  platform install instructions when absent.
- **Discard:** Discard if command detection fails on standard host environments where openssl is installed.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.3   |
| impact_pred         | 80.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 2.5   |
| ev_pred             | 78.56 |

### Step Metrics Rationale

- Very high `p_success_pred` (0.98) and low `entropy_pred` (0.3) due to standard CLI invocation semantics matching
  existing `uv` and `npx` probes.
- `cost_pred` is 1.0.
- Derivation: `EV = 0.98 * 80.0 + 0.5 * 2.5 - 0.3 * 0.3 - 1.0 = 78.40 + 1.25 - 0.09 - 1.0 = 78.56`.

---

## Step 3: Hermetic Unit Test Suite for Makefile Targets and Prerequisite Probes (Bean 0079)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standardized pytest test module addition under `apps/sandbox-executor/tests/` with isolated test
  boundaries and no runtime package modifications.
- **Step Type:** TEST
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Simulating command presence, absence, and failure codes by overriding `PATH` to point to
  temporary directories containing lightweight mock executable scripts enables 100% hermetic testing of Makefile targets
  without modifying host tools or requiring container privileges.
- **Learning target:** Determine the boundary conditions of `make -n` dry-run evaluation and verify cross-platform
  recipe execution inside `subprocess.run` calls within `pytest`.
- **Maximum acceptable cost for this learning:** 3.0

### Intent & Git Integration

- **Step Intent:** Create `apps/sandbox-executor/tests/test_makefile.py` providing hermetic unit tests verifying
  `make help`, dry-run behavior (`make -n`) across core targets, and comprehensive prerequisite check permutations for
  `gh` and `openssl`.
- **Git branch:**
  `I-1791553778-add-gh-and-openssl-prerequisite-checks/P-1791553791-antigravity-agent-gemini-3.8-flash-medium/E-hermetic-makefile-tests/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `apps/sandbox-executor/tests/test_makefile.py` adhering to `pytest==9.1.1` and Python 3.13 standards, with
  complete module and function docstrings and typing annotations.
- Define a fixture or helper locating the root `Makefile` relative to the test file.
- Implement `test_makefile_help_lists_core_targets`: execute `make help` via
  `subprocess.run(capture_output=True, text=True, check=True)` and assert that `help`, `build-images`,
  `check-prerequisites`, `check-docker`, `install-docker`, and `install-homebrew` are documented, and verify the help
  text mentions `gh` and `openssl`.
- Implement `test_makefile_dry_run_core_targets`: iterate through all core targets (`help`, `check-prerequisites`,
  `check-docker`, `build-images`, `install-docker`, `install-homebrew`, `prerequisites`), executing `make -n <target>`,
  asserting exit code 0 and verifying that dry-run mode executes without performing real side effects.
- Implement hermetic test fixture `mock_bin_dir` using `tmp_path`: creates an isolated executable directory with
  symlinks or wrapper shims for essential system utilities (`sh`, `bash`, `uname`, `head`, `printf`, `echo`, `grep`,
  `make`).
- Implement test `test_check_prerequisites_fails_when_gh_missing`: construct environment where `gh` is absent from
  `PATH`. Run `make check-prerequisites` and assert that the process fails with exit code 1, stderr/stdout contains
  `GitHub CLI (gh) not found`, and appropriate platform guidance (`brew install gh` or `apt install gh`) is printed.
- Implement test `test_check_prerequisites_warns_when_gh_unauthenticated`: construct mock `gh` returning version string
  on `--version` but returning exit code 1 on `auth status`. Run `make check-prerequisites` and assert that exit code is
  0, stdout contains advisory warning `gh is not authenticated`, and mentions `gh auth login`.
- Implement test `test_check_prerequisites_passes_when_gh_authenticated`: construct mock `gh` returning exit code 0 for
  both `--version` and `auth status`. Assert no warning is raised for `gh`.
- Implement test `test_check_prerequisites_fails_when_openssl_missing`: construct environment where `openssl` is absent
  from `PATH`. Assert exit code 1, stdout contains `openssl not found`, and install instructions are provided.
- Implement test `test_check_prerequisites_passes_when_openssl_present`: construct mock `openssl` outputting standard
  version line. Assert successful version reporting.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (Validation of entire prerequisite suite relies on this comprehensive test suite)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (Testing Constraints: test location in
  `apps/sandbox-executor/tests/`, test changes explicitly declared), `holon-config/world/constraints.md` §2 (No
  unwhitelisted subprocesses, process containment).
- **Potential failure modes for this step:** Overriding `PATH` too aggressively might hide `make` or basic shell
  utilities, causing test execution failures.
- **Guardrails and early‑abort checks:** Ensure the mock PATH fixture prepends temporary directory to existing system
  PATH rather than completely clearing required system binaries, or explicitly whitelist system shell paths.

### Success & Discard Criteria

- **Success:** `apps/sandbox-executor/tests/test_makefile.py` executes hermetically and all test cases pass without
  depending on the host environment's actual tool installation status.
- **Discard:** Discard if tests fail when run on clean environments lacking `gh` or `docker`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.8   |
| impact_pred         | 88.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 5.5   |
| ev_pred             | 80.47 |

### Step Metrics Rationale

- `p_success_pred` (0.92) is the plan bottleneck due to the intricacies of subprocess environment isolation and mock
  executable scripting in pytest.
- `entropy_pred` is 0.8 due to temporary filesystem structures and sub-process execution.
- `cost_pred` is 3.0 for test suite design, debugging, and verification.
- `learning_value_pred` is 5.5 for creating reusable hermetic Makefile testing fixtures in Holon.
- Derivation: `EV = 0.92 * 88.0 + 0.5 * 5.5 - 0.3 * 0.8 - 3.0 = 80.96 + 2.75 - 0.24 - 3.0 = 80.47`.

---

## Step 4: Verification, Test Execution, Code Quality, and Documentation Hygiene

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard end-to-end verification step confirming full regression test passing and code hygiene.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Verify the end-to-end changes through pytest, task runner checks, linter and formatter validation,
  and Prettier markdown formatting.
- **Git branch:**
  `I-1791553778-add-gh-and-openssl-prerequisite-checks/P-1791553791-antigravity-agent-gemini-3.8-flash-medium/E-verification-and-hygiene/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Execute `uv run pytest apps/sandbox-executor/tests/test_makefile.py` to confirm all Makefile unit tests pass cleanly.
- Execute `uv run task test` (or `uv run pytest apps/sandbox-executor/tests`) to guarantee zero regression across the
  entire test suite.
- Execute `uv run task check` (running `ruff check .` and `ruff format --check .`) to verify full style and type
  compliance.
- Execute `npx prettier --check "**/*.md"` to verify repository-wide markdown formatting hygiene.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1 & §2, `docs/safety.md` §1.
- **Potential failure modes for this step:** Prettier formatting discrepancies or Ruff lint errors in newly created test
  files.
- **Guardrails and early‑abort checks:** Format files using `uv run task format` if any minor formatting issues occur.

### Success & Discard Criteria

- **Success:** All test suites pass with 0 failures, Ruff reports clean formatting, and Prettier validates all markdown
  files.
- **Discard:** Discard if regressions are introduced to existing test suites or formatting cannot be satisfied.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.2   |
| impact_pred         | 75.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 73.94 |

### Step Metrics Rationale

- Very high `p_success_pred` (0.99) and minimal `entropy_pred` (0.2) as this is a read-only verification step.
- `cost_pred` is 1.0.
- Derivation: `EV = 0.99 * 75.0 + 0.5 * 1.5 - 0.3 * 0.2 - 1.0 = 74.25 + 0.75 - 0.06 - 1.0 = 73.94`.
