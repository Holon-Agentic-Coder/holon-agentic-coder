# Git Submodules Guide: `holon-coherence` Integration

This document outlines how Git submodules are managed in the **Holon: Agentic Coder** repository, focusing on the
`holon-coherence` submodule integration.

---

## 1. Architecture & Mechanics

The repository embeds external specialized components as Git submodules to maintain modularity, clean boundaries, and
independent release lifecycles.

### `holon-coherence` Submodule

- **Path**: `holon-coherence/`
- **Remote Repository**: `https://github.com/Holon-Agentic-Coder/holon-coherence.git`
- **Role**: Provides the LLM optimization gateway, MITM proxy, token reduction techniques, wire telemetry logging, and
  SSE stream parsing.
- **Default Branch**: `main`

### How Git Tracks Submodules

Git manages submodules through two separate mechanisms:

1. **Configuration (`.gitmodules`)**: Tracks the submodule URL, local path, and default tracking branch in plain text:

   ```ini
   [submodule "holon-coherence"]
   	path = holon-coherence
   	url = https://github.com/Holon-Agentic-Coder/holon-coherence.git
   	branch = main
   ```

   > [!IMPORTANT] Always use public `https://` URLs in `.gitmodules`. Do not use SSH (`git@github.com:...`), as CI
   > runners, Docker containers, and automated sandboxes clone without user SSH keys.

2. **Git Tree Object (`160000` Gitlink)**: Git records a special tree entry with file mode `160000` pointing to an
   **exact, pinned commit SHA** in the submodule's history:
   ```text
   160000 commit 87dc438254e5c229d3b46fb8a4afe03febf4a7e8 holon-coherence
   ```
   Git **never** automatically advances the submodule to the tip of `main`. The parent repository stays pinned to this
   exact commit until you explicitly update and commit a new gitlink.

---

## 2. Developer Workflow & Operations

### Initializing Submodules

When checking out the repository, creating a new Git worktree, or cloning freshly, submodules will initially appear as
empty directories. Run:

```bash
git submodule update --init --recursive
```

To clone and initialize submodules in a single step:

```bash
git clone --recurse-submodules <repo-url>
```

### Checking Submodule Status

Inspect the current status of submodules:

```bash
git submodule status
```

Status indicators:

- ` ` (space): Submodule is clean and matches the commit pinned in the parent repository.
- `-` (minus): Submodule is uninitialized (run `git submodule update --init`).
- `+` (plus): Checked-out submodule commit differs from the pinned commit in the parent repository.
- `U` (uppercase U): Submodule has merge conflicts.

To inspect the exact gitlink stored in the staging index or HEAD:

```bash
git ls-files --stage holon-coherence
git ls-tree HEAD holon-coherence
```

### Updating `holon-coherence` to Latest Main

When new features, bug fixes, or token optimization enhancements are pushed to `holon-coherence`:

```bash
# 1. Update submodule to the remote tracking branch tip
git submodule update --remote --merge holon-coherence

# 2. Verify status and test
git status
uv run pytest                         # Verify parent test suite passes
(cd holon-coherence && uv run pytest) # Run submodule internal tests

# 3. Stage and commit the updated gitlink
git add holon-coherence
git commit -m "chore(submodule): update holon-coherence to $(git -C holon-coherence rev-parse --short HEAD)"
```

### Pinning to a Specific Commit, Branch, or Tag

If you need to pin `holon-coherence` to a specific version or test branch:

```bash
cd holon-coherence
git fetch origin
git checkout <commit-sha-or-tag>
cd ..

# Stage the updated gitlink
git add holon-coherence
git commit -m "chore(submodule): pin holon-coherence to <commit-sha>"
```

### Developing Inside `holon-coherence`

If making modifications directly inside the `holon-coherence/` folder:

1. Create or checkout a feature branch inside `holon-coherence/`:
   ```bash
   cd holon-coherence
   git checkout -b feat/<name>
   ```
2. Commit and push your changes to `origin` from inside `holon-coherence/`:
   ```bash
   git add .
   git commit -m "feat: your coherence change"
   git push origin feat/<name>
   ```
3. Return to the parent repository root, stage the new commit, and commit it:
   ```bash
   cd ..
   git add holon-coherence
   git commit -m "chore(submodule): bump holon-coherence to latest feat/<name>"
   ```

> [!WARNING] Before merging any parent repository PR into `main`, ensure the submodule commit has been merged into
> `holon-coherence:main` (or pinned to a permanent release tag). If the parent repository points to a submodule feature
> branch commit that is later rebased, squashed, or deleted, downstream clones will fail with missing tree object
> errors.

To avoid accidentally pushing parent commits that reference unpushed local submodule commits:

> [!TIP] Run `git config push.recurseSubmodules check` in the parent repository to configure Git to verify that all
> referenced submodule commits exist on the remote before pushing.

---

## 3. Continuous Integration (CI) Setup

GitHub Actions workflows must checkout submodules so that builds, tests, and Docker containers have access to
`holon-coherence`:

```yaml
- name: Checkout repository and submodules
  uses: actions/checkout@v7
  with:
    submodules: true
```

If submodules themselves contain nested submodules, use:

```yaml
submodules: recursive
```

---

## 4. Removing a Submodule

If a submodule is ever deprecated or replaced:

```bash
# De-initialize the submodule from .git/config
git submodule deinit -f holon-coherence

# Remove the submodule working tree and .gitmodules entry
git rm -f holon-coherence

# Clean up internal git database cache (works in standard clones and git worktrees)
rm -rf "$(git rev-parse --git-path modules/holon-coherence)"

# Commit the removal
git commit -m "chore: remove holon-coherence submodule"
```
