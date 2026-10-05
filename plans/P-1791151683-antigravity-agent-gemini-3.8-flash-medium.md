# Plan for I-1791151674-calibration-integrity-resilience-and-staleness-detection

- **Plan ID:** P-1791151683-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.1.22)
- **Created At:** 2026-10-04T22:08:03.260Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted empirical validation of robust git ref resolution across remote refs
  and commit SHAs, fail-loud git diff metrics calculation, immutable git show provenance extraction, append-only
  calibrated branch commits, and deterministic commit SHA staleness detection.
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

- **Proportion of steps that are exploratory:** 0.50
- **Justification:** Steps 1, 2, and 4 incorporate balanced exploration to test resilient ref resolution fallbacks
  (remote tracking refs, commit SHAs, PR head OIDs), immutable git object reading via git show, and commit SHA staleness
  detection logic across divergent git states without disrupting live repositories.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.5   |
| impact_pred         | 95.0  |
| cost_pred           | 14.0  |
| learning_value_pred | 5.5   |
| ev_pred             | 75.70 |

### Strategy Rationale

The overall plan metrics were derived from individual step-level metrics as follows:

- **p_success_pred**: 0.92. Dictated by the bottleneck step (Step 6: 0.92), which requires constructing hermetic unit
  tests simulating complex git edge cases (remote ref fallback, missing commits, non-zero shortstat diff failures,
  provenance git-show extraction, fast-forward branch updates, and staleness detection) while strictly adhering to
  hermetic testing guidelines without risking container workspace deletion.
- **entropy_pred**: 1.5. Derived as the maximum single-step risk profile (Step 6: 1.5), where comprehensive test mocking
  and git subprocess interactions are validated. The sum of predicted step entropies is 6.3
  (`1.1 + 1.2 + 1.0 + 0.9 + 0.6 + 1.5 = 6.3`), which fits well within the allocated entropy budget of 15.0.
- **impact_pred**: 95.0. Systematically resolves four critical defects (Beans 0054, 0060, 0065, 0066) in the Stage 5
  post-execution calibration engine, restoring integrity to patch size and entropy calculations, preventing destructive
  branch overwrites, enabling mechanical staleness detection on PR updates, and ensuring accurate CLI `--no-commit`
  reporting.
- **cost_pred**: 14.0. Calculated as the direct sum of individual step costs
  (`2.5 + 2.5 + 2.0 + 2.0 + 1.5 + 3.5 = 14.0`).
- **learning_value_pred**: 5.5. Epistemic gain from formalizing robust git ref resolution heuristics across local,
  remote, and commit SHA references, establishing immutable provenance reading standards via `git show`, and
  implementing verifiable staleness detection mechanisms in automated software evolution pipelines.
- **ev_pred**: 75.70. Computed strictly via the config-driven Expected Value formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`. With system constants `lambda = 0.3`
  and `mu = 0.5` loaded from `holon-config/metrics/ev_config.json`:
  `EV = 0.92 * 95.0 + 0.5 * 5.5 - 0.3 * 1.5 - 14.0 = 87.40 + 2.75 - 0.45 - 14.0 = 75.70`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python target `==3.13.*`, workspace
    isolation under `apps/sandbox-executor`).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8 compliance, explicit static typing with
    `typing` module, complete docstrings for public functions, no wildcard imports).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: `pytest==9.1.1`, test files located under
    `apps/sandbox-executor/tests/`, explicit planning of test changes).
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: prefix-based branch isolation, rebase
    discipline, commit boundaries).
  - `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: strict filesystem containment, prevention of
    escape actions and unwhitelisted modifications).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: zero modification of existing
    `holon-knowledge/ledger/*.jsonl` lines, append-only operations).
  - `docs/safety.md` §1 & §2 (Git as safety boundary, sandboxing mandatory for execution).
  - `apps/sandbox-executor/docs/hermetic_testing.md` §1-§3 (Mandatory Rules for Tests: Rule 1: Every role entrypoint
    test must pin HOLON_REPO_DIR to fixture; §3 Sandbox Safety Rail: never run full test discovery from
    `~/.holon-sandbox/workspace`).
