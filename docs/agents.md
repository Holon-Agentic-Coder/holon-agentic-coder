# agents.md

This document defines the **agent model** for Holon. Agents are autonomous AI entities that plan, execute, learn, and
evolve within the constraints of the safety model, git discipline, and metric-driven decision-making.

Agents enable:

- **autonomous intent execution** (planning → execution → measurement)
- **self-improvement** (proposing better estimators, tactics, patterns)
- **recursive decomposition** (spawning sub-intents to solve complex problems)
- **competitive planning** (generating multiple plan variants, selecting best by EV)
- **earned autonomy** (gaining capabilities through demonstrated reliability)

---

## Core agent principles

### 1) Agents are config-driven

- Agents are initialised with a `config_path` (defaulting to `holon-config/`) and a `knowledge_path` (defaulting to
  `holon-knowledge/`).
- Agents load their identity, mission, physics (metrics), safety rules, and local project priors from `holon-config/`.
- Agents load their historical memory (Ledger, KB) and universal wisdom (WB) from `holon-knowledge/`.

### 2) Agents are goal-directed

- Every agent operates on an **intent** (goal + constraints).
- Agents plan actions to maximise **Expected Value (EV)**.
- Agents measure outcomes and update calibration.

### 3) Agents are metric-driven

- Agents use **P(success), ΔS, Impact, Cost, LearningValue** to evaluate plans.
- Agents select plans with highest EV under constraints.
- Agents learn from **calibration errors** (predicted vs actual).

### 4) Agents are sandboxed

- Agents execute in **isolated environments** (git branches + sandboxes).
- Agents cannot escape sandbox boundaries.
- Agents cannot modify core invariants without human approval.

### 5) Agents are trust-bounded

- Agents start with **baseline trust** (limited autonomy).
- Agents earn trust through **demonstrated reliability**.
- Agents lose trust through **failures or violations**.

### 6) Agents are evolvable

- Agents can propose **improvements to themselves** (estimators, tactics, patterns).
- Agents can spawn **sub-agents** (recursive decomposition).
- Agents can generate **new intents** (autonomous goal-setting, with trust gates).

---

## Agent types

### 1) **Planner Agent**

**Role:** Generate plan variants for a given intent.

**Inputs:**

- Intent (goal, constraints, scope)
- KB (project-specific patterns and tactics from `holon-knowledge/kb/`)
- WB (universal invariants from `holon-knowledge/wb/`)
- Ledger (historical calibration data from `holon-knowledge/ledger/`)
- `holon-config/prompts/` (mission and persona)
- `holon-config/metrics/` (local EV coefficients)

**Outputs:**

- Plan variants (structured steps, sub-intents, predictions)
- Predicted metrics (P(success), ΔS, Impact, Cost, EV)

**Operational Logic:**

1. **Context Retrieval:** Queries the KB and WB for similar successful patterns and universal reasoning invariants.
2. **Plan Generation:** Uses the config-driven mission to produce multiple candidate plan graphs (competitive planning).
3. **Metric Estimation:** Applies the EV formula using coefficients from `holon-config/metrics/` and calibration data
   from the Ledger.
4. **Variant Creation:** Packages each candidate as a Plan Variant with unique IDs and predicted outcomes.

---

### 2) **Executor Agent**

**Role:** Execute a selected plan in a sandboxed environment.

**Inputs:**

- Intent and Selected Plan
- Sandbox configuration
- KB (tactics and modules from `holon-knowledge/kb/`)
- `holon-config/prompts/` (mission and persona)
- `holon-config/rules/` (safety rules and git flow policies)

**Outputs:**

- Execution results (success/failure)
- Artifacts (code, diffs, test results)
- Measured metrics (actual P(success), ΔS, Impact, Cost, LearningValue)

**Operational Logic:**

1. **Sandbox Initialisation:** Prepares an isolated environment and applies safety rules defined in
   `holon-config/rules/`.
