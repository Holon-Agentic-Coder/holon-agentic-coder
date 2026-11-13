# Plan Calibration Report: P-1794575045-ca973365-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1794575045-ca973365-claude-agent-claude-opus-5-5.md`](P-1794575045-ca973365-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1794575233-98e59399-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1794575029-nopost/P-1794575045-ca973365-claude-agent-claude-opus-5-5/E-1794575233-98e59399-claude-agent-claude-opus-5-5/_`
- **Intent Branch:** `I-1794575029-nopost/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-11-13T13:24:03.000Z`
- **Evaluated Commit SHA:** `30272b13d60443c29d95578c24b52b2d8a6dd0b8`
- **Base SHA:** `2651aa3afda08e18c986ee086f758339ad803d89`
- **Merge-Base SHA:** `2651aa3afda08e18c986ee086f758339ad803d89`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.87`             | `1.00`            | `0.13`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `4.10`             | `0.71`            | `3.39`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `68.00`            | `68.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `9.00`             | `7.65`            | `1.35`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `3.50`             | `3.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `50.68`            | `61.89`           | `11.21`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `50.68`            | `61.89`           | `11.21`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.87 - 1.00| = 0.13$
- **Entropy Error:** $|4.10 - 0.71| = 3.39$
- **Impact Error:** $|68.00 - 68.00| = 0.00$
- **Cost Error:** $|9.00 - 7.65| = 1.35$
- **Learning Value Error:** $|3.50 - 3.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.87 \times 68.0 + 0.5 \times 3.5 - 0.3 \times 4.10 - 9.00 = 50.68$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 68.0 + 0.5 \times 3.5 - 0.3 \times 0.71 - 7.65 = 61.89$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 68.0 + 0.5 \times 3.5 - 0.3 \times 0.71 - 7.65 = 61.89$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +11.21$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `2.2` (8 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794575045-ca973365-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.13$,
$cost\_error = 1.35$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
