# Plan Calibration Report: P-1795565501-ANA-G4N

- **Plan Reference:** [`plans/I-1795565481-touch-set-self-doc/P-1795565501-ANA-G4N.md`](P-1795565501-ANA-G4N.md)
- **Execution ID:** `E-1795565805-ANA-G4N`
- **Execution Branch:** `I-1795565481-touch-set-self-doc/P-1795565501-ANA-G4N/E-1795565805-ANA-G4N/_`
- **Intent Branch:** `I-1795565481-touch-set-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-25T00:45:08.000Z`
- **Evaluated Commit SHA:** `c56b058c4943f90acc8f99d175d53aaeea409e24`
- **Base SHA:** `0c9335197799122a18093c70e174926bc3e23d0b`
- **Merge-Base SHA:** `0c9335197799122a18093c70e174926bc3e23d0b`
- **Merged Commits:** `2`
- **Patch Size Base:** `target_merge_base` (merge-base `0c9335197799122a18093c70e174926bc3e23d0b` with the target
  branch)

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.87`             | `1.00`            | `0.13`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `1.95`             | `0.65`            | `1.30`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `55.00`            | `55.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `6.00`             | `5.10`            | `0.90`                                | High (≤ 1.0)    | Highly Accurate            |
| **Learning Value**                                 | `2.00`             | `2.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `42.27`            | `50.71`           | `8.45`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `42.27`            | `50.71`           | `8.45`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.87 - 1.00| = 0.13$
- **Entropy Error:** $|1.95 - 0.65| = 1.30$
- **Impact Error:** $|55.00 - 55.00| = 0.00$
- **Cost Error:** $|6.00 - 5.10| = 0.90$
- **Learning Value Error:** $|2.00 - 2.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.87 \times 55.0 + 0.5 \times 2.0 - 0.3 \times 1.95 - 6.00 = 42.27$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 55.0 + 0.5 \times 2.0 - 0.3 \times 0.65 - 5.10 = 50.71$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 55.0 + 0.5 \times 2.0 - 0.3 \times 0.65 - 5.10 = 50.71$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +8.45$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `2.0` (3 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795565501-ANA-G4N` executed with minimal error ($p\_success\_error = 0.13$, $cost\_error = 0.90$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none