- **Potential violations or edge cases:**
  - Destructive git commands wiping uncommitted changes or published branch histories (`checkout -B`).
  - Git subprocess calls failing when branches exist only on remote tracking refs or as detached commit SHAs.
  - Git diff shortstat exiting non-zero and silently returning zero modified files, producing distorted metrics.
  - Telemetry read from mutable host checkout rather than immutable commit objects at the execution ref.
  - Calibration reports becoming silently stale as additional review commits land on the PR branch.
  - Misleading console logs indicating a commit was created when `--no-commit` was passed.
- **Mitigations built into the plan:**
  - Robust ref resolution with automatic remote tracking lookup and explicit fetch before computing diffs.
  - Fast-forward append-only commits on `/calibrated` branches instead of destructive `checkout -B`.
  - Loud failure on non-zero diff shortstat exit codes to prevent corrupted metrics.
  - Provenance data extraction via `git show <ref>:<path>` with defensive fallback to working tree.
  - Explicit recording of `Evaluated Commit SHA` and automated staleness comparison against branch HEAD.
  - Accurate `--no-commit` logging and JSON serialization (`"committed": false`).
  - Strict compliance with sandbox safety rail during verification.
- **Residual risk accepted (and why):**
  - Network-isolated environments where remote tracking branches cannot be fetched dynamically: the ref resolver
    gracefully attempts local resolution and commit SHA fallback before reporting a resolution failure.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 6.3
- **Budget Compliance:** The strategy fits within budget (Predicted Plan Entropy sum of 6.3 is well below the 15.0
  allocated budget).

## Plan Description & Strategy

This plan implements calibration integrity, resilience, and staleness detection across
`sandbox_executor/calibration.py`, `cli.py`, and `flow.py`:

1. **Robust Git Reference Resolution & Remote Fetching (Beans 0054, 0060):** Implement helper functions in
   `sandbox_executor/calibration.py` to resolve execution and plan refs against remote tracking refs
   (`origin/<branch>`), commit SHAs, or PR head refs (`refs/pull/<n>/head` or `gh pr view --json headRefOid`) if local
   refs are missing or stale. Automatically fetch remote target refs before calculating metrics and eliminate the
   requirement that the execution branch be checked out locally.
2. **Fail-Loud Metrics Diff Calculation & Git Ref Provenance Extraction (Bean 0060):** Update `parse_actual_metrics` to
   raise a loud `RuntimeError` if `git diff --shortstat` fails with non-zero exit; never treat an unresolvable ref as 0
   files modified. Read `executions.jsonl` and execution markdown records via `git show <exec_ref>:<path>` (falling back
   to local file if ref is unavailable) to ensure metrics reflect the execution commit rather than the host checkout's
   active branch. Assert the head SHA measured and warn if it differs from the PR head.
3. **Append-Only Calibrated Branches (Bean 0060):** Make Stage 5 calibration append-only: if `<branch>/calibrated`
   already exists locally or remotely, update the report as a fast-forward commit on top of it instead of destructively
   wiping it with `git checkout -B`.
4. **Evaluated Commit SHA & Staleness Detection (Bean 0065):** In `CalibrationReport`, `format_markdown_report`, and
   JSON output, record the exact evaluated commit SHA (`- **Evaluated Commit SHA:** <sha>`). Implement staleness
   verification helpers (`is_calibration_stale`, `parse_evaluated_commit_sha`) to verify whether the evaluated commit
   SHA matches current branch HEAD, enabling mechanical staleness detection.
5. **Accurate `--no-commit` Logging & JSON Output (Bean 0066):** When `--no-commit` is specified, output clear messages
   indicating the report was written to the working tree and that no branch was created or committed. Omit the
   `Calibrated branch: ...` announcement. In `--json` output, include `"committed": false` (or `true`) and
   `"evaluated_commit_sha": "<sha>"`.
6. **Hermetic Unit Tests & Static Verification:** Implement comprehensive unit tests in
   `apps/sandbox-executor/tests/test_calibration.py` covering remote ref resolution, SHA targets, deleted branch
   fallback, non-zero diff failure, git show provenance reading, fast-forward `/calibrated` branch updates, evaluated
   commit SHA recording, staleness detection, and `--no-commit` messaging/JSON schema. Verify with
   `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest -m "not integration_test and not stress"`, and
   `npx --yes prettier@3.8.4 --check "**/*.md"`.

