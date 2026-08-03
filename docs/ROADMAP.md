# Auren roadmap

The goal is a personal voice agent that feels like it knows you, talks like a
person, and eventually speaks first. The path there is not one large release. It
is a sequence of phases where each one is independently useful and none of them
can be skipped without weakening the next.

## Principles

- **Trust is the product.** An agent that is right 95% of the time on
  consequential actions costs more to supervise than to replace. Reliability on
  a small surface beats breadth.
- **Safety belongs in the platform, not the prompt.** Anything enforced only by
  instructions is advisory and will eventually fail. Confirmation, scoping, and
  audit live in the gateway.
- **The pod stays a replaceable inference runtime.** Business logic,
  credentials, and user data stay on Railway. This constraint has held so far
  and is worth defending.
- **Ship the smallest slice that changes how Auren feels.** Prefer one capability
  proven end to end over several half-built ones.

## Where we are

Working today: LiveKit voice loop with local STT, LLM, and TTS on RunPod; a
FastAPI tool gateway on Railway with seven tools; reminders and notes persisted
in Postgres; a chat UI with text input; idle shutdown for the GPU pod.

Phases 0 and 1 are complete: verified identity, conversation persistence,
personal memory with consent, and a contextual greeting. One gap still blocks
the proactive feel people expect:

1. **Nothing delivers a reminder.** They are stored with a `due_at` and read back
   on request. No process ever notices that one is due. (Phase 4)

---

## Phase 0 — A durable, verified user (done)

**Goal.** The same person is recognisably the same person tomorrow.

**Why first.** Memory, preferences, proactivity, and per-user OAuth tokens all
key off a stable identity, and the anonymous endpoint was the blocking
dependency rather than a loose end.

**What was built.**

1. A `users` table keyed by our own id, with the provider's subject stored as
   `external_id`. Tools and memory reference the internal id, so changing or
   adding an identity provider later does not orphan anyone's data.
2. Clerk for sign-in. The browser sends its session token as a bearer header and
   the API verifies it offline against the issuer's JWKS, with the key set
   cached and refreshed on rotation. Clerk was chosen over Auth.js because the
   API is a separate Python service and needs to verify identity independently.
3. `user_id` removed from `VoiceTokenRequest` altogether, so a forged id is not
   rejected, it is unrepresentable. `create_voice_session` now requires a real
   id and can no longer fall back to an anonymous one.
4. Rate limiting on the voice token route rekeyed from IP to user.
5. Alembic, with a baseline migration that also discards the unattributable
   `anonymous-` rows. Startup still creates tables outside production so local
   development and tests need no migration step.

**Verified by.** Tests covering a missing token, an expired token, a foreign
issuer, an unknown signing key, a forged `user_id` in the body, and the same
provider subject resolving to the same internal id across sessions.

---

## Phase 1 — Personal: memory with consent (done)

**Goal.** Auren opens knowing who you are and what happened last time.

**Depends on.** Phase 0.

**What was built.**

1. Conversation sessions and turns are buffered in the worker via
   `conversation_item_added`, then flushed to `/v1/memory/sessions/flush` on
   shutdown.
2. `user_profiles` and `memories` tables store a rolling summary and durable
   facts with source session provenance and soft-delete.
3. Distillation runs on the worker against the local LLM while the GPU is warm;
   the gateway only persists the result.
4. Session start fetches `/v1/memory/context` and injects an instructions block
   plus a personalized greeting. Tools: `recall`, `remember`, `forget`.
5. The web UI has a Memory panel to inspect and forget stored facts. Alembic
   migration `0002_personal_memory` owns the schema change.

**Done when.** Auren greets you by name, refers to the previous conversation
unprompted, and a memory you delete stops appearing.

---

## Phase 2 — Natural: latency and turn-taking

**Goal.** It stops feeling like a request-response system.

**Why here.** Naturalness is mostly timing, not wording, and two specific things
in the current implementation work against it.

**Steps.**

1. **Stream synthesis.** `tts_node` currently drains the entire LLM response
   (`async for chunk in text` into a list) before calling Chatterbox once, so
   time-to-first-audio waits for the last token. Chunk on sentence boundaries
   and synthesise incrementally. This is the single largest perceived-latency
   win available and touches one function.
2. **Add semantic turn detection.** The session runs on Silero VAD alone, which
   ends a turn on silence and therefore interrupts people who pause to think.
   LiveKit's turn-detector plugin judges whether an utterance is actually
   finished.
