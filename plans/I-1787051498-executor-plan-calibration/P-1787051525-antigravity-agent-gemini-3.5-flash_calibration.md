# Plan Calibration Report: P-1787051525-antigravity-agent-gemini-3.5-flash

- **Plan Reference:**
  [`plans/P-1787051525-antigravity-agent-gemini-3.5-flash.md`](P-1787051525-antigravity-agent-gemini-3.5-flash.md)
- **Execution Reference:**
  [`executions/E-1787051559-antigravity-agent-gemini-3.5-flash.md`](../executions/E-1787051559-antigravity-agent-gemini-3.5-flash.md)
- **Intent Branch:** `I-1787051498-executor-plan-calibration/_`
- **Evaluating Agent:** `antigravity-agent/gemini-3.5-flash`
- **Evaluation Timestamp:** `2026-08-18T11:13:50.000Z`

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating   | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :---------------- | :------------------------- |
| **$P(\text{success})$**   | `0.98`             | `1.00`            | `0.02`                                | High ($\le 0.05$) | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `1.20`             | `0.40`            | `0.80`                                | High ($\le 1.0$)  | Overestimated Risk         |
| **Impact**                | `35.00`            | `35.00`           | `0.00`                                | Exact             | Perfectly Calibrated       |
| **Cost**                  | `4.00`             | `3.50`            | `0.50`                                | High ($\le 1.0$)  | Highly Accurate            |
| **Learning Value**        | `2.50`             | `2.50`            | `0.00`                                | Exact             | Perfectly Calibrated       |
| **Expected Value ($EV$)** | `31.19`            | `32.63`           | `1.44`                                | High              | Conservative Underestimate |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.98 - 1.00| = 0.02$
- **Entropy Error:** $|1.20 - 0.40| = 0.80$
- **Impact Error:** $|35.00 - 35.00| = 0.00$
- **Cost Error:** $|4.00 - 3.50| = 0.50$
- **Learning Value Error:** $|2.50 - 2.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.98 \times 35.0 + 0.5 \times 2.5 - 0.3 \times 1.20 - 4.00 = 31.19$$
  $$EV_{\text{actual}} = 1.00 \times 35.0 + 0.5 \times 2.5 - 0.3 \times 0.40 - 3.50 = 32.63$$ $$\Delta EV = +1.44$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `0.4` (1 calibration file created, 1 execution log created).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.2` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1787051525` executed with minimal error ($p\_success\_error = 0.02$, $cost\_error = 0.50$). The calibration step
successfully completed the execution lifecycle and delivered verified post-execution analysis.
