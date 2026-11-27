# Plan Calibration Report: P-1795800019-ANA-G4N

- **Plan Reference:** [`plans/I-1795799994-ci-merge-bookkeeping-chain/P-1795800019-ANA-G4N.md`](P-1795800019-ANA-G4N.md)
- **Execution ID:** `E-1795800401-ANA-G4N`
- **Execution Branch:** `I-1795799994-ci-merge-bookkeeping-chain/P-1795800019-ANA-G4N/E-1795800401-ANA-G4N/_`
- **Intent Branch:** `I-1795799994-ci-merge-bookkeeping-chain/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T17:58:50.000Z`
- **Evaluated Commit SHA:** `ed215051a5cdee7fc6cc5aea398e1de91fdb2840`
- **Base SHA:** `737d3dfc31c2133504c01a3fda9f6892c223f41b`
- **Merge-Base SHA:** `737d3dfc31c2133504c01a3fda9f6892c223f41b`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.82`             | `1.00`            | `0.18`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `7.60`             | `1.38`            | `6.22`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `75.00`            | `75.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `15.50`            | `13.17`           | `2.33`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.00`             | `4.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `45.72`            | `63.42`           | `17.70`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `45.72`            | `63.42`           | `17.70`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.82 - 1.00| = 0.18$
- **Entropy Error:** $|7.60 - 1.38| = 6.22$
- **Impact Error:** $|75.00 - 75.00| = 0.00$
- **Cost Error:** $|15.50 - 13.17| = 2.33$
- **Learning Value Error:** $|4.00 - 4.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.82 \times 75.0 + 0.5 \times 4.0 - 0.3 \times 7.60 - 15.50 = 45.72$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 75.0 + 0.5 \times 4.0 - 0.3 \times 1.38 - 13.17 = 63.42$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 75.0 + 0.5 \times 4.0 - 0.3 \times 1.38 - 13.17 = 63.42$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +17.70$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `4.5` (9 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795800019-ANA-G4N` executed with minimal error ($p\_success\_error = 0.18$, $cost\_error = 2.33$). The
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

Against the merge base with origin/main: added 0 docstring(s), added 0 comment(s), removed 3 docstring(s) and removed 0
comment(s) across 14 changed file(s).

- Changed files checked: 14
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 3
- Removed comments (not '# why:'): 0
