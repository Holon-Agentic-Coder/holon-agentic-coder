# Docker Buildx Bake Migration

This document outlines the rationale, architecture, and performance benefits of migrating the Holon image build system
to Docker Buildx Bake (`docker-bake.hcl`).

---

## The Previous Build Setup

Previously, image builds were managed by a custom shell script (`build_all_images.sh`) that:

1. Built the base image (`holon/base`) sequentially.
2. Launched background parallel Bash subprocesses to build the remaining 9 images (`orchestrator` and 8 agents).
3. Attempted to load images into the local Docker daemon engine sequentially using `docker buildx build`.

### Limitations of the Previous Approach

- **Lack of a Global Dependency Graph:** Each parallel subprocess executed a separate, isolated `docker buildx build`
  command. Buildx had no awareness of the overall project graph, leading to redundant work and resource contention.
- **Local Engine Import/Export Overhead:** Downstream agent stages had to wait for the base image to be fully loaded
  into the local Docker daemon before they could begin building.
- **Cache Pollution & Overlap:** Remote cache targets (`type=gha`) shared overlapping parameters, causing cache uploads
  to conflict or overwrite one another in parallel.
- **Resource Management:** Concurrency had to be hardcoded in Bash, which is fragile and does not scale well across
  different machine sizes (local dev vs. CI runners).

---

## The Solution: Docker Buildx Bake

Docker Buildx Bake allows defining the entire project build configuration in a declarative High-Level Configuration (
HCL) file: `docker-bake.hcl`.

### Key Benefits

### 1. Directed Acyclic Graph (DAG) Execution

BuildKit compiles all targets into a single dependency tree. It resolves which stages to execute concurrently,
optimising CPU usage and maximising build parallelisation.

### 2. In-Memory Context References (`target:base`)

Instead of referencing parent images from the local Docker engine registry, downstream targets reference parent build
states in memory:

```hcl
target "agent-pi" {
  inherits = ["common"]
  contexts = {
    holon-base = "target:base"
  }
}
```

This bypasses importing and exporting intermediate base layers to disk, significantly reducing disk I/O.

### 3. Unified Cache Scope

The entire project is assigned a single unified remote GHA cache scope (`holon-sandbox-executor`):

```hcl
cache-from = CI == "true" ? ["type=gha,scope=holon-sandbox-executor"] : []
cache-to = CI == "true" ? ["type=gha,mode=max,scope=holon-sandbox-executor"] : []
```

This ensures BuildKit can match parent (`holon-base`) and child layer metadata correctly, enabling full layer caching
across targets and preventing cache thrashing.

---

## How to Run the Build

The wrapper script `build_all_images.sh` has been simplified to delegate directly to Docker Buildx Bake:

```bash
# Build all images locally (loaded into local Docker daemon)
./apps/sandbox-executor/build_all_images.sh

# Build with logs timestamped
./apps/sandbox-executor/build_all_images.sh --output-log

# Build without cache
./apps/sandbox-executor/build_all_images.sh --no-cache
```

At its core, the script executes:

```bash
docker buildx bake -f apps/sandbox-executor/docker-bake.hcl --load
```

---

## Build Performance Benchmarks

Buildx Bake compiles the dependency graph of all eight images into a unified DAG, running independent target builds
concurrently while sharing intermediate base layers in memory. The table below outlines benchmark comparisons between a
cold build (`--no-cache`) and an incremental cached build:

| Target Image              | Cold Build (`--no-cache`) | Incremental Cached Build | Primary Bottleneck / Activity                       |
| ------------------------- | ------------------------- | ------------------------ | --------------------------------------------------- |
| `holon/base`              | ~72s                      | ~0.4s                    | Node.js / Python base layer compilation, apt pkgs   |
| `holon/orchestrator`      | ~4s                       | ~0.2s                    | Entrypoint script injection & source packaging      |
| `holon/agent-claude`      | ~38s                      | ~0.5s                    | `@anthropic-ai/claude-code@2.1.302` npm install     |
| `holon/agent-codex`       | ~24s                      | ~0.4s                    | `@openai/codex@0.163.0` npm install                 |
| `holon/agent-gemini`      | ~42s                      | ~0.5s                    | `@google/gemini-cli@0.64.0` npm install             |
| `holon/agent-opencode`    | ~31s                      | ~0.4s                    | `opencode-ai@1.19.3` npm install                    |
| `holon/agent-pi`          | ~35s                      | ~0.5s                    | `@earendil-works/pi-coding-agent@1.3.0` npm install |
| `holon/agent-antigravity` | ~29s                      | ~0.4s                    | Antigravity CLI installer execution                 |
| **Total Build & Load**    | **~118s (concurrent)**    | **~2.8s (DAG eval)**     | Local daemon export & layer loading                 |

---

## Layer Caching & BuildKit Mount Scoping

Buildx Bake leverages dedicated BuildKit cache mounts and unified remote cache scopes to maximise build efficiency
without cross-target contamination:

1. **Per-Agent Isolated Cache Mounts**: Each agent stage specifies an isolated npm cache mount
   (`--mount=type=cache,id=npm-<agent>,uid=1000,gid=1000,target=/home/holon/.npm`). Isolating cache IDs per target
   prevents concurrent npm process lock contention and cache corruption during parallel stage execution.
2. **Unified Remote Cache Scope**: For CI environments, `docker-bake.hcl` assigns a single cache scope
   (`type=gha,scope=holon-sandbox-executor`). This ensures BuildKit shares common base layer hashes across all
   downstream agent targets in GitHub Actions workflows.
3. **In-Memory Context Propagation**: Downstream agent targets reference `holon-base` directly from memory during the
   DAG evaluation, avoiding costly intermediary disk writes or local Docker engine load/unload cycles.

---

## Writable Tmpfs Overlay Architecture

Under Holon's strict sandbox containment policy (`holon-config/world/constraints.md` §2 and `docs/safety.md` §2), host
credential directories mounted into containers must remain strictly read-only (`:ro`) to eliminate the risk of
credential tampering, credential leakage, or unauthorised token mutation.

However, modern CLI agent runners frequently require runtime write access to local log directories and execution caches.
To satisfy both constraints simultaneously, `sandbox_executor/cli.py` provisions in-memory Docker tmpfs overlays:

- **Read-Only Session Mount**: Host session paths (`~/.holon/sessions/antigravity` or `~/.gemini/antigravity-cli`) are
  mounted at `/home/holon/.gemini/antigravity-cli:ro`, preserving host authentication tokens.
- **Log Tmpfs Overlay**: Mounted at `/home/holon/.gemini/antigravity-cli/log:uid=1000,gid=1000`, providing volatile
  scratch space for process logs.
- **Cache Tmpfs Overlay**: Mounted at `/home/holon/.gemini/antigravity-cli/cache:uid=1000,gid=1000`, providing volatile
  cache space.
- **Config Tmpfs Overlay**: Mounted at `/home/holon/.gemini/config:uid=1000,gid=1000` to allow project state creation
  without root ownership conflicts.

This architecture guarantees that all write operations performed by the agent remain strictly ephemeral in RAM and are
purged upon container termination, preserving complete credential boundary isolation.

---

## Developer Best Practices

> [!IMPORTANT] **Run the build script locally before pushing changes:** Whenever you modify the
> [Dockerfile](../Dockerfile) or [docker-bake.hcl](../docker-bake.hcl), you must run
> `./apps/sandbox-executor/build_all_images.sh` locally to ensure there are no compilation, dependency, or concurrency
> conflicts before pushing to remote.
