# Outreach Enqueue API

Secure server-side entrypoint for dashboard, n8n, CSV import, or internal API producers.

Flow:

```text
dashboard / n8n / import
  -> supabase/functions/outreach-enqueue
  -> public.enqueue_outreach_dm_job(...)
  -> ig_dm_jobs pending
  -> outreach_session / unfollow_outreach_pipeline
```

The function never writes directly to `ig_dm_jobs` and never calls the sender.

## Runtime

Supabase Edge Function:

```text
supabase/functions/outreach-enqueue/index.ts
```

Routes accepted by the function:

- `POST /outreach/enqueue`
- `POST /outreach/bulk-enqueue`
- Supabase function-local aliases: `POST /enqueue`, `POST /bulk-enqueue`

## Required Environment

```text
SUPABASE_URL=<project url>
SUPABASE_SERVICE_ROLE_KEY=<server-side only>
OUTREACH_ENQUEUE_INTERNAL_API_TOKEN=<shared bearer token for n8n/internal API>
```

Optional:

```text
OUTREACH_ENQUEUE_ALLOWED_ORIGINS=https://dashboard.example.com
OUTREACH_ENQUEUE_ALLOWED_ACCOUNT_IDS=<uuid>,<uuid>   # temporary ownership guard
OUTREACH_ENQUEUE_MAX_BATCH_SIZE=100
OUTREACH_ENQUEUE_MAX_PENDING_PER_ACCOUNT=500
```

`OUTREACH_ENQUEUE_INTERNAL_API_TOKEN` is mandatory. If it is missing, the function returns `503`.

## Remote Secrets

Remote Edge Function secrets must be configured on the Supabase project before
running HTTP validation. Never commit or print the real token value.

```bash
supabase secrets set --project-ref zgafnshkjywfltxgbtzg \
  OUTREACH_ENQUEUE_INTERNAL_API_TOKEN="<shared-bearer-token>" \
  OUTREACH_ENQUEUE_ALLOWED_ACCOUNT_IDS="42c625c2-e761-4100-8a9d-7ae1373de97d" \
  OUTREACH_ENQUEUE_MAX_BATCH_SIZE="100" \
  OUTREACH_ENQUEUE_MAX_PENDING_PER_ACCOUNT="500"
```

Confirm names only:

```bash
supabase secrets list --project-ref zgafnshkjywfltxgbtzg
```

## Single Enqueue

```bash
curl -X POST "$SUPABASE_URL/functions/v1/outreach-enqueue/outreach/enqueue" \
  -H "Authorization: Bearer $OUTREACH_ENQUEUE_INTERNAL_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "account_id": "00000000-0000-4000-8000-000000000000",
    "recipient_username": "muse_europe",
    "source": "dashboard",
    "campaign_id": "11111111-1111-4111-8111-111111111111",
    "message_body": "Bonjour, je voulais vous contacter rapidement.",
    "priority": 0,
    "metadata": {
      "external_request_id": "dashboard-req-123",
      "import_id": "import-20260524-a"
    }
  }'
```

Response:

```json
{
  "ok": true,
  "result": "created",
  "job_id": "...",
  "status": "pending",
  "account_id": "...",
  "recipient_username": "muse_europe",
  "recipient_username_normalized": "muse_europe",
  "campaign_id": "...",
  "source": "dashboard",
  "message_frozen": true
}
```

`result` can be `created` or `duplicate_existing`.

## Bulk Enqueue

```bash
curl -X POST "$SUPABASE_URL/functions/v1/outreach-enqueue/outreach/bulk-enqueue" \
  -H "Authorization: Bearer $OUTREACH_ENQUEUE_INTERNAL_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "account_id": "00000000-0000-4000-8000-000000000000",
    "source": "campaign",
    "campaign_id": "11111111-1111-4111-8111-111111111111",
    "template_id": "22222222-2222-4222-8222-222222222222",
    "recipients": ["user1", "user2", "bad@@"],
    "metadata": {
      "external_request_id": "n8n-run-123",
      "import_id": "csv-20260524-a"
    }
  }'
```

Response:

```json
{
  "ok": false,
  "summary": {
    "accepted": 2,
    "duplicates": 0,
    "rejected": 1
  },
  "results": [
    { "recipient_username": "user1", "ok": true, "result": "created", "job_id": "..." },
    { "recipient_username": "bad@@", "ok": false, "error": "invalid_username" }
  ]
}
```

## Remote Validation

Use the repo validation script for the Entry 1 remote checkpoint. The script
reads secrets only from environment variables and masks the bearer token in
logs.

```bash
export SUPABASE_URL="https://zgafnshkjywfltxgbtzg.supabase.co"
export ENTRY1_BASE_URL="$SUPABASE_URL/functions/v1/outreach-enqueue"
export ENTRY1_ACCOUNT_ID="42c625c2-e761-4100-8a9d-7ae1373de97d"
export OUTREACH_ENQUEUE_INTERNAL_API_TOKEN="<shared-bearer-token>"

./scripts/validate-entry1.sh
```

The script validates:

- single enqueue returns `ok=true`, a `job_id`, `status=pending`, and frozen message text;
- bulk enqueue accepts or deduplicates two valid usernames and rejects `bad@@`;
- missing authorization and wrong bearer token are rejected;
- `welcome_scan`, `metadata.handoff=unfollow`, client-provided `status`, invalid username, and `import_csv` are rejected.

After the script passes, verify rows in `ig_dm_jobs`.

Single:

```sql
SELECT
  id,
  account_id,
  dm_type,
  recipient_username,
  recipient_username_normalized,
  message_body,
  source,
  campaign_id,
  status,
  metadata,
  idempotency_key,
  created_at
FROM public.ig_dm_jobs
WHERE account_id = '42c625c2-e761-4100-8a9d-7ae1373de97d'::uuid
  AND dm_type = 'outreach'
  AND metadata->>'external_request_id' = 'entry1-remote-single-test'
ORDER BY created_at DESC;
```

Bulk:

```sql
SELECT
  id,
  recipient_username,
  status,
  source,
  metadata,
  created_at
FROM public.ig_dm_jobs
WHERE account_id = '42c625c2-e761-4100-8a9d-7ae1373de97d'::uuid
  AND dm_type = 'outreach'
  AND metadata->>'import_id' = 'entry1-remote-bulk-test'
ORDER BY created_at DESC;
```

## Validation

Implemented now:

- bearer-token auth;
- optional account allowlist guard;
- `outreach_enabled=true`;
- username trim / strip `@` / lowercase / obvious invalid-character rejection;
- source allowlist: `n8n`, `dashboard`, `manual`, `campaign`;
- `import_csv` rejected until enum migration;
- forbidden client fields rejected: `dm_type`, `status`, `reserved_by`, `reserved_at`, `started_at`, `finished_at`, `sent_at`, `attempts`, `idempotency_key`;
- dangerous metadata rejected, including `handoff=unfollow`;
- safe metadata whitelist only;
- template active + account ownership + `template_type='outreach'`;
- default template exists and is active when no `message_body` or `template_id` is supplied;
- pending queue max guard;
- batch size guard.

MVP TODO:

- replace `OUTREACH_ENQUEUE_ALLOWED_ACCOUNT_IDS` with real tenant/account ownership;
- check paid package/add-on tables when they exist;
- add `import_csv` enum or map it in a v2 product decision;
- add import/campaign tracking tables;
- add bulk RPC for high-volume imports.
