# Client follow-up operator

Use this playbook when the user wants to prepare for a client meeting, capture
commitments, send a recap, follow up on an unanswered message, or turn research
and notes into a client-ready deliverable.

## Outcome

The user finishes with the right context, a reviewed outgoing message or
document, and a durable next step. Success is a verified outcome, not a draft
left in the conversation.

## Workflow

1. Start a visible workflow with concrete outcomes.
2. Resolve the client, meeting, thread, dates, recipients, and connected account
   from Calendar, Gmail, memory, or a short clarification. Never guess identity.
3. Run independent read-only retrieval in parallel: relevant email threads,
   calendar events, notes, memories, and web research when requested.
4. Summarize only evidence that the tools returned. Separate facts, open
   questions, commitments, owners, and dates.
5. Create the requested artifact when the work needs a durable report, brief,
   spreadsheet, or presentation. Verify the returned artifact id.
6. Draft the email with exact recipients, subject, body, and artifact ids.
   Drafting may be automatic; sending always uses the gateway approval flow.
7. After approval, verify the send or calendar result from the tool response.
8. If a response or deadline matters, schedule one precise follow-up job with a
   narrow Gmail query and due time. Never let a background job send or modify
   external data by itself.
9. Complete the workflow only after the final result and next step are visible.

## Guardrails

- Reading and research may run in parallel. Writes wait for all target-resolving
  reads and must run in dependency order.
- Email send, calendar create/update/delete, and destructive actions require the
  platform's explicit approval. Do not treat conversational enthusiasm as
  approval.
- Do not expose private chain-of-thought. Publish plans, tool status, concise
  decision summaries, approval requests, and verified outcomes.
- If a tool is unavailable, identify the missing connection or capability and
  preserve completed work as a draft or artifact instead of claiming success.

## Suggested product metrics

For this workflow, prefer actions that make these measurable: minutes saved,
follow-ups completed on time, approval-to-send success, duplicate-action rate,
and the percentage of workflows completed without manual copy/paste.
