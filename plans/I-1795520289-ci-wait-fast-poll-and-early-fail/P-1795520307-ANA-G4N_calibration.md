# Plan Calibration Report: P-1795520307-ANA-G4N

- **Plan Reference:**
  [`plans/I-1795520289-ci-wait-fast-poll-and-early-fail/P-1795520307-ANA-G4N.md`](P-1795520307-ANA-G4N.md)
- **Execution ID:** `E-1795525965-ANA-G4N`
- **Execution Branch:** `I-1795520289-ci-wait-fast-poll-and-early-fail/P-1795520307-ANA-G4N/E-1795525965-ANA-G4N/_`
- **Intent Branch:** `I-1795520289-ci-wait-fast-poll-and-early-fail/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-24T14:03:16.000Z`
- **Evaluated Commit SHA:** `be6430ad2ec87dfd64c813247b35c829511bb325`
- **Base SHA:** `9118cea4a5895584a8b45475d67b98ea2276ebd1`
- **Merge-Base SHA:** `9118cea4a5895584a8b45475d67b98ea2276ebd1`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.87`             | `1.00`            | `0.13`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `2.80`             | `0.72`            | `2.08`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `60.00`            | `60.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `12.00`            | `10.20`           | `1.80`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `2.50`             | `2.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `40.61`            | `50.83`           | `10.22`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `40.61`            | `50.83`           | `10.22`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.87 - 1.00| = 0.13$
- **Entropy Error:** $|2.80 - 0.72| = 2.08$
- **Impact Error:** $|60.00 - 60.00| = 0.00$
- **Cost Error:** $|12.00 - 10.20| = 1.80$
- **Learning Value Error:** $|2.50 - 2.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.87 \times 60.0 + 0.5 \times 2.5 - 0.3 \times 2.80 - 12.00 = 40.61$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 60.0 + 0.5 \times 2.5 - 0.3 \times 0.72 - 10.20 = 50.83$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 60.0 + 0.5 \times 2.5 - 0.3 \times 0.72 - 10.20 = 50.83$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +10.22$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `2.3` (6 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795520307-ANA-G4N` executed with minimal error ($p\_success\_error = 0.13$, $cost\_error = 1.80$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `83.3%`

Changed files outside the touch set:

- `apps/sandbox-executor/tests/test_merged_head_ci_gate.py`

Declared entries the diff did not touch:

- none
