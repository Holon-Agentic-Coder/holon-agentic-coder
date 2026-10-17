# Plan for I-1792242950-flow-review-pr-and-build-enhancements

- **Plan ID:** P-1792243199-antigravity-agent-gemini-3.8-flash-medium
- **Parent Intent ID:** NONE
- **Agent:** antigravity-agent/gemini-3.8-flash-medium (version: 1.4.0)
- **Created At:** 2026-10-17T13:20:00.016Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** NONE
- **Exploration stance:** balanced with targeted protocol discovery for GitHub CLI PR commenting and review error
  handling, conventional commit title parsing, and ledger schema compliance under unattended pipeline flow.
- **Safety priority level:** elevated
- **Priority Justification:** Triggered by `docs/safety.md` §1 (Git as safety boundary: isolated branch execution), §2
  (Mandatory execution sandboxing), §4 (Entropy as safety signal: iteration budget bounding), §5 (Human review as final
  gate: Bean 0034 Human-Only PR Merging prohibition on autonomous merge to `main`), `holon-config/world/constraints.md`
  §1 (Git flow constraints: strict branch prefix isolation and commit boundaries), §2 (Sandbox containment tiers: zero
  credential leakage), §3 (Ledger immutability: zero modification policy for append-only archives), and
  `holon-config/world/ruleset.md` §1, §2, §3 (Python `==3.13.*`, monorepo workspace layout under
  `apps/sandbox-executor`, PEP 8, static type annotations, docstring integrity, pytest 9.1.1 hermetic test boundaries).
  Elevated safety ensures that multi-pass review loop actions strictly preserve human gating prior to any branch
  integration with `main`.

## Exploration

- **Proportion of steps that are exploratory:** 0.17
- **Justification:** Step 1 performs a focused informational audit of existing Makefile targets, GitHub CLI
  review/comment protocols, HTTP 422 error states, conventional PR title schemas, and ledger action entry schemas prior
  to modifying production code.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.35  |
| impact_pred         | 95.0  |
| cost_pred           | 10.8  |
| learning_value_pred | 5.2   |
| ev_pred             | 81.00 |

### Strategy Rationale

The overall plan metrics were derived from individual step-level metrics as follows:

- **p_success_pred**: 0.94. Governed by the critical bottleneck step (Step 4: 0.94), which implements GitHub PR
  consensus dual-posting via `gh pr comment` and handles GitHub API author self-approval restrictions (HTTP 422: Can not
  approve your own pull request) with graceful fallback to `--comment`. Network edge cases, rate limits, and GitHub CLI
  exit codes represent the primary failure surface.
- **entropy_pred**: 0.35. Evaluated as the maximum single-step risk profile (Step 4: 0.35), where external subprocess
  interactions and API failure fallback logic introduce behavioral entropy. The cumulative sum of predicted step
  entropies across all six steps is 1.25 (`0.20 + 0.10 + 0.25 + 0.35 + 0.20 + 0.15 = 1.25`), remaining comfortably
  within the allocated entropy budget of 15.0.
- **impact_pred**: 95.0. High architectural and developer experience impact fulfilling Beans 0116, 0113, 0119, and 0109
  across `holon-agentic-coder`: harmonising Makefile build targets, preserving auditability through granular review-loop
  action ledger records, maximising PR conversation visibility via dual-posted consensus comments, and standardising
  pull requests with conventional titles and structured markdown bodies.
- **cost_pred**: 10.8. Calculated as the direct sum of predicted step costs
  (`1.2 + 0.8 + 2.0 + 2.6 + 2.2 + 2.0 = 10.8`).
- **learning_value_pred**: 5.2. Epistemic gain delivered to the system through empirical discovery of GitHub CLI comment
  and review semantics, HTTP 422 author self-approval error recovery, semantic PR template generation, and ledger action
  provenance recording.
