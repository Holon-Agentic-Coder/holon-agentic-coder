# Plan Calibration Report: P-1794529184-344adb09-claude-agent-claude-sonnet-5-5

- **Plan Reference:**
  [`plans/P-1794529184-344adb09-claude-agent-claude-sonnet-5-5.md`](P-1794529184-344adb09-claude-agent-claude-sonnet-5-5.md)
- **Execution ID:** `E-1794529251-fe8df888-claude-agent-claude-sonnet-5-5`
- **Execution Branch:**
  `I-1794529168-pidir/P-1794529184-344adb09-claude-agent-claude-sonnet-5-5/E-1794529251-fe8df888-claude-agent-claude-sonnet-5-5/_`
- **Intent Branch:** `I-1794529168-pidir/_`
- **Evaluating Agent:** `claude-agent/claude-sonnet-5-5`
- **Evaluation Timestamp:** `2026-11-13T00:32:39.000Z`
- **Evaluated Commit SHA:** `f3f1a4d80c3ac7f318a05294424c3d736ae0d151`
- **Base SHA:** `6dbf0c947e221c512c8641afa09f36c6868d9e82`
- **Merge-Base SHA:** `6dbf0c947e221c512c8641afa09f36c6868d9e82`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.90`             | `1.00`            | `0.10`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `3.00`             | `0.58`            | `2.42`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `60.00`            | `60.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `8.00`             | `6.80`            | `1.20`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `3.00`             | `3.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `46.60`            | `54.53`           | `7.93`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `46.60`            | `54.53`           | `7.93`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.90 - 1.00| = 0.10$
- **Entropy Error:** $|3.00 - 0.58| = 2.42$
- **Impact Error:** $|60.00 - 60.00| = 0.00$
- **Cost Error:** $|8.00 - 6.80| = 1.20$
- **Learning Value Error:** $|3.00 - 3.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.90 \times 60.0 + 0.5 \times 3.0 - 0.3 \times 3.00 - 8.00 = 46.60$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 60.0 + 0.5 \times 3.0 - 0.3 \times 0.58 - 6.80 = 54.53$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 60.0 + 0.5 \times 3.0 - 0.3 \times 0.58 - 6.80 = 54.53$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +7.93$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `1.8` (4 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794529184-344adb09-claude-agent-claude-sonnet-5-5` executed with minimal error ($p\_success\_error = 0.10$,
$cost\_error = 1.20$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