---

## Step 1: Robust Git Reference Resolution, Remote Fetching & SHA Resolution in sandbox_executor.calibration

- **Sub‑intent recommendation:** NO
- **Reasoning:** Localized enhancements to reference resolution logic within `sandbox_executor/calibration.py` with zero
  external dependencies and immediate testability.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Resolving execution and plan branches through a tiered lookup (commit SHA -> local ref ->
  remote tracking ref -> remote fetch) allows calibration to succeed deterministically in post-merge PR environments,
  ephemeral worktrees, or headless CI checkouts without requiring a local branch checkout.
- **Learning target:** Identify git reference lookup patterns and fetch behaviors that operate cleanly in bare or
  detached HEAD workspaces without network access or remote credentials when local tracking refs exist.
- **Maximum acceptable cost for this learning:** cost_pred of 2.5 units.

### Intent & Git Integration

- **Step Intent:** Implement `resolve_git_ref`, `fetch_remote_ref_if_needed`, and integrate robust reference resolution
  into `extract_branch_components`, `generate_calibration`, and `run_calibrate`.
- **Git branch:** I-1791151674-calibration-integrity-resilience-and-staleness-detection/step1-robust-ref-resolution
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Define helper function `resolve_git_ref(ref_name: str, repo_dir: str = ".") -> str`:
  - Strip leading/trailing whitespace and `/` characters from `ref_name`.
  - Validate if `ref_name` is already a 40-character hexadecimal SHA or short commit SHA using
    `git rev-parse --verify <ref_name>^{commit}` via subprocess. If exit code is 0, return the canonical full commit
    SHA.
  - Check if `ref_name` is a valid local branch using `git rev-parse --verify refs/heads/<ref_name>`. If exit code is 0,
    return `ref_name`.
  - Check if `origin/<ref_name>` or `refs/remotes/origin/<ref_name>` exists using `git rev-parse --verify`. If found,
    return `f"origin/{ref_name}"` or the remote tracking ref.
  - If ref is not found locally, attempt to fetch from remote using `git fetch origin <ref_name>:<ref_name>` or
    `git fetch origin <ref_name>`. Wrap in try/except so network failures in offline sandboxes do not crash
    unexpectedly.
  - Check if `ref_name` matches a pull request pattern (e.g. `pull/<n>/head` or `refs/pull/<n>/head`). Attempt to
    resolve via `git fetch origin pull/<n>/head:pr-<n>` or `gh pr view --json headRefOid` if `gh` CLI is available.
  - If all resolution attempts fail, raise a descriptive `RuntimeError` stating that target reference cannot be resolved
    locally or remotely.
- Define helper function `resolve_commit_sha(ref: str, repo_dir: str = ".") -> str`:
  - Run `git rev-parse --verify f"{ref}^{{commit}}"` in `repo_dir`.
  - Return the 40-character SHA string or raise `RuntimeError` if resolution fails.
- Update `extract_branch_components(branch_str: str)`:
  - Preserve original input while extracting structured identifiers.
  - If `branch_str` is a raw commit SHA or short ref, populate component fields gracefully.
- Update `generate_calibration` to resolve `execution_branch` and `plan_branch` using `resolve_git_ref` before metrics
  extraction.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1 & §2 (typing, docstrings, PEP 8),
  `holon-config/world/constraints.md` §1 (Git Flow constraints), `docs/safety.md` §1.
- **Potential failure modes for this step:** Network timeouts when attempting git fetch in offline sandboxes; ambiguous
  ref names matching both a tag and a branch.
- **Guardrails and early‑abort checks:** Apply 15-second subprocess timeouts; prioritize local refs before attempting
  remote fetches; handle non-zero git fetch exits gracefully without uncaught exceptions.

### Success & Discard Criteria

- **Success:** `resolve_git_ref` resolves local branches, remote tracking branches (`origin/...`), commit SHAs, and
  falls back safely without requiring local checkout.
