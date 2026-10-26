# safety.md

This document defines the **safety model** for Holon. Safety is achieved through **sandboxing**, **trust levels**,
**entropy budgets**, **human review boundaries**, and **git-based isolation**.

The safety model enables:

- **exploration without catastrophic risk** (branches can be discarded)
- **earned autonomy** (agents gain capabilities through demonstrated reliability)
- **blast radius containment** (failures are isolated to branches)
- **human oversight at critical boundaries** (promotion to `main`, estimator changes, governance)

---

## Core safety principles: Config-Driven Safety

Safety in Holon is not a static set of hardcoded rules. It is a **governed layer** externalised in
`holon-config/rules/`. This allows project-specific safety boundaries, trust thresholds, and sandboxing policies to be
managed centrally.

- **Trust Level Definitions:** Capabilities and escalation thresholds are loaded from
  `holon-config/rules/trust_levels.json`.
- **Sandbox Selection Policy:** Logic for choosing sandbox types based on entropy and trust is loaded from
  `holon-config/rules/sandbox_policy.json`.
- **Entropy Budgets:** Default allocations and enforcement rules are loaded from
  `holon-config/rules/entropy_rules.json`.
- **Git Flow Constraints:** Mandatory rebase and merge rules are loaded from `holon-config/rules/git_flow_rules.json`.

---

### 1) Git is the safety boundary

- Every intent executes in an **isolated git branch**.
- Failures are **contained to the branch** (no impact on `main` or sibling branches).
- Branches can be **abandoned without consequence**.
- Only **human-approved intents** merge to `main`.

### 2) Sandboxing is mandatory for execution

- All code execution happens in **sandboxed environments** (containers, VMs, isolated processes).
- Sandboxes have **restricted access** to filesystem, network, and system resources.
- Sandbox escape attempts are **detected and logged**.

### 3) Trust is earned, not granted

- Agents start with **baseline trust** (can plan and execute, cannot spawn intents).
- Trust increases through **demonstrated reliability** (calibration accuracy, low failure rate).
- Higher trust unlocks **more autonomy** (spawn sub-intents, propose root intents, propose estimators).

### 4) Entropy is a safety signal

- High predicted entropy = **high risk** = deeper review, stronger sandboxing, or rejection.
- Entropy budgets **limit blast radius** (intents cannot exceed allocated entropy).
- Entropy spikes trigger **automatic escalation** (human review, deeper model routing).

### 5) Human review is the final gate

- Agents can explore freely in branches.
- Only **parent intents** (direct children of `main`) require human review to merge.
- **Governance changes** (estimators, routing policies, core invariants) always require human approval.

---

## Sandboxing model

### Sandbox types

#### 1) **Process sandbox** (baseline)

- Isolated Python process with restricted imports.
- No network access.
- Filesystem access limited to intent branch directory.
- No subprocess spawning (except whitelisted tools: `git`, `pytest`).

**Use case:** Low-entropy intents (< 10), familiar work, no external dependencies.

**Implementation:**

```python
import os
import subprocess


def run_in_process_sandbox(code, allowed_paths):
    env = os.environ.copy()


env["PYTHONPATH"] = ":".join(allowed_paths)
env["NO_NETWORK"] = "1"

result = subprocess.run(["python", "-c", code], env=env, capture_output=True, timeout=300, cwd=allowed_paths[0])
return result
```

#### 2) **Container sandbox** (standard)

- Docker container with minimal base image.
- No network access (unless explicitly allowed for specific intent).
- Filesystem mounted read-only except for intent workspace.
- Resource limits (CPU, memory, disk).

**Use case:** Medium-entropy intents (10-30), moderate complexity, external dependencies.

**Implementation:**

```bash
docker run \
--rm \
--network none \
--cpus 2 \
--memory 4g \
--read-only \
--tmpfs /tmp \
-v $(pwd)/intent_workspace:/workspace \
holon-sandbox:latest \
python /workspace/execute.py
```

#### 3) **VM sandbox** (high-security)

