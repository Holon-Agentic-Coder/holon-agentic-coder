# Plan Calibration Report: P-1793246383-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1793246383-claude-agent-claude-opus-5-5.md`](P-1793246383-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1793246576-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1793246367-url-fuzz-leak-check-ignores-random-temp-paths/P-1793246383-claude-agent-claude-opus-5-5/E-1793246576-claude-agent-claude-opus-5-5/_`
- **Intent Branch:** `I-1793246367-url-fuzz-leak-check-ignores-random-temp-paths/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-10-29T04:28:03.000Z`
- **Evaluated Commit SHA:** `d017043ec150c63c6733b371f99efcc7dce9c5ff`
- **Base SHA:** `ff604b68ef1d39b5a696c52e7d1374cc562a4fb5`
- **Merge-Base SHA:** `ff604b68ef1d39b5a696c52e7d1374cc562a4fb5`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**   | `0.85`             | `1.00`            | `0.15`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `2.20`             | `0.36`            | `1.84`                                | Moderate        | Overestimated Risk         |
| **Impact**                | `70.00`            | `70.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                  | `10.00`            | `8.50`            | `1.50`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**        | `3.00`             | `3.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value ($EV$)** | `50.34`            | `62.89`           | `12.55`                               | Moderate        | Conservative Underestimate |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.85 - 1.00| = 0.15$
- **Entropy Error:** $|2.20 - 0.36| = 1.84$
- **Impact Error:** $|70.00 - 70.00| = 0.00$
- **Cost Error:** $|10.00 - 8.50| = 1.50$
- **Learning Value Error:** $|3.00 - 3.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.85 \times 70.0 + 0.5 \times 3.0 - 0.3 \times 2.20 - 10.00 = 50.34$$
  $$EV_{\text{actual}} = 1.00 \times 70.0 + 0.5 \times 3.0 - 0.3 \times 0.36 - 8.50 = 62.89$$ $$\Delta EV = +12.55$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `1.1` (1 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1793246383-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.15$,
$cost\_error = 1.50$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
