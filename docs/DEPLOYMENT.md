# Auren deployment standard

## 1. Workload placement

### Local developer machine

Run only:

- Next.js frontend
- Railway API during backend development
- unit, integration, lint, and build checks
- mocked voice-provider adapters where practical

Do not treat a laptop process as a production dependency. A developer without
an NVIDIA GPU should use the development RunPod environment for end-to-end
voice tests.

### Vercel

Run only the Next.js browser application. Its sole runtime configuration is
`NEXT_PUBLIC_API_URL`. LiveKit API credentials, database credentials, model
credentials, and service-to-service secrets must not exist in the frontend
project.

### Railway

Run the public application API (Python, FastAPI):

- authentication and authorization
- LiveKit participant-token creation
- the tool gateway the voice worker calls
- user profile, tools, memory, reminders, and task APIs
- PostgreSQL and migrations
- rate limiting, audit events, and webhooks

The included MVP endpoint is anonymous and rate-limited. Add real user
authentication before a public launch and derive `userId` from the verified
session rather than accepting it from the browser.

Tool routes (`/v1/tools`) are authenticated with the `X-Auren-Service-Token`
shared secret and refuse to serve at all in production when
`TOOL_GATEWAY_TOKEN` is unset. Tables are created on startup; move to Alembic
once the data model starts changing.

### RunPod

Run the latency-sensitive GPU path together:

- LiveKit voice worker
- Speaches/faster-whisper STT
- Ollama/Qwen LLM while self-hosting the LLM
- Chatterbox Turbo TTS

Bind all three model APIs to loopback. The LiveKit worker connects outbound to
LiveKit Cloud. Tools and memory are not implemented on the pod: the worker calls
the Railway tool gateway over HTTPS with a scoped service token, so credentials
and user data never reach the GPU host.

Use an immutable, versioned Docker image. Model weights belong on a persistent
network volume, while application code and dependencies belong in the image.
Use a supervisor with restart policies and a health aggregator if all GPU
processes share one Pod. Pin every dependency and model revision.

This repository implements that contract in `infra/runpod`: the Dockerfile,
supervisor process definitions, model bootstrap, health server, idle watchdog,
and image build workflow are versioned alongside the worker.

RunPod cannot stop a Pod on low GPU utilisation, and utilisation alone would be
wrong here anyway, since the GPU idles while a user is speaking. The
`idle-watchdog` process stops the Pod through RunPod's API only after no LiveKit
sessions, completed bootstrap, and sub-threshold GPU all hold for the full idle
window, past a startup grace period. See `infra/runpod/README.md` for the
policy and its cost caveats.

## 2. Environments

Create three isolated environments:

| Environment | Git trigger | LiveKit | RunPod |
| --- | --- | --- | --- |
| Development | feature branches/local | Separate development project | On-demand development pod |
| Staging | `staging` branch | Separate staging project | On-demand staging pod/template |
| Production | `main` or version tag | Separate production project | Production pod from immutable image |

Never allow preview or staging deployments to inherit production credentials.

## 3. Central configuration

Use one Infisical project with `dev`, `staging`, and `prod` environments and
these paths:

```text
/web
  NEXT_PUBLIC_API_URL

/api
  AUREN_ENV
  CORS_ORIGINS
  LIVEKIT_URL
  LIVEKIT_API_KEY
  LIVEKIT_API_SECRET
  LIVEKIT_AGENT_NAME
  DATABASE_URL
  TOOL_GATEWAY_TOKEN
  DEFAULT_TIMEZONE
  WEB_SEARCH_PROVIDER          # plus the matching provider API key

/voice-worker
  AUREN_ENV
  LIVEKIT_URL
  LIVEKIT_API_KEY
  LIVEKIT_API_SECRET
  LIVEKIT_AGENT_NAME
  TOOL_GATEWAY_BASE_URL
  TOOL_GATEWAY_TOKEN
  FASTER_WHISPER_BASE_URL
  FASTER_WHISPER_MODEL
  LLM_BASE_URL
  LLM_MODEL
  CHATTERBOX_BASE_URL
  CHATTERBOX_MODEL
  CHATTERBOX_VOICE
  RUNPOD_API_KEY               # Pod stop permission, for the idle watchdog
  AUTO_STOP_ENABLED
  AUTO_STOP_IDLE_MINUTES
  AUTO_STOP_GPU_THRESHOLD
  AUTO_STOP_STARTUP_GRACE_MINUTES
```

Configure Infisical Secret Syncs for `/web` to Vercel and `/api` to Railway.
Use an Infisical machine identity for `/voice-worker`; keep only that bootstrap
identity in RunPod Secrets. Restart or redeploy a service after rotating a
value so the running process receives the new environment.

Locally, start processes through Infisical instead of maintaining secret files:

```bash
infisical run --env=dev --path=/api -- uv run uvicorn app.main:app --reload --port 8080
infisical run --env=dev --path=/voice-worker -- uv run python agent.py dev
```

The exact project is selected by the local Infisical CLI configuration. A
`.env.local` file remains supported for initial setup and offline development,
but it is not the source of truth.

## 4. Credential boundaries

- Use different LiveKit key pairs for the Railway token service and RunPod
  worker, even within the same LiveKit project.
- Never prefix a secret with `NEXT_PUBLIC_`.
- Never send a RunPod or Infisical credential to the browser.
- Use a separate service credential for RunPod-to-Railway calls.
- Rotate credentials before revoking the old value to avoid downtime.
- Do not log access tokens, API secrets, prompts containing sensitive user data,
  or raw environment values.

## 5. CI/CD policy

Pull requests must run:

```bash
npm ci
npm run build
cd services/api && uv sync --locked && uv run pytest
cd services/voice-worker && uv sync --locked && uv run python -m py_compile agent.py tools.py
```

Use immutable image tags such as the Git commit SHA for RunPod. Promote the
same image from staging to production; do not rebuild it between environments.
Keep database migrations as a separate Railway release step when persistence
is introduced.

## 6. Production readiness gates

Before public traffic:

- authenticated token endpoint
- exact CORS allowlist
- API and session rate limits
- structured logs with request/session IDs
- health checks for API, worker, STT, LLM, and TTS
- restart policies and startup probes
- error and latency monitoring
- credential rotation procedure
- backups and migration rollback for PostgreSQL
- load test for concurrent calls and GPU memory usage
