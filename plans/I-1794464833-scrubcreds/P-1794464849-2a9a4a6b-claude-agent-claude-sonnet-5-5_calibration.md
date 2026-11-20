# Plan Calibration Report: P-1794464849-2a9a4a6b-claude-agent-claude-sonnet-5-5

- **Plan Reference:**
  [`plans/P-1794464849-2a9a4a6b-claude-agent-claude-sonnet-5-5.md`](P-1794464849-2a9a4a6b-claude-agent-claude-sonnet-5-5.md)
- **Execution ID:** `E-1794464926-c106ee4e-claude-agent-claude-sonnet-5-5`
- **Execution Branch:**
  `I-1794464833-scrubcreds/P-1794464849-2a9a4a6b-claude-agent-claude-sonnet-5-5/E-1794464926-c106ee4e-claude-agent-claude-sonnet-5-5/_`
- **Intent Branch:** `I-1794464833-scrubcreds/_`
- **Evaluating Agent:** `claude-agent/claude-sonnet-5-5`
- **Evaluation Timestamp:** `2026-11-12T07:13:08.000Z`
- **Evaluated Commit SHA:** `7fb676b38e1df72e15f5f5b74d9e97fc9e54c7c7`
- **Base SHA:** `aa60be96b8f35474e33188656f7659784be12c02`
- **Merge-Base SHA:** `aa60be96b8f35474e33188656f7659784be12c02`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.90`             | `1.00`            | `0.10`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `3.50`             | `0.48`            | `3.02`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `60.00`            | `60.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `8.00`             | `6.80`            | `1.20`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `6.00`             | `6.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `47.95`            | `56.06`           | `8.11`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `47.95`            | `56.06`           | `8.11`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.90 - 1.00| = 0.10$
- **Entropy Error:** $|3.50 - 0.48| = 3.02$
- **Impact Error:** $|60.00 - 60.00| = 0.00$
- **Cost Error:** $|8.00 - 6.80| = 1.20$
- **Learning Value Error:** $|6.00 - 6.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.90 \times 60.0 + 0.5 \times 6.0 - 0.3 \times 3.50 - 8.00 = 47.95$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 60.0 + 0.5 \times 6.0 - 0.3 \times 0.48 - 6.80 = 56.06$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 60.0 + 0.5 \times 6.0 - 0.3 \times 0.48 - 6.80 = 56.06$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +8.11$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `1.5` (5 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794464849-2a9a4a6b-claude-agent-claude-sonnet-5-5` executed with minimal error ($p\_success\_error = 0.10$,
$cost\_error = 1.20$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
