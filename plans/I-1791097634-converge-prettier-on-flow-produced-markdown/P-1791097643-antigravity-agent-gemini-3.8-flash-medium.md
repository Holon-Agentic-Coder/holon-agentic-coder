# Plan for I-1791097634-converge-prettier-on-flow-produced-markdown

- **Plan ID:** P-1791097643-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-10-04T07:07:23.534Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted empirical validation of bounded multi-pass prettier formatting
  convergence, entrypoint and pipeline integration, and prompt hardening to eliminate markdown hygiene failures and
  review iteration oscillation.
- **Safety priority level:** elevated
- **Priority Justification:** Triggered by holon-config/world/ruleset.md §1 & §2 (Python ==3.13.\*, workspace isolation
  under apps/sandbox-executor, PEP 8, typing, docstrings), holon-config/world/ruleset.md §3 (Testing Constraints:
  pytest==9.1.1, test locations under apps/sandbox-executor/tests/, test changes declared in planning),
  holon-config/world/constraints.md §1 (Git Flow: branch isolation, commit boundaries),
  holon-config/world/constraints.md §2 (Sandbox containment: subprocess and filesystem boundaries),
  holon-config/world/constraints.md §3 (Ledger immutability: zero modifications to historical ledger entries),
  docs/safety.md §1 & §2 (Git as safety boundary, sandboxing mandatory), and
  apps/sandbox-executor/docs/hermetic_testing.md §1-§3 (Mandatory rules: every role entrypoint test must pin
  HOLON_REPO_DIR to fixture; Sandbox Safety Rail: never run full pytest discovery from ~/.holon-sandbox/workspace).

## Exploration

- **Proportion of steps that are exploratory:** 0.60
- **Justification:** Steps 1, 2, and 4 incorporate balanced exploration to empirically test multi-pass prettier
  convergence thresholds, entrypoint pre-commit markdown discovery and in-place formatting behavior, and hermetic
  subprocess mocking patterns without touching live container workspaces.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.91  |
| entropy_pred        | 1.8   |
| impact_pred         | 95.0  |
| cost_pred           | 12.0  |
| learning_value_pred | 5.2   |
| ev_pred             | 76.51 |

### Strategy Rationale

The overall plan metrics were derived from individual step-level metrics as follows:

- **p_success_pred**: 0.91. Dictated by the bottleneck step (Step 4: 0.91), which requires creating comprehensive
  hermetic unit tests simulating oscillating prettier runs, fixed points, failure logging, and entrypoint integration
  while strictly complying with the hermetic testing rail without triggering workspace deletion hazards.
- **entropy_pred**: 1.8. Derived as the maximum single-step risk profile (Step 2: 1.8), where modifications across
  multiple entrypoints (planner, executor, calibration, flow) integrate the formatting utility before git staging. The
  sum of predicted step entropies is 5.8 (1.2 + 1.8 + 0.6 + 1.5 + 0.7 = 5.8), which is well within the allocated budget
  of 15.0.
