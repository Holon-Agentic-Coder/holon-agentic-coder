# FAQ: Architectural Clarifications

This document clarifies the core design decisions for the Autonomous Agentic Coder system, explaining how the
architecture addresses common concerns regarding industry standards and operational efficiency.

## 1. How does the system handle LLM context window limits?

The architecture uses a **Just-In-Time (JIT) Context Assembly** strategy. Instead of dumping the entire ledger or
codebase into the prompt, we use a "State-On-Demand" model.

- **Ledger Projections:** Agents only receive ledger entries relevant to their specific task type (e.g., a Coding Agent
  only sees the Plan and File Discovery results).
- **Pull-based Exploration:** No codebase context is provided upfront. Agents must use tools to explore and "pull"
  specific code snippets into their context as needed.

## 2. Why use a simple file-based Knowledge Base instead of a Vector Database?

We use a **Precision-First Retrieval** strategy. The system relies on simple, fast tools like `grep` or `ripgrep` to
find exact matches for symbols, tags, or specific terms.

- **Grep-First Approach:** This ensures 100% precision for technical queries. If an agent needs to find where a function
  is defined or a project rule is documented, a literal search is often more reliable than a vector search that might
  return a "similar" but irrelevant result.
- **Upgrade Path:** If performance becomes a bottleneck, we can introduce a **Hybrid Retrieval** layer. This would use
  semantic search to narrow down a large corpus to a smaller set of candidates, followed by exact ( `grep`-based) search
  within that set to ensure technical accuracy.

## 3. Why isn't the system built on the Model Context Protocol (MCP)?

We avoid "protocol lock-in." The core architecture remains lean and agnostic. MCP is treated as a **capability of the
agent**, not a foundation of the system. If an agent needs a tool available via MCP, the agent manages that connection
itself.

## 4. How do agents understand code structure without AST parsing?

Structural understanding (AST, dependency graphs) is an **evolvable trait**. We do not hardcode these tools into the
base system. Agents that acquire or develop AST capabilities will naturally outperform those that don't, succeeding
under evolutionary selection pressure.

## 5. Why use a rigid "Plan-Execute-Measure" loop instead of iterative debugging?

The execution loop is a diagnostic tool for the Planner. If an agent is stuck in a long "fix-it" loop, it indicates the
intent was poorly decomposed. By capturing this struggle in the ledger, we can objectively penalise the Planner, forcing
the evolution of better, more granular planning.

## 6. Does the JSONL Ledger suffer from concurrency issues?

No. We use **Git as the Transaction Manager**. Each agent operates on its own branch with its own local ledger. The "
rebase at start, rebase at end" rule ensures that state is reconciled before merging into the deciding node's branch (in
practice the plan branch; the root intent alone merges to `main`, after human review). This provides isolation and
atomic commits without a traditional database.

## 7. Why use fixed model tiers instead of dynamic routing?

Fixed models are required for **reproducible evolution**. By treating the Model ID as a fixed variable in an agent's
configuration, we can accurately measure whether improvements come from the agent's logic or the underlying model.
Useless or inefficient model-agent pairings are then pruned by selection pressure.

## 8. Isn't competitive planning too expensive?

Cost is an evolutionary pressure. To manage this, the system prioritises **smaller, faster models** for rapid feedback
loops. Massive LLMs are a last resort. Agents that achieve successful outcomes using cheaper resources earn a higher
fitness score, naturally driving the system toward cost-efficiency.

## 9. Why use the term "Entropy" instead of "Risk Score"?

Entropy is a relative measure of system disorder within a specific **Project World**. A score of 10 might be "safe" in a
complex legacy world but "unstable" in a simple script. The system must learn to calibrate these thresholds for each
unique environment.

## 10. Why force all communication through the Ledger?

There is no "fast path." Every interaction, including simple clarifying questions, must be recorded to ensure the
**Evolutionary Record** is 100% traceable and reproducible. We prioritise the integrity of the system's "DNA" over
micro-optimisations in performance.

## 11. Why is the documentation so extensive for an MVP?

The documentation defines the **Architectural North Star**, not a delivery roadmap. Its purpose is to communicate the
full design integrity, recursion, git-flow, and evolution, ensuring the core thesis is preserved regardless of the
implementation phase.

## 12. How does the system handle branch bloat from fractal recursion?

Modern Git implementations can handle hundreds of thousands of branches with minimal performance degradation. The
branch-per-intent strategy provides a direct, structural mapping between the Git tree and the Ledger, which is more
valuable for traceability than the minor overhead of managing many branches.

## 13. How are financial "blowouts" managed during the bootstrap phase?

The initial evolution process for a new "World Type" will be expensive and slow. While we have not yet designed a
specific **Cost Circuit Breaker** mechanism, we acknowledge this as a critical area for future development. Once a world
(like Python) is bootstrapped and stable agents have evolved, the cost per intent drops significantly. For new worlds,
the initial high cost is a necessary investment in the system's education.

