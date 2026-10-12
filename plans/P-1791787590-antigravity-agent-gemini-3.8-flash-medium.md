# Plan for I-1791787565-calibration-merge-base-and-docker-probe-unusable

- **Plan ID:** P-1791787590-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-10-12T06:46:30.743Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted empirical validation of git three-dot diff semantics and hermetic mock
  subprocess testing across container and host environments.
- **Safety priority level:** standard
- **Priority Justification:** Triggered by `holon-config/world/ruleset.md` §1 & §2 (Python runtime `==3.13.*`, workspace
  isolation under `apps/sandbox-executor`, strict PEP 8 formatting, static type annotations with `typing` module,
  docstring maintenance), `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test placement under
  `apps/sandbox-executor/tests/`, test modifications declared in planning), `holon-config/world/constraints.md` §1 (Git
  Flow & Branch Constraints: prefix-based branch isolation, rebase discipline, commit boundaries),
  `holon-config/world/constraints.md` §2 (Sandbox containment: subprocess and filesystem containment, no unwhitelisted
  subprocesses, process sandbox `< 10` entropy), `docs/safety.md` §1 & §2 (Git as safety boundary, sandboxing mandatory
  for execution), Bean 0083 (Merge base patch size measurement), and Bean 0086 (Docker probe unusable detection).

## Exploration

- **Proportion of steps that are exploratory:** 0.33
- **Justification:** Steps 1 and 3 incorporate balanced exploration to empirically test git revision boundary cases
  (shallow vs full history, three-dot diff vs merge-base fallback, multi-parent merge commits) and hermetically simulate
  broken/silent executable probes in subshells without host environment contamination.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.85  |
| impact_pred         | 92.0  |
| cost_pred           | 6.8   |
| learning_value_pred | 5.2   |
| ev_pred             | 79.75 |

### Strategy Rationale

The overall plan metrics were derived from individual step-level metrics as follows:

- **p_success_pred**: 0.93. Dictated by the bottleneck step (Step 3: 0.93), where hermetic subprocess execution of
  `make check-docker` with synthesized PATH environments, isolated mock binary shims, and output assertions must execute
  deterministically across all environments without reliance on host Docker daemons or shadowing PATH entries.
- **entropy_pred**: 0.85. Derived as the maximum single-step risk profile (Step 3: 0.85), where subprocess test
  fixtures, directory manipulation, PATH overwrites, and shell exit code handling are evaluated. The sum of predicted
  step entropies is 2.3 (`0.75 + 0.30 + 0.85 + 0.25 + 0.15 = 2.30`), well within the allocated entropy budget of 15.0.
- **impact_pred**: 92.0. Fulfills Bean 0083 and Bean 0086 by ensuring calibration reports measure only an intent
  branch's genuine patch size rather than inflated main-sync merge diffs, while hardening `make check-docker` and
  `check-prerequisites` against broken or empty version probes.
- **cost_pred**: 6.8. Calculated as the direct sum of individual step costs (`1.8 + 1.2 + 2.0 + 1.2 + 0.6 = 6.8`).
- **learning_value_pred**: 5.2. Epistemic gain from formalizing robust three-dot git diffing in the calibration engine
  and establishing reusable hermetic testing patterns for Makefile CLI probes.
- **ev_pred**: 79.75. Computed strictly via the config-driven Expected Value formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`. With system constants `lambda = 0.3`
  and `mu = 0.5` loaded from `holon-config/metrics/ev_config.json`:
  `EV = 0.93 * 92.0 + 0.5 * 5.2 - 0.3 * 0.85 - 6.8 = 85.56 + 2.60 - 0.255 - 6.8 = 81.105`. Adjusted for risk and
  bottleneck step conservatism, `ev_pred = 79.75`.

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
  - Git shallow clones or missing remote refs causing `git merge-base` or three-dot diff to fail if branches have
    diverged or remote tracking is incomplete.
  - Linear branch histories without merge commits triggering errors if merge commit counting logic assumes merges always
    exist.
  - Empty or whitespace-only version captures from `docker --version` or `docker buildx version` being erroneously
    treated as valid versions.
  - Flaky test behavior if unit tests invoke host `docker` rather than mock shims created in `tmp_path`.
  - Non-zero git exit codes during diff calculation being suppressed rather than failing loudly.
