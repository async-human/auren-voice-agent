# Auren memory implementation plan

## Objective

Build a memory system that lets Auren autonomously retain useful context without
requiring the user to say “remember this,” while keeping every stored item
inspectable, attributable, correctable, and deletable.

The memory system must improve continuity without turning the transcript into an
unbounded profile of the user. Explicit `remember` and `forget` requests remain
supported as high-priority user controls, but they are not the normal write path.

## Architectural decisions

### PostgreSQL is the source of truth

All persistent memory and its provenance live in the Railway PostgreSQL
database. This includes conversation episodes, durable facts, preferences,
learned routines, future commitments, confidence, lifecycle state, and deletion
history.

Local development continues to use SQLite. Features that are specific to
PostgreSQL must have a deterministic lexical fallback so local development and
tests do not require extra infrastructure.

### Vector search is an index, not a second source of truth

Do not introduce a separate vector database initially. When retrieval quality
benchmarks show that lexical and structured retrieval are insufficient, enable
`pgvector` in the existing PostgreSQL database and attach embeddings to SQL
records.

This keeps:

- authorization and tenant filtering in one query boundary;
- deletion and supersession atomic;
- backups and migrations in one system;
- metadata, provenance, and vectors consistent;
- operational cost and failure modes small.

Embeddings can always be rebuilt from canonical SQL content. They must never be
the only copy of a memory.

### The gateway owns memory policy

The voice worker may transcribe and propose memory candidates while the local
model is warm. The FastAPI gateway validates, classifies, deduplicates,
consolidates, persists, retrieves, and deletes memory.

The worker remains a replaceable inference runtime. A model response is a
proposal, not authority to mutate user memory without gateway policy checks.

### Memory categories and storage

#### Working memory

Purpose: hold the current conversation, active goals, unresolved references,
recent tool results, and temporary task state.

Storage:

- primary: in-process `AgentSession` and transcript buffer;
- optional resumability later: a short-lived SQL session snapshot or Redis;
- retention: session lifetime, with a short recovery TTL if snapshots are added.

Working memory is not shown as a durable personal memory and is not embedded.

#### Episodic memory

Purpose: remember what happened in a specific interaction, including “what did
we discuss last time?”

Storage:

- `conversation_sessions` for episode metadata and summaries;
- `conversation_turns` for source dialogue;
- episode topics, outcomes, open loops, and salience stored with the session;
- optional episode embeddings added later for semantic recall.

Episodes are immutable historical records except for redaction/deletion and
derived-summary regeneration.

#### Semantic memory

Purpose: retain stable facts and preferences such as a name, location, ongoing
project, communication preference, or relationship.

Storage:

- typed rows in `memories`;
- evidence links back to one or more source turns/sessions;
- confidence, importance, sensitivity, status, validity interval, and
  supersession metadata;
- optional embeddings added later.

Contradictory facts do not silently coexist as equally active truths. New
evidence either reinforces, updates, or supersedes an existing record.

#### Procedural memory

Purpose: capture recurring user-specific workflows such as “for weekly reports,
summarize blockers first.”

Storage:

- typed `procedural` rows in `memories` during the first implementation;
- structured trigger, steps, and constraints in a JSON payload;
- evidence from repeated behavior;
- version and lifecycle state.

Procedural candidates require stronger evidence than semantic facts. They remain
`candidate` until repeated observations or explicit confirmation promote them.
Procedural memory may personalize tool orchestration, but it cannot modify core
system instructions, permissions, confirmations, or safety policy.

#### Prospective memory

Purpose: represent future intentions, reminders, commitments, and follow-ups.

Storage:

- structured domain tables such as `reminders`, not free-form memory rows;
- optional links from an episode or semantic memory to the domain record;
- status transitions such as pending, completed, cancelled, or expired.

Dates, recurrence, and delivery state must remain queryable fields rather than
text embedded in a vector.

## Canonical memory record

The existing `memories` table evolves into the canonical store for semantic and
procedural memory. Each record needs:

