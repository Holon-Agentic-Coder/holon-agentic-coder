# Plan Calibration Report: P-1794225025-924685ae-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1794225025-924685ae-claude-agent-claude-opus-5-5.md`](P-1794225025-924685ae-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1794225351-0062997c-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1794224634-plan-owned-decomposition-with-plan-branch-children--1-record-the-decomposition-decision-on-the-plan-ro/P-1794225025-924685ae-claude-agent-claude-opus-5-5/E-1794225351-0062997c-claude-agent-claude-opus-5-5/_`
- **Intent Branch:**
  `I-1794224634-plan-owned-decomposition-with-plan-branch-children--1-record-the-decomposition-decision-on-the-plan-ro/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-11-09T12:37:29.000Z`
- **Evaluated Commit SHA:** `8b6121d46978da978b664fc747c2f44a0c825e0d`
- **Base SHA:** `403c8a46e317863b73fe3452bab8f9eebcf50521`
- **Merge-Base SHA:** `403c8a46e317863b73fe3452bab8f9eebcf50521`
- **Merged Commits:** `0`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.74`             | `1.00`            | `0.26`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `13.00`            | `3.04`            | `9.96`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `80.00`            | `80.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `15.00`            | `12.75`           | `2.25`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `5.00`             | `5.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `42.80`            | `68.84`           | `26.04`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `42.80`            | `68.84`           | `26.04`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.74 - 1.00| = 0.26$
- **Entropy Error:** $|13.00 - 3.04| = 9.96$
- **Impact Error:** $|80.00 - 80.00| = 0.00$
- **Cost Error:** $|15.00 - 12.75| = 2.25$
- **Learning Value Error:** $|5.00 - 5.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.74 \times 80.0 + 0.5 \times 5.0 - 0.3 \times 13.00 - 15.00 = 42.80$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 80.0 + 0.5 \times 5.0 - 0.3 \times 3.04 - 12.75 = 68.84$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 80.0 + 0.5 \times 5.0 - 0.3 \times 3.04 - 12.75 = 68.84$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +26.04$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `10.0` (18 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794225025-924685ae-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.26$,
$cost\_error = 2.25$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
