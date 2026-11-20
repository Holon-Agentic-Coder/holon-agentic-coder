# Plan Calibration Report: P-1794727651-2a1e918e-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1794727651-2a1e918e-claude-agent-claude-opus-5-5.md`](P-1794727651-2a1e918e-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1794727887-d80950e5-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1794727635-guardbase/P-1794727651-2a1e918e-claude-agent-claude-opus-5-5/E-1794727887-d80950e5-claude-agent-claude-opus-5-5/_`
- **Intent Branch:** `I-1794727635-guardbase/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-11-15T07:51:46.000Z`
- **Evaluated Commit SHA:** `1da578e1954df20e3dc7d80bfc7c207e26173767`
- **Base SHA:** `8e421cfdeac78dcb4285f544da8e2fa531c27768`
- **Merge-Base SHA:** `8e421cfdeac78dcb4285f544da8e2fa531c27768`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.82`             | `1.00`            | `0.18`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `6.30`             | `1.01`            | `5.29`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `80.00`            | `80.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `12.00`            | `10.20`           | `1.80`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.00`             | `4.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `53.71`            | `71.50`           | `17.79`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `53.71`            | `71.50`           | `17.79`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.82 - 1.00| = 0.18$
- **Entropy Error:** $|6.30 - 1.01| = 5.29$
- **Impact Error:** $|80.00 - 80.00| = 0.00$
- **Cost Error:** $|12.00 - 10.20| = 1.80$
- **Learning Value Error:** $|4.00 - 4.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.82 \times 80.0 + 0.5 \times 4.0 - 0.3 \times 6.30 - 12.00 = 53.71$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 80.0 + 0.5 \times 4.0 - 0.3 \times 1.01 - 10.20 = 71.50$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 80.0 + 0.5 \times 4.0 - 0.3 \times 1.01 - 10.20 = 71.50$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +17.79$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `3.2` (7 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794727651-2a1e918e-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.18$,
$cost\_error = 1.80$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
