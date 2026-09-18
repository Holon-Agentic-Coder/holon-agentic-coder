# Holon: Agentic Coder

**Git-native, sandbox-isolated, self-evolving agentic coding architecture** built around **fractal (recursive)
intents**, **competitive planning variants**, **append-only learning**, and **entropy-aware decision-making**.

Repository:
[https://github.com/Holon-Agentic-Coder/holon-agentic-coder](https://github.com/Holon-Agentic-Coder/holon-agentic-coder)

---

## What is Holon?

A **holon** is something that is simultaneously a **whole** and a **part**. In this project:

- An **Intent** is a whole task to the agent working on it.
- The same Intent is also a part of a larger parent Intent.
- An **Agent** is a whole autonomous actor, and also a part of a wider evolving population.

**Holon: Agentic Coder** is a blueprint (and eventual reference implementation) for autonomous coding agents that can:

- decompose work recursively,
- generate multiple plans for the same intent,
- choose actions via explicit decision physics (success probability + entropy + EV),
- execute in isolated sandboxes,
- record _everything_ in an append-only ledger,
- promote durable learnings into a curated knowledge base,
- and (long-term goal) propose their **own** intents and evaluate intent quality—earning increasing autonomy over time.

---

## Why this exists (origin story, in engineering terms)

Most agentic coding systems fail in predictable ways:

1. **State contamination:** exploration corrupts the working tree, environments drift, and "it worked once" can't be
   reproduced.
2. **Planning amnesia:** plans are ephemeral; the system cannot learn which planning strategies work.
3. **No causal trace:** actions are taken without a durable "why/how/what happened" record.
4. **Human review overload:** humans are asked to review noisy micro-changes rather than synthesised parent-level
   outcomes.
5. **No comparable variants:** there's no fair way to compare plan A vs plan B vs model choice A vs model choice B.
6. **Hidden routing:** switching between models (fast/cheap vs deep/reasoning) is treated as invisible magic instead of
   a measurable decision.

Holon is designed to make these failure modes structurally difficult by:

- using **Git as the universal state machine**,
- isolating exploration via **one sandbox per sub-intent**,
- capturing _intent → plan → action → result_ into an **append-only Evolution Ledger**,
- treating planning as a **versioned hypothesis space**,
- and making model routing and exploration risk explicit via **entropy-aware metrics**.

---

## Important Notice

Before diving deep into this README, we **strongly urge** all readers to first read the
[`core_concepts.md`](docs/core_concepts.md) document. It contains essential foundational information, terminology, and
principles that are critical for fully understanding the architecture, workflows, and design decisions described here.

Specifically, it introduces:

- **Fractal Intents:** Recursive task decomposition representing work as a tree of intents (goals) and sub-intents down
  to atomic actions.
- **Competitive Planning:** Generating and comparing multiple distinct plan hypotheses (variants) for a single intent.
- **Expected Value ($EV$):** The core decision metric used to rank plans, balancing success probability ($P(success)$),
  value, cost, and risk.
- **The Entropy Framework:** Measuring local plan complexity and risk ($\Delta S_{intent}$) and global system disorder
  ($S_{system}$) to enforce safety limits.
- **The Intent Lifecycle State Machine:** Tracking intents from proposed to executing, completed, failed, merged, or
  discarded states.

Please take the time to review `core_concepts.md` carefully to get the most out of this project.

---

## Core principles (non-negotiable invariants)

### 1) Intent is fractal (recursive, unbounded depth)

There is no fixed number of levels. Any intent may spawn sub-intents, which may spawn sub-sub-intents, until leaf nodes
become **atomic intents** (direct actions).

### 2) Planning is first-class and competitive

For the same intent, multiple **planning variants** exist simultaneously (v1, v2, v3...). They are competing hypotheses,
not edits.

### 3) Exploration is allowed because it's isolated

Low-probability and risky exploration is permitted because branches and sandboxes can be discarded. The system learns
from outcomes without polluting the mainline.

### 4) Git is the universal state machine

Git provides immutable history, causal lineage, reproducibility, and cheap branching as isolation.

### 5) Strict parent-centric Git flow

- **Sub-intents merge only into their parent intent branch**
- **Never directly to `main`**
- Only **parent intents** are presented to humans for review and promotion to `main`

### 6) Mandatory dual rebase gates

Sub-intents must rebase from the latest remote parent:

- **before** execution (to avoid stale work)
- **after** execution (to resolve conflicts at the smallest semantic scope)

### 7) "Physics before intelligence"

Holon starts with weak-but-stable definitions of:

- **P(success)** (success prediction)
- **ΔS** (entropy / divergence / blast radius)
- **EV** (expected value)

These must be **comparable and explainable** before any "learning" can be meaningful.

### 8) Model routing is a first-class decision

Agents may route tasks to different models (e.g., Gemini Flash vs DeepThink). Routing choice must be:

- recorded,
- evaluated,
- and improved via selection pressure (Routing ROI).

### 9) Unique project worlds require autonomous evolution

Each project is effectively its own **unique world** with distinct rules, constraints, and dynamics. Foundational models
provide powerful priors but cannot fully capture the infinite variety of possible project worlds.

True progress arises from **autonomous exploration and evolution within these unique rules**. Agents use foundational
models as starting hypotheses but actively experiment, learn from failures, and evolve domain-specific strategies
tailored to the project’s context.

This recursive, fractal-like learning process is the core of Holon’s value proposition, shifting the focus from scaling
models alone to building self-improving agentic systems that adapt within their own environments.

### 10) Earned autonomy (long-term)

Agents should eventually propose their own intents and spawn sub-intents recursively—but autonomy is **earned** through
demonstrated intent quality and calibration.

---

## Quick mental model

Holon operates as a **Stateless Engine** using two durable stores:

- **`holon-config/` (Static Priors):** the human-authored "Constitutional Brain" (prompts, world context, safety rules,
  metric weights).
- **`holon-knowledge/` (Dynamic Experience):** the machine-authored "Evolving Memory" (append-only ledger, curated
  knowledge base, universal wisdom base).

---

## High-level architecture

```mermaid
flowchart TB
    H[Human / Root Goals] --> I[Intent Node<br>#40;fractal recursion#41;]
I --> PV[Planning Variant Pool<br>v1, v2, v3...]
PV --> PE[Plan Evaluation<br>pre + post metrics]
PE --> D[Decision Engine<br>P#40;success#41;, ΔS, EV]
D --> MR[Model Router<br>fast vs deep tiers]
MR --> SI[Sub-Intent Expansion]
SI --> RB1[Rebase Gate 1<br>#40;pre-execution#41;]
RB1 --> SX[Sandbox Execution<br>#40;1 sandbox per sub-intent#41;]
SX --> RB2[Rebase Gate 2<br>#40;post-execution#41;]
RB2 --> MP[Merge ONLY to Parent]
MP --> L[Evolution Ledger<br>append-only]
MP --> KB[Knowledge Base<br>curated + versioned]
L --> SEL[Selection Pressure<br>#40;plan fitness, routing ROI#41;]
KB --> SEL
SEL --> PV
MP --> HR[Human Review<br>Parent intent only]
HR --> MAIN[Merge to main]
```

---

## Git flow (summary)

Holon enforces a strict Git discipline to ensure isolation, reproducibility, and clean human review boundaries.

**Key rules:**

- Sub-intents branch from their parent intent (never from `main`)
- Sub-intents merge **only** into their parent intent (never directly to `main`)
- Sub-intents must rebase from parent **before** and **after** execution
- Only parent intents are reviewed by humans and merged to `main` (short-term goal)

**For complete details**, see [`docs/git_flow.md`](docs/git_flow.md).

---

## Naming conventions (IDs & branches)

Holon treats identity as explicit and hierarchical. Identity is the primary key for the Evolution Ledger.

### Intent ID

An intent is generated by a specific model. Multiple models may generate competing intent proposals concurrently:

- `I-{timestamp}-{slug}`
- Example: `I-1771890389-refactor-metrics`

### Plan ID

A plan is a versioned variant tied to an intent and the model tier used to produce it:

- `P-{timestamp}-{agent}-{model}`
- Example: `P-1771890396-claude-code-claude-opus-4.6`

_Note: This example shows a "Deep" model planning for an intent that was originally proposed by a "Flash" model._

### Branch naming (Fractal Nesting)

Holon uses a filesystem-like branch structure to represent the intent hierarchy. Sub-intents are nested under their
parent's path.

#### Root Intent Branch

```
I-{timestamp}-{slug}/_
```

Example: `I-1771890389-refactor-metrics/_`

#### Sub-Intent Branch (Nested)

```
{root_intent}/I-{timestamp}-{slug}
```

Example: `I-1771890389-refactor-metrics/I-1771890390-improve-estimators/_`

#### Plan Branch (Execution)

Work derived from a specific plan variant occurs on a plan branch nested under the intent:

```
I-1771890389-refactor-metrics/P-{timestamp}-{agent}-{model}
I-1771890389-refactor-metrics/E-{timestamp}-{agent}-{model}
```

Example Plan Branch: `I-1771890389-refactor-metrics/P-1771890396-claude-code-claude-opus-4.6`  
Example Execution Branch: `I-1771890389-refactor-metrics/E-1771890398-opencode-big-pickle`

---

## Metrics ("physics") at a glance

Holon uses simple, stable metrics early, then evolves better estimators later.

### Success prediction (P(success))

Probability that executing a plan advances the intent without violating constraints:

$$P(success) \in [0,1]$$

**Tracked:**

- **Pre-execution (predicted):** estimated before the plan runs
- **Post-execution (actual):** measured after completion (1.0 if success, 0.0 if failure)

### Entropy (ΔS)

Expected divergence / blast radius / irreversibility introduced by the plan:

$$\Delta S \in [0,\infty)$$

Entropy is a **cost signal**, not "badness". Higher entropy means:

- More files/systems touched
- More irreversible changes
- Higher conflict likelihood
- Greater novelty/uncertainty

**Tracked:**

- **Pre-execution (predicted):** estimated based on plan analysis
- **Post-execution (actual):** measured from git diff, test coverage changes, dependency graph changes

### Expected value (EV)

Combines impact, success likelihood, learning value, entropy, and cost:

$$EV = P(success) \cdot Impact + \mu \cdot LearningValue - \lambda \cdot \Delta S - Cost$$

Where:

- **Impact:** estimated value delivered if the intent succeeds
- **LearningValue:** estimated epistemic gain delivered to the system, independent of success
- **μ (mu):** learning value weight (tunable, typically 0.1–1.0)
- **λ (lambda):** entropy penalty weight (tunable, typically 0.1–1.0)
- **Cost:** resource consumption (tokens, time, compute)

**Used for:**

- Selecting between competing plans
- Deciding whether to continue planning (convergence)
- Evaluating agent/model performance over time

### Calibration

The system tracks **prediction accuracy** by comparing pre-execution estimates to post-execution measurements:

- **P(success) calibration:** Are agents overconfident? Underconfident?
- **ΔS calibration:** Are entropy estimates accurate?
- **Impact calibration:** Did the intent deliver expected value?

Poor calibration reduces an agent's trust level and influences future routing decisions.

**For complete formulas and bootstrap definitions**, see [`docs/metrics.md`](docs/metrics.md).

---

## Planning variants & convergence (when to stop planning)

Planning is not allowed to loop forever. For any given intent, the system may generate multiple **planning variants**
(v1, v2, v3...), but must eventually **converge** and select a plan to execute.

### Why multiple variants?

Different planning approaches may yield different tradeoffs:

- **v1:** conservative, low entropy, incremental
- **v2:** aggressive, higher entropy, faster impact
- **v3:** novel approach, untested but potentially superior

Variants are **competing hypotheses**, not iterative edits.

### Convergence triggers (when to stop generating new plans)

Planning stops when any of the following conditions is met:

#### 1. EV plateau

No meaningful improvement in Expected Value after N consecutive variants:

```
if EV(v_n) - EV(v_n-1) < threshold for N iterations:
stop planning
```

Default: `threshold = 0.05`, `N = 3`

#### 2. Entropy budget exhausted

Cumulative planning cost (tokens, time, or entropy) exceeds the allocated budget:

```
if sum(planning_cost(v1...vn)) > planning_budget:
stop planning
```

Planning itself consumes resources and must be bounded.

#### 3. Dominant plan found

A plan achieves sufficiently high P(success) at acceptable ΔS:

```
if P(success) > 0.85 and ΔS < acceptable_threshold:
stop planning (high confidence)
```

#### 4. Planning cost exceeds expected return

Continuing to plan costs more than the expected gain:

```
if cost_of_next_variant > expected_EV_improvement:
stop planning (diminishing returns)
```

### Plan selection (after convergence)

Once planning stops, the system selects the plan with the **highest EV**:

```
selected_plan = argmax(EV(v1), EV(v2), ..., EV(vn))
```

All variants and their scores are recorded in the **Evolution Ledger** for future learning.

### Multi-model planning (optional)

Different models may generate plans for the same intent concurrently:

- Gemini Flash generates v1 (fast, cheap)
- Claude Sonnet generates v2 (deeper reasoning)
- Gemini DeepThink generates v3 (complex decomposition)

This enables **model routing selection pressure**: which model produces better plans for which types of intents?

**For detailed convergence policy implementation**, see [`docs/architecture.md`](docs/architecture.md) and
[`docs/metrics.md`](docs/metrics.md).

---

## Dynamic model routing (captured + evolved)

Holon treats **model selection as a first-class decision** with measurable outcomes. Different LLMs (and tiers of the
same LLM) may be used for different stages, including:

- intent proposal (including competing intent proposals from multiple models)
- plan generation (multiple planning variants per intent, potentially multi-model)
- execution assistance (coding, refactoring, test fixing)
- evaluation and write-up (summaries, ledger/K.B. promotion candidates)

### Why routing matters

Routing is a tradeoff across:

- capability (reasoning depth, code quality)
- latency (time to first token / completion)
- cost (tokens, $)
- risk/entropy (blast radius and irreversibility)
- novelty (unfamiliar repo areas / dependencies)

Holon makes routing explicit so routing itself can be **improved over time**.

### Routing inputs (signals)

A routing decision should be explainable and based on observable signals, such as:

- **intent complexity:** tree depth, dependency count, ambiguity
- **predicted entropy (ΔS):** estimated blast radius of the plan
- **success sensitivity:** how costly failure is (production risk, irreversibility)
- **novelty:** how unfamiliar the code region is to the system/K.B.
- **budget constraints:** time, tokens, compute limits
- **agent trust level:** permitted entropy budget and recursion depth

### Routing outputs (recorded metadata)

Every routed action records metadata into the **Evolution Ledger**, including:

- `model_id` (e.g., `gemini_flash`, `gemini_deepthink`, `claude_sonnet`)
- `agent_family` (e.g., `gemini`, `claude`)
- `routing_reason` (short explanation + key signals)
- `stage` (intent, planning, execution, evaluation)
- `token_usage`, `latency_ms`, `inference_cost_est`
- predicted vs actual outcomes (P(success), ΔS, EV impact)

This makes routing decisions **auditable** and **comparable**.

### Multi-model concurrency

Holon supports multiple models operating concurrently on the same parent intent, for example:

- competing intent proposals from different models
- parallel plan variants generated by different models
- execution assistance escalated from a fast model to a deep model when stuck

Concurrency is controlled via:

- budgets (token/time/entropy)
- convergence rules (stop when EV plateaus or dominant plan emerges)
- strict Git isolation (no direct merges to `main`)

### Routing evolution (selection pressure)

Holon improves routing by measuring which routes produce better outcomes under constraints.

Key evolving metrics include:

- **Routing ROI:** outcome quality per unit cost/latency
- **Calibration:** how accurate each model is at predicting P(success) and ΔS
- **Failure modes:** where specific models systematically underperform
- **Stage-fit:** which models do best at intent generation vs planning vs execution

Over time, the system learns:

- which model tiers are best for which intent types,
- when escalation is justified,
- and when cheap/fast planning is sufficient.

**For detailed routing policy and scoring**, see [`docs/architecture.md`](docs/architecture.md) and
[`docs/metrics.md`](docs/metrics.md).

---

## Autonomous intent generation (long-term)

Holon aims for agents that can **propose their own intents** and (eventually) **spawn recursive sub-intent trees**
without human initiation.

This is the most powerful—and most dangerous—capability in the system. It must be **earned**, not granted by default.

### Why autonomous intent generation?

In a fully autonomous system, agents should be able to:

- observe the codebase, ledger, and K.B.
- identify improvement opportunities (refactoring, tech debt, optimisation, new features)
- propose intents with justification and predicted metrics
- decompose those intents into sub-intents recursively
- execute and learn from outcomes

This closes the loop: agents evolve the system **and** evolve themselves.

### The risk (why it must be earned)

Unconstrained autonomous intent generation can lead to:

- **runaway recursion:** agents spawn infinite sub-intents
- **misaligned goals:** agents optimise for the wrong objectives
- **resource exhaustion:** entropy budgets blown on low-value work
- **sandbox escape attempts:** agents propose intents to modify validation rules
- **chaotic exploration:** high-entropy changes with no learning value

### Earned autonomy via trust levels

Agents progress through **capability tiers** based on demonstrated performance:

| Level | Name           | Capabilities                                 | Unlock Criteria                                         |
| ----- | -------------- | -------------------------------------------- | ------------------------------------------------------- |
| **0** | **Observer**   | Read ledger, read K.B.                       | Default                                                 |
| **1** | **Proposer**   | Propose atomic intents (no sub-intents)      | 10 successful executions                                |
| **2** | **Decomposer** | Propose intents with 1 level of sub-intents  | Intent Quality > 0.7 for 20 proposals                   |
| **3** | **Architect**  | Propose intents with 2 levels of sub-intents | Intent Quality > 0.8 for 50 proposals                   |
| **4** | **Autonomous** | Unlimited recursion depth                    | Intent Quality > 0.85 for 100 proposals                 |
| **5** | **Meta**       | Can propose changes to validation rules      | Intent Quality > 0.9 for 200 proposals + human approval |

**Trust levels are per-agent** and can be **demoted** if quality degrades.

### Intent proposal mechanism

When an agent proposes an intent, it must include:

```json
{
  "proposal_id": "PROP-1738901234-x7y9",
  "proposed_by": "agent-gemini-deepthink-001",
  "agent_trust_level": 3,
  "intent_proposal": {
    "goal": "Implement caching layer for ledger reads",
    "justification": "Ledger reads are 60% of query time; caching can reduce by 80%",
    "predicted_impact": 0.85,
    "predicted_p_success": 0.75,
    "predicted_entropy": 25,
    "predicted_ev": 0.51,
    "decomposition": {
      "type": "recursive",
      "depth": 2,
      "sub_intents": [
        {
          "goal": "Design cache invalidation strategy",
          "predicted_entropy": 8,
          "sub_intents": []
        },
        {
          "goal": "Implement Redis cache adapter",
          "predicted_entropy": 12,
          "sub_intents": []
        }
      ]
    }
  }
}
```

### Proposal validation (gates)

Every proposal goes through validation checks:

#### 1. Recursion depth check

Agent's trust level determines max allowed sub-intent depth:

```python
if proposed_depth > TRUST_LEVEL_MAX_DEPTH[agent.trust_level]:
    reject("Agent level {level} limited to depth {max_depth}")
```

#### 2. Entropy budget check

Total tree entropy must fit within agent's budget:

```python
total_entropy = calculate_tree_entropy(proposal.decomposition)
if total_entropy > agent.max_entropy_budget:
    reject("Total tree entropy {total} exceeds budget {budget}")
```

#### 3. Alignment check

Sub-intents must be aligned with parent goal and system objectives.

#### 4. Novelty check

Proposal must not duplicate recent intents or violate known anti-patterns from K.B.

#### 5. Meta-proposal gate

Any proposal targeting the validation system itself requires **Level 5 + human approval**.

### Intent quality scoring (how agents level up)

After an intent completes, it receives an **Intent Quality Score** based on:

- **Impact accuracy:** Did it deliver predicted value?
- **P(success) calibration:** Was the success prediction accurate?
- **Entropy calibration:** Was the entropy estimate accurate?
- **Alignment:** Did it advance parent/system goals?
- **Efficiency:** Resource usage vs outcome quality

Agents with consistently high Intent Quality Scores earn higher trust levels and larger budgets.

### Safety mechanisms

#### Immutable core rules

Certain validation rules **cannot be modified** by agent proposals:

- max recursion depth per trust level
- entropy budget caps
- human approval requirement for Level 5
- proposal rate limits

#### Audit trail

All proposals, validations, and outcomes are logged in the **Evolution Ledger**.

#### Demotion

Agents whose quality degrades are **demoted** to lower trust levels, reducing their autonomy.

### Bootstrap path (phased rollout)

Autonomous intent generation is introduced gradually:

**Phase 1:** Human-only intent creation (bootstrap)  
**Phase 2:** Agents propose atomic intents (Level 1)  
**Phase 3:** Agents propose single-level decomposition (Level 2)  
**Phase 4:** Agents propose multi-level trees (Level 3-4)  
**Phase 5:** Exceptional agents propose meta-changes (Level 5, human-gated)

**For complete trust ladder, validation rules, and scoring formulas**, see [`docs/safety.md`](docs/safety.md) and
[`docs/architecture.md`](docs/architecture.md).

---

## Status

This repository is intended to evolve from **concept + spec** into a **reference implementation**.

Current focus (recommended build order):

1. Core ID + naming + Git invariants
2. Append-only ledger schema + writer
3. Planning variant representation + scoring stubs
4. Convergence policy
5. Sandbox executor harness
6. Model routing + routing ROI capture
7. Autonomy proposals + trust ladder enforcement

---

## Documentation map

The documents in this repository are structured hierarchically, building from basic concepts and architecture down to
specific operational subsystems:

```mermaid
flowchart TD
    CC["core_concepts.md (Foundational Principles & Terms)"]
    APP["appendix.md (Glossary of Metrics & Terms)"]
    ARCH["architecture.md (Technical Design Blueprint)"]

    CC --> ARCH
    CC -.-> APP

    subgraph OperationalPillars [Operational Pillars]
        GF["git_flow.md (Git-Native Isolation Rules)"]
        PE["planning_and_evaluation.md (Variant Evaluation)"]
        MET["metrics.md (Mathematical Estimators & Formulas)"]
        SF["safety.md (Trust Levels & Budgets)"]
    end

    subgraph KnowledgeAndLedger [Knowledge & Ledgers]
        KB["knowledgebase_schema.md (Curation Workflow)"]
        WB["wisdombase_schema.md (Meta-Evolution)"]
        LD["ledger_schema.md (Audit Logs & Records)"]
    end

    subgraph AgentImplementation [Agent Implementation]
        AD["agent_design.md (Agent Contracts)"]
        AGS["agents.md (Execution CLI Adapters)"]
    end

    ARCH --> OperationalPillars
    ARCH --> KnowledgeAndLedger
    ARCH --> AgentImplementation

    PE <--> MET
    AD --> AGS

    EX["examples.md (Practical Scenarios & Workflows)"]
    OperationalPillars --> EX
    AgentImplementation --> EX
```

### Document Directory

- **Foundations & Core Blueprint:**
  - [`docs/core_concepts.md`](docs/core_concepts.md) — Conceptual definitions and principles.
  - [`docs/architecture.md`](docs/architecture.md) — Full technical architecture design.
  - [`docs/appendix.md`](docs/appendix.md) — Glossary of mathematical symbols and terms.
- **Operational Rules & Evaluation:**
  - [`docs/git_flow.md`](docs/git_flow.md) — Git-native branch, rebase, and merge invariants.
  - [`docs/planning_and_evaluation.md`](docs/planning_and_evaluation.md) — Planning lifecycle, variants, and convergence
    policies.
  - [`docs/metrics.md`](docs/metrics.md) — Detailed formulation of estimators ($P(success)$, $\Delta S$, $EV$, and
    calibration).
  - [`docs/safety.md`](docs/safety.md) — Resource limits, budgets, sandboxing, and trust ladders.
- **Knowledge & Ledgers:**
  - [`docs/knowledgebase_schema.md`](docs/knowledgebase_schema.md) — Propose/validate/promote schemas.
  - [`docs/wisdombase_schema.md`](docs/wisdombase_schema.md) — Universal code invariants and meta-principles.
  - [`docs/ledger_schema.md`](docs/ledger_schema.md) — JSON schemas for the evolution ledger.
- **Execution & Integrations:**
  - [`docs/agent_design.md`](docs/agent_design.md) — High-level agent architecture and behavioral contracts.
  - [`docs/agents.md`](docs/agents.md) — Instructions for integrating specific agents (Gemini, Claude, etc.).
  - [`docs/examples.md`](docs/examples.md) — End-to-end practical walkthroughs.

---

## Sandbox CLI usage

All containerized Holon roles (`intent`, `plan`, `execute`) are driven by the `holon` CLI. It maps agent names to
images, forwards credentials (`GITHUB_TOKEN`, `HOLON_AGENT_*`), mounts the SSH agent socket, and optionally attaches the
token-reduction proxy.

The CLI can be executed via multiple standard pathways:

### Execution Pathways

#### 1. Repository Convenience Script (`./holon`)

From the root of the repository, the convenience script [`./holon`](holon) sets up `PYTHONPATH` and invokes the CLI
entrypoint directly:

```bash
./holon intent intents/my-task.json                                  # Intent Creator
./holon plan "I-1784983150-build-execution/_" --agent pi-agent --model gemini-3.5-flash
./holon execute "I-1784983150-build-execution/P-1784988130-pi-agent-gemini-3.5-flash/_" \
  --agent pi-agent --model gemini-3.5-flash --token-reduce
```

#### 2. Ephemeral Execution (`uvx`)

Run the CLI on-demand in an isolated ephemeral environment without installing it into the system:

```bash
# Run from local repository clone
uvx --from ./apps/sandbox-executor holon intent intents/my-task.json
uvx --from ./apps/sandbox-executor holon plan "I-1784983150-build-execution/_" --agent pi-agent --model gemini-3.5-flash
uvx --from ./apps/sandbox-executor holon execute "I-1784983150-build-execution/P-1784988130-pi-agent-gemini-3.5-flash/_" --agent pi-agent --model gemini-3.5-flash

# Run directly from remote Git repository
uvx --from "git+https://github.com/Holon-Agentic-Coder/holon-agentic-coder.git@main#subdirectory=apps/sandbox-executor" holon --help
```

#### 3. Global Installation (`uv tool install`)

Install the `holon` executable globally onto your `$PATH` using `uv tool`:

```bash
# Install from local clone
uv tool install apps/sandbox-executor

# Or install directly from remote Git repository
uv tool install "git+https://github.com/Holon-Agentic-Coder/holon-agentic-coder.git@main#subdirectory=apps/sandbox-executor"
```

Once installed, invoke `holon` directly anywhere:

```bash
holon intent intents/my-task.json
holon plan "I-1784983150-build-execution/_" --agent pi-agent --model gemini-3.5-flash
holon execute "I-1784983150-build-execution/P-1784988130-pi-agent-gemini-3.5-flash/_" --agent pi-agent --model gemini-3.5-flash
```

To upgrade a globally installed version:

```bash
uv tool upgrade holon
```

#### 4. Local Development (Editable Installation)

When developing locally within the repository, synchronize the virtual environment using `uv sync` to install `holon` in
editable mode:

```bash
uv sync
uv run holon --help

# Or install editably in an existing virtual environment:
uv pip install -e apps/sandbox-executor
```

> [!NOTE] `--token-reduce` is available on `plan` and `execute` (not on `intent`) and is currently **experimental / not
> yet functional** (Phase 2 addon is missing, so runs degrade to direct egress). See
> [Running Plan Generation](docs/sandbox/create_plan.md) and [Running Execution](docs/sandbox/execute_plan.md) for the
> full contract.

### Token reduction (`--token-reduce`)

`--token-reduce` starts a locally-owned mitmproxy sidecar and moves the sandbox onto a per-run Docker network so agent
responses can be compacted before they are tokenized.

> [!WARNING] **`--token-reduce` is experimental and not yet functional.** The Phase 2 mitmproxy addon (`mitm_addon.py`)
> is not shipped yet, so the preflight raises `FileNotFoundError`, the CLI logs an actionable error, and the run
> continues with **direct egress** — no interception takes place.

> [!WARNING] Once functional, `--token-reduce` performs **local TLS interception** against that locally-owned proxy. A
> Holon Root CA is generated at `~/.holon/certs/holon-root-ca.crt` with `basicConstraints=critical,CA:TRUE` and
> `keyUsage=critical,keyCertSign,cRLSign` (a CA without `keyUsage` is refused as a trust anchor by several TLS stacks)
> and is rotated automatically before it would expire within 30 days.
>
> **Trust mechanism (merged bundle, not `update-ca-certificates`)**: the sandbox image runs as the unprivileged `holon`
> user, so the Debian trust store can never be refreshed. Instead the sandbox entrypoint concatenates the image's system
> store (`/etc/ssl/certs/ca-certificates.crt`) with the Holon CA into `/home/holon/.holon-ca-bundle.crt`, and
> `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` / `CURL_CA_BUNDLE` point at that merged file — those variables _replace_ the
> trust store of the clients that read them, so pointing them at the single-cert Holon mount would break every
> legitimate HTTPS endpoint (github.com, api.openai.com, the agent's own LLM endpoint). `NODE_EXTRA_CA_CERTS` points at
> the Holon CA alone because it _augments_ Node's built-in roots. Loopback and link-local endpoints are excluded via
> `NO_PROXY` / `no_proxy`.
>
> **Key exposure**: a MITM proxy inherently requires the CA **private key** to sign forged leaves, so
> `~/.holon/proxy-ca/mitmproxy-ca.pem` (key + cert, mode `0600`) and `mitmproxy-ca-cert.pem` are mounted **read-only
> into the proxy sidecar only** (`/home/mitmproxy/.mitmproxy`). The private key is never mounted into the _agent_
> container, which only ever receives the public certificate.
>
> **Retention / redaction posture**: the proxy cache (`~/.holon/proxy-cache`) is mounted read-only into the sidecar and
> sidecar logs are size-bounded (`--log-opt max-size=5m --log-opt max-file=2`), but **no credential redaction is
> implemented yet** (Phase 2). `--token-reduce` must therefore only be used against a locally-owned proxy.

- **Prerequisites**: `docker` and `openssl` on the host `PATH`. On any failure (missing binary, missing addon script,
  failed sidecar, proxy that never becomes ready) the CLI logs an actionable error and the run continues with **direct
  egress**; a dead proxy is never injected.
- **Containment**: the sidecar is capped at `--memory=256m --cpus=0.5` with bounded log rotation, and both it and its
  per-run network (`holon-net-<pid>-<uuid>`) are removed when the run finishes — on every exit path, including early
  failures while assembling the `docker run` command.

| Variable             | Effect                                                                                                                                                    |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `HOLON_TOKEN_REDUCE` | Opt-in without the flag (`1`, `true`, `yes`, `on`). Attaches to an already-running proxy; never starts one.                                               |
| `HOLON_PROXY_URL`    | Proxy URL used in the `HOLON_TOKEN_REDUCE` path. Defaults to the host gateway (`host.docker.internal:8080` on macOS/Windows, `172.17.0.1:8080` on Linux). |

> [!IMPORTANT] Host `HTTP_PROXY` / `HTTPS_PROXY` are **never** interpreted as opt-in; sandbox networking only changes
> when `--token-reduce` or `HOLON_TOKEN_REDUCE` is set explicitly.

---

## Contributing

This project is currently in the **ideation and planning phase**. The focus is on refining the conceptual architecture,
principles, and specifications—not implementation.

Contributions are welcome in the form of:

- **Conceptual feedback:** challenging assumptions, identifying edge cases, proposing alternative approaches
- **Documentation improvements:** clarity, completeness, consistency across specs
- **Theoretical refinement:** better metrics definitions, convergence policies, safety mechanisms
- **Use case analysis:** how this architecture would handle specific real-world scenarios

Please open **issues** or **discussions** for:

- questions about design decisions
- proposed changes to core principles
- identification of gaps or contradictions in the spec

**Note:** This repository is a **blueprint and specification**, not a reference implementation. There are no plans to
accept implementation PRs at this time.

---

## How to cite / describe Holon (one paragraph)

Holon: Agentic Coder is a git-native architecture for autonomous coding agents that models work as a recursive tree of
intents, evaluates multiple planning variants per intent, executes sub-intents in isolated sandboxes with mandatory
rebasing, records all intent/action/outcome data in an append-only evolution ledger, curates learnings into a versioned
knowledge base, and treats success prediction, entropy, and model routing as first-class measurable signals driving
selection pressure and earned autonomy.

---

**Next:** See [`docs/architecture.md`](docs/architecture.md) for the complete technical design.
