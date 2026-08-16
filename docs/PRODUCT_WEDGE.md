# Product wedge: the client follow-up operator

Auren's first focused job is to close the gap between a client conversation and
the next completed action. The target user is a client-facing professional who
currently searches several systems, writes the same recap twice, attaches a
document, schedules a follow-up, and later checks manually for a reply.

## The painful workflow

Before a meeting, Auren assembles the calendar context, recent email thread,
relevant memories, and requested research. After the meeting, it turns notes
into commitments with owners and dates, creates the requested deliverable,
drafts the recap with the correct attachments, sends only after approval, and
checks later for a reply without autonomously sending anything.

This is a stronger initial business than a generic assistant because it is
frequent, cross-tool, time-sensitive, easy to measure, and costly when missed.

## Initial ideal customer profile

- Independent consultants and small professional-services teams
- Agencies and account managers handling several active clients
- Recruiters and founders running relationship-heavy sales or hiring processes

Start with one segment during discovery; do not build three separate products.

## Activation event

A new user reaches activation after completing this loop once:

1. Connect Google.
2. Ask Auren to prepare for a real client meeting or summarize a real thread.
3. Review and approve a generated follow-up with an optional attachment.
4. See the verified send and a scheduled next step in the execution trace.

## Product scorecard

| Metric | Initial target |
| --- | --- |
| End-to-end workflow completion | at least 80% in staging evals |
| Consequential action without approval | 0 |
| Duplicate sends or calendar writes | 0 |
| Median manual time saved | at least 10 minutes per workflow |
| Weekly activated users completing 3+ workflows | primary retention signal |
| User correction after final approval preview | track and reduce by cohort |

## What not to build yet

Do not add another broad integration until the Gmail, Calendar, document,
approval, scheduling, and recovery paths for this workflow pass repeatable
end-to-end evaluations. Breadth is not product-market fit.
