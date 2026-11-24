# Plan Calibration Report: P-1792815994-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1792815994-claude-agent-claude-opus-5-5.md`](P-1792815994-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1792816376-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1792815987-redact-github-token-in-cli-echoes-v2/P-1792815994-claude-agent-claude-opus-5-5/E-1792816376-claude-agent-claude-opus-5-5/_`
- **Intent Branch:** `I-1792815987-redact-github-token-in-cli-echoes-v2/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-10-24T05:39:13.000Z`
- **Evaluated Commit SHA:** `f1117d31d970fbb0ae465dca390b2cfe31e16d69`
- **Base SHA:** `c83d46a719a1ca8478a20b91efd0a6617d5302c3`
- **Merge-Base SHA:** `c83d46a719a1ca8478a20b91efd0a6617d5302c3`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                    | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------ | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**   | `0.80`             | `1.00`            | `0.20`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**  | `0.80`             | `3.04`            | `2.24`                                | Moderate        | Underestimated Risk        |
| **Impact**                | `7.00`             | `7.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                  | `5.00`             | `4.25`            | `0.75`                                | High (≤ 1.0)    | Highly Accurate            |
| **Learning Value**        | `4.00`             | `4.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value ($EV$)** | `2.36`             | `3.84`            | `1.48`                                | High            | Conservative Underestimate |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.80 - 1.00| = 0.20$
- **Entropy Error:** $|0.80 - 3.04| = 2.24$
- **Impact Error:** $|7.00 - 7.00| = 0.00$
- **Cost Error:** $|5.00 - 4.25| = 0.75$
- **Learning Value Error:** $|4.00 - 4.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.80 \times 7.0 + 0.5 \times 4.0 - 0.3 \times 0.80 - 5.00 = 2.36$$
  $$EV_{\text{actual}} = 1.00 \times 7.0 + 0.5 \times 4.0 - 0.3 \times 3.04 - 4.25 = 3.84$$ $$\Delta EV = +1.48$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `10.0` (24 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1792815994-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.20$, $cost\_error =
0.75$). The calibration step successfully completed the execution lifecycle and delivered verified post-execution
analysis.

---

## 5. Review-Loop Annotation: Known-Wrong Predicted Metrics (Bean 0146)

> Added during the Stage 4 review loop, after `holon calibrate` produced sections 1–4 above (unchanged on the
> `/calibrated` branch).

The `plans.jsonl` row for `P-1792815994-claude-agent-claude-opus-5-5` was written by a host `holon` install that
predates the `planner.parse_metrics()` fix from PR #91. It therefore records a per-step metrics table instead of the
plan's `## Overall Plan Metrics`. Calibration reads that row, so every **Predicted** value and every delta in sections
1–2 is computed against the wrong baseline. The plan file's Overall values are:

| Metric         | Overall (plan file) | Recorded row (used above) |
| :------------- | :------------------ | :------------------------ |
| p_success_pred | 0.68                | 0.80                      |
| entropy_pred   | 10.9                | 0.80                      |
| impact_pred    | 100.0               | 7.0                       |
| cost_pred      | 51.0                | 5.0                       |
| learning_value | 4.0                 | 4.0                       |
| ev_pred        | 15.73               | 2.36                      |

The ledger row is append-only and is not rewritten. A plans-ledger correction row, and calibration that prefers it, are
tracked by Bean 0146; recompute this report's deltas once that lands.
