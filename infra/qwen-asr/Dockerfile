# syntax=docker/dockerfile:1.7
ARG CUDA_BASE_IMAGE=nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04
FROM ${CUDA_BASE_IMAGE}

LABEL org.opencontainers.image.title="Auren Qwen3-ASR Runtime" \
      org.opencontainers.image.description="Automatically managed Qwen3-ASR vLLM companion runtime"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/tmp/uv-cache \
    HF_HOME=/workspace/models/qwen/huggingface \
    HF_HUB_CACHE=/workspace/models/qwen/huggingface/hub \
    HF_HUB_ENABLE_HF_TRANSFER=0 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    DO_NOT_TRACK=1 \
    CC=/usr/bin/gcc \
    CXX=/usr/bin/g++ \
    VLLM_WORKER_MULTIPROC_METHOD=spawn \
    QWEN_ASR_MODEL=Qwen/Qwen3-ASR-1.7B \
    QWEN_ASR_PORT=8011 \
    QWEN_ASR_GPU_MEMORY_UTILIZATION=0.55 \
    QWEN_ASR_MAX_MODEL_LEN=4096 \
    QWEN_ASR_MAX_NUM_SEQS=8 \
    QWEN_ASR_ENFORCE_EAGER=true

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential ca-certificates curl ffmpeg libsndfile1 procps \
       python3 python3-dev python3-venv tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.0 /uv /uvx /usr/local/bin/

WORKDIR /opt/qwen-asr
COPY infra/qwen-asr/pyproject.toml infra/qwen-asr/uv.lock ./
RUN uv sync --locked --no-dev --compile-bytecode \
    && .venv/bin/python -c \
       "import qwen_asr, torch, vllm; print(qwen_asr.__name__, torch.__version__, vllm.__version__)" \
    && rm -rf /tmp/uv-cache

COPY --chmod=0755 infra/qwen-asr/entrypoint.sh /opt/qwen-asr/entrypoint.sh

EXPOSE 8011

HEALTHCHECK --interval=20s --timeout=5s --start-period=20m --retries=6 \
  CMD curl --fail --silent "http://127.0.0.1:${QWEN_ASR_PORT}/health" >/dev/null || exit 1

ENTRYPOINT ["/usr/bin/tini", "-s", "--", "/opt/qwen-asr/entrypoint.sh"]