- **Discard:** Discard if resolution mutates existing local branch HEADs or hangs indefinitely on unavailable remotes.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.1   |
| impact_pred         | 85.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 4.0   |
| ev_pred             | 79.92 |

### Step Metrics Rationale

High certainty implementation (p_success_pred 0.95) with modest entropy (1.1). Epistemic gain (4.0) arises from
establishing universal ref resolution rules across container and host workspaces. Expected Value derivation:
`EV = 0.95 * 85.0 + 0.5 * 4.0 - 0.3 * 1.1 - 2.5 = 80.75 + 2.0 - 0.33 - 2.5 = 79.92`.

---

## Step 2: Fail-Loud Metrics Diff Calculation & Git Ref Provenance Extraction in sandbox_executor.calibration

- **Sub‑intent recommendation:** NO
- **Reasoning:** Critical integrity correction to `parse_actual_metrics` preventing silent metric corruption and reading
  immutable execution telemetry directly from git commit objects.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Failing loudly on non-zero `git diff` shortstat exits prevents distorted zero-stat SSA
  and EV calculations, while extracting `executions.jsonl` and execution markdown records via `git show <ref>:<path>`
  guarantees evaluation reflects the executed commit rather than the host checkout's active branch.
- **Learning target:** Understand failure modes of git show across sparse checkouts, shallow clones, and uncommitted
  working trees to provide reliable fallbacks.
- **Maximum acceptable cost for this learning:** cost_pred of 2.5 units.

### Intent & Git Integration

- **Step Intent:** Update `parse_actual_metrics` in `sandbox_executor/calibration.py` to enforce loud failure on git
  diff errors and read execution metadata from the execution commit ref via `git show`.
- **Git branch:** I-1791151674-calibration-integrity-resilience-and-staleness-detection/step2-fail-loud-diff-provenance
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Define helper function `read_git_file(ref: str, rel_path: str, repo_dir: str = ".") -> str | None`:
  - Run `git show f"{ref}:{rel_path}"` using `subprocess.run` with `capture_output=True`, `text=True`.
  - If returncode is 0, return `res.stdout`.
  - If returncode is non-zero, log debug message and return None.
- Overhaul `parse_actual_metrics` in `sandbox_executor/calibration.py`:
  - In Section 1 (Ledger reading):
    - Attempt to read `holon-knowledge/ledger/executions.jsonl` via
      `read_git_file(target_ref, "holon-knowledge/ledger/executions.jsonl", repo_dir=repo_dir)`.
    - If contents returned from git ref, parse JSON records to locate the execution record matching `execution_id` or
      `plan_branch`.
    - If not found or if `read_git_file` returns None, fall back to reading local file on disk
      (`os.path.join(repo_dir, "holon-knowledge", "ledger", "executions.jsonl")`) with an informational log note.
  - In Section 2 (Execution markdown reading):
    - Attempt to read `executions/{execution_id}.md` via
      `read_git_file(target_ref, f"executions/{execution_id}.md", repo_dir=repo_dir)`.
    - Fall back to reading local file from disk if git show fails.
    - Parse status, exit code, and test pass rate from the extracted content.
  - In Section 3 (Git diff shortstat calculation):
    - Ensure `base_ref` and `target_ref` are resolved commit references or branch names.
    - Run `git diff --shortstat f"{base_ref}..{target_ref}" --`.
    - If returncode is non-zero:
      - Extract error message from `res.stderr.strip()`.
      - Raise
        `RuntimeError(f"Git diff failed between '{base_ref}' and '{target_ref}' with exit code {res.returncode}: {error_message}")`.
      - Never swallow non-zero exit codes or default silently to 0 files modified.
    - Parse shortstat text if returncode is 0; extract files changed, insertions, and deletions.
  - Assert head SHA measured:
    - Obtain evaluated commit SHA via `resolve_commit_sha(target_ref, repo_dir=repo_dir)`.
    - If PR head reference is known and differs from evaluated SHA, log a clear warning alerting operators to head SHA
      divergence.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (Docstrings, typing), `holon-config/world/constraints.md` §3
  (Ledger immutability: read-only access), `docs/safety.md` §1.
