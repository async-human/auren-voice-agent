#!/usr/bin/env bash
set -euo pipefail

provider="${STT_PROVIDER:-whisper}"

case "${provider,,}" in
  whisper|faster-whisper|faster_whisper)
    cd /opt/speaches
    exec /opt/speaches/.venv/bin/uvicorn \
      --factory \
      --host 127.0.0.1 \
      --port 8000 \
      speaches.main:create_app \
      --log-level info
    ;;
  qwen|qwen3|qwen3-asr|qwen3_asr)
    echo "STT_PROVIDER=${provider}; using the configured external/companion STT endpoint"
    exec tail --follow /dev/null
    ;;
  *)
    echo "Unsupported STT_PROVIDER=${provider}" >&2
    exit 2
    ;;
esac
