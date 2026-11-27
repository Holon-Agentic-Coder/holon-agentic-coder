# Plan Calibration Report: P-1795817889-ANA-G4N

- **Plan Reference:**
  [`plans/I-1795817858-hac-silent-except-agent-runner/P-1795817889-ANA-G4N.md`](P-1795817889-ANA-G4N.md)
- **Execution ID:** `E-1795818110-ANA-G4N`
- **Execution Branch:** `I-1795817858-hac-silent-except-agent-runner/P-1795817889-ANA-G4N/E-1795818110-ANA-G4N/_`
- **Intent Branch:** `I-1795817858-hac-silent-except-agent-runner/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T22:38:12.000Z`
- **Evaluated Commit SHA:** `fc7562dd4970fb52e308936da296471ca07fdf6f`
- **Base SHA:** `d7ae32a69824148ee210544c9ea647c19a4f0951`
- **Merge-Base SHA:** `d7ae32a69824148ee210544c9ea647c19a4f0951`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.88`             | `1.00`            | `0.12`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `2.16`             | `0.33`            | `1.83`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `60.00`            | `60.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `9.00`             | `7.65`            | `1.35`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `2.00`             | `2.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `44.15`            | `53.25`           | `9.10`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `44.15`            | `53.25`           | `9.10`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.88 - 1.00| = 0.12$
- **Entropy Error:** $|2.16 - 0.33| = 1.83$
- **Impact Error:** $|60.00 - 60.00| = 0.00$
- **Cost Error:** $|9.00 - 7.65| = 1.35$
- **Learning Value Error:** $|2.00 - 2.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.88 \times 60.0 + 0.5 \times 2.0 - 0.3 \times 2.16 - 9.00 = 44.15$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 60.0 + 0.5 \times 2.0 - 0.3 \times 0.33 - 7.65 = 53.25$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 60.0 + 0.5 \times 2.0 - 0.3 \times 0.33 - 7.65 = 53.25$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +9.10$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `1.0` (2 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795817889-ANA-G4N` executed with minimal error ($p\_success\_error = 0.12$, $cost\_error = 1.35$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none

---

## 6. Review Churn

- **Full reviews:** `1`
- **Scoped reviews:** `0`
- **Approval carry-overs:** `0`
- **Pipeline runs:** `1`
- **Pipeline reruns:** `0`
- **Non-defect churn (target merge):** `0`
- **Non-defect churn (bookkeeping):** `0`
- **Non-defect churn (CI noise):** `0`

---

## 7. Self-Explaining Code Counts

Against the merge base with origin/main: added 0 docstring(s), added 0 comment(s), removed 0 docstring(s) and removed 0
comment(s) across 7 changed file(s).

- Changed files checked: 7
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 0
- Removed comments (not '# why:'): 0
