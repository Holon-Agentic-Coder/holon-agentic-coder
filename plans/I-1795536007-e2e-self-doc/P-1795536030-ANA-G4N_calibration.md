# Plan Calibration Report: P-1795536030-ANA-G4N

- **Plan Reference:** [`plans/I-1795536007-e2e-self-doc/P-1795536030-ANA-G4N.md`](P-1795536030-ANA-G4N.md)
- **Execution ID:** `E-1795536281-ANA-G4N`
- **Execution Branch:** `I-1795536007-e2e-self-doc/P-1795536030-ANA-G4N/E-1795536281-ANA-G4N/_`
- **Intent Branch:** `I-1795536007-e2e-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-24T16:59:28.000Z`
- **Evaluated Commit SHA:** `3e6629da753c8c76d97749ff549d41e4d4bd3915`
- **Base SHA:** `2c6e56fedf163d70fe1ecb3b1c5f4ea585f77a5c`
- **Merge-Base SHA:** `2c6e56fedf163d70fe1ecb3b1c5f4ea585f77a5c`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.85`             | `1.00`            | `0.15`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `2.74`             | `1.33`            | `1.41`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `55.00`            | `55.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `4.60`             | `3.91`            | `0.69`                                | High (≤ 1.0)    | Highly Accurate            |
| **Learning Value**                                 | `2.50`             | `2.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `42.58`            | `51.94`           | `9.36`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `42.58`            | `51.94`           | `9.36`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.85 - 1.00| = 0.15$
- **Entropy Error:** $|2.74 - 1.33| = 1.41$
- **Impact Error:** $|55.00 - 55.00| = 0.00$
- **Cost Error:** $|4.60 - 3.91| = 0.69$
- **Learning Value Error:** $|2.50 - 2.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.85 \times 55.0 + 0.5 \times 2.5 - 0.3 \times 2.74 - 4.60 = 42.58$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 55.0 + 0.5 \times 2.5 - 0.3 \times 1.33 - 3.91 = 51.94$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 55.0 + 0.5 \times 2.5 - 0.3 \times 1.33 - 3.91 = 51.94$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +9.36$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `4.3` (7 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795536030-ANA-G4N` executed with minimal error ($p\_success\_error = 0.15$, $cost\_error = 0.69$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `71.4%`

Changed files outside the touch set:

- `.github/workflows/test-stress.yml`
- `apps/sandbox-executor/tests/test_ci_durations.py`

Declared entries the diff did not touch:

- none
