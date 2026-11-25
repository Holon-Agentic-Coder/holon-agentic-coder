# Plan Calibration Report: P-1795544980-ANA-G4N

- **Plan Reference:** [`plans/I-1795544951-git-utils-self-doc/P-1795544980-ANA-G4N.md`](P-1795544980-ANA-G4N.md)
- **Execution ID:** `E-1795545306-ANA-G4N`
- **Execution Branch:** `I-1795544951-git-utils-self-doc/P-1795544980-ANA-G4N/E-1795545306-ANA-G4N/_`
- **Intent Branch:** `I-1795544951-git-utils-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-24T18:52:51.000Z`
- **Evaluated Commit SHA:** `51f4e4244b727c8be3d2cf0746f2997c8720b4cf`
- **Base SHA:** `4b59a0cc1b9aba9c7fd7eb0c5e7c19b39d3cedef`
- **Merge-Base SHA:** `4b59a0cc1b9aba9c7fd7eb0c5e7c19b39d3cedef`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.81`             | `1.00`            | `0.19`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `3.85`             | `1.79`            | `2.06`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `65.00`            | `65.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `10.00`            | `8.50`            | `1.50`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `2.50`             | `2.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `42.75`            | `57.21`           | `14.46`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `42.75`            | `57.21`           | `14.46`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.81 - 1.00| = 0.19$
- **Entropy Error:** $|3.85 - 1.79| = 2.06$
- **Impact Error:** $|65.00 - 65.00| = 0.00$
- **Cost Error:** $|10.00 - 8.50| = 1.50$
- **Learning Value Error:** $|2.50 - 2.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.81 \times 65.0 + 0.5 \times 2.5 - 0.3 \times 3.85 - 10.00 = 42.75$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 65.0 + 0.5 \times 2.5 - 0.3 \times 1.79 - 8.50 = 57.21$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 65.0 + 0.5 \times 2.5 - 0.3 \times 1.79 - 8.50 = 57.21$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +14.46$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `5.8` (9 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795544980-ANA-G4N` executed with minimal error ($p\_success\_error = 0.19$, $cost\_error = 1.50$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none