- Full virtual machine with snapshot/restore.
- Network isolated or monitored.
- Kernel-level isolation.
- Automatic rollback on failure.

**Use case:** High-entropy intents (> 30), novel work, untrusted code, exploratory research.

**Implementation:**

- Use Firecracker, QEMU, or cloud VM.
- Snapshot before execution, restore on failure.
- Monitor syscalls for escape attempts.

### Sandbox selection policy (Config-Driven)

The logic for selecting a sandbox is loaded from `holon-config/rules/sandbox_policy.json`.

```python
def select_sandbox(predicted_entropy, novelty, trust_level, config):
    # Load thresholds from external rules config
    policy = config.load_rules().sandbox_policy

    if predicted_entropy > policy.vm_entropy_threshold or novelty > policy.vm_novelty_threshold:
        return "vm_sandbox"
    elif predicted_entropy > policy.container_entropy_threshold or trust_level == "baseline":
        return "container_sandbox"
    else:
        return "process_sandbox"
```

### Sandbox escape detection

Monitor for:

- Attempts to access paths outside allowed workspace.
- Network connections (unless explicitly allowed).
- Subprocess spawning (except whitelisted tools).
- Privilege escalation attempts.
- Filesystem writes outside workspace.

**Response:**

1. Immediately terminate sandbox.
2. Log escape attempt to ledger (`sandbox_escape_attempted`).
3. Mark intent as failed.
4. Reduce agent trust level.
5. Trigger human review.

---

## Trust model

### Trust levels

The system defines four distinct trust tiers for agents, representing an escalation of capabilities earned through
historical performance and calibration reliability.

| Trust Level                                 | Capabilities (Can)                                                                                      | Restrictions (Cannot)                                                                                         | Promotion/Escalation Criteria                                                                             |
| :------------------------------------------ | :------------------------------------------------------------------------------------------------------ | :------------------------------------------------------------------------------------------------------------ | :-------------------------------------------------------------------------------------------------------- |
| **Baseline** <br>_(Default for new agents)_ | <ul><li>Plan actions</li><li>Execute in isolated sandbox</li><li>Read Ledger and KB</li></ul>           | <ul><li>Spawn sub-intents</li><li>Propose root intents</li><li>Propose estimators</li><li>Modify KB</li></ul> | Initial baseline state upon agent registration                                                            |
| **Medium** <br>_(Earned)_                   | <ul><li>All Baseline capabilities</li><li>Spawn sub-intents (up to depth 3)</li></ul>                   | <ul><li>Propose root intents</li><li>Propose estimators</li><li>Modify KB directly</li></ul>                  | $\ge 10$ successful executions with average success calibration error $< 0.30$                            |
| **High** <br>_(Earned)_                     | <ul><li>All Medium capabilities</li><li>Propose root intents (requires human approval)</li></ul>        | <ul><li>Propose estimators</li><li>Modify core invariants</li></ul>                                           | $\ge 30$ successful executions with average success calibration error $< 0.20$                            |
| **Highest** <br>_(Earned)_                  | <ul><li>All High capabilities</li><li>Propose estimators (requires human approval & backtest)</li></ul> | <ul><li>Modify core invariants directly (always requires human gating)</li></ul>                              | $\ge 100$ successful executions with average success calibration error $< 0.15$ (manual promotion review) |

---

### Trust scoring formula (Config-Driven)

Weights used in trust calculation are loaded from `holon-config/rules/trust_levels.json`.

```python
def compute_trust_score(agent_id, ledger, config):
    # Load scoring weights from external trust config
    weights = config.load_rules().trust_weights
    executions = ledger.get_executions(agent_id=agent_id)

    success_rate = sum(e.status == "success" for e in executions) / len(executions)
    mean_calibration_error = mean(abs(e.predicted.p_success - e.actual.p_success) for e in executions)
    mean_entropy_error = mean(abs(e.predicted.entropy - e.actual.entropy) for e in executions)

    # Penalties
    sandbox_escapes = ledger.count_events(agent_id=agent_id, event_type="sandbox_escape_attempted")
    rebase_conflicts = sum(e.rebase_conflicts for e in executions)

    trust_score = (
        weights.success_rate * success_rate
        + weights.calibration * (1 - min(1.0, mean_calibration_error))
        + weights.entropy * (1 - min(1.0, mean_entropy_error / 50))
        + weights.rebase * (1 - min(1.0, rebase_conflicts / 10))
        - weights.escape_penalty * sandbox_escapes
    )

    return max(0.0, min(1.0, trust_score))
```

