# Plan Calibration Report: P-1795561377-ANA-G4N

- **Plan Reference:** [`plans/I-1795561357-local-llm-self-doc/P-1795561377-ANA-G4N.md`](P-1795561377-ANA-G4N.md)
- **Execution ID:** `E-1795562229-ANA-G4N`
- **Execution Branch:** `I-1795561357-local-llm-self-doc/P-1795561377-ANA-G4N/E-1795562229-ANA-G4N/_`
- **Intent Branch:** `I-1795561357-local-llm-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-24T23:33:40.000Z`
- **Evaluated Commit SHA:** `d7838b5b0222291deb88ce47bde004cebb358d15`
- **Base SHA:** `acbf790ea691cf89a53dd8f986eeb01a1855386b`
- **Merge-Base SHA:** `acbf790ea691cf89a53dd8f986eeb01a1855386b`
- **Merged Commits:** `1`
- **Patch Size Base:** `target_merge_base` (merge-base `acbf790ea691cf89a53dd8f986eeb01a1855386b` with the target
  branch)

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.87`             | `1.00`            | `0.13`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `2.70`             | `0.99`            | `1.71`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `80.00`            | `80.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `9.00`             | `7.65`            | `1.35`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `2.50`             | `2.50`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `61.04`            | `73.30`           | `12.26`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `61.04`            | `73.30`           | `12.26`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.87 - 1.00| = 0.13$
- **Entropy Error:** $|2.70 - 0.99| = 1.71$
- **Impact Error:** $|80.00 - 80.00| = 0.00$
- **Cost Error:** $|9.00 - 7.65| = 1.35$
- **Learning Value Error:** $|2.50 - 2.50| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.87 \times 80.0 + 0.5 \times 2.5 - 0.3 \times 2.70 - 9.00 = 61.04$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 80.0 + 0.5 \times 2.5 - 0.3 \times 0.99 - 7.65 = 73.30$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 80.0 + 0.5 \times 2.5 - 0.3 \times 0.99 - 7.65 = 73.30$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +12.26$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `3.2` (2 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795561377-ANA-G4N` executed with minimal error ($p\_success\_error = 0.13$, $cost\_error = 1.35$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none
