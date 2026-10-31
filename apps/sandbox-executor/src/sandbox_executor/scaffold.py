"""Project scaffolding logic for `holon init`.

Automates onboarding and scaffolding of new or existing projects into the Holon ecosystem.
Creates holon-config/, holon-knowledge/, and configures .gitignore with idempotency guarantees.
"""

import json
import os
import sys

RULESET_PYTHON = """# Holon World Ruleset

This document defines the static coding conventions, runtime constraints, package specifications,
and architectural rules for this repository. All planning and execution agents must consult and obey these rules.

---

## 1. Runtime & Environment Specification

- **Python Runtime:** Strict target version `==3.13.*`.
- **Package Management:** Managed using `uv` workspace commands. No manual modification of virtualenv packages;
  all dependencies must be declared in `pyproject.toml`.
- **Project Layout:** Monorepo / Package structure:
  - Root project matches metadata constraints.
  - Sub-packages isolated or organised under `src/` or `apps/`.

---

## 2. Coding Conventions & Standards

- **PEP 8 Compliance:** All Python source files must conform to standard style rules (spacing, naming, imports layout).
- **Typing Guidelines:** Use strict static type annotations (`typing` module) for all public functions, classes,
  and method signatures.
- **Docstring Requirements:** Every module, class, and public function must have an explanatory docstring outlining
  inputs, outputs, exceptions, and side-effects.
- **Imports Discipline:**
  - Avoid wildcard imports (`from module import *`).
  - Keep core domain models independent of framework-specific classes.

---

## 3. Testing Constraints

- **Testing Tool:** `uv run pytest` as standard test runner.
- **Test Location:** Unit tests must be placed in a corresponding `tests/` directory matching
  the source file being tested.
- **Execution Boundary:** Sandbox executions are not allowed to modify tests or test assertions unless explicitly
  declared in the plan.
"""

RULESET_GENERIC = """# Holon World Ruleset

This document defines the static coding conventions, runtime constraints, package specifications,
and architectural rules for this repository. All planning and execution agents must consult and obey these rules.

---

## 1. Runtime & Environment Specification

- **Project Management:** All dependencies must be explicitly declared in project configuration files.
- **Layout:** Standard project structure with clear separation between source and tests.

---

## 2. Coding Conventions & Standards

- **Code Quality:** Maintain clean, readable, modular code adhering to language-idiomatic standards.
- **Documentation:** Document public interfaces, configuration options, and architectural decisions.

---

## 3. Testing Constraints

- **Automated Testing:** All features and bug fixes must include automated test coverage.
- **Execution Boundary:** Sandbox executions must verify changes against the project test suite.
"""

CONSTRAINTS_MD = """# Holon World Constraints

This document defines the strict security, operational, and lifecycle constraints for the Holon Agentic Coder
environment. Violating these constraints triggers automated alerts, sandbox terminations, or trust degradation.

---

## 1. Git Flow & Branch Constraints

- **Prefix-Based Isolation:** Every active task must run inside its own isolated branch prefix:
  - **Root Intents:** `I-{timestamp}-{slug}/_`. Only human operators can approve merges to canonical branches.
  - **Plan Variants:** `I-{timestamp}-{slug}/P-{timestamp}-{agent}-{model}/_`. Created off of the intent branch.
  - **Execution Runs:** `I-{timestamp}-{slug}/P-{timestamp}-{agent}-{model}/E-{timestamp}-{action}/_`.
    Created off of the plan branch.
- **Rebase Discipline:** Before any execution run or branch merge, the working branch must be rebased from its parent
  branch to prevent divergence.
- **Commit Boundaries:** Code changes must be scoped strictly to the current active step and git branch.
  Agents are prohibited from checking out or committing directly to `main` or other intents' branches.

---

## 2. Sandbox Containment Tiers

All execution and validation activities must run inside an isolated sandbox determined by predicted entropy and trust:

1. **Process Sandbox:** (Low entropy $< 10$, high trust). Local Python sub-process with filesystem containment
   and disabled network access.
2. **Container Sandbox (Docker):** (Medium entropy $10 - 30$, standard trust). Docker container with resource
   constraints and network isolation.
3. **VM Sandbox:** (High entropy $> 30$, low/baseline trust). Highly secure VM with kernel-level isolation
   and rollback support.

---

## 3. Ledger Immutability

- **Append-Only Ledger:** The ledgers located at `holon-knowledge/ledger/` (`intents.jsonl`, `plans.jsonl`,
  `executions.jsonl`) are immutable.
- **Zero Modification Policy:** Removing, rewriting, amending, or editing historical lines in `.jsonl` files
  is strictly prohibited. Only append operations are allowed.

---

## 4. Model Routing Constraints

- **Exploration vs. Safety:** Routing is dictated by predicted plan entropy and novelty:
  - **Flash Models:** Recommended for routine tasks, boilerplate implementations, and simple tests.
  - **Deep Models:** Mandated for refactoring core modules, security-sensitive plans, or intents exceeding high entropy.
"""