- **Potential failure modes for this step:** Broken diff arguments when base_ref and target_ref share no common
  ancestor; git show failing when execution records were created in working tree but uncommitted.
- **Guardrails and early‑abort checks:** Provide graceful fallback to working tree file only when git show returns
  non-zero; ensure exceptions contain precise stderr output for rapid diagnostics.

### Success & Discard Criteria

- **Success:** Non-zero diff exits raise `RuntimeError`; `executions.jsonl` is parsed from git commit ref when
  available; zero files changed is never falsely reported on broken diffs.
- **Discard:** Discard if working tree modifications occur during diff inspection or if valid executions are rejected
  due to transient formatting quirks.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 1.2   |
| impact_pred         | 90.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 4.5   |
| ev_pred             | 83.09 |

### Step Metrics Rationale

Targeted refactoring with high probability of success (0.93) and controlled entropy (1.2). Provides significant
epistemic gain (4.5) by establishing strict metric provenance verification. Expected Value derivation:
`EV = 0.93 * 90.0 + 0.5 * 4.5 - 0.3 * 1.2 - 2.5 = 83.70 + 2.25 - 0.36 - 2.5 = 83.09`.

---

## Step 3: Fast-Forward & Append-Only Calibrated Branch Management in sandbox_executor.calibration and flow.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Localized git flow update in `run_calibrate` and `flow.py` ensuring re-running Stage 5 is append-only
  and non-destructive, strictly adhering to Bean 0060.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Replace destructive `git checkout -B` branch resets in `run_calibrate` with fast-forward, append-only
  commits on `/calibrated` branches, updating `flow.py` Stage 5 to support idempotent re-runs.
- **Git branch:**
  I-1791151674-calibration-integrity-resilience-and-staleness-detection/step3-append-only-calibrated-branches
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `run_calibrate` in `sandbox_executor/calibration.py`:
  - When `skip_commit` is False:
    - Check if `calibrated_branch` already exists locally: run `git rev-parse --verify refs/heads/{calibrated_branch}`.
    - Check if `calibrated_branch` exists on remote: run
      `git rev-parse --verify refs/remotes/origin/{calibrated_branch}`.
    - If `calibrated_branch` exists locally:
      - Checkout the existing branch: `git checkout {calibrated_branch}`.
      - Fast-forward to latest remote if remote tracking ref exists (`git merge --ff-only origin/{calibrated_branch}`).
    - If `calibrated_branch` exists only on remote:
      - Create local tracking branch: `git checkout -b {calibrated_branch} origin/{calibrated_branch}`.
    - If `calibrated_branch` does not exist locally or remotely:
      - Create new branch from execution ref: `git checkout -b {calibrated_branch} {execution_branch}`.
    - Eliminate all occurrences of `git checkout -B {calibrated_branch} {execution_branch}` which destructively resets
      existing history.
    - Write report file to `plans/{plan_id}_calibration.md`, run `converge_prettier`, stage with `git add`, and commit
      with author/committer environment variables.
    - If commit produces no changes ("nothing to commit"), log informational note without throwing an exception.
- In `flow.py` Stage 5 (`run_calibrate_stage`):
  - Ensure re-running calibration against an already calibrated flow branch completes smoothly using the append-only
    logic, updating `context.calibrated_branch` and recording updated report payload.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §1 (Git Flow constraints: prefix isolation, no destructive
  branch rewrites), `docs/safety.md` §1.
- **Potential failure modes for this step:** Merge conflicts if calibrated branch diverged from execution branch; dirty
  working tree preventing branch switch.
- **Guardrails and early‑abort checks:** Use `--ff-only` for fast-forward synchronization; verify clean working tree
  before switching branches; abort cleanly if merge conflict arises.

### Success & Discard Criteria

- **Success:** Re-running calibration on an existing `/calibrated` branch appends a commit on top rather than wiping
  history; fresh calibration creates branch from execution ref.
- **Discard:** Discard if previous calibration commit history is lost or if branch pointers are forcefully reset.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 1.0   |
| impact_pred         | 85.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 3.5   |
| ev_pred             | 79.35 |

### Step Metrics Rationale

