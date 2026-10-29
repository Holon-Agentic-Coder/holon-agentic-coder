# core_concepts.md

This document defines the fundamental concepts, terminology, and core metrics used throughout the Holon self-evolving AI
agent system. It establishes a shared vocabulary and formalises the contracts that govern agent behaviour and system
operation.

---

## The Project as a Unique World: The Stateless Engine

Each project is effectively its own **unique world** defined by its **Governance** (how it should act) and its
**Experience** (what it has learnt).

In Holon, this is formalised through a **Stateless Engine** architecture using two mandatory directories:

### 1) `holon-config/` (Static Priors)

This directory acts as the **"Constitutional Brain"** for Holon, containing human-authored rules and physics:

- **Rule Set:** Language versions, coding conventions, and API contracts (in `world/ruleset.md`).
- **Constraints:** Security policies and git flow rules (in `world/constraints.md`).
- **Physics Constants:** EV weights and estimator parameters (in `metrics/`).

### 2) `holon-knowledge/` (Dynamic Experience)

This directory acts as the **"Evolving Memory"** for Holon, containing machine-authored intelligence:

- **Ledger:** The append-only event log of every action.
- **Knowledge Base (KB):** Project-specific patterns, tactics, and failure modes.
- **Wisdom Base (WB):** Universal invariants and cross-project heuristics that meta-evolve over time.

### Why This Matters

Foundational models provide broad priors but cannot fully capture the project-specific "physics" of these unique worlds.
By externalising these into `holon-config/` and `holon-knowledge/`, Holon agents become **config-driven**. They are
initialised with a `config_path` and a `knowledge_path` and load their missions, constraints, and "physics" from these
external brains. Each project world is unique and dynamic, evolving over time as rules and constraints change.

This ensures agents remain stateless and portable while their behaviour is governed by both the specific, evolving rules
of the project and the universal wisdom of the engine.

Holon’s agents must therefore **learn, adapt, and evolve within the context of these unique worlds**. They do this by
generating hypotheses (plans and intents) that test assumptions about the rule set and constraints, learning from
outcomes, and updating their internal models accordingly.

This recursive, fractal-like exploration and adaptation is the core mechanism that enables Holon to bridge the gap
between general foundational knowledge and project-specific expertise, delivering solutions tailored to the unique
physics of each project world.

---

## 1) Intent Ontology

### 1.1 Intent

An **Intent** is a discrete unit of work or goal that an agent or the system aims to accomplish. It encapsulates a
specific task, objective, or problem to solve.

- Each intent has a unique identifier.
- Each intent corresponds to a dedicated Git branch.
- Intents can be decomposed into smaller intents to manage complexity.

### 1.2 Root Intent

A **Root Intent** is a top-level intent created either by a human or an autonomous agent with earned autonomy.

- It represents a major goal or project.
- Requires human review before merging into the canonical `main` branch.
- All other intents in its hierarchy eventually reach it: each merges into its deciding node's branch, and the root's
  deciding plan branch is what its PR carries to `main`.

### 1.3 Parent Intent

A **Parent Intent** is the immediate parent of a sub-intent in the intent hierarchy.

- Owns its sub-intents through its deciding plan (or execution): each sub-intent runs autonomously through the full
  pipeline, and the agent raises its PR and merges it automatically into that deciding branch, with no human
  intervention (see [Git Flow](git_flow.md) section 4.1).
- May itself be a sub-intent of a higher-level intent.
- Exists at any depth except the root level.

### 1.4 Sub-Intent

A **Sub-Intent** is an intent spawned from another intent to break down complex work into manageable parts. In practice
the split is decided by one of the parent intent's plans, which sits between the parent and its sub-intents in the
hierarchy.

- Is cut from, and merges only into, its deciding node's branch (in practice the plan branch): it runs autonomously
  through the full flow, and the agent raises its PR and merges it automatically, whether it is a leaf or has its own
  sub-intents. Only the root intent's PR to `main` stops for a human (see [Git Flow](git_flow.md) sections 4.1-4.2).
