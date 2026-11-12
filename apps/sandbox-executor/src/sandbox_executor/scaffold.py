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

**Write Protocol:** create `plans/{plan_id}.md` with your file-write tool immediately, write
the header and the `## Overall Plan Metrics` table first, append each step as it is finished, and keep your final chat
message to one short confirmation line.

**Code Placement & Sizing:**

- New behaviour goes in the module that owns the data (or a new module); maintain one module per stage, command, runner
  or provider.
- Functions stay within repository ruff complexity limits (`C901 <= 10`, `PLR0912 <= 12`, `PLR0913 <= 5`,
  `PLR0915 <= 50`) without adding inline '# noqa' suppressions.
- Use intention-revealing names in the domain language (`intent`, `plan`, `execution`, `review`, `calibration`,
  `ledger`).
- Add no new imports from entrypoints into the host flow engine.
- The plan lists its touch set at module granularity and prefers adding focused modules over growing existing files
  past ~400 lines.
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
7. Code placement: new behaviour goes in the module that owns the data (or a new module); maintain one module per stage,
   command, runner or provider; functions stay within repository ruff complexity limits (`C901 <= 10`, `PLR0912 <= 12`,
   `PLR0913 <= 5`, `PLR0915 <= 50`) without adding inline '# noqa' suppressions; use intention-revealing names in the
   domain language (`intent`, `plan`, `execution`, `review`, `calibration`, `ledger`); add no new imports from
   entrypoints into the host flow engine.

**Plan Content:** {plan_content}

**Intent Metadata:**

```json
{intent_json}
```
"""

REVIEW_LOOP_TEMPLATE = """# Holon Review Loop Agent Instructions

You are the autonomous **Holon Review Loop Agent** operating inside `{repo_dir}` for target PR/branch `{target}` against
upstream branch `{target_branch}`. Your purpose is to execute the Stage 4 Review & Resolve Loop under the ensemble
consensus model and post one short consensus review built only from what the reviewers actually reported.

---

## Loop invariants

1. **Iterate to consensus**: review and resolve until the ensemble approves unanimously or the iteration budget
   (`{max_iterations}`) is reached. The loop never pauses or asks for guidance mid-flight; the only exits are a posted
   consensus approval or a final review posted at the cap. The flow engine owns the iteration counter, saves it in the
   flow checkpoint (`review_iterations_done`), and on a resume from the review stage passes the continued count as the
   `--iteration` label so ledger `review_iteration` values and posted `Iteration {iteration}` headings form one
   monotonic sequence across halts and resumes; `--iteration` is never a loop limit and is never set by operators.
2. **Fresh context**: every review pass and resolution pass runs in a newly spawned subagent with fresh context.
3. **Unanimous approval**: the verdict is `APPROVED` only when all `{ensemble_size}` independent reviewers approve with
   zero Critical, zero Important and zero open Nit findings, and every CI check passes (`gh pr checks` or the local test
   suite, exit code `0`). Any objection, failed reviewer or failing check means `CHANGES_REQUESTED`. A reviewer that
   failed or left no parseable output is reported as failed, never counted as a vote.
4. **Nits are actioned**: every Nit is adjudicated like any other finding and ends `applied` (fixed, verified,
   committed) or `rejected` with a one-line reason (`not diff-grounded`, `inaccurate`, `out of scope`, or
   `conflicting with a higher-severity finding`). An unadjudicated Nit blocks approval. A finding set that survives 3
   consecutive iterations with no diff change is `rejected: no-convergence`. Rejected findings are passed to the next
   iteration's reviewers as already adjudicated so they are not re-raised.
5. **Mergeability first**: check mergeability (and re-sync drifted state: dirty tree, new commits, pending CI) before
   posting a review or adding calibration. Out-of-sync state is work, not a blocker.
6. **Increment review**: after the first pass, review only the increment since the last reviewed commit; after a
   resolver push, the next engine iteration reviews the new head in full and any push invalidates earlier votes.
7. **Attrition absorption**: a single failed or timed-out pass never aborts the loop; relaunch or salvage it.
8. **Namespaced artefacts**: every artefact under `.subagent/` is scoped by repository and pull request:
   `.subagent/<repo>_pr<N>_<purpose>_iter_<iteration>_{sha}.<ext>`, with state in `.subagent/<repo>_pr<N>_state.json`.
9. **Ledger API only**: append action records to `holon-knowledge/ledger/executions.jsonl` only through
   `sandbox_executor.flow.record_review_action()` (or, without a `FlowContext`, the context-free
   `sandbox_executor.ledger.try_append_action_record(..., preserve_shape=True)`), never by writing to the file directly.
10. **Human-only merging**: never run `gh pr merge`, enable auto-merge or add the PR to a merge queue. Merging remains
    with the human maintainer; on consensus approval, halt and request human review.
11. **Resolver discipline**: when the resolver container runs on `CHANGES_REQUESTED`, it adjudicates every finding to
    `applied` or `rejected`, runs `uv run task lint` and `uv run task test` before pushing, commits applied fixes with
    the finding ids in the commit message, and never force-pushes or rewrites history.

Review commits carry these RFC 2822 trailers:

```text
Action-Origin: pr_review
Review-Iteration: {iteration}
Review-Finding-Ids: {finding_ids}
Review-Severity: {severity}
```

Write in Australian English (`initialise`, `summarise`, `optimisation`) and put arithmetic inside code spans.

---

## Roles

