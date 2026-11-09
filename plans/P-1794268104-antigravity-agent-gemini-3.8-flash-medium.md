# Plan for I-1794268087-mount-host-worktree-into-sandbox-containers

- **Plan ID:** P-1794268104-fd29467b-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.5.3)
- **Created At:** 2026-11-09T23:48:24.994Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None
- **Exploration stance:** refactor_heavy with rigorous host-container responsibility segregation, lossless crash resilience, and complete purge of legacy volume and reset code.
- **Safety priority level:** standard
- **Priority Justification:** All changes strictly respect the process and container sandbox boundary (`docs/safety.md`), eliminating unnecessary root container chown invocations, avoiding network egress dependencies inside the sandbox, and operating on dedicated git branches.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** Deterministic refactor and purge of legacy volume and reset mechanisms governed by explicit acceptance criteria and validated by hermetic unit tests.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 3.80  |
| impact_pred         | 92.0  |
| cost_pred           | 7.50  |
| learning_value_pred | 5.00  |
| ev_pred             | 80.34 |

### Strategy Rationale

The intent is accepted as is (`ACCEPT_AS_IS`). The current architecture where sandbox role containers (Stage 4 review ensemble and resolver, and stage executors) operate within Docker named volumes and execute destructive git preambles (`git reset --hard HEAD`, `git clean -fdx`, `git fetch origin`, and `git checkout -B <branch> origin/<branch>`) on every launch exhibits critical vulnerabilities:
1. Container crashes, timeouts, or OOM terminations wipe all unpushed edits and local commits on subsequent launches.
2. Cloning inside the sandbox requires network egress, GitHub API tokens, and a responsive proxy sidecar.
3. Named volumes are created as root-owned, necessitating a root chown init container (`docker run --user root ... chown`) that introduces security and execution overhead and leaves orphan volumes on disk.

The chosen strategy replaces this legacy machinery with a clean separation of concerns:
- **Host responsibility:** The host flow engine validates that the target worktree is an existing, attached git worktree (auto-provisioning `flow-<slug>` off `origin/main` when absent), and bind-mounts it directly into the container at `/home/holon/.holon-sandbox/workspace`. The host also mounts the repository's backing git directory at its host path so git commands inside the container resolve the git directory without network access.
- **Container responsibility:** The container operates directly in place on the mounted worktree without cloning, fetching, or destructive resets. Unpushed edits, logs, and artifacts are immediately persistent on the host filesystem.
- **Unified artifacts:** Intermediate files (prompts, responses, dispositions, scratch notes) are centralised under `<worktree>/.subagent/`.
- **Complete purge:** All obsolete named volume creation, root chown init containers, volume teardown, in-container cloning and reset functions (`_clone_or_fetch`, `_reset_reused_checkout`, `_ensure_head`, `_origin_matches`, `_has_checkout`), and their corresponding unit tests are completely excised.

Overall plan metrics are derived as follows:
- **Success Probability (`p_success_pred = 0.94`):** Governed by the comprehensive verification bottleneck in Step 6 (`p_success_pred = 0.94`), ensuring that all host worktree mount, git directory resolution, and crash resilience tests pass cleanly without regressions across the test suite.
- **Plan Entropy (`entropy_pred = 3.80`):** Calculated as the direct sum of step-level entropies: `Delta_S = 0.70 + 0.60 + 0.40 + 0.70 + 0.50 + 0.90 = 3.80`. This is well within the allocated budget of `15.0`.
- **Impact (`impact_pred = 92.0`):** Eliminates silent work loss during container crashes, removes root privilege requirements for workspace initialisation, removes sandbox network dependencies for git operations, and significantly streamlines the codebase.
- **Cost (`cost_pred = 7.50`):** Sum of step-level implementation, code purging, documentation updating, and test execution costs: `Cost = 1.40 + 1.20 + 0.80 + 1.30 + 1.00 + 1.80 = 7.50`.
- **Learning Value (`learning_value_pred = 5.00`):** Establishes an enduring pattern for hermetic worktree bind-mounting with backing gitdir resolution across container boundaries in developer agent harnesses.
- **Expected Value (`ev_pred = 80.34`):** Evaluated strictly via system configuration constants (`lambda = 0.3`, `mu = 0.5`):
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.94 * 92.0 + 0.5 * 5.00 - 0.3 * 3.80 - 7.50 = 86.48 + 2.50 - 1.14 - 7.50 = 80.34`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md#1-git-flow--branch-constraints`: Execution runs strictly on isolated intent and plan branches. Merging to `main` remains the exclusive responsibility of human maintainers.
  - `holon-config/world/constraints.md#2-sandbox-containment-tiers`: Low predicted plan entropy (`3.80 < 10.0`) allows execution within the hermetic process and container sandboxes with complete network isolation.
  - `holon-config/world/constraints.md#3-ledger-immutability`: Append-only ledgers (`executions.jsonl`, `intents.jsonl`, `plans.jsonl`) remain strictly append-only; historical entries are never rewritten or deleted.
  - `holon-config/world/ruleset.md#1-runtime--environment-specification`: Target Python `==3.13.*` standard library; no external dependencies added.
  - `holon-config/world/ruleset.md#2-coding-conventions--standards`: Full PEP 8 compliance, Australian English spelling conventions, strict type annotations, and comprehensive docstrings.
  - `holon-config/world/ruleset.md#3-testing-constraints`: All unit and integration tests executed strictly through `uv run pytest`.
  - `docs/safety.md#Sandboxing-model`: Eliminating root chown init containers enhances containment safety by ensuring no container runs with root user privileges during role dispatch.
  - `apps/sandbox-executor/flow_module_budgets.json`: Per-module line budgets are lowered or maintained, never raised.