- Has a slug different from every sibling under the same deciding node, so sibling ids cannot collide.
- Can have its own sub-intents, forming a fractal hierarchy up to `MAX_SUB_INTENT_DEPTH` levels below the root.

### 1.5 Understanding Fractal Recursion (Why it matters)

**What it means:** Fractal recursion in Holon means that tasks look and behave the same way regardless of size or depth.
A massive, high-level feature request (e.g., "Implement OAuth") and a tiny refactoring task (e.g., "Add test for
helper") are both modeled as **Intents**. Each has a git branch, competitive planning phase, execution sandbox, and
performance metrics.

**Why we use it (Motivation):**

1. **Dynamic Scaling:** A single centralized planner cannot predict details for a large project without hallucinating or
   making errors. Under fractal recursion, agents plan one level down at a time, recursively decomposing complex intents
   into smaller sub-intents only as they are discovered.
2. **Blast Radius Control:** By isolating sub-intents to their own branches and sandboxes, bugs or failed approaches
   only affect that local branch. The parent or sibling intents remain untouched and safe.
3. **Calibrated Delegation:** Higher-trust agents can propose and manage root intents, while baseline-trust agents are
   restricted to executing simple leaf-node sub-intents.

---

## 2) The Value Calculus

### 2.1 Probability of Success ($P(success)$)

- The estimated likelihood that an intent or plan will achieve its goal.
- Calculated pre-execution using a weighted combination of plan features.
- Updated post-execution based on actual outcomes for calibration.

### 2.2 Impact

- The expected positive effect or benefit delivered by completing the intent.
- Measured in domain-specific units (e.g., performance gain, cost savings).
- Used to prioritise intents with higher value.

### 2.3 Cost

- The resources, time, or effort required to execute the intent.
- Includes computational cost, human review time, and entropy introduced.
- Helps balance benefit against expenditure.

### 2.4 Learning Value

- The epistemic gain produced by executing an intent, independent of success or failure.
- It exists to stop the system from over-penalising exploration, and to justify spikes/failure-mode discovery when
  bounded by entropy budgets.
- Learning Value is the expected knowledge gain from running the plan, including new constraints discovered, new failure
  modes, and reusable KB entries.

---

## 3) The Entropy Framework - Disorder, Novelty, and Stability

At its core, entropy measures the **degree of disorder, uncertainty, and novelty** introduced by an intent or plan
within the unique project world. It quantifies how much a plan disrupts the current stable state — introducing
complexity, unpredictability, or divergence from known patterns.

Think of entropy as the "amount of surprise or disruption" a plan causes. Just as physical entropy measures disorder in
a thermodynamic system, Holon's entropy measures disorder in the informational and operational state of the project.

Managing entropy is crucial:

- **Too much entropy** leads to chaos, instability, and risk of failure.
- **Too little entropy** leads to stagnation, lack of innovation, and missed opportunities for improvement.

Entropy is distinct from a simple risk score or probability of failure. It captures both known risks and the unknown
unknowns — the hidden complexities and emergent behaviours that can arise in a complex project environment.

By explicitly measuring and budgeting entropy, Holon agents can safely explore novel strategies while maintaining
overall system stability, enabling a balanced evolution of the project world.

### 3.1 Intent Entropy ($\Delta S_{intent}$ or $\Delta S$)

- A measure of disorder, uncertainty, or risk **introduced by a single intent or action**.
- Quantifies how much an intent’s changes increase unpredictability or instability locally.
- Modeled as a weighted sum of five key components:
  - **SSA** (State Surface Area)
  - **IRR** (Irreversibility)
  - **CL** (Conflict Likelihood)
  - **SER** (Sandbox Escape Risk)
  - **NOV** (Novelty)
- For detailed definitions of these factors, see the [Appendix](appendix.md).
- Used to evaluate and rank intents during planning and merging.

### 3.2 System Entropy ($S_{system}$)

- A global measure of disorder or instability **across the entire system state**.
- Aggregates entropy contributions from all active intents, branches, and unresolved conflicts.
- Reflects the overall health and stability of the system.
- High system entropy indicates a fragmented, unstable, or risky state that requires maintenance.

#### 3.2.1 Why System Entropy is Needed (Motivation)

While Per-Intent Entropy ($\Delta S_{intent}$) measures the local risk of running a _single_ plan, System Entropy
($S_{system}$) tracks the _systemic debt_ of the repository:

1. **Branch Divergence:** If many agents work on separate branches simultaneously, they diverge from the main codebase.
   System Entropy aggregates this drift; as branches age or proliferate, $S_{system}$ rises, signaling that the system
   is growing unstable.
2. **Coordinated Focus (Circuit Breaking):** If system entropy exceeds the project's budget, the orchestrator triggers a
   circuit breaker that blocks starting new feature work, forcing agents to clean up stale branches, resolve merge
   conflicts, and rebase, bringing the repository back to a stable state.

- Managed by:
  - Pruning or discarding high-entropy intents
  - Merging completed intents to reduce branch divergence
  - Resolving conflicts and rebasing frequently
  - Enforcing entropy budgets to limit exploration risk

### 3.3 Relationship Between Intent and System Entropy

- System entropy is the **sum or aggregation** of all individual intent entropies plus additional systemic factors (
  e.g., unresolved conflicts, stale branches).
- Controlling system entropy requires managing the entropy of individual intents and their interactions.
- Agents must balance exploration (which increases intent entropy) with system stability (which requires keeping system
  entropy within bounds).

### 3.4 Entropy Budget and Recursive Recalculation

- **System Stability Limit:** A predefined limit on allowable system entropy that ensures the project world remains
  stable, navigable, and manageable.
- **Branch Pruning and Maintenance:** System entropy accounts for the proliferation of old, stale, or merged branches.
  When the budget is exceeded, the system triggers automated pruning of the branch namespace to reduce disorder.
- **Constraint-Driven Actions:** Intents or plans that would cause system entropy to exceed the budget are subject to:
  - **Pruning or Discarding:** High-entropy or low-value branches are abandoned and deleted.
  - **Execution Delay:** Work is paused until system maintenance (e.g., merging or pruning) reduces entropy.
  - **Fractal Decomposition:** Complex intents are broken down into smaller, lower-entropy sub-intents to distribute
    risk.
- **Recursive Upward Propagation:** Entropy is not static. Each time a child intent's entropy is merged or updated, the
  parent intent's entropy and the entropy of all ancestors up to the root intent is automatically recalculated. This
  ensures that the "blast radius" of changes is accurately reflected throughout the entire intent hierarchy in
  real-time.

---

## 4) The Decision Engine

### 4.1 Expected Value ($EV$)

- Combines $P(success)$, Impact, Cost, Learning Value, and Per-Intent Entropy ($\Delta S_{intent}$) into a single
  metric.
- **Wisdom-Guided:** The fundamental formula logic and "meta-physics" are defined in the Wisdom Base (
  `holon-knowledge/wb/`).
- **Config-Driven:** The weights and coefficients used in the formula are loaded from `holon-config/metrics/`.
- Formula (conceptual):

$$EV = P(success)\cdot Impact + \mu\cdot LearningValue - \lambda \cdot \Delta S_{intent} - Cost$$

- Guides autonomous decision-making, planning, and merging.
- $\mu$ (Learning Value weight) and $\lambda$ (Entropy weight) are defined in the external config.

---

## 5) The Intent Lifecycle State Machine

### 5.1 States

| State     | Description                                                                                  |
| --------- | -------------------------------------------------------------------------------------------- |
| Proposed  | Intent has been created but not yet executed.                                                |
| Executing | Intent is currently being worked on.                                                         |
| Completed | Intent execution finished successfully.                                                      |
| Failed    | Intent execution finished with failure.                                                      |
| Merged    | Intent changes merged into the deciding node's branch (sub-intents) or `main` (root intent). |
| Discarded | Intent abandoned and branch closed.                                                          |

### 5.2 Transitions

- **Proposed → Executing**: When an agent starts working on the intent.
- **Executing → Completed/Failed**: Based on execution outcome.
- **Completed → Merged**: Automatically for sub-intents (into the deciding node's branch, for leaf sub-intents and
  nested parents alike); by the maintainer's PR merge only for the root intent, the only human step (see
  [Git Flow](git_flow.md) sections 4.1-4.2).
