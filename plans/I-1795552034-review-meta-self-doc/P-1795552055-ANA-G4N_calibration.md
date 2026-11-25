# Plan Calibration Report: P-1795552055-ANA-G4N

- **Plan Reference:** [`plans/I-1795552034-review-meta-self-doc/P-1795552055-ANA-G4N.md`](P-1795552055-ANA-G4N.md)
- **Execution ID:** `E-1795552366-ANA-G4N`
- **Execution Branch:** `I-1795552034-review-meta-self-doc/P-1795552055-ANA-G4N/E-1795552366-ANA-G4N/_`
- **Intent Branch:** `I-1795552034-review-meta-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-24T22:57:26.000Z`
- **Evaluated Commit SHA:** `0175c138dcc56180a16ac16aabeab0c46d719176`
- **Base SHA:** `4c82c0f7e8237757876da7ddd02021cbf2c1b093`
- **Merge-Base SHA:** `4c82c0f7e8237757876da7ddd02021cbf2c1b093`
- **Merged Commits:** `2`
- **Patch Size Base:** `target_merge_base` (merge-base `4c82c0f7e8237757876da7ddd02021cbf2c1b093` with the target
  branch)

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.85`             | `1.00`            | `0.15`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `3.01`             | `0.82`            | `2.19`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `50.00`            | `50.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `7.50`             | `6.38`            | `1.12`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `2.20`             | `2.20`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `35.20`            | `44.47`           | `9.27`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `35.20`            | `44.47`           | `9.27`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.85 - 1.00| = 0.15$
- **Entropy Error:** $|3.01 - 0.82| = 2.19$
- **Impact Error:** $|50.00 - 50.00| = 0.00$
- **Cost Error:** $|7.50 - 6.38| = 1.12$
- **Learning Value Error:** $|2.20 - 2.20| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.85 \times 50.0 + 0.5 \times 2.2 - 0.3 \times 3.01 - 7.50 = 35.20$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 50.0 + 0.5 \times 2.2 - 0.3 \times 0.82 - 6.38 = 44.47$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 50.0 + 0.5 \times 2.2 - 0.3 \times 0.82 - 6.38 = 44.47$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +9.27$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `2.6` (4 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795552055-ANA-G4N` executed with minimal error ($p\_success\_error = 0.15$, $cost\_error = 1.12$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none