### Trust level assignment

```python
def assign_trust_level(trust_score, execution_count):
    if trust_score < 0.5 or execution_count < 10:
        return "baseline"
    elif trust_score < 0.7 or execution_count < 30:
        return "medium"
    elif trust_score < 0.85 or execution_count < 100:
        return "high"
    else:
        return "highest"  # still requires human approval for sensitive actions
```

### Trust degradation

Trust can **decrease** if:

- Calibration error increases significantly (> 0.5 for 3+ consecutive executions).
- Sandbox escape attempted.
- Repeated rebase conflicts (> 5 in 10 executions).
- Intent abandoned due to agent error (not external factors).

**Response:**

- Reduce trust level by one tier.
- Require human review for next 5 executions.
- Log trust degradation event to ledger.

---

## Entropy budgets (blast radius limits)

### Budget allocation

Every intent has an **entropy budget** that limits its blast radius:

```python
def allocate_entropy_budget(intent_type, parent_budget, trust_level):
    if intent_type == "root":
        # Root intents get full budget (set by human)
        return parent_budget
    else:
        # Sub-intents get fraction of parent budget
        fraction = {"baseline": 0.3, "medium": 0.5, "high": 0.7, "highest": 0.9}[trust_level]
        return parent_budget * fraction
```

### Budget enforcement

Before execution:

