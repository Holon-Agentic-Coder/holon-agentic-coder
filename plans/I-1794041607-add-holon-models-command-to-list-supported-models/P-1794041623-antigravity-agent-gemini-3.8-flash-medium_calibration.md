# Plan Calibration Report: P-1794041623-antigravity-agent-gemini-3.8-flash-medium

- **Plan Reference:**
  [`plans/P-1794041623-antigravity-agent-gemini-3.8-flash-medium.md`](P-1794041623-antigravity-agent-gemini-3.8-flash-medium.md)
- **Execution ID:** `E-1794042006-antigravity-agent-gemini-3.8-flash-medium`
- **Execution Branch:**
  `I-1794041607-add-holon-models-command-to-list-supported-models/P-1794041623-antigravity-agent-gemini-3.8-flash-medium/E-1794042006-antigravity-agent-gemini-3.8-flash-medium/_`
- **Intent Branch:** `I-1794041607-add-holon-models-command-to-list-supported-models/_`
- **Evaluating Agent:** `antigravity-agent/gemini-3.8-flash-medium`
- **Evaluation Timestamp:** `2026-11-07T09:33:23.000Z`
- **Evaluated Commit SHA:** `87bd5d61165d599cc624aa4761f6369617c99147`
- **Base SHA:** `5949076d89b513959dc0b994f52570a640b97d0c`
- **Merge-Base SHA:** `5949076d89b513959dc0b994f52570a640b97d0c`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**   | `0.94`             | `1.00`            | `0.06`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `2.80`             | `1.51`            | `1.29`                                | Moderate        | Overestimated Risk         |
| **Impact**                | `88.00`            | `88.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                  | `9.50`             | `8.07`            | `1.43`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**        | `7.50`             | `7.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value ($EV$)** | `76.13`            | `83.23`           | `7.10`                                | Moderate        | Conservative Underestimate |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.94 - 1.00| = 0.06$
- **Entropy Error:** $|2.80 - 1.51| = 1.29$
- **Impact Error:** $|88.00 - 88.00| = 0.00$
- **Cost Error:** $|9.50 - 8.07| = 1.43$
- **Learning Value Error:** $|7.50 - 7.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.94 \times 88.0 + 0.5 \times 7.5 - 0.3 \times 2.80 - 9.50 = 76.13$$
  $$EV_{\text{actual}} = 1.00 \times 88.0 + 0.5 \times 7.5 - 0.3 \times 1.51 - 8.07 = 83.23$$ $$\Delta EV = +7.10$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `4.9` (6 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794041623-antigravity-agent-gemini-3.8-flash-medium` executed with minimal error ($p\_success\_error = 0.06$,
$cost\_error = 1.43$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
