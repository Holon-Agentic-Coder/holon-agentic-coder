# Plan Calibration Report: P-1794478317-4eb07fff-claude-agent-claude-sonnet-5-5

- **Plan Reference:**
  [`plans/P-1794478317-4eb07fff-claude-agent-claude-sonnet-5-5.md`](P-1794478317-4eb07fff-claude-agent-claude-sonnet-5-5.md)
- **Execution ID:** `E-1794478418-d600c349-claude-agent-claude-sonnet-5-5`
- **Execution Branch:**
  `I-1794478300-salvage/P-1794478317-4eb07fff-claude-agent-claude-sonnet-5-5/E-1794478418-d600c349-claude-agent-claude-sonnet-5-5/_`
- **Intent Branch:** `I-1794478300-salvage/_`
- **Evaluating Agent:** `claude-agent/claude-sonnet-5-5`
- **Evaluation Timestamp:** `2026-11-12T10:35:58.000Z`
- **Evaluated Commit SHA:** `73d8b5dd10a40b75585f9b3fc39156670d0a1ba2`
- **Base SHA:** `5a3398b538cd81369027bc6a14dc9de725af8c4b`
- **Merge-Base SHA:** `5a3398b538cd81369027bc6a14dc9de725af8c4b`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.82`             | `1.00`            | `0.18`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `6.20`             | `1.43`            | `4.77`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `62.00`            | `62.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `14.00`            | `11.90`           | `2.10`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `5.00`             | `5.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `37.48`            | `52.17`           | `14.69`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `37.48`            | `52.17`           | `14.69`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.82 - 1.00| = 0.18$
- **Entropy Error:** $|6.20 - 1.43| = 4.77$
- **Impact Error:** $|62.00 - 62.00| = 0.00$
- **Cost Error:** $|14.00 - 11.90| = 2.10$
- **Learning Value Error:** $|5.00 - 5.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.82 \times 62.0 + 0.5 \times 5.0 - 0.3 \times 6.20 - 14.00 = 37.48$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 62.0 + 0.5 \times 5.0 - 0.3 \times 1.43 - 11.90 = 52.17$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 62.0 + 0.5 \times 5.0 - 0.3 \times 1.43 - 11.90 = 52.17$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +14.69$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `4.6` (11 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794478317-4eb07fff-claude-agent-claude-sonnet-5-5` executed with minimal error ($p\_success\_error = 0.18$,
$cost\_error = 2.10$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