- **ev_pred**: 81.00. Derived using the config-driven Expected Value formula:
  `EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost`. With system coefficients
  `lambda = 0.3` and `mu = 0.5` loaded from `holon-config/metrics/ev_config.json`:
  `EV = 0.94 * 95.0 + 0.5 * 5.2 - 0.3 * 0.35 - 10.8 = 89.30 + 2.60 - 0.105 - 10.8 = 80.995 -> 81.00`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints: strict prefix isolation, commit boundaries
    scoped to active step branch, mandatory rebase discipline).
  - `holon-config/world/constraints.md` §2 (Sandbox Containment Tiers: container isolation, process safety, zero
    credential leakage).
  - `holon-config/world/constraints.md` §3 (Ledger Immutability: append-only policy for
    `holon-knowledge/ledger/executions.jsonl`).
  - `holon-config/world/ruleset.md` §1 (Runtime & Environment Specification: Python `==3.13.*`, uv workspace management
    under `apps/sandbox-executor`).
  - `holon-config/world/ruleset.md` §2 (Coding Conventions & Standards: PEP 8 compliance, strict static type hints,
    docstring preservation).
  - `holon-config/world/ruleset.md` §3 (Testing Constraints: pytest 9.1.1, test hermeticity, preserve test boundaries in
    `apps/sandbox-executor/tests/`).
  - `docs/safety.md` §1 & §5 (Git as safety boundary, Human review as final gate: Bean 0034 Human-Only PR Merging rule
    requiring unconditional halt at consensus approval without merging to `main`).
  - Intent Q1 Answer: The alias for `build-images` in Makefile is `build-image: build-images`, added to `.PHONY` and
    documented in `make help`.
  - Intent Q2 Answer: PR review consensus is dual-posted: always post top-level discussion comment via `gh pr comment`,
    plus official review via `gh pr review`.
- **Potential violations or edge cases:**
  - Attempting to autonomously merge pull requests to `main` upon consensus approval, directly violating Bean 0034 and
    `docs/safety.md` §5.
  - Crashing the review stage when `gh pr review --approve` fails with HTTP 422 because the PR author cannot approve
    their own PR.
  - Omitting the top-level PR conversation comment, reducing visibility of automated review findings in GitHub PR
    discussion tabs.
  - Corrupting or rewriting existing lines in `executions.jsonl`, violating ledger immutability constraints.
  - Generating malformed conventional commit PR titles or omitting referenced Bean IDs from PR descriptions.
- **Mitigations built into the plan:**
  - Strict preservation of `StageStatus.HALTED_FOR_HUMAN` upon review consensus approval, emitting manual approval
    commands without merging.
  - Robust exception handling and regex/substring inspection on `gh pr review` stderr (detecting `422` or
    `Can not approve your own pull request`) with immediate graceful fallback to `gh pr review --comment`.
  - Unconditional dual-posting: executing `gh pr comment` for discussion thread visibility in addition to the structured
    `gh pr review` submission.
  - Utilizing `atomic_ledger_append` to guarantee append-only atomicity for all discrete review actions, findings, and
    resolver events.
  - Deterministic formatting of semantic PR titles (`feat(...)`, `fix(...)`) and structured markdown bodies with
    explicit Bean cross-references.
- **Residual risk accepted (and why):**
  - Dependency on GitHub CLI availability and active authentication token in live CI/CD environments; accepted because
    the engine implements graceful offline fallbacks and error logging when `gh` is unavailable or running in hermetic
    test sandboxes.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 1.25
- **Budget Compliance:** The strategy fits within budget

## Plan Description & Strategy

This plan implements developer experience and flow orchestration enhancements across `holon-agentic-coder` addressing
Beans 0116, 0113, 0119, and 0109:

1. **Makefile Alignment (Bean 0116):**
   - Add `build-image: build-images` alias to `Makefile`.
   - Add `build-image` to `.PHONY`.
   - Update `help` target in `Makefile` to document `build-image`.
   - Update `apps/sandbox-executor/tests/test_makefile.py` to assert `build-image` presence in `make help` and verify
     alias dispatch.

