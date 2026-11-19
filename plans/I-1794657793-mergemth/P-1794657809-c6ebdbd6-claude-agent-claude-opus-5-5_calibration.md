# Plan Calibration Report: P-1794657809-c6ebdbd6-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1794657809-c6ebdbd6-claude-agent-claude-opus-5-5.md`](P-1794657809-c6ebdbd6-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1794657985-030a8f07-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1794657793-mergemth/P-1794657809-c6ebdbd6-claude-agent-claude-opus-5-5/E-1794657985-030a8f07-claude-agent-claude-opus-5-5/_`
- **Intent Branch:** `I-1794657793-mergemth/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-11-14T12:17:43.000Z`
- **Evaluated Commit SHA:** `61fb7e161284165eec35165195c0ae5aa0ff2eba`
- **Base SHA:** `8e04d1035213b5c80df6c80216d3f15f2718cce0`
- **Merge-Base SHA:** `8e04d1035213b5c80df6c80216d3f15f2718cce0`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.88`             | `1.00`            | `0.12`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `4.50`             | `1.54`            | `2.96`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `80.00`            | `80.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `14.50`            | `12.32`           | `2.18`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.00`             | `4.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `56.55`            | `69.22`           | `12.67`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `56.55`            | `69.22`           | `12.67`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.88 - 1.00| = 0.12$
- **Entropy Error:** $|4.50 - 1.54| = 2.96$
- **Impact Error:** $|80.00 - 80.00| = 0.00$
- **Cost Error:** $|14.50 - 12.32| = 2.18$
- **Learning Value Error:** $|4.00 - 4.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.88 \times 80.0 + 0.5 \times 4.0 - 0.3 \times 4.50 - 14.50 = 56.55$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 80.0 + 0.5 \times 4.0 - 0.3 \times 1.54 - 12.32 = 69.22$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 80.0 + 0.5 \times 4.0 - 0.3 \times 1.54 - 12.32 = 69.22$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +12.67$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `5.0` (14 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794657809-c6ebdbd6-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.12$,
$cost\_error = 2.18$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
