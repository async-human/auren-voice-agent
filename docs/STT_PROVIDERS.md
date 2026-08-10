# Speech-to-text providers

Auren supports two speech-to-text providers behind one OpenAI-compatible
contract. A signed-in user chooses a provider before a call; the API validates
the choice, signs it into LiveKit metadata, and the worker creates the matching
client for that session.

| Provider | Runtime | Auren mode | Default model |
| --- | --- | --- | --- |
| `whisper` | Speaches / faster-whisper | utterance REST | `Systran/faster-whisper-medium.en` |
| `qwen` | Qwen3-ASR / vLLM | utterance REST | `Qwen/Qwen3-ASR-1.7B` |

Whisper remains the safe default. Qwen is opt-in until an Auren-specific
evaluation demonstrates sufficient accuracy, latency, and reliability.

## Shared configuration

The voice worker owns model endpoint credentials:

```dotenv
STT_PROVIDER=whisper
STT_AVAILABLE_PROVIDERS=whisper,qwen

STT_BASE_URL=http://127.0.0.1:8000/v1
STT_MODEL=Systran/faster-whisper-medium.en
STT_LANGUAGE=en
STT_USE_REALTIME=false
STT_API_KEY=local

QWEN_ASR_BASE_URL=https://your-qwen-endpoint/v1
QWEN_ASR_MODEL=Qwen/Qwen3-ASR-1.7B
QWEN_ASR_LANGUAGE=
QWEN_ASR_USE_REALTIME=false
QWEN_ASR_API_KEY=replace-with-a-secret
```

The API controls which choices the browser can see:

```dotenv
STT_DEFAULT_PROVIDER=whisper
STT_AVAILABLE_PROVIDERS=whisper,qwen
```

Keep the API and worker allowlists identical. Never advertise Qwen until its
endpoint is healthy and reachable from the worker. `STT_BASE_URL` values are
normalized to end in `/v1`; the corresponding health URL defaults to `/health`.
Set `QWEN_ASR_HEALTH_URL` only when a gateway exposes health somewhere else.

Whisper and Qwen currently use utterance transcription with LiveKit VAD and
turn endpointing. Set all `*_USE_REALTIME` values to `false`.

## Whisper runtime

The main RunPod worker image bundles Speaches and starts it automatically:

```dotenv
STT_PROVIDER=whisper
STT_BASE_URL=http://127.0.0.1:8000/v1
STT_MODEL=Systran/faster-whisper-medium.en
STT_LANGUAGE=en
STT_USE_REALTIME=false
STT_API_KEY=local
```

Use `Systran/faster-whisper-large-v3` only after measuring VRAM usage and
end-to-end latency on the shared worker GPU.

## Automatically managed Qwen companion

Qwen runs in its own container so its vLLM dependency graph and GPU allocation
remain isolated from the voice worker. The checked-in companion image:

- pins `qwen-asr[vllm]` and its compatible vLLM version;
- launches `qwen-asr-serve` as the container's main process;
- reads the bearer token from a RunPod Secret via `VLLM_API_KEY`;
- caches model weights under `/workspace/models/qwen/huggingface`;
- restarts automatically whenever RunPod starts or replaces the container; and
- reports liveness through `/health` on port `8011`.

The image is built by `.github/workflows/qwen-asr-image.yml` and published as:

```text
ghcr.io/OWNER/auren-qwen-asr:sha-<full-commit-sha>
```

Create a RunPod template using the instructions in
[`infra/qwen-asr/README.md`](../infra/qwen-asr/README.md). Leave the container
start command empty so the image entrypoint runs. An old interactive pod that
used `nohup` is not automatically converted; redeploy it once from the new
template.

## Deployment isolation

Keep Whisper on the main worker GPU and Qwen on the companion GPU. Selecting a
provider changes the endpoint used for a LiveKit session; it does not move model
weights between GPUs.

Do not expose the Qwen port without a network boundary. A vLLM API key protects
OpenAI-compatible model routes, but it is not a replacement for a private
network, firewall, or authenticated gateway. Prefer private networking and
allow traffic only from the Auren worker.

## Verification

After deploying both images:

1. Confirm the Qwen companion returns HTTP 200 from `/health`.
2. Confirm authenticated `/v1/models` lists `Qwen/Qwen3-ASR-1.7B`.
3. Set both worker and API allowlists to `whisper,qwen`.
4. Confirm both choices appear before starting a call.
5. Run the same recorded utterances through both providers.
6. Compare WER, finalization latency, real-time factor, GPU memory,
   hallucinations, Indian English, Hindi, Hinglish, and background noise.

The main worker readiness endpoint checks every enabled provider. If Qwen is
unhealthy, readiness remains false instead of presenting a provider that cannot
complete a call.
