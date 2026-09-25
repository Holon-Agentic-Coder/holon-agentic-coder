#!/usr/bin/env bash
set -euo pipefail

# Resolve the directory of this script and repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Help menu
show_help() {
  cat <<EOF
Usage: $(basename "$0") [options]

Builds all Docker images for the Holon agentic environments using Docker Buildx Bake.

Options:
  -h, --help      Show this help message and exit
  --no-cache      Build Docker images without using the cache (forces fresh packages/dependencies)
  --output-log    Print all build logs to stdout with timestamps prepended
EOF
}

# Check command line arguments
NO_CACHE_FLAG=""
OUTPUT_LOG="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      show_help
      exit 0
      ;;
    --no-cache)
      NO_CACHE_FLAG="--no-cache"
      shift
      ;;
    --output-log)
      OUTPUT_LOG="true"
      shift
      ;;
    *)
      echo "Unknown option: $1"
      show_help
      exit 1
      ;;
  esac
done

# Helper function to generate a timestamp (written to the variable name passed as the first argument)
get_timestamp() {
  local target_var="$1"
  local ts_val=""
  if [[ -n "${EPOCHREALTIME:-}" ]]; then
    local epoch="$EPOCHREALTIME"
    local sec="${epoch%.*}"
    local usec="${epoch#*.}"
    local formatted_sec
    formatted_sec="$(date -d "@$sec" +"%Y-%m-%d %H:%M:%S" 2>/dev/null || date -r "$sec" +"%Y-%m-%d %H:%M:%S" 2>/dev/null || date +"%Y-%m-%d %H:%M:%S")"
    ts_val="${formatted_sec}.${usec:0:3}"
  else
    if ! printf -v ts_val '%(%Y-%m-%d %H:%M:%S)T' -1 2>/dev/null || [[ -z "$ts_val" ]]; then
      ts_val="$(date +"%Y-%m-%d %H:%M:%S")"
    fi
  fi
  printf -v "$target_var" "%s" "$ts_val"
}

# Helper function to print log lines from stdin with timestamps in real-time
print_log_with_timestamps() {
  local ts
  while IFS= read -r line || [[ -n "$line" ]]; do
    get_timestamp ts
    printf "[%s] %s\n" "$ts" "$line"
  done
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "Starting Docker Buildx Bake build for all Holon images..."

  # Build the bake arguments
  BAKE_ARGS=("-f" "$SCRIPT_DIR/docker-bake.hcl" "--load")

  if [[ -n "$NO_CACHE_FLAG" ]]; then
    BAKE_ARGS+=("--no-cache")
  fi

  # Change directory to repository root so relative paths in docker-bake.hcl resolve correctly
  cd "$REPO_ROOT"

  # Run docker buildx bake
  # If OUTPUT_LOG is true, pipe the output to print_log_with_timestamps
  if [[ "$OUTPUT_LOG" == "true" ]]; then
    if ! docker buildx bake "${BAKE_ARGS[@]}" 2>&1 | print_log_with_timestamps; then
      echo "ERROR: Docker Buildx Bake failed!"
      exit 1
    fi
  else
    if ! docker buildx bake "${BAKE_ARGS[@]}"; then
      echo "ERROR: Docker Buildx Bake failed!"
      exit 1
    fi
  fi

  echo "All images built successfully!"
fi