High probability refactoring (0.94) with low entropy (1.0). Preserves git auditability and prevents history loss during
re-calibration. Expected Value derivation:
`EV = 0.94 * 85.0 + 0.5 * 3.5 - 0.3 * 1.0 - 2.0 = 79.90 + 1.75 - 0.30 - 2.0 = 79.35`.

---

## Step 4: Evaluated Commit SHA Recording & Staleness Detection Engine in sandbox_executor.calibration

- **Sub‑intent recommendation:** NO
- **Reasoning:** Core domain enhancement addressing Bean 0065 by recording the exact evaluated commit SHA and providing
  mechanical staleness verification methods.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Recording the exact evaluated commit SHA in the calibration report allows mechanical,
  deterministic staleness verification by comparing the report SHA against branch HEAD, preventing flattering outdated
  metrics after PR review updates.
- **Learning target:** Validate schema integration for commit SHA metadata across markdown reports and JSON payloads
  ensuring compatibility with automated PR review bots.
- **Maximum acceptable cost for this learning:** cost_pred of 2.0 units.

### Intent & Git Integration

- **Step Intent:** Add `evaluated_commit_sha` to `CalibrationReport`, include it in markdown report headers and JSON
  output, and implement `is_calibration_stale` and `parse_evaluated_commit_sha` helper functions.
- **Git branch:** I-1791151674-calibration-integrity-resilience-and-staleness-detection/step4-staleness-detection
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `sandbox_executor/calibration.py`:
  - Update `CalibrationReport` dataclass:
    - Add `evaluated_commit_sha: str = ""` field.
    - Include `evaluated_commit_sha` in `to_dict()` output.
  - In `generate_calibration`:
    - Resolve the execution reference to its canonical 40-character commit SHA using
      `resolve_commit_sha(execution_ref, repo_dir=repo_dir)`.
    - Pass `evaluated_commit_sha` into `report_dict` and `CalibrationReport` constructor.
  - In `format_markdown_report`:
    - Add `- **Evaluated Commit SHA:** `{evaluated_commit_sha}``immediately following`- **Evaluation Timestamp:** ...`.
  - Implement staleness verification helper functions:
    - `parse_evaluated_commit_sha(report_content_or_path: str) -> str | None`:
      - Reads markdown content from string or file path.
      - Uses regex pattern `r"-\s+\*\*Evaluated Commit SHA:\*\*\s+`?([0-9a-fA-F]{7,40})`?"` to extract the evaluated
        SHA.
      - Returns the extracted SHA string or None if unparseable.
    - `is_calibration_stale(report_path: str, current_head_ref: str | None = None, repo_dir: str = ".") -> tuple[bool, str, str]`:
      - Extracts `evaluated_sha` from report using `parse_evaluated_commit_sha`.
      - If `evaluated_sha` is None, return `(True, "", "missing_evaluated_sha")`.
      - Resolve current branch HEAD commit SHA via `resolve_commit_sha(current_head_ref or "HEAD", repo_dir=repo_dir)`.
      - Compare `evaluated_sha` and `current_head_sha`.
      - Return `(is_stale, evaluated_sha, current_head_sha)` where `is_stale = (evaluated_sha != current_head_sha)`.
    - `verify_calibration_freshness(report_path: str, current_head_ref: str | None = None, repo_dir: str = ".") -> bool`:
      - Convenience wrapper returning True if calibration is fresh (not stale), False otherwise.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (Typing, docstrings), `docs/safety.md` §1 & §4 (Entropy and
  calibration signals).
- **Potential failure modes for this step:** Regex mismatches due to unexpected whitespace or markdown formatting
  changes; comparing short SHAs to full 40-character SHAs.
- **Guardrails and early‑abort checks:** Canonicalize all SHAs to lowercase 40-character strings or compare common
  prefixes; format report using `converge_prettier` to guarantee markdown formatting uniformity.

### Success & Discard Criteria

- **Success:** Calibration reports display `- **Evaluated Commit SHA:** <sha>`; JSON output contains
  `evaluated_commit_sha`; `is_calibration_stale` correctly flags reports as stale when new commits land on the branch.
