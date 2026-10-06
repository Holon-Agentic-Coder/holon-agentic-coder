#!/usr/bin/env bash
set -euo pipefail


# Extract key from ephemeral secret bundle if present
SECRET_BUNDLE="${HOLON_SECRET_BUNDLE_PATH:-/run/secrets/holon_auth.json}"
if [ -f "$SECRET_BUNDLE" ]; then
    if ! command -v jq &>/dev/null; then
        echo "role_dispatcher: WARNING: secret bundle detected at '$SECRET_BUNDLE' but 'jq' is not installed or available on PATH; skipping secret bundle ingestion (downstream agent authentication may fail)" >&2
    else
        # Safely extract bundle parameters under set -euo pipefail without crashing on malformed JSON
        if BUNDLE_AGENT=$(jq -r '.agent_id // ""' "$SECRET_BUNDLE" 2>/dev/null); then
            BUNDLE_AGENT="${BUNDLE_AGENT,,}"
            BUNDLE_AGENT_ID="${BUNDLE_AGENT//-agent/}"
            BUNDLE_AGENT_ID="${BUNDLE_AGENT_ID//agent-/}"
            
            TARGET_AGENT_ID=$(echo "${HOLON_AGENT_ID:-}" | tr '[:upper:]' '[:lower:]' | sed 's/-agent//g' | sed 's/agent-//g')
            
            if [ -z "$BUNDLE_AGENT_ID" ] || [ "$BUNDLE_AGENT_ID" = "$TARGET_AGENT_ID" ]; then
                API_KEY=$(jq -r '.api_key // .token // ""' "$SECRET_BUNDLE" 2>/dev/null || true)
                if [ -n "$API_KEY" ] && [ "$API_KEY" != "null" ]; then
                    export HOLON_AGENT_KEY="$API_KEY"
                fi
            fi
        else
            echo "role_dispatcher: WARNING: failed to parse secret bundle at '$SECRET_BUNDLE'; skipping secret bundle ingestion" >&2
        fi
    fi
fi

# Map HOLON_AGENT_KEY to legacy/vendor-specific API variables
if [ -n "${HOLON_AGENT_KEY:-}" ] && [ -n "${HOLON_AGENT_ID:-}" ]; then
    AGENT_ID=$(echo "${HOLON_AGENT_ID}" | tr '[:upper:]' '[:lower:]' | sed 's/-agent//g' | sed 's/agent-//g')
    case "${AGENT_ID}" in
        antigravity)
            export AGY_USER_TOKEN="${HOLON_AGENT_KEY}"
            export GOOGLE_API_KEY="${HOLON_AGENT_KEY}"
            ;;
        claude)
            export ANTHROPIC_API_KEY="${HOLON_AGENT_KEY}"
            ;;
        pi)
            export PI_API_KEY="${HOLON_AGENT_KEY}"
            ;;
        codex)
            export OPENAI_API_KEY="${HOLON_AGENT_KEY}"
            ;;
        gemini)
            export GEMINI_API_KEY="${HOLON_AGENT_KEY}"
            ;;
        opencode)
            export OPENCODE_API_KEY="${HOLON_AGENT_KEY}"
            ;;
    esac
fi

# Trust the host-provided Holon Root CA when token reduction is enabled.
#
# The sandbox image runs as the unprivileged `holon` user (Dockerfile: `USER holon`), so
# update-ca-certificates — which writes root-owned files under /etc/ssl/certs — can never succeed
# here. Instead we materialise a MERGED bundle: the image's system trust store plus the Holon CA.
#
# SSL_CERT_FILE / REQUESTS_CA_BUNDLE / CURL_CA_BUNDLE REPLACE the trust store of the clients that
# read them, so they must point at this merged file and never at the single-cert Holon mount (that
# would break every legitimate HTTPS endpoint). NODE_EXTRA_CA_CERTS AUGMENTS Node's built-in roots,
# so it stays on the single-cert mount. Failures are non-fatal but always reported on stderr.
_holon_ca_log() { printf 'role_dispatcher: %s\n' "$*" >&2; }

HOLON_ROOT_CA_PATH="${HOLON_ROOT_CA_PATH:-/usr/local/share/ca-certificates/holon-root-ca.crt}"
HOLON_CA_BUNDLE_PATH="${HOLON_CA_BUNDLE_PATH:-/home/holon/.holon-ca-bundle.crt}"
SYSTEM_CA_BUNDLE="${SYSTEM_CA_BUNDLE:-/etc/ssl/certs/ca-certificates.crt}"

if [ -f "$HOLON_ROOT_CA_PATH" ]; then
    if [ -r "$SYSTEM_CA_BUNDLE" ]; then
        HOLON_CA_SOURCES=("$SYSTEM_CA_BUNDLE" "$HOLON_ROOT_CA_PATH")
    else
        _holon_ca_log "system CA bundle '$SYSTEM_CA_BUNDLE' is missing or unreadable; building '$HOLON_CA_BUNDLE_PATH' from the Holon Root CA only"
        HOLON_CA_SOURCES=("$HOLON_ROOT_CA_PATH")
    fi

    if cat "${HOLON_CA_SOURCES[@]}" > "$HOLON_CA_BUNDLE_PATH"; then
        chmod 600 "$HOLON_CA_BUNDLE_PATH" || _holon_ca_log "could not chmod 600 '$HOLON_CA_BUNDLE_PATH'"
        export SSL_CERT_FILE="$HOLON_CA_BUNDLE_PATH"
        export REQUESTS_CA_BUNDLE="$HOLON_CA_BUNDLE_PATH"
        # CURL_CA_BUNDLE is honoured by `requests`, not by the curl binary itself.
        export CURL_CA_BUNDLE="$HOLON_CA_BUNDLE_PATH"
        export NODE_EXTRA_CA_CERTS="$HOLON_ROOT_CA_PATH"
    else
        # Never leave the trust-store overrides pointing at a file that does not exist: unset them so
        # clients fall back to the image's default store instead of failing every HTTPS request.
        _holon_ca_log "could not write merged CA bundle to '$HOLON_CA_BUNDLE_PATH'; unsetting SSL_CERT_FILE/REQUESTS_CA_BUNDLE/CURL_CA_BUNDLE so clients use the image default store (Holon-signed traffic may fail verification)"
        unset SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE
    fi
fi

ROLE="${HOLON_ROLE:-}"

case "$ROLE" in
    intent-creator)
        exec python3 -m sandbox_executor.entrypoint.intent_creator "$@"
        ;;
    planner)
        exec python3 -m sandbox_executor.entrypoint.planner "$@"
        ;;
    executor)
        exec python3 -m sandbox_executor.entrypoint.executor "$@"
        ;;
    *)
        # If no role, allow running arbitrary commands (like ls or bash)
        exec "$@"
        ;;
esac
