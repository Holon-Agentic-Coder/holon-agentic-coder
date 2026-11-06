# Plan Calibration Report: P-1794003851-antigravity-agent-gemini-3.8-flash-medium

- **Plan Reference:**
  [`plans/P-1794003851-antigravity-agent-gemini-3.8-flash-medium.md`](P-1794003851-antigravity-agent-gemini-3.8-flash-medium.md)
- **Execution ID:** `E-1794004123-antigravity-agent-gemini-3.8-flash-medium`
- **Execution Branch:**
  `I-1794003835-fix-resolver-runner-build-cmd-attribute-error/P-1794003851-antigravity-agent-gemini-3.8-flash-medium/E-1794004123-antigravity-agent-gemini-3.8-flash-medium/_`
- **Intent Branch:** `I-1794003835-fix-resolver-runner-build-cmd-attribute-error/_`
- **Evaluating Agent:** `antigravity-agent/gemini-3.8-flash-medium`
- **Evaluation Timestamp:** `2026-11-06T22:48:37.000Z`
- **Evaluated Commit SHA:** `b5d9277979f0bc810768b4773703f245523e80bc`
- **Base SHA:** `49445ee4f4aa1f64d8d31cd5185b6bd9f13a67df`
- **Merge-Base SHA:** `49445ee4f4aa1f64d8d31cd5185b6bd9f13a67df`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**   | `0.96`             | `1.00`            | `0.04`                                | High (≤ 0.05)   | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `2.10`             | `0.51`            | `1.59`                                | Moderate        | Overestimated Risk         |
| **Impact**                | `100.00`           | `100.00`          | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                  | `4.50`             | `3.82`            | `0.68`                                | High (≤ 1.0)    | Highly Accurate            |
| **Learning Value**        | `6.00`             | `6.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value ($EV$)** | `93.87`            | `99.03`           | `5.16`                                | Moderate        | Conservative Underestimate |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.96 - 1.00| = 0.04$
- **Entropy Error:** $|2.10 - 0.51| = 1.59$
- **Impact Error:** $|100.00 - 100.00| = 0.00$
- **Cost Error:** $|4.50 - 3.82| = 0.68$
- **Learning Value Error:** $|6.00 - 6.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.96 \times 100.0 + 0.5 \times 6.0 - 0.3 \times 2.10 - 4.50 = 93.87$$
  $$EV_{\text{actual}} = 1.00 \times 100.0 + 0.5 \times 6.0 - 0.3 \times 0.51 - 3.82 = 99.03$$ $$\Delta EV = +5.16$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `1.6` (4 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794003851-antigravity-agent-gemini-3.8-flash-medium` executed with minimal error ($p\_success\_error = 0.04$,
$cost\_error = 0.68$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
