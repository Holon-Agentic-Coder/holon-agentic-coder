# Plan Calibration Report: P-1793246201-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1793246201-claude-agent-claude-opus-5-5.md`](P-1793246201-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1793246527-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1793246184-execute-failure-keeps-partial-code-and-resumes-from-it/P-1793246201-claude-agent-claude-opus-5-5/E-1793246527-claude-agent-claude-opus-5-5/_`
- **Intent Branch:** `I-1793246184-execute-failure-keeps-partial-code-and-resumes-from-it/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-11-14T12:04:50.000Z`
- **Evaluated Commit SHA:** `55a11e05e3e48b3f5d5b8aed0b59493fa49f066b`
- **Base SHA:** `a61c3d49f8dc350e1b0d6877dffa70ae08289db2`
- **Merge-Base SHA:** `a61c3d49f8dc350e1b0d6877dffa70ae08289db2`
- **Merged Commits:** `1`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.68`             | `1.00`            | `0.32`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `10.00`            | `3.04`            | `6.96`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `90.00`            | `90.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `27.50`            | `23.38`           | `4.12`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `5.00`             | `5.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `33.20`            | `68.21`           | `35.01`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `33.20`            | `68.21`           | `35.01`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.68 - 1.00| = 0.32$
- **Entropy Error:** $|10.00 - 3.04| = 6.96$
- **Impact Error:** $|90.00 - 90.00| = 0.00$
- **Cost Error:** $|27.50 - 23.38| = 4.12$
- **Learning Value Error:** $|5.00 - 5.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.68 \times 90.0 + 0.5 \times 5.0 - 0.3 \times 10.00 - 27.50 = 33.20$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 90.0 + 0.5 \times 5.0 - 0.3 \times 3.04 - 23.38 = 68.21$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 90.0 + 0.5 \times 5.0 - 0.3 \times 3.04 - 23.38 = 68.21$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +35.01$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `10.0` (12 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1793246201-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.32$, $cost\_error =
4.12$). The calibration step successfully completed the execution lifecycle and delivered verified post-execution
analysis.