- **Potential violations or edge cases:**
  - Git in the container encountering gitdir pointer errors because the host git directory path does not resolve inside the container namespace.
  - Dubious ownership warnings from git inside the container (`safe.directory` checks) due to UID mapping differences between host and container user `holon`.
  - Accidental leftover references to `ReviewWorkspaceSession` or named Docker volumes causing runtime `AttributeError` or broken dispatch kwargs.
  - Incomplete cleanup of tests causing failures in suites that expect named volume creation or destructive in-container reset calls.
- **Mitigations built into the plan:**
  - Automatically detect the repository's git directory via `git rev-parse --git-common-dir` or `.git` file parsing on the host, and bind-mount that exact path into the container at the matching host path.
  - Explicitly configure `safe.directory` for the mounted workspace and git directories inside the container.
  - Systematically purge all obsolete volume and reset functions, types, and references across `apps/sandbox-executor/` and `docs/`.
  - Verify every test file and replace obsolete assertions with assertions validating bind-mounts, absence of named volumes, and lossless workspace preservation.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 3.80
- **Budget Compliance:** The strategy fits within budget (`3.80 <= 15.0`).

## Plan Description & Strategy

The plan is divided into six logical, testable steps executed in dependency order:
1. **Host Worktree Validation & Bind-Mounting in CLI & Flow Engine:** Update `cli/__init__.py` (`run_docker_container`, `dispatch_role_agent`) and `flow/dispatch.py` to validate/provision the host worktree, bind-mount the worktree at `/home/holon/.holon-sandbox/workspace`, and mount the backing git directory at its host path inside the container.
2. **Container In-Place Workspace Validation & In-Container Git Purge:** Simplify `entrypoint/review_loop.py` to validate workspace existence and check `git rev-parse HEAD`, completely deleting `_clone_or_fetch`, `_reset_reused_checkout`, `_verify_head`, `_reusable_origin`, and `_has_checkout`.
3. **Unified Intermediate Artifacts under `.subagent/`:** Migrate all review scratch directories and intermediate files from `.holon/review/` to `.subagent/review/` across `review_ensemble.py`, prompt templates, and docstrings.
4. **Worktree Naming and Reuse across All Five Stages:** Enforce persistent `flow-<slug>` worktree reuse across Stages 1–5, forbid creating `pr<N>-<slug>` when `flow-<slug>` exists, and implement ephemeral sibling worktrees `flow-<slug>-rev<index>` for concurrent ensemble turns.
5. **Purge Obsolete Volume Machinery and Update Flow Module Budgets:** Excised `ReviewWorkspaceSession`, `review_session_volume_name`, `REVIEW_SESSION_VOLUME_LABEL`, and `HOLON_REVIEW_PERSIST` from `flow/review/workspace.py` and `loop.py`, remove `SANDBOX_WORKSPACE_UID_GID` from `agent_runner.py`, and lower budgets in `flow_module_budgets.json`.
6. **Comprehensive Hermetic Testing and Verification:** Remove obsolete unit tests that assert named volume creation and reset behaviours, add new hermetic pytest tests covering points 6(a)–(g), and execute `uv run pytest`, `uv run task lint`, and `uv run task lint-docs`.


---

## Step 1: Host Worktree Validation, Provisioning, and Bind-Mount Resolution in CLI and Flow Dispatch

