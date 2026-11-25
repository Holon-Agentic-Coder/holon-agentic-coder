# Plan Calibration Report: P-1795569476-ANA-G4N

- **Plan Reference:** [`plans/I-1795569457-agent-defaults-self-doc/P-1795569476-ANA-G4N.md`](P-1795569476-ANA-G4N.md)
- **Execution ID:** `E-1795569826-ANA-G4N`
- **Execution Branch:** `I-1795569457-agent-defaults-self-doc/P-1795569476-ANA-G4N/E-1795569826-ANA-G4N/_`
- **Intent Branch:** `I-1795569457-agent-defaults-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-25T01:36:43.000Z`
- **Evaluated Commit SHA:** `d5835c8aa456155dbdd52de98bd568c66ac35272`
- **Base SHA:** `cb5e048c2e4a5802c7cd8f4e8d8cf5edb837c5ac`
- **Merge-Base SHA:** `cb5e048c2e4a5802c7cd8f4e8d8cf5edb837c5ac`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.90`             | `1.00`            | `0.10`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `2.00`             | `0.75`            | `1.25`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `55.00`            | `55.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `13.00`            | `11.05`           | `1.95`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `1.50`             | `1.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `36.65`            | `44.47`           | `7.82`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `36.65`            | `44.47`           | `7.82`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.90 - 1.00| = 0.10$
- **Entropy Error:** $|2.00 - 0.75| = 1.25$
- **Impact Error:** $|55.00 - 55.00| = 0.00$
- **Cost Error:** $|13.00 - 11.05| = 1.95$
- **Learning Value Error:** $|1.50 - 1.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.90 \times 55.0 + 0.5 \times 1.5 - 0.3 \times 2.00 - 13.00 = 36.65$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 55.0 + 0.5 \times 1.5 - 0.3 \times 0.75 - 11.05 = 44.47$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 55.0 + 0.5 \times 1.5 - 0.3 \times 0.75 - 11.05 = 44.47$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +7.82$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `2.4` (2 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795569476-ANA-G4N` executed with minimal error ($p\_success\_error = 0.10$, $cost\_error = 1.95$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none