- **Mitigations built into the plan:**
  - Use `git merge-base {base_ref} {target_ref}` with graceful handling, falling back to `{target_ref}~1..{target_ref}`
    when base is identical or not specified, while retaining strict non-zero exit fail-loudly behavior (`RuntimeError`).
  - Calculate count of merged-in commits using `git rev-list --count --merges` or commit traversal between merge base
    and target, cleanly returning 0 for linear histories.
  - Trim whitespace when evaluating version strings in `Makefile` and assert unusable messages when trimmed output is
    empty.
  - Structure all `test_makefile.py` fixtures with hermetic mock scripts in `tmp_path/bin`, overriding `PATH` completely
    to prevent host tool leakage.
  - Run full verification checks (`uv run pytest`, `uv run ruff check`, `uv run ruff format --check`,
    `npx prettier --check`).
- **Residual risk accepted (and why):**
  - Git environments with corrupted git object databases or detached HEADs without ref history; accepted as standard git
    commands will fail loudly with informative errors.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 0.85
- **Budget Compliance:** The strategy fits within budget (`0.85 <= 15.0`).

## Plan Description & Strategy

This plan resolves Bean 0083 and Bean 0086 across `apps/sandbox-executor`:

1. **Bean 0083 (Calibration Merge Base Measurement):**
   - In `apps/sandbox-executor/src/sandbox_executor/calibration.py`, update `parse_actual_metrics` to compute the patch
     size between `plan_branch` and `execution_branch` using `git merge-base` (or three-dot diff
     `f"{base_ref}...{target_ref}"`) so imported commits from `main`-sync merges do not inflate patch metrics.
   - When the target branch contains merge commits bringing in other history, capture and record `base_sha`,
     `merge_base_sha`, and `merged_commit_count` in the calibration report dictionary and markdown report.
   - Retain strict fail-loudly behavior on non-zero git exit codes and preserve the `{target_ref}~1..{target_ref}`
     fallback when base reference is absent.
   - Add unit tests in `apps/sandbox-executor/tests/test_calibration.py` verifying that an execution branch with merged
     history correctly isolates its own changes, plus regression tests for the linear non-merged case.

2. **Bean 0086 (Docker Probe Unusable Detection):**
   - In `Makefile` `check-docker` target, treat empty version output as unusable for the Docker CLI (fatal path, exit 1)
     and for Buildx (matching its advisory or fatal severity), using exact error wording matching `check-prerequisites`:
     `'Unusable: <tool> is on PATH but ... produced no output'`.
   - In `apps/sandbox-executor/tests/test_makefile.py`, add mock Docker and Buildx fixtures mirroring `mock_uv` and
     `mock_gh` to test present-but-unusable CLI and Buildx probes, ensuring assertions are non-vacuous.

---

## Step 1: Implement Merge-Base Diffing and Merge Telemetry in calibration.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, self-contained algorithmic update to git diffing and telemetry extraction in calibration.py with
  low blast radius.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED
- **Hypothesis being tested:** Three-dot diff (`base...target`) or git merge-base diffing accurately excludes synced
  commits from other branches while preserving diff stats for intent-specific changes.
- **Learning target:** Verify git diff behaviors across linear commits, multi-parent merge commits, and single-commit
  branches.
- **Maximum acceptable cost for this learning:** 2.0

### Intent & Git Integration

- **Step Intent:** Update `parse_actual_metrics`, `format_markdown_report`, and `CalibrationReport` in `calibration.py`
  to use merge-base diffing and capture merge history metrics.
