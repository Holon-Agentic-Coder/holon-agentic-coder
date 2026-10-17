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

## Developer Best Practices

> [!IMPORTANT] **Run the build script locally before pushing changes:** Whenever you modify the \*
> _[Dockerfile](../Dockerfile)\*\* or _ _[docker-bake.hcl](../docker-bake.hcl)\*\*, you _ \*must\*\* run
> `./apps/sandbox-executor/build_all_images.sh` locally to ensure there are no compilation, dependency, or concurrency
> conflicts before pushing to remote.
