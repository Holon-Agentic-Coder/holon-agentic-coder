# Plan Calibration Report: P-1793921914-antigravity-agent-gemini-4-argon

- **Plan Reference:**
  [`plans/P-1793921914-antigravity-agent-gemini-4-argon.md`](P-1793921914-antigravity-agent-gemini-4-argon.md)
- **Execution ID:** `E-1793922181-antigravity-agent-gemini-4-argon`
- **Execution Branch:**
  `I-1793921896-bump-default-turn-timeout-to-1-hour/P-1793921914-antigravity-agent-gemini-4-argon/E-1793922181-antigravity-agent-gemini-4-argon/_`
- **Intent Branch:** `I-1793921896-bump-default-turn-timeout-to-1-hour/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-argon`
- **Evaluation Timestamp:** `2026-11-06T00:03:35.000Z`
- **Evaluated Commit SHA:** `d924d662151175a90f55a74c53e10d70945d4b71`
- **Base SHA:** `a980eafe76fa87c1dd880f03f0bfa38c71ec2f16`
- **Merge-Base SHA:** `a980eafe76fa87c1dd880f03f0bfa38c71ec2f16`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**   | `0.96`             | `1.00`            | `0.04`                                | High (≤ 0.05)   | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `3.20`             | `0.40`            | `2.80`                                | Moderate        | Overestimated Risk         |
| **Impact**                | `78.00`            | `78.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                  | `4.50`             | `3.82`            | `0.68`                                | High (≤ 1.0)    | Highly Accurate            |
| **Learning Value**        | `2.50`             | `2.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value ($EV$)** | `70.67`            | `75.31`           | `4.64`                                | High            | Conservative Underestimate |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.96 - 1.00| = 0.04$
- **Entropy Error:** $|3.20 - 0.40| = 2.80$
- **Impact Error:** $|78.00 - 78.00| = 0.00$
- **Cost Error:** $|4.50 - 3.82| = 0.68$
- **Learning Value Error:** $|2.50 - 2.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.96 \times 78.0 + 0.5 \times 2.5 - 0.3 \times 3.20 - 4.50 = 70.67$$
  $$EV_{\text{actual}} = 1.00 \times 78.0 + 0.5 \times 2.5 - 0.3 \times 0.40 - 3.82 = 75.31$$ $$\Delta EV = +4.64$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `1.2` (5 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1793921914-antigravity-agent-gemini-4-argon` executed with minimal error ($p\_success\_error = 0.04$,
$cost\_error = 0.68$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
