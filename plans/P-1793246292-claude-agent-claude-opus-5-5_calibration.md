# Plan Calibration Report: P-1793246292-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1793246292-claude-agent-claude-opus-5-5.md`](P-1793246292-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1793246422-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1793246276-prompts-repeat-test-runs-only-for-performance-under-xdist/P-1793246292-claude-agent-claude-opus-5-5/E-1793246422-claude-agent-claude-opus-5-5/_`
- **Intent Branch:** `I-1793246276-prompts-repeat-test-runs-only-for-performance-under-xdist/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-10-29T04:15:03.000Z`
- **Evaluated Commit SHA:** `98d3bc5ef34df2243e6a64054e3fc14d239d52a7`
- **Base SHA:** `069c90b880616948adcb0752144de2f4f9db3d33`
- **Merge-Base SHA:** `069c90b880616948adcb0752144de2f4f9db3d33`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**   | `0.88`             | `1.00`            | `0.12`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `1.70`             | `0.44`            | `1.26`                                | Moderate        | Overestimated Risk         |
| **Impact**                | `70.00`            | `70.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                  | `8.00`             | `6.80`            | `1.20`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**        | `3.00`             | `3.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value ($EV$)** | `54.59`            | `64.57`           | `9.98`                                | Moderate        | Conservative Underestimate |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.88 - 1.00| = 0.12$
- **Entropy Error:** $|1.70 - 0.44| = 1.26$
- **Impact Error:** $|70.00 - 70.00| = 0.00$
- **Cost Error:** $|8.00 - 6.80| = 1.20$
- **Learning Value Error:** $|3.00 - 3.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.88 \times 70.0 + 0.5 \times 3.0 - 0.3 \times 1.70 - 8.00 = 54.59$$
  $$EV_{\text{actual}} = 1.00 \times 70.0 + 0.5 \times 3.0 - 0.3 \times 0.44 - 6.80 = 64.57$$ $$\Delta EV = +9.98$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `1.3` (4 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1793246292-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.12$,
$cost\_error = 1.20$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
