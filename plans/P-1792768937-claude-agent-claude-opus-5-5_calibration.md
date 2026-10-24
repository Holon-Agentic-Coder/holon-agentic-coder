# Plan Calibration Report: P-1792768937-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1792768937-claude-agent-claude-opus-5-5.md`](P-1792768937-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1792769272-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1792765419-inherit-default-runner-from-harness-agent/P-1792768937-claude-agent-claude-opus-5-5/E-1792769272-claude-agent-claude-opus-5-5/_`
- **Intent Branch:** `I-1792765419-inherit-default-runner-from-harness-agent/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-10-24T04:45:10.000Z`
- **Evaluated Commit SHA:** `ba4b87d9f0157a7fcfb8f65d15376f0e2512c0a6`
- **Base SHA:** `ae03481d91cfd5546dc11fc3806c3def85a0cb6b`
- **Merge-Base SHA:** `ae03481d91cfd5546dc11fc3806c3def85a0cb6b`
- **Merged Commits:** `1`

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**   | `0.88`             | `1.00`            | `0.12`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `0.30`             | `3.04`            | `2.74`                                | Moderate        | Underestimated Risk        |
| **Impact**                | `6.00`             | `6.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                  | `2.00`             | `1.70`            | `0.30`                                | High (≤ 1.0)    | Highly Accurate            |
| **Learning Value**        | `2.00`             | `2.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value ($EV$)** | `4.19`             | `4.39`            | `0.20`                                | High            | Conservative Underestimate |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.88 - 1.00| = 0.12$
- **Entropy Error:** $|0.30 - 3.04| = 2.74$
- **Impact Error:** $|6.00 - 6.00| = 0.00$
- **Cost Error:** $|2.00 - 1.70| = 0.30$
- **Learning Value Error:** $|2.00 - 2.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.88 \times 6.0 + 0.5 \times 2.0 - 0.3 \times 0.30 - 2.00 = 4.19$$
  $$EV_{\text{actual}} = 1.00 \times 6.0 + 0.5 \times 2.0 - 0.3 \times 3.04 - 1.70 = 4.39$$ $$\Delta EV = +0.20$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `10.0` (21 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1792768937-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.12$, $cost\_error =
0.30$). The calibration step successfully completed the execution lifecycle and delivered verified post-execution
analysis.

---

## 5. Review-Loop Annotation: Known-Wrong Predicted Metrics (Bean 0146)

> Added during the Stage 4 review loop, after `holon calibrate` produced sections 1–4 above (unchanged on the
> `/calibrated` branch).

The `plans.jsonl` row for `P-1792768937-claude-agent-claude-opus-5-5` was written by a planner whose metrics parser let
the last metrics table in the plan win (fixed in PR #91), so it records the plan's Step 7 metrics instead of its
`## Overall Plan Metrics`. Calibration reads that row, so every **Predicted** value and every delta in sections 1–2 is
computed against the wrong baseline. The plan file's Overall values are:

| Metric         | Overall (plan file) | Recorded row (used above) |
| :------------- | :------------------ | :------------------------ |
| p_success_pred | 0.70                | 0.88                      |
| entropy_pred   | 10.2                | 0.3                       |
| impact_pred    | 90.0                | 6.0                       |
| cost_pred      | 30.0                | 2.0                       |
| learning_value | 5.0                 | 2.0                       |
| ev_pred        | 32.44               | 4.19                      |

The ledger row is append-only and is not rewritten. A plans-ledger correction row, and calibration that prefers it, are
tracked by Bean 0146; recompute this report's deltas once that lands.
