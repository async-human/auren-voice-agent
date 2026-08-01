# RunPod production deployment

This directory replaces every interactive RunPod installation command with one
immutable GPU image. The image runs four supervised services:

- Speaches/faster-whisper on `127.0.0.1:8000`
- Ollama/Qwen on `127.0.0.1:11434`
- Chatterbox Turbo on `127.0.0.1:8004`
- LiveKit voice worker

Only the aggregate health endpoint binds publicly, on port `9090`. The model
APIs remain loopback-only.

## Pinned components

| Component | Pin |
| --- | --- |
| CUDA runtime | `12.8.1-cudnn-runtime-ubuntu24.04` |
| Speaches | `c78b77d5ce56f8c1042f6bc11209697cca2b2445` (`v0.8.3`) |
| Chatterbox Server | `f0afcc6d01d4424ad72950038dff66646b24bc78` (`v2.0.0`) |
| Chatterbox engine fork | `cc0357396d9c73fc1e6c544ee40bb596020edd09` |
| Ollama | `0.32.5`, archive checksum verified during build |
| LiveKit worker | This repository's locked `uv.lock` |

The A40 is an Ampere GPU. Chatterbox therefore uses its supported CUDA 12.1
PyTorch wheels inside the CUDA 12.8 runtime rather than the Blackwell-specific
PyTorch 2.9 path.

## Build locally

From the repository root:

```bash
docker build \
  --file infra/runpod/Dockerfile \
  --tag ghcr.io/YOUR_GITHUB_USER/auren-runpod:local \
  .
```

The image is large. Use a builder with at least 50 GB of free disk. The provided
GitHub Actions workflow builds and publishes an immutable commit-SHA tag.

## Create the RunPod template

1. Attach an NVIDIA A40 GPU.
2. Set container image to `ghcr.io/OWNER/auren-runpod:<git-sha>`.
3. Allocate at least 50 GB container disk.
4. Attach a network volume of at least 50 GB at `/workspace`.
5. Leave the container start command empty so the image `ENTRYPOINT` runs.
6. Optionally expose HTTP port `9090` for a health monitor. Do not expose ports
   `8000`, `8004`, or `11434`.
7. Add the non-secret variables from `runpod.env.example`.
8. Store the two LiveKit credentials in RunPod Secrets and reference them from
   the template as:

```text
LIVEKIT_API_KEY={{ RUNPOD_SECRET_livekit_worker_api_key }}
LIVEKIT_API_SECRET={{ RUNPOD_SECRET_livekit_worker_api_secret }}
```

The authoritative values should remain in the `/voice-worker` production scope
in Infisical. RunPod Secrets are the runtime mirror, not a second manually
maintained source file.

## First startup

The first startup downloads model weights into `/workspace/models`. This can
take several minutes. Later starts reuse the volume. Check progress with:

```bash
curl -sS http://127.0.0.1:9090/health/live
curl -sS http://127.0.0.1:9090/health/ready | python -m json.tool
supervisorctl -c /etc/supervisor/conf.d/auren.conf status
```

The readiness endpoint becomes HTTP 200 only after:

- Speaches is healthy and the Whisper model is cached
- Ollama has pulled and warmed Qwen
- Chatterbox has loaded successfully
- the LiveKit worker is running

## Updating

Never run `git pull`, `pip install`, `uv sync`, or `ollama pull` manually in a
production pod. Update a pinned version in the Dockerfile, build a new image,
test it in staging, and then point the production template to that exact image
tag or digest. Rollback means selecting the previous image digest.
