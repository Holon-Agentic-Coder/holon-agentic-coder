# Plan Calibration Report: P-1795518467-ANA-G4N

- **Plan Reference:**
  [`plans/I-1795425253-resolve-conflicts-in-flow/P-1795425271-CLA-CO5.5--4-route-stage-4-and-stage-5/P-1795518467-ANA-G4N.md`](P-1795518467-ANA-G4N.md)
- **Execution ID:** `E-1795518759-ANA-G4N`
- **Execution Branch:**
  `I-1795425253-resolve-conflicts-in-flow/P-1795425271-CLA-CO5.5--4-route-stage-4-and-stage-5/P-1795518467-ANA-G4N/E-1795518759-ANA-G4N/_`
- **Intent Branch:** `I-1795425253-resolve-conflicts-in-flow/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-24T12:46:56.000Z`
- **Evaluated Commit SHA:** `8452d325081c9fa866587d6c37a95f2ddcefbcbc`
- **Base SHA:** `cdd93fdad3a8b64aaf76317ac751d45ead0d4d0a`
- **Merge-Base SHA:** `cdd93fdad3a8b64aaf76317ac751d45ead0d4d0a`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.76`             | `1.00`            | `0.24`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `10.50`            | `3.04`            | `7.46`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `85.00`            | `85.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `16.00`            | `13.60`           | `2.40`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `5.00`             | `5.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `47.95`            | `72.99`           | `25.04`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `47.95`            | `72.99`           | `25.04`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.76 - 1.00| = 0.24$
- **Entropy Error:** $|10.50 - 3.04| = 7.46$
- **Impact Error:** $|85.00 - 85.00| = 0.00$
- **Cost Error:** $|16.00 - 13.60| = 2.40$
- **Learning Value Error:** $|5.00 - 5.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.76 \times 85.0 + 0.5 \times 5.0 - 0.3 \times 10.50 - 16.00 = 47.95$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 85.0 + 0.5 \times 5.0 - 0.3 \times 3.04 - 13.60 = 72.99$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 85.0 + 0.5 \times 5.0 - 0.3 \times 3.04 - 13.60 = 72.99$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +25.04$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `10.0` (38 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795518467-ANA-G4N` executed with minimal error ($p\_success\_error = 0.24$, $cost\_error = 2.40$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `76.6%`
- **Recall (changed files the touch set covered):** `94.7%`

Changed files outside the touch set:

- `apps/sandbox-executor/src/sandbox_executor/flow/review/adjudication.py`
- `apps/sandbox-executor/src/sandbox_executor/review_request.py`

Declared entries the diff did not touch:

- `apps/sandbox-executor/src/sandbox_executor/flow/stage_conflicts.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/review/conflict_merge.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/review/posting.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/review_restart.py`
- `apps/sandbox-executor/tests/flow/review/test_approval_carry_over.py`
- `apps/sandbox-executor/tests/flow/review/test_loop.py`
- `apps/sandbox-executor/tests/flow/stages/test_calibrate.py`
- `apps/sandbox-executor/tests/flow/test_sync_boundaries.py`
- `apps/sandbox-executor/tests/test_calibrate_no_fallback.py`
- `apps/sandbox-executor/tests/test_review_ensemble.py`
- `apps/sandbox-executor/tests/test_review_restart.py`
