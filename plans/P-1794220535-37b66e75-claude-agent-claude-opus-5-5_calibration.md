# Plan Calibration Report: P-1794220535-37b66e75-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1794220535-37b66e75-claude-agent-claude-opus-5-5.md`](P-1794220535-37b66e75-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1794220711-a8d9ac38-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1794220515-run-ci-on-pull-requests-into-any-branch/P-1794220535-37b66e75-claude-agent-claude-opus-5-5/E-1794220711-a8d9ac38-claude-agent-claude-opus-5-5/_`
- **Intent Branch:** `I-1794220515-run-ci-on-pull-requests-into-any-branch/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-11-09T11:03:18.000Z`
- **Evaluated Commit SHA:** `85b5b3e43378bffe298fd3f3bb0c117e838be671`
- **Base SHA:** `3aafec65445b82e255e25b9fdcf8066c974e0568`
- **Merge-Base SHA:** `3aafec65445b82e255e25b9fdcf8066c974e0568`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.80`             | `1.00`            | `0.20`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `5.80`             | `1.31`            | `4.49`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `85.00`            | `85.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `15.00`            | `12.75`           | `2.25`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `5.00`             | `5.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `53.76`            | `74.36`           | `20.60`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `53.76`            | `74.36`           | `20.60`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.80 - 1.00| = 0.20$
- **Entropy Error:** $|5.80 - 1.31| = 4.49$
- **Impact Error:** $|85.00 - 85.00| = 0.00$
- **Cost Error:** $|15.00 - 12.75| = 2.25$
- **Learning Value Error:** $|5.00 - 5.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.80 \times 85.0 + 0.5 \times 5.0 - 0.3 \times 5.80 - 15.00 = 53.76$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 85.0 + 0.5 \times 5.0 - 0.3 \times 1.31 - 12.75 = 74.36$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 85.0 + 0.5 \times 5.0 - 0.3 \times 1.31 - 12.75 = 74.36$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +20.60$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `4.2` (10 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794220535-37b66e75-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.20$,
$cost\_error = 2.25$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
