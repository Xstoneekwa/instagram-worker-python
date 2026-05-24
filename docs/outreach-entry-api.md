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
OUTREACH_ENQUEUE_ALLOWED_ACCOUNT_IDS=<uuid>,<uuid>   # temporary ops fallback only
OUTREACH_ENQUEUE_ALLOWLIST_FALLBACK_ENABLED=false
OUTREACH_ENQUEUE_MAX_BATCH_SIZE=100
OUTREACH_ENQUEUE_MAX_PENDING_PER_ACCOUNT=500
```

`OUTREACH_ENQUEUE_INTERNAL_API_TOKEN` authenticates internal producers such as
n8n or a dashboard backend. Client JWT requests can also be accepted, but they
must pass the database ownership and entitlement helper.

`OUTREACH_ENQUEUE_ALLOWED_ACCOUNT_IDS` was the Entry 1 MVP guard. In Entry 2A it
is not the production authorization model. It is only used when
`OUTREACH_ENQUEUE_ALLOWLIST_FALLBACK_ENABLED=true`, and only for internal-token
requests. Keep that fallback disabled in production after ownership rows are
seeded.

## Entry 2A Ownership / Entitlement Model

Entry 2A adds a multi-client gate before the Edge Function calls
`public.enqueue_outreach_dm_job(...)`:

```text
producer auth
  -> account_id belongs to an active client
  -> active outreach entitlement for that client
  -> ig_account_dm_settings.outreach_enabled=true
  -> enqueue_outreach_dm_job(...)
