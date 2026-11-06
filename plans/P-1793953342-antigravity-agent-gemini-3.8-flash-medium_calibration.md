# Plan Calibration Report: P-1793953342-antigravity-agent-gemini-3.8-flash-medium

- **Plan Reference:**
  [`plans/P-1793953342-antigravity-agent-gemini-3.8-flash-medium.md`](P-1793953342-antigravity-agent-gemini-3.8-flash-medium.md)
- **Execution ID:** `E-1793953767-antigravity-agent-gemini-3.8-flash-medium`
- **Execution Branch:**
  `I-1793953326-fix-resolver-run-turn-argument-signature/P-1793953342-antigravity-agent-gemini-3.8-flash-medium/E-1793953767-antigravity-agent-gemini-3.8-flash-medium/_`
- **Intent Branch:** `I-1793953326-fix-resolver-run-turn-argument-signature/_`
- **Evaluating Agent:** `antigravity-agent/gemini-3.8-flash-medium`
- **Evaluation Timestamp:** `2026-11-06T08:50:15.000Z`
- **Evaluated Commit SHA:** `1307436053eadd790b88412657de2b3bfd4366c3`
- **Base SHA:** `3d4502d7f8aa4a65299bc369267961c8396b0945`
- **Merge-Base SHA:** `3d4502d7f8aa4a65299bc369267961c8396b0945`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**   | `0.93`             | `1.00`            | `0.07`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `0.90`             | `0.46`            | `0.44`                                | High (≤ 1.0)    | Overestimated Risk         |
| **Impact**                | `95.00`            | `95.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                  | `4.00`             | `3.40`            | `0.60`                                | High (≤ 1.0)    | Highly Accurate            |
| **Learning Value**        | `2.00`             | `2.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value ($EV$)** | `85.08`            | `92.46`           | `7.38`                                | Moderate        | Conservative Underestimate |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.93 - 1.00| = 0.07$
- **Entropy Error:** $|0.90 - 0.46| = 0.44$
- **Impact Error:** $|95.00 - 95.00| = 0.00$
- **Cost Error:** $|4.00 - 3.40| = 0.60$
- **Learning Value Error:** $|2.00 - 2.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.93 \times 95.0 + 0.5 \times 2.0 - 0.3 \times 0.90 - 4.00 = 85.08$$
  $$EV_{\text{actual}} = 1.00 \times 95.0 + 0.5 \times 2.0 - 0.3 \times 0.46 - 3.40 = 92.46$$ $$\Delta EV = +7.38$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `1.4` (2 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1793953342-antigravity-agent-gemini-3.8-flash-medium` executed with minimal error ($p\_success\_error = 0.07$,
$cost\_error = 0.60$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