- **Sub‑intent recommendation:** NO
- **Reasoning:** Low risk, tightly scoped refactoring of container invocation parameters and git directory bind-mount resolution in existing dispatcher modules.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Validate or auto-provision the target git worktree on the host before container dispatch, bind-mount it into the container at `/home/holon/.holon-sandbox/workspace`, and mount the backing git directory at its host path so git commands function without network access.
- **Git branch:** `I-1794268087-mount-host-worktree-into-sandbox-containers/P-1794268104-fd29467b-antigravity-agent-gemini-3.8-flash-medium/E-1794268104-host-worktree-bindmount`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. **Host Worktree Validation and Resolution:**
   - In `apps/sandbox-executor/src/sandbox_executor/cli/__init__.py` and `flow/dispatch.py`, ensure that any role container dispatch receives a validated, attached git worktree path.
   - If `target_worktree` is absent, bare, or pointing to the repository's default branch checkout, invoke `git_flow.ensure_worktree()` to derive and attach the dedicated sibling `flow-<slug>` worktree branched off `origin/main`.
2. **Container Bind-Mount Construction:**
   - In `run_docker_container()` within `cli/__init__.py`, eliminate the named Docker volume mount (`-v review_workspace_volume:...`).
   - Add a direct bind-mount mapping the resolved host worktree directory to the canonical container workspace mountpoint: `-v <host_worktree_abs_path>:/home/holon/.holon-sandbox/workspace:rw`.
   - Forward `HOLON_TARGET_WORKTREE=/home/holon/.holon-sandbox/workspace` in the container environment so in-container `get_workspace_dir()` resolves deterministically.
3. **Backing Git Directory Resolution and Mounting:**
   - Inspect the target worktree's `.git` entry on the host. If `<target_worktree>/.git` is a file (standard git worktree), resolve its backing git directory by querying `git rev-parse --git-common-dir` and `git rev-parse --git-dir` from the worktree directory.
   - Resolve the absolute, canonical host path of the common git directory (e.g. `/path/to/main/repo/.git` or `/path/to/bare.git`).
   - Add an additional bind-mount to the container: `-v <host_common_git_dir>:<host_common_git_dir>:rw`. Because the container's `.git` file contains `gitdir: <host_worktree_gitdir_path>`, mounting the host git directory at the identical host path allows git inside the container to read and write commits, refs, and objects natively without modification or network access.
   - If the target worktree is a primary checkout (where `.git` is an internal directory), the primary workspace bind-mount already contains `.git`, so no auxiliary git directory mount is required.
4. **Git Safe Directory and Permission Alignment:**
   - Ensure `safe.directory` is configured for `/home/holon/.holon-sandbox/workspace` and the mounted git directory inside the container, avoiding ownership mismatch errors between the host UID and the container `holon` user.
   - Dispatch the container directly as user `holon` (`1000:1000`) without creating named volumes and without running any root chown init container.
5. **Dispatcher Signature Cleanup:**
   - Remove `review_workspace_volume` parameter from `dispatch_role_agent()`, `run_docker_container()`, and `_dispatch_role()`, replacing it with the standard `target_worktree` parameter.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (All subsequent container executions depend on the host correctly mounting the worktree and backing git directory).

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md#Sandboxing-model`, `holon-config/world/constraints.md#2-sandbox-containment-tiers`.
- **Potential failure modes for this step:** Git commands inside the container failing with "fatal: not a git repository" if the backing git directory path is incomplete or misaligned; container permission errors if UID mapping is rejected.
- **Guardrails and early‑abort checks:** Validate that `os.path.isdir(target_worktree)` holds before dispatch, assert `git rev-parse --git-common-dir` returns 0, and verify with a dry run that the generated Docker argument list contains only valid absolute directory mounts.

### Success & Discard Criteria

- **Success:** Role containers launch with the host worktree bind-mounted at `/home/holon/.holon-sandbox/workspace` and backing git directory mounted; git commands in the container resolve HEAD and branches without network access; zero named Docker volumes created.
- **Discard:** If bind-mounting the worktree or backing git directory triggers permission or containment violations that cannot be resolved without root privileges, stop and abort.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.70  |
| impact_pred         | 90.0  |
| cost_pred           | 1.40  |
| learning_value_pred | 4.00  |
| ev_pred             | 86.79 |

### Step Metrics Rationale

