# Plan for I-1794049523-replace-repo-dir-with-target-worktree

- **Plan ID:** P-1794049540-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.5.3)
- **Created At:** 2026-11-07T11:05:40.758Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with focus on clean canonical flag replacement and robust verification across CLI
  options and execution flows.
- **Safety priority level:** standard
- **Priority Justification:** Changes are internal refactorings of CLI option flags, variable naming, and associated
  test updates without elevated privilege operations or external network requirements.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The intent has clear, concrete specifications and acceptance criteria with zero
  backward-compatibility aliases required.

## Overall Plan Metrics

| metric              | value  |
| ------------------- | ------ |
| p_success_pred      | 0.94   |
| entropy_pred        | 3.20   |
| impact_pred         | 100.00 |
| cost_pred           | 11.50  |
| learning_value_pred | 7.00   |
| ev_pred             | 85.04  |

### Strategy Rationale

The intent is accepted as-is (`ACCEPT_AS_IS`). The goal is to eliminate confusion in bare-clone and multi-worktree
repository architectures where operators mistake `--repo-dir` for a branch reference or starting point rather than a
concrete filesystem worktree. Because Holon is pre-release software, backwards compatibility is not required; replacing
and completely removing `--repo-dir` with `--target-worktree` provides clean, unambiguous ergonomics across all
subcommands, internals, and test harnesses.

The strategy decomposes the intent into five focused, sequential steps:

1. **Canonical Option Definition (`options.py`, `cli/__init__.py`, and role commands):** Replace `add_repo_dir_option`
   with `add_target_worktree_option` registering strictly `--target-worktree` with `dest='target_worktree'`. Completely
   remove `--repo-dir` with zero backward-compatibility aliases, update role dispatchers (`execute.py`, `plan.py`,
   `intent.py`, `_role.py`), and re-export `add_target_worktree_option` in `cli/__init__.py`.
2. **Subcommands & Dispatchers Migration across 11 Commands:** Update all 11 subcommands (`flow`, `doctor`, `calibrate`,
   `review`, `ledger`, `promote`, `reconcile`, `record`, `stats`, `veto`, `consult`) to register `--target-worktree` and
   read `args.target_worktree` in their command dispatch routines.
3. **Engine, Context, and Environment Variable Refactor:** In `sandbox_executor/flow.py`, rename `FlowContext.repo_dir`
   to `FlowContext.target_worktree`. In `agent_runner.py`, `turns.py`, `calibration.py`, and `git_flow.py`, rename
   parameter names from `repo_dir` to `target_worktree`. Replace `HOLON_REPO_DIR` with `HOLON_TARGET_WORKTREE` across
   runner environments and workspace resolution logic.
4. **Comprehensive Test Suite Updates and Dedicated Option Tests:** Create
   `apps/sandbox-executor/tests/test_cli_options.py` verifying real option parsing of `--target-worktree` and asserting
   that passing `--repo-dir` fails with an unrecognized argument error. Update `test_flow.py` and other test files to
   pass `target_worktree` and `--target-worktree`.
5. **Full Suite Execution, Formatting, and Lint Verification:** Execute
   `uv run pytest apps/sandbox-executor/tests/test_cli_options.py apps/sandbox-executor/tests/test_flow.py`, execute the
   full test suite with `uv run task test`, and verify code hygiene using `uv run task check` (`uv run task lint` and
   `uv run task lint-docs`).

**Derivation of Overall Metrics:**

- `p_success_pred`: Bottleneck step is Step 3 (`p_success_pred = 0.94`) due to the surface area of renaming context
  fields and parameters across flow engine modules. With clear boundaries, high test coverage, and compiler/linter
  checks, the overall `p_success_pred` is evaluated at `0.94`.
- `entropy_pred`: Derived by summing step entropies (`0.50 + 0.80 + 0.90 + 0.70 + 0.30 = 3.20`), which is well below the
  allocated entropy budget of `15.0`.
- `impact_pred`: Derived by summing step impacts (`15.00 + 25.00 + 30.00 + 20.00 + 10.00 = 100.00`), reflecting total
  elimination of option ambiguity and clean worktree semantics across the entire CLI toolchain.
