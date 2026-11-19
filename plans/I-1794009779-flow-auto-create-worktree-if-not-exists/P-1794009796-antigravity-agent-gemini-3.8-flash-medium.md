# Plan for I-1794009779-flow-auto-create-worktree-if-not-exists

- **Plan ID:** P-1794009796-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.5.2)
- **Created At:** 2026-11-07T00:03:16.974Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted empirical git worktree testing across bare clones and detached or
  deleted directories.
- **Safety priority level:** standard
- **Priority Justification:** Worktree operations are local filesystem/git actions within repository or temporary test
  fixtures; no destructive host commands or external network access.

## Exploration

- **Proportion of steps that are exploratory:** 0.25
- **Justification:** Probing git worktree edge cases (bare repos, stale worktree metadata, existing vs non-existing
  branch attachment) via hermetic fixtures provides critical empirical certainty.

## Overall Plan Metrics

| metric              | value  |
| ------------------- | ------ |
| p_success_pred      | 0.92   |
| entropy_pred        | 3.80   |
| impact_pred         | 100.00 |
| cost_pred           | 9.50   |
| learning_value_pred | 6.00   |
| ev_pred             | 84.36  |

### Strategy Rationale

The intent is accepted as-is (`ACCEPT_AS_IS`). The goal is to eliminate operator friction, branch collisions, and
runtime failures when invoking `holon flow` from bare Git repositories or when target worktree directories do not exist
or leave stale administrative records. This is achieved through three core technical capabilities:

1. **Automatic Worktree Resolution & Provisioning (`git_flow.py`):** Detecting whether `repo_dir` is a working tree or
   bare repository, deriving canonical worktree paths and branch names, and safely attaching to existing branches or
   provisioning new ones.
2. **Self-Healing Stale Worktree Records:** Pre-emptively executing `git worktree prune` to clean orphaned records from
   disk deletions before worktree addition.
3. **Hermetic Integration Testing Minimising Mocks:** Constructing real bare Git repositories in `tmp_path` to verify
   genuine worktree provisioning, pruning, and branch reuse without brittle mocking.

**Derivation of Overall Metrics:**

- `p_success_pred`: Derived with Step 3 as the bottleneck step (`p_success_pred = 0.92`). The compound probability
  across all steps is `0.94 * 0.95 * 0.92 * 0.98 = 0.805`, with an overall qualitative estimate of `0.92` based on high
  step independence, robust error handling, and hermetic verification.
- `entropy_pred`: Derived by summing step entropies (`1.20 + 0.80 + 1.40 + 0.40 = 3.80`), which is well below the
  allocated entropy budget of `15.0`.
- `impact_pred`: Derived by summing step impacts (`35.00 + 25.00 + 30.00 + 10.00 = 100.00`), reflecting end-to-end
  reliability when running `holon flow` across diverse repository layouts.
- `cost_pred`: Derived by summing step resource costs (`2.80 + 2.20 + 3.50 + 1.00 = 9.50`).
- `learning_value_pred`: Derived by summing step learning values (`2.00 + 1.50 + 2.00 + 0.50 = 6.00`), capturing
  reusable patterns for headless Git worktree orchestration.
- `ev_pred`: Derived using the config formula
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost` where `mu = 0.5` and `lambda = 0.3`:
  `EV = 0.92 * 100.00 + 0.5 * 6.00 - 0.3 * 3.80 - 9.50 = 92.00 + 3.00 - 1.14 - 9.50 = 84.36`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md#1`: Python runtime `==3.13.*`, dependencies managed strictly via `uv` at the
    repository root. Zero new external dependencies introduced.
  - `holon-config/world/ruleset.md#2`: Australian English spelling (centralised, sanitised, minimise, prioritised),
    strict static type annotations (`typing`), PEP 8 compliance, and comprehensive docstrings outlining inputs, outputs,
    exceptions, and side-effects.
  - `holon-config/world/ruleset.md#3`: Testing via `pytest==9.1.1` (`uv run pytest`); unit and integration tests placed
    under `apps/sandbox-executor/tests/`.
  - `holon-config/world/constraints.md#1` & `docs/safety.md#1`: Branch isolation under intent branch
    `I-1794009779-flow-auto-create-worktree-if-not-exists`; merging to `main` remains strictly human-maintainer gated.
  - `holon-config/world/constraints.md#3`: Immutability of the three append-only ledgers (`intents.jsonl`,
    `plans.jsonl`, `executions.jsonl`).
  - `.agents/AGENTS.md`: Mandatory tool probing (`command -v`), no throwaway scripts, single root virtual environment
    (`uv run pytest`), target-native paths only, and strict avoidance of mocks for filesystem/git integration tests.
