# Gmail and Google Calendar

Auren keeps Google credentials in the API service. The voice worker receives only
tool results; OAuth access and refresh tokens never run on the GPU worker or in the
browser.

## Production configuration

Enable the Gmail API and Google Calendar API in the Google Cloud project, create a
Web application OAuth client, and register the exact callback URL:

`https://<api-host>/v1/connections/google/callback`

Configure the API service:

```text
GOOGLE_CLIENT_ID=<web client id>
GOOGLE_CLIENT_SECRET=<web client secret>
GOOGLE_REDIRECT_URI=https://<api-host>/v1/connections/google/callback
PUBLIC_APP_URL=https://<web-app-host>
TOKEN_ENCRYPTION_KEY=<independent high-entropy secret>
DEFAULT_TIMEZONE=Asia/Kolkata
```

Run `uv run alembic upgrade head` before deploying the API. After this release,
disconnect and reconnect any existing Google connection so Google grants the new
least-privilege scopes and Auren captures the primary calendar timezone.

Auren requests `gmail.modify`, the narrowest Gmail scope that supports reading,
drafting, sending, and moving messages to Trash. It does not request
`https://mail.google.com/`, so Auren cannot bypass Trash and immediately erase mail
permanently. After this release every existing Google connection must be reconnected
once to grant `gmail.modify`.

The voice session context includes the authenticated sign-in email when the identity
token provides it and the connected Google account email from OAuth. Auren may use a
known address for explicit “email me” or “invite me” requests, but must confirm which
address to use when the two differ and must never invent a recipient.

## Guardrail policy

| Operation | Confirmation |
| --- | --- |
| Search or read Gmail | No; read-only |
| List events or find availability | No; read-only |
| Create/update a Gmail draft | No; reversible and explicitly requested |
| Send email | Always; exact draft version is bound to the approval |
| Move email to Gmail Trash | Always; exact message version is bound to approval |
| Create Calendar event / invite attendees | Always; date is validated first |
| Update Calendar event | Always; event ID and ETag are bound to approval |
| Delete Calendar event | Always; event ID and ETag are bound to approval |

Pending approvals expire after 15 minutes. Revised Gmail drafts supersede older
approvals. Email bodies are encrypted in pending actions and omitted from audit
details. Sends and Calendar inserts are not blindly retried; stable message/event
identifiers are used to verify ambiguous upstream results without duplicating work.
Invalid or past Calendar creates are rejected before an approval is created. Failed or
stale actions leave the pending list immediately after the UI refreshes.

## End-to-end acceptance test

Use a dedicated Google test account and the real conversation UI:

1. Connect Google from **Connect → Google Calendar & Gmail**.
2. Say: **“Read my most recent inbox email.”** Confirm the newest message's sender,
   subject, and body match Gmail.
3. Say: **“What is on my calendar today?”** Confirm the result uses the test
   account's timezone, includes earlier events today, and excludes tomorrow.
4. Say: **“Draft an email to <test-recipient> with subject Auren test and body This
   is a guarded test.”** Confirm a real Gmail draft exists and no message was sent.
5. Say: **“Cancel.”** Confirm the draft remains and Sent is unchanged.
6. Create the draft again, inspect the exact approval preview, and say:
   **“Confirm.”** Confirm the UI moves through awaiting approval to completed and
   exactly one verified message appears in Sent.
7. Ask Auren to create a future Calendar event with a test attendee. Confirm no
   event exists before approval, then approve and verify exactly one event and one
   invitation.
8. Ask Auren to move that event by 30 minutes. Verify the exact event is shown for
   approval, then approve and confirm there is only one updated event.
9. Ask Auren to delete the event. For a recurring event, verify it asks whether to
   delete one occurrence or the series. Approve and verify removal and attendee
   notification.
10. Search for a dedicated test email, ask Auren to delete it, approve the exact
    sender/subject/date preview, and verify the message appears in Gmail Trash rather
    than being permanently erased.
11. Say **“email me a test summary.”** Verify Auren confirms the known account address
    (or asks which address when sign-in and Google emails differ) before drafting.
12. Disconnect Google. Confirm the connection disappears and subsequent Google tool
   requests ask the user to reconnect.
