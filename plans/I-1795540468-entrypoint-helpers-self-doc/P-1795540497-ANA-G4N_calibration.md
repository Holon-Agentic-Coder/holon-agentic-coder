# Plan Calibration Report: P-1795540497-ANA-G4N

- **Plan Reference:**
  [`plans/I-1795540468-entrypoint-helpers-self-doc/P-1795540497-ANA-G4N.md`](P-1795540497-ANA-G4N.md)
- **Execution ID:** `E-1795540819-ANA-G4N`
- **Execution Branch:** `I-1795540468-entrypoint-helpers-self-doc/P-1795540497-ANA-G4N/E-1795540819-ANA-G4N/_`
- **Intent Branch:** `I-1795540468-entrypoint-helpers-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-24T17:51:33.000Z`
- **Evaluated Commit SHA:** `4225fba89c6c69c9e60b40fa3d5bf0376f7047ac`
- **Base SHA:** `1441a6636bb3225a1e3f19126bf501ace5fcb1a2`
- **Merge-Base SHA:** `1441a6636bb3225a1e3f19126bf501ace5fcb1a2`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.85`             | `1.00`            | `0.15`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `3.80`             | `1.29`            | `2.51`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `60.00`            | `60.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `10.50`            | `8.92`            | `1.58`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `2.50`             | `2.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `40.61`            | `51.94`           | `11.33`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `40.61`            | `51.94`           | `11.33`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.85 - 1.00| = 0.15$
- **Entropy Error:** $|3.80 - 1.29| = 2.51$
- **Impact Error:** $|60.00 - 60.00| = 0.00$
- **Cost Error:** $|10.50 - 8.92| = 1.58$
- **Learning Value Error:** $|2.50 - 2.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.85 \times 60.0 + 0.5 \times 2.5 - 0.3 \times 3.80 - 10.50 = 40.61$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 60.0 + 0.5 \times 2.5 - 0.3 \times 1.29 - 8.92 = 51.94$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 60.0 + 0.5 \times 2.5 - 0.3 \times 1.29 - 8.92 = 51.94$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +11.33$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `4.2` (11 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795540497-ANA-G4N` executed with minimal error ($p\_success\_error = 0.15$, $cost\_error = 1.58$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none
