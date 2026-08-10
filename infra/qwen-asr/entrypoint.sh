#!/usr/bin/env bash
set -euo pipefail

required=(QWEN_ASR_API_KEY)
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

mkdir -p "${HF_HOME}" /workspace/logs

# vLLM reads this variable without placing the secret in the process command
# line. The public RunPod template must still restrict access to the model port.
export VLLM_API_KEY="${QWEN_ASR_API_KEY}"
unset QWEN_ASR_API_KEY

arguments=(
  "${QWEN_ASR_MODEL}"
  --served-model-name "${QWEN_ASR_MODEL}"
  --host 0.0.0.0
  --port "${QWEN_ASR_PORT}"
  --gpu-memory-utilization "${QWEN_ASR_GPU_MEMORY_UTILIZATION}"
  --max-model-len "${QWEN_ASR_MAX_MODEL_LEN}"
  --max-num-seqs "${QWEN_ASR_MAX_NUM_SEQS}"
)

case "${QWEN_ASR_ENFORCE_EAGER,,}" in
  true|1|yes|on) arguments+=(--enforce-eager) ;;
  false|0|no|off) ;;
  *)
    echo "QWEN_ASR_ENFORCE_EAGER must be true or false" >&2
    exit 1
    ;;
esac

gpu_name="$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
echo "Starting ${QWEN_ASR_MODEL} on ${gpu_name}, port ${QWEN_ASR_PORT}"
exec /opt/qwen-asr/.venv/bin/qwen-asr-serve "${arguments[@]}"