- `cost_pred`: Derived by summing step resource costs (`1.50 + 2.50 + 3.00 + 3.00 + 1.50 = 11.50`).
- `learning_value_pred`: Derived by summing step learning values (`1.00 + 1.50 + 2.00 + 2.00 + 0.50 = 7.00`), capturing
  canonical command-line patterns and worktree-native architecture practices.
- `ev_pred`: Derived using the config formula
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost` where `mu = 0.5` and `lambda = 0.3`:
  `EV = 0.94 * 100.00 + 0.5 * 7.00 - 0.3 * 3.20 - 11.50 = 94.00 + 3.50 - 0.96 - 11.50 = 85.04`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md#1`: Target Python runtime `==3.13.*`; all dependencies managed via `uv` at the
    repository root. Zero new external dependencies.
  - `holon-config/world/ruleset.md#2`: Australian English spelling (centralised, sanitised, minimise, prioritised),
    strict static type annotations (`typing`), PEP 8 compliance, and comprehensive docstrings.
  - `holon-config/world/ruleset.md#3`: Testing via `pytest==9.1.1` (`uv run pytest`); unit and integration tests placed
    under `apps/sandbox-executor/tests/`.
  - `holon-config/world/constraints.md#1` & `docs/safety.md#1`: Branch isolation under intent branch
    `I-1794049523-replace-repo-dir-with-target-worktree`; merging to `main` remains strictly human-maintainer gated.
  - `holon-config/world/constraints.md#3`: Immutability of the three append-only ledgers (`intents.jsonl`,
    `plans.jsonl`, `executions.jsonl`).
  - `.agents/AGENTS.md`: Mandatory tool probing (`command -v`), no throwaway scripts, single root virtual environment
    (`uv run pytest`), target-native paths only, and strict avoidance of mocks for option parsing tests.
- **Potential violations or edge cases:**
  - Retaining an accidental alias or fallback to `--repo-dir`: Violates the explicit requirement for zero
    backward-compatibility aliases and rejection with an unrecognized argument error.
  - Missed subcommand parser: If one of the 11 subcommands still looks for `args.repo_dir`, it will fail at runtime with
    an `AttributeError`.
  - Stale references to `HOLON_REPO_DIR` in container runners or test fixtures: Could cause tests or runners to fall
    back to default workspace unexpectedly.
  - Stale docstrings or help strings referencing `--repo-dir`.
- **Mitigations built into the plan:**
  - Complete removal of `add_repo_dir_option` and all `--repo-dir` arguments from all parser configurations.
  - Comprehensive unit test in `test_cli_options.py` testing each of the 11 subcommands to assert that `--repo-dir`
    raises an unrecognized argument error (`SystemExit` with exit code 2).
  - Explicit global search and replacement of `HOLON_REPO_DIR` with `HOLON_TARGET_WORKTREE`.
  - Verification of test suite via `uv run pytest` and linting via `uv run task check`.
- **Residual risk accepted (and why):**
  - Minimal residual risk: Pre-release status permits breaking CLI flag changes, and automated tests ensure full parser
    and runtime coverage.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 3.20
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

The strategy completely replaces `--repo-dir` with `--target-worktree` across all options, subcommands, engine flow
contexts, runner interfaces, and test fixtures. It ensures that the Holon CLI presents a coherent, worktree-first
interface aligned with Git's bare clone and worktree primitives. The plan proceeds in five verified steps: option
definition, subcommand registration, engine and context refactor, test suite authoring, and end-to-end quality
validation.

---

## Step 1: Canonical CLI Option Definition and Option Helpers Refactor

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, tightly scoped refactoring of the shared options module and role dispatchers with high
  determinism and low risk.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Replace `add_repo_dir_option` with `add_target_worktree_option` in `sandbox_executor.cli.options`,
  register strictly `--target-worktree` with `dest='target_worktree'`, and update `cli/__init__.py` and role command
  modules.
