# Auren Voice Agent

Deployment-ready separation for an open-source realtime voice agent.

## Runtime architecture

| Runtime | Deploys | Responsibility |
| --- | --- | --- |
| Vercel | Root Next.js app | Browser UI and LiveKit WebRTC client; contains no private credentials |
| Railway | `services/api` | Authentication boundary, LiveKit token creation, future tools/memory APIs |
| RunPod | `services/voice-worker` plus GPU model servers | LiveKit agent, faster-whisper STT, Qwen/Ollama LLM, Chatterbox TTS |
| LiveKit Cloud | Managed realtime transport | Rooms, WebRTC media, agent dispatch |

The browser never connects directly to a RunPod model port. Speaches, Ollama,
and Chatterbox should bind to `127.0.0.1` and be consumed only by the voice
worker on the same GPU machine. A LiveKit agent makes an outbound WebSocket
connection, so the worker requires no public inbound port.

## Local development

Local development runs the frontend and API. End-to-end voice tests use a
separate development LiveKit project and a development RunPod worker.

### 1. Railway API locally

```bash
cd services/api
cp .env.example .env.local
npm install
npm run dev
```

### 2. Vercel frontend locally

From the repository root:

```bash
cp .env.example .env.local
npm install
npm run dev
```

Open <http://localhost:3000>. The frontend calls the API at the value of
`NEXT_PUBLIC_API_URL`; it never receives a LiveKit API secret.

### 3. Voice worker development

The GPU services and worker should normally run on a development RunPod pod.
For interactive debugging on a suitable Linux GPU host:

```bash
cd services/voice-worker
cp .env.example .env.local
uv sync
uv run python agent.py dev
```

Use `uv run python agent.py start` in a managed production process.

## Deployments

- Connect the repository root to Vercel.
- Create a Railway service rooted at `services/api`; Railway detects the
  included Dockerfile.
- Build the included immutable RunPod GPU image from
  `infra/runpod/Dockerfile`. It contains the worker and pinned versions of all
  three model servers. Do not reinstall packages interactively or depend on
  `/tmp` in production.
- Use a persistent RunPod network volume only for model caches; code and Python
  environments belong in the image.

Detailed boundaries, variables, and rollout steps are in
[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).
The concrete GPU image and RunPod template instructions are in
[`infra/runpod/README.md`](infra/runpod/README.md).

## Secrets

`.env.local` is a local-development convenience only. Production values should
be managed in Infisical and synchronized to Vercel and Railway. The RunPod
container should retrieve its scoped variables using an Infisical machine
identity at startup. Never commit `.env` files.

Rotate any credentials previously pasted into chat, terminal output, or source
files before using this project in production.
