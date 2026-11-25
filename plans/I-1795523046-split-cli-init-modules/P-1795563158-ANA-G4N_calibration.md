# Plan Calibration Report: P-1795563158-ANA-G4N

- **Plan Reference:** [`plans/I-1795523046-split-cli-init-modules/P-1795563158-ANA-G4N.md`](P-1795563158-ANA-G4N.md)
- **Execution ID:** `E-1795563737-ANA-G4N`
- **Execution Branch:** `I-1795523046-split-cli-init-modules/P-1795563158-ANA-G4N/E-1795563737-ANA-G4N/_`
- **Intent Branch:** `I-1795523046-split-cli-init-modules/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-25T00:44:10.000Z`
- **Evaluated Commit SHA:** `c2f861c4e105060f3ee75d76d89d51e41af93a8b`
- **Base SHA:** `0c9335197799122a18093c70e174926bc3e23d0b`
- **Merge-Base SHA:** `0c9335197799122a18093c70e174926bc3e23d0b`
- **Merged Commits:** `4`
- **Patch Size Base:** `target_merge_base` (merge-base `0c9335197799122a18093c70e174926bc3e23d0b` with the target
  branch)

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.84`             | `1.00`            | `0.16`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `9.03`             | `3.04`            | `5.99`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `75.00`            | `75.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `30.00`            | `33.00`           | `3.00`                                | Moderate        | Underestimated Cost        |
| **Learning Value**                                 | `4.00`             | `4.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `32.29`            | `43.09`           | `10.80`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `32.29`            | `43.09`           | `10.80`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.84 - 1.00| = 0.16$
- **Entropy Error:** $|9.03 - 3.04| = 5.99$
- **Impact Error:** $|75.00 - 75.00| = 0.00$
- **Cost Error:** $|30.00 - 33.00| = 3.00$
- **Learning Value Error:** $|4.00 - 4.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.84 \times 75.0 + 0.5 \times 4.0 - 0.3 \times 9.03 - 30.00 = 32.29$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 75.0 + 0.5 \times 4.0 - 0.3 \times 3.04 - 33.00 = 43.09$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 75.0 + 0.5 \times 4.0 - 0.3 \times 3.04 - 33.00 = 43.09$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +10.80$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `10.0` (27 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795563158-ANA-G4N` executed with minimal error ($p\_success\_error = 0.16$, $cost\_error = 3.00$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `92.6%`

Changed files outside the touch set:

- `apps/sandbox-executor/tests/doctor_replay.py`
- `apps/sandbox-executor/tests/test_doctor_replay.py`

Declared entries the diff did not touch:

- none