2. **Step Execution:** Runs plan steps sequentially, utilising proven tactics from the KB or generating new
   implementations based on the mission.
3. **Validation:** Executes tests and captures all tool outputs (git, pytest, etc.) to the Ledger.
4. **Post-Execution Measurement:** Measures actual entropy, impact, and cost to compute calibration errors.

---

### 3) **Curator Agent**

**Role:** Extract patterns and failure modes from the Ledger and propose KB entries.

**Inputs:**

- Ledger (historical executions from `holon-knowledge/ledger/`)
- KB (existing entries in `holon-knowledge/kb/`)
- `holon-config/prompts/` (mission and persona)
- `holon-config/schemas/` (KB validation rules)

**Outputs:**

- KB entry proposals (patterns, tactics, failure modes)
- Evidence (ledger references, success/failure counts)

**Operational Logic:**

1. **Pattern Extraction:** Scans the Ledger for recurring successful intent structures and extracts them as reusable
   patterns.
2. **Failure Analysis:** Groups failed intents by reason to identify root causes and propose mitigations.
3. **Validation:** Ensures all proposals meet the evidence thresholds and schema requirements defined in
   `holon-config/`.
4. **Ledger Logging:** Records all proposals and evidence to the Ledger for human or higher-trust agent review.

---

### 4) **Evaluator Agent**

**Role:** Evaluate plan variants and select the best by Expected Value (EV).

**Inputs:**

- Intent and Plan variants (with predicted metrics)
- `holon-config/prompts/` (mission and persona)
- `holon-config/rules/` (convergence policy)

**Outputs:**

- Selected plan
- Convergence reason (EV plateau, dominant plan, budget exhausted)

**Operational Logic:**

1. **Ranking:** Sorts variants by EV based on the "physics" defined in the configuration.
2. **Convergence Check:** Evaluates the variant set against the convergence policy (e.g., is the EV gap between the top
   two variants wide enough?).
3. **Decision:** Signals whether to proceed with the best plan or continue generating more variants to reduce
   uncertainty.

---

### 5) **Meta-Agent (Orchestrator)**

**Role:** Coordinate the entire intent lifecycle (creation → planning → execution → measurement → promotion).

**Inputs:**

- Intent queue (pending work)
- Agent pool (available specialised agents)
- Ledger and KB (`holon-knowledge/`)
- `holon-config/` (root configuration and rules)

**Outputs:**

- Work assignments and Intent state transitions
- Human review packages

**Operational Logic:**

1. **Dispatch:** Assigns planning and execution tasks to specialised agents based on their capabilities and trust
   levels.
2. **State Management:** Tracks intents through their lifecycle (Proposed → Planning → Executing → Merged).
3. **Git Flow Coordination:** Enforces mandatory rebase/merge rules defined in `holon-config/world/constraints.md`.
4. **Escalation:** Generates review packages for humans when intents reach promotion boundaries or safety triggers.

---

### 6) **Researcher Agent** (Highest trust only)

**Role:** Propose improvements to estimators, metrics, and core engine heuristics.

**Inputs:**

- Ledger (historical calibration data)
- KB and WB (`holon-knowledge/`)
- `holon-config/prompts/` (researcher mission)

**Outputs:**

- Estimator and Policy proposals (with backtest validation)

**Operational Logic:**

1. **Bias Detection:** Analyses calibration errors in the Ledger to find systematic inaccuracies in the engine's
   "physics."
2. **Hypothesis Testing:** Generates improved estimator formulas and backtests them against historical project data.
3. **Wisdom Ascension:** Identifies project-specific patterns in the KB that are successful across multiple projects and
   proposes them for promotion to the Wisdom Base.

---

## Agent lifecycle

### 1) Agent creation

Agents are instantiated with a unique ID, a specific model tier, and a trust level. They are linked to `config_path` and
`knowledge_path` to ensure they "wake up" with the correct priors and memory.

### 2) Agent execution

