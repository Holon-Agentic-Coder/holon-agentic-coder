# Plan for I-1794147383-executor-git-repository-adapter

- **Plan ID:** P-1794147403-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.5.3)
- **Created At:** 2026-11-08T14:16:44.467Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted adapter extraction, rigorous line-count containment in executor.py, and
  comprehensive AST and hermetic unit test authoring.
- **Safety priority level:** standard
- **Priority Justification:** All proposed modifications are strictly internal to `sandbox_executor`
  (`apps/sandbox-executor/src/sandbox_executor/git.py`,
  `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`, and `apps/sandbox-executor/tests/test_git.py`)
  without modifying sandbox policies, credential mechanisms, or core system invariants
  (`docs/safety.md#Sandboxing-model`).

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** Deterministic object-oriented adapter extraction, call-site migration, and regression test
  authoring with clear constraints and acceptance criteria.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 2.70  |
| impact_pred         | 88.0  |
| cost_pred           | 9.20  |
| learning_value_pred | 5.00  |
| ev_pred             | 75.21 |

### Strategy Rationale

The intent is accepted as is (`ACCEPT_AS_IS`). In `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`,
approximately 80 raw `run_cmd(["git", ...])` and subprocess git invocations are directly scattered throughout
orchestration logic. This coupling mixes low-level subprocess argument formatting with execution orchestration,
complicates unit testing, and impedes modular entrypoint decomposition.

Centralising git operations behind a dedicated `GitRepository` adapter in
`apps/sandbox-executor/src/sandbox_executor/git.py` addresses these challenges cleanly:

1. **Intention-revealing API:** Methods such as `rev_parse`, `current_branch`, `status_porcelain`, `is_clean`, `add`,
   `commit`, `push`, `fetch`, `checkout`, `merge_base`, `rebase_onto`, `config`, `reset`, and `clean` provide cohesive
   domain semantics while delegating to the existing robust subprocess runner or an injected runner.
2. **Pluggable runner seam:** By accepting an optional `runner` callable (defaulting to `run_git` with
   `cwd=self.repo_dir`), callers such as `executor.py` can pass `run_cmd` to maintain identical `safe.directory`
   handling, logging, redaction, and command truncation behaviour without breaking existing test seams.
3. **Line count discipline:** Replacing multi-line raw command lists in `executor.py` with concise single-line adapter
   calls will shrink `executor.py` well below its current 2,275-line boundary, maintaining zero new `# noqa`
   suppressions and respecting the repository size limits.
4. **Hermetic testing and automated enforcement:** Adding unit tests in `apps/sandbox-executor/tests/test_git.py`
   verifies `GitRepository` against hermetic temporary repositories and fake runners, while an AST lint test
   programmatically guarantees that no raw `['git', ...]` command literals remain in `executor.py` outside low-level
   runners.

Overall plan metrics are derived as follows:

- **Success Probability (`p_success_pred = 0.94`):** Governed by the bottleneck migration step, Step 2
  (`p_success_pred = 0.94`), which involves refactoring 80 call sites across 2,275 lines of `executor.py` while strictly
  preserving backward compatibility with all existing tests in `test_executor.py`.
- **Plan Entropy (`entropy_pred = 2.70`):** Sum of step-level entropies: `Delta_S = 0.80 + 1.20 + 0.70 = 2.70`, which
  fits comfortably within the allocated entropy budget of `15.0`.
- **Impact (`impact_pred = 88.0`):** Eliminates scattered git plumbing, enforces clean domain abstraction, prevents
  executor monolithic sprawl, and prepares the sandbox entrypoint for full decomposition.
- **Cost (`cost_pred = 9.20`):** Sum of estimated execution costs across adapter implementation, call-site migration,
  and test authoring: `Cost = 2.50 + 4.50 + 2.20 = 9.20`.
- **Learning Value (`learning_value_pred = 5.00`):** Establishes a reusable adapter pattern for subprocess decoupling
  and provides an automated AST inspection pattern for preventing raw command leaks in entrypoints.