- High probability of success (`p_success_pred = 0.96`) as git worktree directory resolution is deterministic using standard git plumbing (`rev-parse`).
- Low entropy (`entropy_pred = 0.70`) confined to CLI container argument construction.
- Impact (`impact_pred = 90.0`) directly eliminates named Docker volume creation and root init containers.
- Cost (`cost_pred = 1.40`) reflects parameter refactoring and path resolution logic.
- Expected Value derivation:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.96 * 90.0 + 0.5 * 4.00 - 0.3 * 0.70 - 1.40 = 86.40 + 2.00 - 0.21 - 1.40 = 86.79`.


---

## Step 2: Container In-Place Workspace Validation and In-Container Git Purge in `review_loop.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Surgical simplification of container entrypoint initialisation by excising fragile legacy git sync helpers and replacing them with validation assertions.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Replace complex in-container clone, fetch, reset, and wipe functions in `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py` with fast, non-destructive in-place workspace validation.
- **Git branch:** `I-1794268087-mount-host-worktree-into-sandbox-containers/P-1794268104-fd29467b-antigravity-agent-gemini-3.8-flash-medium/E-1794268104-container-inplace-validation`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. **Purge Obsolete Git Network & Reset Functions:**
   - In `apps/sandbox-executor/src/sandbox_executor/entrypoint/review_loop.py`, completely delete:
     - `_clone_or_fetch(repo_dir, target)`
     - `_reset_reused_checkout(repo_dir)`
     - `_verify_head(repo_dir, target)`
     - `_reusable_origin(repo_dir)`
     - `_normalised_repo_url(url)`
     - `_has_checkout(repo_dir)`
     - `_wipe_workspace(repo_dir)`
   - Delete all internal invocations and fallback error handling related to these functions.
2. **Implement Non-Destructive Workspace Validation:**
   - Implement a streamlined `_validate_mounted_workspace(repo_dir: str, target: ReviewTarget) -> ReviewTarget`:
     - Assert that `repo_dir` exists and is a directory. If absent, raise `ReviewPreparationError("validate", f"workspace directory '{repo_dir}' does not exist")`.
     - Run `git rev-parse --verify HEAD^{commit}` in `repo_dir`. If returncode != 0 or output is empty, raise `ReviewPreparationError("validate", f"mounted workspace at '{repo_dir}' is not a valid git commit")`.
     - Read the resolved head commit SHA from the `rev-parse` output and update `target.head_sha` if not already set.
     - Verify that the checked-out branch or commit matches `target.head_branch` (when `target` is a branch reference).
     - Ensure zero destructive operations: no `git reset --hard`, no `git clean -fdx`, no `git fetch origin`, and no `git clone`.
3. **Preserve Container Role Execution Flow:**
   - The review loop, reviewer ensemble turns, and resolver mode continue to execute directly within `repo_dir` (`/home/holon/.holon-sandbox/workspace`).
   - Edits, linters, and tests operate in place.
   - Commit and push operations continue to run directly from the container via `git commit` and `git push origin HEAD:refs/heads/<branch>` without host intermediation.
   - Any uncommitted edit, test log, or intermediate file written by the container is immediately resident on the host filesystem via the bind-mount, ensuring full crash survivability.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md#Sandboxing-model`, `holon-config/world/ruleset.md#2-coding-conventions--standards`.
- **Potential failure modes for this step:** Entrypoint failing if the mounted workspace has an uncommitted index or detached HEAD during legitimate branch reviews.
- **Guardrails and early‑abort checks:** Catch git validation errors in `_validate_mounted_workspace` and format clear, redacted `ReviewPreparationError` diagnostics without crashing abruptly.

### Success & Discard Criteria

- **Success:** `review_loop.py` initialises the workspace in less than 50 milliseconds via `rev-parse HEAD` without network calls, clone, fetch, or clean; all obsolete functions are completely deleted.
- **Discard:** If role containers fail to execute linting, tests, or pushes on the mounted worktree, halt and verify worktree permissions.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.60  |
| impact_pred         | 92.0  |
| cost_pred           | 1.20  |
| learning_value_pred | 4.00  |
| ev_pred             | 88.94 |

### Step Metrics Rationale