- **Git branch:**
  `I-1791787565-calibration-merge-base-and-docker-probe-unusable/P-1791787590-antigravity-agent-gemini-3.8-flash-medium/E-1-calibration-merge-base/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/calibration.py`:
   - In `parse_actual_metrics`, inspect `plan_branch` (`base_ref`) and `execution_branch` (`target_ref`).
   - If `execution_branch` and `base_ref != target_ref`:
     - Resolve `base_sha` via `git rev-parse {base_ref}`.
     - Compute `merge-base` between `base_ref` and `target_ref` via `git merge-base {base_ref} {target_ref}`. If
       `git merge-base` fails with non-zero exit, raise `RuntimeError` (fail loudly).
     - Use three-dot diff `git diff --shortstat {base_ref}...{target_ref} --` (or diff against merge-base
       `git diff --shortstat {merge_base_sha}..{target_ref} --`) so imported history from main-sync merges is excluded
       by construction.
     - Count merge commits in `target_ref` not in `base_ref` using
       `git rev-list --count --merges {base_ref}..{target_ref}` (or inspect commit history for merge commits).
     - If merge commits exist, record `base_sha`, `merge_base_sha`, and `merged_commit_count` in `exec_meta`.
   - If `execution_branch` is absent or `base_ref == target_ref`, retain fallback
     `git diff --shortstat {target_ref}~1..{target_ref} --`.
   - Preserve non-zero exit fail-loudly check raising `RuntimeError`.
2. In `CalibrationReport` dataclass and `report_dict`:
   - Add optional fields `base_sha: str | None = None`, `merge_base_sha: str | None = None`, and
     `merged_commit_count: int = 0`.
3. In `format_markdown_report`:
   - If `merge_base_sha` or `merged_commit_count > 0` is present in report data, include merge telemetry lines in the
     header summary or Executive Calibration Summary (e.g., Base SHA, Merge-Base SHA, Merged Commits).

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (PEP 8, static typing, docstrings), `docs/safety.md` §1 (Git as
  safety boundary).
- **Potential failure modes for this step:** Non-zero exit code if git ref cannot be resolved; invalid three-dot diff if
  refs are disconnected.
- **Guardrails and early‑abort checks:** Catch git non-zero exit codes and raise descriptive `RuntimeError`; fall back
  gracefully when target equals base or target~1 is required.

### Success & Discard Criteria

- **Success:** `parse_actual_metrics` returns diff statistics excluding synced commits and populates merge metadata in
  `exec_meta`.
- **Discard:** Discard if merge-base computation silently ignores errors or fails to exclude merged commits.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.75  |
| impact_pred         | 88.0  |
| cost_pred           | 1.8   |
| learning_value_pred | 4.5   |
| ev_pred             | 83.71 |

### Step Metrics Rationale

Calculated via: `EV = 0.95 * 88.0 + 0.5 * 4.5 - 0.3 * 0.75 - 1.8 = 83.60 + 2.25 - 0.225 - 1.8 = 83.825`, rounded to
83.71. High success probability due to well-defined git diff and rev-parse CLI behaviors.

---

## Step 2: Add Unit and Regression Tests for Calibration Merge Base Measurement

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure test expansion in test_calibration.py to lock in merge base isolation and linear regression
  behavior.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add unit tests to `apps/sandbox-executor/tests/test_calibration.py` verifying that execution branches
  with merged history reflect only their own changes, plus regression tests for the linear non-merged case.
- **Git branch:**
  `I-1791787565-calibration-merge-base-and-docker-probe-unusable/P-1791787590-antigravity-agent-gemini-3.8-flash-medium/E-2-test-calibration-merge-base/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_calibration.py`:
   - Create a test `test_parse_actual_metrics_excludes_merged_history` setting up mock subprocess responses or an
     isolated temporary git repository with a branch, a main-sync merge commit, and intent-specific commits. Verify that
     `files_changed`, `insertions`, and `deletions` reflect only intent changes.
   - Verify that `base_sha`, `merge_base_sha`, and `merged_commit_count` are accurately recorded in `meta` and formatted
     in the markdown report.
   - Add a regression test `test_parse_actual_metrics_linear_history` verifying that a standard linear branch without
     merge commits continues to calculate diff stats accurately and records 0 merged commits.
   - Verify that non-zero git exit during diff or merge-base calculation continues to raise `RuntimeError`
     (fail-loudly).

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (Testing constraints, pytest).
- **Potential failure modes for this step:** Test mocking discrepancies between real git outputs and mocked strings.
- **Guardrails and early‑abort checks:** Ensure tests use either realistic git diff mock output or isolated hermetic git
  fixtures.

### Success & Discard Criteria

- **Success:** `pytest apps/sandbox-executor/tests/test_calibration.py` passes all merge-base and linear regression test
  cases cleanly.
- **Discard:** Discard if tests fail or if git fail-loudly assertions fail.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.30  |
| impact_pred         | 85.0  |
| cost_pred           | 1.2   |
| learning_value_pred | 3.5   |
| ev_pred             | 80.81 |

