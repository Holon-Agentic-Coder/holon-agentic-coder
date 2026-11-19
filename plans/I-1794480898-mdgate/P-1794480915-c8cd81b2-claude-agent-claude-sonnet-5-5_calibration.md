# Plan Calibration Report: P-1794480915-c8cd81b2-claude-agent-claude-sonnet-5-5

- **Plan Reference:**
  [`plans/P-1794480915-c8cd81b2-claude-agent-claude-sonnet-5-5.md`](P-1794480915-c8cd81b2-claude-agent-claude-sonnet-5-5.md)
- **Execution ID:** `E-1794481013-c5a9f3d0-claude-agent-claude-sonnet-5-5`
- **Execution Branch:**
  `I-1794480898-mdgate/P-1794480915-c8cd81b2-claude-agent-claude-sonnet-5-5/E-1794481013-c5a9f3d0-claude-agent-claude-sonnet-5-5/_`
- **Intent Branch:** `I-1794480898-mdgate/_`
- **Evaluating Agent:** `claude-agent/claude-sonnet-5-5`
- **Evaluation Timestamp:** `2026-11-12T11:09:55.000Z`
- **Evaluated Commit SHA:** `579c827a2edd54da2e84f5c7df3b2e91feb50859`
- **Base SHA:** `b0827d7dbc0b4fc35196b36ef3f0e98c3722a494`
- **Merge-Base SHA:** `b0827d7dbc0b4fc35196b36ef3f0e98c3722a494`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.80`             | `1.00`            | `0.20`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `8.00`             | `1.09`            | `6.91`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `60.00`            | `60.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `20.00`            | `17.00`           | `3.00`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `5.00`             | `5.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `28.10`            | `45.17`           | `17.07`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `28.10`            | `45.17`           | `17.07`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.80 - 1.00| = 0.20$
- **Entropy Error:** $|8.00 - 1.09| = 6.91$
- **Impact Error:** $|60.00 - 60.00| = 0.00$
- **Cost Error:** $|20.00 - 17.00| = 3.00$
- **Learning Value Error:** $|5.00 - 5.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.80 \times 60.0 + 0.5 \times 5.0 - 0.3 \times 8.00 - 20.00 = 28.10$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 60.0 + 0.5 \times 5.0 - 0.3 \times 1.09 - 17.00 = 45.17$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 60.0 + 0.5 \times 5.0 - 0.3 \times 1.09 - 17.00 = 45.17$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +17.07$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `3.5` (8 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794480915-c8cd81b2-claude-agent-claude-sonnet-5-5` executed with minimal error ($p\_success\_error = 0.20$,
$cost\_error = 3.00$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
