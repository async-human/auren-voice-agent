# Auren Voice Agent

Deployment-ready separation for an open-source realtime voice agent.

## Runtime architecture

| Runtime | Deploys | Responsibility |
| --- | --- | --- |
| Vercel | Root Next.js app | Browser UI and LiveKit WebRTC client; contains no private credentials |
| Railway | `services/api` (Python, FastAPI) | Authentication boundary, LiveKit token creation, tool gateway, database |
| RunPod | `services/voice-worker` plus GPU model servers | LiveKit agent, faster-whisper STT, Qwen/Ollama LLM, Chatterbox TTS |
| LiveKit Cloud | Managed realtime transport | Rooms, WebRTC media, agent dispatch |

The browser never connects directly to a RunPod model port. Speaches, Ollama,
and Chatterbox should bind to `127.0.0.1` and be consumed only by the voice
worker on the same GPU machine. A LiveKit agent makes an outbound WebSocket
connection, so the worker requires no public inbound port.

## Agent tools

The worker holds no business logic. Each tool is a thin call to the Railway
gateway, which owns the database, credentials, and audit trail, so the GPU pod
stays a replaceable inference runtime.

```text
voice worker  --POST /v1/tools/invoke-->  Railway FastAPI  -->  database / external APIs
```

| Tool | Backing service |
| --- | --- |
| `get_current_time` | Standard library timezone data |
| `get_weather` | Open-Meteo (no API key required) |
| `create_reminder`, `list_reminders` | Application database |
| `save_note`, `search_notes` | Application database |
| `search_web` | Tavily, Brave, or SearXNG when configured; keyless DuckDuckGo fallback otherwise |
| `recall`, `remember`, `forget` | Personal memory tables (with UI inspect/forget) |

Set `TOOL_GATEWAY_BASE_URL` and `TOOL_GATEWAY_TOKEN` on the worker to enable
tools. With the URL unset the agent still runs, just without them.

Add a new tool by writing a `ToolSpec` in `services/api/app/tools/` and
registering it in `registry.py`, then exposing a matching `@function_tool`
wrapper in `services/voice-worker/tools.py`. `ToolSpec.confirmation_required`
is reserved for higher-risk integrations such as calendar and email.

## Local development

Local development runs the frontend and API. End-to-end voice tests use a
separate development LiveKit project and a development RunPod worker.

### 1. Railway API locally

```bash
cd services/api
cp .env.example .env.local
uv sync
uv run uvicorn app.main:app --reload --port 8080
```

The API defaults to a local SQLite file, so no database server is needed for
development. Point `DATABASE_URL` at Postgres for staging and production, where
`uv run alembic upgrade head` applies the schema. Run the tests with
`uv run pytest`.

Set `CLERK_ISSUER` to your Clerk instance so the API can verify browser session
tokens. To work offline without Clerk, leave it unset and set
`DEV_USER_ID=local-developer` instead; that shortcut is ignored in production.

### 2. Vercel frontend locally

From the repository root:

```bash
cp .env.example .env.local
npm install
npm run dev
```

Open <http://localhost:3000>. The frontend calls the API at the value of
`NEXT_PUBLIC_API_URL`; it never receives a LiveKit API secret.

Sign-in uses Clerk, so `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and
`CLERK_SECRET_KEY` need to be set. The browser sends its Clerk session token to
the API, which derives the user from it: the user id is never sent in the
request body.

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
  included Dockerfile. Attach a Postgres instance and set `DATABASE_URL`;
  `postgres://` URLs are rewritten to use `asyncpg` automatically.
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
The phased plan for memory, naturalness, and proactivity is in
[`docs/ROADMAP.md`](docs/ROADMAP.md).

## Secrets

`.env.local` is a local-development convenience only. Production values should
be managed in Infisical and synchronized to Vercel and Railway. The RunPod
container should retrieve its scoped variables using an Infisical machine
identity at startup. Never commit `.env` files.

Rotate any credentials previously pasted into chat, terminal output, or source
files before using this project in production.