- **Expected Value (`ev_pred = 75.21`):** Derived using the standard formula (`lambda = 0.3`, `mu = 0.5`):
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.94 * 88.0 + 0.5 * 5.00 - 0.3 * 2.70 - 9.20 = 82.72 + 2.50 - 0.81 - 9.20 = 75.21`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md#1-git-flow--branch-constraints`: All changes developed on the intent branch
    `I-1794147383-executor-git-repository-adapter`; merging to `main` remains reserved for the human maintainer.
  - `holon-config/world/constraints.md#2-sandbox-containment-tiers`: Predicted plan entropy (`2.70 < 10.0`) qualifies
    for process sandbox execution.
  - `holon-config/world/constraints.md#3-ledger-immutability`: The three append-only ledgers (`intents.jsonl`,
    `plans.jsonl`, `executions.jsonl`) remain strictly untouched and append-only.
  - `holon-config/world/ruleset.md#1-runtime--environment-specification`: Strictly Python `==3.13.*` standard library
    and existing project dependencies; no new third-party packages.
  - `holon-config/world/ruleset.md#2-coding-conventions--standards`: Adhere to PEP 8, Australian English spelling
    (`centralised`, `behaviour`, `synchronised`, `initialise`, `artefact`), intention-revealing domain naming, and
    strict static type annotations.
  - `holon-config/world/ruleset.md#3-testing-constraints`: Tests run strictly through `uv run pytest`; existing test
    assertions in `test_executor.py` remain untouched.
  - File sizing and complexity constraints: `git.py` must remain well under 1,000 lines (target ~450-500 lines);
    `executor.py` must not grow (line count `<= 2,275`); zero new inline `# noqa` suppressions.
- **Potential violations or edge cases:**
  - `executor.py` exceeding 2,275 lines if adapter initialisation or replacement boilerplate is overly verbose.
  - Test seam breakage in `test_executor.py` if monkeypatched `run_cmd` calls fail to be invoked by `GitRepository`.
  - Ruff complexity violations (`C901 > 10`, `PLR0912 > 12`, `PLR0913 > 5`, `PLR0915 > 50`) in newly introduced
    `GitRepository` methods.
  - Subtle divergence in command execution flags (e.g. `check=False` vs `check=True`, stdout stripping) causing silent
    behavioral shifts in git merge or commit error handling.
- **Mitigations built into the plan:**
  - Design `GitRepository` to accept `runner` callable directly, defaulting to `run_git` but allowing `run_cmd` to be
    passed, preserving exact mock and logging semantics.
  - Keep `GitRepository` methods concise and single-purpose, delegating argument formation to dedicated small helper
    methods to prevent complexity threshold breaches.
  - Replace multi-line `run_cmd(["git", ...])` blocks in `executor.py` with concise one-line adapter calls to achieve
    net negative line growth.
  - Preserve all existing module-level functions in `git.py` as delegating wrappers so that external consumers across
    the codebase remain 100% backward compatible.
  - Include an automated AST test in `test_git.py` to continuously assert that no raw `['git', ...]` lists exist in
    `executor.py` outside `run_cmd` and `_get_local_safe_directories`.
- **Residual risk accepted (and why):**
  - Minimal residual risk of subtle git command returncode or exception handling discrepancies across the 80 call sites;
    mitigated by running the full `test_executor.py` test suite with zero regressions allowed.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 2.70
- **Budget Compliance:** The strategy fits within budget (`2.70 <= 15.0`).

## Plan Description & Strategy

This plan introduces a clean `GitRepository` adapter class into `sandbox_executor.git`, migrates all raw git command
invocations in `sandbox_executor.entrypoint.executor` to use this adapter, and establishes automated regression and AST
tests.

The implementation is structured into three discrete, verifiable steps:

1. **Adapter Authoring in `git.py`:** Create `GitRepository` with intention-revealing methods (`run`, `current_branch`,
   `rev_parse`, `status_porcelain`, `is_clean`, `add`, `commit`, `push`, `fetch`, `checkout`, `merge_base`,
   `rebase_onto`, `config`, `reset`, `clean`) bound to `repo_dir` and accepting an optional `runner`. Maintain all
   existing module-level helpers as delegating wrappers. Ensure total file size stays well below 1,000 lines with zero
   new `# noqa` suppressions.
2. **Call-Site Migration in `executor.py`:** Route all 80 raw git subprocess and `run_cmd` call sites in `executor.py`
   through `GitRepository(repo_dir, runner=run_cmd)`, eliminating raw `['git', ...]` literals outside `run_cmd` and
   `_get_local_safe_directories`. Ensure `executor.py` line count remains `<= 2,275` lines with zero new `# noqa`
   suppressions and 100% backward compatibility.
3. **Hermetic & AST Test Authoring in `test_git.py`:** Add comprehensive unit tests in `test_git.py` verifying all
   `GitRepository` methods and add an AST-based test asserting no raw git command lists remain in `executor.py`. Verify
   all tests via `uv run pytest` and ensure `uv run task lint` and `uv run task lint-docs` pass cleanly.

---

## Step 1: Implement `GitRepository` Adapter Class and Delegating Wrappers in `git.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Self-contained object-oriented adapter implementation within `sandbox_executor/git.py` with low
  entropy, clear requirements, and no circular dependencies.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add `GitRepository` class bound to `repo_dir` with an optional `runner` callable and
  intention-revealing methods in `sandbox_executor/git.py`, preserving all existing module-level functions as delegating
  wrappers without exceeding complexity limits or 1,000 lines.
- **Git branch:**
  `I-1794147383-executor-git-repository-adapter/P-1794147403-antigravity-agent-gemini-3.8-flash-medium/E-git-repository-adapter`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/git.py`:
   - Define a type alias `GitRunnerCallable` matching callable signatures that accept command argument lists and keyword
     arguments (`cwd`, `check`, `timeout`, `env`) returning `subprocess.CompletedProcess[str]`.
   - Implement `GitRepository` class:
     - `__init__(self, repo_dir: str | Path, runner: GitRunnerCallable | None = None)`: Initialise with `repo_dir`
       stored as a string and `runner` defaulting to a private delegator to `run_git(..., cwd=self.repo_dir)`.
     - `run(self, args: list[str], check: bool = True, timeout: int | None = 60, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]`:
       Execute arbitrary git arguments (excluding the leading `'git'`) using the configured runner bound to
       `self.repo_dir`.
     - `rev_parse(self, ref: str, verify: bool = False, symbolic: bool = False, is_inside_work_tree: bool = False, is_shallow: bool = False, check: bool = False) -> subprocess.CompletedProcess[str]`:
       Construct appropriate `rev-parse` argument list based on flags and execute.
     - `current_branch(self) -> str | None`: Query current symbolic ref or short branch name, returning `None` in
       detached HEAD state or on failure.
     - `status_porcelain(self, untracked: str = "all") -> str`: Execute `status --porcelain` and return stripped stdout
       text.
     - `is_clean(self) -> bool`: Return boolean indicating whether working tree and staging area have no untracked or
       modified files based on porcelain status.
     - `add(self, paths: list[str] | str, all_files: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]`:
       Stage specified path patterns or all modifications (`-A`).
     - `commit(self, message: str, allow_empty: bool = False, author: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]`:
       Commit staged changes with message and optional flags.
     - `push(self, remote: str = "origin", branch: str | None = None, set_upstream: bool = False, force: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]`:
       Push commits to remote tracking branch.
     - `fetch(self, remote: str = "origin", branch: str | None = None, deepen: int | None = None, prune: bool = False, check: bool = False) -> subprocess.CompletedProcess[str]`:
       Fetch revisions with optional depth deepening.
     - `checkout(self, target: str, create: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]`:
       Switch branches or create and switch (`-b`).
     - `merge_base(self, ref1: str, ref2: str, check: bool = False) -> subprocess.CompletedProcess[str]`: Find common
       ancestor commit between two refs.
     - `rebase_onto(self, newbase: str, upstream: str, branch: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]`:
       Execute git rebase operation.
     - `config(self, key: str, value: str | None = None, local: bool = True, get_all: bool = False, check: bool = False) -> subprocess.CompletedProcess[str]`:
       Read or write repository configuration entries.
     - `reset(self, paths: list[str] | None = None, hard: bool = False, quiet: bool = True, check: bool = False) -> subprocess.CompletedProcess[str]`:
       Unstage files or reset working tree.
     - `clean(self, force: bool = True, directories: bool = True, check: bool = False) -> subprocess.CompletedProcess[str]`:
       Remove untracked files and directories (`git clean -fd`).
     - Additional helper methods for executor compatibility: `merge(target, no_commit, no_ff)`, `merge_abort()`,
       `diff(args)`.
2. Refactor existing module-level functions in `git.py`:
   - Update `resolve_ref(repo_dir, ref)`, `get_head_commit(repo_dir)`,
     `branch_exists(repo_dir, branch_name, check_remote)`, `get_diff_numstat(repo_dir, base_ref, head_ref)`,
     `changed_paths(...)`, and `has_effective_diff(...)` to instantiate or delegate to `GitRepository(repo_dir)` while
     maintaining exact function signatures, docstrings, and return types.
3. Validate coding standards:
   - Ensure all public methods have complete type annotations and explanatory docstrings.
   - Use Australian English spelling in docstrings and comments (`centralised`, `initialise`).
   - Confirm function complexity remains well within ruff limits (`C901 <= 10`, `PLR0912 <= 12`, `PLR0913 <= 5`,
     `PLR0915 <= 50`) without adding any `# noqa` suppressions.
   - Verify line count of `git.py` remains around 450-520 lines, well below the 1,000-line ceiling.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2-coding-conventions--standards`,
  `docs/safety.md#Sandboxing-model`.
