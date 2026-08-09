# Execution visibility

Auren exposes a structured, user-facing execution trace for every tool call. The
trace explains what capability was selected, why that step is useful, the safe
inputs, lifecycle status, elapsed time, approval state, and verified result.

The trace deliberately does **not** expose private model chain-of-thought. Decision
summaries are short, deterministic explanations generated from the selected tool and
safe arguments. Email bodies, memory contents, access tokens, raw provider payloads,
and internal approval snapshots are never published over the LiveKit activity channel.

## Lifecycle

Tool events use one of these states:

- `started`
- `awaiting_approval`
- `completed`
- `cancelled`
- `failed`

Every event includes `tool`, `invocationId`, `status`, `displayName`, and
`decisionSummary`. It may also include `inputSummary`, `resultSummary`, `durationMs`,
`actionId`, and workflow fields.

## Workflow progress

Tasks requiring multiple tool calls begin with `start_workflow`. Its plan is shown in
the UI immediately. `current_step` is the number of completed plan steps: zero means
the first step is active, one means the first is complete and the second is active,
and so on. `complete_workflow` marks all steps complete only after the final outcome
has been verified.

Independent read-only steps may execute concurrently. Dependent steps run in order,
and consequential writes must wait for target resolution and user approval.

## Privacy rules

- Never publish credentials, access tokens, refresh tokens, service tokens, or OAuth
  authorization headers.
- Never publish full email/document bodies or protected approval arguments.
- Show recipient and subject for email actions, but keep content in the approval UI.
- Describe resource targeting without exposing opaque Gmail or Calendar IDs.
- Truncate public summaries so activity packets stay compact and readable.
- Activity telemetry must never be allowed to break tool execution.

