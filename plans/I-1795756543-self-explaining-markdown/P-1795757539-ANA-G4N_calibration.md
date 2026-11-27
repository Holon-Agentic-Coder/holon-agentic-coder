# Plan Calibration Report: P-1795757539-ANA-G4N

- **Plan Reference:** [`plans/I-1795756543-self-explaining-markdown/P-1795757539-ANA-G4N.md`](P-1795757539-ANA-G4N.md)
- **Execution ID:** `E-1795757762-ANA-G4N`
- **Execution Branch:** `I-1795756543-self-explaining-markdown/P-1795757539-ANA-G4N/E-1795757762-ANA-G4N/_`
- **Intent Branch:** `I-1795756543-self-explaining-markdown/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T06:02:29.000Z`
- **Evaluated Commit SHA:** `17726fd5ff772580191d77412448b4504ad17191`
- **Base SHA:** `b3c6be4f4496a451082b7300e5d1ccbaf1767dd6`
- **Merge-Base SHA:** `b3c6be4f4496a451082b7300e5d1ccbaf1767dd6`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.77`             | `1.00`            | `0.23`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `5.50`             | `1.35`            | `4.15`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `75.00`            | `75.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `15.00`            | `12.75`           | `2.25`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.50`             | `4.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `43.35`            | `64.10`           | `20.75`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `43.35`            | `64.10`           | `20.75`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.77 - 1.00| = 0.23$
- **Entropy Error:** $|5.50 - 1.35| = 4.15$
- **Impact Error:** $|75.00 - 75.00| = 0.00$
- **Cost Error:** $|15.00 - 12.75| = 2.25$
- **Learning Value Error:** $|4.50 - 4.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.77 \times 75.0 + 0.5 \times 4.5 - 0.3 \times 5.50 - 15.00 = 43.35$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 75.0 + 0.5 \times 4.5 - 0.3 \times 1.35 - 12.75 = 64.10$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 75.0 + 0.5 \times 4.5 - 0.3 \times 1.35 - 12.75 = 64.10$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +20.75$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `4.4` (5 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795757539-ANA-G4N` executed with minimal error ($p\_success\_error = 0.23$, $cost\_error = 2.25$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `83.3%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- `scripts/check_self_explaining.py`

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
comment(s) across 10 changed file(s).

- Changed files checked: 10
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 0
- Removed comments (not '# why:'): 0
