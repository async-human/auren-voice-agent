# Artifacts and capability registry

Auren can now turn completed work into private, downloadable files and attach those files to approval-bound Gmail drafts. The capability registry makes every exposed operation, risk level, dependency, and output explicit so missing tool coverage can be detected instead of discovered by a user mid-conversation.

## Supported artifacts

| Tool | Formats | Intended use |
| --- | --- | --- |
| `create_document` | DOCX, PDF, Markdown, HTML, TXT | Reports, briefs, research, letters, and notes |
| `create_spreadsheet` | XLSX, CSV | Tables, comparisons, trackers, and structured exports |
| `create_presentation` | PPTX | Title and content-slide presentations |
| `list_artifacts` | Metadata | Find recent generated files for download or attachment |

Generated files are immutable. Metadata is stored in the database; bytes are written atomically to `ARTIFACT_STORAGE_DIR` and verified against their recorded size and SHA-256 hash on every download or attachment. Spreadsheet strings beginning with formula-control characters are escaped to prevent formula injection.

Browser endpoints require the signed-in user and never accept a user id from the client:

- `GET /v1/artifacts`
- `GET /v1/artifacts/{artifact_id}`
- `GET /v1/artifacts/{artifact_id}/download`

An artifact owned by another account returns `404`, which avoids disclosing whether it exists.

## Gmail attachment flow

`draft_email` and `send_email` accept up to ten `artifact_ids`. Before creating MIME content, the API verifies that each artifact belongs to the acting user, passes its integrity check, and fits within `ARTIFACT_EMAIL_MAX_BYTES` in aggregate.

The safe send lifecycle remains unchanged:

1. Auren creates or updates a real Gmail draft, including attachments.
2. The API reads the exact raw draft back from Gmail and hashes the complete MIME message.
3. The pending action stores the message body encrypted and binds approval to that draft hash.
4. If the draft changes, the old approval is invalid and the user must review the new version.
5. Only `confirm_pending_action` sends the approved draft; ambiguous send responses are never blindly retried.

## Capability metadata

`GET /v1/tools` exposes gateway tools to the voice worker. Authenticated clients can use `GET /v1/capabilities` for the user-facing catalog grouped by domain. Each tool has:

- a stable name, domain, operation, and version;
- a risk level: `read`, `write`, `consequential`, or `destructive`;
- reversibility and parallel-safety flags;
- an optional required connection;
- declared output formats.

Startup fails if a registered tool has no capability metadata or metadata exists for a nonexistent tool. This makes catalog completeness an enforced invariant.

## Production configuration

Set these on the API service:

```dotenv
ARTIFACT_STORAGE_DIR=/data/auren-artifacts
ARTIFACT_MAX_BYTES=10485760
ARTIFACT_EMAIL_MAX_BYTES=20971520
```

On Railway, mount a persistent volume at `/data` before enabling artifacts. The local default is `services/api/.data/artifacts`, which is ignored by Git.

The current filesystem adapter is deliberately isolated behind `app.services.artifacts`. A future S3-compatible adapter can preserve the same ownership, checksum, API, and tool contracts while replacing only the byte-storage implementation.

## Multi-tool report workflow

For a request such as “research this topic, create a report, and email it to me,” the voice agent should:

1. start a visible workflow plan;
2. run independent research calls concurrently where supported;
3. update the workflow only after observable progress;
4. synthesize the result before generating the requested artifact;
5. pass the exact returned artifact id to `draft_email`;
6. wait for the user to approve the exact Gmail draft before sending;
7. complete the workflow only after verified delivery.

The UI presents safe rationale and verified outcomes, not hidden chain-of-thought or private artifact content.
