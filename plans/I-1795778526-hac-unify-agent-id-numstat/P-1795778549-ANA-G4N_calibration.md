# Plan Calibration Report: P-1795778549-ANA-G4N

- **Plan Reference:** [`plans/I-1795778526-hac-unify-agent-id-numstat/P-1795778549-ANA-G4N.md`](P-1795778549-ANA-G4N.md)
- **Execution ID:** `E-1795778785-ANA-G4N`
- **Execution Branch:** `I-1795778526-hac-unify-agent-id-numstat/P-1795778549-ANA-G4N/E-1795778785-ANA-G4N/_`
- **Intent Branch:** `I-1795778526-hac-unify-agent-id-numstat/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-27T11:39:52.000Z`
- **Evaluated Commit SHA:** `c06a51b6dec8816ec250585a256de0da5db6dd03`
- **Base SHA:** `00698b28f563c13787b88ad511783a38cffc1af0`
- **Merge-Base SHA:** `00698b28f563c13787b88ad511783a38cffc1af0`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.83`             | `1.00`            | `0.17`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `3.30`             | `0.88`            | `2.42`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `75.00`            | `75.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `8.00`             | `6.80`            | `1.20`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `2.00`             | `2.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `54.26`            | `68.94`           | `14.68`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `54.26`            | `68.94`           | `14.68`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.83 - 1.00| = 0.17$
- **Entropy Error:** $|3.30 - 0.88| = 2.42$
- **Impact Error:** $|75.00 - 75.00| = 0.00$
- **Cost Error:** $|8.00 - 6.80| = 1.20$
- **Learning Value Error:** $|2.00 - 2.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.83 \times 75.0 + 0.5 \times 2.0 - 0.3 \times 3.30 - 8.00 = 54.26$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 75.0 + 0.5 \times 2.0 - 0.3 \times 0.88 - 6.80 = 68.94$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 75.0 + 0.5 \times 2.0 - 0.3 \times 0.88 - 6.80 = 68.94$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +14.68$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `2.8` (7 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795778549-ANA-G4N` executed with minimal error ($p\_success\_error = 0.17$, $cost\_error = 1.20$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `87.5%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- `apps/sandbox-executor/src/sandbox_executor/agent_defaults.py`

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
comment(s) across 12 changed file(s).

- Changed files checked: 12
- Added docstrings: 0
- Added comments (not '# why:'): 0
- Removed docstrings: 0
- Removed comments (not '# why:'): 0
