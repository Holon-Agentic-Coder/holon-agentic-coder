# metrics.md

This document defines the **bootstrap (day-zero) metrics** used by Holon to:

- estimate **P(success)** and **entropy** _before execution_,
- measure **actual outcomes** _after execution_,
- compute **Expected Value (EV)** for plan selection,
- monitor **overall system entropy** for health + maintenance triggers,
- calibrate the estimators over time.

**Design constraints (bootstrapping):**

- Metrics must be **weak but stable**: simple, deterministic, available from Git + ledger data.
- Definitions are **semantic lock-ins**: agents may improve estimators later, but not change meanings without explicit
  human-approved meta-change.

---

## 1) Core Definitions: Config-Driven Physics

Holon’s "physics" — the weights, coefficients, and constants that drive decision-making — are not hardcoded. They are
externalised in `holon-config/metrics/`. This allows the system's behaviour to be tuned and evolved without changing the
underlying engine.

- **`λ` (Entropy penalty):** Loaded from `holon-config/metrics/ev_config.json`.
- **`μ` (Learning Value weight):** Loaded from `holon-config/metrics/ev_config.json`.
- **Entropy weights (`w_i`, `u_i`):** Loaded from `holon-config/metrics/entropy_config.json`.
- **System entropy coefficients (`α, β, γ, δ, ε`):** Loaded from `holon-config/metrics/system_entropy_config.json`.

---

### 1.1 Predicted vs. Actual Metrics

For each plan execution we store:

- Predicted (pre-execution):
  - `p_success_pred ∈ [0, 1]`
  - `entropy_pred ≥ 0`
  - `impact_pred ≥ 0`
  - `cost_pred ≥ 0`
  - `learning_value_pred ∈ [0, 10]`
  - `ev_pred` derived

- Actual (post-execution):
  - `success_actual ∈ {0, 1}` (or optionally `[0,1]` for partial credit, see §1.4)
  - `entropy_actual ≥ 0`
  - `impact_actual ≥ 0`
  - `cost_actual ≥ 0`
  - `learning_value_actual ∈ [0, 10]`
  - `ev_actual` derived (optional; depends on how you price actual cost)

All of these are recorded in the **Ledger** for calibration and selection pressure.

---

### 1.2 Expected Value (EV)

EV is the selection objective used to compare competing plan variants for the same intent.

We define:

$$EV = P(success)\cdot Impact + \mu\cdot LearningValue - \lambda \cdot \Delta S_{intent} - Cost$$

Where:

- `P(success)` is predicted success probability.
- `Impact` is expected benefit if the plan succeeds.
- `ΔS_intent` (or `Entropy`) is expected disorder / risk introduced by executing the plan.
- `Cost` is expected resource consumption (time, compute, tokens, etc).
- `LearningValue` is the epistemic gain delivered to the system, independent of success
- `λ` is a system-wide entropy penalty coefficient.
- `μ` is a system-wide learning value coefficient.

**Bootstrap λ (naive):**

- `λ = 0.3`
- `μ = 0.5`

**Notes:**

- EV is used primarily for _ranking plan variants_.
- EV does not need to be in “real dollars”; it just needs consistent units.

---

### 1.3 Success: meaning and measurement

**Semantic lock-in definition:**

- `success_actual = 1` if the intent’s acceptance criteria are met.
- `success_actual = 0` otherwise.

**Bootstrap acceptance criteria (typical coding intent):**

- All tests pass (or the intent explicitly allows failures).
- Lint/format constraints satisfied if required.
- No sandbox violations occurred.
- No unresolved Git conflicts remain.
- Required artifacts are produced.

**Optional partial success (later phase):**

- `success_actual ∈ [0,1]` based on weighted checks.
- If you adopt this, keep the **meaning** consistent: it becomes “degree of acceptance satisfaction.”

---

### 1.4 Calibration Errors

Used to improve estimators.

- Success calibration error (per execution):
  - `p_success_error = |p_success_pred - success_actual|`

