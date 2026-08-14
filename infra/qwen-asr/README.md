# Qwen3-ASR companion image

This image replaces the interactive `uv`, `nohup`, and manual restart commands
previously used on the second RunPod. Qwen is the container's main process, so
RunPod starts it automatically after every restart, host migration, or template
redeployment.

## Build and publish

Merges to `main` that touch this directory trigger
`.github/workflows/qwen-asr-image.yml`. The workflow publishes immutable and
moving tags:

```text
ghcr.io/OWNER/auren-qwen-asr:sha-<full-commit-sha>
ghcr.io/OWNER/auren-qwen-asr:main
```

Use the immutable SHA tag in production. `main` is convenient only for staging.

To build locally from the repository root:

```bash
docker build \
  --file infra/qwen-asr/Dockerfile \
  --tag auren-qwen-asr:local \
  .
```

## RunPod template

Create a dedicated Pod template with:

1. An RTX A5000 24 GB or larger NVIDIA GPU.
2. Image `ghcr.io/OWNER/auren-qwen-asr:sha-<full-commit-sha>`.
3. GHCR registry authentication with package-read access if the image is private.
4. At least 30 GB container disk.
5. The existing persistent/network volume mounted at `/workspace`.
6. HTTP port `8011` exposed only where the Auren worker can reach it.
7. Environment variables copied from `runpod.env.example`.
8. `QWEN_ASR_API_KEY` backed by a RunPod Secret.
9. An empty container start command so the checked-in `ENTRYPOINT` runs.

Do not enable Jupyter or SSH in the production template unless operationally
required. Do not install packages interactively after deployment; update the
Dockerfile and redeploy an immutable image instead.

If startup fails with `Failed to find C compiler`, the image is missing
`build-essential`. The Dockerfile installs it for Triton's runtime JIT; rebuild
and redeploy rather than patching a live pod.

The first boot downloads roughly 4.4 GB of model files and performs vLLM model
and audio warm-up. Subsequent boots reuse `/workspace` but still need several
minutes to load weights into GPU memory. Automatic startup removes manual work;
it does not remove model initialization time.

## Verification

Inside the Pod:

```bash
curl --fail http://127.0.0.1:8011/health

curl --fail \
  -H "Authorization: Bearer ${QWEN_ASR_API_KEY}" \
  http://127.0.0.1:8011/v1/models
```

From the Auren worker, configure:

```dotenv
STT_PROVIDER=whisper
STT_AVAILABLE_PROVIDERS=whisper,qwen
QWEN_ASR_BASE_URL=https://YOUR-STABLE-QWEN-HOST/v1
QWEN_ASR_HEALTH_URL=https://YOUR-STABLE-QWEN-HOST/health
QWEN_ASR_MODEL=Qwen/Qwen3-ASR-1.7B
QWEN_ASR_LANGUAGE=
QWEN_ASR_USE_REALTIME=false
QWEN_ASR_API_KEY=the-same-secret
```

The raw RunPod proxy hostname can change if a replacement Pod receives a new
Pod ID. Production should put the companion behind a stable private hostname or
gateway rather than embedding a replaceable Pod hostname in the worker.

## Failure behavior

- A missing secret or GPU causes an immediate non-zero exit.
- RunPod restarts the failed container according to the Pod lifecycle.
- Docker health stays `starting` while vLLM loads and becomes healthy only when
  `/health` responds.
- The Auren worker readiness check remains false while Qwen is unavailable.

vLLM bearer authentication covers its OpenAI-compatible routes, but it is not a
complete network security boundary. Restrict the port with private networking,
firewall rules, or an authenticated gateway.
