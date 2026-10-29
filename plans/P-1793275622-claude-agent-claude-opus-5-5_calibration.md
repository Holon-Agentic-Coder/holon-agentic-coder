# Plan Calibration Report: P-1793275622-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1793275622-claude-agent-claude-opus-5-5.md`](P-1793275622-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1793275792-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1793275605-ledger-writes-are-pure-appends-never-reorder-intents/P-1793275622-claude-agent-claude-opus-5-5/E-1793275792-claude-agent-claude-opus-5-5/_`
- **Intent Branch:** `I-1793275605-ledger-writes-are-pure-appends-never-reorder-intents/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-10-29T15:04:32.000Z`
- **Evaluated Commit SHA:** `b8dde777831869ae42db1926444b4a9589f7443b`
- **Base SHA:** `740d16327b3c23892735ebae9208a288968b010e`
- **Merge-Base SHA:** `740d16327b3c23892735ebae9208a288968b010e`
- **Merged Commits:** `1`

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**   | `0.82`             | `1.00`            | `0.18`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `6.10`             | `3.04`            | `3.06`                                | Moderate        | Overestimated Risk         |
| **Impact**                | `82.00`            | `82.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                  | `14.00`            | `11.90`           | `2.10`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**        | `5.00`             | `5.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value ($EV$)** | `53.91`            | `71.69`           | `17.78`                               | Moderate        | Conservative Underestimate |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.82 - 1.00| = 0.18$
- **Entropy Error:** $|6.10 - 3.04| = 3.06$
- **Impact Error:** $|82.00 - 82.00| = 0.00$
- **Cost Error:** $|14.00 - 11.90| = 2.10$
- **Learning Value Error:** $|5.00 - 5.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.82 \times 82.0 + 0.5 \times 5.0 - 0.3 \times 6.10 - 14.00 = 53.91$$
  $$EV_{\text{actual}} = 1.00 \times 82.0 + 0.5 \times 5.0 - 0.3 \times 3.04 - 11.90 = 71.69$$ $$\Delta EV = +17.78$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `10.0` (12 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1793275622-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.18$,
$cost\_error = 2.10$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
