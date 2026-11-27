# Plan Calibration Report: P-1795796523-ANA-G4N

- **Plan Reference:**
  [`plans/I-1795796484-hac-stage-registry-git-forge/P-1795796523-ANA-G4N.md`](P-1795796523-ANA-G4N.md)
- **Execution ID:** `E-1795796891-ANA-G4N`
- **Execution Branch:** `I-1795796484-hac-stage-registry-git-forge/P-1795796523-ANA-G4N/E-1795796891-ANA-G4N/_`
- **Intent Branch:** `I-1795796484-hac-stage-registry-git-forge/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T16:54:34.000Z`
- **Evaluated Commit SHA:** `37a1017e4982e62c3174b9f759edb53dc9569b6c`
- **Base SHA:** `bce757d7836f6b2da7b63065c650544a4a62bfbf`
- **Merge-Base SHA:** `bce757d7836f6b2da7b63065c650544a4a62bfbf`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.81`             | `1.00`            | `0.19`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `6.23`             | `1.54`            | `4.69`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `85.00`            | `85.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `14.00`            | `11.90`           | `2.10`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `3.00`             | `3.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `54.48`            | `74.14`           | `19.66`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `54.48`            | `74.14`           | `19.66`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.81 - 1.00| = 0.19$
- **Entropy Error:** $|6.23 - 1.54| = 4.69$
- **Impact Error:** $|85.00 - 85.00| = 0.00$
- **Cost Error:** $|14.00 - 11.90| = 2.10$
- **Learning Value Error:** $|3.00 - 3.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.81 \times 85.0 + 0.5 \times 3.0 - 0.3 \times 6.23 - 14.00 = 54.48$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 85.0 + 0.5 \times 3.0 - 0.3 \times 1.54 - 11.90 = 74.14$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 85.0 + 0.5 \times 3.0 - 0.3 \times 1.54 - 11.90 = 74.14$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +19.66$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `5.0` (14 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795796523-ANA-G4N` executed with minimal error ($p\_success\_error = 0.19$, $cost\_error = 2.10$). The
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
comment(s) across 19 changed file(s).

- Changed files checked: 19
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 0
- Removed comments (not '# why:'): 0