2. **Semantic PR Generation (Bean 0109):**
   - In `apps/sandbox-executor/src/sandbox_executor/flow.py`, implement semantic PR title and structured description
     generation from intent and plan metadata.
   - Format conventional PR titles (e.g., `feat(slug): ...`, `fix(slug): ...`) based on intent `category`, `slug`, and
     `title`.
   - Construct rich markdown PR bodies containing Summary, Changes, and explicit Bean cross-references (e.g.,
     `Closes Bean 0116, 0113, 0119, 0109`).

3. **PR Review Consensus Dual-Posting & Self-Approval Fallback (Bean 0119):**
   - In `apps/sandbox-executor/src/sandbox_executor/flow.py`, when posting consensus reviews to GitHub, always execute
     `gh pr comment` so the report is visible in the main PR conversation tab.
   - Execute `gh pr review` with `--approve` on consensus approval; when GitHub returns HTTP 422
     (`Can not approve your own pull request`), gracefully catch the error and fall back to `gh pr review --comment`.
   - Retain strict enforcement of Bean 0034 (Human-Only PR Merging) by halting at consensus approval with status
     `HALTED_FOR_HUMAN`.

4. **Granular Ledger Action Recording (Bean 0113):**
   - In `apps/sandbox-executor/src/sandbox_executor/flow.py`, ensure all discrete review-loop actions (iteration
     evaluations, individual findings detected, and resolver iteration events) append structured action records to
     `holon-knowledge/ledger/executions.jsonl`.
   - Ensure action records contain standard fields: `action_id`, `execution_id`, `action`, `category`, `target`,
     `outcome`, `entry_type`, `action_origin`, `review_iteration`, `review_finding_ids`, `review_severity`, `labels`,
     and `created_at`.

5. **Verification & Quality Gates:**
   - Update unit test suites in `apps/sandbox-executor/tests/test_flow.py`, `test_cli.py`, and `test_makefile.py`.
   - Verify all tests pass hermetically:
     `uv run pytest apps/sandbox-executor/tests -m 'not integration_test and not stress'`.
   - Run `make lint` across python, shell, markdown, and json.

---

## Step 1: Audit Makefile targets, GitHub CLI PR commenting and review error models, and ledger schema specifications (Beans 0116, 0113, 0119, 0109 Discovery)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Informational audit and reconnaissance step inspecting existing Makefile definitions, GitHub CLI
  behavior, and ledger action schemas without altering production code.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The current Stage 4 review loop and PR handling in `flow.py` can be extended with
  dual-posting, HTTP 422 author self-approval fallbacks, semantic PR titles, and granular action logging without
  breaking existing single-pass review contracts or ledger reconciliation logic.
- **Learning target:** Exact command line signatures for `gh pr comment` and `gh pr review`, exact error patterns for
  GitHub HTTP 422 self-approval rejections, conventional title mappings from intent categories, and action ledger
  schemas for discrete review actions.
- **Maximum acceptable cost for this learning:** 1.5 cost units.

### Intent & Git Integration

- **Step Intent:** Audit existing Makefile targets in `Makefile`, inspect GitHub CLI review methods in `flow.py`, and
  analyze action record schemas in `holon-knowledge/ledger/executions.jsonl`.
