# Plan for I-1790379192-fix-executor-git-fallback-reinitialization

- **Plan ID:** P-1790379215-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-09-25T23:33:35.053Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with focus on empirical diagnosis of transient git failure modes in container sandboxes while enforcing strict history-preservation invariants during git reinitialization.
- **Safety priority level:** elevated
- **Priority Justification:** This intent alters the git repository lifecycle and commit generation logic in the core execution engine (`apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`). Under `docs/safety.md` §1 (Git is the safety boundary) and `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints), git history isolation and linear parentage are foundational safety invariants; corrupting or severing commit lineages compromises change traceability and sandbox containment.

## Exploration

- **Proportion of steps that are exploratory:** 0.25
- **Justification:** Step 1 evaluates a balanced exploratory hypothesis regarding in-process diagnosis and remediation of transient git probe failures (dubious ownership, index locks, and environment pollution) within dynamic sandbox containers before resorting to fallback reconstruction. Steps 2 through 4 represent deterministic implementations and regression testing.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 3.2   |
| impact_pred         | 90.0  |
| cost_pred           | 17.0  |
| learning_value_pred | 5.0   |
| ev_pred             | 61.04 |

### Strategy Rationale

The overall plan metrics were derived from the individual step-level metrics as follows:

- **p_success_pred**: 0.85. Derived from the bottleneck steps (Step 2 and Step 4, each with p_success = 0.88), discounted slightly to 0.85 to account for joint execution and edge cases across distinct sandbox environments.
- **entropy_pred**: 3.2. Sourced from the maximum step-level entropy (Step 2: 2.8) with an integration margin for cross-step state tracking. The sum of step-level entropies is 8.8 (2.2 + 2.8 + 1.8 + 2.0), remaining well under the allocated budget of 15.0.
- **impact_pred**: 90.0. Reflects the critical system-wide utility of eliminating orphan commit creation, halting destructive deletion of usable git object databases, and preserving commit history across all sandbox execution runs.
- **cost_pred**: 17.0. Calculated as the exact arithmetic sum of individual step costs (4.0 + 6.0 + 3.0 + 4.0 = 17.0).
- **learning_value_pred**: 5.0. Epistemic gain from formalizing robust git worktree recovery patterns and cataloging transient container failure modes in the knowledge base.
- **ev_pred**: 61.04. Computed strictly using the bootstrap config formula:
  $$EV = P(success) \cdot Impact + \mu \cdot LearningValue - \lambda \cdot \Delta S_{intent} - Cost$$
  with $\lambda = 0.3$ and $\mu = 0.5$:
  $$0.85 \cdot 90.0 + 0.5 \cdot 5.0 - 0.3 \cdot 3.2 - 17.0 = 76.5 + 2.5 - 0.96 - 17.0 = 61.04$$.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python ==3.13.*, uv workspace management).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8 compliance, explicit docstrings, typing guidelines).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: pytest standard runner, explicit declaration of test changes).
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: Prefix-based isolation, commit boundaries).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: Append-only ledger updates to `executions.jsonl`).
  - `docs/safety.md` §1 & §4 (Git safety boundaries, blast radius containment, entropy budget compliance).
- **Potential violations or edge cases:**
  - Remote fetch failure when origin remote is unreachable or network is restricted in high-security sandbox tiers.
  - Stale `.git/index.lock` or conflicting git environment overrides (`GIT_DIR`, `GIT_WORK_DIR`) remaining active during subsequent git operations.
  - Inadvertent loss of uncommitted files modified by the agent if workspace reset operations (`git reset --hard` instead of `--soft`) are applied incorrectly.
  - Masking actual repository corruption if benign check false-positives suppress necessary reinitialization.
- **Mitigations built into the plan:**
  - Explicit diagnostic probing that checks stderr output for known signatures (`safe.directory`, `index.lock`, `GIT_DIR`) prior to taking action.
  - Never calling `shutil.rmtree` on `.git`; moving corrupt repositories to `.git.backup.<timestamp>` inside the workspace to retain full object databases for post-mortem analysis.
  - Re-attaching to `FETCH_HEAD` via `git reset --soft` or `git update-ref`, ensuring untracked and modified worktree files are preserved intact.
  - Explicit loud failure handling: aborting push, recording failure status in `executions.jsonl`, and preserving execution logs if base commit recovery fails.
- **Residual risk accepted (and why):**
  - If the upstream remote repository is completely unreachable during fallback recovery, the execution cannot push its branch; accepted because pushing an orphan root commit disconnected from parent history is strictly prohibited by Git Flow invariants.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 8.8
- **Budget Compliance:** The strategy fits within budget (Predicted Plan Entropy sum of 8.8 < 15.0 allocated).

## Plan Description & Strategy

This plan repairs the sandbox executor git recovery mechanism in `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py` and updates its accompanying test suite in `apps/sandbox-executor/tests/test_executor.py`. 

Under the current implementation, when `git rev-parse --is-inside-work-tree` fails following an agent run, `executor.py` unconditionally deletes the `.git` directory via `shutil.rmtree`, runs `git init`, assigns `git symbolic-ref HEAD refs/heads/<exec_branch>`, and adds `origin`. Because it never fetches or re-attaches the base plan commit, the subsequent commit is created as a disconnected orphan root commit lacking any shared history with `plan_branch` or `main`. Furthermore, deleting `.git` permanently destroys the local object database and reflogs.

To resolve this defect safely:
- In **Step 1**, we implement benign probe diagnostics and remediation to handle transient failures (such as safe directory ownership mismatches, stale lockfiles, and environment variable overrides) without rebuilding healthy repositories.
- In **Step 2**, we replace destructive deletion with timestamped workspace backups and implement history-preserving base commit re-attachment using configurable remote fetching and soft resetting.
- In **Step 3**, we implement loud failure semantics and push gating so that unrecoverable base commits fail explicitly in the ledger and skip remote branch pushing while preserving local diagnostics.
- In **Step 4**, we correct the misleading test assertion in `test_executor.py` and add regression tests verifying parent commit continuity, non-destructive backup behavior, benign error recovery, and failure gating.

---

## Step 1: Diagnose and Remediate Benign Git Worktree Probe Failures

- **Sub‑intent recommendation:** NO
- **Reasoning:** Scoped diagnostic improvement in `executor.py` with moderate complexity and clear functional boundaries.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED
- **Hypothesis being tested:** Transient git probe failures caused by dubious safe.directory ownership, stale index locks, or lingering agent process environment variables (e.g. GIT_DIR/GIT_WORK_DIR) can be detected and remediated in-place without deleting or re-initializing the git repository.
- **Learning target:** Understand the exact distribution of benign probe failure causes in sandbox containers and verify in-place remediation efficacy.
- **Maximum acceptable cost for this learning:** 4.0 cost units.

### Intent & Git Integration

- **Step Intent:** Add diagnostic inspection to `executor.py` following a failed `git rev-parse --is-inside-work-tree` probe to identify and repair benign failure causes (ownership, stale locks, environment variables) and re-verify worktree health before triggering re-initialization.
- **Git branch:** I-1790379192-fix-executor-git-fallback-reinitialization/step1-benign-probe-diagnostics
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`, encapsulate the worktree probe and diagnosis logic in a dedicated helper function.
- Execute `git rev-parse --is-inside-work-tree` capturing stdout, stderr, and return code.
- If the returncode is non-zero, inspect stderr and environment state for benign root causes:
  - Check if stderr indicates dubious repository ownership (e.g. fatal: detected dubious ownership in repository). If so, register the repository path in global git configuration via `git config --global --add safe.directory <repo_dir>`.
  - Check for environment variable pollution left behind by agent runners (such as `GIT_DIR`, `GIT_WORK_DIR`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`). If present in `os.environ`, strip or sanitize them before running git commands.
  - Check for the existence of `.git/index.lock`. If present, inspect whether an active git process is holding it; if orphaned, safely remove the stale lockfile.
- After applying targeted benign remediations, re-execute the `git rev-parse --is-inside-work-tree` probe.
- If the secondary probe succeeds, log an informational notice indicating the benign condition was repaired, and proceed with normal staging without entering repository re-initialization.
- Ensure all subprocess calls adhere to repository redaction guidelines (`redact_args` / `redact_text`).

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO (Failure here falls back to recovery in Step 2, but successful benign repair avoids expensive re-cloning/re-init)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (Coding Conventions & Docstrings), `docs/safety.md` §1 (Git boundary).
- **Potential failure modes for this step:**
  - Inadvertently clearing a valid active index lock during concurrent git execution.
  - Over-broad environment variable stripping affecting subsequent non-git agent tooling.
- **Guardrails and early‑abort checks:**
  - Only remove `.git/index.lock` if confirmed stale or if agent runner process has already exited.
  - Confine environment variable overrides strictly to subprocess command invocations or explicit git-specific keys.

### Success & Discard Criteria

- **Success:** Repositories encountering safe.directory ownership warnings or lingering GIT_* environment variables are successfully repaired in-place and pass the worktree probe without triggering re-initialization.
- **Discard:** Discard if ownership or lock repair destabilizes the container's global git configuration or fails to resolve the probe error.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 2.2   |
| impact_pred         | 75.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 65.34 |

### Step Metrics Rationale

This step tests a balanced hypothesis addressing common container sandbox failure modes. Learning value (5.0) reflects the epistemic gain of cataloging and resolving sandbox environment probe failures, yielding a high EV of 65.34.

---

## Step 2: Implement Non-Destructive Backup and History-Preserving Base Commit Re-Attachment

- **Sub‑intent recommendation:** NO
- **Reasoning:** Core algorithmic repair of git recovery inside `executor.py` requiring coordinated git ref manipulation.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Replace `shutil.rmtree` deletion of `.git` with a non-destructive timestamped backup, and implement base commit fetching and soft-reset re-attachment during re-initialization so execution commits maintain parent history.
- **Git branch:** I-1790379192-fix-executor-git-fallback-reinitialization/step2-nondestructive-history-preservation
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`, locate the fallback re-initialization block triggered when worktree recovery fails.
- Check whether the existing `.git` directory contains a salvageable object database:
  - If `.git` exists and is genuinely unusable or corrupted, do NOT delete it via `shutil.rmtree`.
  - Instead, rename/move the existing `.git` path to a timestamped backup directory within the workspace (e.g. `.git.backup.<timestamp>`), preserving all existing objects, packfiles, and diagnostic logs.
- Initialize a fresh repository using `git init` in `repo_dir`.
- Configure origin remote: add origin with `repo_url` (or update remote URL if already present).
- Fetch the base plan branch commit from origin:
  - Support a configurable retry count (defaulting to 3 attempts with exponential backoff) and refspec to tolerate transient network glitches.
  - Execute `git fetch origin <plan_branch>` with `--depth 1` (or complete refspec).
- Re-attach the base commit to the execution branch:
  - Point HEAD at `exec_branch` and reset onto the fetched `FETCH_HEAD` base commit using `git reset --soft FETCH_HEAD` (or `git update-ref refs/heads/<exec_branch> FETCH_HEAD` followed by setting `git symbolic-ref HEAD refs/heads/<exec_branch>`).
  - Ensure the worktree contents modified by the agent are preserved in the staging area and working directory without being overwritten or discarded.
- Ensure that subsequent `git add` and `git commit` commands produce a commit having the fetched `plan_branch` tip as its immediate parent commit.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Primary architectural fix ensuring commit continuity and preventing orphan root commits)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §1 (Git is the safety boundary), `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints), `holon-config/world/ruleset.md` §2 (Docstrings & PEP 8).
- **Potential failure modes for this step:**
  - Network timeout or authentication failure during `git fetch`.
  - Accidental worktree wiping if `git reset --hard` is invoked instead of `git reset --soft`.
  - Storage exhaustion if repeated large `.git.backup.<timestamp>` directories accumulate.
- **Guardrails and early‑abort checks:**
  - Strictly enforce `--soft` for any reset command to prevent worktree data loss.
  - Check that `FETCH_HEAD` exists before executing branch re-attachment; if fetch fails, transition to the loud failure handler in Step 3.

### Success & Discard Criteria

- **Success:** When re-initialization occurs, the usable `.git` is moved aside rather than deleted, `plan_branch` is fetched, and the resulting execution commit has the `plan_branch` tip commit SHA as its parent.
- **Discard:** Abort if `FETCH_HEAD` cannot be attached or if worktree files created by the agent are lost during reset.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 2.8   |
| impact_pred         | 90.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 74.36 |

### Step Metrics Rationale

High impact (90.0) as this resolves the core orphan commit defect. Entropy (2.8) is moderately elevated due to git ref and reset mechanics, but the deterministic procedure yields an EV of 74.36.

---

## Step 3: Implement Loud Failure Handling and Push Gating for Unrecoverable Git State

- **Sub‑intent recommendation:** NO
- **Reasoning:** Scoped failure mode handling and ledger status updating to prevent propagating orphan branches upstream.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Implement explicit loud failure handling when base commit recovery or fetching fails, logging actionable diagnostics, updating ledger execution status to failure, and blocking remote push of orphan branches.
- **Git branch:** I-1790379192-fix-executor-git-fallback-reinitialization/step3-loud-failure-gating
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`, add error handling when `git fetch` or base commit re-attachment fails during fallback recovery.
- If the base commit cannot be fetched or verified:
  - Log an actionable error message to `sys.stderr` detailing the exact failure (e.g. unable to fetch plan_branch base commit from remote origin, cannot guarantee commit parentage).
  - Update the execution status in memory to `failure`.
  - Update the execution summary with actionable diagnostic details.
  - Write or update the markdown execution record in `executions/{exec_id}.md` reflecting the failed status and diagnostic summary.
  - In `holon-knowledge/ledger/executions.jsonl`, record the execution entry with `status: "failure"`.
  - Force `skip_push = True` to guarantee that no orphan root commit or corrupted branch is pushed to remote origin.
  - Preserve all workspace diagnostics, backup directories (`.git.backup.*`), and modified files for operator inspection.
