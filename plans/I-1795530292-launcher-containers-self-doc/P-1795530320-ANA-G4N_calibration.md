# Plan Calibration Report: P-1795530320-ANA-G4N

- **Plan Reference:**
  [`plans/I-1795530292-launcher-containers-self-doc/P-1795530320-ANA-G4N.md`](P-1795530320-ANA-G4N.md)
- **Execution ID:** `E-1795530595-ANA-G4N`
- **Execution Branch:** `I-1795530292-launcher-containers-self-doc/P-1795530320-ANA-G4N/E-1795530595-ANA-G4N/_`
- **Intent Branch:** `I-1795530292-launcher-containers-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-24T14:51:34.000Z`
- **Evaluated Commit SHA:** `f19ee3b3cf07fb2f74810d7b09103b78cf9b8540`
- **Base SHA:** `92a37975fb6ec8cf6019db0d337b8e74d5f2f9fa`
- **Merge-Base SHA:** `92a37975fb6ec8cf6019db0d337b8e74d5f2f9fa`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.89`             | `1.00`            | `0.11`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `5.23`             | `1.68`            | `3.55`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `55.00`            | `55.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `12.00`            | `10.20`           | `1.80`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `2.50`             | `2.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `36.63`            | `45.55`           | `8.92`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `36.63`            | `45.55`           | `8.92`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.89 - 1.00| = 0.11$
- **Entropy Error:** $|5.23 - 1.68| = 3.55$
- **Impact Error:** $|55.00 - 55.00| = 0.00$
- **Cost Error:** $|12.00 - 10.20| = 1.80$
- **Learning Value Error:** $|2.50 - 2.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.89 \times 55.0 + 0.5 \times 2.5 - 0.3 \times 5.23 - 12.00 = 36.63$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 55.0 + 0.5 \times 2.5 - 0.3 \times 1.68 - 10.20 = 45.55$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 55.0 + 0.5 \times 2.5 - 0.3 \times 1.68 - 10.20 = 45.55$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +8.92$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `5.5` (10 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795530320-ANA-G4N` executed with minimal error ($p\_success\_error = 0.11$, $cost\_error = 1.80$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none