- **Git branch:**
  `I-1792242950-flow-review-pr-and-build-enhancements/P-1792243199-antigravity-agent-gemini-3.8-flash-medium/E-1792243201-audit-pr-and-build-specs/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Audit `Makefile` target conventions:
   - Inspect `.PHONY` declarations, `build-images` implementation, and `help` target formatting.
   - Verify alignment with `apps/sandbox-executor/build_all_images.sh`.
   - Check `apps/sandbox-executor/tests/test_makefile.py` assertions and `PREREQUISITE_HELP_LIST` patterns.
2. Audit PR creation and review mechanisms:
   - Check `gh pr comment` argument conventions: `gh pr comment <target> --body <text>`.
   - Check `gh pr review` argument conventions: `gh pr review <target> --approve --body <text>` and
     `gh pr review <target> --comment --body <text>`.
   - Confirm exact error strings emitted by GitHub CLI on author self-approval: HTTP 422 with message "Can not approve
     your own pull request" or "GraphQL: Can not approve your own pull request".
3. Audit semantic PR title and description structure:
   - Define category to conventional prefix mapping: `feature` -> `feat`, `fix` -> `fix`, `refactor` -> `refactor`,
     `documentation` -> `docs`, `chore` -> `chore`, `test` -> `test`.
   - Define markdown description format including Summary, Changes, and referenced Bean numbers.
4. Audit ledger action record schemas in `executions.jsonl`:
   - Inspect fields used by existing action records (`action_id`, `execution_id`, `action`, `category`, `target`,
     `outcome`, `entry_type`, `action_origin`, `review_iteration`, `review_finding_ids`, `review_severity`, `labels`,
     `created_at`).
   - Define schemas for discrete evaluation actions, finding detections, and resolver iterations.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §1 (Git boundary), §4 (Entropy bounding), §5 (Human review boundary: Bean 0034),
  `holon-config/world/ruleset.md` §2.
- **Potential failure modes for this step:** Inaccurate assumptions regarding `gh` CLI error output formats or incorrect
  ledger field naming.
- **Guardrails and early‑abort checks:** Validate `gh` command syntax against GitHub CLI specifications; verify ledger
  schemas against `holon-knowledge/ledger/executions.jsonl`.

### Success & Discard Criteria

- **Success:** Complete design specifications finalized for Makefile alias, semantic PR metadata generation,
  dual-posting review consensus, self-approval error recovery, and granular action logging.
- **Discard:** Discard if requirements conflict with Bean 0034 (Human-Only PR Merging) or ledger immutability
  constraints.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.20  |
| impact_pred         | 80.0  |
| cost_pred           | 1.2   |
| learning_value_pred | 4.2   |
| ev_pred             | 79.24 |

### Step Metrics Rationale

High probability (`0.98`) and low entropy (`0.20`) for a read-only audit. Cost is minimal (`1.2`), and learning value is
high (`4.2`) as it clarifies GitHub CLI error recovery dynamics and ledger schemas. Expected value derivation:
`EV = 0.98 * 80.0 + 0.5 * 4.2 - 0.3 * 0.20 - 1.2 = 78.40 + 2.10 - 0.06 - 1.2 = 79.24`.

---

## Step 2: Harmonise Makefile targets with build-image alias and update help target (Bean 0116)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Straightforward, localized configuration update in `Makefile` and corresponding test assertion update
  in `test_makefile.py`.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add `build-image` alias pointing to `build-images` in `Makefile`, add `build-image` to `.PHONY`,
  update `help` target documentation, and update `test_makefile.py`.
- **Git branch:**
  `I-1792242950-flow-review-pr-and-build-enhancements/P-1792243199-antigravity-agent-gemini-3.8-flash-medium/E-1792243202-makefile-build-image-alias/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In root `Makefile`:
   - Add `build-image` to the `.PHONY` target declaration on line 56:
     `.PHONY: help build-images build-image check-prerequisites ...`
   - In the `help` target, add documentation entry for `build-image`:
     `@printf "  %-22s %s\n" "build-image" "Alias for build-images: Build all sandbox Docker images."`
   - Add the target definition immediately adjacent to `build-images`: `build-image: build-images`
2. In `apps/sandbox-executor/tests/test_makefile.py`:
   - Update `test_makefile_help_lists_core_targets` to include `"build-image"` in `expected_targets`.
   - Add a unit test `test_makefile_build_image_alias` verifying that running `make -n build-image` targets
     `build_all_images.sh` identically to `make -n build-images`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §1 (Monorepo root conventions), Intent Q1 Answer
  (`build-image: build-images`, added to `.PHONY` and documented in `make help`).
- **Potential failure modes for this step:** Syntax error or tab/space indentation errors in `Makefile` breaking GNU
  Make execution.
