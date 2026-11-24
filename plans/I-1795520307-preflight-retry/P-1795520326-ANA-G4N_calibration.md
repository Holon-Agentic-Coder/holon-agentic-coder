# Plan Calibration Report: P-1795520326-ANA-G4N

- **Plan Reference:** [`plans/I-1795520307-preflight-retry/P-1795520326-ANA-G4N.md`](P-1795520326-ANA-G4N.md)
- **Execution ID:** `E-1795520557-ANA-G4N`
- **Execution Branch:** `I-1795520307-preflight-retry/P-1795520326-ANA-G4N/E-1795520557-ANA-G4N/_`
- **Intent Branch:** `I-1795520307-preflight-retry/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-24T12:11:44.000Z`
- **Evaluated Commit SHA:** `d8ca6ea421761cb51c5f66d98a3f4b5effb3dbe9`
- **Base SHA:** `782e456d1961e8406e5915cc96e7c9ae74d66d1a`
- **Merge-Base SHA:** `782e456d1961e8406e5915cc96e7c9ae74d66d1a`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.83`             | `1.00`            | `0.17`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `3.06`             | `0.88`            | `2.18`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `95.00`            | `95.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `15.00`            | `12.75`           | `2.25`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `2.50`             | `2.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `64.18`            | `83.24`           | `19.06`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `64.18`            | `83.24`           | `19.06`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.83 - 1.00| = 0.17$
- **Entropy Error:** $|3.06 - 0.88| = 2.18$
- **Impact Error:** $|95.00 - 95.00| = 0.00$
- **Cost Error:** $|15.00 - 12.75| = 2.25$
- **Learning Value Error:** $|2.50 - 2.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.83 \times 95.0 + 0.5 \times 2.5 - 0.3 \times 3.06 - 15.00 = 64.18$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 95.0 + 0.5 \times 2.5 - 0.3 \times 0.88 - 12.75 = 83.24$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 95.0 + 0.5 \times 2.5 - 0.3 \times 0.88 - 12.75 = 83.24$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +19.06$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `2.8` (3 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795520326-ANA-G4N` executed with minimal error ($p\_success\_error = 0.17$, $cost\_error = 2.25$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `66.7%`
- **Recall (changed files the touch set covered):** `66.7%`

Changed files outside the touch set:

- `apps/sandbox-executor/tests/test_preflight_retries.py`

Declared entries the diff did not touch:

- `apps/sandbox-executor/tests/test_preflight_claude_keychain.py`
