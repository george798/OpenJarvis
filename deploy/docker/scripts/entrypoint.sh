#!/bin/sh
set -e

sync_cloud_env_key() {
  key_name="$1"
  eval "key_val=\$$key_name"
  [ -z "$key_val" ] && return 0
  keys_file="/root/.openjarvis/cloud-keys.env"
  mkdir -p "$(dirname "$keys_file")"
  if [ -f "$keys_file" ] && grep -q "^${key_name}=" "$keys_file"; then
    sed -i "s|^${key_name}=.*|${key_name}=${key_val}|" "$keys_file"
  else
    echo "${key_name}=${key_val}" >> "$keys_file"
  fi
}

for _cloud_var in OPENROUTER_API_KEY ANTHROPIC_API_KEY OPENAI_API_KEY NVIDIA_NIM_API_KEY GEMINI_API_KEY GOOGLE_API_KEY MINIMAX_API_KEY FISH_API_KEY CARTESIA_API_KEY; do
  sync_cloud_env_key "$_cloud_var"
done

# Export keys from cloud-keys.env so jarvis serve sees FISH_API_KEY etc.
load_cloud_keys_into_env() {
  keys_file="/root/.openjarvis/cloud-keys.env"
  [ -f "$keys_file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|\#*) continue ;;
    esac
    key="${line%%=*}"
    val="${line#*=}"
    key="$(printf '%s' "$key" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | tr -d '\357\273\277')"
    val="$(printf '%s' "$val" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    case "$key" in
      ''|*[!A-Za-z0-9_]* ) continue ;;
    esac
    [ -n "$key" ] && export "$key=$val"
  done < "$keys_file"
}
load_cloud_keys_into_env

# When Ollama is the configured backend, wait for it before jarvis serve.
# If the API server starts while Ollama is still down, engine discovery
# falls back to LiteLLM, which cannot serve bare Ollama tags like
# qwen3.6:35b-a3b-q4_K_M and every chat fails with "LLM Provider NOT provided".
wait_for_ollama() {
  ollama_host="${OLLAMA_HOST:-http://host.docker.internal:11434}"
  case "$ollama_host" in
    */api/*) ollama_tags_url="$ollama_host" ;;
    *) ollama_tags_url="${ollama_host%/}/api/tags" ;;
  esac
  max_attempts="${OLLAMA_WAIT_ATTEMPTS:-30}"
  attempt=1
  echo "[openjarvis] Waiting for Ollama at $ollama_tags_url ..."
  while [ "$attempt" -le "$max_attempts" ]; do
    if curl -sf "$ollama_tags_url" >/dev/null 2>&1; then
      echo "[openjarvis] Ollama is ready."
      return 0
    fi
    sleep 2
    attempt=$((attempt + 1))
  done
  echo "[openjarvis] WARNING: Ollama not reachable after $max_attempts attempts."
  echo "[openjarvis] Start Ollama on the host (ollama serve) and restart this container."
  return 1
}

# Ensure Playwright browsers exist (image-baked under /opt/playwright; self-heal if missing).
PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/opt/playwright}"
export PLAYWRIGHT_BROWSERS_PATH
if ! ls "$PLAYWRIGHT_BROWSERS_PATH"/chromium-* >/dev/null 2>&1; then
  echo "[openjarvis] Playwright Chromium not found in $PLAYWRIGHT_BROWSERS_PATH — installing..."
  mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"
  playwright install --with-deps chromium
fi

# Soft dependency probe — never blocks startup (build-time verify is hard-fail).
# Surfaces image drift after volume mounts / partial upgrades as a clear log line
# instead of a mid-chat "No module named …" surprise.
if [ -f /app/deploy/docker/scripts/verify_hybrid_deps.py ]; then
  echo "[openjarvis] Checking hybrid runtime deps..."
  python /app/deploy/docker/scripts/verify_hybrid_deps.py --soft --check-browsers \
    || echo "[openjarvis] WARNING: hybrid dep check reported issues (see above)."
fi

echo "[openjarvis] Starting MCP SSE bridge on :8888..."
python /app/deploy/docker/scripts/mcp_sse_server.py &
MCP_PID=$!

API_PID=""
if [ -n "$NVIDIA_NIM_API_KEY" ] || [ -n "$ANTHROPIC_API_KEY" ] || [ -n "$OPENROUTER_API_KEY" ] || [ -n "$OPENAI_API_KEY" ] || [ -n "$OLLAMA_HOST" ] || [ "${OPENJARVIS_ENGINE_DEFAULT:-}" = "ollama" ]; then
  if [ -n "$OLLAMA_HOST" ] || [ "${OPENJARVIS_ENGINE_DEFAULT:-}" = "ollama" ]; then
    echo "[openjarvis] Ollama configured — starting API server on :8000..."
    wait_for_ollama || true
  elif [ -n "$NVIDIA_NIM_API_KEY" ]; then
    echo "[openjarvis] NVIDIA NIM API key detected — starting API server on :8000 (LiteLLM)..."
  else
    echo "[openjarvis] Cloud API key detected — starting API server on :8000..."
  fi
  python /app/deploy/docker/scripts/inject_web_bootstrap.py || true
  serve_args="--host 0.0.0.0 --port 8000"
  if [ -n "$OLLAMA_HOST" ] || [ "${OPENJARVIS_ENGINE_DEFAULT:-}" = "ollama" ]; then
    serve_args="$serve_args --engine ollama"
  fi
  jarvis serve $serve_args &
  API_PID=$!
else
  echo "[openjarvis] No inference backend configured — API server skipped."
  echo "[openjarvis] Memory indexing + MCP tools still work. Set OLLAMA_HOST or a cloud API key in .env and restart."
fi

shutdown() {
  echo "[openjarvis] Shutting down..."
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
  kill "$MCP_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap shutdown TERM INT

if [ -n "$API_PID" ]; then
  while kill -0 "$API_PID" 2>/dev/null && kill -0 "$MCP_PID" 2>/dev/null; do
    sleep 2
  done
  echo "[openjarvis] A child process exited unexpectedly"
  shutdown
  exit 1
else
  wait "$MCP_PID"
fi