- High probability of success (`p_success_pred = 0.96`) as simplifying code by deletion and adding basic validation is predictable and robust.
- Low entropy (`entropy_pred = 0.60`) with significant reduction in code complexity.
- High impact (`impact_pred = 92.0`) eliminating the root cause of past work losses (destructive resets and wipes).
- Cost (`cost_pred = 1.20`) covers module editing and verification.
- Expected Value derivation:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.96 * 92.0 + 0.5 * 4.00 - 0.3 * 0.60 - 1.20 = 88.32 + 2.00 - 0.18 - 1.20 = 88.94`.


---

## Step 3: Unified Intermediate Artifacts under `<worktree>/.subagent/`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Straightforward path migration standardising intermediate review scratch files under the already git-ignored `.subagent/` root directory.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Relocate all intermediate subagent files (reviewer prompts, agent responses, dispositions JSON, stderr tails, scratch notes, and review state) from `.holon/review/` to `<worktree>/.subagent/review/iter-<N>/` so host skills and containers share a single persistent location.
- **Git branch:** `I-1794268087-mount-host-worktree-into-sandbox-containers/P-1794268104-fd29467b-antigravity-agent-gemini-3.8-flash-medium/E-1794268104-unified-subagent-artifacts`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. **Update Review Ensemble Scratch Paths:**
   - In `apps/sandbox-executor/src/sandbox_executor/review_ensemble.py`:
     - Update constant `REVIEW_ROOT_REL = ".subagent/review"`.
     - Update path resolution helpers:
       - `reviewer_dir_rel(iteration, index)` returning `.subagent/review/iter-<n>/reviewer-<i>`.
       - `reviewer_report_rel(iteration, index)` returning `.subagent/review/iter-<n>/reviewer-<i>/report.md`.
       - Update scratch containment guards in `_safe_scratch_path()` and `_review_scratch_root()` to ensure paths resolve strictly within `<repo_dir>/.subagent/review/`.
2. **Update Prompts and Scaffold Templates:**
   - In `holon-config/prompts/review_loop.template.md`:
     - Update instructions to specify that reviewer scratch directories, reports, and trailers are located under `.subagent/review/`.
     - Update prohibitions: "Never read other files under `.subagent/review/`; other reviewers' reports are not part of your input."
   - In `apps/sandbox-executor/src/sandbox_executor/scaffold.py`:
     - Ensure generated report references and namespaced review artifacts point to `.subagent/`.
3. **Harmonise Host-Side and Container Review State:**
   - Ensure the resolver disposition records, error context tails, and intermediate state JSON files write to `<worktree>/.subagent/review/iter-<N>/dispositions.json` and `<worktree>/.subagent/review/state.json`.
   - Because `<worktree>` is bind-mounted directly from the host and `.subagent` is ignored in `.gitignore`, all intermediate reviewer turns and scratch notes written by the container are instantly observable on the host for manual auditing and debugging without polluting the git index.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#2-coding-conventions--standards`, `.gitignore#L10`.
- **Potential failure modes for this step:** Stale references to `.holon/review` breaking regexes or path assertions in existing tests.
- **Guardrails and early‑abort checks:** Validate that `.subagent` is git-ignored, and verify that `_safe_scratch_path()` rejects any path attempting directory traversal outside `.subagent/review/`.

### Success & Discard Criteria

- **Success:** Reviewer ensemble and resolver turns read and write intermediate artifacts under `<worktree>/.subagent/review/`; zero files written to `.holon/review/`.
- **Discard:** If scratch containment validation fails or breaks path security invariants, revert and realign path resolution.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.40  |
| impact_pred         | 85.0  |
| cost_pred           | 0.80  |
| learning_value_pred | 3.00  |
| ev_pred             | 83.88 |

### Step Metrics Rationale

