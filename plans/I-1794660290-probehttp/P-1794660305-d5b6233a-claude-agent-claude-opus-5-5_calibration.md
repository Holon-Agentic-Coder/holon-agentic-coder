# Plan Calibration Report: P-1794660305-d5b6233a-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1794660305-d5b6233a-claude-agent-claude-opus-5-5.md`](P-1794660305-d5b6233a-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1794660422-9a1950d6-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1794660290-probehttp/P-1794660305-d5b6233a-claude-agent-claude-opus-5-5/E-1794660422-9a1950d6-claude-agent-claude-opus-5-5/_`
- **Intent Branch:** `I-1794660290-probehttp/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-11-14T13:02:59.000Z`
- **Evaluated Commit SHA:** `73e0c739204d7eb88d186c89784bfce6d0a4a3bb`
- **Base SHA:** `5fc2f90eb2212a8c7363f8b081370994d777df57`
- **Merge-Base SHA:** `5fc2f90eb2212a8c7363f8b081370994d777df57`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.88`             | `1.00`            | `0.12`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `4.00`             | `0.53`            | `3.47`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `70.00`            | `70.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `10.00`            | `8.50`            | `1.50`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `3.00`             | `3.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `51.90`            | `62.84`           | `10.94`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `51.90`            | `62.84`           | `10.94`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.88 - 1.00| = 0.12$
- **Entropy Error:** $|4.00 - 0.53| = 3.47$
- **Impact Error:** $|70.00 - 70.00| = 0.00$
- **Cost Error:** $|10.00 - 8.50| = 1.50$
- **Learning Value Error:** $|3.00 - 3.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.88 \times 70.0 + 0.5 \times 3.0 - 0.3 \times 4.00 - 10.00 = 51.90$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 70.0 + 0.5 \times 3.0 - 0.3 \times 0.53 - 8.50 = 62.84$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 70.0 + 0.5 \times 3.0 - 0.3 \times 0.53 - 8.50 = 62.84$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +10.94$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `1.6` (4 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794660305-d5b6233a-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.12$,
$cost\_error = 1.50$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