```

Tables:

- `clients`
- `client_users`
- `client_instagram_accounts`
- `client_entitlements`

Helpers:

- `public.client_can_enqueue_outreach(auth_user_id, account_id)` for future
  client JWT flows.
- `public.client_account_has_outreach_entitlement(account_id)` for internal
  producer flows authenticated by `OUTREACH_ENQUEUE_INTERNAL_API_TOKEN`.

The Edge Function still never reads or exposes `ig_accounts` directly to a
dashboard client. `ig_accounts` may contain operationally sensitive fields such
as credentials or device identifiers on the live project. Client dashboard reads
must use filtered views/endpoints in a later Entry 2C patch.

Entry 2A also revokes direct `authenticated` execution of
`public.enqueue_outreach_dm_job(...)`. Producers should go through the Edge
Function so ownership, entitlement, metadata, source, and forbidden-field checks
are enforced consistently.

Credentials/password update, auto-login, provisioning, device/clone/timeslot
assignment, campaigns/imports, `import_csv`, admin cancel/requeue, and
Slack/Discord alerting are intentionally out of scope for Entry 2A.

## Entry 2B Account-Scoped Subscriptions

Entry 2B adds the product/billing layer that distinguishes `full_cycle` from
`outreach_only` without changing worker flows or runtime quotas.

Tables:

- `client_subscriptions`
  - product contract for one client;
  - `subscription_type in ('full_cycle', 'outreach_only')`;
  - lifecycle status only (`active`, `paused`, `cancelled`, `expired`);
  - no device assignment, credentials, package quotas, or runtime settings.
- `client_subscription_accounts`
  - links a subscription to one or more `client_instagram_accounts`;
  - makes subscriptions account-scoped so one client can run different packages
    on different Instagram accounts.
- `client_subscription_modules`
  - modules included in or added onto the subscription;
  - `feature_code in ('welcome', 'follow', 'unfollow', 'outreach')`;
  - `outreach_only` subscriptions may only enable `outreach`.

Runtime authorization remains in `client_entitlements`. Entry 2B adds nullable
account/source columns so future synced entitlements can be account-scoped:

- `account_id`
- `source_subscription_id`
- `source_subscription_account_id`
- `source_subscription_module_id`

`account_id IS NULL` keeps Entry 2A client-wide entitlement behavior for existing
rows. Account-scoped rows apply only to that Instagram account. The Outreach
helpers accept either form:

```text
client-wide entitlement: client_id + feature_code
account-scoped entitlement: client_id + account_id + feature_code
```

The migration also adds
`public.sync_client_subscription_entitlements(subscription_id)`, an explicit
service-role/admin function. It is intentionally **not** a trigger. Admin/backend
code should call it after changing a subscription, subscription account, or
module. This avoids silent package changes overwriting runtime settings.

The sync mapping is intentional:

- `client_subscription_modules.entitlement_type='included'` becomes
  `client_entitlements.entitlement_type='bundle'`.
- `client_subscription_modules.entitlement_type='addon'` remains
  `client_entitlements.entitlement_type='addon'`.

Product/runtime separation:

- `client_subscriptions` / `client_subscription_modules` = billing/product.
- `client_entitlements` = runtime authorization effective for APIs/helpers.
- `ig_account_dm_settings`, `ig_account_unfollow_settings`,
  `ig_account_follow_settings`, `ig_account_dm_counters` = runtime settings and
  counters.

Entry 2B deliberately does not freeze package numbers such as follow/unfollow
daily limits or Welcome DM limits. The initial Outreach runtime baseline remains
the existing account DM settings/runtime decision (for example, current
`outreach_per_session_limit` defaults), and full package settings are deferred to
the later production readiness settings freeze.

Agency/multi-account classification is also deferred. The account-scoped model
supports clients with multiple Instagram accounts now; a later admin/dashboard
patch can add a safe `client_type`/agency override or computed classification
without changing the subscription-account relationship.

Future agency classification roadmap:

- compute agency when `count(client_instagram_accounts) > 1`;
- allow an admin override;
- show agency status in the admin dashboard;
- support multi-account client dashboard views;
- add agency-specific billing/permissions if needed;
- allow virtual assistants to filter agency clients.

Premium AI modules are also future scope. Entry 2B does not add
`ai_comment` or `ai_targeting` as runtime `feature_code` values because no
backend flows exist yet. Do not expose these modules as operational until their
workers, queues, safety controls, quotas, and dashboards exist.

Future Premium AI roadmap:

- AI targeting settings and source/scoring;
- AI comment prompts/settings;
- AI comment queue/jobs;
- quotas/counters;
- dashboard client config;
- admin monitoring;
- safety review;
- runtime worker implementation.

Future dashboard/API surface (not implemented in Entry 2B):

- Client: `GET /client/subscription`
- Client: `GET /client/modules`
- Client: `GET /client/limits-visible`
- Admin: `GET /admin/subscriptions`
- Admin: `POST /admin/subscriptions`
- Admin: `PATCH /admin/subscriptions/:id`
- Admin: `POST /admin/subscriptions/:id/modules`
- Admin: `POST /admin/subscriptions/:id/sync-entitlements`

## Entry 2C Device / Clone / Assignment Model

Entry 2C adds the capacity and assignment layer for phones, clones/app profiles,
and account-scoped subscription assignments. It does not change the worker
runtime, Edge Functions, credentials, auto-login, provisioning, campaigns,
imports, or session window guards.

Production target:

- development/test may use Android Studio emulators such as `emulator-5554`;
- production uses real Android phones connected by USB hubs to a Mac worker host;
- phones may become `offline`, `unauthorized`, `maintenance`, or `disabled`;
- ADB serials are operational routing hints, not the only durable identity.

Tables:

- `phone_devices`
  - admin-only device inventory;
  - `device_kind in ('emulator', 'physical_phone')`;
  - `pool_type in ('full_cycle', 'outreach_only', 'shared')`;
  - `status in ('available', 'reserved', 'active', 'maintenance', 'offline', 'unauthorized', 'disabled')`;
  - ops fields: `adb_serial`, `device_udid`, `host_machine`, `hub_label`, `hub_port`, `status_reason`;
  - `max_clones` stores capacity; do not assume a global 4-clone limit.
- `phone_clones`
  - admin-only clone/app-profile inventory for a device;
  - supports emulator clones and real-phone app instances/profiles;
  - `current_account_id` is denormalized/admin-only; the source of truth is
    `account_assignments`.
- `account_assignments`
  - links `client_subscriptions` / `client_subscription_accounts` to
    `phone_devices` / `phone_clones`;
  - `assignment_type in ('full_cycle', 'outreach_only')`;
  - `slot_kind in ('full_cycle_6h', 'outreach_short')`;
  - includes `starts_at` / `ends_at` reservation windows but does not enforce
    runtime session duration.

Validation rules:

- `clone.device_id` must match `account_assignments.device_id`;
- `subscription_account.account_id` must match `account_assignments.account_id`;
- `subscription.client_id` must match `account_assignments.client_id`;
- `assignment_type` must match `client_subscriptions.subscription_type`;
- `full_cycle` uses `slot_kind='full_cycle_6h'`;
- `outreach_only` uses `slot_kind='outreach_short'`;
- device `pool_type` must match the assignment type or be `shared`;
- one open assignment (`pending`, `reserved`, `active`) per account;
- a clone cannot have overlapping open assignment windows.

Dashboard visibility:

- client dashboard should read a future safe view/RPC only, with statuses such as
  `pending_assignment`, `preparing`, `connected`, `action_required`, `paused`;
- client dashboard must never read `adb_serial`, `device_udid`, `host_machine`,
  `hub_label`, `hub_port`, clone internals, `ig_accounts.email`, or
  `ig_accounts.password`;
- admin/assistant dashboards may see device kind, pool, host/hub/port, status,
  status reason, max clones, and clone capacity.

Relation to existing `ig_accounts` operational fields:

- `phone_devices` + `phone_clones` + `account_assignments` are the future source
  of truth for assignment;
- live `ig_accounts.device_id`, `device_name`, `device_udid`, `clone_mode`, and
  `login_method` remain legacy/compat ops fields;
- Entry 2C does not sync assignment data back into `ig_accounts`;
- client dashboard reads must still avoid direct `ig_accounts` exposure.

Roadmap after Entry 2C:

- Entry 2C-2: auto-assign RPC;
- Entry 2C-3: worker/dispatcher reads assignments and resolves host/device/clone;
- B3: business session 6h window guard and phone rest/runtime scheduler;
- Entry 2D: credential onboarding, password update, and secret references;
- Entry 2E: provisioning jobs, login/relogin/2FA/checkpoint handling.

## Entry 2C-2 Auto-Assign Helper

Entry 2C-2 adds a service-role SQL helper:

```sql
public.auto_assign_account_from_subscription(
  p_subscription_account_id uuid,
  p_starts_at timestamptz,
  p_ends_at timestamptz,
  p_preferred_device_id uuid default null,
  p_preferred_clone_id uuid default null,
  p_metadata jsonb default '{}'::jsonb
)
```

The function reserves one available clone for an account-scoped subscription and
creates `account_assignments.status='reserved'`. It returns the assignment,
device, clone, account, subscription, assignment type, slot kind, and requested
window as JSON.

Security:

- the function is `SECURITY DEFINER`;
- execution is service-role only;
- `PUBLIC`, `anon`, and `authenticated` must not have execute privileges;
- Entry 2C-2 includes a corrective grants migration to keep
  `anon_can_execute=false`, `authenticated_can_execute=false`, and
  `service_role_can_execute=true`.

Selection policy:

- `client_subscription_accounts.status` must be `active`;
- `client_subscriptions.status` must be `active` for `p_starts_at`;
- `p_ends_at` must be greater than `p_starts_at`;
- no open assignment may already exist for the same `account_id`;
- `assignment_type` comes from `client_subscriptions.subscription_type`;
- `slot_kind` is `full_cycle_6h` for `full_cycle` and `outreach_short` for
  `outreach_only`;
- eligible devices have `status in ('available', 'active')`;
- eligible devices use the exact matching pool or `shared`;
- eligible clones must have `status='available'`;
- clone windows must not overlap an open assignment;
- preferred device/clone IDs are honored only if compatible.

The clone candidate is locked with `FOR UPDATE SKIP LOCKED`, and Entry 2C
constraints/triggers still act as the final guard for account double assignment,
pool/type mismatches, and clone window overlap.

Status update policy:

- inserts `account_assignments.status='reserved'`;
- sets `phone_clones.status='reserved'`;
- sets `phone_clones.current_account_id` to the assigned account;
- does **not** update `phone_devices.status`;
- treats `account_assignments` as the source of truth.

Entry 2C-2 deliberately does not add a timeslot catalog. The requested
`starts_at`/`ends_at` window is stored directly on the assignment. Future
scheduler work may add a richer catalog and may safely support planning on
currently reserved/active clones. In v1, clones must be `available` to avoid
ambiguity around logged-in accounts and warm sessions.

Future work:

- `release_assignment(...)` should release the assignment and restore clone
  availability;
- Entry 2C-3 should connect worker dispatch to assignment/device/clone routing;
- B3 should enforce runtime session windows and phone rest;
- Entry 2D/2E should handle credentials, provisioning, login, 2FA, and
  checkpoint flows.

## Remote Secrets

Remote Edge Function secrets must be configured on the Supabase project before
running HTTP validation. Never commit or print the real token value.

```bash
supabase secrets set --project-ref zgafnshkjywfltxgbtzg \
  OUTREACH_ENQUEUE_INTERNAL_API_TOKEN="<shared-bearer-token>" \
  OUTREACH_ENQUEUE_ALLOWED_ACCOUNT_IDS="42c625c2-e761-4100-8a9d-7ae1373de97d" \
  OUTREACH_ENQUEUE_ALLOWLIST_FALLBACK_ENABLED="false" \
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

