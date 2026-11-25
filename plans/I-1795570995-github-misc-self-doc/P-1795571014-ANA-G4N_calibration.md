# Plan Calibration Report: P-1795571014-ANA-G4N

- **Plan Reference:** [`plans/I-1795570995-github-misc-self-doc/P-1795571014-ANA-G4N.md`](P-1795571014-ANA-G4N.md)
- **Execution ID:** `E-1795571370-ANA-G4N`
- **Execution Branch:** `I-1795570995-github-misc-self-doc/P-1795571014-ANA-G4N/E-1795571370-ANA-G4N/_`
- **Intent Branch:** `I-1795570995-github-misc-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-25T02:02:47.000Z`
- **Evaluated Commit SHA:** `efbb7d9c0d84956965b1153c51fca162e16b836d`
- **Base SHA:** `ab4dbfd975130c9e28055d2ff936bc907e894cfb`
- **Merge-Base SHA:** `ab4dbfd975130c9e28055d2ff936bc907e894cfb`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.87`             | `1.00`            | `0.13`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `3.34`             | `1.18`            | `2.16`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `60.00`            | `60.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `6.00`             | `5.10`            | `0.90`                                | High (≤ 1.0)    | Highly Accurate            |
| **Learning Value**                                 | `1.80`             | `1.80`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `46.10`            | `55.45`           | `9.35`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `46.10`            | `55.45`           | `9.35`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.87 - 1.00| = 0.13$
- **Entropy Error:** $|3.34 - 1.18| = 2.16$
- **Impact Error:** $|60.00 - 60.00| = 0.00$
- **Cost Error:** $|6.00 - 5.10| = 0.90$
- **Learning Value Error:** $|1.80 - 1.80| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.87 \times 60.0 + 0.5 \times 1.8 - 0.3 \times 3.34 - 6.00 = 46.10$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 60.0 + 0.5 \times 1.8 - 0.3 \times 1.18 - 5.10 = 55.45$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 60.0 + 0.5 \times 1.8 - 0.3 \times 1.18 - 5.10 = 55.45$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +9.35$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `3.8` (7 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795571014-ANA-G4N` executed with minimal error ($p\_success\_error = 0.13$, $cost\_error = 0.90$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none
