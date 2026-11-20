# Plan Calibration Report: P-1794294214-0a2285cf-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1794294214-0a2285cf-claude-agent-claude-opus-5-5.md`](P-1794294214-0a2285cf-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1794294472-5622c01d-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1794294198-review-consensus-never-approves-past-an-open-critical/P-1794294214-0a2285cf-claude-agent-claude-opus-5-5/E-1794294472-5622c01d-claude-agent-claude-opus-5-5/_`
- **Intent Branch:** `I-1794294198-review-consensus-never-approves-past-an-open-critical/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-11-10T08:30:14.000Z`
- **Evaluated Commit SHA:** `733b6aacf08a2abe352aaea617769aad2772b0f2`
- **Base SHA:** `53ebffb6949d74877ec4919c59d9b3bd1ad767b5`
- **Merge-Base SHA:** `53ebffb6949d74877ec4919c59d9b3bd1ad767b5`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.75`             | `1.00`            | `0.25`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `12.70`            | `3.04`            | `9.66`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `90.00`            | `90.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `23.50`            | `19.97`           | `3.53`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `6.00`             | `6.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `43.19`            | `72.12`           | `28.93`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `43.19`            | `72.12`           | `28.93`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.75 - 1.00| = 0.25$
- **Entropy Error:** $|12.70 - 3.04| = 9.66$
- **Impact Error:** $|90.00 - 90.00| = 0.00$
- **Cost Error:** $|23.50 - 19.97| = 3.53$
- **Learning Value Error:** $|6.00 - 6.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.75 \times 90.0 + 0.5 \times 6.0 - 0.3 \times 12.70 - 23.50 = 43.19$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 90.0 + 0.5 \times 6.0 - 0.3 \times 3.04 - 19.97 = 72.12$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 90.0 + 0.5 \times 6.0 - 0.3 \times 3.04 - 19.97 = 72.12$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +28.93$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `10.0` (19 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794294214-0a2285cf-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.25$,
$cost\_error = 3.53$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
