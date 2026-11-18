# Plan for I-1789686556-package-and-document-holon-cli

- **Plan ID:** P-1789686633-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-09-17T23:10:33.803Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** conservative with focus on robust packaging configuration, workspace synchronization, and
  comprehensive developer documentation without unnecessary codebase churn.
- **Safety priority level:** standard
- **Priority Justification:** This intent performs configuration, dependency synchronization, and documentation updates
  entirely within the standard workspace boundaries, requiring no external network access, elevated privileges, or
  high-risk architectural alterations under docs/safety.md and holon-config/world/constraints.md.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** Packaging setup using standard PEP 517/621 build tools (Hatchling, uv) and updating developer
  documentation are well-defined, deterministic engineering tasks with zero requirement for exploratory spikes.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 2.5   |
| impact_pred         | 80.0  |
| cost_pred           | 12.0  |
| learning_value_pred | 4.0   |
| ev_pred             | 59.65 |

### Strategy Rationale

The overall plan metrics were derived from the individual step-level metrics as follows:

- **p_success_pred**: 0.88. Sourced from the bottleneck Step 3 (verifying wheel build, entrypoint execution pathways,
  and regression test suites), representing the primary integration risk.
- **entropy_pred**: 2.5. Derived from the maximum step-level entropy (Step 3: 2.0) with an additional buffer for
  cross-workspace manifest synchronization. The sum of step-level entropies is 6.5, well below the allocated budget of
  15.0.
- **impact_pred**: 80.0. Reflects the substantial utility of enabling global installation (uv tool install), ephemeral
  execution (uvx), and clear documentation for developers and automated sandbox runners across the entire Holon
  ecosystem.
- **cost_pred**: 12.0. Computed as the sum of individual step costs (2.0 + 3.0 + 4.0 + 3.0 = 12.0).
- **learning_value_pred**: 4.0. Epistemic gain from establishing and codifying PEP 517/621 packaging and wheel
  distribution standards within the monorepo workspace.
- **ev_pred**: 59.65. Calculated according to the standard config-driven EV formula EV = P(success) _ Impact + mu _
  LearningValue - lambda _ Entropy - Cost with lambda = 0.3 and mu = 0.5: 0.88 _ 80.0 + 0.5 _ 4.0 - 0.3 _ 2.5 - 12.0 =
  70.4 + 2.0 - 0.75 - 12.0 = 59.65.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - holon-config/world/ruleset.md §1 (Runtime & Environment Specification: Python ==3.13.\*, uv workspace management,
    [tool.uv.sources] path mappings).
  - holon-config/world/ruleset.md §2 (Coding Conventions & Standards: PEP 8 compliance, docstrings, typing guidelines).
  - holon-config/world/ruleset.md §3 (Testing Constraints: pytest runner, no modifying test assertions).
  - holon-config/world/constraints.md §1 (Git Flow & Branch Constraints: branch prefix isolation, commit boundaries).
  - docs/safety.md §1 & §2 (Sandboxing, blast radius containment, git isolation).
- **Potential violations or edge cases:**
  - Workspace member resolution failure if pyproject.toml package name does not match the source mapping in the root
    manifest.
  - Omission of nested subpackages or non-code resources in Hatchling wheel target table.
  - Breakage of the existing ./holon convenience shell wrapper if the module path or entrypoint definition drifts.
- **Mitigations built into the plan:**
  - Synchronize both root dependencies and [tool.uv.sources] simultaneously with uv sync verification.
  - Explicit configuration of [tool.hatch.build.targets.wheel] with packages = ["src/sandbox_executor"].
  - Regression testing against test_cli.py and verifying ./holon retains full backward compatibility.
- **Residual risk accepted (and why):**
  - Minor drift in lockfile hashing if external dependencies resolve different transitive sub-dependencies; accepted
    because uv.lock is pinned and committed.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 6.5
- **Budget Compliance:** The strategy fits within budget (Predicted Plan Entropy sum of 6.5 < 15.0 allocated).

## Plan Description & Strategy

This plan packages the Holon CLI as a standard Python distribution with console entrypoints and documents all standard
execution pathways. In Step 1, we configure apps/sandbox-executor/pyproject.toml by renaming the package to holon,
activating Hatchling as the build backend, specifying wheel package targets, and registering holon =
"sandbox_executor.cli:main" under [project.scripts]. In Step 2, we update the root pyproject.toml workspace manifest to
depend on holon, update [tool.uv.sources], and execute uv sync to update uv.lock. In Step 3, we build and validate the
distribution wheel, test ephemeral (uvx) and direct entrypoint execution, and verify the existing test suite passes
without regressions. In Step 4, we update README.md and the sandbox guides in docs/sandbox/ to document ephemeral
execution (uvx), global installation (uv tool install), tool upgrades (uv tool upgrade), and local editable development
workflows.

