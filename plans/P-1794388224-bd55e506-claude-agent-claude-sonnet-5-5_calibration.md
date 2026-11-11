# Plan Calibration Report: P-1794388224-bd55e506-claude-agent-claude-sonnet-5-5

- **Plan Reference:**
  [`plans/P-1794388224-bd55e506-claude-agent-claude-sonnet-5-5.md`](P-1794388224-bd55e506-claude-agent-claude-sonnet-5-5.md)
- **Execution ID:** `E-1794388336-46c54247-claude-agent-claude-sonnet-5-5`
- **Execution Branch:**
  `I-1794388202-review-loop-waits-for-ci-outside-the-iteration-budget/P-1794388224-bd55e506-claude-agent-claude-sonnet-5-5/E-1794388336-46c54247-claude-agent-claude-sonnet-5-5/_`
- **Intent Branch:** `I-1794388202-review-loop-waits-for-ci-outside-the-iteration-budget/_`
- **Evaluating Agent:** `claude-agent/claude-sonnet-5-5`
- **Evaluation Timestamp:** `2026-11-11T09:55:54.000Z`
- **Evaluated Commit SHA:** `eecfe302eced1b010712643783c2a126439ae7d6`
- **Base SHA:** `c1146d187510856f5422b8f30c22ab636b4811a2`
- **Merge-Base SHA:** `c1146d187510856f5422b8f30c22ab636b4811a2`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.80`             | `1.00`            | `0.20`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `8.00`             | `1.92`            | `6.08`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `60.00`            | `60.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `20.00`            | `17.00`           | `3.00`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.00`             | `4.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `27.60`            | `44.42`           | `16.82`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `27.60`            | `44.42`           | `16.82`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.80 - 1.00| = 0.20$
- **Entropy Error:** $|8.00 - 1.92| = 6.08$
- **Impact Error:** $|60.00 - 60.00| = 0.00$
- **Cost Error:** $|20.00 - 17.00| = 3.00$
- **Learning Value Error:** $|4.00 - 4.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.80 \times 60.0 + 0.5 \times 4.0 - 0.3 \times 8.00 - 20.00 = 27.60$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 60.0 + 0.5 \times 4.0 - 0.3 \times 1.92 - 17.00 = 44.42$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 60.0 + 0.5 \times 4.0 - 0.3 \times 1.92 - 17.00 = 44.42$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +16.82$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `6.3` (15 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794388224-bd55e506-claude-agent-claude-sonnet-5-5` executed with minimal error ($p\_success\_error = 0.20$,
$cost\_error = 3.00$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