- **Potential violations or edge cases:**
  - Unclean repository state where previous worktree folder was deleted from disk but registered in `.git/worktrees/`:
    mitigated by pre-emptive `git worktree prune`.
  - Branch collision when branch `flow-<slug>` already exists from an earlier partial run: mitigated by detecting
    existing branch refs and using `git worktree add <path> <branch>` rather than `git worktree add -b`.
  - Running in bare repository without checked-out working files: mitigated by auto-provisioning a dedicated worktree
    and redirecting `repo_dir`.
  - Path separator incompatibilities on different platforms: mitigated by using target-native path abstractions
    (`pathlib.Path`, `os.path`).
- **Mitigations built into the plan:**
  - Pre-emptive `git worktree prune` before any creation or attachment call.
  - Ref-existence check to discriminate between branch creation and branch attachment.
  - Fallback from `origin/main` to local `main` or `HEAD` in disconnected test environments.
  - Real hermetic integration testing in `tmp_path` without external network access or mocks.
- **Residual risk accepted (and why):**
  - Minimal residual risk: Git worktree commands are standard Git plumbing with battle-tested atomic locking semantics.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 3.80
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

The strategy introduces robust Git worktree management and self-healing automation directly into Holon Flow:

1. **Modular Worktree Helper Layer (`git_flow.py`):**
   - Provide functions `is_bare_repository(repo_dir)`, `is_working_tree(repo_dir)`, `prune_worktrees(repo_dir)`, and
     `ensure_worktree(repo_dir, slug, target_branch)`.
   - Ensure all Git command executions scrub agent secrets and enforce sensible timeouts via `run_git`.
2. **CLI Flow Lifecycle Integration (`flow.py`):**
   - At the beginning of `flow.run()`, before context construction, detect whether the target `repo_dir` exists and is a
     valid working tree.
   - If `repo_dir` does not exist or points to a bare repository, derive the worktree location and branch name
     (`flow-<slug>`) from the intent metadata.
   - Auto-provision or attach the worktree via `ensure_worktree()` and update `args.repo_dir` and `context.repo_dir` so
     all subsequent lifecycle stages execute seamlessly in the dedicated worktree.
3. **Hermetic Integration Tests Minimising Mocks (`test_flow.py`):**
   - Use pytest's `tmp_path` fixture to set up real bare and working tree Git repositories on disk.
   - Test worktree auto-provisioning from bare clones, self-healing stale worktree records after manual folder deletion,
     and idempotent branch reuse.
4. **Validation and Quality Gates:**
   - Execute affected tests via `uv run pytest apps/sandbox-executor/tests/test_flow.py`, run the full test suite once
     via `uv run task test`, and verify code and documentation formatting via `uv run task check`.

---

## Step 1: Implement Worktree Provisioning & Self-Healing Utilities in git_flow.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Implementing worktree resolution and pruning utilities is a focused, low-risk module with well-defined
  Git command semantics that integrates directly into sandbox-executor.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Automatic git worktree pruning reliably self-heals stale worktree registrations caused by
  unceremoniously deleted directories, allowing seamless branch attachment or creation without manual operator
  intervention.
- **Learning target:** Verify how `git worktree prune` and `git worktree add` behave across diverse Git repository
  topologies (bare clone, existing local branch, remote tracking ref, and deleted directory artifacts).
- **Maximum acceptable cost for this learning:** `3.00` cost units.

### Intent & Git Integration