- **Git branch:** `I-1794049523-replace-repo-dir-with-target-worktree`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/cli/options.py`:
   - Replace function `add_repo_dir_option` with
     `add_target_worktree_option(parser: argparse.ArgumentParser, default: str | None = None, help: str = "Target git worktree directory path (default: current directory).") -> None`.
   - Register argument `--target-worktree` with `dest="target_worktree"`, `default=default`, and the provided help text.
   - Completely remove `add_repo_dir_option` and any references to `--repo-dir`.
2. In `apps/sandbox-executor/src/sandbox_executor/cli/__init__.py`:
   - Update imports from `sandbox_executor.cli.options` to import `add_target_worktree_option` instead of
     `add_repo_dir_option`.
   - Update `__all__` list to export `add_target_worktree_option` and remove `add_repo_dir_option`.
   - Update internal helpers such as `dispatch_role_agent` parameter list and keyword argument forwarding to accept
     `target_worktree: str | None = None` (updating docstrings and comments).
3. In `apps/sandbox-executor/src/sandbox_executor/cli/commands/_role.py`:
   - Update `prepare_role_execution` to read `getattr(args, "target_worktree", None)` and pass it under the key
     `"target_worktree"`.
4. In `apps/sandbox-executor/src/sandbox_executor/cli/commands/execute.py`,
   `apps/sandbox-executor/src/sandbox_executor/cli/commands/intent.py`, and
   `apps/sandbox-executor/src/sandbox_executor/cli/commands/plan.py`:
   - Replace import of `add_repo_dir_option` with `add_target_worktree_option`.
   - Call `add_target_worktree_option(parser)`.
   - Ensure arguments read `args.target_worktree` when invoking role dispatchers.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2`, `apps/sandbox-executor/docs/hermetic_testing.md`
- **Potential failure modes for this step:** Stale imports of `add_repo_dir_option` raising `ImportError`.
- **Guardrails and early‑abort checks:** Probe imports using
  `python -c "from sandbox_executor.cli.options import add_target_worktree_option"`.

### Success & Discard Criteria

- **Success:** `add_target_worktree_option` is exported, `--target-worktree` is registered with
  `dest='target_worktree'`, and `add_repo_dir_option` is completely removed.
- **Discard:** If breaking changes to role execution cannot be resolved within predicted cost.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.50  |
| impact_pred         | 15.00 |
| cost_pred           | 1.50  |
| learning_value_pred | 1.00  |
| ev_pred             | 13.55 |

### Step Metrics Rationale

Calculated via `EV = 0.98 * 15.00 + 0.5 * 1.00 - 0.3 * 0.50 - 1.50 = 14.70 + 0.50 - 0.15 - 1.50 = 13.55`. High
probability and low entropy due to standard option refactoring in a single module and its immediate callers.

---

## Step 2: Subcommand Option Registration and Dispatcher Migration across 11 Commands

- **Sub‑intent recommendation:** NO
- **Reasoning:** Straightforward migration across 11 CLI command definitions in
  `apps/sandbox-executor/src/sandbox_executor/cli/commands/`.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update all 11 Holon CLI subcommands (`flow`, `doctor`, `calibrate`, `review`, `ledger`, `promote`,
  `reconcile`, `record`, `stats`, `veto`, `consult`) to accept and read `args.target_worktree`.
