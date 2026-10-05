# Plan Calibration Report: P-1791151683-antigravity-agent-gemini-3.8-flash-medium

- **Plan Reference:**
  [`plans/P-1791151683-antigravity-agent-gemini-3.8-flash-medium.md`](P-1791151683-antigravity-agent-gemini-3.8-flash-medium.md)
- **Execution Reference:**
  [`executions/E-1791151927-antigravity-agent-gemini-3.8-flash-medium.md`](../executions/E-1791151927-antigravity-agent-gemini-3.8-flash-medium.md)
- **Intent Branch:** `I-1791151674-calibration-integrity-resilience-and-staleness-detection/_`
- **Evaluating Agent:** `antigravity-agent/gemini-3.8-flash-medium`
- **Evaluation Timestamp:** `2026-10-05T07:42:55.000Z`
- **Evaluated Commit SHA:** `c95abcc826b1771cb411752a10529229cbc2dc12`

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**   | `0.92`             | `1.00`            | `0.08`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `1.50`             | `2.91`            | `1.41`                                | Moderate        | Underestimated Risk        |
| **Impact**                | `95.00`            | `95.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                  | `3.50`             | `2.98`            | `0.52`                                | High (≤ 1.0)    | Highly Accurate            |
| **Learning Value**        | `5.00`             | `5.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value ($EV$)** | `85.95`            | `93.65`           | `7.70`                                | Moderate        | Conservative Underestimate |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.92 - 1.00| = 0.08$
- **Entropy Error:** $|1.50 - 2.91| = 1.41$
- **Impact Error:** $|95.00 - 95.00| = 0.00$
- **Cost Error:** $|3.50 - 2.98| = 0.52$
- **Learning Value Error:** $|5.00 - 5.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.92 \times 95.0 + 0.5 \times 5.0 - 0.3 \times 1.50 - 3.50 = 85.95$$
  $$EV_{\text{actual}} = 1.00 \times 95.0 + 0.5 \times 5.0 - 0.3 \times 2.91 - 2.98 = 93.65$$ $$\Delta EV = +7.70$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `9.6` (12 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1791151683-antigravity-agent-gemini-3.8-flash-medium` executed with minimal error ($p\_success\_error = 0.08$,
$cost\_error = 0.52$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