- **Step Intent:** Create a dedicated helper module `apps/sandbox-executor/src/sandbox_executor/git_flow.py` containing
  robust utilities to inspect working tree status, prune stale worktree records, derive canonical worktree paths and
  branch names, and attach or create dedicated git worktrees.
- **Git branch:**
  `I-1794009779-flow-auto-create-worktree-if-not-exists/P-1794009796-antigravity-agent-gemini-3.8-flash-medium/E-1794009796-worktree-provisioning-helpers/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Define `is_bare_repository(repo_dir: str) -> bool`:
   - Invoke `git rev-parse --is-bare-repository` within `repo_dir` using safe Git runner utilities.
   - Return `True` if stdout is `true`, otherwise `False`. Handle invalid paths or non-git directories gracefully by
     returning `False` or raising clear domain exceptions.

2. Define `is_working_tree(repo_dir: str) -> bool`:
   - Check if `repo_dir` exists on disk as a directory.
   - Invoke `git rev-parse --is-inside-work-tree` within `repo_dir`.
   - Return `True` if stdout is `true` and the repository is not bare; return `False` if path is absent, command fails,
     or repo is bare.

3. Define `prune_worktrees(repo_dir: str) -> None`:
   - Execute `git worktree prune` in `repo_dir` with `check=False` to scrub stale administrative registrations for
     directories that were deleted without `git worktree remove`.

4. Define `derive_worktree_layout(base_repo_dir: str, slug: str) -> tuple[str, str]`:
   - Derive the branch name as `flow-{slug}`.
   - Derive the target worktree path: if `base_repo_dir` is a bare clone or specific directory, resolve target worktree
     path alongside it (e.g., `../flow-{slug}` or `flow-{slug}` within parent directory). Ensure target-native path
     handling using `os.path` and `pathlib.Path`.

5. Define `ensure_worktree(repo_dir: str, slug: str, target_branch: str = "origin/main") -> str`:
   - If `repo_dir` exists and is already a valid working tree, return `repo_dir` directly.
   - Identify the repository root / git directory. If `repo_dir` does not exist, resolve to current repository or
     adjacent git directory.
   - Run `prune_worktrees(base_git_dir)` self-healing step before attempting any worktree creation or attachment.
   - Derive target branch name `flow-{slug}` and target worktree path.
   - Check if target branch `flow-{slug}` exists locally (`refs/heads/flow-{slug}`) or remotely
     (`refs/remotes/origin/flow-{slug}`).
   - If branch does not exist: create and attach worktree via
     `git worktree add --no-track -b flow-{slug} <path> <target_branch>`. If `<target_branch>` (e.g. `origin/main`) does
     not exist in local/offline test repos, fall back gracefully to `main` or `HEAD`.
   - If branch already exists: attach worktree via `git worktree add <path> flow-{slug}` without colliding or recreating
     the branch.
   - Return the verified active worktree directory path.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2`, `docs/safety.md#1`, `docs/safety.md#2`.
- **Potential failure modes for this step:**
  - `git worktree add` failure if path already exists on disk; mitigated by checking path existence and running
    `worktree prune`.
  - Missing remote tracking branch `origin/main` in disconnected environments; mitigated by checking ref validity before
    passing to `git worktree add` and falling back to `main`.
- **Guardrails and early‑abort checks:**
  - Validate that `slug` is sanitised and non-empty.
  - Fail closed if Git commands fail unexpectedly, logging redacted commands.

### Success & Discard Criteria

- **Success:** `apps/sandbox-executor/src/sandbox_executor/git_flow.py` exports `is_bare_repository`, `is_working_tree`,
  `prune_worktrees`, and `ensure_worktree` with full type annotations, docstrings, and error handling.
- **Discard:** Discard if worktree operations require unresolvable root permissions or external network dependencies.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 1.20  |
| impact_pred         | 35.00 |
| cost_pred           | 2.80  |
| learning_value_pred | 2.00  |
| ev_pred             | 30.74 |

### Step Metrics Rationale

- `p_success_pred`: `0.94`. Git worktree CLI commands are mature and well-documented; standard subprocess handling with
  redaction in `run_git` minimizes failure risk.