- **Potential failure modes for this step:**
  - Incompatible argument defaults or return types breaking delegating wrappers.
  - Adding `# noqa` comments or exceeding ruff complexity ceilings.
- **Guardrails and early‑abort checks:**
  - Verify that `apps/sandbox-executor/tests/test_git.py` continues to pass for all existing delegating wrapper tests.

### Success & Discard Criteria

- **Success:** `GitRepository` is fully defined in `git.py` with all required intention-revealing methods, existing
  module-level helpers delegate to it, `git.py` line count is <= 600 lines, and no `# noqa` comments are added.
- **Discard:** Discard if method signatures cannot support the execution requirements of `executor.py` without breaking
  existing caller contracts.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.80  |
| impact_pred         | 84.0  |
| cost_pred           | 2.50  |
| learning_value_pred | 4.00  |
| ev_pred             | 80.74 |

### Step Metrics Rationale

High success probability (`0.97`) and low entropy (`0.80`) because the adapter encapsulates well-understood git commands
within a single file. `EV = 0.97 * 84.0 + 0.5 * 4.00 - 0.3 * 0.80 - 2.50 = 81.48 + 2.00 - 0.24 - 2.50 = 80.74`.

---

## Step 2: Route Executor Git Operations Through `GitRepository` and Compress Line Count in `executor.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** High volume of call-site updates across one file, but bounded and deterministic refactoring with
  existing test suites providing verification.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Replace direct `run_cmd(["git", ...])` and raw subprocess git invocations in
  `sandbox_executor/entrypoint/executor.py` with `GitRepository(repo_dir, runner=run_cmd)` method calls, ensuring no raw
  git literals remain outside low-level helpers and keeping line count `<= 2,275` lines with zero new `# noqa`
  suppressions.
