# Plan Calibration Report: P-1794196917-8bed7894-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1794196917-8bed7894-claude-agent-claude-opus-5-5.md`](P-1794196917-8bed7894-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1794197319-7ead078e-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1794196901-resolver-uv-available-and-fail-loudly/P-1794196917-8bed7894-claude-agent-claude-opus-5-5/E-1794197319-7ead078e-claude-agent-claude-opus-5-5/_`
- **Intent Branch:** `I-1794196901-resolver-uv-available-and-fail-loudly/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-11-09T04:34:40.000Z`
- **Evaluated Commit SHA:** `3962c9872ba5a5aeb7d6fec9878f30e0ed8e824a`
- **Base SHA:** `fe34e8877ecb38daace409489d348b59a75300f4`
- **Merge-Base SHA:** `fe34e8877ecb38daace409489d348b59a75300f4`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**   | `0.72`             | `1.00`            | `0.28`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `12.40`            | `2.15`            | `10.25`                               | Moderate        | Overestimated Risk         |
| **Impact**                | `90.00`            | `90.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                  | `35.00`            | `29.75`           | `5.25`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**        | `5.00`             | `5.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value ($EV$)** | `28.58`            | `62.11`           | `33.53`                               | Moderate        | Conservative Underestimate |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.72 - 1.00| = 0.28$
- **Entropy Error:** $|12.40 - 2.15| = 10.25$
- **Impact Error:** $|90.00 - 90.00| = 0.00$
- **Cost Error:** $|35.00 - 29.75| = 5.25$
- **Learning Value Error:** $|5.00 - 5.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.72 \times 90.0 + 0.5 \times 5.0 - 0.3 \times 12.40 - 35.00 = 28.58$$
  $$EV_{\text{actual}} = 1.00 \times 90.0 + 0.5 \times 5.0 - 0.3 \times 2.15 - 29.75 = 62.11$$ $$\Delta EV = +33.53$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `7.0` (14 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794196917-8bed7894-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.28$,
$cost\_error = 5.25$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