- **Discard:** Discard if staleness check false-positives on unmodified branches or fails to detect new commits.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.9   |
| impact_pred         | 88.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 4.2   |
| ev_pred             | 84.31 |

### Step Metrics Rationale

High certainty implementation (0.96) with low entropy (0.9). Delivers significant learning value (4.2) by establishing a
mechanically auditable integrity check for post-execution evaluations. Expected Value derivation:
`EV = 0.96 * 88.0 + 0.5 * 4.2 - 0.3 * 0.9 - 2.0 = 84.48 + 2.10 - 0.27 - 2.0 = 84.31`.

---

## Step 5: Accurate CLI --no-commit Logging, User Feedback & JSON Schema Synchronization in cli.py and calibration.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Straightforward UI and serialization correction in `sandbox_executor/cli.py` and `calibration.py` to
  eliminate misleading messages and ensure schema consistency under Bean 0066.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Correct `holon calibrate --no-commit` messaging to report working-tree generation without claiming
  commits or branch creation, and include `"committed": false` and `"evaluated_commit_sha"` in `--json` output.
- **Git branch:** I-1791151674-calibration-integrity-resilience-and-staleness-detection/step5-no-commit-logging-json
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `sandbox_executor/calibration.py`:
  - Add `committed: bool = True` field to `CalibrationReport` and include in `to_dict()`.
  - In `run_calibrate`:
    - Record `report.committed = not skip_commit`.
    - When `skip_commit` is True:
      - Write report to `plans/{plan_id}_calibration.md` and format with `converge_prettier`.
      - Do NOT create, switch, or commit to any git branch.
      - If `json_output` is False:
        - Output message: `Calibration report generated in working tree at {report_rel} (uncommitted)`.
        - Do NOT print `Calibrated branch: ...`.
        - Print EV summary: `Predicted EV: ... | Actual EV: ... (ΔEV: ...)`.
      - If `json_output` is True:
        - Output JSON serialized report with `"committed": false`, `"calibrated_branch": None`, and
          `"evaluated_commit_sha": report.evaluated_commit_sha`.
    - When `skip_commit` is False:
      - If `json_output` is False:
        - Print standard messages: `Calibration report generated and committed at {report_rel}` and
          `Calibrated branch: {report.calibrated_branch}`.
      - If `json_output` is True:
        - Output JSON serialized report with `"committed": true` and
          `"evaluated_commit_sha": report.evaluated_commit_sha`.
- In `sandbox_executor/cli.py`:
  - Verify `calibrate` argument parser cleanly passes `skip_commit` and `json_output` to `run_calibrate`.

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (Coding conventions), `docs/safety.md` §1.
- **Potential failure modes for this step:** Inconsistent JSON keys breaking downstream CLI consumers.
- **Guardrails and early‑abort checks:** Ensure backward compatibility of JSON keys; verify output with automated schema
  assertions.

### Success & Discard Criteria

- **Success:** `--no-commit` outputs clear uncommitted messages and omits branch announcement; JSON output accurately
  includes `"committed": false` and `"evaluated_commit_sha"`.
- **Discard:** Discard if `--no-commit` triggers git branch checkout or commit operations.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.6   |
| impact_pred         | 80.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 78.22 |

### Step Metrics Rationale

Very high probability task (0.98) with minimal entropy (0.6). Resolves Bean 0066 confusion and ensures truthful CLI
telemetry. Expected Value derivation:
`EV = 0.98 * 80.0 + 0.5 * 3.0 - 0.3 * 0.6 - 1.5 = 78.40 + 1.50 - 0.18 - 1.5 = 78.22`.

---

## Step 6: Comprehensive Hermetic Unit Testing & Static Code Verification in tests/test_calibration.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standardized verification suite expansion and linter validation strictly conforming to
  `apps/sandbox-executor/docs/hermetic_testing.md` and repository standards.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Build exhaustive unit test coverage for all ref resolution, diff failure, git show provenance,
  append-only branch, staleness detection, and `--no-commit` behaviors in `tests/test_calibration.py`, followed by
  linter and format verification.