- **Git branch:** `I-1794049523-replace-repo-dir-with-target-worktree`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/cli/commands/flow.py`:
   - Replace `parser.add_argument("--repo-dir", ...)` with `add_target_worktree_option(parser, default=".")` or
     `parser.add_argument("--target-worktree", dest="target_worktree", default=".", help="Target git worktree directory path (default: current directory).")`.
   - Update `run()` and worktree provisioning helpers (`_ensure_flow_worktree`, `_build_flow_context`) to read
     `getattr(args, "target_worktree", ".")` and assign `args.target_worktree = worktree_path`.
2. In `apps/sandbox-executor/src/sandbox_executor/cli/commands/doctor.py`:
   - Import `add_target_worktree_option` from `sandbox_executor.cli.options` and add
     `add_target_worktree_option(parser, default=".")`.
   - Update `run()` and `cli.run_doctor` signature / implementation to accept and read
     `getattr(args, "target_worktree", ".")`.
3. In `apps/sandbox-executor/src/sandbox_executor/cli/commands/calibrate.py`:
   - Replace `--repo-dir` argument with `add_target_worktree_option(parser, default=".")` or `--target-worktree`.
   - In `run()`, pass `target_worktree=args.target_worktree` (updating `run_calibrate` call).
4. In `apps/sandbox-executor/src/sandbox_executor/cli/commands/review.py`:
   - Replace `--repo-dir` with `--target-worktree` (dest `target_worktree`).
   - In `run()`, read `getattr(args, "target_worktree", None)` and forward as `target_worktree`.
5. In `apps/sandbox-executor/src/sandbox_executor/cli/commands/ledger.py`:
   - Replace `--repo-dir` with `--target-worktree`.
   - In `run()`, resolve ledger path using `args.target_worktree`.
6. In `apps/sandbox-executor/src/sandbox_executor/cli/commands/promote.py`:
   - Replace `--repo-dir` with `--target-worktree`.
   - In `run()`, resolve database and knowledge directory paths using `args.target_worktree`.
7. In `apps/sandbox-executor/src/sandbox_executor/cli/commands/reconcile.py`:
   - Replace `--repo-dir` with `--target-worktree`.
   - In `run()`, resolve ledger path using `args.target_worktree`.
8. In `apps/sandbox-executor/src/sandbox_executor/cli/commands/record.py`:
   - Replace `--repo-dir` with `--target-worktree`.
   - In `run()`, resolve ledger path using `args.target_worktree`.
9. In `apps/sandbox-executor/src/sandbox_executor/cli/commands/stats.py`:
   - Replace `--repo-dir` with `--target-worktree`.
   - In `run()`, resolve database path using `args.target_worktree`.
10. In `apps/sandbox-executor/src/sandbox_executor/cli/commands/veto.py`:
    - Replace `--repo-dir` with `--target-worktree` in both list and lift sub-commands.
    - In `run()`, resolve database path using `args.target_worktree`.
11. In `apps/sandbox-executor/src/sandbox_executor/cli/commands/consult.py`:
    - Replace `--repo-dir` with `--target-worktree`.
    - In `run()`, resolve database path using `args.target_worktree`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2`, `docs/safety.md`
- **Potential failure modes for this step:** Omission of a subcommand leading to inconsistent CLI flag behavior.
- **Guardrails and early‑abort checks:** Inspect all 11 command parser configurations via `build_parser()` to verify
  `--target-worktree` is accepted and `--repo-dir` is absent.

### Success & Discard Criteria

- **Success:** All 11 subcommands accept `--target-worktree` as the canonical flag and read `args.target_worktree`.
- **Discard:** Any requirement to retain `--repo-dir` alias is rejected.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.80  |
| impact_pred         | 25.00 |
| cost_pred           | 2.50  |
| learning_value_pred | 1.50  |
| ev_pred             | 22.01 |

### Step Metrics Rationale

Calculated via `EV = 0.96 * 25.00 + 0.5 * 1.50 - 0.3 * 0.80 - 2.50 = 24.00 + 0.75 - 0.24 - 2.50 = 22.01`. Broad impact
establishing uniform CLI flag conventions across all subcommands.

---

## Step 3: FlowContext, Engine Internals, and Environment Variable Migration

- **Sub‑intent recommendation:** NO
- **Reasoning:** Core engine parameter refactoring across `flow.py`, `agent_runner.py`, `turns.py`, `calibration.py`,
  and `git_flow.py`.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Rename `FlowContext.repo_dir` to `FlowContext.target_worktree`, rename parameter names from
  `repo_dir` to `target_worktree` in engine modules, and replace `HOLON_REPO_DIR` with `HOLON_TARGET_WORKTREE`.
