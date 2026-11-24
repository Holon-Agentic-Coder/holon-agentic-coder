# Plan Calibration Report: P-1795496340-ANA-G4A

- **Plan Reference:** [`plans/I-1795496321-flow-e2e-test-teardown/P-1795496340-ANA-G4A.md`](P-1795496340-ANA-G4A.md)
- **Execution ID:** `E-1795497446-ANA-G4A`
- **Execution Branch:** `I-1795496321-flow-e2e-test-teardown/P-1795496340-ANA-G4A/E-1795497446-ANA-G4A/_`
- **Intent Branch:** `I-1795496321-flow-e2e-test-teardown/_`
- **Evaluating Agent:** `antigravity-agent/gemini-3.8-flash-medium`
- **Evaluation Timestamp:** `2026-11-24T11:14:32.000Z`
- **Evaluated Commit SHA:** `89f2911082530be0d1daf503b7392c94bd9962e4`
- **Base SHA:** `bb71d60cb671353c1dd9ebfd38c9c009591a2c73`
- **Merge-Base SHA:** `bb71d60cb671353c1dd9ebfd38c9c009591a2c73`
- **Merged Commits:** `3`
- **Patch Size Base:** `target_merge_base` (merge-base `bb71d60cb671353c1dd9ebfd38c9c009591a2c73` with the target
  branch)

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.84`             | `1.00`            | `0.16`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `8.60`             | `3.04`            | `5.56`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `84.00`            | `84.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `9.80`             | `8.33`            | `1.47`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `6.50`             | `6.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `61.43`            | `78.01`           | `16.58`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `61.43`            | `78.01`           | `16.58`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.84 - 1.00| = 0.16$
- **Entropy Error:** $|8.60 - 3.04| = 5.56$
- **Impact Error:** $|84.00 - 84.00| = 0.00$
- **Cost Error:** $|9.80 - 8.33| = 1.47$
- **Learning Value Error:** $|6.50 - 6.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.84 \times 84.0 + 0.5 \times 6.5 - 0.3 \times 8.60 - 9.80 = 61.43$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 84.0 + 0.5 \times 6.5 - 0.3 \times 3.04 - 8.33 = 78.01$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 84.0 + 0.5 \times 6.5 - 0.3 \times 3.04 - 8.33 = 78.01$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +16.58$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `10.0` (9 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795496340-ANA-G4A` executed with minimal error ($p\_success\_error = 0.16$, $cost\_error = 1.47$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `66.7%`

Changed files outside the touch set:

- `apps/sandbox-executor/src/sandbox_executor/e2e_guards.py`
- `apps/sandbox-executor/src/sandbox_executor/e2e_teardown.py`
- `apps/sandbox-executor/src/sandbox_executor/e2e_types.py`

Declared entries the diff did not touch:

- none