- **Failed → Discarded**: Failed intents are pruned but may contribute learnings.
- **Any → Discarded**: Intent may be discarded due to low value or high entropy.

## 6) Convergence

In this system, **convergence** of a plan or intent means that the planning process has reached a point where further
generation and evaluation of new plan variants is no longer expected to yield significantly better options. In other
words, the planner and evaluator agree that the current best plan(s) are good enough to proceed to execution.

### Key points about convergence:

- **Stopping Criterion:**  
  Convergence is a formal stopping condition for the planning phase. It signals that the system should stop exploring
  new plan variants and select the best candidate for execution.

- **Based on Metrics:**  
  Convergence decisions rely on metrics like Expected Value (EV), entropy, planning cost, and improvement trends. For
  example, if the EV improvements between new variants become negligible (an EV plateau), or if one plan clearly
  dominates others by a significant margin, the system considers the plan converged.

- **Entropy and Budget Constraints:**  
  Convergence also occurs if the planning process risks exceeding entropy or resource budgets, preventing runaway
  exploration that could destabilise the system.

- **Types of Convergence Triggers:**
  - **Dominant Plan:** One plan’s EV is sufficiently higher than all others.
  - **EV Plateau:** Recent new plans do not improve EV meaningfully.
  - **Budget Exhaustion:** Planning cost or entropy budget is reached.
  - **Max Variants or Time Limit:** Hard limits on planning iterations or duration.

