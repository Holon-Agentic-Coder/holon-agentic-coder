# Plan Calibration Report: P-1795544838-ANA-G4N

- **Plan Reference:** [`plans/I-1795544814-core-helpers-self-doc/P-1795544838-ANA-G4N.md`](P-1795544838-ANA-G4N.md)
- **Execution ID:** `E-1795545249-ANA-G4N`
- **Execution Branch:** `I-1795544814-core-helpers-self-doc/P-1795544838-ANA-G4N/E-1795545249-ANA-G4N/_`
- **Intent Branch:** `I-1795544814-core-helpers-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-24T19:30:44.000Z`
- **Evaluated Commit SHA:** `eef44e5d6f3b98d95b3aa687b2c69ca0dffe56b0`
- **Base SHA:** `ff1668c898f22e1bafaecc3010d3ef149cac21ff`
- **Merge-Base SHA:** `ff1668c898f22e1bafaecc3010d3ef149cac21ff`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.86`             | `1.00`            | `0.14`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `4.68`             | `1.71`            | `2.97`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `55.00`            | `55.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `9.00`             | `7.65`            | `1.35`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.50`             | `4.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `39.15`            | `49.09`           | `9.94`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `39.15`            | `49.09`           | `9.94`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.86 - 1.00| = 0.14$
- **Entropy Error:** $|4.68 - 1.71| = 2.97$
- **Impact Error:** $|55.00 - 55.00| = 0.00$
- **Cost Error:** $|9.00 - 7.65| = 1.35$
- **Learning Value Error:** $|4.50 - 4.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.86 \times 55.0 + 0.5 \times 4.5 - 0.3 \times 4.68 - 9.00 = 39.15$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 55.0 + 0.5 \times 4.5 - 0.3 \times 1.71 - 7.65 = 49.09$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 55.0 + 0.5 \times 4.5 - 0.3 \times 1.71 - 7.65 = 49.09$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +9.94$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `5.6` (10 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795544838-ANA-G4N` executed with minimal error ($p\_success\_error = 0.14$, $cost\_error = 1.35$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none