- **Git branch:** `I-1794049523-replace-repo-dir-with-target-worktree`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/flow.py`:
   - Rename dataclass attribute `FlowContext.repo_dir: str = "."` to `FlowContext.target_worktree: str = "."`.
   - Update `FlowContext.to_dict()` and `from_dict()` serialization to use `"target_worktree"`.
   - Rename `get_default_checkpoint_path(repo_dir: str, slug: str)` parameter to `target_worktree: str`.
   - Update all internal functions in `flow.py` (e.g. `_ledgers_with_conflict_markers`, `_resolve_conflicting_ledger`,
     `_resolve_commit_ref`, `_run_stage1_intent_creation`, `_run_stage2_planning`, `_run_stage3_execution`,
     `_run_stage4_review`, `_run_stage5_calibration`, etc.) to name the parameter `target_worktree: str` and reference
     `context.target_worktree`.
2. In `apps/sandbox-executor/src/sandbox_executor/agent_runner.py`:
   - Rename parameter in `cleanup_repo_dir(repo_dir: str, ...)` to `cleanup_target_worktree(target_worktree: str, ...)`
     or rename parameter `target_worktree: str`.
   - In `get_workspace_dir()`: Replace reading `os.getenv("HOLON_REPO_DIR")` with `os.getenv("HOLON_TARGET_WORKTREE")`.
   - Update docstrings and error messages to refer to worktree directory and `HOLON_TARGET_WORKTREE`.
3. In `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`:
   - Update docstrings and references from `HOLON_REPO_DIR` to `HOLON_TARGET_WORKTREE`.
   - Replace `os.getenv("HOLON_REPO_DIR")` checks with `os.getenv("HOLON_TARGET_WORKTREE")`.
4. In `apps/sandbox-executor/src/sandbox_executor/calibration.py`:
   - Rename parameter `repo_dir: str = "."` to `target_worktree: str = "."` across `fetch_remote_ref_if_needed`,
     `resolve_commit_sha`, `resolve_git_ref`, `read_git_file`, `is_calibration_report_stale`, `run_calibrate`, and
     associated helper functions.
   - Update docstrings to reflect target git worktree.
5. In `apps/sandbox-executor/src/sandbox_executor/git_flow.py`:
   - Rename parameter `repo_dir: str` to `target_worktree: str` across worktree management and branch helpers:
     `is_bare_repository`, `is_working_tree`, `prune_worktrees`, `derive_worktree_layout`, `ensure_worktree`,
     `check_pr_mergeability`, etc.
6. In `apps/sandbox-executor/src/sandbox_executor/turns.py`:
   - Ensure working directory parameters for agent execution and supervisor monitoring consistently accept and document
     `target_worktree`.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (Bottleneck step: `p_success_pred = 0.94`; failure to rename consistently across all engine
  calls will cause runtime attribute or keyword argument errors)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2`, `docs/safety.md#2`
- **Potential failure modes for this step:** Keyword argument mismatch between callers and functions when invoking
  `run_calibrate` or `FlowContext`.
- **Guardrails and early‑abort checks:** Use static analysis (`ruff check`) and targeted unit tests to verify all
  internal call signatures match.

### Success & Discard Criteria

- **Success:** `FlowContext.target_worktree` is the single source of truth, engine functions use `target_worktree`, and
  `HOLON_TARGET_WORKTREE` replaces `HOLON_REPO_DIR`.
- **Discard:** Discard if changes introduce regressions into worktree resolution or git flow execution.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.90  |
| impact_pred         | 30.00 |
| cost_pred           | 3.00  |
| learning_value_pred | 2.00  |
| ev_pred             | 25.93 |

### Step Metrics Rationale

Calculated via `EV = 0.94 * 30.00 + 0.5 * 2.00 - 0.3 * 0.90 - 3.00 = 28.20 + 1.00 - 0.27 - 3.00 = 25.93`. Represents the
core internal refactoring step with the highest impact on code clarity and architectural correctness.

---

## Step 4: Test Suite Option Flag Migration and Dedicated CLI Options Unit Tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Authoring test cases and updating existing integration tests to exercise real CLI option parsing and
  ensure rejection of obsolete flags.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Create `apps/sandbox-executor/tests/test_cli_options.py` and update `test_flow.py` and other test
  files to verify `--target-worktree` parsing, assert `--repo-dir` fails with an unrecognized argument error, and pass
  `target_worktree`.
