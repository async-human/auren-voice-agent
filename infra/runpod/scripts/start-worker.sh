#!/usr/bin/env bash
set -euo pipefail

deadline=$((SECONDS + ${AUREN_BOOT_TIMEOUT_SECONDS:-1800}))
while [[ ! -f /workspace/runtime/models-ready ]]; do
  if (( SECONDS >= deadline )); then
    echo "Timed out waiting for model bootstrap" >&2
    exit 1
  fi
  sleep 2
done

cd /opt/auren-worker
exec /opt/auren-worker/.venv/bin/python agent.py start
