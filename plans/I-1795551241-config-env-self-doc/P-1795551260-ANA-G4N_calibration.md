# Plan Calibration Report: P-1795551260-ANA-G4N

- **Plan Reference:** [`plans/I-1795551241-config-env-self-doc/P-1795551260-ANA-G4N.md`](P-1795551260-ANA-G4N.md)
- **Execution ID:** `E-1795551519-ANA-G4N`
- **Execution Branch:** `I-1795551241-config-env-self-doc/P-1795551260-ANA-G4N/E-1795551519-ANA-G4N/_`
- **Intent Branch:** `I-1795551241-config-env-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-25T00:11:34.000Z`
- **Evaluated Commit SHA:** `d81830d2c5369dfbc83d27668d4147dfcf9468c4`
- **Base SHA:** `e1d1ae37b119fd11eae50dc588a37e8a467aaef0`
- **Merge-Base SHA:** `e1d1ae37b119fd11eae50dc588a37e8a467aaef0`
- **Merged Commits:** `4`
- **Patch Size Base:** `target_merge_base` (merge-base `e1d1ae37b119fd11eae50dc588a37e8a467aaef0` with the target
  branch)

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.85`             | `1.00`            | `0.15`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `3.80`             | `1.10`            | `2.70`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `65.00`            | `65.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `10.00`            | `8.50`            | `1.50`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `3.00`             | `3.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `45.61`            | `57.67`           | `12.06`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `45.61`            | `57.67`           | `12.06`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.85 - 1.00| = 0.15$
- **Entropy Error:** $|3.80 - 1.10| = 2.70$
- **Impact Error:** $|65.00 - 65.00| = 0.00$
- **Cost Error:** $|10.00 - 8.50| = 1.50$
- **Learning Value Error:** $|3.00 - 3.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.85 \times 65.0 + 0.5 \times 3.0 - 0.3 \times 3.80 - 10.00 = 45.61$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 65.0 + 0.5 \times 3.0 - 0.3 \times 1.10 - 8.50 = 57.67$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 65.0 + 0.5 \times 3.0 - 0.3 \times 1.10 - 8.50 = 57.67$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +12.06$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `3.5` (8 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795551260-ANA-G4N` executed with minimal error ($p\_success\_error = 0.15$, $cost\_error = 1.50$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `75.0%`

Changed files outside the touch set:

- `apps/sandbox-executor/src/sandbox_executor/redaction.py`
- `apps/sandbox-executor/tests/test_redaction.py`

Declared entries the diff did not touch:

- none