- **Git branch:** `I-1794049523-replace-repo-dir-with-target-worktree`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `apps/sandbox-executor/tests/test_cli_options.py`:
   - Test canonical option parsing: Verify that passing `--target-worktree <path>` to `build_parser().parse_args(...)`
     for each of the 11 subcommands (`flow`, `doctor`, `calibrate`, `review`, `ledger`, `promote`, `reconcile`,
     `record`, `stats`, `veto`, `consult`) correctly populates `args.target_worktree == <path>`.
   - Test obsolete flag rejection: Assert that passing `--repo-dir <path>` to `build_parser().parse_args(...)` for each
     of the subcommands triggers `SystemExit` with exit code 2 and outputs an unrecognized argument error to stderr.
   - Test default resolution: Assert that omitting `--target-worktree` sets `args.target_worktree` to the expected
     default (e.g. `.` or `None`).
   - Minimise mocks: Test real `argparse` parser structures constructed by `build_parser()`.
2. Update `apps/sandbox-executor/tests/test_flow.py`:
   - Update tests instantiating `FlowContext` to use `target_worktree=...` instead of `repo_dir=...`.
   - Update CLI invocation tests (e.g. `test_flow_auto_creates_worktree_for_bare_repo_via_cli`) to pass
     `--target-worktree` instead of `--repo-dir`.
   - Update assertions inspecting `called_ctx.target_worktree`.
3. Update other tests across the test suite:
   - In `apps/sandbox-executor/tests/test_agent_runner.py`, `test_executor.py`, and `hermetic_fixtures.py`, update
     environment variable patching from `HOLON_REPO_DIR` to `HOLON_TARGET_WORKTREE`.
   - Update any direct keyword arguments from `repo_dir` to `target_worktree`.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3`, `.agents/AGENTS.md` (pytest execution rules, mock minimisation)
- **Potential failure modes for this step:** Broken tests due to remaining `FlowContext(repo_dir=...)` calls.
- **Guardrails and early‑abort checks:** Run
  `uv run pytest apps/sandbox-executor/tests/test_cli_options.py apps/sandbox-executor/tests/test_flow.py` to confirm
  targeted test execution passes.

### Success & Discard Criteria

- **Success:** `test_cli_options.py` and `test_flow.py` pass cleanly, verifying both `--target-worktree` acceptance and
  `--repo-dir` rejection.
- **Discard:** Discard if mock-heavy or brittle tests fail to validate actual parser behavior.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.70  |
| impact_pred         | 20.00 |
| cost_pred           | 3.00  |
| learning_value_pred | 2.00  |
| ev_pred             | 16.79 |

### Step Metrics Rationale

Calculated via `EV = 0.95 * 20.00 + 0.5 * 2.00 - 0.3 * 0.70 - 3.00 = 19.00 + 1.00 - 0.21 - 3.00 = 16.79`. High learning
value establishing clean CLI argument testing patterns that avoid brittle mocking.

---

## Step 5: End-to-End Test Suite Execution, Formatting, and Lint Verification

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard quality assurance, formatting, linting, and full test suite execution.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Verify the complete test suite and code quality gates using `uv run task test` and
  `uv run task check`.
- **Git branch:** `I-1794049523-replace-repo-dir-with-target-worktree`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run targeted unit tests:
   - Execute `uv run pytest apps/sandbox-executor/tests/test_cli_options.py apps/sandbox-executor/tests/test_flow.py`.
2. Run the full unit test suite:
   - Execute `uv run task test`.
3. Run linting and documentation checks:
   - Execute `uv run task check` (runs `uv run task lint` and `uv run task lint-docs`).
   - If any formatting issues arise, execute `uv run task format` and re-verify.
4. Verify Australian English spelling and docstring conventions across all touched files.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3, Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#1-#3`, `.agents/AGENTS.md`
- **Potential failure modes for this step:** Unformatted code, missing docstrings, or lint failures.
- **Guardrails and early‑abort checks:** Strict execution of taskipy recipes configured in `pyproject.toml`.

### Success & Discard Criteria

- **Success:** All tests pass with zero failures and `uv run task check` passes cleanly.
- **Discard:** Discard if linting or unit tests cannot pass cleanly.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.30  |
| impact_pred         | 10.00 |
| cost_pred           | 1.50  |
| learning_value_pred | 0.50  |
| ev_pred             | 8.46  |

### Step Metrics Rationale

Calculated via `EV = 0.98 * 10.00 + 0.5 * 0.50 - 0.3 * 0.30 - 1.50 = 9.80 + 0.25 - 0.09 - 1.50 = 8.46`. Final
verification gate ensuring zero regressions across the codebase.