- **Git branch:**
  `I-1794147383-executor-git-repository-adapter/P-1794147403-antigravity-agent-gemini-3.8-flash-medium/E-executor-git-migration`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`:
   - Import `GitRepository` from `sandbox_executor.git`.
   - Identify all functions containing git invocations:
     - `_probe_git_repo`: Replace `rev-parse --is-inside-work-tree` calls with
       `repo.rev_parse(is_inside_work_tree=True)`.
     - `_ensure_git_user_config`: Replace `git config --local user.email` and `user.name` with `repo.config(...)`.
     - Target branch sync and merge helpers: Replace `git fetch`, `git rev-parse`, `git merge-base`, `git merge`,
       `git diff`, `git status --porcelain`, and `git merge --abort` with corresponding `repo` methods.
     - Commit salvage and salvage pathspec helpers: Replace `git add -A`, `git diff --cached`, `git reset` with
       `repo.add(...)`, `repo.reset(...)`, and `repo.run(...)`.
     - Branch checkout and creation helpers: Replace `git checkout -b` and `git checkout` with `repo.checkout(...)`.
     - Git push and tracking setup: Replace `git push -u` with `repo.push(...)`.
     - Clean and reset routines: Replace `git clean -fd` and `git reset --hard` with `repo.clean()` and
       `repo.reset(hard=True)`.
   - Instantiate `GitRepository(repo_dir, runner=run_cmd)` within repository-scoped functions or create a lightweight
     helper `_get_repo(repo_dir)`.
   - Retain `_get_local_safe_directories(repo_dir)` and `run_cmd(args, ...)` as the sole sanctioned locations where the
     raw string `"git"` or `["git", ...]` is permitted to appear.
2. Line count optimisation:
   - Convert verbose multi-line `run_cmd(["git", ...], cwd=repo_dir, check=False)` patterns into concise single-line
     `repo.method(...)` calls.
   - Clean up redundant intermediate variables where adapter return values or booleans simplify flow control.
   - Guarantee that the line count of `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py` is strictly
     less than or equal to 2,275 lines (targeting ~2,180-2,240 lines).
   - Ensure zero new `# noqa` suppressions are introduced.
3. Test compatibility check:
   - Ensure that `test_executor.py` passes completely without any modifications to test files.
   - Confirm that monkeypatching `run_cmd` in existing tests continues to intercept git calls seamlessly via the
     adapter's injected `runner=run_cmd`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Represents the primary operational risk: touches 80 call sites across 2,275 lines of
  orchestration code; any breakage causes test failures)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2-coding-conventions--standards`,
  `apps/sandbox-executor/README.md`.
- **Potential failure modes for this step:**
  - Line count of `executor.py` expanding past 2,275 lines.
  - Breaking mocks in `test_executor.py` that inspect `run_cmd.call_args`.
  - Accidental omission of git arguments (such as `--no-ff`, `--porcelain`, or custom pathspecs) altering runtime
    execution outcomes.
- **Guardrails and early‑abort checks:**
  - Verify line count immediately via `wc -l apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`.
  - Run `uv run pytest apps/sandbox-executor/tests/test_executor.py` to ensure all existing executor tests pass.

### Success & Discard Criteria

- **Success:** All 80 raw git calls in `executor.py` are routed through `GitRepository`, no raw `['git', ...]` command
  literals remain outside `run_cmd` and `_get_local_safe_directories`, `executor.py` line count is <= 2,275 lines, zero
  new `# noqa` suppressions exist, and all existing tests pass.
- **Discard:** Discard if refactoring causes unresolvable regressions in `test_executor.py` or increases line count
  above 2,275.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 1.20  |
| impact_pred         | 88.0  |
| cost_pred           | 4.50  |
| learning_value_pred | 4.50  |
| ev_pred             | 80.11 |

### Step Metrics Rationale

Bottleneck step with `p_success_pred = 0.94` and `entropy_pred = 1.20` due to the broad touch set in `executor.py`. High
impact (`88.0`) achieved by eliminating 80+ scattered subprocess invocations.
`EV = 0.94 * 88.0 + 0.5 * 4.50 - 0.3 * 1.20 - 4.50 = 82.72 + 2.25 - 0.36 - 4.50 = 80.11`.

