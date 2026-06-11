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

for _cloud_var in OPENROUTER_API_KEY ANTHROPIC_API_KEY OPENAI_API_KEY NVIDIA_NIM_API_KEY GEMINI_API_KEY GOOGLE_API_KEY MINIMAX_API_KEY; do
  sync_cloud_env_key "$_cloud_var"
done

echo "[openjarvis] Starting MCP SSE bridge on :8888..."
python /app/deploy/docker/scripts/mcp_sse_server.py &
MCP_PID=$!

API_PID=""
if [ -n "$NVIDIA_NIM_API_KEY" ] || [ -n "$ANTHROPIC_API_KEY" ] || [ -n "$OPENROUTER_API_KEY" ] || [ -n "$OPENAI_API_KEY" ] || [ -n "$OLLAMA_HOST" ] || [ "${OPENJARVIS_ENGINE_DEFAULT:-}" = "ollama" ]; then
  if [ -n "$OLLAMA_HOST" ] || [ "${OPENJARVIS_ENGINE_DEFAULT:-}" = "ollama" ]; then
    echo "[openjarvis] Ollama configured — starting API server on :8000..."
  elif [ -n "$NVIDIA_NIM_API_KEY" ]; then
    echo "[openjarvis] NVIDIA NIM API key detected — starting API server on :8000 (LiteLLM)..."
  else
    echo "[openjarvis] Cloud API key detected — starting API server on :8000..."
  fi
  python /app/deploy/docker/scripts/inject_web_bootstrap.py || true
  jarvis serve --host 0.0.0.0 --port 8000 &
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