- **Guardrails and early‑abort checks:** Use strict tab indentation for recipe lines; verify with `make help` and
  `make -n build-image`.

### Success & Discard Criteria

- **Success:** `make help` displays both `build-images` and `build-image`, `make -n build-image` succeeds cleanly, and
  `test_makefile.py` passes.
- **Discard:** Discard if `Makefile` syntax fails or causes regressions across other core targets.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.99  |
| entropy_pred        | 0.10  |
| impact_pred         | 82.0  |
| cost_pred           | 0.8   |
| learning_value_pred | 1.5   |
| ev_pred             | 81.10 |

### Step Metrics Rationale

Extremely high probability (`0.99`) and minimal entropy (`0.10`) for a standard GNU Make alias. Cost is very low
(`0.8`), and learning value is moderate (`1.5`). Expected value derivation:
`EV = 0.99 * 82.0 + 0.5 * 1.5 - 0.3 * 0.10 - 0.8 = 81.18 + 0.75 - 0.03 - 0.8 = 81.10`.

---

## Step 3: Implement semantic PR title and structured description generation from intent/plan metadata (Bean 0109)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Modular logic in `sandbox_executor/flow.py` transforming structured intent and plan metadata into
  conventional PR titles and markdown bodies.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add `generate_semantic_pr_metadata` helper in `flow.py` to produce conventional PR titles and
  structured markdown bodies referencing Bean IDs.
- **Git branch:**
  `I-1792242950-flow-review-pr-and-build-enhancements/P-1792243199-antigravity-agent-gemini-3.8-flash-medium/E-1792243203-semantic-pr-generation/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Implement
   `generate_semantic_pr_metadata(intent_data: dict[str, Any], plan_metadata: dict[str, Any] | None = None) -> tuple[str, str]`
   in `apps/sandbox-executor/src/sandbox_executor/flow.py`:
   - Title generation logic:
     - Extract `category` (e.g., `feature`, `fix`, `refactor`, `chore`, `docs`, `test`), defaulting to `feat`.
     - Map category to conventional prefix: `feature` -> `feat`, `fix` -> `fix`, `bugfix` -> `fix`, `refactoring` ->
       `refactor`, `refactor` -> `refactor`, `documentation` -> `docs`, `chore` -> `chore`, `test` -> `test`.
     - Extract scope: use primary label or simplified slug component (e.g. `flow`, `review-loop`, `makefile`).
     - Extract title/description: clean summary string from intent `title` or `description`.
     - Combine into standard title: `<prefix>(<scope>): <concise_title>` (truncated to <= 100 characters).
   - Description generation logic:
     - Construct rich markdown body containing:
       - `## Summary`: intent description and goal.
       - `## Key Changes`: bulleted breakdown of components addressed.
       - `## Referenced Beans`: list of beans from `intent_data.get("beans", [])` formatted as `Closes Bean <ID>` or
         `Beans: <ID>, <ID>`.
       - `## Plan & Validation Metadata`: Plan ID, Expected Value (EV), Agent, Model.
2. Integrate with PR creation logic:
   - When creating or verifying a PR via GitHub CLI (`gh pr create`), use the generated semantic title and structured
     description.
   - If PR already exists, allow updating PR title and body via `gh pr edit` if metadata changed.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (Docstrings, static typing, PEP 8),
  `docs/intent/intent_markdown_spec.md`.
- **Potential failure modes for this step:** Missing or malformed intent metadata fields (e.g. missing `category` or
  `beans`) leading to `KeyError` or generic titles.
- **Guardrails and early‑abort checks:** Provide robust default fallbacks for all metadata fields (`category="feat"`,
  `scope="core"`, `beans=[]`).

### Success & Discard Criteria

- **Success:** `generate_semantic_pr_metadata` produces valid conventional commit titles and rich markdown descriptions
  containing summary, changes, and Bean cross-references for any valid intent payload.
