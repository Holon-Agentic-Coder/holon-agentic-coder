# Plan Calibration Report: P-1795563598-ANA-G4N

- **Plan Reference:** [`plans/I-1795563579-claude-auth-self-doc/P-1795563598-ANA-G4N.md`](P-1795563598-ANA-G4N.md)
- **Execution ID:** `E-1795563907-ANA-G4N`
- **Execution Branch:** `I-1795563579-claude-auth-self-doc/P-1795563598-ANA-G4N/E-1795563907-ANA-G4N/_`
- **Intent Branch:** `I-1795563579-claude-auth-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-25T00:11:15.000Z`
- **Evaluated Commit SHA:** `117688b1bd915d2aa9869f22ea4ee1263390091c`
- **Base SHA:** `e1d1ae37b119fd11eae50dc588a37e8a467aaef0`
- **Merge-Base SHA:** `e1d1ae37b119fd11eae50dc588a37e8a467aaef0`
- **Merged Commits:** `1`
- **Patch Size Base:** `target_merge_base` (merge-base `e1d1ae37b119fd11eae50dc588a37e8a467aaef0` with the target
  branch)

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.84`             | `1.00`            | `0.16`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `3.21`             | `0.76`            | `2.45`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `60.00`            | `60.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `9.00`             | `7.65`            | `1.35`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `2.00`             | `2.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `41.44`            | `53.12`           | `11.68`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `41.44`            | `53.12`           | `11.68`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.84 - 1.00| = 0.16$
- **Entropy Error:** $|3.21 - 0.76| = 2.45$
- **Impact Error:** $|60.00 - 60.00| = 0.00$
- **Cost Error:** $|9.00 - 7.65| = 1.35$
- **Learning Value Error:** $|2.00 - 2.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.84 \times 60.0 + 0.5 \times 2.0 - 0.3 \times 3.21 - 9.00 = 41.44$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 60.0 + 0.5 \times 2.0 - 0.3 \times 0.76 - 7.65 = 53.12$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 60.0 + 0.5 \times 2.0 - 0.3 \times 0.76 - 7.65 = 53.12$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +11.68$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `2.4` (4 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795563598-ANA-G4N` executed with minimal error ($p\_success\_error = 0.16$, $cost\_error = 1.35$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none