Every task execution is wrapped in a logging envelope. The system records the start, any tool calls made during
execution, and the final outcome (success/failure) to the immutable Ledger.

Additionally, the active agent CLI version is resolved at run-time by calling the runner's `get_version()` mechanism.
This queries the CLI binary directly with version flags (such as `--version`, `-v`, or `version`) with a fallback to
predefined Dockerfile-aligned agent versions if the binary is absent or the execution fails. The resolved
`agent_version` is logged alongside the agent name in both the `plans.jsonl` and `executions.jsonl` ledgers to ensure
full auditability.

### 3) Agent trust update

The system periodically recalculates agent trust scores based on successful executions, calibration accuracy, and
adherence to safety invariants. Level transitions (e.g., Baseline to Medium) are governed by rules in
`holon-config/rules/trust_levels.json`.

---

## Agent communication

### Agent-to-agent communication (via ledger)

Agents do not communicate directly. Instead:

- Agents **write to ledger** (events, results, proposals).
- Agents **read from ledger** (historical data, calibration).
- Agents **read from KB** (patterns, tactics, estimators).

This ensures:

- **Auditability:** All communication is logged.
- **Isolation:** Agents cannot interfere with each other.
- **Consistency:** Single source of truth (ledger + KB).

### Agent-to-human communication (via review packages)

Agents communicate with humans through:

- **Review packages** (intent summaries, diffs, metrics).
- **Proposals** (estimator improvements, KB entries).
- **Alerts** (critical events, trust degradation).

---

## Agent autonomy levels

### Level 0: No autonomy (human-driven)

- Human creates intents manually.
- Human selects plans manually.
- Human executes manually.

### Level 1: Execution autonomy (baseline trust)

- Agent executes assigned intents.
- Agent cannot spawn sub-intents.
- Agent cannot propose root intents.

### Level 2: Decomposition autonomy (medium trust)

- Agent can spawn sub-intents (up to `MAX_SUB_INTENT_DEPTH`, currently 2).
- Agent can decompose complex intents.
- Agent cannot propose root intents.

### Level 3: Goal autonomy (high trust)

- Agent can propose root intents (subject to human approval).
- Agent can generate goals based on system needs.
- Agent cannot modify estimators.

### Level 4: Meta autonomy (highest trust)

- Agent can propose estimator improvements (subject to human approval).
- Agent can propose routing policy changes.
- Agent can propose KB patterns.
- Agent cannot modify core invariants.

### Level 5: Full autonomy (future, not in bootstrap)

- Agent can modify core invariants (subject to human approval).
- Agent can propose safety policy changes.
- Agent can propose trust model changes.

---

## Agent intent generation (autonomous goal-setting)

### When agents generate intents

Agents with **high or highest trust** can generate intents in these scenarios:

#### 1) **System needs** (proactive)

- Calibration error increasing → propose "Improve P(success) estimator"
- KB missing patterns for common intent type → propose "Extract pattern for X"
- Routing ROI declining → propose "Analyse routing policy effectiveness"

#### 2) **Failure response** (reactive)

- Rebase conflict storm → propose "Refactor module to reduce coupling"
- Repeated test failures → propose "Improve test coverage for module X"
- Sandbox escape attempt → propose "Strengthen sandbox isolation"

#### 3) **Exploration** (curiosity-driven)

- Low-probability tactic with high learning value → propose "Explore alternative approach to X"
- Novel problem with no KB patterns → propose "Research solution for X"

### Intent quality scoring

When an agent proposes an intent, the system scores its quality:

```python
def score_intent_quality(proposed_intent, agent_id, ledger, kb):
    score = 0.0

    # Alignment with system needs
    if addresses_calibration_error(proposed_intent, ledger):
        score += 0.3
    if fills_kb_gap(proposed_intent, kb):
        score += 0.2
    if responds_to_failure(proposed_intent, ledger):
        score += 0.3

    # Novelty vs redundancy
    similar_intents = kb.find_similar_intents(proposed_intent.goal)
    if len(similar_intents) == 0:
        score += 0.2  # Novel
    else:
        score -= 0.1  # Redundant

    # Agent track record
    agent_trust_score = compute_trust_score(agent_id, ledger)
    score += 0.2 * agent_trust_score

    # Predicted EV
    predicted_ev = estimate_intent_ev(proposed_intent, kb, ledger)
    if predicted_ev > 50:
        score += 0.2

    return max(0.0, min(1.0, score))
```

