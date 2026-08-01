#!/usr/bin/env bash
set -euo pipefail

required=(
  LIVEKIT_URL
  LIVEKIT_API_KEY
  LIVEKIT_API_SECRET
  LIVEKIT_AGENT_NAME
)

for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "Required environment variable ${name} is not set" >&2
    exit 1
  fi
done

if ! nvidia-smi >/dev/null 2>&1; then
  echo "No NVIDIA GPU is visible inside the container" >&2
  exit 1
fi

mkdir -p \
  /workspace/logs \
  /workspace/models \
  /workspace/models/huggingface \
  /workspace/models/chatterbox-hf \
  /workspace/models/ollama \
  /workspace/chatterbox/reference_audio \
  /workspace/chatterbox/outputs \
  /workspace/runtime

rm -f /workspace/runtime/models-ready /workspace/runtime/active-sessions.json

echo "Starting Auren GPU runtime on $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
exec /usr/bin/tini -- /usr/bin/supervisord -c /etc/supervisor/conf.d/auren.conf
