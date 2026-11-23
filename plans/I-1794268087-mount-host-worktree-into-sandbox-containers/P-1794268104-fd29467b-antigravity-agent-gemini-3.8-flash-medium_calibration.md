# Plan Calibration Report: P-1794268104-fd29467b-antigravity-agent-gemini-3.8-flash-medium

- **Plan Reference:**
  [`plans/P-1794268104-fd29467b-antigravity-agent-gemini-3.8-flash-medium.md`](P-1794268104-fd29467b-antigravity-agent-gemini-3.8-flash-medium.md)
- **Execution ID:** `E-1794270142-ea5a02bf-antigravity-agent-gemini-3.8-flash-high`
- **Execution Branch:**
  `I-1794268087-mount-host-worktree-into-sandbox-containers/P-1794268104-fd29467b-antigravity-agent-gemini-3.8-flash-medium/E-1794270142-ea5a02bf-antigravity-agent-gemini-3.8-flash-high/_`
- **Intent Branch:** `I-1794268087-mount-host-worktree-into-sandbox-containers/_`
- **Evaluating Agent:** `antigravity-agent/gemini-3.8-flash-high`
- **Evaluation Timestamp:** `2026-11-10T05:47:15.000Z`
- **Evaluated Commit SHA:** `afb4c4064aa2455be22ebb1dc27edf1973a17e83`
- **Base SHA:** `edb877e1bbfc0a6bdb98b54b49c724c3567fe353`
- **Merge-Base SHA:** `edb877e1bbfc0a6bdb98b54b49c724c3567fe353`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.94`             | `1.00`            | `0.06`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `3.80`             | `1.68`            | `2.12`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `92.00`            | `92.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `7.50`             | `6.38`            | `1.12`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `5.00`             | `5.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `80.34`            | `87.62`           | `7.28`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `80.34`            | `87.62`           | `7.28`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.94 - 1.00| = 0.06$
- **Entropy Error:** $|3.80 - 1.68| = 2.12$
- **Impact Error:** $|92.00 - 92.00| = 0.00$
- **Cost Error:** $|7.50 - 6.38| = 1.12$
- **Learning Value Error:** $|5.00 - 5.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.94 \times 92.0 + 0.5 \times 5.0 - 0.3 \times 3.80 - 7.50 = 80.34$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 92.0 + 0.5 \times 5.0 - 0.3 \times 1.68 - 6.38 = 87.62$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 92.0 + 0.5 \times 5.0 - 0.3 \times 1.68 - 6.38 = 87.62$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +7.28$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `5.5` (11 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794268104-fd29467b-antigravity-agent-gemini-3.8-flash-medium` executed with minimal error ($p\_success\_error =
0.06$, $cost\_error = 1.12$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