- Entropy calibration error:
  - `entropy_error = |entropy_pred - entropy_actual|`

- Learning Value calibration error:
  - `learning_value_error = |learning_value_pred - learning_value_actual|`

Aggregate over windows (e.g., last 7 days) to detect drift.

---

## 2) Per-Intent Entropy (ΔS_intent)

### 2.1 Meaning

**Per-intent entropy** estimates the disorder / risk introduced by executing _this_ plan in the system.

It is intentionally not “Shannon entropy”; it’s a **risk/complexity proxy** that must be:

- computable before execution (prediction),
- measurable after execution (actual),
- comparable across plans.

For detailed definitions of the constituent factors (SSA, IRR, CL, SER, NOV), see the [Appendix](appendix.md).

---

### 2.2 Predicted Entropy Formula (Bootstrap)

Define:

$$\Delta S_{intent,pred} = w_1\cdot SSA + w_2\cdot IRR + w_3\cdot CL + w_4\cdot SER + w_5\cdot NOV$$

Where each term is normalised onto a comparable scale (recommended 0–10 for bootstrap).

#### Terms

- `SSA` (State Surface Area):
  - expected number of files changed, LOC changed, config surface area, etc.
- `IRR` (Irreversibility):
  - expected difficulty of reverting changes (migrations, schema changes, destructive ops).
- `CL` (Conflict Likelihood):
  - expected probability of rebase/merge conflicts (file overlap, hot files, concurrent sub-intents).
- `SER` (Sandbox Escape Risk):
  - expected probability of violating sandbox constraints (network, filesystem, secrets).
- `NOV` (Novelty):
  - how unfamiliar the approach/domain is relative to KB + recent ledger outcomes.

#### Bootstrap weights (naive)

- `w1 = 0.30`
- `w2 = 0.25`
- `w3 = 0.20`
- `w4 = 0.15`
- `w5 = 0.10`

**Rationale:**

- Surface area and irreversibility dominate because they correlate strongly with downstream cleanup cost.

---

### 2.3 Measuring Actual Per-Intent Entropy (Bootstrap)

Actual entropy is measured post-execution using observed signals.

Define:

$$\Delta S_{intent,actual} = u_1\cdot SSA_{obs} + u_2\cdot IRR_{obs} + u_3\cdot CL_{obs} + u_4\cdot SER_{obs} + u_5\cdot NOV_{obs}$$

#### Observable approximations (day-zero)

- `SSA_obs`:
  - `files_changed`
  - `lines_added + lines_deleted`
  - `config_files_touched` (e.g., CI, build, dependency manifests)
  - (normalise to 0–10)

- `IRR_obs`:
  - touched migration files? destructive scripts? schema diffs?
  - if none, low
  - (normalise to 0–10)

- `CL_obs`:
  - rebase conflict occurred? (`0/1`)
  - number of conflict hunks
  - number of “hot files” touched
  - (normalise to 0–10)

- `SER_obs`:
  - any sandbox policy violation? (`0/1`)
  - blocked syscalls/network attempts count
  - (normalise to 0–10)

- `NOV_obs`:
  - novelty cannot be measured perfectly; bootstrap uses:
    - “new dependency added?” (0/1)
    - “new module path created?” (0/1)
    - “KB has matching pattern?” (yes/no)
  - (normalise to 0–10)

**Bootstrap simplification:**

- Use the same weights `u_i = w_i` until you have evidence to change them (human-approved).

---

## 3) P(success)

### 3.1 Meaning

`P(success)_pred` is the estimated probability that executing the plan will satisfy acceptance criteria.

---

### 3.2 Bootstrap estimator (one simple option)

A bootstrap estimator can be a weighted feature model:

$$P(success) = clamp(0,1,\; b + \sum_i a_i f_i)$$

Example features:

- `f_coverage`: test coverage expectations / presence of tests
- `f_dep_conf`: dependency confidence (known stable libs vs unknown)
- `f_plan_depth`: number of steps / sub-intents
- `f_novelty`: novelty score (higher novelty reduces probability)
- `f_model_fit`: model capability match (e.g., using a `reasoning_mode` model for complex refactors).

You can keep the exact coefficients in code; what matters here is the semantic meaning:

- Higher novelty and deeper plans tend to reduce success probability.
- Better coverage and dependency confidence increase it.
- Correct architectural alignment (model fit) provides a positive coefficient.

---

## 4) Impact

### 4.1 Meaning

Impact estimates the benefit delivered if the intent succeeds.

Impact is not dollars; it’s a **relative utility score** used for ranking.

---

### 4.2 Bootstrap impact heuristics

Bootstrap scoring sources (examples):

- `test_count_added` (more tests = higher reliability impact)
- `critical_path_touched` (core modules)
- `feature_enablement` (unblocks next intents)
- `kb_value` (adds a reusable pattern or failure mode)
- `bug_severity` (if intent fixes a production issue)

Normalise impact to a typical range like 0–100.

---

## 5) Learning Value

### 5.1 Meaning

`learning_value` estimates the **epistemic gain** delivered to the system by executing this intent, **independent of
whether the intent succeeds or fails**.

It captures the value of:

- Exploring low-probability or novel approaches.
- Producing failure modes that improve future P(success) estimators.
- Generating KB entries (patterns, anti-patterns, constraints) that benefit future agents.
- Reducing NOV (Novelty) for future similar intents.

Learning value is the primary justification for executing high-`entropy`, low-`p_success` intents. Without it, the EV
formula would always penalise exploration and the system would converge prematurely on known-safe paths.

---

### 5.2 Bootstrap scoring heuristics (0–10 scale)

| Score | Meaning                                                                   |
| ----- | ------------------------------------------------------------------------- |
| 0–2   | Routine, well-understood work. No new knowledge expected.                 |
| 3–5   | Moderate novelty. Likely to produce reusable KB patterns.                 |
| 6–8   | High novelty or deliberate spike. Expected to reduce future entropy.      |
| 9–10  | Pure exploration. Primary value is failure-mode discovery or calibration. |

### 5.3 Predicted vs Actual

- `learning_value_pred`: estimated before execution by the planner.
- `learning_value_actual`: assessed post-execution by the evaluator based on:

- KB entries created or updated.
- Calibration improvement (reduction in CD(t)).
- `NOV` reduction for similar future intents.
- Documented failure modes added to KB.

### 5.4 Calibration error

```
learning_value_error = |learning_value_pred - learning_value_actual|
```

Aggregate over windows alongside other calibration errors (see §1.4).

### 5.5 Bootstrap μ rationale

- `μ = 0.5` (lower than the implicit weight on Impact) ensures learning value does not dominate over delivering actual
  results, but is sufficient to justify high-entropy exploratory steps.
- Agents may propose adjusting μ via a meta-intent once calibration data exists.

---

## 6) Cost

### 6.1 Meaning

Cost estimates the resources consumed to execute the plan.

---

### 6.2 Model-Aware Cost Estimation (Bootstrap)

The `cost_pred` estimator is influenced by the model's architectural traits and modalities.

- **Base Token Cost:** Predicted tokens / wall time.
- **Modality Premium:** If `active_in_session` includes non-text modalities (e.g., image, audio), a `sensory_premium`
  multiplier is applied to the base cost.
- **Architecture Weight:** `moe` or `dense` frontier models are priced with higher normalised units than `distilled` or
  `quantised` models.
- **Reasoning Surcharge:** Models in `reasoning_mode` are weighted with a higher "latency cost" (compensating for longer
  wall-times during the internal thinking phase).

Normalise to a comparable scale (0–200 typical).

---

## 7) System-Level Entropy (S_system)

### 7.1 Meaning

**System entropy** measures overall “disorder” / “instability” across the entire repo + agent swarm.

It’s used for:

- health monitoring,
- spawning maintenance intents (cleanup, recalibration),
- preventing runaway divergence.

This is distinct from per-intent entropy:

- per-intent entropy is about a single plan’s risk.
- system entropy is about global state complexity.

---

### 7.2 Proposed S_system formula (Bootstrap)

Define:

$$S_{system}(t) = \alpha\cdot BD(t) + \beta\cdot KF(t) + \gamma\cdot CD(t) + \delta\cdot ATV(t) + \varepsilon\cdot UC(t)$$

Where:

- `BD(t)` Branch Divergence
- `KF(t)` Knowledge Fragmentation
- `CD(t)` Calibration Drift
- `ATV(t)` Agent Trust Variance
- `UC(t)` Unresolved Conflicts

#### (A) Branch Divergence `BD(t)`

Measures how spread-out the live state is.

Bootstrap definition:

$$BD(t) = \sum_{b \in ActiveBranches} ahead(b)\cdot ageDays(b)$$

Where:

- `ahead(b)` = commits ahead of `main`
- `ageDays(b)` = days since branch created or last updated

#### (B) Knowledge Fragmentation `KF(t)`

Measures inconsistency / decay in KB.

Bootstrap definition:

$$KF(t) = \frac{deprecated}{total} + \frac{conflicts}{max(1,totalPatterns)}$$

Where:

- `deprecated` = KB entries marked deprecated
- `conflicts` = KB entries flagged as contradictory

#### (C) Calibration Drift `CD(t)`

Measures estimator miscalibration.

Bootstrap definition:

$$CD(t) = mean(|P_{pred} - success_{actual}|)$$

Over a sliding window (e.g., last N=50 executions).

#### (D) Agent Trust Variance `ATV(t)`

Measures instability across agent trust.

Bootstrap definition:

$$ATV(t) = Var(trustScores)$$

#### (E) Unresolved Conflicts `UC(t)`

Measures blocking issues.

Bootstrap definition:

$$UC(t) = rebaseConflicts + failedIntentsUnresolved + staleBranches$$

---

### 7.3 Bootstrap coefficients

Start with equal weights unless you have a reason:

- `α = 1.0`
- `β = 1.0`
- `γ = 1.0`
- `δ = 1.0`
- `ε = 1.0`

**Later improvement path:**

- learn weights by correlating each term with maintenance workload / failure rates.

---

## 8) Maintenance Triggers from System Entropy

These are policy examples (tune later).

### 8.1 High system entropy

If `S_system(t) > S_high`:

- spawn maintenance intents:
  - “merge or prune stale branches”
  - “resolve rebase conflicts”
  - “prune deprecated KB entries”
  - “recalibrate P(success) and entropy estimators”

Example thresholds:

- `S_high = 150`

### 8.2 Calibration drift trigger

If `CD(t) > CD_high`:

- spawn “improve P(success) estimator”
- spawn “audit entropy predictor”

Example threshold:

- `CD_high = 0.40`

### 8.3 Conflict pressure trigger

If `UC(t) > UC_high`:

- spawn “conflict reduction policy” intent
- enforce sequential merges for hot files

Example threshold:

- `UC_high = 10`

---

## 9) Ledger Requirements (Minimum)

For calibration and measurement, the ledger must store:

- plan selection:
  - `intent_id`, `plan_id`, `variant_id`
  - predicted: `p_success_pred`, `entropy_pred`, `impact_pred`, `cost_pred`, `learning_value_pred`, `ev_pred`
  - routing: `model_id`, `routing_reason`

- execution outcome:
  - `success_actual`
  - actual: `entropy_actual`, `impact_actual`, `cost_actual`, `learning_value_actual`
  - git: rebases attempted, conflicts, resolved, merge targets
  - sandbox: violations, blocked actions

---

## 10) Practical Notes (Bootstrapping)

- Keep normalisation simple and consistent.
- Do not overfit early; record data first.
- “Entropy” here is a **risk proxy**; later you can add more principled components, but do not change its meaning
  without a human-approved meta-change.