For Entry 2A, first apply the migration and seed ownership/entitlement rows for the
test account. **Checkpoint proof uses SQL seed + SQL helper checks + Edge Function
curl** (see below). PostgREST `POST /rest/v1/*` currently returns `PGRST102` on the
linked project even for manual curl with valid JSON, so `validate-entry2a.sh`
defaults to **edge-only** mode and does not treat REST seed as checkpoint proof.

```bash
export SUPABASE_URL="https://zgafnshkjywfltxgbtzg.supabase.co"
export ENTRY2A_BASE_URL="$SUPABASE_URL/functions/v1/outreach-enqueue"
export ENTRY2A_ACCOUNT_ID="42c625c2-e761-4100-8a9d-7ae1373de97d"
export OUTREACH_ENQUEUE_INTERNAL_API_TOKEN="<shared-bearer-token>"

# After SQL seed below:
./scripts/validate-entry2a.sh

# Optional: also exercise PostgREST paths (diagnostic; may fail with PGRST102)
# export SUPABASE_SERVICE_ROLE_KEY="<service-role-key>"
# ENTRY2A_REST_ENABLED=1 ./scripts/validate-entry2a.sh

# Optional: disabled-entitlement Edge rejection (after SQL sets active=false)
# ENTRY2A_RUN_DISABLED_TEST=1 ./scripts/validate-entry2a.sh
```

