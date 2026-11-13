# Plan Calibration Report: P-1794546217-ec4d1e6a-claude-agent-claude-sonnet-5-5

- **Plan Reference:**
  [`plans/P-1794546217-ec4d1e6a-claude-agent-claude-sonnet-5-5.md`](P-1794546217-ec4d1e6a-claude-agent-claude-sonnet-5-5.md)
- **Execution ID:** `E-1794546298-5e4151be-claude-agent-claude-sonnet-5-5`
- **Execution Branch:**
  `I-1794546201-keepdiff/P-1794546217-ec4d1e6a-claude-agent-claude-sonnet-5-5/E-1794546298-5e4151be-claude-agent-claude-sonnet-5-5/_`
- **Intent Branch:** `I-1794546201-keepdiff/_`
- **Evaluating Agent:** `claude-agent/claude-sonnet-5-5`
- **Evaluation Timestamp:** `2026-11-13T05:22:33.000Z`
- **Evaluated Commit SHA:** `a255e2d5d6cad47557373d1786360db033891ecf`
- **Base SHA:** `99a2c39a5369e022e7605759a45471c89e357765`
- **Merge-Base SHA:** `99a2c39a5369e022e7605759a45471c89e357765`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.80`             | `1.00`            | `0.20`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `6.00`             | `1.72`            | `4.28`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `70.00`            | `70.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `14.00`            | `11.90`           | `2.10`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `5.00`             | `5.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `42.70`            | `60.08`           | `17.38`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `42.70`            | `60.08`           | `17.38`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.80 - 1.00| = 0.20$
- **Entropy Error:** $|6.00 - 1.72| = 4.28$
- **Impact Error:** $|70.00 - 70.00| = 0.00$
- **Cost Error:** $|14.00 - 11.90| = 2.10$
- **Learning Value Error:** $|5.00 - 5.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.80 \times 70.0 + 0.5 \times 5.0 - 0.3 \times 6.00 - 14.00 = 42.70$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 70.0 + 0.5 \times 5.0 - 0.3 \times 1.72 - 11.90 = 60.08$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 70.0 + 0.5 \times 5.0 - 0.3 \times 1.72 - 11.90 = 60.08$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +17.38$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `5.6` (14 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794546217-ec4d1e6a-claude-agent-claude-sonnet-5-5` executed with minimal error ($p\_success\_error = 0.20$,
$cost\_error = 2.10$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