1. **Check predicted entropy** against budget.
2. If `predicted_entropy > budget` and the plan lists no explicit sub-intent list, **re-plan automatically** (a bounded
   number of times) and then **fail the stage**; a plan with an explicit sub-intent list runs each child through the
   full pipeline. No stage before the merge waits for a human (see [Turn and Stage Budgets](budgets.md#entropy-budget)).

After execution:

1. **Measure actual entropy**.
2. If `actual_entropy > budget * 1.5`, **trigger investigation** (why was prediction so wrong?).
3. **Deduct actual entropy** from parent's remaining budget.

### Budget exhaustion

If parent intent's entropy budget is exhausted:

- No more sub-intents can be spawned.
- Agent must either:
  - Complete remaining work within budget.
  - Request budget increase (requires human approval).
  - Abandon intent.

---

## Human review boundaries

### Automatic human review triggers

Human review is **required** for:

1. **Parent intent merge to `main`**
   - All parent intents (direct children of `main`) require human approval before merge.
   - Sub-intents merge to parent without human review (parent review covers them).

2. **Estimator proposals**
   - Any change to P(success), ΔS, Impact, Learning Value, or EV estimators.
   - Requires backtest validation + human approval.

3. **Routing policy changes**
   - Changes to model routing heuristics.
   - Requires ROI validation + human approval.

4. **Core invariant changes**
   - Changes to git discipline rules, metric constraints, trust model.
   - Always requires human approval (highest trust + explicit approval).

5. **Entropy budget exceeded**
   - If `actual_entropy > budget * 1.5`, human reviews to understand why.

6. **Sandbox escape attempt**
   - Immediate human review + trust degradation.

7. **Repeated failures**
   - If agent fails 3+ consecutive intents, human reviews agent state.

8. **Novel intent types**
   - If intent type has no historical precedent in KB, human reviews plan before execution.

### Human review workflow

```

1. Agent completes intent in branch
2. Agent requests merge to parent (or main)
3. System generates review package:
    - Intent goal and constraints
    - Plan selected (with alternatives considered)
    - Execution summary (predicted vs actual metrics)
    - Git diff (from parent branch)
    - Test results
    - Ledger trace (all events for this intent)
4. Human reviews package
5. Human decision:
    - **Approve:** Intent merges to parent/main
    - **Reject:** Intent branch abandoned, feedback recorded
    - **Request changes:** Agent spawns new intent to address feedback
6. Decision logged to ledger (human_review_decision)
```

### Review package format

```json
{
  "intent_id": "I-root-001-bootstrap-metrics",
  "goal": "Bootstrap naive metrics estimators and ledger logging",
  "status": "awaiting_review",
  "branch": "intent/I-root-001-bootstrap-metrics",
  "parent_branch": "main",
  "agent_id": "agent-planner-03",
  "trust_level": "medium",
  "predicted_metrics": {
    "p_success": 0.75,
    "entropy": 18.5,
    "impact": 85,
    "learning_value": 6.5,
    "ev": 58.3
  },
  "actual_metrics": {
    "p_success": 1.0,
    "entropy": 22.1,
    "impact": 90,
    "learning_value": 7.2,
    "ev": 67.2
  },
  "calibration": {
    "p_success_error": 0.25,
    "entropy_error": 3.6,
    "impact_error": 5.0,
    "learning_value_error": 0.7
  },
  "diff_summary": {
    "files_modified": 8,
    "lines_added": 342,
    "lines_deleted": 12,
    "modules_added": ["holon/metrics/p_success.py", "holon/metrics/entropy.py"]
  },
  "test_results": {
    "passed": 15,
    "failed": 0,
    "coverage": 0.87
  },
  "ledger_trace": [
    "seq 42: intent_created",
    "seq 43: plan_variant_created (v1)",
    "seq 44: plan_variant_created (v2)",
    "seq 45: plan_variant_created (v3)",
    "seq 46: planning_converged (winner: v3)",
    "seq 47: execution_started",
    "seq 48-67: tool_calls (git, pytest, python)",
    "seq 68: execution_completed (success)"
  ],
  "review_url": "https://github.com/user/holon/compare/main...intent/I-root-001-bootstrap-metrics"
}
```

---

## Failure containment

### Failure types and responses

#### 1) **Execution failure** (code error, test failure)

- **Containment:** Failure isolated to intent branch.
- **Response:** Log to ledger, measure actual metrics, update calibration.
- **Recovery:** Agent can retry with different plan, or abandon intent.
- **No impact:** Other intents unaffected.

#### 2) **Rebase conflict**

- **Containment:** Conflict isolated to intent branch.
- **Response:** Log conflict files, abort rebase, notify agent.
- **Recovery:** Agent resolves conflict or requests parent to merge siblings sequentially.
- **Escalation:** If conflicts persist (> 3 attempts), human review.

#### 3) **Entropy budget exceeded**

- **Containment:** Intent cannot spawn more sub-intents.
- **Response:** Log budget exhaustion, notify agent.
- **Recovery:** Agent completes work within budget, or requests increase (human approval).
- **Escalation:** If actual entropy >> predicted (> 1.5x), human reviews estimator accuracy.

#### 4) **Sandbox escape attempt**

- **Containment:** Sandbox terminated immediately.
- **Response:** Log escape attempt, mark intent as failed, reduce agent trust.
- **Recovery:** None (intent abandoned).
- **Escalation:** Immediate human review + investigation.

#### 5) **Calibration degradation**

- **Containment:** Agent trust level reduced.
- **Response:** Log calibration errors, trigger estimator review.
- **Recovery:** Agent continues with reduced autonomy until calibration improves.
- **Escalation:** If calibration error > 0.5 for 5+ executions, human reviews agent/estimator.

---

## Exploration vs exploitation safety

### Exploration is encouraged (within safety boundaries)

Holon is designed to **explore low-probability actions** for **learning value**. This is safe because:

1. **Git isolation:** Exploration happens in branches that can be discarded.
2. **Sandboxing:** Exploration cannot escape to host system.
3. **Entropy budgets:** Exploration is limited by blast radius.
4. **Human review:** Exploration results are reviewed before promotion to `main`.

### Exploration guidelines

- **Low-probability actions are valuable** if they provide learning (even if they fail).
- **Failure is data:** Failed explorations improve estimators and populate failure modes in KB.
- **No human approval needed for exploration** (only for promotion to `main`).
- **Entropy is the cost of exploration:** High-entropy exploration consumes more budget.

### Exploitation (production work)

- **High-probability actions** (proven patterns from KB).
- **Low entropy** (predictable, low blast radius).
- **Fast execution** (routed to flash models).
- **Merges to `main`** after human review.

---

## Safety invariants (must always hold)

### Invariant 1: Git discipline

- Sub-intents **never merge directly to `main`**.
- Sub-intents **always rebase from parent before and after execution**.
- Only parent intents (direct children of `main`) merge to `main` after human review.

### Invariant 2: Sandboxing

- All code execution happens in sandboxes.
- Sandbox type matches predicted entropy and trust level.
- Sandbox escapes are detected and logged.

### Invariant 3: Entropy budgets

- Every intent has an entropy budget.
- Predicted entropy is checked against budget before execution.
- Actual entropy is measured and deducted from parent budget.

### Invariant 4: Human review

- Parent intents require human approval to merge to `main`.
- Estimator changes require human approval.
- Governance changes require human approval.

### Invariant 5: Trust boundaries

- Agents cannot exceed their trust level capabilities.
- Trust is earned through demonstrated reliability.
- Trust can degrade based on performance.

### Invariant 6: Immutability

- Ledger is append-only (never modified).
- Core invariants cannot be changed by agents (only by humans).
- KB entries are versioned (old versions retained).

---

## Safety monitoring and alerts

### Real-time monitoring

Monitor for:

- **Entropy spikes:** `actual_entropy > predicted_entropy * 1.5`
- **Calibration degradation:** `mean_calibration_error > 0.5` over last 5 executions
- **Sandbox escapes:** Any escape attempt
- **Rebase conflict storms:** > 3 conflicts in single intent
- **Budget exhaustion:** Intent cannot proceed due to entropy budget
- **Trust degradation:** Agent trust level reduced

### Alert levels

#### **Info** (logged, no action)

- Execution completed successfully
- Plan variant created
- Rebase successful

#### **Warning** (logged, monitored)

- Calibration error > 0.3
- Entropy error > 20%
- Single rebase conflict
- Budget at 80%

#### **Error** (logged, agent notified)

- Execution failed
- Rebase conflict (> 1 attempt)
- Budget exhausted
- Calibration error > 0.5

#### **Critical** (logged, human notified immediately)

- Sandbox escape attempt
- Trust degradation
- Repeated failures (3+)
- Estimator proposal rejected
- Entropy spike (> 1.5x predicted)

---

## Safety evolution

### Safety metrics improve over time

As the system evolves:

- **Estimators improve:** Better predictions = fewer surprises = less risk.
- **KB grows:** More proven patterns = less exploration needed = lower entropy.
- **Trust increases:** Reliable agents earn more autonomy.
- **Routing improves:** Better model selection = better outcomes = higher ROI.

### Safety is measurable

Track over time:

- Mean calibration error (should decrease)
- Sandbox escape attempts (should be zero)
- Entropy budget overruns (should decrease)
- Human review rejection rate (should decrease)
- Trust level distribution (should shift toward higher trust)

### Safety is auditable

Every safety-relevant event is logged:

- Sandbox selection and execution
- Trust level changes
- Entropy budget allocation and consumption
- Human review decisions
- Calibration errors
- Failure modes

---

## Related documents

- [`ledger_schema.md`](ledger_schema.md) — safety events logged to ledger
- [`kb_schema.md`](kb_schema.md) — failure modes and mitigations in KB
- [`metrics.md`](metrics.md) — entropy as safety signal
- [`git_flow.md`](git_flow.md) — git isolation as safety boundary
- [`architecture.md`](architecture.md) — how safety integrates into system
