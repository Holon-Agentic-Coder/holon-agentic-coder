# AI Agent Token Reduction Subpackage

This subpackage implements a multi-tiered token optimisation system designed to significantly reduce LLM token usage,
context sizes, and API costs during autonomous agent operations.

---

## 🚀 Features & Components

The system operates across four primary optimisation layers:

1. **Phase 1: MITM Interception & Certificate Trust**
   - Intercepts outbound agent HTTPS requests to LLM APIs (Anthropic, OpenAI, Gemini).
   - Automatically bootstraps self-signed Root CA certificates to enable secure SSL/TLS traffic interception.
   - Injectable proxy settings via volume mounts and container trust environment variables.

2. **Phase 2: Context Cleaning & Prompt Cache Optimisation**
   - **Tool Result Deduplication**: Omit repeating redundant payloads (e.g., repeating `cat` or `grep` file reads) from
     previous turns and replace them with structural references.
   - **Prompt Cache Control Breakpoints**: Inject Anthropic-specific `"cache_control": {"type": "ephemeral"}`
     breakpoints to utilize cheaper prompt caching.
   - **Summarisation**: Condenses conversation history dynamically when turn count exceeds thresholds.

3. **Phase 3: Hybrid & Semantic Prompt Caching**
   - Disk-backed SQLite exact and semantic prompt caching.
   - Normalises transient values (timestamps, run IDs, UUIDs) before checking hits.
   - Token-based semantic similarity checking using Jaccard indexes for minor prompt variations.
   - **Tuple Return & Operational Short-Circuiting**: `intercept_request` returns
     `(cleaned_request_json, cached_response_or_none)`. If a cached response is present, `MitmproxyAddon.request(flow)`
     sets `flow.response = Response.make(200, ...)` to short-circuit the request directly without making outbound LLM
     API network calls.

4. **Phase 4: Targeted Context Retrieval & Orchestration**
   - **RAG Codebase Indexing**: Symbol-based AST parsing and BM25 search to prune initial context inputs.
   - **OpenBrain Episodic Memory**: Session-to-session memory database to preserve learnings.
   - **Ringer Orchestrator**: High-capability Architect model delegating micro-tasks to cheaper, faster Executor agents.

---

## 📖 Usage Instructions

### 1. Direct CLI Usage

Enable token reduction transparently when spawning plan generation or execution containers by adding the
`--token-reduce` flag:

```bash
# Generate a plan with token reduction proxy enabled
./holon plan I-1787914053-token-reduction-plan/_ --token-reduce

# Execute plan steps with token reduction proxy enabled
./holon execute I-1787914053-token-reduction-plan/P-1787914074-antigravity-agent-gemini-3.5-flash/_ --token-reduce
```

### 2. Setting Up the Host MITM Interception Proxy & Launching `agy`

#### Step A: Run the MITM Sidecar Docker Container

Start the `mitmproxy` container with the token reduction addon:

> [!NOTE] Run the following command from the repository root directory so that `$(pwd)` resolves correctly to the source
> files.

```bash
mkdir -p ~/.holon/proxy-ca ~/.holon/cache
chmod 700 ~/.holon/proxy-ca

docker run --rm --name host-mitm-proxy \
  -p 127.0.0.1:8080:8080 \
  -v ~/.holon/proxy-ca:/home/mitmproxy/.mitmproxy:rw \
  -v ~/.holon/cache:/home/mitmproxy/.holon/cache:rw \
  -v $(pwd)/apps/sandbox-executor/src:/tmp/src \
  -e PYTHONPATH=/tmp/src \
  -e PYTHONUNBUFFERED=1 \
  -v $(pwd)/apps/sandbox-executor/src/sandbox_executor/token_reduction/mitm_addon.py:/tmp/mitm_addon.py:ro \
  mitmproxy/mitmproxy:12.2.3 \
  mitmdump -s /tmp/mitm_addon.py \
           --listen-port 8080 \
           --set ignore_hosts='^([a-zA-Z0-9.-]+\.)?github(usercontent)?\.com:443$'
```

On first run, the Root CA certificate is generated automatically and stored at
`~/.holon/proxy-ca/mitmproxy-ca-cert.pem`.

> [!TIP] **Root CA Key Security & Linux Permissions**: Ensure `chmod 700 ~/.holon/proxy-ca` is set to safeguard
> generated private CA keys from unauthorized local users. On native Linux hosts with strict file permissions, users can
> pass `--user $(id -u):$(id -g)` to the `docker run` command or ensure `chmod 700 ~/.holon/proxy-ca ~/.holon/cache`.

#### Step B: Launch `agy` Connected to the Proxy

Once the container is running, launch `agy` in your terminal:

```bash
HTTP_PROXY="http://127.0.0.1:8080" \
HTTPS_PROXY="http://127.0.0.1:8080" \
http_proxy="http://127.0.0.1:8080" \
https_proxy="http://127.0.0.1:8080" \
NODE_EXTRA_CA_CERTS="$HOME/.holon/proxy-ca/mitmproxy-ca-cert.pem" \
SSL_CERT_FILE="$HOME/.holon/proxy-ca/mitmproxy-ca-cert.pem" \
NO_PROXY="localhost,127.0.0.1,::1,api.github.com,github.com,.githubusercontent.com" \
no_proxy="localhost,127.0.0.1,::1,api.github.com,github.com,.githubusercontent.com" \
agy
```

---

## 🐍 Python API Examples

### Using the Hybrid Cache Store

```python
from sandbox_executor.token_reduction import HybridCacheStore

# Initialise the cache store pointing to the local SQLite DB
cache = HybridCacheStore(cache_dir="~/.holon/cache", similarity_threshold=0.85)

payload = {
    "model": "claude-3-5-sonnet",
    "messages": [{"role": "user", "content": "Fix bug in task-4567 at 2026-08-29T10:00:00Z"}]
}

# Lookup will normalise timestamps/IDs and check exact or semantic matches
cached_response = cache.get(payload, provider="anthropic")
if cached_response:
    print("Cache hit:", cached_response)
else:
    # Perform API call ...
    api_response = {"content": "Bug fixed."}
    cache.put(payload, api_response, provider="anthropic")
```

### Context Deduplication & Summarisation

```python
from sandbox_executor.token_reduction import ContextCleaner

cleaner = ContextCleaner(enable_deduplication=True, max_turns=20)

raw_payload = {
    "system": "System instructions...",
    "messages": [
        {"role": "user", "content": "Read file main.py"},
        {"role": "assistant", "content": [{"type": "tool_result", "content": "class App: ..."}]}
    ]
}

# Optimises messages history and injects Anthropic cache control markers
optimized_payload = cleaner.process_payload(raw_payload, provider="anthropic")
```

### RAG Codebase Search

```python
from sandbox_executor.token_reduction import RAGCodebaseIndexer

indexer = RAGCodebaseIndexer(root_dir="/workspace")

# Query codebase class/function definitions
symbols = indexer.graph_find_symbol("DatabaseConnection")

# Perform keyword-relevance semantic search
matches = indexer.semantic_search("metrics cache")
```
