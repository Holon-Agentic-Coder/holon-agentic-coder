# agent_design.md

This document provides a high-level architectural overview and behavioural design principles for the autonomous agents
within the Holon system. It defines the governance framework, trust and autonomy models, and interaction protocols that
guide agent behaviour and system integration.

---

## 1) Agent Ecosystem Overview

The Holon system consists of multiple autonomous agents collaborating to plan, execute, learn, and evolve. Agents
operate within a fractal intent hierarchy and adhere to strict safety, trust, and operational contracts.

### Core Architecture: The Stateless Engine

All agents in Holon are **config-driven**. They are initialised with a `config_path` (defaulting to `holon-config/`) and
a `knowledge_path` (defaulting to `holon-knowledge/`). This separation ensures that governance (how to act) remains
distinct from experience (what has been learnt).

- **`holon-config/` (Static Priors):** Agent missions (`prompts/`), validation rules (`schemas/`), and project physics
  (`world/` and `metrics/`).
- **`holon-knowledge/` (Dynamic Experience):** Memory (`ledger/`), knowledge base (`kb/`), and wisdom base (`wb/`).

### Key Components

- **Meta-Agent (Orchestrator):** Config-driven coordinator that manages the intent lifecycle, agent dispatch, and system
  state.
- **Worker Agents:** Specialised agents (Planner, Executor, Curator, Evaluator, Researcher) performing tasks based on
  priors in `holon-config/` and experience in `holon-knowledge/`.
- **Knowledge Base (KB):** Project-specific repository of curated patterns, tactics, and failure modes stored in
  `holon-knowledge/kb/`.
- **Wisdom Base (WB):** Global repository of universal invariants stored in `holon-knowledge/wb/`.
- **Ledger:** Immutable event log capturing all agent actions and decisions stored in `holon-knowledge/ledger/`.

---

## 2) Agent Roles and Responsibilities

| Agent Type | Primary Role                                  | Autonomy Scope                   |
| ---------- | --------------------------------------------- | -------------------------------- |
| Meta-Agent | Orchestrates intent lifecycle and agent tasks | System-wide coordination         |
| Planner    | Generates and evaluates plan variants         | Intent-level planning            |
| Executor   | Executes plans in sandboxed environments      | Intent-level execution           |
| Curator    | Extracts patterns and failure modes           | Knowledge base curation          |
| Evaluator  | Selects best plans and manages convergence    | Planning evaluation              |
| Researcher | Proposes estimator and policy improvements    | System-level learning and tuning |

---

## 3) Trust and Autonomy Model

Agents operate under a **trust-bounded autonomy** framework, where capabilities expand with demonstrated reliability.

| Trust Level   | Capabilities                                                        | Autonomy Examples                         |
| ------------- | ------------------------------------------------------------------- | ----------------------------------------- |
| Baseline      | Execute assigned tasks, generate basic plans                        | Execute plans, generate simple variants   |
| Medium        | Spawn sub-intents, propose patterns and tactics                     | Decompose intents, propose KB entries     |
| High          | Propose root intents, novel plans, and advanced tactics             | Autonomous goal-setting, complex planning |
| Highest       | Propose estimator improvements, routing policies, and system tuning | Meta-learning, policy refinement          |
| Full (Future) | Modify core invariants and safety policies (with human approval)    | System governance and evolution           |

---

## 4) Behavioural Contracts and Safety

Agents must adhere to the following immutable contracts unless explicitly overridden by human review:

- **Sandboxing:** Agents execute only within isolated environments; no sandbox escape allowed.
- **Git Discipline:** Agents must follow mandatory rebase and merge rules.
- **Metric-Driven Decisions:** Agents use defined metrics (P(success), Entropy, Impact, LearningValue, Cost, EV) for all
  planning and execution.
- **No Core Invariant Modification:** Agents cannot autonomously change core system invariants or safety policies.
- **Human Review Boundary:** Root intents require human approval before promotion, given by the maintainer's PR merge,
  the only human step; no stage before it waits for a human. Sub-intents merge only into the branch of their deciding
  node (in practice the plan branch that listed them; an execution branch when an execution decided the split), and the
  agent raises and merges each sub-intent's PR automatically, so only the root intent's PR to `main` stops for a human.
  Until the implementation follows, the current flow still targets the parent intent branch and leaves each sub-intent
  PR merge to the maintainer (see [Git Flow](git_flow.md) Stage 3 under the flow equivalence guarantee).
- **Communication via Ledger:** All inter-agent communication and state changes are logged in the ledger; no direct
  agent-to-agent messaging.

---

## 5) Agent Interaction Protocols

### 5.1 Intent Lifecycle Management

- Meta-Agent creates and tracks intents.
- Planner agents generate multiple plan variants per intent.
- Evaluator agents select plans based on Expected Value and convergence policies.
- Executor agents run selected plans in sandboxes.
- Curator agents extract learnings and update the KB.
- Researcher agents propose system-level improvements.

### 5.2 Model Routing

- Agents select models dynamically based on intent complexity and entropy.
- Routing tiers (Flash, Medium, Deep) balance speed, cost, and capability.
- Routing decisions are logged and influence agent trust and evolution.

### 5.3 Trust Updates

- Agent trust levels are updated based on execution success, calibration accuracy, and adherence to contracts.
- Trust changes affect autonomy scope and task assignments.

---

## 6) Autonomy Escalation and Earned Autonomy

Agents start with limited capabilities and earn higher autonomy by demonstrating:

- Consistent success in assigned tasks.
- Accurate metric predictions and low calibration error.
- Responsible spawning and merging of sub-intents.
- Compliance with safety and operational contracts.

Autonomy escalation enables:

- Recursive intent decomposition without human intervention.
- Autonomous root intent generation (subject to trust gates).
- Proposal of estimator and policy improvements.

---

## 7) Agent Lifecycle

1. **Initialisation:** Agents are instantiated with an `agent_id`, `model`, `trust_level`, `config_path` (defaulting to
   `holon-config/`), and `knowledge_path` (defaulting to `holon-knowledge/`).
2. **Configuration & Knowledge Loading:** Agents use a `ConfigLoader` to fetch their local project priors and a
   `KnowledgeLoader` to fetch their historical memory and universal wisdom.
3. **Task Assignment:** Meta-Agent dispatches intents and plans based on agent capabilities.
4. **Execution:** Agents perform tasks, log results, and update metrics.
5. **Learning:** Agents update internal models and propose KB entries or system improvements.
6. **Trust Evaluation:** Agent trust is recalculated and autonomy adjusted.
7. **Retirement:** Agents may be retired or upgraded based on performance and system needs.

---

## 8) Summary

This design framework ensures that Holon’s agents operate safely, efficiently, and collaboratively within a governed
ecosystem. Trust-bounded autonomy and rigorous behavioural contracts enable scalable, recursive, and self-improving AI
agent behaviour while maintaining human oversight where necessary.

---

## Related Documents

- [`agents.md`](agents.md) — Detailed agent type specifications and code examples
- [`git_flow.md`](git_flow.md) — Git branching and merging discipline
- [`core_concepts.md`](core_concepts.md) — Fundamental terminology and metrics
- [`safety.md`](safety.md) — Safety policies and sandboxing
- [`ledger_schema.md`](ledger_schema.md) — Event logging and communication protocols
- [`architecture.md`](architecture.md) — Overall system architecture and data flow