3. **Make the greeting contextual.** `session.say("I'm ready. What can I help
   you with?")` is a fixed string. Once Phase 1 lands, greet from the profile:
   time of day, name, an open thread from last time. Small change, large
   perceptual effect.
4. **Mask tool latency.** A gateway round trip to Railway is seconds of silence.
   Acknowledge briefly before long calls rather than going quiet.
5. **Measure it.** Log end-of-speech to first-audio-out per turn. Naturalness
   regressions are invisible without a number.

**Done when.** Median time to first audio is under a second on a tool-free turn,
and pausing mid-sentence does not hand the turn over.

---

## Phase 3 — Safe action: the confirmation platform

**Goal.** Auren can do things that cost something, safely.

**Why before proactivity.** The safety rail must exist before the first risky
tool, not alongside it.

**Steps.**

1. **Two-phase execution at the gateway.** `ToolSpec.confirmation_required`
   already exists and is unused. Give it teeth: a consequential tool call
   returns a pending action and a token instead of executing, and only an
   explicit confirmation redeems it. Enforced in the gateway, never in the
   prompt.
2. **Preview in the UI, not in audio.** There is no diff in a voice channel.
   Confirming "send the email" by ear means approving text you never read.
   Render the draft in the chat pane and confirm by click or by reading back
   specifics.
3. **Idempotency keys.** Voice is lossy. A misheard "yes", a dropped connection,
   a retry — any of these send the message twice without one.
4. **Audit log.** Every invocation, its arguments, its outcome, and who
   confirmed it. The gateway is already the single chokepoint. "What did you do
   while I was out?" must be answerable.
5. **Undo where the API allows it**, and say plainly when it does not.

**Done when.** A tool marked consequential cannot execute in one shot, even if
the model tries, and the attempt is in the audit log.

---

## Phase 4 — Proactive, within explicit boundaries

**Goal.** Auren speaks first, and you never resent it for doing so.

**Architectural constraint.** Proactivity lives on Railway, not the pod. The
idle watchdog stops the GPU after 30 idle minutes, so a pod-side scheduler would
be asleep exactly when it is needed. Railway is always on and cheap.

**Steps.**

1. **Make reminders fire.** A scheduler that notices `due_at` has passed. This
   is the smallest honest proactivity and it closes gap 3 above.
2. **Escalate deliberately.** Silent, then visible in the UI, then a push
   notification, then waking the pod to speak aloud. Only the last is expensive
   and intrusive; most things should stop at the second rung.
3. **Encode boundaries as data.** Quiet hours, per-category consent, and a cap
   on interruptions per day, stored and editable — not prompt text.
4. **Handle the cold start.** Waking a stopped pod costs minutes. Queue the
   utterance and speak it when the session is live rather than pretending the
   delay is not there. The RunPod start API is the counterpart to the stop call
   in `infra/runpod/scripts/idle_watchdog.py`.

**Done when.** A reminder set by voice arrives on time through the least
intrusive channel that satisfies your stated boundaries.

---

## Phase 5 — Breadth, once the platform is trustworthy

**Goal.** New capabilities become configuration rather than engineering.

**Steps.**

1. **Calendar first, end to end.** OAuth, timezones, recurrence, conflict
   detection, prepare-confirm-execute, idempotency, audit, undo. The point is
   proving the pattern; email is much smaller afterwards.
2. **Then email**, read and draft before send.
3. **MCP servers behind the gateway.** Never in the worker. The moment the pod
   speaks MCP directly it holds third-party credentials, and the separation this
   architecture is built on collapses.
4. **Credential hygiene.** Long-lived OAuth tokens are a different security
   posture from today's stateless read tools: encryption at rest, minimal
   scopes, and a revocation path.

---

## Deliberately not yet

- **A vector database.** At one user and a few hundred memories, a rolling
  summary plus keyword search beats embeddings on latency and debuggability. Add
  retrieval infrastructure when recall quality actually fails.
- **A bigger local model.** Conversation quality is fine. If multi-step tool
  orchestration starts dropping arguments or skipping confirmations, route tool
  planning to a stronger model rather than replacing the model for everything.
- **Serverless inference.** Real once idle periods dominate, but it means
  restructuring the persistent LiveKit worker and accepting cold starts. The
  idle watchdog buys enough time to defer this.
- **Wake words and always-on listening.** Ambient presence is a Phase 4-plus
  idea. It is unpleasant before memory and boundaries exist.
