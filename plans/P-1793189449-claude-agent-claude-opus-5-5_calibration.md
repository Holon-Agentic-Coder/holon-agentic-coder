# Plan Calibration Report: P-1793189449-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1793189449-claude-agent-claude-opus-5-5.md`](P-1793189449-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1793189685-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1793189433-review-loop-fail-closed-target-and-guards/P-1793189449-claude-agent-claude-opus-5-5/E-1793189685-claude-agent-claude-opus-5-5/_`
- **Intent Branch:** `I-1793189433-review-loop-fail-closed-target-and-guards/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-10-28T13:10:36.000Z`
- **Evaluated Commit SHA:** `575030be92e66c6d722de9cc21af97146b47693d`
- **Base SHA:** `46fe7a2ccd14f2eaaccce3ba30868a8df2b6e923`
- **Merge-Base SHA:** `46fe7a2ccd14f2eaaccce3ba30868a8df2b6e923`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**   | `0.78`             | `1.00`            | `0.22`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `9.00`             | `3.04`            | `5.96`                                | Moderate        | Overestimated Risk         |
| **Impact**                | `85.00`            | `85.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                  | `20.50`            | `17.43`           | `3.07`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**        | `5.00`             | `5.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value ($EV$)** | `45.60`            | `69.16`           | `23.56`                               | Moderate        | Conservative Underestimate |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.78 - 1.00| = 0.22$
- **Entropy Error:** $|9.00 - 3.04| = 5.96$
- **Impact Error:** $|85.00 - 85.00| = 0.00$
- **Cost Error:** $|20.50 - 17.43| = 3.07$
- **Learning Value Error:** $|5.00 - 5.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.78 \times 85.0 + 0.5 \times 5.0 - 0.3 \times 9.00 - 20.50 = 45.60$$
  $$EV_{\text{actual}} = 1.00 \times 85.0 + 0.5 \times 5.0 - 0.3 \times 3.04 - 17.43 = 69.16$$ $$\Delta EV = +23.56$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `10.0` (12 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1793189449-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.22$,
$cost\_error = 3.07$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
