# Plan Calibration Report: P-1795799861-ANA-G4N

- **Plan Reference:**
  [`plans/I-1795799839-stage1-requires-assumptions/P-1795799861-ANA-G4N.md`](P-1795799861-ANA-G4N.md)
- **Execution ID:** `E-1795800164-ANA-G4N`
- **Execution Branch:** `I-1795799839-stage1-requires-assumptions/P-1795799861-ANA-G4N/E-1795800164-ANA-G4N/_`
- **Intent Branch:** `I-1795799839-stage1-requires-assumptions/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T17:41:55.000Z`
- **Evaluated Commit SHA:** `0bee37d0a66060df11d0431cbdb9a3fc301b041e`
- **Base SHA:** `f829a70a0b088dfa072bfcc4eb5509fc75233b34`
- **Merge-Base SHA:** `f829a70a0b088dfa072bfcc4eb5509fc75233b34`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.85`             | `1.00`            | `0.15`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `5.40`             | `2.13`            | `3.27`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `92.00`            | `92.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `15.00`            | `12.75`           | `2.25`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `5.50`             | `5.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `64.33`            | `81.36`           | `17.03`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `64.33`            | `81.36`           | `17.03`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.85 - 1.00| = 0.15$
- **Entropy Error:** $|5.40 - 2.13| = 3.27$
- **Impact Error:** $|92.00 - 92.00| = 0.00$
- **Cost Error:** $|15.00 - 12.75| = 2.25$
- **Learning Value Error:** $|5.50 - 5.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.85 \times 92.0 + 0.5 \times 5.5 - 0.3 \times 5.40 - 15.00 = 64.33$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 92.0 + 0.5 \times 5.5 - 0.3 \times 2.13 - 12.75 = 81.36$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 92.0 + 0.5 \times 5.5 - 0.3 \times 2.13 - 12.75 = 81.36$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +17.03$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `7.0` (19 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795799861-ANA-G4N` executed with minimal error ($p\_success\_error = 0.15$, $cost\_error = 2.25$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `95.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- `apps/sandbox-executor/src/sandbox_executor/flow/intent_questions.py`

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
comment(s) across 24 changed file(s).

- Changed files checked: 24
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 0
- Removed comments (not '# why:'): 0
