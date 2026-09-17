"""Project scaffolding logic for `holon init`.

Automates onboarding and scaffolding of new or existing projects into the Holon ecosystem.
Creates holon-config/, holon-knowledge/, and configures .gitignore with idempotency guarantees.
"""

import json
import os

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
  - Sub-packages isolated or organized under `src/` or `apps/`.

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

- **Testing Tool:** `pytest` as standard test runner.
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

and the current state of the project give a detailed plan.
"""

EXECUTOR_TEMPLATE = """You are an autonomous execution agent operating directly inside the repository workspace at
`{repo_dir}`. Your task is to implement the plan `{plan_id}` for intent `{intent_id}` by directly writing, modifying,
creating, and deleting codebase files.

**Critical Execution Directives:**

1. You must implement all changes specified in the plan by directly writing and modifying the files in this repository.
2. Do NOT merely summarize or discuss the plan in text—you MUST modify the actual files on the filesystem using your
   file creation and editing tools.
3. Run the relevant test suite using your terminal tools to verify your implementation and ensure all tests pass.
4. Ensure code formatting, linting, and repo conventions are followed.

**Plan Content:** {plan_content}

**Intent Metadata:**

```json
{intent_json}
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


def _write_file(path: str, content: str, force: bool = False) -> tuple[bool, str]:
    """Writes a file if it does not exist, or overwrites if force=True.

    Returns (written, message).
    """
    if os.path.exists(path):
        if not force:
            return False, f"Skipped existing {path} (use --force to overwrite)"
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return True, f"Overwrote {path}"

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return True, f"Created {path}"


def _ensure_ledger_file(path: str) -> tuple[bool, str]:
    """Ensures ledger file exists. NEVER overwrites or truncates existing ledger files."""
    if os.path.exists(path):
        return False, f"Preserved existing ledger {path}"

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8"):
        pass  # create empty file
    return True, f"Created ledger {path}"


def _update_gitignore(target_dir: str, template: str) -> tuple[bool, str]:
    """Creates or updates .gitignore with standard ignore patterns without duplicates."""
    gitignore_path = os.path.join(target_dir, ".gitignore")
    patterns = PYTHON_GITIGNORE if template == "python" else GENERIC_GITIGNORE

    existing_lines = []
    if os.path.exists(gitignore_path):
        with open(gitignore_path, encoding="utf-8") as f:
            existing_lines = [line.strip() for line in f.readlines()]

    missing_patterns = [p for p in patterns if p.strip() not in existing_lines]
    if not missing_patterns:
        return False, f".gitignore already up to date ({gitignore_path})"

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
            f.write("\n# Holon\n")
        for p in missing_patterns:
            f.write(f"{p}\n")

    action = "Updated" if os.path.exists(gitignore_path) and existing_lines else "Created"
    return True, f"{action} {gitignore_path} (added: {', '.join(missing_patterns)})"


def init_project(target_dir: str = ".", template: str = "python", force: bool = False) -> int:
    """Scaffolds Holon directories, templates, and ledgers in target_dir.

    Args:
        target_dir: The project root directory to initialize.
        template: Project template type ('python' or 'generic').
        force: If True, overwrite configuration files (ledger files are never overwritten).

    Returns:
        0 on success, non-zero on error.
    """
    target_dir = os.path.abspath(target_dir)
    os.makedirs(target_dir, exist_ok=True)

    ruleset_content = RULESET_PYTHON if template == "python" else RULESET_GENERIC

    print(f"Initializing Holon project in {target_dir} (template: {template})...")

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

    for sub_dir in ["plans", "kb"]:
        full_sub = os.path.join(knowledge_dir, sub_dir)
        os.makedirs(full_sub, exist_ok=True)
        gitkeep = os.path.join(full_sub, ".gitkeep")
        if not os.path.exists(gitkeep):
            with open(gitkeep, "w", encoding="utf-8"):
                pass
            print(f"  - Created {gitkeep}")

    # 3. Updating .gitignore
    _, gitignore_msg = _update_gitignore(target_dir, template)
    print(f"  - {gitignore_msg}")

    print("Holon initialization complete.")
    return 0