## 14. How does the system handle operational edge cases?

### Large Projects and Monorepos

- **Scoped State Space:** For monorepos, State Surface Area (SSA) calculation and file discovery tools are scoped
  strictly to the sub-project directory.
- **Sandbox Path Restrictions:** Sandboxes are mounted with read-only access to the monorepo root and read-write access
  restricted to the targeted module.
- **Sub-Tree Rebasing:** Git flow conflict checking (Conflict Likelihood) is optimized by ignoring commits in unrelated
  sub-folders, preventing false conflict warnings.

### Very Long-Running Intents or Workflows

- **Mandatory Checkpointing:** Intents expected to take a long time are broken down into child intents representing
  logical milestones.
- **Active Rebase Pulses:** If an execution branch remains open for a long period, the orchestrator periodically pushes
  a background rebase onto the branch it was cut from (for a sub-intent, its deciding plan or execution branch) to catch
  up with concurrent changes early, rather than waiting for a massive end-of-execution conflict.
- **Budget Checks:** If a planner or executor turn exceeds its turn, stall or stage budget, one resume turn is attempted
  first, but only when the salvaged artefact is incomplete (not usable, or missing a required section) and the stage
  still has time left (so neither a stage-budget breach nor a breach that left a complete artefact gets one). A plan
  that is usable after it carries on with nothing recorded, and a breach confined to the executor's notes-only re-prompt
  is only logged. Otherwise the container commits the salvaged partial artefact (the executor commits its partial notes,
  not the code changes), records one `ACT-BUDGET` action in the ledger and the stage fails; the flow stops there rather
  than pausing for a human. A host-side `holon flow` stage ceiling also fails the stage, but writes no `ACT-BUDGET`
  record (see [Turn and Stage Budgets](budgets.md)).

### Niche or Unsupported Languages and Frameworks

- **Language-Agnostic Core:** The engine relies on standard shell execution, Git files, and JSON payloads, making it
  independent of any language.
- **Bootstrapping New Worlds:** When the system is introduced to a new language/framework, it uses a generic shell
  environment. The Curator Agent watches the ledger, records linter/compiler failures as new KB `failure_modes`, and
  extracts successful configuration patterns (like new Makefiles or build scripts) into KB `patterns`. Over time, future
  planners retrieve these patterns to handle the new environment seamlessly.

## 15. How does Holon compare to other Agentic Coding Architectures?

To understand when to choose Holon over other agentic architectures, consider the following comparison:

| Dimension               | Standard Coding Agents (e.g., SWE-agent, Devin, Claude Code)                    | Multi-Agent Frameworks (e.g., AutoGen, CrewAI)                       | Holon Architecture                                                                      |
| :---------------------- | :------------------------------------------------------------------------------ | :------------------------------------------------------------------- | :-------------------------------------------------------------------------------------- |
| **Workflow State**      | Linear thread / conversation history.                                           | Message routing between agents in a chat loop.                       | **Git-native fractal tree** (every intent is a branch).                                 |
| **Concurrency**         | Single-agent execution. Concurrency managed outside the agent.                  | Agents chat concurrently, but conflict resolution on code is ad-hoc. | **Git transaction isolation** (automatic concurrent rebasing/merging).                  |
| **Planning Approach**   | Single-plan / linear execution. Iterative correction (run-error-fix loops).     | Role-based collaboration. Plans are generated via chat protocols.    | **Competitive planning** (generates multiple distinct plan graphs; ranks by EV).        |
| **Safety & Trust**      | Coarse boundaries (e.g. read-only vs write-all tools).                          | Role boundaries and human-in-the-loop checks.                        | **Autonomy trust ladder & cost/entropy budgets** (earned permissions).                  |
| **Continuous Learning** | Session-only. Learning is lost between runs unless system prompts are modified. | Session-only. No built-in automated extraction of patterns.          | **Continuous Curation** (Curator Agent validates ledger and writes active KB patterns). |

### Specific Problems Holon Solves:

1. **The Iterative Loop Trap (Infinite Budget Exhaustion):** Traditional agents can get trapped in infinite "
   try-fail-repeat" terminal loops on a single complex file. Holon stops this by:
   - Requiring explicit plan variants up front.
   - Decomposing work fractally so failures are localized.
   - Using EV calculations and convergence policies to stop execution before budgets blowout.
2. **Concurrent Collaboration Failures:** In typical multi-agent systems, if two agents modify overlapping code, they
   overwrite each other or cause merge conflicts. Holon treats Git branches as database transactions, resolving
   conflicts automatically via reactive sub-intents.
3. **Estimation & Calibration Error:** Holon requires agents to predict outcomes ($P(success)$ and Entropy) _before_
   execution and measures _actual_ outcomes _afterwards_. The Curator Agent utilizes this difference to calibrate future
   estimations.