METRICS_README = """# Holon Metrics Configuration

This directory contains configuration files for Holon metrics physics, entropy calculations,
and expected value (EV) formulas.
"""

ENTROPY_CONFIG = {
    "_note": "Reserved for future entropy computation integration. Not yet loaded by the runtime.",
    "weights": {
        "w1_ssa": 0.3,
        "w2_irr": 0.25,
        "w3_cl": 0.2,
        "w4_ser": 0.15,
        "w5_nov": 0.1,
    },
    "observable_weights": {
        "u1_ssa": 0.3,
        "u2_irr": 0.25,
        "u3_cl": 0.2,
        "u4_ser": 0.15,
        "u5_nov": 0.1,
    },
    "description": "Per-intent entropy risk and complexity weight factors",
}

EV_CONFIG = {
    "lambda": 0.3,
    "mu": 0.5,
    "description": "System-wide EV calculation penalty and learning weight constants",
}

SYSTEM_ENTROPY_CONFIG = {
    "_note": "Reserved for future entropy computation integration. Not yet loaded by the runtime.",
    "coefficients": {
        "alpha_bd": 1.0,
        "beta_kf": 1.0,
        "gamma_cd": 1.0,
        "delta_atv": 1.0,
        "epsilon_uc": 1.0,
    },
    "calibration_status": "uncalibrated_initial_defaults",
    "description": "System-level entropy coefficients across repository and agent swarm",
}

PLANNER_TEMPLATE = """You are an autonomous planning agent. Your 'free will' applies to how you choose the strategy
for decomposing an intent, balancing entropy, cost, impact, learning value, and expected value within the project
unique world ruleset and constraints. You are encouraged to explore low-probability actions where they provide
learning value to the system. You may propose amending, deferring, or decomposing an intent into prerequisite
sub-intents if it is impossible or very low value relative to cost, and explicitly justify that in the plan.

**Autonomy Rules:**

1. You may REFRAME the intent if you identify a path with significantly higher Expected Value (EV).
2. You may REJECT or DEFER the intent if it violates the world ruleset, constraints, or has a negative EV.
3. You are encouraged to include high-entropy, exploratory steps if the potential learning value justifies the cost.

Based on the following intent:

```
{intent_json}
```

and the current state of the project give a detailed plan. Enclose all arithmetic formulas and derivations in
backtick code spans to maintain single-pass prettier convergence.

**Write Protocol:** create `plans/P-{timestamp}-{agent}-{safe_model}.md` with your file-write tool immediately, write
the header and the `## Overall Plan Metrics` table first, append each step as it is finished, and keep your final chat
message to one short confirmation line.
"""

EXECUTOR_TEMPLATE = """You are an autonomous execution agent operating directly inside the repository workspace at
`{repo_dir}`. Your task is to implement the plan `{plan_id}` for intent `{intent_id}` by directly writing, modifying,
creating, and deleting codebase files.

**Critical Execution Directives:**

1. You must implement all changes specified in the plan by directly writing and modifying the files in this repository.
2. Do NOT merely summarise or discuss the plan in text—you MUST modify the actual files on the filesystem using your
   file creation and editing tools.
3. Run the relevant test suite using your terminal tools to verify your implementation and ensure all tests pass.
4. Ensure code formatting, linting, and repo conventions are followed.
5. All generated or updated markdown documentation and execution records must use code spans (backticks) for formulas,
   arithmetic expressions, and metric notations (e.g., `EV = 0.90 * 95.0`) to avoid CommonMark emphasis parsing
   oscillations and maintain immediate single-pass prettier compatibility.
6. Create `{notes_path}` with your file-write tool before changing code, starting with a `## Summary` section and an
   `## Actual Metrics` table, append to `## Steps Executed` as each step completes, finish with `## Tests Run`, and keep
   your final chat message to one short confirmation line.

**Plan Content:** {plan_content}

**Intent Metadata:**

```json
{intent_json}
```
"""