- `entropy_pred`: `1.20`. Minimal disorder introduced into the codebase as this is a modular helper file.
- `impact_pred`: `35.00`. Provides the core engine for self-healing worktree automation across bare clones and deleted
  paths.
- `cost_pred`: `2.80`. Modest development and local debugging effort.
- `learning_value_pred`: `2.00`. Yields valuable insights into git worktree lifecycle edge cases in headless automation.
- `ev_pred`: Derived inside code spans:
  `EV = 0.94 * 35.00 + 0.5 * 2.00 - 0.3 * 1.20 - 2.80 = 32.90 + 1.00 - 0.36 - 2.80 = 30.74`.

---

## Step 2: Integrate Automatic Worktree Resolution into Flow CLI Command Lifecycle

- **Sub‑intent recommendation:** NO
- **Reasoning:** Integrating into `flow.py` involves localized orchestration wiring at the start of `run()` before
  `FlowContext` construction, maintaining low complexity and clear boundaries.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** In `apps/sandbox-executor/src/sandbox_executor/cli/commands/flow.py`, automatically detect whether
  `repo_dir` exists and is a valid working tree; if not, invoke `ensure_worktree()` to provision or attach the worktree
  using the intent slug, and update `args.repo_dir` and the active context.
- **Git branch:**
  `I-1794009779-flow-auto-create-worktree-if-not-exists/P-1794009796-antigravity-agent-gemini-3.8-flash-medium/E-1794009796-cli-flow-worktree-integration/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Extract or determine intent slug:
   - In `_resolve_checkpoint_and_intent()`, or right before building the context in `run()`, inspect `intent_data` or
     `context` for the `slug` field (e.g. `intent_data.get("slug")` or `getattr(context, "slug", None)`).
   - If an intent file or checkpoint provides a slug, use it as the slug identifier for the flow worktree. If slug
     cannot be found, derive a sanitised fallback from intent file basename or target directory name.

2. Detect working tree state and auto-provision:
   - Check if `args.repo_dir` is a valid working tree via `is_working_tree(args.repo_dir)`.
   - If `args.repo_dir` does not exist or `is_bare_repository(args.repo_dir)` is True, invoke
     `ensure_worktree(args.repo_dir, slug=slug, target_branch=target_branch)`.
   - Log an informative message indicating that an isolated git worktree has been provisioned or attached for the flow
     run.

3. Update active execution paths:
   - Update `args.repo_dir` to point to the resolved active worktree directory.
   - Ensure `_build_flow_context()` assigns the provisioned worktree path to `context.repo_dir`.
   - Ensure subsequent flow stages (intent creation, planning, execution, review, calibration) operate seamlessly within
     the isolated worktree directory.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#1`, `holon-config/world/constraints.md#1`, `docs/safety.md#1`.
- **Potential failure modes for this step:**
  - Running `holon flow` without an intent file or slug; mitigated by checking for slug presence and preserving existing
    behaviour when neither bare repo nor non-existent directory is supplied.
  - Modifying operator directory state; mitigated by scoping worktree creation to isolated flow-specific subdirectories
    or sibling paths.
- **Guardrails and early‑abort checks:**
  - Verify `repo_dir` points to a valid on-disk directory before delegating to `cli.run_flow_pipeline`.

### Success & Discard Criteria

- **Success:** Executing `holon flow <intent>` with `--repo-dir` pointing to a bare clone or non-existent path
  automatically establishes the dedicated worktree and proceeds through the flow stages.
- **Discard:** Discard if flow CLI execution breaks existing options (such as `--checkpoint`, `--from-stage`, or
  `--dry-run`).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.80  |
| impact_pred         | 25.00 |
| cost_pred           | 2.20  |
| learning_value_pred | 1.50  |
| ev_pred             | 22.06 |

### Step Metrics Rationale

- `p_success_pred`: `0.95`. High certainty since the CLI invocation sequence in `run()` is concise and well-isolated.
- `entropy_pred`: `0.80`. Minimal systemic entropy; fits neatly into existing argument resolution and context builder
  routines.
- `impact_pred`: `25.00`. Enables seamless invocation of `holon flow` from bare clones, monorepos, and arbitrary
  execution directories.
