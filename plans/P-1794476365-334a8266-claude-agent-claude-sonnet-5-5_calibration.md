# Plan Calibration Report: P-1794476365-334a8266-claude-agent-claude-sonnet-5-5

- **Plan Reference:**
  [`plans/P-1794476365-334a8266-claude-agent-claude-sonnet-5-5.md`](P-1794476365-334a8266-claude-agent-claude-sonnet-5-5.md)
- **Execution ID:** `E-1794476446-ef30eb97-claude-agent-claude-sonnet-5-5`
- **Execution Branch:**
  `I-1794476347-nopost/P-1794476365-334a8266-claude-agent-claude-sonnet-5-5/E-1794476446-ef30eb97-claude-agent-claude-sonnet-5-5/_`
- **Intent Branch:** `I-1794476347-nopost/_`
- **Evaluating Agent:** `claude-agent/claude-sonnet-5-5`
- **Evaluation Timestamp:** `2026-11-12T09:55:53.000Z`
- **Evaluated Commit SHA:** `8212d951c13f8f58e5597b0368aeb7892c4631a9`
- **Base SHA:** `5499b94eac30a71294e83d80dd6d691f08bee69a`
- **Merge-Base SHA:** `5499b94eac30a71294e83d80dd6d691f08bee69a`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.86`             | `1.00`            | `0.14`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `5.40`             | `1.60`            | `3.80`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `62.00`            | `62.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `13.00`            | `11.05`           | `1.95`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.00`             | `4.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `40.70`            | `52.47`           | `11.77`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `40.70`            | `52.47`           | `11.77`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.86 - 1.00| = 0.14$
- **Entropy Error:** $|5.40 - 1.60| = 3.80$
- **Impact Error:** $|62.00 - 62.00| = 0.00$
- **Cost Error:** $|13.00 - 11.05| = 1.95$
- **Learning Value Error:** $|4.00 - 4.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.86 \times 62.0 + 0.5 \times 4.0 - 0.3 \times 5.40 - 13.00 = 40.70$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 62.0 + 0.5 \times 4.0 - 0.3 \times 1.60 - 11.05 = 52.47$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 62.0 + 0.5 \times 4.0 - 0.3 \times 1.60 - 11.05 = 52.47$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +11.77$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `5.2` (14 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794476365-334a8266-claude-agent-claude-sonnet-5-5` executed with minimal error ($p\_success\_error = 0.14$,
$cost\_error = 1.95$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