- Very high success probability (`p_success_pred = 0.98`) as path constant updates and directory refactoring are low risk.
- Minimal entropy (`entropy_pred = 0.40`).
- High impact (`impact_pred = 85.0`) providing a single unified intermediate artifact root for both automated containers and manual host tooling.
- Low cost (`cost_pred = 0.80`).
- Expected Value derivation:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.98 * 85.0 + 0.5 * 3.00 - 0.3 * 0.40 - 0.80 = 83.30 + 1.50 - 0.12 - 0.80 = 83.88`.


---

## Step 4: Worktree Naming and Reuse across All Five Stages

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standardises worktree naming lifecycle and branch attachment across all flow stages, preventing worktree drift or redundant recreation.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Enforce that `flow-<slug>` is the single persistent worktree across all five stages (Intent, Plan, Execute, Review, Calibrate) and resumed runs, ensure `pr<N>-<slug>` is never created when `flow-<slug>` exists, and provide ephemeral `flow-<slug>-rev<index>` worktrees for concurrent reviewer tests.
- **Git branch:** `I-1794268087-mount-host-worktree-into-sandbox-containers/P-1794268104-fd29467b-antigravity-agent-gemini-3.8-flash-medium/E-1794268104-worktree-naming-and-reuse`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. **Persistent Worktree Invariant across Stages 1–5:**
   - In `apps/sandbox-executor/src/sandbox_executor/cli/commands/_flow_worktree.py` and `cli/commands/flow.py`:
     - Guarantee that the provisioned or attached `flow-<slug>` worktree remains the single active worktree for all stages of the flow run.
     - When a pull request is created (e.g. in Stage 4 or automated PR creation), verify that the existing `flow-<slug>` worktree is never renamed, deleted, or recreated.
     - In checkpoint saving and restoration (`flow/checkpoint.py`), record and restore the `flow-<slug>` worktree path consistently.
     - Resumed runs with `--checkpoint` or `--from-stage` must locate and reuse the existing `flow-<slug>` worktree without creating duplicate worktrees.
2. **Strict Guard Against `pr<N>-<slug>` Worktree Collisions:**
   - In `sandbox_executor/git_flow.py` and review target resolution:
     - Constrain `pr<N>-<slug>` creation: it is strictly reserved for standalone review commands (e.g. `holon review-loop <pr_number>`) when no local worktree already exists for that branch.
     - If a `flow-<slug>` worktree is already checked out for that branch or PR head branch, reuse the `flow-<slug>` worktree directly and forbid the creation of `pr<N>-<slug>`.
3. **Ephemeral Reviewer Turn Worktrees:**
   - In `sandbox_executor/review_ensemble.py`:
     - If concurrent ensemble reviewer turns require independent worktrees for running isolated test suites or build commands, derive ephemeral sibling worktrees named `flow-<slug>-rev<index>`.
     - Automatically prune and delete these ephemeral sibling worktrees (`git worktree remove --force` followed by `git worktree prune`) immediately upon completion of the review iteration.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md#1-git-flow--branch-constraints`, `docs/safety.md#Core-safety-principles`.
- **Potential failure modes for this step:** Git worktree lock contention or failing to prune ephemeral sibling worktrees on unhandled exceptions.
- **Guardrails and early‑abort checks:** Wrap ephemeral worktree lifecycle in `try...finally` context managers, ensuring `prune_worktrees()` is executed unconditionally.

### Success & Discard Criteria

- **Success:** Flow runs seamlessly across all 5 stages in the single `flow-<slug>` worktree; resumed runs reuse it; `pr<N>-<slug>` is never created when `flow-<slug>` exists; ephemeral reviewer worktrees are cleanly pruned.
- **Discard:** If worktree derivation collides with active branches or fails to attach to the correct head commit, abort and inspect git worktree registry.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.70  |
| impact_pred         | 88.0  |
| cost_pred           | 1.30  |
| learning_value_pred | 4.00  |
| ev_pred             | 84.09 |

### Step Metrics Rationale

