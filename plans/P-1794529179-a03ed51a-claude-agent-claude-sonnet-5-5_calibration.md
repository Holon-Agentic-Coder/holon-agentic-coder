# Plan Calibration Report: P-1794529179-a03ed51a-claude-agent-claude-sonnet-5-5

- **Plan Reference:**
  [`plans/P-1794529179-a03ed51a-claude-agent-claude-sonnet-5-5.md`](P-1794529179-a03ed51a-claude-agent-claude-sonnet-5-5.md)
- **Execution ID:** `E-1794529261-7bedbfa9-claude-agent-claude-sonnet-5-5`
- **Execution Branch:**
  `I-1794529163-attrib/P-1794529179-a03ed51a-claude-agent-claude-sonnet-5-5/E-1794529261-7bedbfa9-claude-agent-claude-sonnet-5-5/_`
- **Intent Branch:** `I-1794529163-attrib/_`
- **Evaluating Agent:** `claude-agent/claude-sonnet-5-5`
- **Evaluation Timestamp:** `2026-11-13T00:46:22.000Z`
- **Evaluated Commit SHA:** `ea989ab146ff8bebc925d119ba3882d9c67c7000`
- **Base SHA:** `31cdc6a8f1c8d472918b4a5f6419afc4f6ba1320`
- **Merge-Base SHA:** `31cdc6a8f1c8d472918b4a5f6419afc4f6ba1320`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.85`             | `1.00`            | `0.15`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `4.00`             | `1.09`            | `2.91`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `55.00`            | `55.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `12.00`            | `10.20`           | `1.80`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.00`             | `4.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `35.55`            | `46.47`           | `10.92`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `35.55`            | `46.47`           | `10.92`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.85 - 1.00| = 0.15$
- **Entropy Error:** $|4.00 - 1.09| = 2.91$
- **Impact Error:** $|55.00 - 55.00| = 0.00$
- **Cost Error:** $|12.00 - 10.20| = 1.80$
- **Learning Value Error:** $|4.00 - 4.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.85 \times 55.0 + 0.5 \times 4.0 - 0.3 \times 4.00 - 12.00 = 35.55$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 55.0 + 0.5 \times 4.0 - 0.3 \times 1.09 - 10.20 = 46.47$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 55.0 + 0.5 \times 4.0 - 0.3 \times 1.09 - 10.20 = 46.47$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +10.92$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `3.5` (8 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794529179-a03ed51a-claude-agent-claude-sonnet-5-5` executed with minimal error ($p\_success\_error = 0.15$,
$cost\_error = 1.80$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