- `cost_pred`: `2.20`. Low implementation complexity.
- `learning_value_pred`: `1.50`. Demonstrates clean parameter lifecycle management and dynamic context derivation in CLI
  pipelines.
- `ev_pred`: Derived inside code spans:
  `EV = 0.95 * 25.00 + 0.5 * 1.50 - 0.3 * 0.80 - 2.20 = 23.75 + 0.75 - 0.24 - 2.20 = 22.06`.

---

## Step 3: Implement Real Hermetic Git Integration Tests Minimising Mocks

- **Sub‑intent recommendation:** NO
- **Reasoning:** Authoring focused hermetic integration tests using `tmp_path` directly validates the Git worktree
  mechanics without mocks and fits cleanly into the existing test suite.
- **Step Type:** TEST
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Testing worktrees against real Git commands on disk in `tmp_path` exposes true filesystem
  and ref-locking nuances that mocks routinely obscure, ensuring real-world reliability.
- **Learning target:** Understand the exact exit codes and stderr signatures of `git worktree add` and
  `git worktree prune` across bare clones, deleted directories, and pre-existing local branches.
- **Maximum acceptable cost for this learning:** `3.50` cost units.

### Intent & Git Integration

- **Step Intent:** Add hermetic integration tests in `apps/sandbox-executor/tests/test_flow.py` (or a dedicated
  `apps/sandbox-executor/tests/test_worktree.py`) that create real bare Git repositories in `tmp_path`, test automatic
  worktree provisioning, self-healing stale record pruning, and idempotent branch reuse without mocking Git
  subprocesses.
- **Git branch:**
  `I-1794009779-flow-auto-create-worktree-if-not-exists/P-1794009796-antigravity-agent-gemini-3.8-flash-medium/E-1794009796-hermetic-worktree-tests/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Helper fixture for real hermetic Git repo in `tmp_path`:
   - Initialize a real Git repository with an initial commit on `main`.
   - Create a bare clone of the repository in a subfolder (`bare.git`).
   - Ensure `git config user.name` and `git config user.email` are set locally to ensure commit operations succeed
     hermetically.

2. Test Case 1: Automatic worktree creation from bare repository:
   - Provide `repo_dir` pointing to `bare.git`.
   - Invoke `ensure_worktree()` with a test slug (e.g., `test-auto-provision`).
   - Verify that a worktree directory is created, branch `flow-test-auto-provision` is created, and the worktree has a
     valid checked-out working tree with files from `main`.

3. Test Case 2: Self-healing stale worktree records via `git worktree prune`:
   - Create a worktree using `git worktree add`.
   - Forcefully delete the worktree folder from disk using `shutil.rmtree` (simulating an operator deleting the
     directory without running `git worktree remove`).
   - Note that Git still holds administrative records in `.git/worktrees/`.
   - Call `ensure_worktree()` with the same path/slug.
   - Verify that `git worktree prune` cleans the stale registration and allows creation or attachment of the worktree
     without raising fatal Git errors.

4. Test Case 3: Idempotent attachment to existing branch:
   - Create an existing branch `flow-test-reuse` in the repository.
   - Invoke `ensure_worktree()` for that slug.
   - Verify that `git worktree add <path> <branch>` attaches the existing branch without
     `fatal: a branch named ... already exists`.

5. Test Case 4: Flow CLI end-to-end dry-run with worktree auto-provisioning:
   - Test invoking `flow.run()` with `--dry-run` and `--repo-dir` set to a bare repository.
   - Verify that the CLI automatically provisions the worktree and completes without error.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (Validation bottleneck for the entire worktree provisioning feature; tests prove acceptance
  criteria)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3`, `docs/safety.md#2`, `AGENTS.md` (no mocks where real git
  operations can be run hermetically in `tmp_path`).
- **Potential failure modes for this step:**
  - Filesystem permission issues in test environments; mitigated by using pytest `tmp_path` fixture which cleans up
    automatically.
  - Slower execution if multiple clones are created; mitigated by keeping repositories minimal (single lightweight
    commit).
- **Guardrails and early‑abort checks:**
  - Assert that all tests run strictly offline without network access.

