# Pluggable speech-to-text providers

Auren uses one OpenAI-compatible speech-to-text contract while allowing the
inference runtime to change independently. A signed-in user can choose a
provider before each call; that validated choice is carried in signed LiveKit
metadata and the worker creates the matching STT client for that session.

| `STT_PROVIDER` | Runtime | Mode used by Auren | Default model |
| --- | --- | --- | --- |
| `whisper` | Speaches / faster-whisper | utterance REST | `Systran/faster-whisper-medium.en` |
| `qwen` | Qwen3-ASR on vLLM | utterance REST | `Qwen/Qwen3-ASR-1.7B` |
| `nemotron` | NVIDIA NeMo-Speech.cpp | native realtime WebSocket | `nvidia/nemotron-3.5-asr-streaming-0.6b` |

Whisper remains the production default. Qwen and Nemotron are opt-in until an
Auren-specific evaluation shows a worthwhile accuracy or latency improvement.
Auren does not load model weights inside the worker: every enabled provider
must have a reachable inference runtime.

## Common configuration

```dotenv
STT_PROVIDER=whisper
STT_AVAILABLE_PROVIDERS=whisper
STT_BASE_URL=http://127.0.0.1:8000/v1
STT_MODEL=Systran/faster-whisper-medium.en
STT_LANGUAGE=en
STT_USE_REALTIME=false
STT_API_KEY=local
```

`STT_PROVIDER` is the worker default and uses the generic `STT_*` endpoint
settings. `STT_AVAILABLE_PROVIDERS` is the allowlist for signed session choices.
For safe rollout, it defaults to only the default provider.

The API independently controls what the browser can see:

```dotenv
STT_DEFAULT_PROVIDER=whisper
STT_AVAILABLE_PROVIDERS=whisper
```

Keep the API and worker allowlists identical. Never advertise a provider until
its private endpoint is configured and tested on the worker.

`STT_BASE_URL` is normalized to end in `/v1`. `STT_HEALTH_URL` is optional and
defaults to the same service's `/health` route. Use it when a gateway exposes
health at a different path.

`FASTER_WHISPER_BASE_URL` and `FASTER_WHISPER_MODEL` remain supported as
backward-compatible Whisper aliases, but new deployments should use the
provider-neutral variables.

Never expose an unprotected model port to the public internet. Use a private
network and set `STT_API_KEY` when the runtime is not loopback-only.

## Whisper profile

The immutable RunPod image bundles Speaches and starts it automatically for the
default profile:

```dotenv
STT_PROVIDER=whisper
STT_BASE_URL=http://127.0.0.1:8000/v1
STT_MODEL=Systran/faster-whisper-medium.en
STT_LANGUAGE=en
STT_USE_REALTIME=false
STT_API_KEY=local
```

Use `Systran/faster-whisper-large-v3` only after checking the shared GPU's VRAM
budget and end-to-end latency.

## Qwen3-ASR profile

