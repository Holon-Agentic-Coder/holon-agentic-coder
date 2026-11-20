# Plan Calibration Report: P-1790562483-antigravity-agent-gemini-3.8-flash-medium

- **Plan Reference:**
  [`plans/P-1790562483-antigravity-agent-gemini-3.8-flash-medium.md`](P-1790562483-antigravity-agent-gemini-3.8-flash-medium.md)
- **Execution Reference:**
  [`executions/E-1790563015-antigravity-agent-gemini-3.8-flash-medium.md`](../executions/E-1790563015-antigravity-agent-gemini-3.8-flash-medium.md)
- **Intent Branch:** `I-1790562445-preserve-parent-history-in-executor-git-recovery/_`
- **Evaluating Agent:** `antigravity-agent/gemini-3.8-flash-medium`
- **Evaluation Timestamp:** `2026-09-28T07:43:45.000Z`

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**   | `0.96`             | `1.00`            | `0.04`                                | High (≤ 0.05)   | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `0.80`             | `1.96`            | `1.16`                                | Moderate        | Underestimated Risk        |
| **Impact**                | `70.00`            | `70.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                  | `2.00`             | `1.70`            | `0.30`                                | High (≤ 1.0)    | Highly Accurate            |
| **Learning Value**        | `2.50`             | `2.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value ($EV$)** | `66.21`            | `68.96`           | `2.75`                                | High            | Conservative Underestimate |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.96 - 1.00| = 0.04$
- **Entropy Error:** $|0.80 - 1.96| = 1.16$
- **Impact Error:** $|70.00 - 70.00| = 0.00$
- **Cost Error:** $|2.00 - 1.70| = 0.30$
- **Learning Value Error:** $|2.50 - 2.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.96 \times 70.0 + 0.5 \times 2.5 - 0.3 \times 0.80 - 2.00 = 66.21$$
  $$EV_{\text{actual}} = 1.00 \times 70.0 + 0.5 \times 2.5 - 0.3 \times 1.96 - 1.70 = 68.96$$ $$\Delta EV = +2.75$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `6.4` (5 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1790562483-antigravity-agent-gemini-3.8-flash-medium` executed with minimal error ($p\_success\_error = 0.04$,
$cost\_error = 0.30$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