- Ensure that the process terminates with an informative exit code or exception after recording the failure in the ledger, adhering to ledger immutability and fail-loud principles.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO (Step 2 handles successful recovery; Step 3 ensures safety when recovery is impossible)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §1 (Git boundary), `holon-config/world/constraints.md` §3 (Ledger Immutability), `docs/safety.md` §5 (Human review boundaries).
- **Potential failure modes for this step:**
  - Silently falling back to pushing an orphan commit if error flag checking is incomplete.
  - Corrupting `executions.jsonl` by overwriting instead of appending.
- **Guardrails and early‑abort checks:**
  - Verify that `executions.jsonl` is opened strictly in append mode (`"a"`).
  - Explicit assertion that `git push` is never called when base commit re-attachment fails.

### Success & Discard Criteria

- **Success:** If base commit retrieval fails, the executor logs an actionable error, records `status: "failure"` in `executions.jsonl`, writes the execution markdown record, and skips `git push`.
- **Discard:** Discard if failure handling suppresses error logging or pushes an orphan branch to origin.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.8   |
| impact_pred         | 70.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 62.36 |

### Step Metrics Rationale

Straightforward fail-loud safety logic with low entropy (1.8) and high probability of success (0.92), delivering solid EV (62.36).

---

## Step 4: Correct Defective Test Assertions and Implement Comprehensive Regression Suites