### Success & Discard Criteria

- **Success:** All new hermetic integration tests pass deterministically via
  `uv run pytest apps/sandbox-executor/tests/test_flow.py` (or test_worktree.py) with 100% assertions satisfied and zero
  mock leakage.
- **Discard:** Discard if tests require internet access, root privileges, or fail intermittently across platforms.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.40  |
| impact_pred         | 30.00 |
| cost_pred           | 3.50  |
| learning_value_pred | 2.00  |
| ev_pred             | 24.68 |

### Step Metrics Rationale

- `p_success_pred`: `0.92`. Bottleneck step because real Git subprocess operations in `tmp_path` must handle
  cross-platform paths and refspec resolution cleanly.
- `entropy_pred`: `1.40`. Introduces new integration test scenarios that interact directly with the disk and Git binary.
- `impact_pred`: `30.00`. Provides permanent regression protection against worktree collisions, stale worktrees, and
  bare repo issues.
- `cost_pred`: `3.50`. Constructing hermetic Git scenarios in `tmp_path` takes slightly more setup than pure unit mocks,
  but provides vastly superior confidence.
- `learning_value_pred`: `2.00`. Demonstrates best-practice real Git fixtures in `pytest` for agentic coding.
- `ev_pred`: Derived inside code spans:
  `EV = 0.92 * 30.00 + 0.5 * 2.00 - 0.3 * 1.40 - 3.50 = 27.60 + 1.00 - 0.42 - 3.50 = 24.68`.

---

## Step 4: Verification, Linting, Quality Gates & Regression Suite Execution

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard regression verification and formatting sweeps across the workspace to guarantee release
  readiness.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Run targeted test passes, workspace linting, doc formatting, and the complete standard test suite to
  guarantee zero regressions and strict adherence to codebase quality standards.
- **Git branch:**
  `I-1794009779-flow-auto-create-worktree-if-not-exists/P-1794009796-antigravity-agent-gemini-3.8-flash-medium/E-1794009796-verification-and-gates/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Execute targeted test suite:
   - Run affected tests first: `uv run pytest apps/sandbox-executor/tests/test_flow.py` (or `test_worktree.py`).
   - Validate that all new and pre-existing flow test cases pass cleanly without warnings.

2. Execute full standard unit test suite:
   - Run `uv run task test` once to ensure no regressions in other packages or entrypoints.

3. Execute code and documentation quality gates:
   - Run `uv run task check` (which runs `uv run task lint` and `uv run task lint-docs`).
   - Ensure Ruff linting, formatting checks, and Prettier markdown checks succeed cleanly with zero errors.
   - If minor formatting deltas exist, run `uv run task format` and verify with `uv run task check`.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#1`, `holon-config/world/ruleset.md#2`,
  `holon-config/world/ruleset.md#3`, `AGENTS.md`.
- **Potential failure modes for this step:**
  - Formatting violations or unformatted markdown triggers Prettier failures; mitigated by running `uv run task format`
    if required.
- **Guardrails and early‑abort checks:**
  - Verify virtual environment health using `uv run task check`.

### Success & Discard Criteria

- **Success:** Both `uv run pytest` and `uv run task check` exit with code 0.
- **Discard:** Discard if unrelated test failures or unresolvable lockfile discrepancies emerge.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.40  |
| impact_pred         | 10.00 |
| cost_pred           | 1.00  |
| learning_value_pred | 0.50  |
| ev_pred             | 8.93  |

### Step Metrics Rationale

- `p_success_pred`: `0.98`. High probability of success for automated test runs and linting tasks.
- `entropy_pred`: `0.40`. Minimal risk and zero disorder introduced.
- `impact_pred`: `10.00`. Provides total quality assurance and CI compliance.
- `cost_pred`: `1.00`. Standard execution runtime.
- `learning_value_pred`: `0.50`. Confirms workspace stability and test hygiene.
- `ev_pred`: Derived inside code spans:
  `EV = 0.98 * 10.00 + 0.5 * 0.50 - 0.3 * 0.40 - 1.00 = 9.80 + 0.25 - 0.12 - 1.00 = 8.93`.