### Intent approval gates

```python
def approve_intent_proposal(proposed_intent, agent_id, quality_score):
    agent = agent_pool.get_agent(agent_id)

    # Trust level gates
    if agent.trust_level == "high":
        # High trust: auto-approve if quality > 0.6
        if quality_score > 0.6:
            return "auto_approved"
        else:
            return "human_review_required"

    elif agent.trust_level == "highest":
        # Highest trust: auto-approve if quality > 0.5
        if quality_score > 0.5:
            return "auto_approved"
        else:
            return "human_review_required"

    else:
        # Lower trust: always require human review
        return "human_review_required"
```

---

## Agent performance metrics

### Per-agent metrics (tracked in ledger)

- **Success rate:** `successful_executions / total_executions`
- **Mean calibration error:** `mean(|predicted - actual|)` for P(success), ΔS, Impact, LearningValue
- **Mean EV accuracy:** `mean(predicted_ev - actual_ev)`
- **Rebase conflict rate:** `rebase_conflicts / total_executions`
- **Sandbox escape attempts:** `count(sandbox_escape_attempted)`
- **Trust score:** Composite score (see `safety.md`)
- **Execution count:** Total number of intents executed
- **Planning efficiency:** `mean(planning_cost / execution_value)`
- **KB contribution rate:** `kb_entries_proposed / executions`

### Aggregate metrics (system-wide)

- **Mean agent trust score:** Across all agents
- **Trust level distribution:** Histogram of trust levels
- **Agent specialisation:** Which agents excel at which intent types
- **Model routing ROI:** By agent and model tier
- **Calibration improvement over time:** System-wide learning curve

---

## Agent specialisation (future evolution)

Over time, agents may specialise:

- **Planning specialists:** Excel at generating high-EV plans for specific domains
- **Execution specialists:** Excel at reliable execution with low entropy
- **Curation specialists:** Excel at extracting patterns and failure modes
- **Research specialists:** Excel at proposing estimator improvements

Specialisation emerges naturally through:

- **Routing:** Meta-agent routes intents to agents with best track record for that intent type
- **Trust:** Specialised agents earn higher trust in their domain
- **KB:** Agents learn from their own successes and failures

---

## Runner and model resolution

`holon plan`, `holon execute`, `holon review-loop`, `holon flow` and `holon doctor` choose the agent runner and the
model through one shared resolver (`sandbox_executor/agent_defaults.py`). The first match wins:

| Tier | Agent runner                     | Model                                 |
| ---- | -------------------------------- | ------------------------------------- |
| 1    | `--agent`                        | `--model`                             |
| 2    | `HOLON_AGENT`                    | `HOLON_MODEL`                         |
| 3    | Detected harness agent (below)   | Default model for the resolved runner |
| 4    | `antigravity-agent` (a fallback) | —                                     |

The result is normalised to the canonical `{id}-agent` form (for example `antigravity` becomes `antigravity-agent`),
which is what ledger rows and plan ids record. A runner without a container image fails straight away with the list of
supported runners and the name of the flag or variable that supplied the bad value.

