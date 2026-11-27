# Plan Calibration Report: P-1795794478-ANA-G4N

- **Plan Reference:**
  [`plans/I-1795794446-stage2-plan-in-intent-scope/P-1795794478-ANA-G4N.md`](P-1795794478-ANA-G4N.md)
- **Execution ID:** `E-1795794723-ANA-G4N`
- **Execution Branch:** `I-1795794446-stage2-plan-in-intent-scope/P-1795794478-ANA-G4N/E-1795794723-ANA-G4N/_`
- **Intent Branch:** `I-1795794446-stage2-plan-in-intent-scope/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T16:35:16.000Z`
- **Evaluated Commit SHA:** `44b0054cdf2099cc3a5d608c9da4fccd770a2a52`
- **Base SHA:** `c9958a896b8bdfa9f8e0c11136c71a64c51a7668`
- **Merge-Base SHA:** `c9958a896b8bdfa9f8e0c11136c71a64c51a7668`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.83`             | `1.00`            | `0.17`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `3.26`             | `0.87`            | `2.39`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `75.00`            | `75.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `9.00`             | `7.65`            | `1.35`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `3.00`             | `3.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `53.77`            | `68.59`           | `14.82`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `53.77`            | `68.59`           | `14.82`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.83 - 1.00| = 0.17$
- **Entropy Error:** $|3.26 - 0.87| = 2.39$
- **Impact Error:** $|75.00 - 75.00| = 0.00$
- **Cost Error:** $|9.00 - 7.65| = 1.35$
- **Learning Value Error:** $|3.00 - 3.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.83 \times 75.0 + 0.5 \times 3.0 - 0.3 \times 3.26 - 9.00 = 53.77$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 75.0 + 0.5 \times 3.0 - 0.3 \times 0.87 - 7.65 = 68.59$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 75.0 + 0.5 \times 3.0 - 0.3 \times 0.87 - 7.65 = 68.59$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +14.82$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `2.8` (3 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795794478-ANA-G4N` executed with minimal error ($p\_success\_error = 0.17$, $cost\_error = 1.35$). The
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
- **Scoped reviews:** `1`
- **Approval carry-overs:** `0`
- **Pipeline runs:** `2`
- **Pipeline reruns:** `0`
- **Non-defect churn (target merge):** `0`
- **Non-defect churn (bookkeeping):** `0`
- **Non-defect churn (CI noise):** `0`

---

## 7. Self-Explaining Code Counts

Against the merge base with origin/main: added 0 docstring(s), added 0 comment(s), removed 0 docstring(s) and removed 0
comment(s) across 8 changed file(s).

- Changed files checked: 8
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 0
- Removed comments (not '# why:'): 0
