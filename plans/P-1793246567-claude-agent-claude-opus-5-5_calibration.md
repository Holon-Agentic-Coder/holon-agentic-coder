# Plan Calibration Report: P-1793246567-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1793246567-claude-agent-claude-opus-5-5.md`](P-1793246567-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1793246853-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1793246551-ci-timing-regression-gate/P-1793246567-claude-agent-claude-opus-5-5/E-1793246853-claude-agent-claude-opus-5-5/_`
- **Intent Branch:** `I-1793246551-ci-timing-regression-gate/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-10-29T04:53:35.000Z`
- **Evaluated Commit SHA:** `74113dbf4829d98ce28ccfd77fae1c38fa485125`
- **Base SHA:** `aa60f19c5a135f5c9a624d29cbd06a76c21cf333`
- **Merge-Base SHA:** `aa60f19c5a135f5c9a624d29cbd06a76c21cf333`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**   | `0.72`             | `1.00`            | `0.28`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `4.00`             | `2.35`            | `1.65`                                | Moderate        | Overestimated Risk         |
| **Impact**                | `80.00`            | `80.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                  | `18.00`            | `15.30`           | `2.70`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**        | `6.00`             | `6.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value ($EV$)** | `41.40`            | `67.00`           | `25.60`                               | Moderate        | Conservative Underestimate |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.72 - 1.00| = 0.28$
- **Entropy Error:** $|4.00 - 2.35| = 1.65$
- **Impact Error:** $|80.00 - 80.00| = 0.00$
- **Cost Error:** $|18.00 - 15.30| = 2.70$
- **Learning Value Error:** $|6.00 - 6.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.72 \times 80.0 + 0.5 \times 6.0 - 0.3 \times 4.00 - 18.00 = 41.40$$
  $$EV_{\text{actual}} = 1.00 \times 80.0 + 0.5 \times 6.0 - 0.3 \times 2.35 - 15.30 = 67.00$$ $$\Delta EV = +25.60$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `7.7` (9 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1793246567-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.28$,
$cost\_error = 2.70$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