---

## Step 1: Configure Package Metadata, Hatchling Build System, and Entrypoints in apps/sandbox-executor/pyproject.toml

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, low-risk configuration change modifying a single manifest file with well-defined declarative
  syntax.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Rename package to holon, activate Hatchling build backend and wheel target table, and define the
  holon = "sandbox_executor.cli:main" entrypoint in [project.scripts].
- **Git branch:** I-1789686556-package-and-document-holon-cli/step1-package-entrypoints
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In apps/sandbox-executor/pyproject.toml, update [project] name = "sandbox-executor" to name = "holon".
- Uncomment and activate the [build-system] table specifying requires = ["hatchling"] and build-backend =
  "hatchling.build".
- Uncomment and activate [tool.hatch.build.targets.wheel] configuring packages = ["src/sandbox_executor"].
- Remove the obsolete instructional comments instructing not to remove the commented block until told.
- Add a [project.scripts] section specifying the console entrypoint holon = "sandbox_executor.cli:main".
- Preserve existing package version 0.1.0 and package description.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (All subsequent workspace synchronization and CLI packaging validation depend on valid package
  naming and entrypoints)

### Safety & Constraint Considerations

- **Relevant rules:** holon-config/world/ruleset.md §1 (Package Management), docs/safety.md §1 (Git boundary).
- **Potential failure modes for this step:**
  - Syntax error in pyproject.toml causing TOML parsing failure.
  - Incorrect package path in wheel targets leading to missing code modules in the built wheel.
- **Guardrails and early‑abort checks:**
  - Validate TOML syntax before saving and verify package directory src/sandbox_executor exists.

### Success & Discard Criteria

- **Success:** apps/sandbox-executor/pyproject.toml contains valid TOML defining name = "holon", Hatchling build
  backend, wheel targets, and [project.scripts] entrypoint.
- **Discard:** Abort if build backend specification contradicts repository Python 3.13 constraints or Hatchling cannot
  resolve packages.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.2   |
| impact_pred         | 40.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 36.39 |

### Step Metrics Rationale

Configuring declarative pyproject fields is standard and has very high success probability and low entropy.

---

## Step 2: Synchronize Root Workspace Dependencies and uv.lock

- **Sub‑intent recommendation:** NO
- **Reasoning:** Direct workspace manifest update and lockfile synchronization utilizing native uv tooling.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update root pyproject.toml dependencies and local source mappings to reference holon, and regenerate
  uv.lock.
- **Git branch:** I-1789686556-package-and-document-holon-cli/step2-workspace-sync
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In root pyproject.toml, update the dependencies array by replacing "sandbox-executor" with "holon".
- In root pyproject.toml [tool.uv.sources], replace the table key sandbox-executor = { path = "apps/sandbox-executor",
  editable = true } with holon = { path = "apps/sandbox-executor", editable = true }.
- Verify [workspace] members remains ["apps/sandbox-executor"].
- Run uv lock (or uv sync) to regenerate uv.lock, ensuring the lockfile maps the renamed workspace package holon to
  apps/sandbox-executor.
- Inspect uv.lock diff to verify that references to sandbox-executor are replaced by holon without introducing
  unrequested external dependency bumps.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Lockfile and dependency alignment are required for any local or containerized execution)

### Safety & Constraint Considerations

- **Relevant rules:** holon-config/world/ruleset.md §1 (Runtime & Environment Specification, Package Management),
  holon-config/world/constraints.md §1 (Commit Boundaries).
- **Potential failure modes for this step:**
  - Lockfile resolution conflict or failure of uv to resolve the local source path.
  - Desynchronization between pyproject.toml dependencies and uv.lock.
- **Guardrails and early‑abort checks:**
  - Abort immediately if uv lock produces unresolved dependency conflicts or fails to locate apps/sandbox-executor.

### Success & Discard Criteria

- **Success:** Root pyproject.toml and uv.lock are cleanly synchronized with holon as an editable workspace member, and
  uv sync completes with zero errors.
- **Discard:** Discard if uv encounters cyclic or irreconcilable package dependency constraints.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.8   |
| impact_pred         | 50.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 42.46 |

### Step Metrics Rationale

Workspace sync is standard in uv projects. Minor risk exists if lockfile generation encounters stale caches, hence
slightly higher entropy than Step 1.

---

## Step 3: Validate Wheel Build, Ephemeral Execution, and Test Suite Regression

