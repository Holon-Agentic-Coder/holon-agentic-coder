# Plan Calibration Report: P-1795551260-ANA-G4N

- **Plan Reference:** [`plans/I-1795551241-config-env-self-doc/P-1795551260-ANA-G4N.md`](P-1795551260-ANA-G4N.md)
- **Execution ID:** `E-1795551519-ANA-G4N`
- **Execution Branch:** `I-1795551241-config-env-self-doc/P-1795551260-ANA-G4N/E-1795551519-ANA-G4N/_`
- **Intent Branch:** `I-1795551241-config-env-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-24T23:06:17.000Z`
- **Evaluated Commit SHA:** `3f350112ca304cd7b0fdc1f7c71aaf594e57b603`
- **Base SHA:** `4c82c0f7e8237757876da7ddd02021cbf2c1b093`
- **Merge-Base SHA:** `4c82c0f7e8237757876da7ddd02021cbf2c1b093`
- **Merged Commits:** `2`
- **Patch Size Base:** `target_merge_base` (merge-base `4c82c0f7e8237757876da7ddd02021cbf2c1b093` with the target
  branch)

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.85`             | `1.00`            | `0.15`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `3.80`             | `1.20`            | `2.60`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `65.00`            | `65.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `10.00`            | `8.50`            | `1.50`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `3.00`             | `3.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `45.61`            | `57.64`           | `12.03`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `45.61`            | `57.64`           | `12.03`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.85 - 1.00| = 0.15$
- **Entropy Error:** $|3.80 - 1.20| = 2.60$
- **Impact Error:** $|65.00 - 65.00| = 0.00$
- **Cost Error:** $|10.00 - 8.50| = 1.50$
- **Learning Value Error:** $|3.00 - 3.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.85 \times 65.0 + 0.5 \times 3.0 - 0.3 \times 3.80 - 10.00 = 45.61$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 65.0 + 0.5 \times 3.0 - 0.3 \times 1.20 - 8.50 = 57.64$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 65.0 + 0.5 \times 3.0 - 0.3 \times 1.20 - 8.50 = 57.64$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +12.03$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `3.9` (10 files modified).
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
- **Recall (changed files the touch set covered):** `60.0%`

Changed files outside the touch set:

- `apps/sandbox-executor/src/sandbox_executor/cli/__init__.py`
- `apps/sandbox-executor/src/sandbox_executor/config_env_report.py`
- `apps/sandbox-executor/src/sandbox_executor/redaction.py`
- `apps/sandbox-executor/tests/test_executor_agent_output_redaction.py`

Declared entries the diff did not touch:

- none
