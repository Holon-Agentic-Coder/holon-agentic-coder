# Plan Calibration Report: P-1794574974-21db7508-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1794574974-21db7508-claude-agent-claude-opus-5-5.md`](P-1794574974-21db7508-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1794575181-a4b61ba6-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1794574957-calrebld/P-1794574974-21db7508-claude-agent-claude-opus-5-5/E-1794575181-a4b61ba6-claude-agent-claude-opus-5-5/_`
- **Intent Branch:** `I-1794574957-calrebld/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-11-13T13:29:54.000Z`
- **Evaluated Commit SHA:** `cb5514016a475841028ea2114e1c384368149e31`
- **Base SHA:** `6344c884ea42b47b1bccdea7762120bb3dcb627c`
- **Merge-Base SHA:** `6344c884ea42b47b1bccdea7762120bb3dcb627c`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.82`             | `1.00`            | `0.18`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `6.10`             | `1.07`            | `5.03`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `72.00`            | `72.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `14.00`            | `11.90`           | `2.10`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.50`             | `4.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `45.46`            | `62.03`           | `16.57`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `45.46`            | `62.03`           | `16.57`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.82 - 1.00| = 0.18$
- **Entropy Error:** $|6.10 - 1.07| = 5.03$
- **Impact Error:** $|72.00 - 72.00| = 0.00$
- **Cost Error:** $|14.00 - 11.90| = 2.10$
- **Learning Value Error:** $|4.50 - 4.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.82 \times 72.0 + 0.5 \times 4.5 - 0.3 \times 6.10 - 14.00 = 45.46$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 72.0 + 0.5 \times 4.5 - 0.3 \times 1.07 - 11.90 = 62.03$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 72.0 + 0.5 \times 4.5 - 0.3 \times 1.07 - 11.90 = 62.03$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +16.57$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `3.5` (8 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794574974-21db7508-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.18$,
$cost\_error = 2.10$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