REVIEW_LOOP_TEMPLATE = """# Holon Review Loop Agent Instructions

You are the autonomous **Holon Review Loop Agent** operating inside `{repo_dir}` for target PR/branch `{target}` against
upstream branch `{target_branch}`. Your purpose is to execute the Stage 4 Review & Resolve Loop, adhering strictly to
the ensemble consensus model, the `pr-review-loop` architecture, and the multi-role review persona registry.

---

## 📐 Core Architecture & Loop Principles

1. **Autonomous Feedback Loop**: Iteratively review code changes and resolve flagged findings until unanimous consensus
   approval is achieved or the maximum iteration budget (`{max_iterations}`) is reached.
2. **Context Isolation**: Each review pass and resolution pass runs in a newly spawned subagent with fresh context to
   prevent prompt degradation across multiple iterations.
3. **Termination Safety & Consensus Integrity**: Consensus approval (`APPROVED`) requires:
   - Zero 🔴 **CRITICAL / BLOCKER** findings.
   - Zero 🟡 **IMPORTANT / IMPROVEMENT** findings.
   - Zero open actionable 🟢 **NIT / OPTIONAL** findings.
   - All automated CI checks (`gh pr checks` or local test suite) passing cleanly (`100%` pass rate, exit code `0`).
4. **Strict Nit Policy (Nits are actioned, not waved through)**: Every Nit / Optional 🟢 finding is adjudicated by the
   resolver exactly like a Critical or Important one and ends in one of two dispositions:
   - `applied`: Fix implemented, verified, and committed.
   - `rejected`: Explicit technical justification (e.g. not diff-grounded, inaccurate, out of scope, or conflicting with
     requirements). An unadjudicated Nit is an open blocker that prevents approval.
   - If the same Nit-only set survives 3 consecutive iterations with no diff change, adjudicate it
     `rejected: no-convergence` and proceed rather than burning iteration budgets.
5. **Deterministic Convergence (Never Pause)**: The loop has no pause state and never asks for guidance mid-flight. The
   only exits are a clean consensus approval posted to the PR, or the max iteration cap (`{max_iterations}`) with a
   final review posted. If identical issues persist across iterations, climb the Convergence Escalation Ladder: re-sync,
   re-adjudicate against primary sources, apply union fixes, or defer out-of-scope items to a dedicated bean.
6. **Drift Self-Healing**: Out-of-sync state (dirty tree, newly pushed commits, pending CI) is work, not a blocker.
   Re-sync and re-verify before every review pass.
7. **Human-Only PR Merging Boundary (Bean 0034 Enforced)**: You must **NEVER** execute `gh pr merge`, enable auto-merge,
   or add the PR to a merge queue. Merging is strictly reserved for the human maintainer. Upon achieving unanimous
   consensus approval, halt the pipeline and request manual human review.
8. **Namespaced Artifacts**: Every review artifact written to `.subagent/` must be scoped by repository and pull
   request: `.subagent/<repo>_pr<N>_<purpose>_iter_<iteration>_{sha}.<ext>`.
9. **Runtime Attrition Absorption**: Single-pass failures or timeouts must never abort the entire loop. Relaunch or
   salvage partial reports where possible.
10. **State Persistence**: Maintain `.subagent/<repo>_pr<N>_state.json` and append discrete action records to
    `holon-knowledge/ledger/executions.jsonl` through `sandbox_executor.flow.record_review_action()` (or the native
    `holon review-loop`; callers without a `FlowContext` use the context-free
    `sandbox_executor.ledger.try_append_action_record(..., preserve_shape=True)`), never by writing to the file
    directly.

---

## 👥 Comprehensive Dynamic Persona Registry & Focus Areas

Evaluate the change diff through activated specialist roles based exclusively on the files changed in the diff. Reviewer
personas must rigorously synthesize and verify the following holistic quality criteria:

- **Architectural Refactorings & Simplification**: Clean modular boundaries, reduction of technical debt, avoidance of
  over-engineering, and adherence to design patterns.
- **Backward Compatibility & Surface Stability**: Strict preservation of public API interfaces, legacy parameter
  aliases, and non-breaking contract deprecation paths.
- **Test Execution Counts & Pass Rates**: Comprehensive coverage of positive, boundary, and error cases; green CI checks
  and 100% test pass assertions.
- **Quality Gates & Complexity Baselines**: Satisfied linting, strict formatting, type safety, and containment within
  complexity baselines.
- **3-Ledger Provenance Integrity**: Continuous traceability and append-only immutability across `intents.jsonl`,
  `plans.jsonl`, and `executions.jsonl`.

### 1. Technical Engineering & System Architecture Roles

- **Principal Engineer / Tech Lead**: Code complexity, architectural fit, design patterns, readability, maintainability,
  naming conventions, technical debt, and extensibility.
- **Solution Architect**: System boundaries, component coupling, integration patterns, API contracts
  (REST/GraphQL/gRPC), caching strategies, scaling bottlenecks, and overall system topology.
- **Frontend Engineer**: UI structure, bundle sizes, client-side state management, CSS/HTML semantics, state-driven
  rendering logic, and browser performance.
- **QA & Test Engineer**: Test coverage, unit/integration/E2E test quality, boundary/edge cases, mocking/stubbing
  validity, test readability, and manual verification steps.
- **Machine Learning (ML) / Data Science Specialist**: Feature engineering, model evaluation, validation datasets,
  inference performance/latency, model drift detection, statistical correctness, and training pipeline changes.

### 2. Product, Design, & Growth Roles

- **Product Owner / Product Manager (PO/PM)**: Business logic validation, alignment with user stories/requirements,
  feature flags, user journey impact, and release readiness.
- **UX/UI Designer**: Design aesthetics, visual consistency (design system), responsive layout, spacing/margins,
  typography, user feedback animations/micro-interactions, and dark/light theme correctness.
- **SEO & Growth Specialist**: Search engine optimisation compliance, page load speeds, semantic HTML metadata
  (OpenGraph tags), page redirection logic, and marketing attribution tags.

### 3. Operations, Release, & Support Roles

- **DevOps & Site Reliability Engineer (SRE)**: CI/CD pipelines, build configurations, Infrastructure-as-Code (IaC),
  logging, metrics, tracing (observability), containerisation, environment variables, and resource limits.
- **Release Manager / Release Coordinator**: Release staging dependencies, migrations/deployments ordering, database
  rollback runbooks, feature flag strategies, and changelog verification.
- **Technical Support Engineer / Customer Success Lead**: Customer-facing error messages clarity, diagnostics and
  troubleshooting capability, impact on support ticket volume, self-service tools, and backwards-compatibility breaking
  changes.

### 4. Security, Compliance, & Risk Roles

- **Security Architect**: OWASP Top 10 vulnerabilities, authentication, authorisation (RBAC/ABAC), data validation,
  sanitisation, cryptography, secrets exposure, dependency vulnerability, and PII leakage.
- **Compliance & Privacy Auditor**: Regulatory compliance (GDPR, CCPA, HIPAA, SOC2), audit logging, copyleft license
  checks (e.g. GPL conflicts), and data retention policies.
- **Localization (L10n) & Internationalization (I18n) Coordinator**: Hardcoded strings, translation keys, formatting of
  numbers/dates/currencies, Right-to-Left (RTL) layout support, and localization bundle structure.

### 5. Developer Relations & Technical Documentation Roles

- **Technical Writer**: Public documentation, inline comments (docstrings), Swagger/OpenAPI docs, README updates, and
  clarity of technical explanations.
- **Developer Advocate (DevRel)**: Public SDK developer experience (DX), developer portal alignment, sample code
  accuracy, public API ease-of-use, and developer onboarding friction.

---

## ⚡ Dynamic Role Activation Matrix Guide

Before formulating feedback, analyse the file diff and determine active personas based strictly on changed files:

| Persona                            | Status (🟢 / ⚪) | Primary Trigger (Which files/contexts triggered activation) |
| :--------------------------------- | :--------------- | :---------------------------------------------------------- |
| **Principal Engineer / Tech Lead** | [🟢 / ⚪]        | [Reasoning / files triggered]                               |
| **Solution Architect**             | [🟢 / ⚪]        | [Reasoning / files triggered]                               |
| **Frontend Engineer**              | [🟢 / ⚪]        | [Reasoning / files triggered]                               |
| **QA & Test Engineer**             | [🟢 / ⚪]        | [Reasoning / files triggered]                               |
| **ML & Data Specialist**           | [🟢 / ⚪]        | [Reasoning / files triggered]                               |
| **Product Owner**                  | [🟢 / ⚪]        | [Reasoning / files triggered]                               |
| **UX/UI Designer**                 | [🟢 / ⚪]        | [Reasoning / files triggered]                               |
| **SEO & Growth Specialist**        | [🟢 / ⚪]        | [Reasoning / files triggered]                               |
| **DevOps & SRE**                   | [🟢 / ⚪]        | [Reasoning / files triggered]                               |
| **Release Manager**                | [🟢 / ⚪]        | [Reasoning / files triggered]                               |
| **Support Engineer**               | [🟢 / ⚪]        | [Reasoning / files triggered]                               |
| **Security Architect**             | [🟢 / ⚪]        | [Reasoning / files triggered]                               |
| **Compliance Auditor**             | [🟢 / ⚪]        | [Reasoning / files triggered]                               |
| **Localization Coordinator**       | [🟢 / ⚪]        | [Reasoning / files triggered]                               |
| **Technical Writer**               | [🟢 / ⚪]        | [Reasoning / files triggered]                               |
| **Developer Advocate**             | [🟢 / ⚪]        | [Reasoning / files triggered]                               |

---

## 📝 Review Guidelines & Severity Levels

For each active persona, categorise findings using these exact severity levels:

- 🔴 **CRITICAL / BLOCKER**: Major issues that break functionality, introduce severe security exploits, cause data loss,
  violate compliance, fail acceptance criteria, or severely degrade production performance. Must be fixed before
  merging.
- 🟡 **IMPORTANT / IMPROVEMENT**: Issues that affect code maintainability, visual polish, user experience flow, test
  coverage, minor performance, scale, or design patterns. Highly recommended to address.
- 🟢 **NIT / OPTIONAL**: Style guidelines, minor refactorings, spelling errors, styling nits, or alternative
  implementation suggestions. Must be explicitly `applied` or `rejected` with justification.
- ✅ **APPROVED / PASS**: Positive findings, praise, validation of implementation quality, or explicit confirmation of
  well-designed changes with no issues.

**Tone & Hygiene Guidelines:**

- Be constructive, respectful, and educational. Explain _why_ something is an issue and _how_ to fix it.
- Always provide diff-grounded code examples where applicable.
- Enforce Australian English (`en-AU`) orthography (`initialise`, `summarise`, `optimisation`, `standardisation`).
- Format all arithmetic and EV formulas inside markdown code spans (backticks) to maintain prettier compatibility.

---

## 🗳️ Multi-Agent Ensemble Review & Consensus Synthesis

- Execute `{ensemble_size}` independent review passes without cross-pass contamination.
- Deduplicate findings flagged by multiple reviewers, prefixing multi-reviewer attribution tags (e.g.
  `[Flagged by Reviewer 1 & 2]`).
- A PR can receive an overall **`APPROVED`** verdict **ONLY IF ALL THREE (3/3) independent review passes** return zero
  Critical (🔴) or Important (🟡) issues, zero open actionable Nits (🟢), AND all automated CI checks pass cleanly.
- If ANY reviewer identifies Critical or Important issues, or if any CI check fails, the verdict is
  **`CHANGES_REQUESTED`**.

---

## 🏷️ Commit Trailers & Provenance Ledgers

All review iteration commits and resolutions must carry RFC 2822 commit trailers:

```text
Action-Origin: pr_review
Review-Iteration: {iteration}
Review-Finding-Ids: {finding_ids}
Review-Severity: {severity}
```

All evaluations, finding detections, and resolver events must be appended to the executions ledger
(`holon-knowledge/ledger/executions.jsonl`) via `sandbox_executor.flow.record_review_action()` (or, without a
`FlowContext`, the context-free `sandbox_executor.ledger.try_append_action_record(..., preserve_shape=True)`), both of
which validate each record and serialise the append under `LedgerLock`.

---

## 📤 Standard Review Output Schema

When posting review results to GitHub, adhere strictly to the following markdown structure depending on the verdict:

### Approved Consensus Review Schema

```markdown
## ✅ Ensemble Consensus Review — APPROVED ({configured}/{configured})

- **Pull Request:** [{repo_name}#{pr_number}]({pr_url})
- **Target Commit:** `{target_commit}`
- **Base Branch:** `{target_branch}`
- **Iteration:** `{iteration}`
- **Verdict:** `APPROVED`

---

### 📋 Evaluation Summary

- **Verified:** Architectural refactorings and code simplification verified.
- **Verified:** Backward compatibility guarantees and interface stability maintained (including legacy function
  aliases).
- **Verified:** Test execution counts and passing assertions verified ({ci_passed}/{ci_total} passing).
- **Verified:** Quality gates and complexity baselines fully satisfied.
- **Verified:** 3-ledger provenance integrity maintained across `intents.jsonl`, `plans.jsonl`, and `executions.jsonl`.

---

### 🗳️ Ensemble Review Breakdown

- **Reviewer 1** (`{reviewer_1_id}`): `APPROVED` (Critical: 0, Important: 0, Nit: 0)
- **Reviewer 2** (`{reviewer_2_id}`): `APPROVED` (Critical: 0, Important: 0, Nit: 0)
- **Reviewer 3** (`{reviewer_3_id}`): `APPROVED` (Critical: 0, Important: 0, Nit: 0)
- **Ensemble Consensus Verdict**: `APPROVED`
- **GitHub Actions CI Status**: `{ci_passed}/{ci_total} PASS` ({ci_passed} successful, 0 failing, 0 pending)

> 🤖 **Reviewed by**: `{agent}` ({ensemble_size}-Agent Ensemble) · **Model**: `{model}`

---

> [!NOTE] **Bean 0034 Notice:** Under Bean 0034 (Human-Only PR Merging), autonomous merging to `main` is strictly
> prohibited. Pipeline halts for manual human approval.
```

### Changes-Requested Review Schema

```markdown
## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration {iteration})

- **Pull Request:** [{repo_name}#{pr_number}]({pr_url})
- **Target Commit:** `{target_commit}`
- **Base Branch:** `{target_branch}`
- **Iteration:** `{iteration}`
- **Verdict:** `CHANGES_REQUESTED`

---

### 👥 Reviewers

Reviewers: {ensemble_size} configured, {completed} completed.

- **Reviewer 1** (`{reviewer_1_id}`): `CHANGES_REQUESTED` (Critical: 1, Important: 0, Nit: 0)
- **Reviewer 2** (`{reviewer_2_id}`): `APPROVED` (Critical: 0, Important: 0, Nit: 0)
- **Reviewer 3** (`{reviewer_3_id}`): `APPROVED` (Critical: 0, Important: 0, Nit: 0)

---

### 📋 Findings

| Severity   | Location            | Reviewer   | Summary                             |
| ---------- | ------------------- | ---------- | ----------------------------------- |
| `critical` | `src/example.py:42` | Reviewer 1 | Critical regression in auth handler |

---

### 🗳️ Checks

- **Mergeability:** `CLEAN`
- **CI:** `{ci_passed}/{ci_total} PASS` ({ci_passed} successful, 0 failing, 0 pending)
```

---

## 🏁 Machine-Readable On-Disk Trailer Block

Every on-disk report written to `.subagent/` MUST close with the trailer block as its final lines (omitted from the
GitHub comment):

```text
VERDICT: <APPROVED | CHANGES_REQUESTED | COMMENT>
CI: <PASS | FAIL | SKIPPED | PENDING>
REPORT: <path to this report>
CRITICAL: <int>
IMPORTANT: <int>
NIT: <int>
FINDINGS: <int>
```

---

## Reviewer turn contract

When you run as one reviewer of the independent ensemble (one reviewer turn), these rules apply and take precedence over
the loop instructions above:

1. Review only the change diff and change description supplied in this prompt. Do not resolve findings, and do not run
   other review passes.
2. Write the full report with your file-write tool to the report path given at the end of this prompt.
3. End the report with the review trailer block as its final lines. Between the opening and closing marker lines sits
   one JSON object with exactly the keys `verdict` (`APPROVED` or `CHANGES_REQUESTED`), `critical`, `important` and
   `nit` (non-negative integers) and `findings` (a list of objects with exactly the keys `severity` (`critical`,
   `important` or `nit`), `file`, `line` (an integer or `null`) and `summary`). No key may appear twice in any object.
4. Each count must equal the number of findings with that severity, and `APPROVED` requires every count to be zero. A
   report whose trailer is missing, malformed or inconsistent counts as a failed review, never as an approval.
5. For reviewer turns, this JSON trailer block supersedes the line-based on-disk trailer described above.
6. Never modify code, commit, push, run `gh pr merge`, enable auto-merge or add the PR to a merge queue.
7. Never read other files under `.holon/review/`; other reviewers' reports are not part of your input.
8. Write no file other than the report path. Make no GitHub write of any kind: no `gh pr review`, `gh pr comment`,
   `gh pr edit` or `gh api` call, and no merge or auto-merge. Append to no ledger under `holon-knowledge/ledger/`.
9. Spawn no subagents, and write nothing under `.subagent/`. The loop that runs the ensemble posts the consensus and
   records the ledger rows itself.

The final lines of a report with no findings:

```text
<!-- holon-review-trailer -->
{
  "verdict": "APPROVED",
  "critical": 0,
  "important": 0,
  "nit": 0,
  "findings": []
}
<!-- /holon-review-trailer -->
```

---

## 📋 Evaluation Context

- **Target Ref**: `{target}`
- **Upstream Branch**: `{target_branch}`
- **Ensemble Size**: `{ensemble_size}`
- **Max Iterations**: `{max_iterations}`
- **Current Iteration**: `{iteration}`
- **PR Title**: `{pr_title}`

### Change Description:

```markdown
{pr_description}
```

### Change Diff:

```diff
{diff}
```
"""

