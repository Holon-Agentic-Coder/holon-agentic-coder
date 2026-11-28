# Plan Calibration Report: P-1795802441-ANA-G4N

- **Plan Reference:**
  [`plans/I-1795802419-tests-fewer-git-review-decomp/P-1795802441-ANA-G4N.md`](P-1795802441-ANA-G4N.md)
- **Execution ID:** `E-1795812763-ANA-G4N`
- **Execution Branch:** `I-1795802419-tests-fewer-git-review-decomp/P-1795802441-ANA-G4N/E-1795812763-ANA-G4N/_`
- **Intent Branch:** `I-1795802419-tests-fewer-git-review-decomp/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T22:09:58.000Z`
- **Evaluated Commit SHA:** `28c52414571a87c8473e5b5eacda45ff9b94587e`
- **Base SHA:** `553e813efbc4ba708bec590d4d0b0e4609e306bf`
- **Merge-Base SHA:** `553e813efbc4ba708bec590d4d0b0e4609e306bf`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.86`             | `1.00`            | `0.14`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `6.40`             | `1.36`            | `5.04`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `78.00`            | `78.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `24.00`            | `20.40`           | `3.60`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.20`             | `4.20`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `43.26`            | `59.29`           | `16.03`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `43.26`            | `59.29`           | `16.03`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.86 - 1.00| = 0.14$
- **Entropy Error:** $|6.40 - 1.36| = 5.04$
- **Impact Error:** $|78.00 - 78.00| = 0.00$
- **Cost Error:** $|24.00 - 20.40| = 3.60$
- **Learning Value Error:** $|4.20 - 4.20| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.86 \times 78.0 + 0.5 \times 4.2 - 0.3 \times 6.40 - 24.00 = 43.26$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 78.0 + 0.5 \times 4.2 - 0.3 \times 1.36 - 20.40 = 59.29$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 78.0 + 0.5 \times 4.2 - 0.3 \times 1.36 - 20.40 = 59.29$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +16.03$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `4.4` (6 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795802441-ANA-G4N` executed with minimal error ($p\_success\_error = 0.14$, $cost\_error = 3.60$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `85.7%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- `apps/sandbox-executor/tests/flow/sync_boundary_repos.py`

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

Against the merge base with origin/main: added 0 docstring(s), added 0 comment(s), removed 1 docstring(s) and removed 1
comment(s) across 11 changed file(s).

- Changed files checked: 11
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 1
- Removed comments (not '# why:'): 1
