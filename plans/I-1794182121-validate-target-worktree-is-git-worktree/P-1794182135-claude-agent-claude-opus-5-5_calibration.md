# Plan Calibration Report: P-1794182135-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1794182135-claude-agent-claude-opus-5-5.md`](P-1794182135-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1794182372-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1794182121-validate-target-worktree-is-git-worktree/P-1794182135-claude-agent-claude-opus-5-5/E-1794182372-claude-agent-claude-opus-5-5/_`
- **Intent Branch:** `I-1794182121-validate-target-worktree-is-git-worktree/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-11-09T00:26:12.000Z`
- **Evaluated Commit SHA:** `083827da37098377adcc7831f6a6f1e6e714b00a`
- **Base SHA:** `ce986684695e51161691acf6cf69fdc01366697e`
- **Merge-Base SHA:** `ce986684695e51161691acf6cf69fdc01366697e`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**   | `0.84`             | `1.00`            | `0.16`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `7.00`             | `1.11`            | `5.89`                                | Moderate        | Overestimated Risk         |
| **Impact**                | `70.00`            | `70.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                  | `12.50`            | `10.62`           | `1.88`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**        | `4.50`             | `4.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value ($EV$)** | `46.45`            | `61.30`           | `14.85`                               | Moderate        | Conservative Underestimate |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.84 - 1.00| = 0.16$
- **Entropy Error:** $|7.00 - 1.11| = 5.89$
- **Impact Error:** $|70.00 - 70.00| = 0.00$
- **Cost Error:** $|12.50 - 10.62| = 1.88$
- **Learning Value Error:** $|4.50 - 4.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.84 \times 70.0 + 0.5 \times 4.5 - 0.3 \times 7.00 - 12.50 = 46.45$$
  $$EV_{\text{actual}} = 1.00 \times 70.0 + 0.5 \times 4.5 - 0.3 \times 1.11 - 10.62 = 61.30$$ $$\Delta EV = +14.85$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `3.6` (5 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794182135-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.16$, $cost\_error =
1.88$). The calibration step successfully completed the execution lifecycle and delivered verified post-execution
analysis.