- High success probability (`p_success_pred = 0.95`) based on existing robust worktree helpers in `git_flow.py`.
- Entropy (`entropy_pred = 0.70`) reflects multi-stage coordination and checkpoint verification.
- High impact (`impact_pred = 88.0`) guaranteeing workspace continuity from intent creation through calibration.
- Cost (`cost_pred = 1.30`) encompasses worktree derivation and lifecycle cleanup.
- Expected Value derivation:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.95 * 88.0 + 0.5 * 4.00 - 0.3 * 0.70 - 1.30 = 83.60 + 2.00 - 0.21 - 1.30 = 84.09`.


---

## Step 5: Purge Obsolete Volume Machinery and Update Flow Module Budgets

- **Sub‑intent recommendation:** NO
- **Reasoning:** Clean codebase hygiene step excising obsolete Docker volume lifecycle code and ratcheting down flow module budgets.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Delete all Docker named volume creation, root chown init container execution, volume teardown and pruning heuristics, and `HOLON_REVIEW_PERSIST` handling from `workspace.py`, `loop.py`, `agent_runner.py`, and `flow.py`; update documentation; and lower per-module line budgets in `flow_module_budgets.json`.
- **Git branch:** `I-1794268087-mount-host-worktree-into-sandbox-containers/P-1794268104-fd29467b-antigravity-agent-gemini-3.8-flash-medium/E-1794268104-purge-volume-machinery`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. **Purge Named Volume Machinery in `flow/review/workspace.py`:**
   - In `apps/sandbox-executor/src/sandbox_executor/flow/review/workspace.py`, delete:
     - `ReviewWorkspaceSession` class entirely.
     - `review_session_volume_name()` function.
     - `review_persist_enabled()` function and `_REVIEW_PERSIST_DISABLED_VALUES`.
     - `REVIEW_SESSION_VOLUME_LABEL` constant.
     - `_DOCKER_CLI_TIMEOUT_SECONDS` constant.
   - Retain only the sandbox guard primitives: `HOST_REVIEW_REFUSAL` and `reviewers_sandboxed()`.
2. **Clean Up `flow/review/loop.py`:**
   - In `apps/sandbox-executor/src/sandbox_executor/flow/review/loop.py`:
     - Remove `workspace_session` parameter from `run_review_loop()` and `_run_review_loop_iterations()`.
     - Remove all volume instantiation, teardown, and fallback logic.
     - Update `flow/__init__.py` to remove `ReviewWorkspaceSession` re-export if no longer needed, or provide a no-op shim only if external callers require it (prefer total deletion as required by acceptance criterion 5).
3. **Clean Up `agent_runner.py` and `cli/commands/flow.py`:**
   - In `apps/sandbox-executor/src/sandbox_executor/agent_runner.py`:
     - Remove `SANDBOX_WORKSPACE_UID_GID` and comments referencing root-owned named volume initialization.
   - In `apps/sandbox-executor/src/sandbox_executor/cli/commands/flow.py`:
     - Remove any residual volume cleanup or persistence flags.
4. **Update Documentation and Global References:**
   - Search the entire repository for references to `ReviewWorkspaceSession`, `review_session_volume_name`, `HOLON_REVIEW_PERSIST`, and root `chown` init container.
   - Update `apps/sandbox-executor/README.md`, `docs/architecture.md`, and any workflow docs to accurately document the host worktree bind-mount architecture.
5. **Ratchet Down Flow Module Line Budgets:**
   - In `apps/sandbox-executor/flow_module_budgets.json`:
     - Measure new line counts of `review/workspace.py` and `review/loop.py`.
     - Lower their line budgets to their new post-purge line counts, ensuring the ratchet enforcement in `scripts/check_flow_module_budgets.py` passes without raising any module budget.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `apps/sandbox-executor/flow_module_budgets.json`, `holon-config/world/ruleset.md#2-coding-conventions--standards`.
- **Potential failure modes for this step:** Broken imports or unresolved symbol references across other stage modules.
- **Guardrails and early‑abort checks:** Run `git grep` across the codebase for all purged symbols, and execute `uv run task lint` to verify that module budget checks pass.

### Success & Discard Criteria

- **Success:** Zero lines of Docker volume create/rm or root chown code remain in `flow/review/workspace.py`; `flow_module_budgets.json` is lowered; `uv run task lint` passes.
- **Discard:** If lowering budgets fails due to unintended line growth in other flow modules, halt and re-audit changes.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.50  |
| impact_pred         | 90.0  |
| cost_pred           | 1.00  |
| learning_value_pred | 3.50  |
| ev_pred             | 87.90 |

### Step Metrics Rationale

