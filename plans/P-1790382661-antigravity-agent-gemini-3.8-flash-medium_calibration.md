# Plan Calibration Report: P-1790382661-antigravity-agent-gemini-3.8-flash-medium

- **Plan Reference:** [`plans/P-1790382661-antigravity-agent-gemini-3.8-flash-medium.md`](P-1790382661-antigravity-agent-gemini-3.8-flash-medium.md)
- **Execution Reference:** [`executions/E-1790401125-antigravity-agent-gemini-3.8-flash-medium.md`](../executions/E-1790401125-antigravity-agent-gemini-3.8-flash-medium.md)
- **Intent Branch:** `I-1790382620-make-sandbox-executor-tests-hermetic/_`
- **Evaluating Agent:** `antigravity-agent/gemini-3.8-flash-medium`
- **Evaluation Timestamp:** `2026-09-27T11:27:57.000Z`

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating   | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :---------------- | :------------------------- |
| **$P(\text{success})$**   | `0.98`             | `1.00`            | `0.02`                                | High (≤ 0.05)     | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `0.60`             | `0.10`            | `0.50`                                | High (≤ 1.0)      | Overestimated Risk         |
| **Impact**                | `65.00`            | `65.00`           | `0.00`                                | Exact             | Perfectly Calibrated       |
| **Cost**                  | `2.00`             | `1.70`            | `0.30`                                | High (≤ 1.0)      | Highly Accurate            |
| **Learning Value**        | `2.00`             | `2.00`            | `0.00`                                | Exact             | Perfectly Calibrated       |
| **Expected Value ($EV$)** | `62.52`            | `64.27`           | `1.75`                                | High              | Conservative Underestimate |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.98 - 1.00| = 0.02$
- **Entropy Error:** $|0.60 - 0.10| = 0.50$
- **Impact Error:** $|65.00 - 65.00| = 0.00$
- **Cost Error:** $|2.00 - 1.70| = 0.30$
- **Learning Value Error:** $|2.00 - 2.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.98 \times 65.0 + 0.5 \times 2.0 - 0.3 \times 0.60 - 2.00 = 62.52$$
  $$EV_{\text{actual}} = 1.00 \times 65.0 + 0.5 \times 2.0 - 0.3 \times 0.10 - 1.70 = 64.27$$
  $$\Delta EV = +1.75$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `0.2` (0 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1790382661-antigravity-agent-gemini-3.8-flash-medium` executed with minimal error ($p\_success\_error = 0.02$, $cost\_error = 0.30$). The calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.
