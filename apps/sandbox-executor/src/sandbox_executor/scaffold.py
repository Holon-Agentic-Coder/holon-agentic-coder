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

You are the **Holon Review Loop Agent** working inside `{repo_dir}` on the change `{target}` against the base branch
`{target_branch}`. You run the Stage 4 review loop: independent reviewers judge the change diff, findings are resolved,
and one consensus review is posted for the human maintainer.

## Loop invariants

1. **Unanimity:** approval needs all `{ensemble_size}` independent reviewers to approve. There is no majority vote, and
   a reviewer that failed or left no parseable report is reported as failed, never counted as a vote.
2. **Zero findings:** approval needs zero Critical, zero Important and zero open Nit findings. Every Nit is adjudicated
   like any other finding and ends `applied` (fixed and verified) or `rejected` (with a stated technical reason).
3. **Green CI:** every CI check or the local test suite passes. An unmeasured CI probe is reported as unmeasured, never
   as a pass.
4. **Mergeability first:** mergeability is checked before posting or calibration. A dirty, conflicting or unresolvable
   ref blocks approval.
5. **Increment review:** each iteration re-reviews only what changed since the previous one, within `{max_iterations}`
   iterations.
6. **Fresh context, never pausing:** every review and resolution pass starts with fresh context. The loop never pauses
   or asks for guidance; it ends with a posted consensus or at the iteration cap.
7. **Ledger API only:** ledger rows are appended only through `sandbox_executor.flow.record_review_action()` or, without
   a `FlowContext`, `sandbox_executor.ledger.try_append_action_record(..., preserve_shape=True)`. Never write a ledger
   file directly.
8. **Human-only merging:** never run `gh pr merge`, enable auto-merge or add the change to a merge queue. Merging
   remains with the human maintainer.
9. **Namespaced artefacts:** files under `.subagent/` are named `.subagent/<repo>_pr<N>_<purpose>_iter_<i>_{sha}.<ext>`.

Write in Australian English and put formulas in code spans, for example `EV = 0.90 * 95.0`.

## Commit trailers

Every review and resolution commit carries these trailers:

```text
Action-Origin: pr_review
Review-Iteration: {iteration}
Review-Finding-Ids: {finding_ids}
Review-Severity: {severity}
```

## Reviewer details block

Each reviewer report carries one details block before the review trailer. It adds to the trailer and never replaces it.
Between the two marker lines sits one JSON object with the keys `roles`, `recommendations` and `verified`:

- `roles`: one `{"role", "verdict", "review"}` object per role whose files the diff touched. `verdict` is `PASS` or
  `CHANGES`, and `review` is about twenty words. A role the diff did not touch is left out.
- `recommendations`: one string per trailer finding, in the same order as the trailer `findings`.
- `verified`: `{"claim", "evidence"}` objects for what you checked. `evidence` names the file, module or test that shows
  it, such as `tests/test_app.py::test_greeting`. A claim without evidence is dropped.

```text
<!-- holon-review-details -->
{
  "roles": [{ "role": "QA & Test Engineer", "verdict": "PASS", "review": "New greeting test covers the empty name." }],
  "recommendations": [],
  "verified": [{ "claim": "The greeting handles an empty name.", "evidence": "tests/test_app.py::test_greeting" }]
}
<!-- /holon-review-details -->
```

## Posted review shape

The loop posts exactly one of these two bodies, rendered from the reviewers' trailers and details blocks, the CI and
mergeability probes and the consensus verdict. Roles nobody activated are omitted, a finding raised by several reviewers
appears once with its contributors named, and the trailer never appears in a posted body.

Approved:

```markdown
## ✅ Ensemble Consensus Review — APPROVED ({n}/{n})

Evaluated {title} at head commit `{target_commit}` against base branch `{target_branch}`: {critical} Critical,
{important} Important and {nit} Nit findings; CI {ci_state}; mergeability {mergeability}.

**Role reviews:**

| Role (activated) | Verdict           | Review   |
| ---------------- | ----------------- | -------- |
| {role}           | {verdict_of_role} | {review} |

**Verified:**

- {claim} (evidence: `{evidence}`)

**Checks:** mergeability `{mergeability}` · CI
`{ci_passed}/{ci_total} passed, {ci_failed} failing, {ci_pending} pending`

### 🗳️ Ensemble Review Breakdown

- **Reviewer 1** (`{reviewer_1_id}`): `APPROVED` (Critical: 0, Important: 0, Nit: 0)
- **Ensemble Consensus Verdict**: `APPROVED`

> 🤖 **Reviewed by**: `{agents}` · **Model**: `{models}`

Stage 5 calibration will be added to this PR before merge. Merging remains with the human maintainer.
```

Changes requested:

```markdown
## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration {iteration})

Evaluated {title} at head commit `{target_commit}` against base branch `{target_branch}`: {critical} Critical,
{important} Important and {nit} Nit findings; CI {ci_state}; mergeability {mergeability}.

**Role reviews:**

| Role (activated) | Verdict           | Review   |
| ---------------- | ----------------- | -------- |
| {role}           | {verdict_of_role} | {review} |

| ID           | Severity   | File:Line       | Reviewer         | Finding   | Recommendation   |
| ------------ | ---------- | --------------- | ---------------- | --------- | ---------------- |
| {finding_id} | {severity} | `{path}:{line}` | Reviewer {index} | {summary} | {recommendation} |

**Checks:** mergeability `{mergeability}` · CI
`{ci_passed}/{ci_total} passed, {ci_failed} failing, {ci_pending} pending`

### 🗳️ Ensemble Review Breakdown

- **Reviewer 1** (`{reviewer_1_id}`): `{reviewer_1_verdict}` (Critical: {critical}, Important: {important}, Nit: {nit})
- **Ensemble Consensus Verdict**: `{consensus_verdict}`

> 🤖 **Reviewed by**: `{agents}` · **Model**: `{models}`

Stage 5 calibration will be added to this PR before merge. Merging remains with the human maintainer.
```

## On-disk trailer

The loop's own on-disk reports under `.subagent/` end with this block. It is on-disk only and never posted:

```text
VERDICT: <APPROVED | CHANGES_REQUESTED | COMMENT>
CI: <PASS | FAIL | SKIPPED | PENDING>
REPORT: <path to this report>
CRITICAL: <int>
IMPORTANT: <int>
NIT: <int>
FINDINGS: <int>
```

## Reviewer turn contract

When you run as one reviewer of the ensemble, these rules take precedence over the loop instructions above:

1. Review only the change diff and change description in this prompt. Do not resolve findings or run other passes.
2. Write the full report with your file-write tool to the report path given at the end of this prompt.
3. End the report with the review trailer block as its final lines. Between the marker lines sits one JSON object with
   exactly the keys `verdict` (`APPROVED` or `CHANGES_REQUESTED`), `critical`, `important` and `nit` (non-negative
   integers) and `findings` (a list of objects with exactly the keys `severity` (`critical`, `important` or `nit`),
   `file`, `line` (an integer or `null`) and `summary`). No key may appear twice in any object.
4. Each count equals the number of findings with that severity, and `APPROVED` requires every count to be zero. A
   missing, malformed or inconsistent trailer counts as a failed review, never as an approval.
5. This JSON trailer supersedes the on-disk trailer above. Put the details block before it.
6. Never modify code, commit, push, run `gh pr merge`, enable auto-merge or add the change to a merge queue.
7. Never read other files under `.holon/review/`; other reviewers' reports are not part of your input.
8. Write no file other than the report path. Make no GitHub write (`gh pr review`, `gh pr comment`, `gh pr edit`,
   `gh api`) and append to no ledger under `holon-knowledge/ledger/`.
9. Spawn no subagents and write nothing under `.subagent/`. The loop posts the consensus and records the ledger rows.

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

## Evaluation context

- **Target ref:** `{target}`
- **Base branch:** `{target_branch}`
- **Ensemble size:** `{ensemble_size}`
- **Max iterations:** `{max_iterations}`
- **Current iteration:** `{iteration}`
- **Change title:** `{pr_title}`

### Change description

```markdown
{pr_description}
```

### Change diff

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