Activate only the roles the changed files call for (for example engineering, testing, security or documentation), name
each one in the reviewer data block, and give each a verdict of `PASS` or `CHANGES`. Never list a role the diff did not
activate, and never report an inactive role as passing.

---

## Posted review shape

The consensus review posted to the PR has exactly this shape, in this order. Every row, bullet and count comes from the
reviewer data; nothing is filled in from a fixed list.

```markdown
## ✅ Ensemble Consensus Review — APPROVED ({approvals}/{configured})

## ❌ Ensemble Consensus Review — CHANGES REQUESTED (Iteration {iteration}) — {approvals}/{configured} approved

Reviewed {title} at `{head_commit}` against `{base_branch}`: {critical} critical, {important} important and {nit} nit
findings; CI {ci_state}; mergeability {mergeability}.

**Role reviews:**

| Role (activated) | Verdict | Review                                                                                  |
| ---------------- | ------- | --------------------------------------------------------------------------------------- |
| Security         | PASS    | Checked the renderer for markdown injection; reviewer strings are redacted and bounded. |

**Verified:**

- Reviewer text is redacted before posting — `apps/sandbox-executor/tests/test_review_report.py`

| ID    | Severity  | File:Line     | Reviewer | Finding                                      | Recommendation    |
| ----- | --------- | ------------- | -------- | -------------------------------------------- | ----------------- |
| R1-F1 | important | src/app.py:12 | 1, 2     | [Flagged by Reviewer 1 & 2] Drops the error. | Return the error. |

**Checks:** Mergeability {mergeability} · CI {passed}/{total} passed, {failed} failing, {pending} pending

### 🗳️ Ensemble Review Breakdown

- **Reviewer 1** (`{reviewer_1_id}`): `{verdict}` (critical: {critical}, important: {important}, nit: {nit})
- **Ensemble Consensus Verdict**: `{consensus_verdict}`

Reviewed by `{agent}` ({n}-Agent Ensemble) · Model `{model}`

Stage 5 calibration will be added to this PR before merge. Merging remains with the human maintainer.
```

- Use the first heading only for a unanimous `APPROVED` verdict and the second for every other verdict.
- Use the `**Verified:**` list for an approval and the findings table otherwise. Each Verified bullet names the file,
  module or test that evidences it; with no evidenced claim, say so instead of writing a bullet.
- A finding raised by several reviewers appears once, with every contributor named in the Finding cell.
- Mergeability is one of `CLEAN`, `DIRTY`, `CONFLICTING` or `MISSING_REF`. A probe that was not measured renders as
  `UNMEASURED` (mergeability) or `CI unmeasured`, never as clean.
- The breakdown has one line per configured reviewer; `{reviewer_1_id}` stands for that reviewer's agent and model. A
  failed reviewer appears as `FAILED` with its reason and contributes no role or vote.

---

## Machine-readable on-disk trailer block

Every on-disk report written to `.subagent/` closes with this trailer block as its final lines. It is written to on-disk
reports only and never posted to the PR:

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
6. Immediately before the trailer block, add the reviewer data block described below. It is optional, but without it the
   posted review shows no roles, finding ids, recommendations or verified claims from you.
7. Never modify code, commit, push, run `gh pr merge`, enable auto-merge or add the PR to a merge queue.
8. Never read other files under `.subagent/review/`; other reviewers' reports are not part of your input.
9. Write no file other than the report path. Make no GitHub write of any kind: no `gh pr review`, `gh pr comment`,
   `gh pr edit` or `gh api` call, and no merge or auto-merge. Append to no ledger under `holon-knowledge/ledger/`.
10. Spawn no subagents, and write nothing under `.subagent/` outside your assigned scratch directory. The loop that runs
    the ensemble posts the consensus and records the ledger rows itself.
11. Flag growth of any file over 1,000 lines as Important, flag new inline complexity suppressions as Important, and
    check that new behaviour lives in the module that owns the data with no new entrypoint-to-engine imports.

### Reviewer data block

The reviewer data block sits between the `<!-- holon-review-roles -->` and `<!-- /holon-review-roles -->` marker lines,
immediately before the trailer block, and holds one JSON object with three keys:

- `roles`: one object per role the diff activated, with `role` (a short name), `verdict` (`PASS` or `CHANGES`) and
  `review` (about twenty words on what the role checked and found).
- `findings`: one object per trailer finding, in the same order as the trailer's `findings`, with `id` (a short token
  such as `R1-F1`) and `recommendation` (the fix you recommend).
- `verified`: one object per claim you checked, with `claim` and `evidence` (the file, module or test that evidences
  it). A claim without evidence is dropped.

The final lines of a report with no findings:

```text
<!-- holon-review-roles -->
{
  "roles": [{"role": "Testing", "verdict": "PASS", "review": "Ran the new parser tests; they cover failed reviewers."}],
  "findings": [],
  "verified": [{"claim": "Failed reviewers cast no vote", "evidence": "tests/test_review_report.py"}]
}
<!-- /holon-review-roles -->
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

## Evaluation Context

- **Target Ref**: `{target}`
- **Upstream Branch**: `{target_branch}`
- **Ensemble Size**: `{ensemble_size}`
- **Max Iterations**: `{max_iterations}`
- **Current Iteration**: `{iteration}`
- **PR Title**: `{pr_title}`

### Change Description:

<!-- prettier-ignore -->
````markdown
{pr_description}
````

### Change Diff:

<!-- prettier-ignore -->
````diff
{diff}
````
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