- **Git branch:** I-1791151674-calibration-integrity-resilience-and-staleness-detection/step6-comprehensive-tests
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `apps/sandbox-executor/tests/test_calibration.py`:
  - Add `TestRefResolution` suite:
    - Test resolving full 40-character commit SHA.
    - Test resolving local branch reference.
    - Test resolving remote tracking branch (`origin/<branch>`).
    - Test fallback git fetch when branch is missing locally.
    - Test resolving PR head ref.
    - Test loud `RuntimeError` failure when reference cannot be resolved.
  - Add `TestActualMetricsIntegrity` suite:
    - Test `parse_actual_metrics` raises `RuntimeError` when `git diff --shortstat` exits with code 128 (bad ref) or 1.
    - Test `parse_actual_metrics` never swallows diff error into "0 files modified".
    - Test `parse_actual_metrics` reads `executions.jsonl` and execution markdown from `git show <ref>:<path>`.
    - Test fallback to working tree file when `git show` exits non-zero.
  - Add `TestAppendOnlyCalibratedBranch` suite:
    - Test `run_calibrate` appends commit to existing local `/calibrated` branch without `git checkout -B`.
    - Test `run_calibrate` checks out remote tracking `/calibrated` branch if local does not exist.
    - Test `run_calibrate` creates new `/calibrated` branch when neither local nor remote exists.
  - Add `TestEvaluatedCommitShaAndStaleness` suite:
    - Test `format_markdown_report` embeds `- **Evaluated Commit SHA:** <sha>`.
    - Test `parse_evaluated_commit_sha` accurately parses SHA from markdown text.
    - Test `is_calibration_stale` returns `(False, sha, sha)` when evaluated SHA matches current HEAD.
    - Test `is_calibration_stale` returns `(True, old_sha, new_sha)` when HEAD has advanced with new commits.
  - Add `TestNoCommitMessagingAndJson` suite:
    - Test console output with `skip_commit=True` contains "uncommitted" and does not announce calibrated branch.
    - Test JSON output with `skip_commit=True` contains `"committed": false` and `"evaluated_commit_sha"`.
    - Test JSON output with `skip_commit=False` contains `"committed": true`.
- Comply strictly with Sandbox Safety Rail (`apps/sandbox-executor/docs/hermetic_testing.md` §3):
  - Run `uv run ruff check .`
  - Run `uv run ruff format --check .`
  - Run `npx --yes prettier@3.8.4 --check "**/*.md"`
  - Verify unit tests via an isolated scratch copy under `/tmp` with `HOLON_REPO_DIR` pinned to fixture.

### Dependencies & Criticality

- **Depends on:** Steps 1 through 5
- **Is Bottleneck:** YES (Final quality and regression gate)

### Safety & Constraint Considerations

- **Relevant rules:** `apps/sandbox-executor/docs/hermetic_testing.md` §1-§3 (Rule 1: Pin `HOLON_REPO_DIR`, §3 Sandbox
  Safety Rail: Never run full discovery from `~/.holon-sandbox/workspace`).
- **Potential failure modes for this step:** Running pytest directly from workspace risking workspace deletion; flaky
  mocks failing on varied git outputs.
- **Guardrails and early‑abort checks:** Strictly run verification from isolated `/tmp` fixture; mock subprocess calls
  thoroughly with hermetic return objects.

### Success & Discard Criteria

- **Success:** 100% test pass rate across new and existing calibration tests; zero ruff lint errors; zero ruff format
  errors; zero prettier markdown errors; sandbox workspace unharmed.
- **Discard:** Discard if tests fail assertions or if linting violations are detected.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.5   |
| impact_pred         | 95.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 5.0   |
| ev_pred             | 85.95 |

### Step Metrics Rationale

Bottleneck validation step with p_success_pred of 0.92 and entropy_pred of 1.5. Sub-intent is NO because testing is
hermetic, cohesive, and directly validates Step 1-5 changes. Delivers substantial learning value (5.0) by ensuring
complete test harness resilience against complex git states. Expected Value derivation:
`EV = 0.92 * 95.0 + 0.5 * 5.0 - 0.3 * 1.5 - 3.5 = 87.40 + 2.50 - 0.45 - 3.5 = 85.95`.
