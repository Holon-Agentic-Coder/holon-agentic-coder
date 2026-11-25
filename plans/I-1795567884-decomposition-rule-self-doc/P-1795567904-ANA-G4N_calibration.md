# Plan Calibration Report: P-1795567904-ANA-G4N

- **Plan Reference:**
  [`plans/I-1795567884-decomposition-rule-self-doc/P-1795567904-ANA-G4N.md`](P-1795567904-ANA-G4N.md)
- **Execution ID:** `E-1795568222-ANA-G4N`
- **Execution Branch:** `I-1795567884-decomposition-rule-self-doc/P-1795567904-ANA-G4N/E-1795568222-ANA-G4N/_`
- **Intent Branch:** `I-1795567884-decomposition-rule-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-25T01:14:49.000Z`
- **Evaluated Commit SHA:** `283deb0dcdcc7b4d4ba7b0a22c17656e83a8ad5c`
- **Base SHA:** `131c3b42ab86a7a40835123794cb74820099518d`
- **Merge-Base SHA:** `131c3b42ab86a7a40835123794cb74820099518d`
- **Merged Commits:** `1`
- **Patch Size Base:** `target_merge_base` (merge-base `131c3b42ab86a7a40835123794cb74820099518d` with the target
  branch)

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.89`             | `1.00`            | `0.11`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `2.21`             | `0.75`            | `1.46`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `65.00`            | `65.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `9.00`             | `7.65`            | `1.35`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `3.00`             | `3.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `49.69`            | `58.62`           | `8.93`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `49.69`            | `58.62`           | `8.93`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.89 - 1.00| = 0.11$
- **Entropy Error:** $|2.21 - 0.75| = 1.46$
- **Impact Error:** $|65.00 - 65.00| = 0.00$
- **Cost Error:** $|9.00 - 7.65| = 1.35$
- **Learning Value Error:** $|3.00 - 3.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.89 \times 65.0 + 0.5 \times 3.0 - 0.3 \times 2.21 - 9.00 = 49.69$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 65.0 + 0.5 \times 3.0 - 0.3 \times 0.75 - 7.65 = 58.62$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 65.0 + 0.5 \times 3.0 - 0.3 \times 0.75 - 7.65 = 58.62$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +8.93$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `2.4` (2 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795567904-ANA-G4N` executed with minimal error ($p\_success\_error = 0.11$, $cost\_error = 1.35$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none
