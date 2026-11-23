# Plan Calibration Report: P-1794124844-claude-agent-claude-fable-5

- **Plan Reference:** [`plans/P-1794124844-claude-agent-claude-fable-5.md`](P-1794124844-claude-agent-claude-fable-5.md)
- **Execution ID:** `E-1794125099-claude-agent-claude-fable-5`
- **Execution Branch:**
  `I-1794124652-post-consensus-review-once-per-pr-exit/P-1794124844-claude-agent-claude-fable-5/E-1794125099-claude-agent-claude-fable-5/_`
- **Intent Branch:** `I-1794124652-post-consensus-review-once-per-pr-exit/_`
- **Evaluating Agent:** `claude-agent/claude-fable-5`
- **Evaluation Timestamp:** `2026-11-08T08:21:09.000Z`
- **Evaluated Commit SHA:** `bfbbe0c44cb8085f9bf7f5163d1a9bc7d1eab720`
- **Base SHA:** `745504d7e5d54ee8c9a531e6261ee8ba37c5c81c`
- **Merge-Base SHA:** `745504d7e5d54ee8c9a531e6261ee8ba37c5c81c`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**   | `0.90`             | `1.00`            | `0.10`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `5.80`             | `0.56`            | `5.24`                                | Moderate        | Overestimated Risk         |
| **Impact**                | `70.00`            | `70.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                  | `12.00`            | `10.20`           | `1.80`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**        | `3.00`             | `3.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value ($EV$)** | `50.76`            | `61.13`           | `10.37`                               | Moderate        | Conservative Underestimate |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.90 - 1.00| = 0.10$
- **Entropy Error:** $|5.80 - 0.56| = 5.24$
- **Impact Error:** $|70.00 - 70.00| = 0.00$
- **Cost Error:** $|12.00 - 10.20| = 1.80$
- **Learning Value Error:** $|3.00 - 3.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.90 \times 70.0 + 0.5 \times 3.0 - 0.3 \times 5.80 - 12.00 = 50.76$$
  $$EV_{\text{actual}} = 1.00 \times 70.0 + 0.5 \times 3.0 - 0.3 \times 0.56 - 10.20 = 61.13$$ $$\Delta EV = +10.37$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `1.7` (3 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794124844-claude-agent-claude-fable-5` executed with minimal error ($p\_success\_error = 0.10$, $cost\_error =
1.80$). The calibration step successfully completed the execution lifecycle and delivered verified post-execution
analysis.