---

## Step 3: Implement Hermetic Unit Tests and AST Raw Git Call Linter in `test_git.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard unit test expansion and quality gate verification acting as the conclusive acceptance check.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add hermetic unit tests in `apps/sandbox-executor/tests/test_git.py` covering all `GitRepository`
  methods and add an AST-based test asserting that `executor.py` contains no raw git command literals outside allowed
  helpers, then run full verification suites.
- **Git branch:**
  `I-1794147383-executor-git-repository-adapter/P-1794147403-antigravity-agent-gemini-3.8-flash-medium/E-test-git-adapter`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/tests/test_git.py`:
   - Import `GitRepository` from `sandbox_executor.git`.
   - Add unit tests for `GitRepository` using temporary hermetic git repositories (created with `tmp_path` and
     `git init`):
     - Test initialisation with default runner and custom runner callable.
     - Test `rev_parse` for valid commit SHA, symbolic HEAD, and non-existent revisions.
     - Test `current_branch` in regular branch state and detached HEAD state.
     - Test `is_clean` and `status_porcelain` across untracked, staged, modified, and clean states.
     - Test `add`, `commit`, `checkout`, `reset`, `clean`, and `config` operations against real repository filesystem
       state.
     - Test error handling when `check=True` raises `RuntimeError` with redacted command text.
   - Add AST-based verification test `test_executor_contains_no_raw_git_literals()`:
     - Parse `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py` into an Abstract Syntax Tree using
       `ast.parse`.
     - Traverse AST nodes to find all list/tuple literals and function call arguments.
     - Identify any list literal whose first element is the string literal `"git"`.
     - Assert that any such literal occurs exclusively inside functions named `run_cmd` or
       `_get_local_safe_directories`.
     - Report line numbers and enclosing function names for any unexpected raw git command occurrences.
   - Ensure `apps/sandbox-executor/tests/test_executor.py` is NOT modified or expanded.
   - Verify `test_git.py` adheres to Australian English and ruff formatting.
2. Execute quality gates strictly from repository root:
   - Run unit tests: `uv run pytest apps/sandbox-executor/tests/test_git.py` and
     `uv run pytest apps/sandbox-executor/tests/test_executor.py`.
   - Run task checks: `uv run task check` (`uv run task lint` and `uv run task lint-docs`).
   - Run full test suite: `uv run task test`.
3. Verify ledger immutability and ensure no temporary artifacts or scratch files remain.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3-testing-constraints`, `docs/safety.md#Sandboxing-model`.
- **Potential failure modes for this step:**
  - Flaky git tests caused by global gitconfig interference (mitigate by running tests with hermetic temp directories
    and isolated git env).
  - AST visitor false positives on docstrings or comments (mitigate by inspecting AST `Constant` nodes inside `List` /
    `Call` AST structures specifically).
- **Guardrails and early‑abort checks:**
  - Use `ast.walk` to target only `ast.List` expressions containing `ast.Constant(value="git")` as their first element.

### Success & Discard Criteria

- **Success:** All `GitRepository` unit tests pass, AST inspection confirms zero raw git command literals in
  `executor.py` outside `run_cmd` and `_get_local_safe_directories`, all tests in `test_git.py` and `test_executor.py`
  pass, and `uv run task check` passes cleanly.
- **Discard:** Discard if AST test reveals unmigrated raw git call sites or if linting checks fail.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.70  |
| impact_pred         | 86.0  |
| cost_pred           | 2.20  |
| learning_value_pred | 5.00  |
| ev_pred             | 82.65 |

### Step Metrics Rationale

High success probability (`0.96`) and low entropy (`0.70`) providing rigorous automated verification and programmatic
prevention of future regression.
`EV = 0.96 * 86.0 + 0.5 * 5.00 - 0.3 * 0.70 - 2.20 = 82.56 + 2.50 - 0.21 - 2.20 = 82.65`.