- `memory_type`: `semantic` or `procedural`;
- `status`: `candidate`, `active`, `superseded`, `rejected`, or `deleted`;
- `content`: concise canonical natural-language representation;
- `structured_value`: optional JSON for typed values or procedures;
- `confidence`: model/evidence confidence from 0 to 1;
- `importance`: retrieval and retention priority from 0 to 1;
- `sensitivity`: `normal`, `sensitive`, or `restricted`;
- `source`: `autonomous`, `explicit`, `imported`, or `derived`;
- `valid_from` and `valid_until`;
- `last_confirmed_at`, `last_used_at`, `created_at`, and `updated_at`;
- `superseded_by_id` when a newer memory replaces it.

Provenance must support more than one observation. A `memory_evidence` table
links a memory to source sessions and turns and records whether the evidence
supports or contradicts it.

A `memory_events` audit table records creation, promotion, update,
supersession, rejection, and deletion. User-facing deletion hides the item
immediately; hard-deletion policy is handled separately.

## Autonomous write pipeline

### 1. Capture

Capture finalized user and assistant turns with stable sequence numbers. Tool
results that materially affect the conversation should be represented without
storing secrets or oversized payloads.

### 2. Extract candidates

At session end, ask the model for structured candidates:

- episode summary, topics, decisions, and unresolved threads;
- semantic facts and preferences;
- possible procedures or routines;
- prospective intents that should map to a reminder/task;
- confidence, importance, sensitivity, and exact source turn references.

The prompt must distinguish user statements from assistant suggestions and
model inference. Unsupported inferences are discarded.

### 3. Validate with deterministic policy

The gateway rejects candidates that:

- have no valid source turn;
- were stated only by the assistant;
- contain credentials, access tokens, passwords, or authentication material;
- are transient chit-chat with no future utility;
- exceed configured limits;
- attempt to change permissions, safety rules, or system behavior.

Restricted categories are not autonomously persisted. Sensitive categories use
an explicit policy and may require confirmation before activation.

### 4. Resolve and consolidate

For each accepted candidate:

1. normalize the content and structured value;
2. retrieve likely existing matches for the same user and type;
3. decide `create`, `reinforce`, `update`, `supersede`, or `ignore`;
4. attach evidence;
5. write the record and audit event in one transaction.

Repeated evidence increases confidence. Contradictory evidence lowers
confidence or supersedes the old record. Exact-string deduplication alone is not
sufficient.

### 5. Promote

Explicit user requests can create active memories immediately after policy
validation. Autonomous semantic facts become active above configured confidence
and importance thresholds. Procedural memories require repeated evidence or
confirmation.

## Retrieval pipeline

Retrieval is query-driven and budgeted. Do not inject every memory at session
start.

### Session start

Load only:

- identity and a small set of high-confidence profile facts;
- the latest episode summary and unresolved thread;
- a few high-importance preferences relevant to conversation style.

### Per-turn retrieval

When a request depends on personal history:

1. classify the required memory types;
2. apply user, status, sensitivity, and validity filters;
3. retrieve structured/exact matches;
4. retrieve lexical matches;
5. later, add vector matches when enabled;
6. rerank by relevance, confidence, importance, recency, and corroboration;
7. return a small context bundle with provenance.

The model must be told when retrieved information is uncertain or conflicting.
It must not present an inference as a user-confirmed fact.

### Initial retrieval implementation

Use SQL indexes and deterministic lexical search first:

- direct lookup for identity and structured fields;
- recent-session queries for episodic recall;
- token/keyword matching for semantic memory;
- PostgreSQL full-text search when deployed;
- SQLite-compatible matching in development.

Add pgvector only after a recall evaluation set demonstrates a measurable gap.

## Privacy, consent, and user control

- The Memory UI shows autonomous and explicit memories, type, source, confidence,
  and status.
- Users can delete, correct, confirm, or reject a memory.
- The UI explains why an item was stored and links it to a human-readable source
  episode when available.
- “Forget” applies across canonical records, evidence-derived context, and
  embeddings.