- **Outcome:**  
  Once converged, the best plan is selected and passed to the executor agent for sandboxed execution.

### Why is convergence important?

- **Efficiency:** Prevents endless planning loops, saving compute and time.
- **Stability:** Avoids excessive entropy and system instability from over-exploration.
- **Decision Confidence:** Provides a principled way to decide when enough information is available to act.

---

## 7) Summary Table of Core Terms

| Term                                     | Definition                                                              | Role in System                                                                            |
| ---------------------------------------- | ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Intent                                   | Unit of work or goal                                                    | Basic building block                                                                      |
| Root Intent                              | Top-level intent requiring human review                                 | Human review boundary                                                                     |
| Parent Intent                            | Immediate parent of a sub-intent                                        | Receives its sub-intents' merges through its deciding plan or execution branch            |
| Sub-Intent                               | Intent spawned from another intent by its deciding plan                 | Runs autonomously; its PR is merged automatically into the deciding node's branch         |
| $P(success)$                             | Probability intent will succeed                                         | Guides planning and merging decisions                                                     |
| Impact                                   | Expected benefit delivered                                              | Prioritisation metric                                                                     |
| Cost                                     | Resources required                                                      | Balances benefit vs expenditure                                                           |
| Learning Value                           | Epistemic gain delivered by executing an intent, independent of success | Encourages bounded exploration, accelerates estimator calibration, and promotes KB growth |
| Per-Intent Entropy ($\Delta S_{intent}$) | Measure of local disorder or risk introduced by a specific plan         | Evaluates planning risk and ranks plan variants                                           |
| Expected Value ($EV$)                    | Combined metric for decision-making and selection                       | Drives autonomous agent behaviour                                                         |
| Convergence                              | Condition when planning sufficiently explores and selects a best plan   | Signals planning termination and readiness for execution                                  |

---

This document forms the semantic foundation for the Holon system. All agents and humans should refer to it to ensure
consistent understanding and operation.

---