Qwen3-ASR should run in its own isolated vLLM runtime. The
[official Qwen3-ASR project](https://github.com/QwenLM/Qwen3-ASR) documents that
the vLLM server
implements OpenAI's `/v1/audio/transcriptions` endpoint, so no Auren-specific
adapter is required.

Start the model service in a dedicated environment or companion deployment:

```bash
vllm serve Qwen/Qwen3-ASR-1.7B \
  --host 0.0.0.0 \
  --port 8000 \
  --api-key replace-with-a-secret
```

For a Qwen-only worker, point the generic settings at the private endpoint:

```dotenv
STT_PROVIDER=qwen
STT_BASE_URL=http://qwen-asr.internal:8000/v1
STT_MODEL=Qwen/Qwen3-ASR-1.7B
# Leave empty for language detection; set hi or en for a forced language.
STT_LANGUAGE=
STT_USE_REALTIME=false
STT_API_KEY=replace-with-a-secret
```

For per-session selection while Whisper remains the default, use the
provider-specific settings instead:

```dotenv
QWEN_ASR_BASE_URL=http://qwen-asr.internal:8000/v1
QWEN_ASR_MODEL=Qwen/Qwen3-ASR-1.7B
QWEN_ASR_LANGUAGE=
QWEN_ASR_USE_REALTIME=false
QWEN_ASR_API_KEY=replace-with-a-secret
```

Qwen's streaming inference API is not the same wire protocol as OpenAI's
Realtime transcription API. Auren therefore uses the supported transcription
REST endpoint and LiveKit's VAD/turn detector for endpointing. The worker fails
fast if `STT_USE_REALTIME=true` is set for Qwen.

## Nemotron 3.5 profile

NVIDIA's [Nemotron 3.5 model card](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)
recommends [NeMo-Speech.cpp](https://github.com/NVIDIA/NeMo-Speech.cpp) as a
lightweight local runtime. It provides both the OpenAI-compatible transcription endpoint and
the realtime event protocol consumed by Auren's LiveKit STT adapter.

Download the official quantized model and run the server:

```bash
hf download nvidia/nemotron-3.5-asr-streaming-0.6b \
  nemotron-3.5-asr-streaming-0.6b.q8_0.gguf \
  --local-dir /workspace/models/stt

export NEMO_SPEECH_HTTP_API_KEY=replace-with-a-secret
nemo-speech serve \
  --config /path/to/auren-voice-agent/infra/stt/nemotron.config.yaml
```

Configure Auren:

```dotenv
STT_PROVIDER=nemotron
STT_BASE_URL=http://nemotron-asr.internal:8080/v1
STT_MODEL=nvidia/nemotron-3.5-asr-streaming-0.6b
STT_LANGUAGE=auto
STT_USE_REALTIME=true
STT_API_KEY=replace-with-a-secret
```

For per-session selection while Whisper remains the default, configure:

```dotenv
NEMOTRON_ASR_BASE_URL=http://nemotron-asr.internal:8080/v1
NEMOTRON_ASR_MODEL=nvidia/nemotron-3.5-asr-streaming-0.6b
NEMOTRON_ASR_LANGUAGE=auto
NEMOTRON_ASR_USE_REALTIME=true
NEMOTRON_ASR_API_KEY=replace-with-a-secret
```

After both companion runtimes pass their smoke tests, enable the selector on
both the API and worker:

```dotenv
STT_AVAILABLE_PROVIDERS=whisper,qwen,nemotron
```

Set `STT_USE_REALTIME=false` to use utterance-level REST during troubleshooting.
The checked-in server configuration uses 160 ms right context as a reasonable
starting point. Benchmark 80, 160, and 320 ms before selecting a production
latency profile.

The NeMo-Speech.cpp runtime is Apache 2.0, while the Nemotron model weights are
governed by NVIDIA's Open Model Development Work License. Complete a license
review before production distribution.

## Deployment and resource isolation

The bundled RunPod image starts Speaches when `STT_PROVIDER=whisper`. Qwen and
Nemotron remain companion or external private services. A session selection
changes the client endpoint; it does not start or stop model servers.

Do not load Whisper, Qwen3-ASR, and Nemotron together on Auren's existing shared
GPU. The pod already hosts the LLM and TTS; multiple ASR runtimes would distort
latency measurements and increase out-of-memory risk. Use one of these patterns:

1. Keep Whisper on the worker pod and run Qwen/Nemotron on isolated companion
   GPUs with private networking.
2. Use the UI selector to run the same scripted utterances through each provider.
3. Keep production's default and fallback on Whisper until the evaluation gate
   is met.

## Verification

The bootstrap process now validates the selected provider in four stages:

1. Provider health endpoint responds.
2. A warm-up transcription succeeds.
3. Chatterbox produces valid, non-silent speech.
4. The selected STT transcribes the synthesized phrase correctly.

The result is stored in `/workspace/runtime/audio-smoke.json` with the provider,
model, STT latency, TTS latency, WAV metrics, and transcript. This validates the
model boundary; a browser microphone test through LiveKit remains required
before promotion.

For provider evaluation, use the same recorded Auren utterances and compare WER,
time to first stable partial, finalization latency, real-time factor, GPU memory,
partial revision rate, hallucinations, Indian English, Hindi, Hinglish, and
background noise. Do not choose a provider from public WER alone.
