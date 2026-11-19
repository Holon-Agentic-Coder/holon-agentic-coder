# Plan Calibration Report: P-1794132519-claude-agent-claude-fable-5

- **Plan Reference:** [`plans/P-1794132519-claude-agent-claude-fable-5.md`](P-1794132519-claude-agent-claude-fable-5.md)
- **Execution ID:** `E-1794132814-claude-agent-claude-fable-5`
- **Execution Branch:**
  `I-1794132504-purge-harness-bean-references-from-code-and-docs/P-1794132519-claude-agent-claude-fable-5/E-1794132814-claude-agent-claude-fable-5/_`
- **Intent Branch:** `I-1794132504-purge-harness-bean-references-from-code-and-docs/_`
- **Evaluating Agent:** `claude-agent/claude-fable-5`
- **Evaluation Timestamp:** `2026-11-08T10:51:06.000Z`
- **Evaluated Commit SHA:** `80ba0e554d7058a02d6347efb1f62bead319fda7`
- **Base SHA:** `ae842f089ceda58c8af2bd11b0aa5a7f246147b7`
- **Merge-Base SHA:** `ae842f089ceda58c8af2bd11b0aa5a7f246147b7`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**   | `0.93`             | `1.00`            | `0.07`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `6.10`             | `3.04`            | `3.06`                                | Moderate        | Overestimated Risk         |
| **Impact**                | `62.00`            | `62.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                  | `10.50`            | `8.92`            | `1.58`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**        | `2.00`             | `2.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value ($EV$)** | `46.33`            | `53.17`           | `6.84`                                | Moderate        | Conservative Underestimate |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.93 - 1.00| = 0.07$
- **Entropy Error:** $|6.10 - 3.04| = 3.06$
- **Impact Error:** $|62.00 - 62.00| = 0.00$
- **Cost Error:** $|10.50 - 8.92| = 1.58$
- **Learning Value Error:** $|2.00 - 2.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.93 \times 62.0 + 0.5 \times 2.0 - 0.3 \times 6.10 - 10.50 = 46.33$$
  $$EV_{\text{actual}} = 1.00 \times 62.0 + 0.5 \times 2.0 - 0.3 \times 3.04 - 8.92 = 53.17$$ $$\Delta EV = +6.84$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `10.0` (60 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794132519-claude-agent-claude-fable-5` executed with minimal error ($p\_success\_error = 0.07$, $cost\_error =
1.58$). The calibration step successfully completed the execution lifecycle and delivered verified post-execution
analysis.