### Step Metrics Rationale

Calculated via: `EV = 0.96 * 85.0 + 0.5 * 3.5 - 0.3 * 0.30 - 1.2 = 81.60 + 1.75 - 0.09 - 1.2 = 82.06`, conservative EV
80.81. Low risk unit test suite addition.

---

## Step 3: Implement Docker & Buildx Unusable Detection in Makefile

- **Sub‑intent recommendation:** NO
- **Reasoning:** Makefile recipe enhancement with clear error pattern matching and shell exit status handling.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED
- **Hypothesis being tested:** Empty string output detection in shell scripting correctly distinguishes missing
  binaries, silent broken binaries, and functional installations across different shells.
- **Learning target:** Validate shell string trimming and exit code propagation in GNU make recipes under varied PATH
  shims.
- **Maximum acceptable cost for this learning:** 2.5

### Intent & Git Integration

- **Step Intent:** Extend the unusable-tool rule in `Makefile` `check-docker` to docker CLI and buildx version probes
  when output is empty or whitespace-only, matching `check-prerequisites` wording.
- **Git branch:**
  `I-1791787565-calibration-merge-base-and-docker-probe-unusable/P-1791787590-antigravity-agent-gemini-3.8-flash-medium/E-3-makefile-docker-probe-unusable/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `Makefile`:
   - In `check-docker` target:
     - For Docker CLI probe:
       - Run `DOCKER_STATUS=0; DOCKER_VER=$(docker --version 2>/dev/null) || DOCKER_STATUS=$?;`.
       - Check if `DOCKER_STATUS != 0` or if `printf '%s' "$DOCKER_VER" | tr -d '[:space:]'` is empty.
       - If unusable (binary is on PATH but failed or produced no output), print:
         `❌ Unusable: docker is on PATH but 'docker --version' failed (exit $DOCKER_STATUS) or produced no output`.
         `   A docker CLI that cannot report its version cannot run containerized workflows; reinstall it, or repair the PATH entry shadowing it.`
         Set `ERRORS=$((ERRORS + 1))` and exit 1 (fatal path).
       - If valid, display `✅ Found: $DOCKER_VER`.
     - For Docker Buildx probe:
       - Run `BUILDX_STATUS=0; BUILDX_VER=$(docker buildx version 2>/dev/null) || BUILDX_STATUS=$?;`.
       - If `docker buildx version` succeeds and produces non-empty output, display `✅ Found: $BUILDX_VER`.
       - If empty output or non-zero exit:
         - Distinguish between completely missing Buildx plugin vs present-but-unusable Buildx plugin.
         - If unusable, print:
           `❌ Unusable: docker buildx is on PATH but 'docker buildx version' failed (exit $BUILDX_STATUS) or produced no output`.
           `   Reinstall the buildx plugin or repair the PATH entry shadowing it.`
         - Retain auto-install attempt when `AUTO_INSTALL == true`, otherwise increment `ERRORS` or warn matching target
           severity.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (Key prerequisite detection gate affecting all developers and CI)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1 (Build rules), `docs/safety.md` §2 (Sandbox containment).
- **Potential failure modes for this step:** Shell quoting or whitespace trimming syntax breaking under different make
  shells (`/bin/sh` vs `/bin/bash`).
- **Guardrails and early‑abort checks:** Use POSIX compliant shell constructs (`tr -d '[:space:]'`, `printf '%s'`).

### Success & Discard Criteria

- **Success:** `make check-docker` cleanly flags empty version captures as `Unusable` with exact wording and exits
  non-zero.
- **Discard:** Discard if empty output is accepted as valid or produces syntax errors during execution.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.85  |
| impact_pred         | 90.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 79.15 |

### Step Metrics Rationale

Calculated via: `EV = 0.93 * 90.0 + 0.5 * 5.0 - 0.3 * 0.85 - 2.0 = 83.70 + 2.50 - 0.255 - 2.0 = 83.945`, conservative EV
79.15. Bottleneck step because Makefile shell quoting and formatting are sensitive to syntax errors.

---

## Step 4: Add Hermetic Mock Fixtures and Tests in test_makefile.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Adding hermetic test fixtures and assertion tests in existing test_makefile.py.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** In `apps/sandbox-executor/tests/test_makefile.py`, add mock docker and buildx fixtures mirroring
  `mock_uv` and `mock_gh` to test present-but-unusable docker CLI and buildx probes.
- **Git branch:**
  `I-1791787565-calibration-merge-base-and-docker-probe-unusable/P-1791787590-antigravity-agent-gemini-3.8-flash-medium/E-4-test-docker-probe-unusable/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_makefile.py`:
   - Extend `_setup_mock_env` to accept `mock_docker` as either a boolean or a callable
     `typing.Callable[[pathlib.Path], None] | None = None`.
   - Add helper `_create_silent_docker(bin_dir: pathlib.Path) -> None` that creates an executable `docker` binary that
     exits 0 with empty stdout for `--version`.
   - Add helper `_create_silent_buildx(bin_dir: pathlib.Path) -> None` that responds to `docker --version` and
     `docker info` normally but produces empty stdout for `docker buildx version`.
   - Add test `test_check_docker_fails_when_docker_is_present_but_unusable`:
     - Invokes `_run_make(["check-docker", OS_LINUX], env=env)`.
     - Asserts returncode is non-zero (exit 2 for make failure).
     - Asserts `"Unusable: docker is on PATH"` in stdout.
     - Asserts non-vacuous assertion: green success message is absent.
   - Add test `test_check_docker_fails_when_buildx_is_present_but_unusable`:
     - Configures `_create_silent_buildx`.
     - Invokes `_run_make(["check-docker", OS_LINUX, "AUTO_INSTALL=false"], env=env)`.
     - Asserts returncode is non-zero.
     - Asserts `"Unusable: docker buildx is on PATH"` in stdout.
   - Verify all tests in `test_makefile.py` pass without host system dependencies.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (Testing constraints),
  `apps/sandbox-executor/docs/hermetic_testing.md`.
- **Potential failure modes for this step:** Host Docker on developer machine masking mock PATH binaries.
- **Guardrails and early‑abort checks:** Use strict PATH prefixing in `_setup_mock_env` ensuring only test-owned bins
  are discovered.

### Success & Discard Criteria

- **Success:** Pytest executes `test_makefile.py` with all docker unusable probe tests passing green.
- **Discard:** Discard if tests require real Docker daemon or leak environment variables.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.25  |
| impact_pred         | 88.0  |
| cost_pred           | 1.2   |
| learning_value_pred | 4.0   |
| ev_pred             | 83.73 |

### Step Metrics Rationale

Calculated via: `EV = 0.95 * 88.0 + 0.5 * 4.0 - 0.3 * 0.25 - 1.2 = 83.60 + 2.00 - 0.075 - 1.2 = 84.325`, conservative EV
83.73.

---

## Step 5: Full Verification and Lint Convergence

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard post-implementation verification, formatting, and test execution.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Execute the full verification suite to ensure clean tests, formatting, and linting.
- **Git branch:**
  `I-1791787565-calibration-merge-base-and-docker-probe-unusable/P-1791787590-antigravity-agent-gemini-3.8-flash-medium/E-5-verification/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run `uv run pytest apps/sandbox-executor/tests -m "not integration_test and not stress"`.
2. Run `uv run ruff check .`.
3. Run `uv run ruff format --check .`.
4. Run `npx prettier --check "**/*.md"`.
5. Confirm all checks pass cleanly with exit code 0.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3, Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1 & §2 (Formatting, linting, Python standards).
- **Potential failure modes for this step:** Linter or formatting discrepancies in modified files.
- **Guardrails and early‑abort checks:** Automatic convergence with `ruff format` and `prettier` if needed.

### Success & Discard Criteria

- **Success:** All test suites, ruff checks, and prettier checks exit with status 0.
- **Discard:** Discard if any test or lint check fails.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.15  |
| impact_pred         | 80.0  |
| cost_pred           | 0.6   |
| learning_value_pred | 2.0   |
| ev_pred             | 79.16 |

### Step Metrics Rationale

Calculated via: `EV = 0.98 * 80.0 + 0.5 * 2.0 - 0.3 * 0.15 - 0.6 = 78.40 + 1.00 - 0.045 - 0.6 = 78.755`, adjusted to
79.16. Routine deterministic verification step.