- **Sub‑intent recommendation:** NO
- **Reasoning:** Confined to `apps/sandbox-executor/tests/test_executor.py`, updating existing test assertions and introducing targeted regression unit tests.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Update the misleading assertion in `test_main_git_recovery_on_corrupted_repo` and add comprehensive regression tests covering benign probe recovery, non-destructive backup, commit parent verification, and loud failure gating.
- **Git branch:** I-1790379192-fix-executor-git-fallback-reinitialization/step4-test-assertion-regression
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/tests/test_executor.py`, locate `test_main_git_recovery_on_corrupted_repo`.
- Update the assertion: replace the assertion that merely checks `git symbolic-ref HEAD` with assertions that verify the full non-destructive, history-preserving sequence:
  - Assert that `git fetch` was called for `plan_branch`.
  - Assert that base commit re-attachment (`git reset --soft FETCH_HEAD` or equivalent `git update-ref`) was issued.
  - Assert that no `shutil.rmtree` was called on the usable `.git` directory.
- Add new test `test_main_git_recovery_benign_safe_directory`:
  - Simulate a `safe.directory` error output on the initial `rev-parse` probe.
  - Assert that `git config --global --add safe.directory` is called.
  - Assert that re-initialization is NOT triggered and existing `.git` is preserved.
- Add new test `test_main_git_recovery_preserves_parent_commit_and_files`:
  - Simulate corrupted/missing `.git` after agent run.
  - Verify that the resulting commit has the `plan_branch` commit tip as its parent commit.
  - Verify that `.git` was moved to a timestamped backup path rather than deleted.
  - Verify that codebase files, `executions/*.md`, and `holon-knowledge/ledger/executions.jsonl` are staged and committed.
- Add new test `test_main_git_recovery_fetch_failure_loud_abort`:
  - Simulate failure during `git fetch` on base commit recovery.
  - Assert that `git push` is never called.
  - Assert that `executions.jsonl` records `status: "failure"`.
  - Assert that actionable error output is emitted to `sys.stderr`.
- Run the complete sandbox-executor test suite using `pytest apps/sandbox-executor/tests` to guarantee zero regressions.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2, Step 3
- **Is Bottleneck:** YES (Validates all previous implementation steps and ensures CI compliance under world testing rules)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (Testing Constraints: explicit declaration of test changes), `apps/sandbox-executor` testing conventions.
- **Potential failure modes for this step:**
  - Flaky tests resulting from un-mocked filesystem operations or leaked environment variables.
  - Incomplete mock coverage of git command sequences.
- **Guardrails and early‑abort checks:**
  - Use isolated `tempfile.TemporaryDirectory` contexts and clean patches for all mock subprocess calls.
  - Ensure all mocked `run_cmd` side effects handle git command sequences deterministically.

### Success & Discard Criteria

- **Success:** Misleading assertion is updated, all new regression test cases pass, and the entire sandbox-executor test suite executes with zero failures or errors.
- **Discard:** Discard if regression tests fail to accurately simulate git parentage verification or introduce test suite regressions.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 2.0   |
| impact_pred         | 80.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.5   |
| ev_pred             | 67.55 |

### Step Metrics Rationale

High-confidence testing step validating the entire repair pipeline. Yields high impact (80.0) by locking in correct behavior and preventing future regressions, resulting in an EV of 67.55.
