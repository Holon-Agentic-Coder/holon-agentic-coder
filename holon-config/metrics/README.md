# Metrics Configuration Directory

This directory contains externalized configuration files defining the parameters, weights, and coefficients for Holon's
config-driven physics and metrics calculations (Expected Value, per-intent entropy, and system-level entropy).

For full details on the mathematical formulas, semantic definitions, and calibration procedures, refer to
[`docs/metrics.md`](../../docs/metrics.md).

## Configuration Files Overview

### 1. `ev_config.json`

Defines the system-wide Expected Value (EV) calculation penalty and learning weight constants:

- **`lambda`** (`float`): System-wide entropy penalty coefficient ($\lambda = 0.3$). Penalizes intent plans that
  introduce high disorder or complexity.
- **`mu`** (`float`): System-wide learning value weight coefficient ($\mu = 0.5$). Rewards intent plans that deliver
  epistemic gain to the system.
- **`description`** (`string`): Brief summary of the configuration purpose.

Formula reference:

$$EV = P(\text{success}) \cdot \text{Impact} + \mu \cdot \text{LearningValue} - \lambda \cdot \Delta S_{\text{intent}} - \text{Cost}$$

---

### 2. `entropy_config.json`

Defines the per-intent entropy risk and complexity weight factors:

> [!NOTE] Reserved for future entropy computation integration. Not yet loaded by the runtime.

Fields:

- **`weights`**: Weight factors ($w_1 \dots w_5$) for predicted per-intent entropy ($\Delta S_{\text{intent,pred}}$):
  - `w1_ssa` (0.30): State Surface Area (LOC / files changed).
  - `w2_irr` (0.25): Irreversibility (migrations / destructive ops).
  - `w3_cl` (0.20): Conflict Likelihood (rebase/merge conflict probability).
  - `w4_ser` (0.15): Sandbox Escape Risk (security policy violation probability).
  - `w5_nov` (0.10): Novelty (unfamiliarity relative to knowledge base).
- **`observable_weights`**: Weight factors ($u_1 \dots u_5$) for actual observed post-execution entropy ($\Delta
  S_{\text{intent,actual}}$).
- **`description`** (`string`): Description of the per-intent entropy weight settings.

---

### 3. `system_entropy_config.json`

Defines system-level entropy coefficients across the repository and agent swarm:

> [!NOTE] Reserved for future entropy computation integration. Not yet loaded by the runtime.

Fields:

- **`coefficients`**: Coefficients ($\alpha, \beta, \gamma, \delta, \epsilon$) for calculating system-wide entropy
  ($S_{\text{system}}$):
  - `alpha_bd` (1.0): Branch Divergence coefficient.
  - `beta_kf` (1.0): Knowledge Fragmentation coefficient.
  - `gamma_cd` (1.0): Calibration Drift coefficient.
  - `delta_atv` (1.0): Agent Trust Variance coefficient.
  - `epsilon_uc` (1.0): Unresolved Conflicts coefficient.
- **`calibration_status`** (`string`): Current status of weight calibration (e.g., `uncalibrated_initial_defaults`).
- **`description`** (`string`): Description of the system-level entropy coefficients.
