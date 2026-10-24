# Plan Calibration Report: P-1792788153-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1792788153-claude-agent-claude-opus-5-5.md`](P-1792788153-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1792788550-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1792765424-incremental-artifacts-budgets-and-local-llm-envelope/P-1792788153-claude-agent-claude-opus-5-5/E-1792788550-claude-agent-claude-opus-5-5/_`
- **Intent Branch:** `I-1792765424-incremental-artifacts-budgets-and-local-llm-envelope/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-10-24T09:49:58.000Z`
- **Evaluated Commit SHA:** `b9864edb0c4f72b6a66a7d880d7897db6bac1fdd`
- **Base SHA:** `55fb98d93e693a2d9fde755276c83b678a715d16`
- **Merge-Base SHA:** `55fb98d93e693a2d9fde755276c83b678a715d16`
- **Merged Commits:** `3`

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**   | `0.85`             | `1.00`            | `0.15`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `0.50`             | `3.04`            | `2.54`                                | Moderate        | Underestimated Risk        |
| **Impact**                | `6.00`             | `6.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                  | `1.20`             | `1.02`            | `0.18`                                | High (≤ 1.0)    | Highly Accurate            |
| **Learning Value**        | `2.00`             | `2.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value ($EV$)** | `4.75`             | `5.07`            | `0.32`                                | High            | Conservative Underestimate |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.85 - 1.00| = 0.15$
- **Entropy Error:** $|0.50 - 3.04| = 2.54$
- **Impact Error:** $|6.00 - 6.00| = 0.00$
- **Cost Error:** $|1.20 - 1.02| = 0.18$
- **Learning Value Error:** $|2.00 - 2.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.85 \times 6.0 + 0.5 \times 2.0 - 0.3 \times 0.50 - 1.20 = 4.75$$
  $$EV_{\text{actual}} = 1.00 \times 6.0 + 0.5 \times 2.0 - 0.3 \times 3.04 - 1.02 = 5.07$$ $$\Delta EV = +0.32$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `10.0` (44 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1792788153-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.15$, $cost\_error =
0.18$). The calibration step successfully completed the execution lifecycle and delivered verified post-execution
analysis.

---

## 5. Review-Loop Annotation: Known-Wrong Predicted Metrics (Bean 0146)

> Added after `holon calibrate` produced sections 1–4 above (unchanged on the `/calibrated` branch).

The `plans.jsonl` row for `P-1792788153-claude-agent-claude-opus-5-5` was written by a planner whose metrics parser let
the last metrics table in the plan win (fixed in PR #91), so it records the plan's Step 10 metrics instead of its
`## Overall Plan Metrics`. Calibration reads that row, so every **Predicted** value and every delta in sections 1–2 is
computed against the wrong baseline. The plan file's Overall values are:

| Metric         | Overall (plan file) | Recorded row (used above) |
| :------------- | :------------------ | :------------------------ |
| p_success_pred | 0.66                | 0.85                      |
| entropy_pred   | 14.6                | 0.5                       |
| impact_pred    | 92.0                | 6.0                       |
| cost_pred      | 19.5                | 1.2                       |
| learning_value | 6.0                 | 2.0                       |
| ev_pred        | 39.84               | 4.75                      |

The ledger row is append-only and is not rewritten. A plans-ledger correction row, and calibration that prefers it, are
tracked by Bean 0146; recompute this report's deltas once that lands.