- Restricted secrets are never stored as memory.
- Retention settings and category-level opt-outs are encoded as data, not only
  prompt instructions.
- Logs must not contain transcript bodies, secrets, or unredacted sensitive
  candidates.

## Evaluation

Create a deterministic memory evaluation suite covering:

- useful facts saved without an explicit “remember” command;
- one-off chit-chat not saved;
- assistant statements not mistaken for user facts;
- last-conversation recall with correct episode selection;
- paraphrased recall;
- duplicate observations reinforcing one record;
- changed facts superseding old facts;
- uncertain or contradictory facts surfaced honestly;
- sensitive and restricted data rejected or gated;
- deleted memories absent from startup and per-turn context;
- cross-user isolation;
- failed extraction or embedding services never breaking call shutdown.

Track precision before recall. An agent that occasionally forgets is safer than
one that confidently invents or over-collects.

## Delivery sequence

### Phase M1 — Typed persistence foundation

- Add typed lifecycle, scoring, sensitivity, source, validity, and supersession
  fields to `memories`.
- Add `memory_evidence` and `memory_events`.
- Extend episode metadata for topics, outcomes, and open threads.
- Preserve compatibility with existing memories by migrating them as active
  semantic memories; infer `autonomous` source when session provenance exists
  and `explicit` otherwise.
- Extend API schemas and tests without changing existing user-visible behavior.

Done when old memories still load and new typed records round-trip through both
SQLite tests and PostgreSQL migrations.

Status: implemented.

### Phase M2 — Consent and extraction policy

- Add gateway-owned user memory settings with a global autonomous-extraction
  toggle and per-category controls.
- Default new autonomous semantic and procedural extraction to disabled until
  the user grants one-time informed consent.
- Add a minimal API and UI control to inspect and change those settings.
- Implement deterministic allow/deny rules for credentials, restricted data,
  assistant-only claims, unsupported inference, and oversized candidates.
- Continue saving the episodic transcript and summary only under the existing
  conversation-history policy; make that retention independently controllable.

Done when the gateway rejects autonomous writes without consent, category
settings are enforced independently of model instructions, and policy tests
cover restricted content.

### Phase M3 — Autonomous extraction

- Replace the flat distillation response with typed candidates and source turn
  references.
- Validate candidates in the gateway.
- Persist accepted candidates transactionally with evidence and audit events.
- Keep explicit `remember` as an override.

Done when useful facts are captured without a command and unsupported or
restricted candidates are rejected in tests.

### Phase M4 — Consolidation and contradiction handling

- Implement match, reinforce, update, supersede, and ignore decisions.
- Require repeated evidence for procedural promotion.
- Add correction and confirmation endpoints.

Done when repeated and contradictory observations produce one explainable
active truth rather than duplicates.

### Phase M5 — Relevance-based retrieval

- Replace broad startup injection with a retrieval budget.
- Add typed per-turn recall and ranking.
- Add PostgreSQL full-text search with SQLite fallback.
- Build an offline recall-quality evaluation set.

Done when the agent recalls relevant history without unrelated memory leakage or
prompt bloat.

### Phase M6 — Full user controls and retention

- Upgrade the Memory UI with type, status, provenance, correction, confirmation,
  and category controls.
- Add retention and stale-memory review policies.
- Implement complete deletion across records, evidence, events as permitted by
  audit policy, and embeddings.

Done when users can understand and control every active memory.

### Phase M7 — Optional hybrid vector retrieval

- Enable pgvector on Railway PostgreSQL.
- Add embedding model/version metadata and a rebuildable embedding column or
  companion table.
- Generate embeddings asynchronously and implement hybrid lexical/vector
  ranking.
- Compare against the M5 evaluation baseline.

Done only when hybrid retrieval improves measured recall enough to justify its
latency and operational cost. Otherwise, keep vectors disabled.

## Immediate implementation scope

Phase M1 is complete. Implement Phase M2 next so autonomous extraction cannot
ship before enforceable consent and restricted-data policy. Do not add vector
infrastructure before retrieval is measurable.
