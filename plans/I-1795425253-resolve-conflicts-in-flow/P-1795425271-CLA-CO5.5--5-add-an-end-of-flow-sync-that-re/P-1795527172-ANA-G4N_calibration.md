# Plan Calibration Report: P-1795527172-ANA-G4N

- **Plan Reference:**
  [`plans/I-1795425253-resolve-conflicts-in-flow/P-1795425271-CLA-CO5.5--5-add-an-end-of-flow-sync-that-re/P-1795527172-ANA-G4N.md`](P-1795527172-ANA-G4N.md)
- **Execution ID:** `E-1795527453-ANA-G4N`
- **Execution Branch:**
  `I-1795425253-resolve-conflicts-in-flow/P-1795425271-CLA-CO5.5--5-add-an-end-of-flow-sync-that-re/P-1795527172-ANA-G4N/E-1795527453-ANA-G4N/_`
- **Intent Branch:** `I-1795425253-resolve-conflicts-in-flow/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-24T15:06:34.000Z`
- **Evaluated Commit SHA:** `0e9357f417b3ffb43f6dc3ce8f7b0b87e0a8e81e`
- **Base SHA:** `058f994e077459244e8120219471fe57ba865e51`
- **Merge-Base SHA:** `058f994e077459244e8120219471fe57ba865e51`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.71`             | `1.00`            | `0.29`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `6.00`             | `1.87`            | `4.13`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `80.00`            | `80.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `11.00`            | `9.35`            | `1.65`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.00`             | `4.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `46.00`            | `72.09`           | `26.09`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `46.00`            | `72.09`           | `26.09`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.71 - 1.00| = 0.29$
- **Entropy Error:** $|6.00 - 1.87| = 4.13$
- **Impact Error:** $|80.00 - 80.00| = 0.00$
- **Cost Error:** $|11.00 - 9.35| = 1.65$
- **Learning Value Error:** $|4.00 - 4.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.71 \times 80.0 + 0.5 \times 4.0 - 0.3 \times 6.00 - 11.00 = 46.00$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 80.0 + 0.5 \times 4.0 - 0.3 \times 1.87 - 9.35 = 72.09$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 80.0 + 0.5 \times 4.0 - 0.3 \times 1.87 - 9.35 = 72.09$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +26.09$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `6.1` (12 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795527172-ANA-G4N` executed with minimal error ($p\_success\_error = 0.29$, $cost\_error = 1.65$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `66.7%`

Changed files outside the touch set:

- `apps/sandbox-executor/src/sandbox_executor/flow/target_probe.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/target_sync.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/target_sync_hooks.py`
- `apps/sandbox-executor/tests/flow/review/test_sync_points.py`

Declared entries the diff did not touch:

- none
