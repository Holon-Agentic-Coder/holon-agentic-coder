# Plan Calibration Report: P-1795569547-ANA-G4N

- **Plan Reference:** [`plans/I-1795569528-github-ci-self-doc/P-1795569547-ANA-G4N.md`](P-1795569547-ANA-G4N.md)
- **Execution ID:** `E-1795569872-ANA-G4N`
- **Execution Branch:** `I-1795569528-github-ci-self-doc/P-1795569547-ANA-G4N/E-1795569872-ANA-G4N/_`
- **Intent Branch:** `I-1795569528-github-ci-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-25T01:37:53.000Z`
- **Evaluated Commit SHA:** `4834a4b15eba238efd9418b3ade30c857f0241ee`
- **Base SHA:** `71d293b4d53cbb9c68d1d4a560007086e4f20a39`
- **Merge-Base SHA:** `71d293b4d53cbb9c68d1d4a560007086e4f20a39`
- **Merged Commits:** `0`
- **Patch Size Base:** `plan`

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.87`             | `1.00`            | `0.13`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `2.69`             | `0.95`            | `1.74`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `48.00`            | `48.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `9.00`             | `7.65`            | `1.35`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `4.00`             | `4.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `33.95`            | `42.06`           | `8.11`                                | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `33.95`            | `42.06`           | `8.11`                                | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.87 - 1.00| = 0.13$
- **Entropy Error:** $|2.69 - 0.95| = 1.74$
- **Impact Error:** $|48.00 - 48.00| = 0.00$
- **Cost Error:** $|9.00 - 7.65| = 1.35$
- **Learning Value Error:** $|4.00 - 4.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.87 \times 48.0 + 0.5 \times 4.0 - 0.3 \times 2.69 - 9.00 = 33.95$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 48.0 + 0.5 \times 4.0 - 0.3 \times 0.95 - 7.65 = 42.06$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 48.0 + 0.5 \times 4.0 - 0.3 \times 0.95 - 7.65 = 42.06$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +8.11$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `3.0` (5 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795569547-ANA-G4N` executed with minimal error ($p\_success\_error = 0.13$, $cost\_error = 1.35$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `100.0%`

Changed files outside the touch set:

- none

Declared entries the diff did not touch:

- none