- **Sub‑intent recommendation:** NO
- **Reasoning:** Verification and validation step that ensures packaging integrity and CLI invocation pathways work
  without breaking existing tests.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Verify wheel packaging, validate CLI executable entrypoints, and ensure existing test suites pass.
- **Git branch:** I-1789686556-package-and-document-holon-cli/step3-packaging-validation
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In apps/sandbox-executor, execute uv build --wheel to ensure a valid wheel distribution holon-0.1.0-py3-none-any.whl
  is generated in dist/.
- Inspect the generated wheel archive structure to confirm sandbox_executor Python package modules and subpackages are
  included.
- Test the console script entrypoint using uv run holon --help from the workspace root to ensure
  sandbox_executor.cli:main is correctly dispatched and prints CLI help without errors.
- Test ephemeral invocation pathways using uvx --from ./apps/sandbox-executor holon --help to confirm isolated execution
  works without explicit prior installation.
- Run the existing test suite (pytest apps/sandbox-executor/tests) to ensure all CLI unit tests (test_cli.py) and runner
  tests pass without regression.
- Clean up any generated dist/ or .whl build artifacts before committing to keep the git working tree pristine.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (Primary quality gate validating that the packaging works end-to-end)

### Safety & Constraint Considerations

- **Relevant rules:** holon-config/world/ruleset.md §3 (Testing Constraints), docs/safety.md §2 (Sandboxing).
- **Potential failure modes for this step:**
  - Build failure due to missing package directories or build backend configuration issues.
  - Console script entrypoint failing to resolve imports when executed outside the repository root.
  - Regression in apps/sandbox-executor/tests/test_cli.py.
- **Guardrails and early‑abort checks:**
  - If uv run holon --help returns non-zero or missing module errors, halt and inspect entrypoint path configuration.

### Success & Discard Criteria

- **Success:** Wheel builds successfully, uv run holon --help outputs valid CLI commands, ephemeral invocation works,
  and all existing pytest tests pass.
- **Discard:** Discard if wheel build introduces unresolvable import regressions or breaks test_cli.py.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 2.0   |
| impact_pred         | 60.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 49.7  |

### Step Metrics Rationale

Serves as the bottleneck integration check across packaging, wheel targets, entrypoints, and test suites.

---

## Step 4: Document Standard Execution Pathways in README.md and Sandbox Guides

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure documentation task updating user guides and developer onboarding docs with standard CLI usage
  patterns.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Document ephemeral execution (uvx), global tool installation (uv tool install), upgrades (uv tool
  upgrade), and local editable development in README.md and sandbox guides.
- **Git branch:** I-1789686556-package-and-document-holon-cli/step4-cli-documentation
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In README.md under ## Sandbox CLI usage, update instructions to highlight standard modern Python execution pathways
  alongside the legacy/convenience ./holon wrapper.
- Document ephemeral execution using uvx --from . holon [intent|plan|execute] ... for running commands on-demand without
  installing into the system environment.
- Document global tool installation using uv tool install . (from repo root) or uv tool install
  git+https://github.com/thomashan/holon-agentic-coder.git to expose holon directly in the user's $PATH.
- Document upgrading the CLI via uv tool upgrade holon.
- Document local development workflows with editable installs via uv sync or uv pip install -e apps/sandbox-executor.
- Update docs/sandbox/create_intent.md, docs/sandbox/create_plan.md, and docs/sandbox/execute_plan.md to reflect the new
  uvx and uv tool install execution options alongside ./holon.
- Clarify the relationship between the standalone holon entrypoint binary and the root ./holon convenience shell wrapper
  script.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** NO (Documentation step; technical changes are already verified in Step 3)

### Safety & Constraint Considerations

- **Relevant rules:** holon-config/world/ruleset.md §2 (Coding Conventions & Standards), docs/safety.md §5 (Human
  review).
- **Potential failure modes for this step:**
  - Broken links, out-of-date command flags, or ambiguous installation instructions confusing end users.
- **Guardrails and early‑abort checks:**
  - Review all updated markdown files to ensure accurate command syntax, relative links, and markdown formatting.

### Success & Discard Criteria

- **Success:** README.md and sandbox documentation files comprehensively explain ephemeral execution, global
  installation, upgrades, and local editable workflows with clear, copy-pasteable command examples.
- **Discard:** Discard if documentation conflicts with existing architectural invariants or misrepresents CLI behavior.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.5   |
| impact_pred         | 60.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.5   |
| ev_pred             | 54.8  |

### Step Metrics Rationale

High probability of success, zero code risk, and clear impact on usability and developer documentation.
