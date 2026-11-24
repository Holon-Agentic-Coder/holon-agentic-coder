# Plan Calibration Report: P-1795467820-ANA-G4A

- **Plan Reference:**
  [`plans/I-1795444114-claude-code-mode-default-on/P-1795467820-ANA-G4A.md`](P-1795467820-ANA-G4A.md)
- **Execution ID:** `E-1795468054-ANA-G4A`
- **Execution Branch:** `I-1795444114-claude-code-mode-default-on/P-1795467820-ANA-G4A/E-1795468054-ANA-G4A/_`
- **Intent Branch:** `I-1795444114-claude-code-mode-default-on/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-argon`
- **Evaluation Timestamp:** `2026-11-24T07:31:33.000Z`
- **Evaluated Commit SHA:** `9a4366cb693044b7d903076c2af430797037ce58`
- **Base SHA:** `9f57492db7b9401d2e11e26f79b05e7c6e4204c3`
- **Merge-Base SHA:** `9f57492db7b9401d2e11e26f79b05e7c6e4204c3`
- **Merged Commits:** `3`
- **Patch Size Base:** `target_merge_base` (merge-base `9f57492db7b9401d2e11e26f79b05e7c6e4204c3` with the target
  branch)

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.94`             | `1.00`            | `0.06`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `3.50`             | `1.26`            | `2.24`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `72.00`            | `72.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `6.00`             | `5.10`            | `0.90`                                | High (≤ 1.0)    | Highly Accurate            |
| **Learning Value**                                 | `2.50`             | `2.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `61.88`            | `67.77`           | `5.89`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `61.88`            | `67.77`           | `5.89`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.94 - 1.00| = 0.06$
- **Entropy Error:** $|3.50 - 1.26| = 2.24$
- **Impact Error:** $|72.00 - 72.00| = 0.00$
- **Cost Error:** $|6.00 - 5.10| = 0.90$
- **Learning Value Error:** $|2.50 - 2.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.94 \times 72.0 + 0.5 \times 2.5 - 0.3 \times 3.50 - 6.00 = 61.88$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 72.0 + 0.5 \times 2.5 - 0.3 \times 1.26 - 5.10 = 67.77$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 72.0 + 0.5 \times 2.5 - 0.3 \times 1.26 - 5.10 = 67.77$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +5.89$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `4.1` (11 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795467820-ANA-G4A` executed with minimal error ($p\_success\_error = 0.06$, $cost\_error = 0.90$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none