- Very high success probability (`p_success_pred = 0.97`) for pure deletion and budget lowering.
- Low entropy (`entropy_pred = 0.50`).
- High impact (`impact_pred = 90.0`) permanently retiring dead volume code and shrinking maintenance surface.
- Cost (`cost_pred = 1.00`).
- Expected Value derivation:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.97 * 90.0 + 0.5 * 3.50 - 0.3 * 0.50 - 1.00 = 87.30 + 1.75 - 0.15 - 1.00 = 87.90`.


---

## Step 6: Comprehensive Hermetic Pytest Suite and Verification

- **Sub‑intent recommendation:** NO
- **Reasoning:** Critical validation bottleneck verifying all seven acceptance criteria, excising obsolete test cases, and confirming full lint, format, and documentation compliance.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Delete obsolete volume and clone/reset unit tests, implement new hermetic tests covering criteria (a) through (g), and verify the complete suite using `uv run pytest`, `uv run task lint`, and `uv run task lint-docs`.
- **Git branch:** `I-1794268087-mount-host-worktree-into-sandbox-containers/P-1794268104-fd29467b-antigravity-agent-gemini-3.8-flash-medium/E-1794268104-hermetic-verification-suite`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. **Delete Obsolete Tests in Existing Suites:**
   - In `apps/sandbox-executor/tests/test_review_loop.py`:
     - Delete tests asserting in-container clone, reset, clean, and origin-mismatch wipe:
       - `test_reused_matching_checkout_resets_cleans_then_syncs_incrementally`
       - `test_reused_checkout_with_a_mismatched_origin_is_wiped_and_recloned`
       - `test_failing_hermetic_reset_falls_back_to_wipe_and_reclone`
       - `test_failing_reset_followed_by_failing_clone_exits_one`
       - `test_second_pass_reuses_the_clone_and_discards_leaked_state`
       - `test_real_origin_mismatch_is_wiped_and_recloned_from_the_configured_remote`
       - `test_poisoned_reused_checkout_is_wiped_and_recloned`
   - In `apps/sandbox-executor/tests/test_cli.py`:
     - Delete `test_review_workspace_volume_adds_exactly_one_mount_and_pins_the_worktree` and `test_review_workspace_volume_overrides_a_forwarded_host_worktree`.
   - In `apps/sandbox-executor/tests/test_review_dispatch.py`:
     - Delete tests asserting `ReviewWorkspaceSession` Docker volume creation, root chown init calls, and fallback clone arguments.
   - In `apps/sandbox-executor/tests/test_review_workspace_integration.py`:
     - Replace the named Docker volume integration test with a test validating host worktree bind-mounting.
2. **Add New Hermetic Test Suite (`apps/sandbox-executor/tests/test_host_worktree_sandbox.py`):**
   - Assert (a): The host worktree is validated or provisioned and bind-mounted into the container at `/home/holon/.holon-sandbox/workspace` along with its backing git directory, ensuring `git rev-parse HEAD` and `git status` work inside the container without network access.
   - Assert (b): The container entrypoint starts directly on the mounted worktree without performing any git clone, git fetch, git reset `--hard`, or git clean `-fdx`.
   - Assert (c): Files created or modified inside the container workspace appear immediately on the host worktree filesystem.
   - Assert (d): A simulated container crash (abrupt exit code 137 or unhandled termination) leaves all uncommitted edits, untracked files, and test logs intact and readable on the host worktree.
   - Assert (e): Intermediate subagent artifacts (prompts, responses, dispositions, scratch notes) are created strictly under `<worktree>/.subagent/review/iter-<N>/`.
   - Assert (f): Inspecting recorded Docker command invocations proves that zero named Docker volumes (`docker volume create`) are created and zero root chown init containers (`--user root ... chown`) are launched.
   - Assert (g): Resumed flow runs and Stage 4 reviews reuse an existing `flow-<slug>` worktree and never create a duplicate `pr<N>-<slug>` worktree when `flow-<slug>` exists.
3. **Execution and Lint Verification Discipline:**
   - Execute affected test modules first:
     `uv run pytest apps/sandbox-executor/tests/test_host_worktree_sandbox.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_review_dispatch.py`
   - Execute the standard test suite once:
     `uv run task test`
   - Execute formatting and lint checks:
     `uv run task lint`
     `uv run task lint-docs`

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3, Step 4, Step 5
- **Is Bottleneck:** YES (Plan success requires all verification assertions to pass cleanly).

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md#3-testing-constraints`, `docs/safety.md#Test-Run-Discipline`.
- **Potential failure modes for this step:** Flaky timing assertions during container execution simulation or residual references in untested edge cases.
- **Guardrails and early‑abort checks:** Use hermetic fixture repositories (`tmp_path`) with local bare remotes, avoiding network calls and real Docker daemon requirements wherever possible.

### Success & Discard Criteria

- **Success:** All new tests in `test_host_worktree_sandbox.py` pass; all existing tests in `uv run task test` pass; `uv run task lint` and `uv run task lint-docs` exit 0; per-module line budgets in `flow_module_budgets.json` are strictly observed.
- **Discard:** If any test fails due to regression in Stage 1–5 coordination or unmitigated security issues, halt and debug before proceeding.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.90  |
| impact_pred         | 92.0  |
| cost_pred           | 1.80  |
| learning_value_pred | 5.00  |
| ev_pred             | 86.91 |

### Step Metrics Rationale

- Probability of success (`p_success_pred = 0.94`) reflects the rigor of testing multiple lifecycle conditions across container boundaries.
- Entropy (`entropy_pred = 0.90`) accounts for new test construction and mock assertions.
- High impact (`impact_pred = 92.0`) providing end-to-end regression defense for the new architecture.
- Cost (`cost_pred = 1.80`) reflects test implementation and suite execution.
- Expected Value derivation:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`
  `EV = 0.94 * 92.0 + 0.5 * 5.00 - 0.3 * 0.90 - 1.80 = 86.48 + 2.50 - 0.27 - 1.80 = 86.91`.
