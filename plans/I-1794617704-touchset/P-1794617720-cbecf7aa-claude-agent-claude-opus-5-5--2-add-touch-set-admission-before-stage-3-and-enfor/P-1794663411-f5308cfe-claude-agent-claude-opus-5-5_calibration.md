# Plan Calibration Report: P-1794663411-f5308cfe-claude-agent-claude-opus-5-5

- **Plan Reference:**
  [`plans/P-1794663411-f5308cfe-claude-agent-claude-opus-5-5.md`](P-1794663411-f5308cfe-claude-agent-claude-opus-5-5.md)
- **Execution ID:** `E-1794663558-0405d999-claude-agent-claude-opus-5-5`
- **Execution Branch:**
  `I-1794617704-touchset/P-1794617720-cbecf7aa-claude-agent-claude-opus-5-5--2-add-touch-set-admission-before-stage-3-and-enfor/P-1794663411-f5308cfe-claude-agent-claude-opus-5-5/E-1794663558-0405d999-claude-agent-claude-opus-5-5/_`
- **Intent Branch:** `I-1794617704-touchset/_`
- **Evaluating Agent:** `claude-agent/claude-opus-5-5`
- **Evaluation Timestamp:** `2026-11-14T23:00:30.000Z`
- **Evaluated Commit SHA:** `1f4763492f39ab39f38ac763e7b83c4c6ba4a081`
- **Patch Size Base:** `merge_base_fallback` (not measured against the plan branch)

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.72`             | `1.00`            | `0.28`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `11.00`            | `3.04`            | `7.96`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `82.00`            | `82.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `16.00`            | `13.60`           | `2.40`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `6.00`             | `6.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `42.74`            | `70.49`           | `27.75`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `42.74`            | `70.49`           | `27.75`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.72 - 1.00| = 0.28$
- **Entropy Error:** $|11.00 - 3.04| = 7.96$
- **Impact Error:** $|82.00 - 82.00| = 0.00$
- **Cost Error:** $|16.00 - 13.60| = 2.40$
- **Learning Value Error:** $|6.00 - 6.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.72 \times 82.0 + 0.5 \times 6.0 - 0.3 \times 11.00 - 16.00 = 42.74$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 82.0 + 0.5 \times 6.0 - 0.3 \times 3.04 - 13.60 = 70.49$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 82.0 + 0.5 \times 6.0 - 0.3 \times 3.04 - 13.60 = 70.49$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +27.75$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `10.0` (53 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1794663411-f5308cfe-claude-agent-claude-opus-5-5` executed with minimal error ($p\_success\_error = 0.28$,
$cost\_error = 2.40$). The calibration step successfully completed the execution lifecycle and delivered verified
post-execution analysis.