- **impact_pred**: 95.0. Resolves the widespread CI failure identified in Bean 0019 (PR #61) where flow PRs fail hygiene
  checks due to unformatted or oscillating markdown artifacts, eliminating wasted review iterations and fragile review
  resolver fixes.
- **cost_pred**: 12.0. Calculated as the direct sum of individual step costs (2.5 + 3.5 + 1.0 + 3.5 + 1.5 = 12.0).
- **learning_value_pred**: 5.2. Epistemic gain from establishing reliable multi-pass convergence algorithms,
  understanding markdown parser oscillation dynamics, hardening agent prompt instructions, and formalizing hermetic CLI
  formatting test patterns.
- **ev_pred**: 76.51. Computed strictly via the config-driven Expected Value formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`. With system constants `lambda = 0.3`
  and `mu = 0.5` loaded from holon-config/metrics/ev_config.json:
  `EV = 0.91 * 95.0 + 0.5 * 5.2 - 0.3 * 1.8 - 12.0 = 86.45 + 2.60 - 0.54 - 12.0 = 76.51`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - holon-config/world/ruleset.md §1 (Runtime & Environment Specification: Python target ==3.13.\*, workspace isolation
    under apps/sandbox-executor).
  - holon-config/world/ruleset.md §2 (Coding Conventions & Standards: PEP 8 compliance, explicit static typing with
    typing module, complete docstrings for public functions, no wildcard imports).
  - holon-config/world/ruleset.md §3 (Testing Constraints: pytest==9.1.1, test files located under
    apps/sandbox-executor/tests/, explicit planning of test changes).
  - holon-config/world/constraints.md §1 (Git Flow & Branch Constraints: prefix-based branch isolation, rebase
    discipline, commit boundaries).
  - holon-config/world/constraints.md §2 (Sandbox Containment Tiers: strict filesystem containment, prevention of escape
    actions and unwhitelisted modifications).
  - holon-config/world/constraints.md §3 (Ledger Immutability: zero modification of existing
    holon-knowledge/ledger/\*.jsonl lines, append-only operations).
  - docs/safety.md §1 & §2 (Git as safety boundary, sandboxing mandatory for execution).
  - apps/sandbox-executor/docs/hermetic_testing.md §1-§3 (Mandatory Rules for Tests: Rule 1: Every role entrypoint test
    must pin HOLON_REPO_DIR to fixture; Rule 4: Validate via canary regression guard; §3 Sandbox Safety Rail: never run
    full test discovery from ~/.holon-sandbox/workspace, run from an isolated scratch copy under /tmp instead).
- **Potential violations or edge cases:**
  - Prettier subprocess execution hanging or timing out on corrupted or unbounded markdown files.
  - Prettier failing to converge within max passes (3 passes) on pathological input, causing unhandled exceptions that
    abort an otherwise successful flow execution.
  - Subprocess execution corrupting files if npx/prettier exits abnormally mid-write.
  - Node.js or npx being missing or unavailable in minimal runtime environments, crashing entrypoints.
  - Accidentally modifying historical ledger entries in holon-knowledge/ledger/ or violating git branch boundaries.
  - Running pytest directly from /home/holon/.holon-sandbox/workspace during verification, risking live workspace
    deletion.
- **Mitigations built into the plan:**
  - Subprocess timeout guards: enforce a strict per-command timeout (e.g., 30s) on all prettier invocations.
  - Bounded convergence loop: hard cap at 3 write passes; if prettier fails to converge or check fails after 3 passes,
    emit a loud structured warning to stderr/logger and return False without corrupting files or crashing the
    entrypoint.
  - Non-destructive execution: check file existence before formatting; verify npx availability with shutil.which prior
    to running subprocesses.
  - Defensive error wrappers: wrap all converge_prettier calls in try/except Exception blocks so formatting failures
    never disrupt core pipeline execution or alter task exit status.
  - Strict sandbox testing rail: all manual test verification will be executed from an isolated copy in /tmp with
    HOLON_REPO_DIR pointing to a temporary fixture.
  - Ledger files untouched: absolutely no edits to holon-knowledge/ledger/\*.
- **Residual risk accepted (and why):**
  - External Node/npx dependency: formatting relies on npx being present on the PATH. In environments without Node.js,
    the utility will log a warning and proceed without formatting, allowing execution to continue safely rather than
    crashing the pipeline.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 5.8
- **Budget Compliance:** The strategy fits within budget (Predicted Plan Entropy sum of 5.8 is well below the 15.0
  allocated budget).

## Plan Description & Strategy

This plan implements automated prettier convergence across all flow-produced markdown artifacts and prompts:

1. **Create Convergence Utility (`sandbox_executor.formatting.converge_prettier`):** Implement a robust formatting
   helper in `apps/sandbox-executor/src/sandbox_executor/formatting.py` that runs
   `npx --yes prettier@3.8.4 --write <files>` in a bounded loop (up to 3 passes) until
   `npx --yes prettier@3.8.4 --check <files>` succeeds with exit code 0. Include explicit handling for unavailable
   Node/npx, timeouts, non-zero exit codes, and non-corrupting fail-safe error logging.
2. **Integrate Prettier Convergence Across Role Entrypoints and Pipeline Stages:** Hook the convergence formatter into
   pre-commit and pre-push steps across:
   - `apps/sandbox-executor/src/sandbox_executor/entrypoint/planner.py` for generated plans (`plans/P-*.md`),
   - `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py` for execution records (`executions/E-*.md`) and
     any modified markdown files,
   - `apps/sandbox-executor/src/sandbox_executor/calibration.py` for calibration reports,
   - `apps/sandbox-executor/src/sandbox_executor/flow.py` across plan, execute, and calibrate pipeline stages so that no
     flow branch is committed or pushed with failing markdown hygiene.
3. **Harden Agent Prompt Guidance Against Markdown Arithmetic Oscillation:** Update prompt templates in
   `holon-config/prompts/planner.template.md` and `holon-config/prompts/executor.template.md` to instruct agents to
   enclose arithmetic, formulas, and metric derivations in backticks (code spans) and avoid raw unescaped
   underscores/asterisks that trigger CommonMark emphasis parsing oscillations.
4. **Hermetic Unit and Integration Tests:** Add comprehensive hermetic tests in
   `apps/sandbox-executor/tests/test_formatting.py` and entrypoint test files validating that oscillating markdown
   converges, clean markdown passes immediately, pass limits are respected, missing tools are handled gracefully, and
   entrypoints properly format files before commit, strictly complying with
   `apps/sandbox-executor/docs/hermetic_testing.md`.
5. **Code Quality, Formatting Conventions, and Isolated Sandbox Rail Verification:** Verify all changes with
   `uv run ruff check .`, `uv run ruff format --check .`, verify markdown formatting with
   `npx --yes prettier@3.8.4 --check "**/*.md"`, and run unit tests from an isolated `/tmp` scratch directory obeying
   the sandbox safety rail.

---

## Step 1: Create Bounded Prettier Convergence Utility in sandbox_executor.formatting

- **Sub‑intent recommendation:** NO
- **Reasoning:** Localized, cohesive utility module creation with well-defined interface, zero external dependencies,
  and direct testability.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** A bounded formatting loop executing prettier --write up to 3 times followed by prettier
  --check can deterministically converge oscillating markdown to a fixed point or fail-safe cleanly without corrupting
  files or crashing when Node/npx is unavailable.
- **Learning target:** Validate loop termination mechanics, exit code interpretations, subprocess timeout guards, and
  error logging behaviors across diverse OS and environment configurations.
- **Maximum acceptable cost for this learning:** cost_pred of 2.5 units.

### Intent & Git Integration

- **Step Intent:** Implement converge_prettier in apps/sandbox-executor/src/sandbox_executor/formatting.py supporting
  bounded multi-pass prettier execution, check verification, tool availability checks, and non-corrupting error
  handling.
- **Git branch:** I-1791097634-converge-prettier-on-flow-produced-markdown/step1-create-convergence-utility
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create module apps/sandbox-executor/src/sandbox_executor/formatting.py with module docstring and explicit typing
  imports.
- Define constant PRETTIER_SPEC = "prettier@3.8.4" and DEFAULT_MAX_PASSES = 3.
- Define helper function is_prettier_available() -> bool using shutil.which("npx") or checking npx availability.
- Define helper function converge_prettier(files: list[str] | list[Path] | str, repo_dir: str | Path | None = None,
  max_passes: int = 3, timeout_seconds: int = 30) -> bool:
  - Normalize files into a list of strings representing relative or absolute file paths.
  - If files list is empty, return True immediately without spawning subprocesses.
  - Resolve working directory repo_dir (defaulting to current working directory).
  - Verify that specified target files exist on disk, filtering out nonexistent paths and logging a diagnostic message
    if any are skipped. If no existing files remain, return True.
  - Check is_prettier_available(). If npx is not found on PATH:
    - Log a clear warning to sys.stderr / logger indicating npx is unavailable and markdown formatting was skipped.
    - Return False without corrupting files.
  - Initialize pass_count = 0.
  - Enter bounded while loop while pass_count < max_passes:
    - Increment pass_count by 1.
    - Construct prettier write command: ["npx", "--yes", PRETTIER_SPEC, "--write", *target_files].
    - Execute subprocess.run with cwd=repo_dir, capture_output=True, text=True, and timeout=timeout_seconds.
    - If write command fails with non-zero exit code (syntax error or corrupt file):
      - Log error details from stderr to logger / sys.stderr.
      - Return False without altering files further.
    - Construct prettier check command: ["npx", "--yes", PRETTIER_SPEC, "--check", *target_files].
    - Execute subprocess.run with cwd=repo_dir, capture_output=True, text=True, and timeout=timeout_seconds.
    - If check command returns exit code 0:
      - Log informational message indicating prettier converged in pass_count pass(es).
      - Return True.
  - If loop finishes without exit code 0 from check:
    - Log a loud warning to logger / sys.stderr indicating prettier failed to converge on target_files after max_passes
      passes.
    - Return False.
  - Defensively wrap all subprocess operations in try/except blocks catching subprocess.TimeoutExpired,
    FileNotFoundError, and generic Exception, logging descriptive error messages and returning False without re-raising
    or crashing caller.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** holon-config/world/ruleset.md §1 & §2 (PEP 8, strict type hints, full docstrings),
  holon-config/world/constraints.md §2 (Sandbox containment).
- **Potential failure modes for this step:** Hanging subprocess calls on infinite loops, ReDoS, or subprocess resource
  leaks.
- **Guardrails and early‑abort checks:** Hard timeout (timeout_seconds=30) on all subprocess calls; check return codes
  before progressing; verify tool availability before spawning.

### Success & Discard Criteria

- **Success:** converge_prettier is callable, runs bounded write passes up to max_passes, verifies with --check, handles
  errors gracefully, and returns boolean convergence status.
- **Discard:** Discard if implementation modifies file contents during check-only operations or fails to terminate on
  cyclic inputs.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.2   |
| impact_pred         | 88.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 4.5   |
| ev_pred             | 82.99 |

### Step Metrics Rationale

This step creates an isolated utility module with high certainty of success (p_success_pred 0.95) and modest entropy
(1.2). It delivers substantial epistemic gain (4.5) by establishing deterministic multi-pass convergence logic. EV
calculation: `0.95 * 88.0 + 0.5 * 4.5 - 0.3 * 1.2 - 2.5 = 83.60 + 2.25 - 0.36 - 2.5 = 82.99`.

---

## Step 2: Integrate Prettier Convergence Across Role Entrypoints and Pipeline Stages

- **Sub‑intent recommendation:** NO
- **Reasoning:** Integration into existing entrypoint functions (planner, executor, calibration) and flow stages before
  git add/commit calls; low architectural risk with clear boundaries.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Invoking converge_prettier before git staging in role entrypoints and flow stages
  guarantees all committed markdown artifacts satisfy prettier --check without masking underlying git errors or altering
  execution status.
- **Learning target:** Understand how git staging and status detection interact with pre-commit in-place markdown
  mutation, ensuring only genuine changes are staged.
- **Maximum acceptable cost for this learning:** cost_pred of 3.5 units.

### Intent & Git Integration

- **Step Intent:** Wire converge_prettier into planner.py, executor.py, calibration.py, and flow.py prior to git add and
  git commit operations across all flow branches.
- **Git branch:** I-1791097634-converge-prettier-on-flow-produced-markdown/step2-integrate-entrypoints-and-flow
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In apps/sandbox-executor/src/sandbox_executor/entrypoint/planner.py:
  - Import converge_prettier from sandbox_executor.formatting.
  - Locate the point after plan_path markdown file is written and before run_cmd(["git", "add", plan_md_rel, ...]).
  - Defensively call converge_prettier([plan_md_rel], repo_dir=repo_dir).
  - Ensure any return value or exception is caught and logged, allowing git staging and commit to continue even if
    prettier is unavailable.
- In apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py:
  - Import converge_prettier from sandbox_executor.formatting.
  - Locate execution completion before git staging and committing.
  - Scan repo_dir for modified or untracked markdown files (.md) using git status or diff, and include the generated
    execution record executions/<execution_id>.md.
  - Call converge_prettier on the identified markdown files with repo_dir=repo_dir.
  - Ensure failure to format does not change exec_status or raise exceptions.
- In apps/sandbox-executor/src/sandbox_executor/calibration.py:
  - Import converge_prettier from sandbox_executor.formatting.
  - In run_calibrate(), right after writing report.markdown_content to report_path and before subprocess.run(["git",
    "add", report_rel], ...):
  - Call converge_prettier([report_rel], repo_dir=repo_dir).
- In apps/sandbox-executor/src/sandbox_executor/flow.py:
  - Import converge_prettier from sandbox_executor.formatting.
  - In step_create_plan(): call converge_prettier([plan_md_rel], repo_dir=repo_dir) prior to git add and commit.
  - In step_execute_plan(): discover modified markdown files and call converge_prettier(md_files, repo_dir=repo_dir)
    prior to git commit.
  - In step_calibrate(): call converge_prettier([report_rel], repo_dir=repo_dir) prior to committing, including the
    fallback calibration path.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES
- **Reasoning:** Step 2 integrates the convergence utility into the core lifecycle. If entrypoints fail during
  pre-commit formatting or crash, subsequent flow steps cannot execute.

### Safety & Constraint Considerations

- **Relevant rules:** holon-config/world/constraints.md §1 (Git Flow isolation), holon-config/world/constraints.md §3
  (Ledger immutability), docs/safety.md §1 & §2.
- **Potential failure modes for this step:** Pre-commit formatting exceptions crashing executor.main() or
  planner.main(), or corrupting staged git changes.
- **Guardrails and early‑abort checks:** Wrap every formatting invocation in try/except blocks; log warnings on failure;
  verify git status before and after formatting to ensure non-markdown files remain untouched.

### Success & Discard Criteria

- **Success:** planner.py, executor.py, calibration.py, and flow.py invoke converge_prettier before committing markdown
  files, ensuring all committed markdown satisfies prettier hygiene.
- **Discard:** Discard if formatting errors alter execution statuses or cause git commit operations to fail.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.8   |
| impact_pred         | 94.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 4.0   |
| ev_pred             | 84.44 |

### Step Metrics Rationale

Step 2 modifies multiple entrypoints (SSA = 2.5, CL = 1.5, entropy_pred = 1.8) and is the core delivery mechanism for
automated hygiene, providing high impact (94.0). EV calculation:
`0.92 * 94.0 + 0.5 * 4.0 - 0.3 * 1.8 - 3.5 = 86.48 + 2.00 - 0.54 - 3.5 = 84.44`.

---

## Step 3: Harden Agent Prompt Guidance Against Markdown Arithmetic Oscillation in holon-config

- **Sub‑intent recommendation:** NO
- **Reasoning:** Non-code prompt configuration adjustments directly mitigating root causes of oscillation in LLM
  markdown generation.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update holon-config/prompts/planner.template.md and executor.template.md to advise agents against
  using unescaped underscores in arithmetic/metric formulas, recommending backticks or asterisks inside code spans to
  ensure single-pass prettier convergence.
- **Git branch:** I-1791097634-converge-prettier-on-flow-produced-markdown/step3-harden-prompt-guidance
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In holon-config/prompts/planner.template.md:
  - Add explicit guidance in the plan formatting requirements:
    - Advise agents that arithmetic and metric formulas (e.g. EV, Delta_S, cost derivations) must be enclosed in
      markdown code spans (backticks) or use asterisks (\*) for multiplication rather than unescaped underscores.
    - Explain that unescaped underscores in arithmetic (such as 0.90 _ 92.0 + 0.5 _ 4.8) trigger CommonMark emphasis
      ambiguity, causing multi-pass prettier formatting oscillations.
    - Provide positive examples of properly enclosed formulas inside backticks.
- In holon-config/prompts/executor.template.md:
  - Update Critical Execution Directives to instruct agents that all generated or updated markdown documentation and
    execution records must use code spans for formulas and metric notations to maintain immediate single-pass prettier
    compatibility.
- Ensure prompt changes preserve all existing template variables ({intent_id}, {plan_id}, {repo_dir}, {intent_json},
  etc.).

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** holon-config/world/ruleset.md §2 (Documentation standards), docs/safety.md §5 (Human review
  boundaries).
- **Potential failure modes for this step:** Breaking template placeholder syntax or confusing agent instruction
  parsing.
- **Guardrails and early‑abort checks:** Inspect template variable names against caller formatting strings in planner.py
  and executor.py to ensure 100% placeholder compatibility.

### Success & Discard Criteria

- **Success:** Prompt templates contain clear, actionable guidance on escaping arithmetic formulas and preventing
  markdown emphasis oscillation while leaving template parameter substitutions intact.
- **Discard:** Discard if template modifications cause formatting or KeyError exceptions during prompt rendering.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.6   |
| impact_pred         | 80.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 2.5   |
| ev_pred             | 78.47 |

### Step Metrics Rationale

Simple configuration update with very low entropy (0.6) and near-certain success (0.98). Directly attacks the upstream
root cause of markdown oscillation. EV calculation:
`0.98 * 80.0 + 0.5 * 2.5 - 0.3 * 0.6 - 1.0 = 78.40 + 1.25 - 0.18 - 1.0 = 78.47`.

---

## Step 4: Implement Comprehensive Hermetic Unit and Regression Tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard hermetic unit testing strictly contained within apps/sandbox-executor/tests/, directly
  verifying Steps 1, 2, and 3 without external network dependencies.
- **Step Type:** TEST
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Comprehensive hermetic unit tests can thoroughly validate oscillating markdown
  convergence, single-pass fixed points, max-pass thresholds, tool absence, and entrypoint integration without touching
  live container workspaces or external remotes.
- **Learning target:** Validate mocking strategies for npx/prettier sub-processes and ensure complete compliance with
  sandbox hermetic safety rails.
- **Maximum acceptable cost for this learning:** cost_pred of 3.5 units.

### Intent & Git Integration

- **Step Intent:** Add apps/sandbox-executor/tests/test_formatting.py and update test_planner.py, test_executor.py,
  test_calibration.py, and test_flow.py with hermetic unit tests following hermetic_testing.md.
- **Git branch:** I-1791097634-converge-prettier-on-flow-produced-markdown/step4-hermetic-unit-tests
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create apps/sandbox-executor/tests/test_formatting.py:
  - Add test_converge_prettier_clean_file:
    - Create a temporary markdown file that is already well-formatted.
    - Mock or execute converge_prettier; assert return value is True and check passes in 1 pass.
  - Add test_converge_prettier_oscillating_markdown:
    - Mock subprocess.run to simulate an oscillating file: pass 1 write returns exit code 0, check returns exit code 1;
      pass 2 write returns exit code 0, check returns exit code 0.
    - Assert converge_prettier performs exactly 2 passes and returns True.
  - Add test_converge_prettier_exceeds_max_passes:
    - Mock subprocess.run to simulate persistent check failure (exit code 1).
    - Assert converge_prettier stops after exactly max_passes (default 3), logs a loud warning, and returns False
      without raising an exception.
  - Add test_converge_prettier_npx_unavailable:
    - Patch shutil.which("npx") to return None (or mock subprocess to raise FileNotFoundError).
    - Assert converge_prettier logs an informative warning, does not crash, and returns False.
  - Add test_converge_prettier_empty_file_list:
    - Assert converge_prettier([]) returns True immediately without spawning subprocesses.
  - Add test_converge_prettier_timeout_handling:
    - Mock subprocess.run to raise subprocess.TimeoutExpired.
    - Assert converge_prettier logs timeout error, does not hang, and returns False.
- In apps/sandbox-executor/tests/test_planner.py:
  - Add unit test verifying planner.main() invokes converge_prettier with [plan_md_rel] before git commit.
  - Adhere strictly to hermetic testing Rule 1: pin HOLON_REPO_DIR to tempfile.TemporaryDirectory, patch
    get_workspace_dir and cleanup_repo_dir.
- In apps/sandbox-executor/tests/test_executor.py:
  - Add unit test asserting executor.main() collects modified markdown files and invokes converge_prettier before git
    commit.
  - Verify hermetic fixture pinning and cleanup patching.
- In apps/sandbox-executor/tests/test_calibration.py:
  - Add unit test verifying run_calibrate() calls converge_prettier on report_rel before committing.
- In apps/sandbox-executor/tests/test_flow.py:
  - Add unit tests verifying flow pipeline stages (plan, execute, calibrate) invoke converge_prettier before committing.
- Audit all new tests against apps/sandbox-executor/docs/hermetic_testing.md to ensure zero live workspace deletion
  hazards and zero external network access.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** YES
- **Reasoning:** This is the primary plan bottleneck (p_success_pred 0.91). All acceptance criteria, edge cases, and
  safety rails are validated here.

### Safety & Constraint Considerations

- **Relevant rules:** apps/sandbox-executor/docs/hermetic_testing.md (Hermetic Testing Standards, Rules 1-5),
  holon-config/world/ruleset.md §3 (Testing Constraints).
- **Potential failure modes for this step:** Unpinned entrypoint tests calling cleanup_repo_dir and deleting
  ~/.holon-sandbox/workspace; failing the canary regression guard.
- **Guardrails and early‑abort checks:** Strictly apply the fixture pattern with tempfile.TemporaryDirectory and
  patch.dict(os.environ, {"HOLON_REPO_DIR": tmp_dir}).

### Success & Discard Criteria

- **Success:** All new unit tests in test_formatting.py and entrypoint test suites pass hermetically with 100% pass
  rate, zero workspace deletions, and full coverage of edge cases.
- **Discard:** Discard if any test requires live network access or alters unpinned host directories.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.91  |
| entropy_pred        | 1.5   |
| impact_pred         | 90.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 5.0   |
| ev_pred             | 80.45 |

### Step Metrics Rationale

Bottleneck step requiring comprehensive mock modeling for multi-pass subprocess interactions and hermetic test
compliance (p_success_pred 0.91, entropy_pred 1.5). Delivers high learning value (5.0) in validating hermetic test
isolation. EV calculation: `0.91 * 90.0 + 0.5 * 5.0 - 0.3 * 1.5 - 3.5 = 81.90 + 2.50 - 0.45 - 3.5 = 80.45`.

---

## Step 5: Code Quality, Conventions, and Isolated Sandbox Rail Verification

- **Sub‑intent recommendation:** NO
- **Reasoning:** Final static analysis, formatting, and test execution step; standard verification procedure.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Execute static analysis, format checks, markdown hygiene checks, and unit test verification from an
  isolated /tmp scratch directory obeying the sandbox safety rail.
- **Git branch:** I-1791097634-converge-prettier-on-flow-produced-markdown/step5-verify-and-format
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Run ruff linter check: uv run ruff check .
- Run ruff format check: uv run ruff format --check .
- Run prettier markdown check across the repository: npx --yes prettier@3.8.4 --check "\*_/_.md"
- Adhere strictly to the Sandbox Safety Rail from apps/sandbox-executor/docs/hermetic_testing.md §3:
  - Never run full test discovery (uv run pytest) directly inside /home/holon/.holon-sandbox/workspace.
  - Create an isolated test directory under /tmp/holon-fixture.
  - Set HOLON_REPO_DIR=/tmp/holon-test-fixture.
  - Copy repository files to /tmp/holon-fixture.
  - From /tmp/holon-fixture, execute the standard unit suite: uv run pytest -m "not integration_test and not stress".
  - Run the hermetic canary guard: uv run pytest apps/sandbox-executor/tests/test_sandbox_hermetic_guard.py.
- Inspect git status to confirm no untracked files or unintentional modifications exist in the workspace.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3, Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** apps/sandbox-executor/docs/hermetic_testing.md §3 (Sandbox Safety Rail),
  holon-config/world/ruleset.md §1 & §2.
- **Potential failure modes for this step:** Accidental in-workspace pytest invocation triggering live directory wipe.
- **Guardrails and early‑abort checks:** Explicit check verifying current working directory is /tmp/holon-fixture before
  running pytest commands.

### Success & Discard Criteria

- **Success:** uv run ruff check ., uv run ruff format --check ., npx --yes prettier@3.8.4 --check "\*_/_.md", and
  isolated pytest suite all pass with zero errors.
- **Discard:** Discard if static analysis or formatting violations require structural architectural refactoring.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.7   |
| impact_pred         | 82.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 78.01 |

### Step Metrics Rationale

Routine verification and hygiene step with high predictability (p_success_pred 0.96), low entropy (0.7), and low cost
(1.5). Confirms complete acceptance criteria satisfaction. EV calculation:
`0.96 * 82.0 + 0.5 * 2.0 - 0.3 * 0.7 - 1.5 = 78.72 + 1.00 - 0.21 - 1.5 = 78.01`.
