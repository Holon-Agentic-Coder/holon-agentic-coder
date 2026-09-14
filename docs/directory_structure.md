# Directory Structure

This document outlines the directory structure and monorepo layout for the **Holon: Agentic Coder** system. The layout
enforces separation of concerns by separating application code, project-specific governance/configuration, and dynamic
event/knowledge logging.

---

## Workspace Layout

```
.
├── apps/                                 # Executable applications
│   ├── holon-cli/                        # CLI wrapper and script tools
│   │   ├── bin/holon                     # The executable CLI tool wrapper
│   │   └── setup.py / pyproject.toml
│   └── sandbox-executor/                 # Isolated Docker-based executor environment
│       ├── Dockerfile
│       ├── build_all_images.sh
│       └── files/
│           └── entrypoints/
│
├── libs/                                 # Shared libraries
│   └── holon-engine/                     # Core orchestrator and agent logic
│       ├── holon/                        # Source Python package
│       │   ├── __init__.py
│       │   ├── orchestrator.py           # Core execution flow orchestrator
│       │   ├── core/
│       │   │   ├── __init__.py
│       │   │   ├── intent.py
│       │   │   ├── planner.py
│       │   │   └── executor.py
│       │   └── utils/
│       └── pyproject.toml
│
├── holon-coherence/                      # Optimization Gateway & Wire Telemetry (Git Submodule)
│   ├── src/holon_coherence/              # Proxy, token reduction algorithms, and telemetry
│   └── tests/
│
├── holon-config/                         # Governance / Static Priors (Human Authored)
│   ├── prompts/                          # Agent missions and templates
│   │   └── planner.template.md
│   ├── schemas/                          # Schemas for ledgers and KBs
│   │   ├── kb.json
│   │   └── ledger.json
│   ├── world/                            # Project physics & environment rules
│   │   ├── ruleset.md
│   │   └── constraints.md
│   ├── rules/                            # Safety and git flow rules
│   │   ├── git_flow_rules.json
│   │   ├── sandbox_policy.json
│   │   └── trust_levels.json
│   └── metrics/                          # EV configuration & weights
│       ├── ev_config.json
│       └── entropy_config.json
│
├── holon-knowledge/                      # Experience / Dynamic Memory (Machine Generated)
│   ├── ledger/                           # Append-only forensic logs
│   │   ├── intents.jsonl
│   │   ├── plans.jsonl
│   │   └── events.jsonl
│   ├── plans/                            # Saved execution plan markdown files
│   ├── kb/                               # Curated knowledge base patterns
│   └── wb/                               # Universal wisdom base axioms
│
├── docs/                                 # Architectural and workflow docs
│   ├── agent_design.md
│   ├── agents.md
│   └── ...
│
├── tests/                                # Test suites
│   ├── test_holon_bootstrap.sh           # E2E bootstrap script
│   └── unit/                             # Engine unit tests
│
├── pyproject.toml                        # Global Python package configuration
└── README.md                             # Monorepo landing page
```

---

## Key Components

### 1. `apps/` and `libs/` (Monorepo Workspaces)

Codebase components are isolated based on whether they are runnable applications or shared libraries:

- **`apps/holon-cli/`**: Contains the command-line interface entry points.
- **`apps/sandbox-executor/`**: Config files and Docker runner scripts to run executions in isolated containers.
- **`libs/holon-engine/`**: The core FIEE (Fractal Intent Evolution Engine) orchestrator, worker agent protocols, and
  decision algorithms.

### 2. `holon-config/` (Static Priors / Governance)

Human-authored, static configuration defining how the system behaves and what constraints are active:

- `prompts/`: Persona descriptions and template system prompts for planning, curation, and execution roles.
- `world/`: Ruleset constraints for target code environments (e.g. constraints on libraries, programming styles,
  conventions).
- `rules/`: Core authorisation parameters, trust-level thresholds, and sandbox configuration.
- `metrics/`: Static expected value ($EV$) coefficients, calibration targets, and entropy weight models.

### 3. `holon-knowledge/` (Dynamic Experience / Memory)

Machine-authored data generated from system executions. This directory is structured into three scope layers to enable
both strict workspace isolation and cross-project knowledge sharing:

- **Project-Specific Knowledge & State (`holon-knowledge/`):**
  - `ledger/`: The forensic timeline containing all intent creations, planning variants, decision traces, and outcome
    records for this specific codebase.
  - `plans/`: The generated plan variants saved as Markdown reports.
  - `kb/` (Knowledge Base): Curated rules, tactics, internal API structures, and coding patterns specific to this
    project, extracted by the Curator Agent.
- **User/Developer-Specific Knowledge (`~/.config/holon/user/`):**
  - A shared local directory on the developer's machine (resolving to `~/.config/holon/user/`).
  - Acts as a shared user-level Wisdom Base (WB), automatically inheriting generalised patterns ascended by Curator
    Agents across all different projects worked on by the same user.
- **Universal Holon Knowledge (`~/.config/holon/universal/`):**
  - Universal truths that hold true irrespective of specific languages, frameworks, or codebases (e.g., development
    methodologies like Red-Green refactoring, clean git branching discipline, and general clean code principles).
  - This universal knowledge base is hosted in a separate dedicated Git repository (e.g.,
    `github.com/Holon-Agentic-Coder/holon-universal-knowledge`), which is cloned into `~/.config/holon/universal/` and
    pulled/synced dynamically by the Holon engine on startup.

### 4. `holon-coherence/` (Git Submodule)

Embedded optimization and wire proxy repository (`https://github.com/Holon-Agentic-Coder/holon-coherence.git`):

- Houses the MITM streaming proxy, SSE token parsing, prompt compression algorithms, and wire telemetry.
- Tracked via Git submodule (pinned commit pointer).
- For complete operational and synchronization instructions, see [`SUBMODULES.md`](../SUBMODULES.md).