SQL seed for the test account (checkpoint proof):

```sql
INSERT INTO public.clients (id, name, status, metadata)
VALUES (
  '00000000-0000-4000-8000-000000002e2a'::uuid,
  'Entry 2A Test Client',
  'active',
  '{"source": "entry2a-validate"}'::jsonb
)
ON CONFLICT (id) DO UPDATE
SET name = EXCLUDED.name,
    status = EXCLUDED.status,
    metadata = EXCLUDED.metadata;

INSERT INTO public.client_instagram_accounts (
  id,
  client_id,
  account_id,
  label,
  onboarding_status,
  provisioning_status,
  login_status
)
VALUES (
  '00000000-0000-4000-8000-00000012e2a1'::uuid,
  '00000000-0000-4000-8000-000000002e2a'::uuid,
  '42c625c2-e761-4100-8a9d-7ae1373de97d'::uuid,
  'Entry 2A Test Account',
  'ready',
  'ready',
  'connected'
)
ON CONFLICT (id) DO UPDATE
SET client_id = EXCLUDED.client_id,
    account_id = EXCLUDED.account_id,
    label = EXCLUDED.label,
    onboarding_status = EXCLUDED.onboarding_status,
    provisioning_status = EXCLUDED.provisioning_status,
    login_status = EXCLUDED.login_status;

INSERT INTO public.client_entitlements (
  id,
  client_id,
  feature_code,
  entitlement_type,
  active,
  metadata
)
VALUES (
  '00000000-0000-4000-8000-00000012e2a2'::uuid,
  '00000000-0000-4000-8000-000000002e2a'::uuid,
  'outreach',
  'standalone',
  true,
  '{"source": "entry2a-validate"}'::jsonb
)
ON CONFLICT (id) DO UPDATE
SET active = EXCLUDED.active,
    entitlement_type = EXCLUDED.entitlement_type,
    metadata = EXCLUDED.metadata;
```

SQL verification:

```sql
SELECT public.client_account_has_outreach_entitlement(
  '42c625c2-e761-4100-8a9d-7ae1373de97d'::uuid
) AS can_enqueue_test_account;

SELECT public.client_account_has_outreach_entitlement(
  '00000000-0000-4000-8000-000000000999'::uuid
) AS can_enqueue_random_account;

SELECT tablename, rowsecurity
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN (
    'clients',
    'client_users',
    'client_instagram_accounts',
    'client_entitlements'
  );
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
- Entry 2A ownership/entitlement guard;
- optional account allowlist fallback for internal producers only when explicitly enabled;
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

- seed production client ownership and entitlements;
- add filtered dashboard read endpoints instead of exposing `ig_accounts`;
- add `import_csv` enum or map it in a v2 product decision;
- add import/campaign tracking tables;
- add bulk RPC for high-volume imports.
