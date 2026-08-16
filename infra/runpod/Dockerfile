# syntax=docker/dockerfile:1.7
ARG CUDA_BASE_IMAGE=nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04
FROM ${CUDA_BASE_IMAGE}

ARG SPEACHES_COMMIT=c78b77d5ce56f8c1042f6bc11209697cca2b2445
ARG CHATTERBOX_SERVER_COMMIT=f0afcc6d01d4424ad72950038dff66646b24bc78
ARG CHATTERBOX_ENGINE_COMMIT=cc0357396d9c73fc1e6c544ee40bb596020edd09
ARG OLLAMA_VERSION=0.32.5
ARG OLLAMA_SHA256=f7d6bdbcf71b83aa8670c4e7dc4b6936c0952fcf8b114eaf6a11cbadb9684214

LABEL org.opencontainers.image.title="Auren GPU Voice Runtime" \
      org.opencontainers.image.description="Pinned Speaches, Ollama, Chatterbox, and LiveKit worker runtime"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/auren-worker \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/tmp/uv-cache \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    AUREN_ENV=production \
    AUREN_HEALTH_PORT=9090 \
    AUREN_BOOT_TIMEOUT_SECONDS=1800 \
    HF_HUB_ENABLE_HF_TRANSFER=0 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    DO_NOT_TRACK=1 \
    GRADIO_ANALYTICS_ENABLED=False \
    ENABLE_UI=false \
    WHISPER__INFERENCE_DEVICE=cuda \
    WHISPER__COMPUTE_TYPE=float16 \
    STT_MODEL_TTL=-1 \
    STT_PRELOAD=true \
    STT_PROVIDER=whisper \
    FASTER_WHISPER_BASE_URL=http://127.0.0.1:8000/v1 \
    FASTER_WHISPER_MODEL=Systran/faster-whisper-medium.en \
    OLLAMA_HOST=127.0.0.1:11434 \
    OLLAMA_MODELS=/workspace/models/ollama \
    OLLAMA_KEEP_ALIVE=-1 \
    OLLAMA_FLASH_ATTENTION=1 \
    OLLAMA_MAX_LOADED_MODELS=1 \
    VISION_PRELOAD=false \
    LLM_BASE_URL=http://127.0.0.1:11434/v1 \
    LLM_MODEL=qwen3:8b \
    CHATTERBOX_BASE_URL=http://127.0.0.1:8004/v1 \
    CHATTERBOX_MODEL=chatterbox-turbo \
    CHATTERBOX_VOICE=Olivia.wav \
    CHATTERBOX_TIMEOUT_SECONDS=30 \
    CHATTERBOX_MAX_SEGMENT_CHARS=120 \
    CHATTERBOX_RETRY_ATTEMPTS=1 \
    LLM_DISABLE_THINKING=true \
    LLM_MAX_COMPLETION_TOKENS=220 \
    AUREN_AUDIO_SMOKE_TIMEOUT_SECONDS=180 \
    HF_HOME=/workspace/models/huggingface \
    TTS_BF16=auto

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential ca-certificates curl ffmpeg git libsndfile1 procps python3-minimal \
       supervisor tini zstd \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash --uid 10001 auren

COPY --from=ghcr.io/astral-sh/uv:0.12.0 /uv /uvx /usr/local/bin/

# Speaches STT: stable release pinned to an immutable commit.
RUN git clone https://github.com/speaches-ai/speaches.git /opt/speaches \
    && cd /opt/speaches \
    && git checkout "${SPEACHES_COMMIT}" \
    && test "$(git rev-parse HEAD)" = "${SPEACHES_COMMIT}" \
    && uv sync --frozen --compile-bytecode --extra ui \
    && rm -rf .git /tmp/uv-cache \
    && chown -R auren:auren /opt/speaches

# Chatterbox server: Python 3.10 and the Ampere-compatible CUDA 12.1 wheels.
# PyTorch's CUDA wheel index intentionally supplements PyPI. uv's secure
# first-index default can otherwise select that index for an NVIDIA transitive
# dependency that only has the required version on PyPI, so this pinned
# environment advances to the next index only when no compatible version exists.
COPY infra/runpod/requirements-chatterbox.txt /tmp/requirements-chatterbox.txt
RUN git clone https://github.com/devnen/Chatterbox-TTS-Server.git /opt/chatterbox \
    && cd /opt/chatterbox \
    && git checkout "${CHATTERBOX_SERVER_COMMIT}" \
    && test "$(git rev-parse HEAD)" = "${CHATTERBOX_SERVER_COMMIT}" \
    && uv venv --python 3.10 .venv \
    && uv pip install --python .venv/bin/python \
       --index-strategy unsafe-first-match \
       -r /tmp/requirements-chatterbox.txt \
    && uv pip install --python .venv/bin/python --no-deps \
       "git+https://github.com/devnen/chatterbox-v2.git@${CHATTERBOX_ENGINE_COMMIT}" \
       s3tokenizer==0.3.0 onnx==1.16.0 \
    && uv pip install --python .venv/bin/python "protobuf>=4.25.0" \
    && rm -rf .git /tmp/requirements-chatterbox.txt /tmp/uv-cache \
    && chown -R auren:auren /opt/chatterbox

COPY --chown=auren:auren infra/runpod/chatterbox.config.yaml /opt/chatterbox/config.yaml

# Ollama: release archive and checksum are both pinned.
RUN curl --fail --location --retry 5 \
      "https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}/ollama-linux-amd64.tar.zst" \
      --output /tmp/ollama.tar.zst \
    && echo "${OLLAMA_SHA256}  /tmp/ollama.tar.zst" | sha256sum --check --strict \
    && tar --use-compress-program=unzstd -xf /tmp/ollama.tar.zst -C /usr/local \
    && rm /tmp/ollama.tar.zst \
    && ollama --version

# LiveKit worker: locked dependencies from this repository.
WORKDIR /opt/auren-worker
COPY services/voice-worker/pyproject.toml services/voice-worker/uv.lock ./
RUN uv sync --locked --no-dev --compile-bytecode \
    && rm -rf /tmp/uv-cache \
    && chown -R auren:auren /opt/auren-worker
COPY --chown=auren:auren \
    services/voice-worker/agent.py \
    services/voice-worker/tools.py \
    services/voice-worker/session_state.py \
    services/voice-worker/memory.py \
    services/voice-worker/screen_reader.py \
    services/voice-worker/stt_provider.py \
    services/voice-worker/stt_settings.py \
    services/voice-worker/http_headers.py \
    services/voice-worker/skills.py \
    ./
COPY --chown=auren:auren services/voice-worker/skill_specs ./skill_specs

COPY infra/runpod/supervisord.conf /etc/supervisor/conf.d/auren.conf
COPY --chown=auren:auren --chmod=0755 infra/runpod/scripts /opt/auren/bin

EXPOSE 9090

HEALTHCHECK --interval=30s --timeout=10s --start-period=30m --retries=3 \
  CMD curl --fail --silent http://127.0.0.1:9090/health/ready >/dev/null || exit 1

ENTRYPOINT ["/opt/auren/bin/entrypoint.sh"]