- **Discard:** Discard if generated PR titles violate Conventional Commits standard or omit Bean references.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.25  |
| impact_pred         | 90.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 85.83 |

### Step Metrics Rationale

High probability (`0.96`) and low entropy (`0.25`) for deterministic text formatting. Cost is modest (`2.0`), and
learning value is solid (`3.0`). Expected value derivation:
`EV = 0.96 * 90.0 + 0.5 * 3.0 - 0.3 * 0.25 - 2.0 = 86.40 + 1.50 - 0.075 - 2.0 = 85.825 -> 85.83`.

---

## Step 4: Implement dual-posting PR review consensus and author self-approval HTTP 422 fallback (Bean 0119)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Focused core engine enhancement in `flow.py` handling GitHub CLI PR review and discussion commenting
  with robust error interception.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Catching GitHub CLI exit code 1 with stderr containing HTTP 422 ("Can not approve your
  own pull request") allows graceful fallback to `gh pr review --comment` while maintaining full review report delivery
  and pipeline integrity.
- **Learning target:** Behavior of `gh pr comment` vs `gh pr review` under various GitHub authorization contexts (bot
  token as PR author vs external reviewer).
- **Maximum acceptable cost for this learning:** 3.0 cost units.

### Intent & Git Integration

- **Step Intent:** In `flow.py`, implement `post_pr_review_consensus` to execute dual-posting (`gh pr comment` for
  conversation visibility + `gh pr review` for official submission) and handle HTTP 422 self-approval restrictions
  gracefully.
- **Git branch:**
  `I-1792242950-flow-review-pr-and-build-enhancements/P-1792243199-antigravity-agent-gemini-3.8-flash-medium/E-1792243204-dual-post-pr-review-consensus/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Implement
   `post_pr_review_consensus(repo_dir: str, target: str, consensus_data: dict[str, Any], proxy_env: dict[str, str] | None = None) -> dict[str, Any]`
   in `flow.py`:
   - Generate formatted markdown review consensus report:
     - Header with consensus verdict (`APPROVED`, `CHANGES_REQUESTED`, `HALTED_FOR_HUMAN`).
     - Iterations completed, CI check status summary, mergeability state, and trailers.
     - Notice enforcing Bean 0034: Human-Only PR Merging (manual merge required).
   - Action 1 (Top-Level Discussion Comment for conversation tab visibility):
     - Execute `gh pr comment <target> --body <report_text>`.
     - Record outcome and timestamp. If `gh pr comment` fails, log advisory warning but proceed to official review.
   - Action 2 (Official Review Submission):
     - Determine review action flag:
       - If consensus approved: start with `--approve`.
       - If consensus rejected or findings detected: `--comment` (or `--request-changes`).
     - Execute `gh pr review <target> <flag> --body <report_text>`.
     - Intercept GitHub API author self-approval restriction:
       - If command fails with HTTP 422 or stderr contains `Can not approve your own pull request` /
         `PullRequestReviewAuthorError`:
         - Log informational message: "Author self-approval restriction encountered (HTTP 422). Falling back to official
           review comment."
         - Re-execute with `gh pr review <target> --comment --body <report_text>`.
   - Return diagnostic dictionary detailing discussion comment status, official review status, and self-approval
     fallback trigger.
2. Integrate `post_pr_review_consensus` into `run_review_loop` and `run_review_stage`:
   - Invoke dual-posting when target resolves to an active PR or branch with remote PR.
   - Ensure Bean 0034 safety halt remains strictly active regardless of GitHub API response.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 3
- **Is Bottleneck:** YES (Critical flow logic; failure in GitHub posting or error handling blocks Stage 4 completion)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §5 (Human review boundary: Bean 0034), Intent Q2 Answer (Dual-posted: always post
  top-level discussion comment via `gh pr comment`, plus official review via `gh pr review`).
- **Potential failure modes for this step:** Unhandled GitHub API errors crashing the pipeline, or infinite fallback
  loops.
- **Guardrails and early‑abort checks:** Fallback attempts strictly capped to one retry; non-zero exit codes outside 422
  captured gracefully without aborting local pipeline state.

### Success & Discard Criteria

- **Success:** Top-level discussion comment is always posted via `gh pr comment`, official review is posted via
  `gh pr review`, and HTTP 422 self-approval errors seamlessly fall back to `--comment` without raising unhandled
  exceptions.
- **Discard:** Discard if implementation fails to fall back on HTTP 422 or violates Bean 0034 by initiating a PR merge.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.94  |
| entropy_pred        | 0.35  |
| impact_pred         | 94.0  |
| cost_pred           | 2.6   |
| learning_value_pred | 4.5   |
| ev_pred             | 87.91 |

### Step Metrics Rationale

Bottleneck step with probability (`0.94`) and entropy (`0.35`) reflecting external API interactions and HTTP 422
branching. Cost is `2.6`, and learning value is high (`4.5`) for establishing resilient GitHub review workflows.
Expected value derivation:
`EV = 0.94 * 94.0 + 0.5 * 4.5 - 0.3 * 0.35 - 2.6 = 88.36 + 2.25 - 0.105 - 2.6 = 87.905 -> 87.91`.

---

## Step 5: Append granular review-loop evaluations, findings, and resolver events to executions.jsonl (Bean 0113)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Modular ledger logging enhancement in `flow.py` ensuring complete provenance and auditability of review
  loop sub-actions.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Ensure all discrete review-loop actions (evaluations, findings detected, resolver iterations, and PR
  review postings) append structured action records to `holon-knowledge/ledger/executions.jsonl`.
- **Git branch:**
  `I-1792242950-flow-review-pr-and-build-enhancements/P-1792243199-antigravity-agent-gemini-3.8-flash-medium/E-1792243205-record-granular-review-actions/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor/src/sandbox_executor/flow.py` within `run_review_loop`:
   - Discrete Action 1: Iteration Evaluation Event
     - Append action record with `action="PR Review Loop iteration N evaluation for <target>"`, `entry_type="action"`,
       `action_origin="pr_review"`, `review_iteration=N`, `outcome="success"|"failure"`.
   - Discrete Action 2: Finding Detection Events
     - If `finding_ids` are detected during an iteration (e.g. `FIND-DIRTY`, `FIND-CI`), append dedicated action record:
       `action="PR Review Loop iteration N findings detected: <finding_ids>"`, `category="review_finding"`,
       `review_finding_ids=finding_ids`, `review_severity=severity`, `outcome="failure"`.
   - Discrete Action 3: Resolver Iteration Events
     - If automated resolution or re-evaluation is triggered across loop iterations, append:
       `action="PR Review Loop iteration N resolver triggered"`, `category="resolver"`, `review_iteration=N`,
       `outcome="running"|"success"`.
   - Discrete Action 4: PR Review Posting Event
     - Upon posting review consensus to GitHub (from Step 4), append:
       `action="PR Review consensus dual-posted to GitHub for <target>"`, `category="github_pr"`, `outcome="success"`,
       `labels=["review", "github", "dual-post"]`.
2. Guard all ledger write operations:
   - Use `atomic_ledger_append` to guarantee append-only atomicity and crash resilience.
   - Respect `dry_run` flag: skip file mutations during dry-run executions while preserving in-memory telemetry.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (Ledger Immutability: zero modification of existing rows),
  `docs/ledger_schema.md`.
- **Potential failure modes for this step:** Schema divergence or formatting invalid JSON strings corrupting ledger
  parsing.
- **Guardrails and early‑abort checks:** Validate each record with `json.dumps()` prior to appending; verify with
  `reconcile_ledger_file` compatibility.

### Success & Discard Criteria

- **Success:** Every review loop pass generates discrete, parseable action records in `executions.jsonl` detailing
  evaluations, findings, and resolver events.
- **Discard:** Discard if ledger writes mutate historical records or violate append-only constraints.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.20  |
| impact_pred         | 92.0  |
| cost_pred           | 2.2   |
| learning_value_pred | 3.5   |
| ev_pred             | 87.81 |

### Step Metrics Rationale

High probability (`0.96`) and low entropy (`0.20`) with established `atomic_ledger_append` patterns. Cost is `2.2`, and
learning value is `3.5`. Expected value derivation:
`EV = 0.96 * 92.0 + 0.5 * 3.5 - 0.3 * 0.20 - 2.2 = 88.32 + 1.75 - 0.06 - 2.2 = 87.81`.

---

## Step 6: Comprehensive verification, unit test additions in test_flow.py, test_cli.py, test_makefile.py, and lint suite pass (Verification & Quality Gates)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Hermetic test suite expansion and validation across all modified modules without external side effects.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Add thorough unit test coverage in `test_flow.py`, `test_cli.py`, and `test_makefile.py` for all new
  features, and verify `make lint` and pytest pass cleanly.
- **Git branch:**
  `I-1792242950-flow-review-pr-and-build-enhancements/P-1792243199-antigravity-agent-gemini-3.8-flash-medium/E-1792243206-verification-and-tests/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Update `apps/sandbox-executor/tests/test_flow.py`:
   - Add tests for `generate_semantic_pr_metadata`:
     - Test category mapping (`feature` -> `feat`, `fix` -> `fix`, `refactor` -> `refactor`, `chore` -> `chore`).
     - Test structured markdown body generation including Summary, Changes, and Bean references.
     - Test fallback handling for missing metadata.
   - Add tests for `post_pr_review_consensus`:
     - Test dual-posting: mock `gh pr comment` and `gh pr review --approve` calls and verify both are invoked.
     - Test author self-approval restriction fallback: mock `gh pr review --approve` returning exit code 1 with stderr
       containing `422: Can not approve your own pull request`, and verify fallback invocation of
       `gh pr review --comment`.
     - Test comment failure resilience: verify `gh pr review` still proceeds if `gh pr comment` encounters non-fatal
       failure.
   - Add tests for granular ledger action appending in `run_review_loop`:
     - Verify `executions.jsonl` receives discrete evaluation records, finding detection records when dirty/CI fails,
       and dual-posting action records.
2. Update `apps/sandbox-executor/tests/test_cli.py`:
   - Verify CLI subcommands and aliases (`review-loop`, `review`, `pr-review-loop`) dispatch correctly with new flow
     parameters.
3. Update `apps/sandbox-executor/tests/test_makefile.py`:
   - Assert `build-image` target is present in `make help` output.
   - Assert `build-image` alias delegates to `build-images`.
4. Run full validation suite:
   - Run `uv run pytest apps/sandbox-executor/tests -m 'not integration_test and not stress'`.
   - Run `make lint` (covering python, shell, markdown, json/yaml).

### Dependencies & Criticality

- **Depends on:** Step 2, Step 3, Step 4, Step 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (pytest 9.1.1, test hermeticity),
  `apps/sandbox-executor/docs/hermetic_testing.md`.
- **Potential failure modes for this step:** Flaky subprocess mocking or linting formatting discrepancies.
- **Guardrails and early‑abort checks:** Use strict mocks for all subprocess and file operations; run Prettier before
  final test assertions.

### Success & Discard Criteria

- **Success:** 100% of unit tests in `test_flow.py`, `test_cli.py`, and `test_makefile.py` pass cleanly, and `make lint`
  reports zero errors.
- **Discard:** Discard if any regressions are detected in existing flow or review loop tests.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.15  |
| impact_pred         | 90.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.5   |
| ev_pred             | 86.51 |

### Step Metrics Rationale

High probability (`0.97`) and low entropy (`0.15`) for hermetic unit testing. Cost is `2.0`, and learning value is
`2.5`. Expected value derivation:
`EV = 0.97 * 90.0 + 0.5 * 2.5 - 0.3 * 0.15 - 2.0 = 87.30 + 1.25 - 0.045 - 2.0 = 86.505 -> 86.51`.