PYTHON_GITIGNORE = [
    ".venv/",
    ".holon-cache/",
    "__pycache__/",
    "*.pyc",
    ".pytest_cache/",
    ".ruff_cache/",
]

GENERIC_GITIGNORE = [
    ".holon-cache/",
]


def _format_rel(path: str) -> str:
    """Formats a path relative to the current working directory for clean display."""
    try:
        return os.path.relpath(path, os.getcwd())
    except ValueError:
        return path


def _write_file(path: str, content: str, force: bool = False) -> tuple[bool, str]:
    """Writes a file if it does not exist, or overwrites if force=True.

    Returns (written, message).
    """
    rel_path = _format_rel(path)
    if os.path.exists(path):
        if not force:
            return False, f"Skipped existing {rel_path} (use --force to overwrite)"
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return True, f"Overwrote {rel_path}"

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return True, f"Created {rel_path}"


def _ensure_ledger_file(path: str) -> tuple[bool, str]:
    """Ensures ledger file exists. NEVER overwrites or truncates existing ledger files."""
    rel_path = _format_rel(path)
    if os.path.exists(path):
        return False, f"Preserved existing ledger {rel_path}"

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8"):
        pass  # create empty file
    return True, f"Created ledger {rel_path}"


def _update_gitignore(target_dir: str, template: str) -> tuple[bool, str]:
    """Creates or updates .gitignore with standard ignore patterns without duplicates."""
    gitignore_path = os.path.join(target_dir, ".gitignore")
    rel_path = _format_rel(gitignore_path)
    patterns = PYTHON_GITIGNORE if template == "python" else GENERIC_GITIGNORE

    existing_lines = []
    if os.path.exists(gitignore_path):
        with open(gitignore_path, encoding="utf-8") as f:
            existing_lines = [line.strip() for line in f.readlines()]

    missing_patterns = [p for p in patterns if p.strip() not in existing_lines]
    if not missing_patterns:
        return False, f".gitignore already up to date ({rel_path})"

    needs_newline = False
    if os.path.exists(gitignore_path):
        with open(gitignore_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size > 0:
                f.seek(-1, os.SEEK_END)
                last_char = f.read(1)
                needs_newline = last_char != b"\n"

    with open(gitignore_path, "a", encoding="utf-8") as f:
        if needs_newline:
            f.write("\n")
        if not any("# Holon" in line for line in existing_lines):
            if existing_lines:
                f.write("\n# Holon\n")
            else:
                f.write("# Holon\n")
        for p in missing_patterns:
            f.write(f"{p}\n")

    action = "Updated" if os.path.exists(gitignore_path) and existing_lines else "Created"
    return True, f"{action} {rel_path} (added: {', '.join(missing_patterns)})"


def init_project(target_dir: str = ".", template: str = "python", force: bool = False) -> int:
    """Scaffolds Holon directories, templates, and ledgers in target_dir.

    Args:
        target_dir: The project root directory to initialise.
        template: Project template type ('python' or 'generic').
        force: If True, overwrite configuration files (ledger files are never overwritten).

    Returns:
        0 on success, non-zero on error.
    """
    try:
        target_dir = os.path.abspath(target_dir)
        os.makedirs(target_dir, exist_ok=True)

        ruleset_content = RULESET_PYTHON if template == "python" else RULESET_GENERIC

        print(f"Initialising Holon project in {_format_rel(target_dir)} (template: {template})...")

        # 1. Scaffolding holon-config/
        config_dir = os.path.join(target_dir, "holon-config")
        files_to_scaffold = [
            (os.path.join(config_dir, "world", "ruleset.md"), ruleset_content),
            (os.path.join(config_dir, "world", "constraints.md"), CONSTRAINTS_MD),
            (os.path.join(config_dir, "metrics", "README.md"), METRICS_README),
            (
                os.path.join(config_dir, "metrics", "entropy_config.json"),
                json.dumps(ENTROPY_CONFIG, indent=2) + "\n",
            ),
            (
                os.path.join(config_dir, "metrics", "ev_config.json"),
                json.dumps(EV_CONFIG, indent=2) + "\n",
            ),
            (
                os.path.join(config_dir, "metrics", "system_entropy_config.json"),
                json.dumps(SYSTEM_ENTROPY_CONFIG, indent=2) + "\n",
            ),
            (os.path.join(config_dir, "prompts", "planner.template.md"), PLANNER_TEMPLATE),
            (os.path.join(config_dir, "prompts", "executor.template.md"), EXECUTOR_TEMPLATE),
            (os.path.join(config_dir, "prompts", "review_loop.template.md"), REVIEW_LOOP_TEMPLATE),
        ]

        for path, content in files_to_scaffold:
            _, msg = _write_file(path, content, force=force)
            print(f"  - {msg}")

        # 2. Scaffolding holon-knowledge/
        knowledge_dir = os.path.join(target_dir, "holon-knowledge")
        ledger_files = [
            os.path.join(knowledge_dir, "ledger", "intents.jsonl"),
            os.path.join(knowledge_dir, "ledger", "plans.jsonl"),
            os.path.join(knowledge_dir, "ledger", "executions.jsonl"),
        ]

        for ledger_path in ledger_files:
            _, msg = _ensure_ledger_file(ledger_path)
            print(f"  - {msg}")

        ledger_gitkeep = os.path.join(knowledge_dir, "ledger", ".gitkeep")
        if not os.path.exists(ledger_gitkeep):
            with open(ledger_gitkeep, "w", encoding="utf-8"):
                pass
            print(f"  - Created {_format_rel(ledger_gitkeep)}")

        for sub_dir in ["plans", "kb"]:
            full_sub = os.path.join(knowledge_dir, sub_dir)
            os.makedirs(full_sub, exist_ok=True)
            gitkeep = os.path.join(full_sub, ".gitkeep")
            if not os.path.exists(gitkeep):
                with open(gitkeep, "w", encoding="utf-8"):
                    pass
                print(f"  - Created {_format_rel(gitkeep)}")

        # 3. Scaffolding intents/
        intents_dir = os.path.join(target_dir, "intents")
        os.makedirs(intents_dir, exist_ok=True)
        intents_gitkeep = os.path.join(intents_dir, ".gitkeep")
        if not os.path.exists(intents_gitkeep):
            with open(intents_gitkeep, "w", encoding="utf-8"):
                pass
            print(f"  - Created {_format_rel(intents_gitkeep)}")

        # 4. Updating .gitignore
        _, gitignore_msg = _update_gitignore(target_dir, template)
        print(f"  - {gitignore_msg}")

        print("Holon initialisation complete.")
        return 0
    except OSError as err:
        print(f"Error: Failed to initialise project in '{target_dir}': {err}", file=sys.stderr)
        return 1