`holon plan`, `holon execute`, `holon review-loop` and `holon flow` log every decision to stderr with its source (the
CLI routes Holon's own INFO log messages there, leaving stdout untouched), for example
`Agent runner resolved to claude-agent (source: harness, harness detection, marker CLAUDECODE)`. A flow resumed from
`--checkpoint` logs each value it keeps from the checkpoint with the source `checkpoint`. `holon doctor` reports the
decisions instead of logging them: on stdout in its text report, or in the `agent` and `model` objects of its `--json`
payload.

### Harness detection

When Holon is started from inside an agent harness, it uses that harness's runner. Markers are checked in this order,
and the first match wins when several are present (the others are logged as ignored):

| Order | Runner              | Markers                                 |
| ----- | ------------------- | --------------------------------------- |
| 1     | `pi-agent`          | `PI_CODING_AGENT`                       |
| 2     | `claude-agent`      | `CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`  |
| 3     | `codex-agent`       | any `CODEX_*` variable                  |
| 4     | `opencode-agent`    | `OPENCODE`, any `OPENCODE_*` variable   |
| 5     | `antigravity-agent` | any `AGY_*` or `ANTIGRAVITY_*` variable |

Credential and configuration variables never count as markers: `AGY_USER_TOKEN`, `GOOGLE_API_KEY`, `CODEX_API_KEY`,
`CODEX_HOME`, `OPENCODE_API_KEY`, `OPENCODE_CONFIG`, `OPENCODE_CONFIG_DIR`, and any variable ending in `_KEY`, `_TOKEN`,
`_SECRET` or `_PASSWORD`. A marker set to an empty value, `0`, `false`, `no` or `off` is ignored.

Detection is switched off inside a Holon sandbox (`HOLON_ROLE` or `HOLON_IN_SANDBOX` set). There it would pick up the
container's own agent rather than the operator's choice, so container entrypoints always receive the runner the host
resolved.

### Default models

| Runner              | Default model               | Notes                                                                                   |
| ------------------- | --------------------------- | --------------------------------------------------------------------------------------- |
| `antigravity-agent` | `gemini-3.8-flash-medium`   | Accepted by agy together with `--effort`; the label on every successful agy ledger row. |
| `claude-agent`      | `claude-opus-5-5`           |                                                                                         |
| `pi-agent`          | `claude-opus-5-5`           | Depends on the configured pi provider; override with `--model` or `HOLON_MODEL`.        |
| `codex-agent`       | `gpt-5-codex`               | Not verified against the live CLI; `holon doctor` reports a rejected model.             |
| `gemini-agent`      | `gemini-3.8-flash`          | Not verified against the live CLI; `holon doctor` reports a rejected model.             |
| `opencode-agent`    | `anthropic/claude-opus-5-5` | opencode expects `provider/model`; not verified against the live CLI.                   |

The previous `gemini-3.5-flash` default has been removed: current agy no longer lists it and rejects it together with
`--effort`.

### Restoring the previous behaviour

Running inside Claude Code, pi or another harness now selects that harness's runner. To go back to the old fixed
default, set:

```bash
export HOLON_AGENT=antigravity-agent
```

`holon review-loop` decides between running locally and in a container exactly as before (`--local`, `--dry-run`,
`--json` or a sandbox environment), whatever runner is resolved. The resolved runner only selects the image and the
attribution.

Historical records are never re-attributed: checkpoints and ledger rows without an agent or model are back-filled with
the fixed legacy values `antigravity-agent` / `gemini-3.8-flash-medium`, not with the current detection result.

Before `holon plan`, `holon execute` and `holon flow` start any work, the selected runner is checked with a readiness
probe; see [`holon doctor`](executor/agent_credentials_requirements.md#readiness-probe-holon-doctor).
`holon review-loop` is not probed: the review loop runs git, `gh` and local checks only and never calls the runner,
which it uses for image selection and attribution.

---

## Related documents

- [`safety.md`](safety.md) — trust levels, sandboxing, entropy budgets
- [`ledger_schema.md`](ledger_schema.md) — agent events logged to ledger
- [`kb_schema.md`](kb_schema.md) — agent proposals to KB
- [`metrics.md`](metrics.md) — metrics agents use for decision-making
- [`git_flow.md`](git_flow.md) — git discipline agents must follow
- [`architecture.md`](architecture.md) — how agents fit into system architecture
