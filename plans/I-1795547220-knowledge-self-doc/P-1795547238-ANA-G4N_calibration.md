# Plan Calibration Report: P-1795547238-ANA-G4N

- **Plan Reference:** [`plans/I-1795547220-knowledge-self-doc/P-1795547238-ANA-G4N.md`](P-1795547238-ANA-G4N.md)
- **Execution ID:** `E-1795547594-ANA-G4N`
- **Execution Branch:** `I-1795547220-knowledge-self-doc/P-1795547238-ANA-G4N/E-1795547594-ANA-G4N/_`
- **Intent Branch:** `I-1795547220-knowledge-self-doc/_`
- **Evaluating Agent:** `antigravity-agent/gemini-4-neon`
- **Evaluation Timestamp:** `2026-11-24T21:17:52.000Z`
- **Evaluated Commit SHA:** `07239e02f66af13576bceb75473a0d406d25efc4`
- **Base SHA:** `4c82c0f7e8237757876da7ddd02021cbf2c1b093`
- **Merge-Base SHA:** `b8093ff6758d858eafc3f199458ee7a2a052a28b`
- **Merged Commits:** `1`
- **Patch Size Base:** `target_merge_base` (merge-base `b8093ff6758d858eafc3f199458ee7a2a052a28b` with the target
  branch)

---

## 1. Executive Calibration Summary

| Metric                                             | Predicted (`pred`) | Actual (`actual`) | Absolute Error (`abs(pred - actual)`) | Accuracy Rating | Bias Direction             |
| :------------------------------------------------- | :----------------- | :---------------- | :------------------------------------ | :-------------- | :------------------------- |
| **$P(\text{success})$**                            | `0.83`             | `1.00`            | `0.17`                                | Moderate        | Slight Underconfidence     |
| **Entropy ($\Delta S$)**                           | `5.28`             | `2.91`            | `2.37`                                | Moderate        | Overestimated Risk         |
| **Impact**                                         | `65.00`            | `65.00`           | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Cost**                                           | `14.50`            | `12.32`           | `2.18`                                | Moderate        | Conservative Cost Estimate |
| **Learning Value**                                 | `3.00`             | `3.00`            | `0.00`                                | Exact           | Perfectly Calibrated       |
| **Expected Value — Clean (without bugs)**          | `39.37`            | `53.31`           | `13.94`                               | Moderate        | Conservative Underestimate |
| **Expected Value — Observed (with bugs included)** | `39.37`            | `53.31`           | `13.94`                               | Moderate        | Conservative Underestimate |
| **Bug EV Drag**                                    | `0.00`             | `0.00`            | `0.00`                                | -               | Drag Penalty               |

---

## 2. Mathematical Derivations & Calibration Errors

- **Success Probability Error:** $|0.83 - 1.00| = 0.17$
- **Entropy Error:** $|5.28 - 2.91| = 2.37$
- **Impact Error:** $|65.00 - 65.00| = 0.00$
- **Cost Error:** $|14.50 - 12.32| = 2.18$
- **Learning Value Error:** $|3.00 - 3.00| = 0.00$
- **Expected Value Realization:**
  $$EV_{\text{pred}} = 0.83 \times 65.0 + 0.5 \times 3.0 - 0.3 \times 5.28 - 14.50 = 39.37$$
  $$EV_{\text{actual}} = EV_{\text{clean}} = 1.00 \times 65.0 + 0.5 \times 3.0 - 0.3 \times 2.91 - 12.32 = 53.31$$
  $$EV_{\text{with\_bugs}} = 1.00 \times 65.0 + 0.5 \times 3.0 - 0.3 \times 2.91 - 12.32 = 53.31$$
  $$\text{Bug EV Drag} = round(EV_{\text{clean}} - EV_{\text{with\_bugs}}, 2) = 0.00$$ $$\Delta EV = +13.94$$

---

## 3. Entropy Factor Breakdown

- **State Surface Area (SSA):** Predicted `0.6` vs Observed `9.6` (23 files modified).
- **Irreversibility (IRR):** Predicted `0.0` vs Observed `0.0` (Zero destructive or stateful changes).
- **Conflict Likelihood (CL):** Predicted `0.1` vs Observed `0.0` (Clean sequential branch merges).
- **Sandbox Escape Risk (SER):** Predicted `0.0` vs Observed `0.0` (Zero security exceptions).
- **Novelty (NOV):** Predicted `0.4` vs Observed `0.4` (Standard calibration reporting schema).

---

## 4. Calibration Assessment

Plan `P-1795547238-ANA-G4N` executed with minimal error ($p\_success\_error = 0.17$, $cost\_error = 2.18$). The
calibration step successfully completed the execution lifecycle and delivered verified post-execution analysis.

---

## 5. Touch Set Accuracy

- **Precision (declared entries the diff touched):** `100.0%`
- **Recall (changed files the touch set covered):** `47.8%`

Changed files outside the touch set:

- `apps/sandbox-executor/src/sandbox_executor/cli/__init__.py`
- `apps/sandbox-executor/src/sandbox_executor/config_env.py`
- `apps/sandbox-executor/src/sandbox_executor/config_env_report.py`
- `apps/sandbox-executor/src/sandbox_executor/redaction.py`
- `apps/sandbox-executor/tests/flow/conftest.py`
- `apps/sandbox-executor/tests/test_approval_fingerprint.py`
- `apps/sandbox-executor/tests/test_calibration_diff_property.py`
- `apps/sandbox-executor/tests/test_credential_hermeticity.py`
- `apps/sandbox-executor/tests/test_executor_agent_output_redaction.py`
- `apps/sandbox-executor/tests/test_turn_supervisor.py`
- `apps/sandbox-executor/tests/test_url_credential_redaction.py`
- `scripts/pre_commit_check.py`

Declared entries the diff did not touch:

- none