---

## 11) Quick Reference

### Per-intent entropy (predicted)

$$\Delta S_{intent,pred} = w_1\cdot SSA + w_2\cdot IRR + w_3\cdot CL + w_4\cdot SER + w_5\cdot NOV$$

### System entropy

$$S_{system}(t) = \alpha\cdot BD(t) + \beta\cdot KF(t) + \gamma\cdot CD(t) + \delta\cdot ATV(t) + \varepsilon\cdot UC(t)$$

### Expected value

$$EV = P(success)\cdot Impact + \mu\cdot LearningValue - \lambda \cdot \Delta S_{intent} - Cost$$

**Bootstrap defaults:**

- `λ = 0.3`
- `μ = 0.5`
- `w = [0.30, 0.25, 0.20, 0.15, 0.10]`
- `α=β=γ=δ=ε=1.0`

---

## 12) System Validation and Testing Strategies

To ensure that the Holon engine operates reliably and that its decision estimators improve over time, system operators
must use the following validation and testing strategies.

### 12.1 Estimator Calibration Validation

We measure the accuracy of predicted metrics against actual post-execution outcomes logged in the Ledger:

- **P(success) Calibration (Brier Score):** We calculate the Brier Score over the last $N = 100$ executions:
  $$BS = \frac{1}{N} \sum_{t=1}^{N} (P(success)_{pred,t} - success_{actual,t})^2$$ _Target:_ A well-calibrated system
  should maintain a Brier Score $BS \le 0.15$.
- **Entropy Prediction Error (MAE):** We calculate the Mean Absolute Error for Per-Intent Entropy predictions:
  $$MAE_{\Delta S} = \frac{1}{N} \sum_{t=1}^{N} |\Delta S_{intent,pred,t} - \Delta S_{intent,actual,t}|$$ _Target:_
  $MAE_{\Delta S} \le 1.5$ (on the 0–10 normalized scale).

If either metric drifts above targets, it indicates estimator degradation, triggering the Curator Agent to recalculate
weights or suggest new model routing parameters.

### 12.2 Estimator Backtesting Workflow

Before promoting a proposed estimator or weight configuration in the KB (`proposed` $\to$ `active`), the curator must
run a simulation backtest:

1. **Replay Dataset:** Extract the last $M = 200$ completed executions from the Evolution Ledger.
2. **Recompute Predictions:** Run the proposed formula against the pre-execution features recorded in those ledger
   entries.
3. **Compare Performance:** Verify that the proposed formula reduces the Brier Score or $MAE_{\Delta S}$ compared to the
   currently active formula on the same dataset. The new config is only promoted if it yields a statistically
   significant improvement.

### 12.3 Sandbox Escape Drills (Security Chaos Testing)

To validate that the sandboxing and trust degradation mechanisms are functioning correctly, operators should execute
periodic security drills:

- **Honeypot Intents:** Deploy test intents that contain hidden malicious payloads (e.g., trying to write to
  `/etc/shadow`, binding a port, or attempting outbound connections to an external server).
- **Validation Criteria:** The test succeeds only if:
  1. The sandbox execution process is instantly terminated.
  2. A `sandbox_escape_attempted` event is logged in the ledger.
  3. The agent is degraded to **Baseline** trust.
  4. The orchestrator halts execution and requests human review.

### 12.4 Git Integration Tests

Validate the harness's Git flow compliance by running mock intent trees:

- **Rebase and Merge Accuracy (target model; the current flow runs sibling sub-intents one at a time and the maintainer
  merges their PRs):** Simulate two sibling sub-intents modifying overlapping files concurrently. Verify that the
  harness detects the rebase conflict, spawns a reactive conflict resolution intent, successfully merges both without
  human intervention, and outputs a clean git history.
- **Schema Conformance:** Run validation checks to ensure all intermediate and final ledger entries conform to
  [ledger_schema.md](ledger_schema.md).
