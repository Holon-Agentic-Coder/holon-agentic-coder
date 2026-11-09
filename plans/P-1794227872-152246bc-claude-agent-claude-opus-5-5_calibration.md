# Plan Calibration Report: P-1794227872-152246bc-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1794227872-152246bc-claude-agent-claude-opus-5-5.md`](P-1794227872-152246bc-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1794228146-88e62c5b-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1794224634-plan-owned-decomposition-with-plan-branch-children--2-nest-decomposed-children-under-the-plan-branch-m/P-1794227872-152246bc-claude-agent-claude-opus-5-5/E-1794228146-88e62c5b-claude-agent-claude-opus-5-5/_`
- **Intent Branch:**
  `I-1794224634-plan-owned-decomposition-with-plan-branch-children--2-nest-decomposed-children-under-the-plan-branch-m/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-11-09T14:19:00.000Z`
- **Evaluated Commit SHA:** `814f86256ee6cced6600c72ec2e793680a5a6e7c`
- **Base SHA:** `4264e5fcf0b1755f299b12a758e3e3a01e93cd75`
- **Merge-Base SHA:** `4264e5fcf0b1755f299b12a758e3e3a01e93cd75`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.65`             | `1.00`            | `0.35`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `12.80`            | `3.04`            | `9.76`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `88.00`            | `88.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `30.00`            | `25.50`           | `4.50`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `6.00`             | `6.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `26.36`            | `64.59`           | `38.23`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `26.36`            | `64.59`           | `38.23`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.65 - 1.00| = 0.35$
- **Entropy Error:** $|12.80 - 3.04| = 9.76$
- **Impact Error:** $|88.00 - 88.00| = 0.00$
- **Cost Error:** $|30.00 - 25.50| = 4.50$
- **Learning Value Error:** $|6.00 - 6.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.65 \times 88.0 + 0.5 \times 6.0 - 0.3 \times 12.80 - 30.00 = 26.36$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 88.0 + 0.5 \times 6.0 - 0.3 \times 3.04 - 25.50 = 64.59$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 88.0 + 0.5 \times 6.0 - 0.3 \times 3.04 - 25.50 = 64.59$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +38.23$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `10.0` (34 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794227872-152246bc-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.35$,
$cost\_error = 4.50$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
