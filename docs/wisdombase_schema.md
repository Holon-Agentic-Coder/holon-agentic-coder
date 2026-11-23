# wb_schema.md

This document defines the **Wisdom Base (WB)** schema for Holon. The WB is the apex of the cognitive hierarchy—a
**highly curated, meta-evolving store** of universal invariants, safety axioms, and global engineering heuristics that
hold true across all project worlds.

The WB enables:

- **Zero-shot project bootstrapping** (agents start with global engineering "wisdom").
- **Cross-project learning** (failures in Project A improve success in Project B).
- **Axiomatic governance** (immutable safety and reasoning principles).

---

## Core principles

### 1) Axiomatic and Universal

- WB entries are **cross-project invariants**. They represent truths that do not change regardless of the local project
  "physics".
- If a local KB entry contradicts a WB invariant, the **WB takes precedence** for safety and core governance.

### 2) Meta-Evolution (Slow and Steady)

- The WB evolves through **Ascension**. Knowledge is only promoted to the WB after it has been proven effective across
  multiple unrelated project ledgers.
- Evolution is significantly slower than the local KB to ensure the stability of the system's "Constitutional Brain".

### 3) Multi-World Validation

- Promotion to WB requires **cross-project backtesting**.
- Evidence must be aggregated from $N$ different project environments (default $N \ge 3$).

---

## Storage format

### Recommended format: Structured JSON files with a derived index

```
holon-knowledge/wb/
├── invariants/
│   ├── safety_axioms.json
│   ├── git_discipline.json
│   └── reasoning_patterns.json
├── meta-physics/
│   ├── ev_formula_v3.json
│   ├── entropy_theory.json
│   └── calibration_heuristics.json
├── heuristics/
│   ├── zero_shot_priors.json
│   └── impact_baselines.json
└── meta.json  # WB-level versioning and metadata
```

No `index.json` is stored or committed; the former `heuristics/index.json` path is git-ignored. The index is derived on
demand by `sandbox_executor.knowledge.wisdom.load_index(subfolder)`, which globs the `WB-*.json` entry files of the
subfolder and returns `wb_id`, `file`, `wb_type`, `status` and `created_ts` for each, sorted by `created_ts` then
`wb_id`. Writing a rule writes only its own entry file.

---

## Common envelope (WB entries)

Every WB entry MUST contain:

- `wb_id` (string, unique)  
  Example: `"WB-invariant-safety-001"`
- `wb_type` (string)  
  Example: `"invariant"`, `"meta-physics"`, `"heuristic"`
- `version` (string)
- `created_ts` (string, ISO-8601 UTC)
- `scope` (string) — Always `"global"`
- `status` (string) — `"active"`, `"deprecated"`, `"experimental"`
- `ascension_evidence` (object)
  - `project_count` (integer) — Number of unique projects where this was validated.
  - `total_executions` (integer) — Aggregated success count across all projects.
  - `global_improvement` (number) — Delta in EV or calibration accuracy measured globally.
  - `provenance` (array of objects) — Forensic links to source KB entries.
    - `project_id` (string) — The unique ID of the project world.
    - `kb_id` (string) — The source ID in the local Knowledge Base.
    - `kb_version` (string) — The specific version of the local pattern.
- `human_approved` (boolean) — Requires high-level architectural approval.

---

## WB entry types

### 1) Invariant (The Constitutional Layer)

**Definition:** Immutable laws governing behaviour, safety, and reasoning.

**Schema Example:**

```json
{
  "wb_id": "WB-invariant-git-001",
  "wb_type": "invariant",
  "payload": {
    "name": "Rebase-Before-Merge Invariant",
    "description": "A branch must be rebased against its parent immediately prior to merge to ensure the forensic integrity of the ledger.",
    "enforcement": "Strict",
    "violation_penalty": 0.5
  }
}
```

### 2) Meta-Physics (The Decision Engine)

**Definition:** The fundamental formulas and constants used for Expected Value ($EV$) calculation and entropy
management.

**Schema Example:**

```json
{
  "wb_id": "WB-physics-ev-formula",
  "wb_type": "meta-physics",
  "payload": {
    "formula": "P(success) * Impact + mu * LearningValue - lambda * Entropy - Cost",
    "mu_default": 0.5,
    "lambda_default": 0.3,
    "rationale": "Balances exploration (LearningValue) against risk (Entropy) using universal coefficients."
  }
}
```

### 3) Heuristic (Zero-Shot Priors)

**Definition:** Global priors for agents entering new project worlds with empty Knowledge Bases.

---

## Ascension process (WB Evolution)

1. **Local Provenance:** A pattern is identified as highly successful in a project-specific KB.
2. **Meta-Curation:** A **Researcher Agent** identifies the pattern's potential for universal application.
3. **Cross-Project Validation:** The pattern is backtested against the ledgers of at least 3 unrelated projects.
4. **Axiomatic Promotion:** If global ROI or calibration accuracy improves, the entry "ascends" to the Wisdom Base.
5. **Broadcast:** All Holon instances receive the updated WB version as their new baseline priors.

---

## Governance and Change Management

The stability of the Holon engine relies on strict boundaries between agent-driven execution and human-driven
governance. Changes to the core "physics", schemas, or system invariants follow a formal change management process.

### 1) Core Principles & "Physics" Updates

The coefficients ($\lambda$, $\mu$, weights) and formulas (Expected Value, Entropy) are defined globally in
`holon-config/metrics/` and the Wisdom Base.

- **Who can propose:** Only agents with **Highest** trust or human operators can propose changes to these parameters.
- **Backtest Validation:** Any proposed change must be accompanied by a backtest report run against historical Ledger
  data (minimum 200 runs) showing a measurable increase in global utility or a decrease in average calibration error.
- **Human Approval Gate:** System-wide config changes cannot be automatically committed. They are proposed as a standard
  git PR against the canonical repository, requiring mandatory human review and manual activation.

### 2) Schema Evolution and Versioning

The structure of the data products (Ledger event schemas, KB/WB schemas) is versioned in `meta.json`:

- **Minor Changes:** Non-breaking schema additions (e.g. adding metadata fields) are versioned via minor bumps (e.g.
  `1.1` to `1.2`) and automatically propagated to sandboxes.
- **Major Changes:** Breaking changes (e.g. removing fields, renaming metrics, restructuring the git flow) bump the
  major version. Agents and orchestrators must be updated to support the new major version before it can be activated in
  the workspace.

### 3) Handling Legacy Work and Deprecation

When breaking changes or core parameter updates are merged into the canonical repository:

- **Legacy Sandboxes:** Active running sub-intents running on legacy branches continue their execution in containers
  pre-loaded with the older compatible schema and metric weights.
- **Stale Branches:** If a branch cannot be rebased due to incompatible schema version differences, it is marked as
  `discarded` or `deprecated` in the Ledger. The orchestrator prunes the branch and prompts the operator or high-trust
  agent to re-propose the intent using the new system standards.

---

## Related documents

- [`knowledgebase_schema.md`](knowledgebase_schema.md) — Local project memory
- [`ledger_schema.md`](ledger_schema.md) — Source of evidence for ascension
- [`architecture.md`](architecture.md) — How the WB fits into the Stateless Engine
- [`appendix.md`](appendix.md) — Reference Glossary for formulas and metrics
