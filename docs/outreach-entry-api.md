# Outreach Enqueue API

Secure server-side entrypoint for dashboard, n8n, CSV import, or internal API producers.

Pour l'etat global du projet et la roadmap complete, voir
`docs/project-knowledge-base.md`.

Flow:

```text
dashboard / n8n / import
  -> supabase/functions/outreach-enqueue
  -> public.enqueue_outreach_dm_job(...)
  -> ig_dm_jobs pending
  -> outreach_session / unfollow_outreach_pipeline
```

The function never writes directly to `ig_dm_jobs` and never calls the sender.

## Outreach Runtime Readiness Context — 2026-06-08

Welcome DM and the Welcome -> Follow handoff are validated before Outreach:

- Welcome production path is list-native, not Search:
  `followers list -> candidat -> profil/thread -> Send -> back followers list`.
- Physical Welcome caps 1/2/3 are validated on `j_automatise_pour_toi`; cap 3
  run `609866af-3b00-4b96-bce6-4047d25ae9ef` sent
  `revario_schweiz`, `vaneaurealestate`, `le12emecru`.
- `account_session` validates the production handoff:
  Welcome optional -> `prepare_dm_to_follow_handoff()` -> Follow rotation.
  Test B `4c9d22db-10f9-4882-90e5-59ef252b6c66` completed 2 Welcome DMs then
  2 full Follows with Like/Mute ON, return CT OK, Outreach OFF and Unfollow OFF.
- The isolated Welcome `send-one` Search smoke remains useful as a technical
  base for Outreach Search DM (`Search -> profile -> DM -> send -> back -> back
  to search zone -> next candidate`), but it is not the Welcome production path.

Before any real Outreach run, audit:

- recipient source and deduplication;
- DB/env caps and daily counters;
- strict separation from Welcome jobs/counters;
- `OUTREACH_DM_REAL_SEND_ENABLED=true` only for the Outreach test;
- `DM_SENDER_REAL_SEND_ENABLED=false` legacy generic flag;
- no Follow/Welcome/Unfollow coupling;
- no double-send and no stale `reserved/running` jobs;
- stop conditions, logs, no-leak, and dashboard/BotApp status projection.

Safe ramp proposal:

- V1 safe Outreach: 10 DMs/day/account.
- After 3-5 clean days: 20 DMs/day/account.
- After validation without restrictions: 30-40 DMs/day/account.
- Hard prudent cap: 50-60 DMs/day for a solid account.

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

## Entry 2C Device / App Instance / Assignment Model

Entry 2C adds the capacity and assignment layer for phones, Instagram app
instances, and account-scoped subscription assignments. It does not change the
worker runtime, Edge Functions, credentials, auto-login, provisioning,
campaigns, imports, or session window guards.

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
- `phone_app_instances`
  - admin-only assignable Instagram app inventory for a device;
  - includes the primary Instagram app and clone/app-profile packages;
  - `instance_type in ('primary_app', 'clone')`;
  - `instance_index=0` is the primary app;
  - clone/app-profile instances use `instance_index >= 1`;
  - `status in ('available', 'occupied', 'disabled', 'unknown')`;
  - an instance is assignable only when `available`, launchable,
    `usable_for_auto_login`, and not bound to another account;
  - an instance already `occupied` by the same account must be reused for
    Schedule slot changes before choosing a free instance.
- `phone_clones`
  - legacy compatibility clone/app-profile inventory for a device;
  - remains during migration for older worker/resolver paths;
  - `current_account_id` is denormalized/admin-only; the source of truth is
    `phone_app_instances` + `account_assignments`.
- `account_assignments`
  - links `client_subscriptions` / `client_subscription_accounts` to
    `phone_devices` / `phone_app_instances`;
  - keeps nullable `clone_id` for compatibility;
  - `app_instance_id` is the app instance source of truth;
  - `assignment_type in ('full_cycle', 'outreach_only')`;
  - `slot_kind in ('full_cycle_6h', 'outreach_short')`;
  - includes `starts_at` / `ends_at` reservation windows but does not enforce
    runtime session duration.

Validation rules:

- `phone_app_instances.device_id` must match `account_assignments.device_id`;
- open assignments require `app_instance_id`;
- `subscription_account.account_id` must match `account_assignments.account_id`;
- `subscription.client_id` must match `account_assignments.client_id`;
- `assignment_type` must match `client_subscriptions.subscription_type`;
- `full_cycle` uses `slot_kind='full_cycle_6h'`;
- `outreach_only` uses `slot_kind='outreach_short'`;
- device `pool_type` must match the assignment type or be `shared`;
- one open assignment (`pending`, `reserved`, `active`) per account;
- an app instance cannot have overlapping open assignment windows;
- a phone cannot have overlapping open assignment windows for different
  accounts.

Validated inventory example, `Entry 2C Emulator Full Cycle`:

- `primary_app` / index 0 / `com.instagram.android`: occupied by
  `cinema_catchup`;
- `Instagram 1` / index 1 / `com.instagram.androie`: available;
- `Instagram 2` / index 2 / `com.instagram.androif`: available;
- `Instagram 3` / index 3 / `com.instagram.androig`: available.

Future physical Samsung A16 inventory must follow the same model. The primary
app is not excluded by nature; it is assignable if free and occupied if a
connected account is already using it.

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

## Entry 2C-3 Worker Assignment Dispatch Resolver

Entry 2C-3 v1 connects only the Python worker startup path for
`run_type=outreach_session` to the Entry 2C assignment model. The resolver is
read-only and resolves:

```text
account_id + run_type
  -> account_assignments(status in reserved, active)
  -> phone_clones
  -> phone_devices
  -> adb_serial for local worker connect_device(...)
```

Feature flags are OFF by default:

- `ACCOUNT_ASSIGNMENT_DISPATCH_ENABLED=false`
- `ACCOUNT_ASSIGNMENT_DISPATCH_REQUIRE_ASSIGNMENT=false`
- `ACCOUNT_ASSIGNMENT_DISPATCH_ENFORCE_WINDOW=false`
- `ACCOUNT_ASSIGNMENT_DISPATCH_RUN_TYPES=outreach_session`
- `ACCOUNT_ASSIGNMENT_DISPATCH_LOG_SENSITIVE=false`

When disabled, the worker keeps the legacy `DEVICE_SERIAL` / default ADB
behavior. When enabled for `outreach_session`, the worker reads the latest
`reserved` or `active` assignment for the target account, validates that its
`assignment_type` is `outreach_only` or `full_cycle`, and uses the assignment
device `adb_serial` as a local variable before `connect_device(...)`.

Entry 2C-3 v1 deliberately does not:

- mutate `account_assignments.status` from `reserved` to `active`;
- release assignments at session end;
- implement the business 6h session guard or phone rest runtime;
- provision credentials, auto-login, relogin, 2FA, or checkpoint flows;
- dispatch `account_session`, unfollow, welcome, or follow sessions from
  assignments;
- change sender/orchestrator behavior or quotas.

Logs use stable events such as `account_assignment_dispatch_resolved`,
`account_assignment_dispatch_missing`,
`account_assignment_dispatch_incompatible`,
`account_assignment_dispatch_fallback_legacy`, and
`account_assignment_dispatch_window_inactive`. Sensitive ops fields such as
`adb_serial`, `device_udid`, host, hub label, and hub port are redacted by
default; only a serial suffix/hash is logged unless the explicit ops flag is
enabled.

Roadmap note: an Ops Realtime Foundation can later add `runtime_events`,
`worker_heartbeats`, `device_heartbeats`, and rate limiter signals. Supabase
Realtime is the V1 target for that foundation; Redis can remain an optional V2.
No Realtime, Redis, scheduler, or rate limiter implementation is part of
Entry 2C-3 v1.

## Entry 2D-1 Account Credentials Metadata

Entry 2D-1 adds a schema-only credential metadata registry:
`public.account_credentials`.

This table stores **metadata and secret references only**. It does not store
Instagram passwords, encrypted passwords, raw secrets, tokens, cookies, recovery
codes, vault payloads, screenshots, raw XML, or local plaintext credential
files.

Core model:

- `account_id` links to `ig_accounts`;
- `client_id` optionally links to `clients`;
- `provider='instagram'`;
- `username_at_submission` is a safe snapshot only;
- `secret_ref` points to a future vault/KMS/secret-manager entry;
- `secret_provider` identifies the future secret backend;
- `credentials_version` is a safe monotonic metadata version;
- `status in ('active', 'superseded', 'revoked')`;
- `reauth_required` and `reauth_reason` are safe status fields;
- `metadata` is safe JSON only.

Security:

- no direct `anon` or `authenticated` access;
- RLS is enabled;
- service-role only policy/grants;
- one active credential per `(account_id, provider)`;
- dashboard clients must use future Edge/RPC endpoints, not direct table reads.

The client dashboard may submit or update a password in a future Entry 2D-2 API,
but it must never read it back. Admin dashboards may read safe credential status
and operational metadata, but never password material or raw secret values.

Entry 2D-1 deliberately does not:

- call a vault or secret manager;
- implement submit/update password APIs;
- add dashboard forms;
- add provisioning jobs;
- auto-login, relogin, handle 2FA, or resolve checkpoints;
- publish incidents or Slack/Discord alerts.

Future phases:

- Entry 2D-2: secure submit/update API or Edge Function writes the secret to a
  vault/KMS and stores only `secret_ref`;
- Entry 2D-3: client/admin safe read status endpoints;
- Entry 2D-4: password update workflow, audit, and credential version bumps;
- Entry 2E: provisioning/login status and jobs;
- Entry 2F/2G: incidents and alerts for credential/login states.

Security NO-GO:

- no password in dashboard-readable Supabase tables;
- no password in logs;
- no password in `account_incidents` or `runtime_events`;
- no password in Slack/Discord or `account_incident_notifications`;
- no password in shared `.env`;
- no raw password API responses;
- no local plaintext credential files.

Future safe incident types may include `credentials_updated`, `reauth_required`,
`login_failed`, `checkpoint_required`, `two_factor_required`,
`credentials_invalid`, and `credential_rotation_failed`. Incident metadata must
remain safe and must never include passwords or raw secret references.

## Backend Patch 1 — Credential Secure Pipeline V1 Foundation

Backend Patch 1 keeps the existing `account_credentials` and
`account_dashboard_actions` foundations, then adds the missing schema/RPC layer
needed for a single secure credential pipeline shared by future Add Profile
onboarding and password update links.

Migration:

- `supabase/migrations/20260529155500_credential_secure_pipeline_v1_foundation.sql`.

Schema changes:

- `account_credentials` gains `last_updated_at`, safe actor/source fields and
  `metadata_safe`;
- `account_dashboard_actions` gains safe created-by fields and `metadata_safe`;
- `credential_update_requests` stores one-time update request metadata with
  `token_hash`, expiry, lifecycle status and `metadata_safe`.

RPC/helpers:

- `record_instagram_credential_ingestion(...)` records safe credential metadata
  and returns only `account_id`, `provider`, `credentials_version`, `status` and
  `last_updated_at`;
- `create_credential_dashboard_action(...)` creates credential-related dashboard
  action skeletons such as `update_instagram_password`;
- `create_credential_update_request_metadata(...)` stores update-link metadata
  with a token hash only and never returns the hash.

Security:

- RLS remains enabled and conservative; direct `anon` / `authenticated` table
  reads or writes are not granted;
- `metadata_safe` rejects forbidden keys such as password, token, raw request
  body, `secret_ref`, Vault payload, raw logs/XML, screenshot paths and device
  internals;
- raw update tokens are not stored; token generation and delivery are future
  Edge/API work.

Legacy status:

- `ig_account_settings.password` remains legacy and deprecated;
- this patch does not read, migrate, delete or backfill legacy password values;
- the dashboard must never read back the legacy password.

Future plan:

- Add Profile should later route the write-only password to the secure backend
  pipeline and stop persisting it in `ig_account_settings.password`;
- password update links should be generated by a future Edge/API, store only
  `token_hash`, and submit the new password as write-only material through the
  credentials API;
- no frontend route, Vercel deploy, device run, runner/sender/outreach/follow
  change, Slack/Discord change or backend publish is part of this patch.

## Entry 2D-2B Instagram Credentials Edge API

Entry 2D-2B adds the server-side boundary for initial Instagram credential
submission and password updates:
`supabase/functions/instagram-credentials`.

The function accepts a password as a **write-only** request field. It validates
the producer, writes the secret payload to Supabase Vault, and stores only safe
metadata in `public.account_credentials`.

Request shape:

```json
{
  "action": "submit",
  "account_id": "00000000-0000-4000-8000-000000000000",
  "username": "optional_username_snapshot",
  "password": "write-only",
  "external_request_id": "optional-safe-idempotency-key"
}
```

`action` may be `submit`, `update_password`, or the internal-only
`submit_add_profile_credentials` added by Backend Patch 2A.

Authentication:

- client dashboard calls use a Supabase Auth JWT;
- internal/admin producers may use `INSTAGRAM_CREDENTIALS_INTERNAL_API_TOKEN`;
- client JWT requests must pass `client_can_manage_instagram_account`;
- the function uses the service-role key only server-side.

Vault behavior:

- Supabase Vault is the V1 backend;
- one new Vault secret is created per `credentials_version`;
- the Vault secret name is
  `phonefarm/instagram/{account_id}/credentials/v{version}`;
- `account_credentials.secret_provider='supabase_vault'`;
- `account_credentials.secret_ref='supabase_vault://{vault_secret_id}'`;
- old Vault secrets are not read, returned, or neutralized in 2D-2B.

Rotation behavior:

- the function reads the current max `credentials_version`;
- it writes the new Vault secret first;
- `rotate_instagram_account_credentials` supersedes existing active metadata
  rows and inserts one new active row;
- `reauth_required=true` and
  `reauth_reason='awaiting_login_verification'` until Entry 2E verifies login;
- `last_rotated_at` is set for `update_password`.

Safe response:

```json
{
  "ok": true,
  "account_id": "00000000-0000-4000-8000-000000000000",
  "provider": "instagram",
  "credentials_version": 1,
  "status": "active",
  "reauth_required": true,
  "next_action": "awaiting_login_verification"
}
```

The response never includes the password, Vault payload, Vault secret value, or
full `secret_ref`.

## Backend Patch 2A — Add Profile Credential Orchestration

Backend Patch 2A extends `supabase/functions/instagram-credentials` without
creating a parallel credential pipeline. The new internal action is:

```json
{
  "action": "submit_add_profile_credentials",
  "account_id": "00000000-0000-4000-8000-000000000000",
  "expected_username": "instagram_username",
  "password": "write-only",
  "actor_type": "admin",
  "actor_id": "optional-safe-actor-id",
  "metadata_safe": {
    "flow": "add_profile"
  },
  "external_request_id": "optional-safe-id"
}
```

Contract:

- authentication is internal-token only via
  `INSTAGRAM_CREDENTIALS_INTERNAL_API_TOKEN`;
- `instagram-credentials` must remain deployed with `--no-verify-jwt`; gateway
  JWT verification rejects the opaque internal bearer token before the function
  can run its own auth check;
- remote HTTP smokes must source a temporary `.env.smoke.local` whose
  `INSTAGRAM_CREDENTIALS_INTERNAL_API_TOKEN` matches the remote Edge secret, and
  that file must be deleted immediately after the smoke;
- the function validates that the `ig_accounts` row exists;
- when `ig_accounts.username` is available, `expected_username` must match it
  before any Vault write; mismatch returns `expected_username_mismatch`;
- the password is accepted only as a write-only POST body field;
- the existing Vault adapter writes one new Supabase Vault secret for the next
  `credentials_version`;
- the existing `rotate_instagram_account_credentials` RPC is called with
  `p_action='submit'` and `p_submitted_via='add_profile'`;
- Patch 2A updates that rotation RPC to populate safe current-state metadata
  (`source`, `metadata_safe`, `updated_by_actor_type`) without accepting or
  returning the password;
- `account_credentials.status` remains `active`, which is the status consumed by
  the Python provisioner;
- if a `client_instagram_accounts` row already exists, status sync is attempted
  to `onboarding_status='credentials_submitted'`,
  `provisioning_status='login_pending'`, and
  `login_status='verification_pending'`; this sync is fail-open after credentials
  have been stored;
- dashboard action sync remains safe and fail-open.

Failure and retry behavior:

- invalid input, missing password, account not found, auth failure and username
  mismatch are rejected before Vault write;
- Vault write failure returns `secret_write_failed` and creates no active
  `account_credentials`;
- metadata rotation failure after Vault write returns
  `credentials_metadata_write_failed` without returning or logging the secret
  reference;
- Patch 2C-1 adds service-role-only cleanup/revoke helpers. Vault physical
  delete is not available in the current remote Vault API; cleanup neutralizes
  the Vault value through `vault.update_secret(...)` and never returns the
  secret value, full `secret_ref`, Vault id, token, or raw metadata;
- duplicate successful submits are currently rotations: the next
  `credentials_version` is created and previous active metadata is superseded;
- `external_request_id` / request id are recorded as safe metadata but do not yet
  provide full idempotency. Patch 2B should avoid blind client retries and should
  treat timeouts as “check status first, then retry if needed”.

Safe response shape:

```json
{
  "ok": true,
  "account_id": "00000000-0000-4000-8000-000000000000",
  "provider": "instagram",
  "credentials_version": 2,
  "credentials_status": "active",
  "status": "active",
  "reauth_required": true,
  "next_action": "awaiting_login_verification",
  "password_status": "write_only"
}
```

No response, log, dashboard action metadata, status metadata, or doc example may
include the password, full request body, `secret_ref`, Vault id/value, raw token,
Authorization header, service-role key, raw XML/logs/screenshots, or device
internals.

Frontend Patch 2B should call this internal action from the server-side
`accounts/create` route after creating `ig_accounts`, then stop writing
`body.password` into `ig_account_settings.password` for new Add Profile flows.
Patch 2A does not modify the frontend, does not activate Request Password
Update, does not create secure links, does not migrate legacy passwords, and
does not touch runner/provisioner/device flows.

Future full Add Profile direction:

```text
Add Profile frontend
  -> accounts/create server-side
  -> create account/settings/filters/status safely
  -> submit_add_profile_credentials (write-only password)
  -> Vault secret
  -> account_credentials active
  -> onboarding/provisioning/login statuses ready for login probe
  -> dashboard/account response with safe credential status only
```

Patch 2A intentionally prepares only the credential orchestration boundary. A
future transactional create-account RPC/API may fold account, settings, filters,
ownership/status and credential orchestration into one backend unit, but it must
still reuse this single Vault + `account_credentials` pipeline.

## Backend Patch 2C-1 — Credential Cleanup / Revoke Foundation

Patch 2C-1 adds backend-only, service-role-only cleanup primitives needed before
running a full authenticated Add Profile smoke in production:

- `revoke_instagram_credentials_vault_secret(p_secret_ref, p_reason,
  p_request_id)` accepts only `supabase_vault://{uuid}` references and
  neutralizes the Vault value with a safe revoked payload via
  `vault.update_secret(...)`;
- `revoke_instagram_account_credentials(p_account_id, p_provider, p_reason,
  p_request_id)` marks Instagram credential metadata as `revoked`, sets
  `revoked_at`, `reauth_required=true`, safe `reauth_reason`, and safe
  `metadata_safe` cleanup fields, then attempts Vault neutralization for linked
  Supabase Vault refs;
- `cleanup_instagram_smoke_account(p_username, p_request_id)` is a strict smoke
  cleanup helper. It only accepts usernames matching `smoke_*` and requires the
  supplied request id to match related safe metadata before deleting smoke rows.

The smoke cleanup helper removes only related smoke rows and returns counts:
`account_removed`, `credentials_removed`, `actions_removed`,
`requests_removed`, `settings_removed`, `filters_removed`,
`client_links_removed`, plus safe Vault cleanup status. It never returns full
`secret_ref`, Vault id, password, token, Authorization header, service-role key,
raw request body, raw metadata, device ids, XML, screenshots, or raw logs.

Patch 2C-1 does not modify frontend code, does not run Add Profile UI E2E, does
not launch device/provisioner/login, does not activate Request Password Update,
does not create secure links, and does not migrate legacy
`ig_account_settings.password`. Full Add Profile E2E remains pending until an
operator-authenticated smoke can use these cleanup primitives.

### Account Lifecycle Vs Credential Cleanup

Account lifecycle is intentionally decoupled from credential cleanup in Patch
2C-1 because `archive`, `trash` and `restore` are restorable product states:

- archive does not revoke or neutralize credentials. A future lifecycle worker
  may suspend campaigns/provisioning/runtime, but restore must remain a simple
  lifecycle transition;
- trash / corbeille during retention does not revoke or neutralize credentials by
  default because the account is still restorable;
- restore must not fail because credentials were neutralized by an automatic
  archive/trash cleanup;
- permanent delete after retention may trigger credential cleanup and Vault
  neutralization;
- explicit credential revoke is a separate operation and does not imply account
  deletion;
- smoke cleanup, failed ingestion cleanup, explicit security revoke and
  permanent account delete cleanup are allowed only through strict backend
  guards.

Allowed Patch 2C-1 cleanup reasons are:

- `smoke_cleanup`;
- `failed_ingestion_cleanup`;
- `explicit_credential_revoke`;
- `security_revoke`;
- `permanent_account_delete`.

The cleanup helpers reject lifecycle-retention reasons such as `archive`,
`trash`, `trash_pending_retention`, `pause`, `paused`,
`cancelled_without_final_delete` and `archived_pending_retention`. No route or
automation may call credential cleanup on archive, trash, scheduled trash,
restore or other restorable lifecycle transitions.

Restoration safety:

- archived credentials remain intact unless explicitly revoked;
- trashed credentials remain intact during retention unless explicitly revoked;
- restore should later check `credentials_status` before resuming runtime, but
  must not assume credentials were deleted;
- if credentials were explicitly revoked while a row was in trash, restore should
  require a credential update before runtime/provisioning resumes.

Future lifecycle sync should be durable and multi-surface across admin
dashboard, BotApp/backend, future client dashboard and automation/runtime. Future
fields should include `source_surface`, `actor_type`, `actor_id`, `reason`,
`lifecycle_status`, `archived_at`, `trashed_at`, `scheduled_trash_at`,
`scheduled_delete_at` / `purge_after`, `permanently_deleted_at`, `sync_status`,
`botapp_sync_status`, `client_dashboard_sync_status`,
`admin_dashboard_sync_status`, `audit_event_id` and
`credential_cleanup_status`. Patch 2C-1 does not create this lifecycle sync.

## Backend Patch 2C-2 — Production Add Profile E2E Checkpoint

Patch 2C-2 validates the full authenticated Add Profile production path after
aligning the Vercel Production
`INSTAGRAM_CREDENTIALS_INTERNAL_API_TOKEN` with the token already accepted by the
Supabase Edge Function. The safe runtime fingerprint observed from Vercel
Production was `sha12=4c74d75cb9fd` with `present=true` and `len=64`.

The no-leak Edge reprobe against `instagram-credentials` no longer returned
`401 unauthorized`. With the Vercel Production token, the fake account probe
passed internal authentication and failed only at the expected business layer
with `account_not_found`.

The authenticated production UI smoke on `/instagram-dashboard` completed the
Add Profile wizard through confirmation and validated:

- `ig_accounts` row created;
- `ig_account_settings` row created;
- `ig_account_filters` row created;
- `ig_account_settings.password = ''`;
- `submit_add_profile_credentials` succeeded through the frontend route and Edge
  Function;
- `account_credentials` row created with `status='active'`;
- `ig_accounts.status='active'`;
- no `credentials_ingestion_failed` UI outcome.

Cleanup used `cleanup_instagram_smoke_account(...)` for the smoke username and
matching request id. It removed the smoke account, settings, filters, credential
metadata and related dashboard action, and neutralized the linked Vault secret.
Post-cleanup verification confirmed smoke counts at zero for accounts, settings,
filters and credentials.

Patch 2C-2 did not modify runtime code, the Edge Function, dashboard UI or
migrations. No token, Authorization header, service-role key, password, full
`secret_ref`, Vault id, cookies/session, raw logs, XML or screenshot path was
printed or stored in the checkpoint.

## Backend Patch 2C-3 — Add Profile Hardening

Patch 2C-3 hardens Add Profile against partial creation states without changing
archive/trash/restore lifecycle behavior and without changing the
`instagram-credentials` Edge Function.

Status contract:

- `active` means the Add Profile flow has completed account, settings, filters
  and active credential metadata;
- `support_required` is the safe state for a newly created Add Profile row until
  credentials are active, and remains the controlled failure state for credential
  ingestion or final status failures;
- `ig_account_settings.password` must remain `''` for new Add Profile rows in
  both success and failure paths.

Route hardening:

- `accounts/create` creates the account initially as `support_required`;
- settings are inserted with `account_status='support_required'` and
  `password=''`;
- settings and filters failures before credential ingestion trigger a targeted
  compensation delete of the newly created account id. Foreign keys cascade the
  just-created settings/filters rows. This compensation is only for the current
  Add Profile account id, before credentials exist;
- credential ingestion failure keeps the account/settings in
  `support_required`, creates a best-effort safe credential support action, and
  returns only safe UI errors such as `credentials_ingestion_failed` or
  `credentials_ingestion_timeout`;
- successful credential ingestion is accepted only when the safe credentials
  response reports active credentials, then the route finalizes `ig_accounts` and
  `ig_account_settings.account_status` to `active`;
- if final status activation fails after credentials are active, the route does
  not report success. It marks the account back to `support_required` and raises
  a safe support action.

Idempotency guard:

- migration `20260529220012_patch2c3_add_profile_username_unique.sql` adds a
  unique index on `lower(btrim(username))` for non-empty `ig_accounts.username`;
- duplicate normalized usernames fail as `account_already_exists` instead of
  creating a second account or a second incoherent credential path.

No-leak constraints remain unchanged: no password, raw request body, token,
Authorization header, service-role key, full `secret_ref`, Vault id/value,
cookies/session, XML, screenshot path or raw logs in UI errors, dashboard action
metadata or docs.

Production validation checkpoint (2026-05-29):

- remote migration `20260529220012` applied; unique index
  `ig_accounts_username_lower_unique` present; duplicate username precheck clean;
- Vercel production deploy active on `www.boostmybusinesses.com` with patched
  `accounts/create`;
- authenticated production smoke username `smoke_add_profile_2c3_e2e`: happy path
  `201` with `ig_accounts.status=active`, settings `account_status=active` and
  `password=''`, filters row present, `account_credentials.status=active`;
- duplicate recreate returned `account_already_exists` (`409`) with a single
  account and credential row;
- targeted cleanup RPC removed account/settings/filters/credentials/actions;
  vault neutralized. No token, password, service-role key, cookie/session,
  full `secret_ref` or Vault id was logged in the checkpoint.

## Backend Patch 2C-4 — Add Profile Audit And Dashboard Actions

Patch 2C-4 adds safe Add Profile audit and dashboard action reconciliation
around the existing 2C-3 flow. It does not change worker Python, runner,
login/provisioner runtime, `instagram-credentials`, archive/trash/delete
lifecycle, or credential cleanup/revoke policy.

Audit behavior:

- migration `20260529203144_patch2c4_add_profile_audit.sql` creates
  `add_profile_audit_events` for durable Add Profile outcome audit;
- each audited row stores safe `account_id` when available, normalized username,
  truncated request ids, `source_surface='admin_dashboard'`,
  `operation='add_profile'`, actor type/id when available, result status and a
  safe failure reason;
- result statuses are `success`, `failed`, `compensated`, and `duplicate`;
- compensated settings/filter setup failures keep an audit row even when the
  account row is deleted.

Dashboard action mapping:

- success: no new blocking action. Open `submit_instagram_credentials` or
  `review_credentials` dashboard actions for the account are resolved after
  credentials are active and account/settings finalization succeeds;
- credential ingestion failure or non-active credential response:
  `review_credentials`, `pending`, `warning`, `audience='admin'`,
  `requires_client_action=false`, `blocking_campaign=true`, deduped by
  `account:{account_id}:dashboard_action:review_credentials`;
- settings/filter failure with successful compensation: audit only, no action
  attached to the deleted account;
- settings/filter failure with failed compensation: `review_credentials` admin
  action for manual review;
- duplicate username: audit `duplicate`, API remains `account_already_exists`,
  no persistent action.

The Credentials Actions page may merge open `account_dashboard_actions` with its
derived read-only signals. It renders safe action fields only and does not expose
metadata or enable acknowledge/dismiss/resolve mutations.

Patch 2C-4 no-leak policy: never store or render passwords, tokens,
Authorization headers, service-role keys, cookies/sessions, full `secret_ref`,
Vault UUIDs/payloads, raw request bodies, raw logs, XML or screenshot paths in
audit rows, dashboard actions, UI, docs, or route errors.

## Backend Patch 2C-5 — Add Profile Username Verification Metadata

Patch 2C-5 adds a safe structure for public Instagram profile metadata around
Add Profile. It deliberately does not implement the target-account quality
filtering engine, mass avatar enrichment, comments/AI ranking, worker runtime,
runner, login/provisioner, device/app start, scraping, MCP integration, or
lifecycle archive/trash/delete behavior.

Schema:

- migration `20260529205443_patch2c5_add_profile_public_metadata.sql` extends
  `ig_accounts` with safe public fields:
  `username_verification_status`, `username_verified_at`,
  `username_verification_reason`, `instagram_user_id`,
  `external_profile_id`, `is_private`, `is_verified`, `followers_count`,
  `avatar_url`, `avatar_checked_at`, and `public_profile_metadata`;
- `public_profile_metadata` uses the existing forbidden-key guard and must not
  contain secrets, raw request bodies, raw provider responses, XML, screenshots,
  cookies, tokens, service-role data or Vault references;
- `avatar_url` is optional and must be HTTP(S), bounded in length, and free of
  token/signature/secret/service-role/Vault markers.

Verification status mapping:

- `verified`: a future safe source confirms the submitted username;
- `not_found`: a future safe source clearly confirms the username does not
  exist;
- `username_changed`: a future safe source observes a canonical username
  redirect/change;
- `private_or_limited` / `inaccessible`: public access is limited but the
  account may exist;
- `verification_unavailable`: no stable public lookup provider is configured;
- `provider_error`: provider call failed without a clear account verdict;
- `invalid_format`: username failed local syntax checks;
- `pending` / `unknown`: no final public profile verdict yet.

Patch 2C-5 Add Profile behavior:

- Add Profile now normalizes the submitted username by trimming `@` and lower
  casing it before creation;
- syntactically invalid usernames are rejected before account creation with a
  safe `username_verification_failed` response;
- because no stable public lookup source exists in-repo, created accounts are
  marked `username_verification_status='verification_unavailable'` with
  `username_verification_reason='public_lookup_not_configured'`;
- this status does not block credential ingestion or active finalization;
- no `review_username` action is created for normal provider-unavailable state,
  avoiding dashboard noise.

Dashboard behavior:

- Manage and Client Accounts load the safe `ig_accounts` public profile fields
  server-side and display username verification status;
- Client Accounts uses `avatar_url` when present and falls back to initials when
  absent;
- Account Detail includes a read-only public profile card.

Future roadmap — Target Account Quality / CT Filtering Engine:

- CT introuvable;
- username changed;
- avatar missing/suspicious where useful;
- followers below a configured threshold, e.g. `<500`;
- verified / blue badge;
- private or non-exploitable;
- no posts;
- FBR `<= 8%` after at least `100` follows;
- no followable profiles after `X` scrolls;
- archive/suppression CT synchronized frontend/backend.

These CT rules are not implemented by Patch 2C-5.

## Backend Patch 2C-6 — Instagram Public Profile Lookup Provider

Patch 2C-6 adds a reusable, server-side public profile lookup contract for Add
Profile and future CT validation. It does not use Instagram credentials,
cookies, sessions, device/app startup, ADB, uiautomator, worker runtime, or
aggressive scraping.

Provider contract:

- `lookupInstagramPublicProfile(username, options)` returns a safe result with
  `status`, `canonical_username`, optional public ids, avatar URL, privacy /
  verified flags, follower count, safe reason, checked timestamp and bounded
  metadata;
- default mode is disabled / not configured and performs no network call;
- mock mode is for local/tests only;
- HTTP mode is opt-in by server-only env and must sanitize responses before
  storage. Raw provider responses, headers, cookies, tokens, HTML, IP/session
  details and secrets are never stored.

Add Profile integration:

- invalid syntax blocks before lookup with `username_verification_failed`;
- clear public `not_found` blocks before account creation with
  `username_not_found`;
- `found` persists verified public metadata on `ig_accounts` and continues the
  secure credentials flow;
- `provider_not_configured`, `unavailable`, `rate_limited` and
  `provider_error` remain fail-open for admin Add Profile and persist safe
  reason metadata rather than creating a blocking dashboard action.

CT Quality Filtering remains out of scope for 2C-6. The provider is prepared for
later CT existence checks, avatar/follower/private/verified enrichment,
canonical username change detection, follower threshold rules such as `<500`,
and future FBR rules.

## Backend Patch 2C-7B — SearchApi Staging Adapter (Not Production)

Patch 2C-7B adds an optional `searchapi` provider mode in the frontend lookup
library. It is a staging/local evaluation adapter only. It is not production
activation and must not be enabled on Vercel production without explicit
operator approval, extended staging tests, and a successful production go/no-go
review.

Server-only env (never `NEXT_PUBLIC_*`):

- `INSTAGRAM_PUBLIC_PROFILE_LOOKUP_PROVIDER=searchapi`
- `INSTAGRAM_PUBLIC_PROFILE_LOOKUP_URL` — SearchApi search endpoint
- `INSTAGRAM_PUBLIC_PROFILE_LOOKUP_API_KEY` — server secret only; never commit

Behavior:

- missing URL or API key → `provider_not_configured`, no external call;
- SearchApi uses a short staging timeout of 7s after the real-key smoke showed
  3s was too aggressive for Add Profile; no retries and no raw provider response
  stored;
- HTTP `404` / explicit not-found → `not_found`;
- HTTP `429` → `rate_limited`;
- HTTP `5xx` / timeout → `unavailable`;
- other non-OK / malformed body → `provider_error`;
- exploitable profile maps to `found` with safe avatar, followers, privacy,
  verified flags and bounded metadata (`provider_mode`, `provider_status`,
  `provider_engine`). SearchApi avatar fields `avatar` / `avatar_hd` are mapped
  to the 2C-6 `avatar_url` contract after URL safety filtering.

Add Profile fail-open rules from 2C-6 are unchanged. Modes `disabled`, `mock`
and generic `http` remain available and unchanged.

Patch 2C-7C records the real-key staging smoke fixes only. It does not make the
provider production-ready, does not modify Vercel production env, and does not
enable SearchApi outside explicit local/staging configuration.

Extended soak recommendation:

- SearchApi remains GO for staging and is a credible Add Profile candidate for
  public username existence, safe avatar, follower count, verified status and
  clear `not_found` checks.
- The `rate_limited` burst observed during the initial extended soak is now
  classified as the free / initial plan quota being reached, not as proof of
  provider instability. After the operator renewed/upgraded limits, the same
  style of test passed again without `rate_limited`.
- Production Add Profile remains NO-GO until the upgraded plan's quota, burst
  limit and expected cost are documented, then a short found / not_found /
  fail-open smoke is repeated. `rate_limited`, `unavailable` and
  `provider_error` must remain fail-open and must never be treated as
  `not_found`.
- CT bulk validation must not call SearchApi once per client form row. Any CT
  use needs queue/batch processing, cache, throttling/spacing, quota tracking,
  decision audit, and dashboard states such as `pending_verification`, `valid`,
  `rejected` and `review`.
- A future throttle/cache/rate-limit guard patch is still recommended as a
  production and scaling guardrail, not as an urgent fix for a bad provider.

## Backend Patch 2C-7D — Public Profile Lookup Guardrails

Patch 2C-7D adds frontend/server-side guardrails around the SearchApi public
profile lookup adapter. It is still staging/local only and does not activate
SearchApi in production.

Design choice:

- in-memory server-side cache and throttle for the current Next.js runtime;
- no DB migration and no dedicated cache table in this patch;
- `ig_accounts` public profile columns remain useful after account creation, but
  they are not sufficient for pre-create `not_found` caching or CT bulk
  verification;
- CT bulk still requires a future queue/cache table or equivalent durable cache.

Guardrails:

- cache TTL defaults: `found` 24h, `not_found` 1h, `rate_limited` /
  `unavailable` / `provider_error` 10m;
- `provider_not_configured` is not cached long-term;
- repeated usernames can hit cache without another SearchApi call;
- internal throttle defaults are conservative and configurable with
  `INSTAGRAM_PUBLIC_PROFILE_LOOKUP_MIN_INTERVAL_MS` and
  `INSTAGRAM_PUBLIC_PROFILE_LOOKUP_MAX_PER_MINUTE`;
- throttle hits map to `rate_limited` with reason `provider_throttled`, and Add
  Profile keeps fail-open behavior;
- metadata stays bounded and safe: `cache_hit`, `throttle_hit`, `rate_limited`,
  `latency_ms`, provider mode/status/reason. No raw response, full URL, API key,
  headers, cookies, sessions or secrets are logged or stored.

CT-2 adds the first durable CT bulk verification queue. Bulk import still does
not fan out SearchApi/provider calls during form submission; accepted rows are
inserted as `pending_verification` and receive one
`ct_target_verification_jobs` row. The dashboard batch route claims a small
number of jobs, runs provider lookup through the existing cache/throttle layer,
updates `ig_targets`, and writes safe audit events. Transient
`rate_limited`/`unavailable`/`provider_error`/provider-not-configured paths are
retried with bounded backoff and never become `rejected_not_found`.

## Backend Patch CT-1 — Target Account Add / Bulk Verification Foundation

CT-1 prepares target account add/import without connecting CT to worker runtime
or follow execution.

Data model:

- `ig_targets` remains the source of truth for CT rows per account.
- CT-1 adds safe verification/quality fields, batch id, source, actor type,
  soft archive/reject reasons and safe metadata.
- `ct_target_audit_events` stores safe add/bulk/verify audit events with counts
  and reasons only.
- `ig_interacted_users` remains the future interaction/FBR source, not a target
  import table and not a raw client/admin projection.

Behavior:

- single add normalizes the username, blocks invalid syntax, checks duplicates
  for the same account, and uses the safe public profile lookup if configured;
- clear `not_found` rejects with `rejected_not_found`;
- `found` applies quality V1: `<500` followers, verified and private profiles
  are rejected; eligible public profiles become `valid`;
- provider unavailable/rate-limited/error and provider-not-configured paths
  remain review/pending and never become `not_found`;
- bulk import classifies every line as pending, invalid syntax,
  duplicate-in-batch or duplicate-existing and inserts only accepted rows as
  `pending_verification`;
- CT-2 creates durable verification jobs for accepted bulk rows only. Invalid
  syntax and duplicates do not receive jobs;
- `POST /api/instagram-dashboard/targets/verify-batch` processes a small
  service-role/admin-safe batch, applies Quality V1, schedules bounded retries
  for transient provider failures, and emits safe aggregate results;
- CT-2B makes that route processor-ready: `limit` remains bounded to 10,
  `worker_id` is sanitized, `dry_run=true` previews claimable jobs without
  mutation/provider calls, `duration_ms` and explicit summary counts are
  returned, and `rate_limited` stops the batch early while requeueing already
  claimed unprocessed jobs with safe retry metadata;
- CT-2B updates `claim_ct_target_verification_jobs` so expired `processing`
  locks can be reclaimed after the 15 minute lock window. `retry_scheduled`
  still respects `next_attempt_at`, clear `not_found` never retries, and
  provider-not-configured/rate-limited/unavailable/provider-error paths never
  become `rejected_not_found`;
- bulk/job verification does not activate SearchApi production. Provider mode
  remains controlled by existing safe environment configuration.

CT Quality V1 decision engine:

- CT-3 centralizes Quality V1 in a pure frontend/server helper,
  `lib/instagram-target-quality.ts`, used by single add and batch verification
  mappings;
- source of truth remains `ig_targets` for shared admin/client/BotApp fields:
  `status`, `quality_status`, `verification_status`, `verification_reason`,
  `rejected_reason`, `source`, `actor_type`, soft archive/delete state and safe
  audit correlation;
- decision priority is explicit: clear `not_found`, followers below 500,
  verified profile, private profile, canonical username mismatch, provider
  unavailable/rate-limited/error, then eligible;
- canonical mismatch is `review_username_changed`, not a destructive rejection;
- missing avatar is warning-only and does not reject a CT in V1;
- FBR remains future runtime performance (`followers gained / follows sent from
  this CT`) and is never part of CT Quality V1.

Surface sync contract:

- `valid` / `eligible` is usable and visible to admin, client and BotApp once
  those surfaces consume CT rows;
- `rejected_*` must expose only safe reasons and remain auditable;
- `review_*` is admin-first and may be shown to client/BotApp only with safe
  messages;
- `archived` / `deleted` remain soft states that must not diverge silently
  across admin, client and BotApp.

CT-4 lifecycle sync contract:

- `ig_targets` remains the source of truth for CT lifecycle and quality state.
- Delete from the admin CT panel is a soft archive only: `status=archived`,
  `archived_at` set, `archive_reason=dashboard_archive`; there is no hard delete.
- Restore/unarchive is explicit and separate from reset. Restore only applies to
  archived targets, never creates a new CT, never changes FBR/performance fields
  and must fail with `duplicate_existing_active` if the same account already has
  an active CT with the same `normalized_username`.
- Restore may return a CT directly to `valid` only when existing quality is
  `eligible`, provider status is `found`, and the provider check is recent.
  Otherwise restore clears archive state and puts the CT back into
  `pending_verification` for queue-based revalidation.
- Reset is technical re-verification and must not unarchive. Archived/deleted CTs
  must be restored before reset.
- Lifecycle audit events are safe rows in `ct_target_audit_events`:
  `target_archive`, `target_restore`, `target_reset`, with actor/source surface,
  previous/next status, reason, account and target identifiers.
- Client dashboard and BotApp CT surfaces are not implemented by CT-4. Future
  readers must use `ig_targets` or a safe projection from it, and BotApp must not
  use archived/rejected/pending/review CTs as active runtime targets.

CT-5 target activity visibility:

- admin Activity Log reads a safe projection from `ct_target_audit_events`;
- displayed CT operations are `target_add_single`, `target_add_bulk`,
  `target_verify`, `target_archive`, `target_restore` and `target_reset`;
- raw `metadata_safe` is never displayed. The UI may project only allowlisted
  safe fields such as `source_surface`, `previous_status` and `next_status`;
- visible fields are timestamp, actor type, action label, account/target safe
  labels or short ids, result/status, safe reason, short `batch_id` and source
  surface;
- client dashboard and BotApp audit surfaces remain future work and must consume
  the same safe audit source/projection when implemented.

Future scheduler readiness:

- CT-2C adds `GET|POST /api/instagram-dashboard/targets/verify-cron`, a
  token-protected internal entrypoint disabled by default
  (`CT_TARGET_VERIFICATION_CRON_ENABLED=false`, `DRY_RUN=true`, small `limit`).
  It reuses `processTargetVerificationBatch`, acquires an expiring Supabase
  scheduler lock, and returns safe skip reasons (`cron_disabled`,
  `scheduler_lock_busy`) without leaking secrets;
- no active `vercel.json` cron is shipped. External schedulers may call the route
  with `Authorization: Bearer` or `x-ct-target-verification-cron-token`;
- CT-2C does not activate SearchApi production and does not touch phones,
  worker Python, follow runtime, FBR or optimization policy.

CT smoke cleanup guardrail:

- never delete `ct_target_audit_events` by `metadata_safe.source` alone. Values
  such as `target_add_bulk` and `target_verify_batch` identify functional write
  paths, not a unique smoke run;
- every CT smoke must collect its explicit smoke `account_id`, inserted
  `target_id` values, queued `job_id` values and `batch_id` values before
  cleanup. Also scope to the strict smoke account/username. Use `created_at`
  only as a secondary guard;
- audit cleanup may delete only rows matching those explicit smoke ids plus the
  expected operation/account. If audit immutability is safer, leave smoke audit
  rows in place instead of broad-deleting shared source values.

CT-2 staging incident: the first audit cleanup used a predicate that was too
broad because it matched shared `metadata_safe.source` values. Probable impact
is audit-only: around 25 `ct_target_audit_events` bulk/verify rows were removed.
Postchecks did not show real non-smoke targets or jobs deleted/modified by that
predicate. Exact deleted audit row identities are not recoverable from the
current DB without PITR/logs, so no blind replay should be attempted.

Out of scope: worker Python, follow engine, runtime Instagram, SearchApi
production activation, Vercel prod env, hard-delete CT, FBR optimization,
Target Discovery IA/MCP, cron scheduling and raw provider payload storage.

Entry 2D-2B deliberately does not add dashboard UI, dashboard actions,
provisioning/login workers, secret reads for workers, or credential incidents.
Entry 2D-3 should add safe status APIs, and Entry 2D-4 should add the dashboard
action model and pending-action count.

Security NO-GO for this API:

- no password in Supabase app tables;
- no password in `account_incidents`, `runtime_events`, Slack/Discord, or
  notification payload audits;
- no password or full request body in logs;
- no direct PostgREST client write to `account_credentials`;
- no client-readable `account_credentials`;
- no full `secret_ref` in client responses.

## Entry 2D-3A Safe Credential Status API

Entry 2D-3A extends `supabase/functions/instagram-credentials` with a safe
status read action for dashboards:

```json
{
  "action": "status",
  "account_id": "00000000-0000-4000-8000-000000000000"
}
```

The status action uses the same authentication boundary as submit/update:

- client dashboard calls use a Supabase Auth JWT;
- internal/admin producers may use `INSTAGRAM_CREDENTIALS_INTERNAL_API_TOKEN`;
- client JWT requests must pass `client_can_manage_instagram_account`;
- the Edge Function uses the service-role key server-side.

The status action does **not** require or accept a password. It rejects secret
or write-only fields such as `password`, `secret_ref`, `secret_provider`,
`metadata`, `token`, `cookie`, `raw_secret`, `webhook_url`, and
`service_role`.

Data sources:

- `public.client_instagram_accounts` for `onboarding_status`,
  `provisioning_status`, and `login_status`;
- active `public.account_credentials` metadata for `credentials_version`,
  `status`, `reauth_required`, `reauth_reason`, `last_submitted_at`, and
  `last_rotated_at`.

The status action deliberately does not read Supabase Vault, decrypt secrets,
read `account_incidents`, or read dashboard actions.

Safe response:

```json
{
  "ok": true,
  "account_id": "00000000-0000-4000-8000-000000000000",
  "provider": "instagram",
  "credentials_configured": true,
  "credentials_version": 2,
  "credentials_status": "active",
  "reauth_required": true,
  "reauth_reason": "awaiting_login_verification",
  "last_submitted_at": "2026-05-25T18:00:00Z",
  "last_rotated_at": null,
  "onboarding_status": "configured",
  "provisioning_status": "pending",
  "login_status": "pending",
  "next_action": "awaiting_login_verification",
  "safe_client_message": "Credentials saved. Login verification is pending."
}
```

When no active credentials exist, `credentials_configured=false`,
`credentials_version=null`, `credentials_status=null`, `reauth_required=false`,
and `next_action='submit_credentials'`.

`next_action` V1 mapping:

- no active credentials → `submit_credentials`;
- `login_status='needs_2fa'` → `complete_2fa`;
- `login_status='checkpoint'` → `resolve_checkpoint`;
- `login_status='mismatch'` → `contact_support`;
- `login_status='failed'` → `update_password`;
- active credentials with
  `reauth_reason='awaiting_login_verification'` → `awaiting_login_verification`;
- other `reauth_required=true` → `update_password`;
- `login_status='connected'` and `reauth_required=false` → `none`;
- fallback with configured credentials → `awaiting_login_verification`.

Dashboard relationship:

- the credentials form uses Entry 2D-2B submit/update;
- the status widget can use Entry 2D-3A status;
- dashboard badge, popup, pending action count, and deep links are reserved for
  Entry 2D-4 dashboard actions;
- Slack/Discord remains operator alerting, not dashboard state.

Security NO-GO for status:

- no password in the response;
- no Vault payload or Vault read;
- no `secret_ref` or full secret reference;
- no raw `metadata`;
- no direct PostgREST client read of `account_credentials`;
- no direct dashboard read of `account_incidents`.

## Entry 2D-4A Centre d'actions dashboard

Entry 2D-4A ajoute le socle schema-only
`public.account_dashboard_actions`. Cette table est le modele d'etat UI pour les
actions affichables dans les dashboards client, admin, assistant et app Mac.
Elle prepare les pastilles de compteur, les panneaux d'actions, les popups
bloquantes, les deep-links vers les bons formulaires et les resolutions futures.

Cette table ne remplace pas les incidents :

- `account_incidents` reste la source operationnelle durable pour les incidents,
  alertes, raisons d'echec et messages admin/assistant;
- `account_incident_notifications` reste uniquement l'audit de livraison
  Slack/Discord;
- `account_dashboard_actions` est la projection actionnable pour l'UI dashboard.

Le modele contient notamment :

- `action_type`, `action_label`, `action_deep_link`;
- `status in ('pending','acknowledged','pending_verification','resolved','dismissed','ignored')`;
- `severity in ('info','warning','error','critical')`;
- `audience in ('client','admin','assistant','ops')`;
- `requires_client_action`;
- `blocking_campaign`;
- `safe_client_message`, `assistant_message`, `admin_message`;
- un `dedupe_key` unique pour les actions actives;
- `metadata` strictement safe.

`action_deep_link` doit rester une route interne dashboard sure, par exemple
vers le formulaire de credentials, le panneau 2FA, la verification checkpoint,
les Target Accounts ou les DM templates. Il ne doit jamais contenir d'URL
secrete, webhook, `secret_ref` ou payload Vault.

Les `action_type` V1 documentes sont :

- credentials/login : `submit_instagram_credentials`,
  `update_instagram_password`, `reconnect_instagram`,
  `review_login_failure`, `complete_two_factor`, `resolve_checkpoint`,
  `confirm_username_change`, `review_account_mismatch`, `contact_support`;
- Growth/Targets : `add_targets`, `review_targets`;
- DM templates : `update_dm_template_welcome`,
  `update_dm_template_outreach`, `review_dm_template`.

La migration ne rend pas cette liste stricte par contrainte SQL afin d'eviter
des migrations frequentes pour chaque nouvelle action produit. Les producteurs
et futures APIs Edge/RPC devront cependant garder une allow-list applicative.

Mappings prepares :

- `credentials_configured=false` -> `submit_instagram_credentials`;
- `reauth_required=true` -> `update_instagram_password`;
- `login_status='needs_2fa'` -> `complete_two_factor`;
- `login_status='checkpoint'` -> `resolve_checkpoint`;
- `login_status='mismatch'` -> `review_account_mismatch`;
- besoin de CT/Target Accounts -> `add_targets`;
- CT insuffisants ou FBR faible -> `review_targets`;
- template DM manquant ou a revoir -> `update_dm_template_welcome`,
  `update_dm_template_outreach` ou `review_dm_template`.

Securite et acces :

- RLS activee;
- aucun acces direct `anon` ou `authenticated`;
- `service_role` uniquement en V1;
- les dashboards devront passer par de futures APIs Edge/RPC safe;
- aucun password, `secret_ref`, payload Vault, webhook URL, token, cookie,
  service-role data, screenshot/XML brut ou metadata sensible ne doit etre
  stocke;
- `admin_message` ne doit pas etre expose a l'audience client par les futures
  APIs.

Phases futures :

- Entry 2D-4B : APIs Edge/RPC safe pour `count` et `list`
  (`pending_count`, `blocking_count`, `client_required_count`, filtres par
  compte, audience, statut et severite);
- Entry 2D-4C : helpers create/update/sync actions et premiere liaison avec
  credentials/status;
- Entry 2D-4D : acknowledge/dismiss/resolve;
- Entry 2E/2F : worker login/provisioning et publishers incidents qui creent,
  synchronisent ou resolvent les actions;
- dashboard repo : badge, popup, panneau, deep-links et formulaires.

## Entry 2D-4B API safe `dashboard-actions`

Entry 2D-4B ajoute `supabase/functions/dashboard-actions`, une Edge Function de
lecture safe pour le centre d'actions dashboard. Elle expose uniquement :

- `action='count'` pour les compteurs de pastille et de blocage;
- `action='list'` pour le panneau pagine des actions.

La fonction ne modifie aucune action. Les mutations `acknowledge`, `dismiss` et
`resolve` restent reservees a Entry 2D-4D.

Requete V1 :

```json
{
  "action": "count",
  "account_id": "optional uuid",
  "audience": "client",
  "status": "pending",
  "limit": 20,
  "offset": 0
}
```

Authentification et filtrage :

- client dashboard : Supabase Auth JWT;
- internal/admin/app Mac : `DASHBOARD_ACTIONS_INTERNAL_API_TOKEN`;
- le client JWT est force sur `audience='client'`;
- le client JWT ne peut pas demander `admin`, `assistant` ou `ops`;
- si `account_id` est fourni, l'ownership passe par
  `client_can_manage_instagram_account`;
- sans `account_id`, le client voit les actions liees a son `client_id` ou a ses
  comptes via `client_instagram_accounts`;
- le chemin internal peut filtrer par `audience`, `account_id` et `status`, mais
  la reponse reste safe.

Statuts actifs pour les badges :

```text
pending, acknowledged, pending_verification
```

Reponse `count` :

```json
{
  "ok": true,
  "pending_count": 2,
  "blocking_count": 1,
  "client_required_count": 1,
  "counts_by_severity": {
    "info": 0,
    "warning": 1,
    "error": 1,
    "critical": 0
  }
}
```

Definitions :

- `pending_count` = actions actives selon les filtres visibles;
- `blocking_count` = actions actives avec `blocking_campaign=true`;
- `client_required_count` = actions actives `audience='client'` et
  `requires_client_action=true`;
- `counts_by_severity` = actions actives groupees par severite.

Reponse `list` :

```json
{
  "ok": true,
  "actions": [
    {
      "id": "00000000-0000-4000-8000-000000000000",
      "account_id": "00000000-0000-4000-8000-000000000000",
      "action_type": "submit_instagram_credentials",
      "status": "pending",
      "severity": "warning",
      "audience": "client",
      "requires_client_action": true,
      "blocking_campaign": true,
      "title": "Connect Instagram",
      "safe_client_message": "Instagram credentials are required.",
      "action_label": "Connect Instagram",
      "action_deep_link": "/accounts/00000000-0000-4000-8000-000000000000/connect-instagram",
      "created_at": "2026-05-25T20:00:00Z",
      "updated_at": "2026-05-25T20:01:00Z"
    }
  ],
  "next_offset": null
}
```

Pagination V1 :

- `limit` est borne entre `1` et `100`, defaut `20`;
- `offset` est borne a `0` minimum;
- tri `created_at desc, id desc`;
- un cursor `created_at/id` pourra remplacer `offset` plus tard si necessaire.

Champs volontairement exclus :

- `admin_message` pour les clients;
- `assistant_message` pour les clients;
- `metadata` par defaut;
- `incident_id` tant qu'il n'est pas utile a l'UI V1;
- password, `secret_ref`, payload Vault, webhook URL, token, cookie,
  service-role data.

Securite :

- aucun acces direct client PostgREST a `account_dashboard_actions`;
- toutes les lectures passent par l'Edge Function et le service-role cote
  serveur;
- aucun payload Slack/Discord n'est reutilise comme payload UI;
- aucun compte hors ownership ne doit etre visible au client;
- aucune mutation d'action n'est incluse dans Entry 2D-4B.

## Entry 2D-4C-1 RPC `upsert_account_dashboard_action`

Entry 2D-4C-1 ajoute une RPC schema-level :
`public.upsert_account_dashboard_action(...)`.

Cette RPC est le point d'ecriture atomique pour creer ou synchroniser les rows
`public.account_dashboard_actions`. Elle reste volontairement bas niveau et
service-role only : elle ne branche pas encore les Edge Functions, les workers,
les incidents runtime, les runners, les senders ou le dashboard UI.

Pourquoi une RPC :

- `account_dashboard_actions` utilise un index unique partiel sur le
  `dedupe_key` des actions actives;
- PostgREST upsert ne porte pas bien la logique de conflit active-only;
- la RPC peut faire un `select ... for update`, appliquer les regles metier,
  puis inserer ou mettre a jour dans une transaction;
- le meme helper pourra etre appele plus tard par les Edge Functions, les
  workers de login/provisioning et les publishers incidents.

Statuts actifs :

```text
pending, acknowledged, pending_verification
```

Comportement de dedupe :

- si aucune action active n'existe pour `dedupe_key`, la RPC insere une nouvelle
  action;
- si une action active existe, la RPC verrouille la row et met a jour les champs
  safe;
- si une action `resolved`, `dismissed` ou `ignored` existe avec le meme
  `dedupe_key`, elle reste historique et une nouvelle action active peut etre
  creee;
- en cas de course concurrente sur l'insert, la RPC retente et met a jour la row
  active nouvellement creee.

Regles de mise a jour active :

- `updated_at` est rafraichi;
- `status` ne peut rester ou devenir qu'un statut actif pendant l'upsert d'une
  action deja active; un upsert ne sert pas a resoudre ou masquer l'action;
- `severity` utilise le max metier :
  `info < warning < error < critical`;
- `client_id`, `incident_id`, audience, messages safe, label, deep-link et flags
  peuvent etre rafraichis par les producteurs service-role;
- `metadata` est fusionne en shallow merge :
  `existing.metadata || incoming_metadata`;
- il n'y a pas d'`occurrence_count` dans cette table, contrairement a
  `account_incidents`.

Validation et securite :

- `account_id`, `action_type`, `title` et `dedupe_key` sont obligatoires;
- `status`, `severity` et `audience` doivent respecter les enums de la table;
- `metadata` doit etre un objet JSON;
- les cles metadata sensibles top-level sont rejetees, notamment `password`,
  `secret`, `secret_ref`, `raw_secret`, `token`, `cookie`, `webhook`,
  `webhook_url`, `vault`, `service_role`, `authorization` et `bearer`;
- aucun password, payload Vault, URL webhook, token/cookie, XML brut ou
  screenshot brut ne doit etre stocke dans `account_dashboard_actions`;
- execute est revoke pour `public`, `anon` et `authenticated`, puis grant
  uniquement a `service_role` (le role owner/postgres conserve son acces);
- aucun client dashboard ne doit appeler cette RPC directement.

Mappings futurs prepares :

- `credentials_configured=false` -> `submit_instagram_credentials`;
- `reauth_required=true` -> `update_instagram_password`;
- `login_status='needs_2fa'` -> `complete_two_factor`;
- `login_status='checkpoint'` -> `resolve_checkpoint`;
- `login_status='mismatch'` -> `review_account_mismatch`;
- incident `active_instagram_account_mismatch` ->
  `review_account_mismatch` pour `admin` ou `assistant`.

Etapes futures :

- Entry 2D-4C-2 : wiring depuis credentials/status ou helpers internal;
- Entry 2D-4D : mutations `acknowledge`, `dismiss`, `resolve`;
- Entry 2E : worker login/provisioning qui cree et resout les actions selon
  `login_status`;
- Entry 2F : publishers `account_incidents` -> `account_dashboard_actions`.

## Entry 2D-4C-2A Sync credentials vers actions dashboard

Entry 2D-4C-2A branche uniquement les succes `submit` et `update_password` de
`supabase/functions/instagram-credentials` vers
`public.upsert_account_dashboard_action(...)`.

Le branchement est volontairement limite :

- il s'execute apres creation du secret Vault et apres ecriture reussie de la
  metadata `account_credentials`;
- il cree ou synchronise une action dashboard en `pending_verification`;
- il ne lit jamais Vault;
- il ne touche pas aux workers, runners, senders, orchestrators ou publishers
  incidents;
- `action=status` reste read-only et ne cree aucune action dashboard.

Mapping `submit` :

- `action_type='submit_instagram_credentials'`;
- `status='pending_verification'`;
- `severity='info'`;
- `audience='client'`;
- `requires_client_action=false`;
- `blocking_campaign=true`;
- `title='Connexion Instagram en vérification'`;
- `safe_client_message='Vos identifiants Instagram ont été enregistrés. Nous vérifions maintenant la connexion.'`;
- `action_label='Voir le statut'`;
- `action_deep_link='/accounts/{account_id}/connect-instagram'`;
- `dedupe_key='account:{account_id}:dashboard_action:submit_instagram_credentials'`.

Mapping `update_password` :

- `action_type='update_instagram_password'`;
- `status='pending_verification'`;
- `severity='info'`;
- `audience='client'`;
- `requires_client_action=false`;
- `blocking_campaign=true`;
- `title='Mot de passe Instagram en vérification'`;
- `safe_client_message='Votre mot de passe a été mis à jour. Nous vérifions maintenant la connexion.'`;
- `action_label='Voir le statut'`;
- `action_deep_link='/accounts/{account_id}/credentials#password'`;
- `dedupe_key='account:{account_id}:dashboard_action:update_instagram_password'`.

Metadata envoyee a la RPC :

```json
{
  "source": "instagram_credentials",
  "action": "submit",
  "credentials_version": 1,
  "request_id": "safe-request-id",
  "external_request_id": "optional-safe-id"
}
```

Cette metadata ne doit jamais contenir password, `secret_ref`, identifiant Vault,
payload Vault, token, cookie, webhook URL ou body brut de requete.

Fail-open :

- si la synchronisation dashboard action echoue, `submit` / `update_password`
  restent OK lorsque Vault et `account_credentials` ont deja reussi;
- l'erreur est journalisee avec un log safe
  `instagram_credentials_dashboard_action_sync_failed`;
- la reponse client V1 reste inchangée et ne contient pas de metadata dashboard.

Resolution future :

- 2D-4C-2A ne resout aucune action;
- `connected + reauth_required=false` sera traite plus tard via 2D-4D/2E;
- les mappings `credentials_configured=false`, `needs_2fa`, `checkpoint`,
  `failed` et `mismatch` restent reserves a un sync dedie ou aux workers futurs.

## Entry 2D-4D-1 RPC `transition_account_dashboard_action`

Entry 2D-4D-1 ajoute la RPC schema-only
`public.transition_account_dashboard_action(...)`.

Cette RPC est le point atomique de transition de statut pour les rows
`public.account_dashboard_actions`. Elle prepare les mutations dashboard futures
`acknowledge`, `dismiss` et `resolve`, sans encore exposer de mutation dans
`supabase/functions/dashboard-actions`.

Pourquoi une RPC :

- les transitions doivent verrouiller la row avec `select ... for update`;
- les statuts terminaux doivent liberer le `dedupe_key` actif de facon
  coherente;
- les timestamps `acknowledged_at`, `dismissed_at` et `resolved_at` doivent
  etre poses dans la meme transaction que le changement de statut;
- les workers futurs 2E/2F pourront resoudre des actions sans dupliquer les
  regles metier cote Edge ou Python.

Signature :

```sql
public.transition_account_dashboard_action(
  p_action_id uuid,
  p_new_status text,
  p_actor_type text,
  p_actor_id uuid default null,
  p_reason text default null,
  p_metadata jsonb default '{}'::jsonb
)
```

Transitions autorisees V1 :

| Statut source | `acknowledged` | `dismissed` | `resolved` | `ignored` |
|---------------|----------------|-------------|------------|-----------|
| `pending` | oui | oui | oui | oui |
| `acknowledged` | oui, idempotent | oui | oui | oui |
| `pending_verification` | oui | oui | oui | oui |
| `resolved` | non | non | non | non |
| `dismissed` | non | non | non | non |
| `ignored` | non | non | non | non |

Les statuts `pending` et `pending_verification` restent crees uniquement par les
producteurs comme `upsert_account_dashboard_action`. La RPC de transition ne
sert jamais a rouvrir une action active depuis un statut terminal.

Timestamps :

- `acknowledged` pose `acknowledged_at = coalesce(acknowledged_at, now())`;
- `dismissed` pose `dismissed_at = now()`;
- `resolved` pose `resolved_at = now()`;
- `ignored` pose seulement `updated_at` et la metadata de transition, sans
  forcer `resolved_at` ou `dismissed_at`;
- `updated_at` est rafraichi sur toute transition reussie;
- les timestamps historiques ne sont pas effaces, par exemple
  `acknowledged_at` reste present si une action acknowledged devient resolved.

Metadata de transition :

```json
{
  "last_transition": "resolved",
  "actor_type": "internal",
  "actor_id": "optional-uuid",
  "reason": "optional safe text",
  "transition_at": "2026-05-25T21:00:00Z"
}
```

La fusion est volontairement shallow :

```text
existing.metadata || p_metadata || transition_metadata
```

`p_metadata` doit etre un objet JSON et ne doit jamais contenir en cle
top-level : password, secret, `secret_ref`, `raw_secret`, token, cookie,
webhook, `webhook_url`, vault, `service_role`, authorization, bearer, XML brut
ou screenshot. `p_reason` est optionnel et borne a 500 caracteres.

Securite :

- la RPC est `SECURITY DEFINER` avec `search_path = public`;
- execute est revoke pour `public`, `anon` et `authenticated`;
- execute est grant uniquement a `service_role` (le role owner/postgres conserve
  son acces);
- aucun client dashboard ne doit appeler cette RPC directement;
- la RPC ne gere pas l'ownership client : ce controle appartient a l'Edge
  Function `dashboard-actions` en Entry 2D-4D-2;
- elle ne lit jamais Vault et ne stocke jamais password, `secret_ref`, payload
  Vault, webhook URL, token ou cookie.

Entry 2D-4D-2 utilisera cette RPC depuis `dashboard-actions` pour exposer
`action='acknowledge'`, `action='dismiss'` et `action='resolve'`. Le chemin
client JWT devra appliquer l'ownership, l'audience client et les restrictions UI
avant appel RPC. Le chemin internal token pourra resoudre
`pending_verification` apres verification worker ou support.

## Entry 2D-4D-2 Mutations Edge `dashboard-actions`

Entry 2D-4D-2 etend `supabase/functions/dashboard-actions` avec trois mutations
safe :

- `action='acknowledge'`;
- `action='dismiss'`;
- `action='resolve'`.

Ces mutations restent exposees uniquement via l'Edge Function. Le dashboard ne
lit ni ne modifie jamais `public.account_dashboard_actions` en direct via
PostgREST. L'Edge Function charge une row safe par `action_id`, applique
l'authentification et l'ownership, puis appelle
`public.transition_account_dashboard_action(...)` avec le service-role.

Requete mutation V1 :

```json
{
  "action": "acknowledge",
  "action_id": "00000000-0000-4000-8000-000000000000",
  "reason": "optional safe text"
}
```

`action_id` est obligatoire et doit etre un UUID. `reason` est optionnel et
borne a 500 caracteres. Les champs sensibles sont rejetes comme pour `count` et
`list` : password, `secret_ref`, token, cookie, webhook URL, service-role data
ou `metadata.password`.

Chargement avant mutation :

- la row est lue via service-role par `action_id`;
- les champs charges sont limites a l'etat et aux champs de reponse safe :
  `id`, `account_id`, `client_id`, `action_type`, `status`, `severity`,
  `audience`, `requires_client_action`, `blocking_campaign`, `title`,
  `safe_client_message`, `action_label`, `action_deep_link`,
  `acknowledged_at`, `dismissed_at`, `resolved_at`, `created_at`,
  `updated_at`;
- `metadata`, `admin_message`, `assistant_message`, `incident_id`, secrets,
  payload Vault et webhook URL ne sont pas charges pour la reponse V1.

Regles client JWT :

- le client peut muter uniquement les actions `audience='client'`;
- l'action doit appartenir au client via `client_id` ou via ownership
  `client_can_manage_instagram_account`;
- `acknowledge` est autorise pour `pending`, `acknowledged` et
  `pending_verification`;
- `dismiss` est interdit pour `pending_verification`;
- `dismiss` est interdit lorsque `blocking_campaign=true` et
  `requires_client_action=true`;
- `resolve` est interdit pour `pending_verification`;
- `resolve` est interdit lorsque `blocking_campaign=true` ou
  `requires_client_action=true`;
- en V1, le client peut donc resoudre seulement une action client non bloquante
  et non requise.

Regles internal token :

- le chemin internal peut `acknowledge`, `dismiss` ou `resolve` toutes les
  audiences;
- il peut resoudre `pending_verification` apres verification worker ou support;
- il peut masquer une action bloquante si l'operation est volontaire;
- la reponse reste safe et ne retourne pas de metadata brute.

Appel RPC :

```json
{
  "p_action_id": "...",
  "p_new_status": "acknowledged",
  "p_actor_type": "client",
  "p_actor_id": "auth-user-id-or-null",
  "p_reason": "optional safe text",
  "p_metadata": {
    "source": "dashboard_actions_edge",
    "request_id": "safe-request-id",
    "mutation": "acknowledge"
  }
}
```

La metadata envoyee a la RPC ne contient jamais token, body complet, password,
`secret_ref`, payload Vault ou webhook URL.

Reponse mutation V1 :

```json
{
  "ok": true,
  "request_id": "safe-request-id",
  "dashboard_action": {
    "id": "00000000-0000-4000-8000-000000000000",
    "account_id": "00000000-0000-4000-8000-000000000000",
    "action_type": "submit_instagram_credentials",
    "status": "acknowledged",
    "severity": "info",
    "audience": "client",
    "requires_client_action": false,
    "blocking_campaign": true,
    "title": "Connexion Instagram en vérification",
    "safe_client_message": "Vos identifiants Instagram ont été enregistrés.",
    "action_label": "Voir le statut",
    "action_deep_link": "/accounts/00000000-0000-4000-8000-000000000000/connect-instagram",
    "acknowledged_at": "2026-05-25T21:30:00Z",
    "dismissed_at": null,
    "resolved_at": null,
    "created_at": "2026-05-25T21:00:00Z",
    "updated_at": "2026-05-25T21:30:00Z"
  }
}
```

`acknowledged` reste un statut actif pour les compteurs V1. `dismissed` et
`resolved` sortent des statuts actifs et font disparaitre l'action des badges
par defaut. Les statuts `resolved`, `dismissed` et `ignored` sont terminaux :
une mutation V1 retourne `transition_not_allowed` si elle vise deja une action
terminale.

Erreurs stables :

- `invalid_action`;
- `action_id_invalid`;
- `action_not_found`;
- `transition_not_allowed`;
- `audience_not_allowed`;
- `account_not_allowed`;
- `field_forbidden:*`;
- `internal_error`.

Les logs de mutation restent safe : `request_id`, `action`, `action_id`,
`actor_type`, statut resultat et code erreur stable. Ils ne doivent jamais
inclure le body complet, un token, une raison non bornee, une metadata brute ou
un secret.

## Entry 2E-1 Modele de statuts login/provisioning/onboarding

Entry 2E-1 stabilise le modele de statuts de
`public.client_instagram_accounts` avant tout branchement runtime. Cette etape
reste schema-first et documentation-only cote application : aucun worker login,
aucun patch Python, aucune Edge Function et aucun run device ne sont ajoutes.

Sources de verite :

- `account_credentials` garde uniquement la metadata credentials et les champs
  safe `credentials_version`, `reauth_required`, `reauth_reason`,
  `last_submitted_at` et `last_rotated_at`;
- `client_instagram_accounts` devient la source de verite dashboard-safe pour
  `onboarding_status`, `provisioning_status` et `login_status`;
- `account_dashboard_actions` represente les actions UI visibles via les APIs
  safe `dashboard-actions`;
- `account_incidents` reste la source durable des incidents ops/admin;
- `account_assignments`, `phone_devices` et `phone_clones` restent la source
  d'affectation device/clone, sans exposition client directe.

Les contraintes SQL existantes de `client_instagram_accounts` etaient :

```text
onboarding_status in ('pending', 'configured', 'ready', 'blocked')
provisioning_status in ('not_started', 'pending', 'provisioning', 'ready', 'failed')
login_status in ('unknown', 'pending', 'connected', 'needs_2fa', 'checkpoint', 'failed', 'mismatch')
```

Entry 2E-1 elargit ces CHECK constraints sans renommer les valeurs existantes
et sans modifier les donnees.

`login_status` V1 :

- `unknown` : etat non encore determine;
- `pending` : login attendu ou en attente de tentative;
- `verification_pending` : credentials recus, verification login a lancer ou en
  cours;
- `connected` : compte Instagram connecte et verifie;
- `needs_2fa` : Instagram demande une validation 2FA;
- `checkpoint` : Instagram demande un checkpoint/security challenge;
- `failed` : tentative login echouee;
- `mismatch` : le compte Instagram actif ne correspond pas au compte attendu;
- `logged_out` : session explicitement deconnectee ou expiree.

`provisioning_status` V1 :

- `not_started` : aucune preparation lancee;
- `pending` : preparation demandee;
- `assigned` : device/clone affecte;
- `provisioning` : etat historique conserve pour compatibilite;
- `login_pending` : login a executer;
- `login_verification_pending` : verification login en cours ou a confirmer;
- `ready` : compte pret pour les flows business;
- `failed` : provisioning echoue;
- `blocked` : provisioning bloque par une action ou un incident;
- `paused` : provisioning volontairement suspendu.

`onboarding_status` V1 :

- `pending` : onboarding ouvert mais incomplet;
- `incomplete` : informations client insuffisantes;
- `credentials_required` : credentials Instagram requis;
- `configured` : valeur historique indiquant une configuration de base;
- `credentials_submitted` : credentials recus, verification a venir;
- `verification_pending` : verification login/provisioning en attente;
- `ready` : onboarding pret;
- `blocked` : onboarding bloque;
- `support_required` : intervention support requise.

Mapping vers actions dashboard :

- absence de credentials actifs -> `submit_instagram_credentials`;
- `reauth_required=true` -> `update_instagram_password`;
- `login_status='needs_2fa'` -> `complete_two_factor`;
- `login_status='checkpoint'` -> `resolve_checkpoint`;
- `login_status='failed'` -> `review_login_failure` ou
  `update_instagram_password` selon la cause;
- `login_status='mismatch'` -> `review_account_mismatch` pour `admin` ou
  `assistant`, avec `requires_client_action=false`;
- `login_status='connected'` et `reauth_required=false` -> resolution des
  actions actives `submit_instagram_credentials`, `update_instagram_password`,
  `reconnect_instagram`, `complete_two_factor`, `resolve_checkpoint` et
  `review_login_failure`.

Phases futures :

- Entry 2E-2 : RPC/helper de mise a jour statut et synchronisation actions
  depuis ces statuts;
- Entry 2E-3 : endpoint internal ou contrat provisioner pour publier les
  resultats login/provisioning;
- Entry 2E-4 : integration Python worker/provisioner derriere feature flag,
  sans modifier les flows sender/follow/outreach;
- Entry 2F : publication incidents -> dashboard actions.

Securite NO-GO :

- aucun password hors Vault;
- aucun password dans `client_instagram_accounts`;
- aucun `secret_ref` dans les APIs de statut client;
- aucun payload Vault dans dashboard/status/actions;
- aucun XML brut ou screenshot brut dans les actions dashboard;
- aucun `adb_serial`, `device_udid`, hub ou host client-side;
- aucun `admin_message` dans les reponses client;
- aucun direct PostgREST client;
- aucun patch runtime ou automatisation login tant que le contrat DB/RPC 2E
  n'est pas stabilise.

## Entry 2E-2A RPC status -> dashboard actions

Entry 2E-2A ajoute deux RPC service-role pour centraliser le contrat entre les
futurs resultats login/provisioning et les actions dashboard :

- `public.update_client_instagram_account_status(...)`;
- `public.sync_account_dashboard_actions_from_status(...)`.

Cette etape reste schema-only cote application : aucun endpoint Edge, aucun
worker Python, aucun runner, aucun webhook, aucun run device et aucune lecture
Vault ne sont ajoutes.

`update_client_instagram_account_status(...)` :

- met a jour uniquement les statuts non nuls de
  `public.client_instagram_accounts` : `login_status`,
  `provisioning_status`, `onboarding_status`;
- laisse les CHECK constraints 2E-1 valider les valeurs;
- accepte `actor_type` parmi `client`, `admin`, `assistant`, `ops`,
  `internal`, `system`, `worker`, `provisioner`;
- accepte une `reason` optionnelle bornee a 500 caracteres;
- accepte uniquement une metadata JSON objet et rejette les cles sensibles;
- peut mettre a jour l'active `account_credentials` Instagram si
  `p_reauth_required` est fourni;
- si `p_login_status='connected'` et `p_reauth_required` est absent, la RPC
  nettoie l'active credential avec `reauth_required=false` et
  `reauth_reason=null`;
- ne lit jamais Vault et ne touche jamais `secret_ref`;
- appelle ensuite `sync_account_dashboard_actions_from_status(...)`.

`sync_account_dashboard_actions_from_status(...)` :

- lit `client_instagram_accounts` par `account_id`;
- lit l'active `account_credentials` Instagram sans exposer `secret_ref`;
- derive `credentials_configured`, `reauth_required`, `reauth_reason`,
  `login_status`, `provisioning_status`, `onboarding_status`;
- cree ou synchronise les actions via `upsert_account_dashboard_action(...)`;
- resout les actions actives via `transition_account_dashboard_action(...)`;
- retourne un JSON safe avec `actions_upserted` et `actions_resolved`.

Mapping V1 :

- pas d'active credentials -> action `submit_instagram_credentials`, client,
  requise et bloquante;
- `reauth_required=true` -> action `update_instagram_password`, client, requise
  et bloquante;
- priorite explicite des statuts login avant les statuts generiques de
  verification : `needs_2fa`, `checkpoint`, `failed`, `mismatch`, `logged_out`,
  puis `connected`; seulement ensuite les cas generiques
  `verification_pending` / `login_verification_pending`;
- `login_status='needs_2fa'` -> action `complete_two_factor`, meme si
  `provisioning_status='login_verification_pending'`;
- `login_status='checkpoint'` -> action `resolve_checkpoint`, meme si
  `provisioning_status='login_verification_pending'`;
- `login_status='verification_pending'` ou
  `provisioning_status='login_verification_pending'` sans statut login explicite
  ci-dessus -> pas de nouvelle action 2FA/checkpoint; les actions credentials
  existantes restent en attente de verification;
- `login_status='failed'` -> action `review_login_failure`;
- `login_status='mismatch'` -> action `review_account_mismatch` pour
  `audience='admin'`, sans action client directe;
- `login_status='logged_out'` -> action `reconnect_instagram`;
- `login_status='connected'` et `reauth_required=false` -> resolution des
  actions actives `submit_instagram_credentials`, `update_instagram_password`,
  `reconnect_instagram`, `complete_two_factor`, `resolve_checkpoint` et
  `review_login_failure`.

Les actions `review_account_mismatch` ne sont pas resolues automatiquement en
2E-2A : la revue admin/assistant reste separee et pourra etre reliee aux
incidents en Entry 2F.

Metadata safe envoyee aux actions :

```json
{
  "source": "status_sync",
  "actor_type": "worker",
  "reason": "login_connected",
  "external_request_id": "optional-safe-id",
  "login_status": "connected",
  "provisioning_status": "ready",
  "onboarding_status": "ready"
}
```

Les cles sensibles top-level sont rejetees : password, secret, `secret_ref`,
`raw_secret`, token, cookie, webhook, `webhook_url`, vault, `service_role`,
authorization, bearer, XML brut, screenshot brut, `device_udid` et
`adb_serial`.

Retour safe :

```json
{
  "ok": true,
  "account_id": "00000000-0000-4000-8000-000000000000",
  "login_status": "connected",
  "provisioning_status": "ready",
  "onboarding_status": "ready",
  "credentials_configured": true,
  "reauth_required": false,
  "reauth_reason": null,
  "actions_upserted": [],
  "actions_resolved": [
    {
      "id": "00000000-0000-4000-8000-000000000000",
      "action_type": "submit_instagram_credentials",
      "status": "resolved"
    }
  ]
}
```

Ce JSON ne contient jamais password, `secret_ref`, payload Vault, token, cookie,
webhook, body brut, metadata sensible ou identifiant device.

Phases suivantes :

- Entry 2E-3 : endpoint internal ou contrat provisioner/admin qui appelle
  `update_client_instagram_account_status(...)`;
- Entry 2E-4 : wrappers `supabase_client` et integration worker/provisioner
  derriere feature flag;
- Entry 2F : incidents -> dashboard actions avec lien `incident_id` lorsque le
  mapping est stable.

## Entry 2E-3A API interne `instagram-account-status`

Entry 2E-3A ajoute l'Edge Function interne
`supabase/functions/instagram-account-status`. Son role est de publier les
resultats login/provisioning/status vers le contrat RPC 2E-2A, sans brancher
encore le runtime Python ni le provisioner.

Cette API est volontairement separee de :

- `instagram-credentials`, qui reste dediee au submit/update/status safe des
  credentials et a l'ecriture Vault;
- `dashboard-actions`, qui reste dediee a la lecture/mutation des actions UI;
- tout worker Python, runner, webhook ou run device.

Authentification V1 :

- internal-token only via `INSTAGRAM_ACCOUNT_STATUS_INTERNAL_API_TOKEN`;
- header `Authorization: Bearer <token>`;
- pas de JWT client en V1;
- pas d'acces client direct;
- jamais de log du token ni du header `Authorization`.

Action V1 unique :

```json
{
  "action": "update_status",
  "account_id": "00000000-0000-4000-8000-000000000000",
  "login_status": "connected",
  "provisioning_status": "ready",
  "onboarding_status": "ready",
  "reauth_required": false,
  "reauth_reason": null,
  "reason": "login_connected",
  "external_request_id": "optional-safe-id",
  "metadata": {
    "source": "provisioner",
    "run_id": "safe-run-id",
    "stage": "login_check"
  }
}
```

Validation :

- `action` doit valoir `update_status`;
- `account_id` est obligatoire et doit etre un UUID;
- au moins un champ parmi `login_status`, `provisioning_status`,
  `onboarding_status` ou `reauth_required` doit etre fourni;
- `reason` est optionnelle et bornee a 500 caracteres;
- `external_request_id` est optionnel, safe et borne a 120 caracteres;
- `metadata` doit etre un objet JSON si fourni;
- les champs sensibles top-level ou metadata sont rejetes :
  password, secret, `secret_ref`, `secret_provider`, `raw_secret`, token,
  cookie, webhook, `webhook_url`, vault, `vault_payload`, `service_role`,
  authorization, bearer, XML brut, screenshot brut, `adb_serial` et
  `device_udid`.

Mapping vers RPC :

```text
account_id           -> p_account_id
login_status         -> p_login_status
provisioning_status  -> p_provisioning_status
onboarding_status    -> p_onboarding_status
reauth_required      -> p_reauth_required
reauth_reason        -> p_reauth_reason
reason               -> p_reason
external_request_id  -> p_external_request_id
metadata safe        -> p_metadata
metadata.source      -> p_actor_type
```

`p_actor_type` vaut `provisioner` si `metadata.source='provisioner'`,
`worker` si `metadata.source='worker'`, sinon `internal`.

La metadata transmise a la RPC est enrichie avec :

```json
{
  "source": "provisioner",
  "edge_function": "instagram-account-status",
  "request_id": "safe-request-id"
}
```

L'API appelle uniquement :

```text
public.update_client_instagram_account_status(...)
```

Elle n'appelle pas directement `sync_account_dashboard_actions_from_status(...)`
car la RPC d'update enchaine deja le sync dashboard actions.

Exemples status/action :

- login OK :

```json
{
  "login_status": "connected",
  "provisioning_status": "ready",
  "onboarding_status": "ready",
  "reauth_required": false,
  "reason": "login_connected"
}
```

Resultat : resolution des actions login/credentials actives.

- 2FA :

```json
{
  "login_status": "needs_2fa",
  "provisioning_status": "login_verification_pending",
  "onboarding_status": "verification_pending",
  "reason": "two_factor_required"
}
```

Resultat : upsert `complete_two_factor`.

- checkpoint :

```json
{
  "login_status": "checkpoint",
  "provisioning_status": "login_verification_pending",
  "onboarding_status": "verification_pending",
  "reason": "checkpoint_required"
}
```

Resultat : upsert `resolve_checkpoint`.

- login failed :

```json
{
  "login_status": "failed",
  "provisioning_status": "failed",
  "onboarding_status": "support_required",
  "reason": "login_failed"
}
```

Resultat : upsert `review_login_failure`.

- mismatch :

```json
{
  "login_status": "mismatch",
  "provisioning_status": "blocked",
  "onboarding_status": "support_required",
  "reason": "account_identity_mismatch"
}
```

Resultat : upsert `review_account_mismatch` pour audience admin.

- logged out :

```json
{
  "login_status": "logged_out",
  "reason": "session_expired"
}
```

Resultat : upsert `reconnect_instagram`.

Fail behavior :

- auth absente ou invalide -> `401 unauthorized`;
- payload invalide -> `400` avec erreur stable;
- compte introuvable -> `404 account_not_found`;
- statut invalide / CHECK constraint -> `400 invalid_status`;
- metadata sensible -> `400 field_forbidden:*`;
- erreur RPC ou reseau -> `500 status_update_failed`.

Contrairement au sync credentials -> dashboard actions, cette API est
fail-closed : si la publication du statut echoue, le caller doit le savoir et
reessayer ou remonter l'erreur.

Reponse safe :

```json
{
  "ok": true,
  "request_id": "safe-request-id",
  "account_id": "00000000-0000-4000-8000-000000000000",
  "login_status": "connected",
  "provisioning_status": "ready",
  "onboarding_status": "ready",
  "credentials_configured": true,
  "reauth_required": false,
  "reauth_reason": null,
  "actions_upserted": [],
  "actions_resolved": [
    {
      "id": "00000000-0000-4000-8000-000000000000",
      "action_type": "complete_two_factor",
      "status": "resolved"
    }
  ]
}
```

La reponse ne contient jamais password, `secret_ref`, payload Vault, metadata
brute sensible, token, `service_role` ou webhook.

Prochaine etape :

- Entry 2E-4 : integration Python/provisioner derriere feature flag, via un
  wrapper `supabase_client`, sans modifier les flows sender/orchestrators.

## Entry 2E-4A Helper Python status publisher

Entry 2E-4A ajoute un helper Python isole
`instagram_account_status_publisher.py`. Il prepare l'integration future du
runtime/provisioner avec l'API interne `instagram-account-status`, mais ne
branche encore aucun flow runtime.

Scope volontaire :

- aucun appel depuis `runner.py`;
- aucun appel depuis `account_identity_guard.py`;
- aucun changement sender, outreach, follow ou account session;
- aucun run device;
- aucun webhook;
- aucun appel HTTP reel dans les tests;
- aucun appel RPC direct Python vers `update_client_instagram_account_status`.

Flags config :

```text
INSTAGRAM_ACCOUNT_STATUS_PUBLISH_ENABLED=false
INSTAGRAM_ACCOUNT_STATUS_API_URL=""
INSTAGRAM_ACCOUNT_STATUS_INTERNAL_API_TOKEN=""
INSTAGRAM_ACCOUNT_STATUS_FAIL_OPEN=true
INSTAGRAM_ACCOUNT_STATUS_TIMEOUT_SECONDS=10.0
```

Le token interne n'est jamais logge. La publication est opt-in et fail-open par
defaut pour ne pas casser les flows runtime existants si l'API de statut est
indisponible.

Interface helper :

```python
publish_instagram_account_status(
    account_id: str,
    login_status: str | None = None,
    provisioning_status: str | None = None,
    onboarding_status: str | None = None,
    reauth_required: bool | None = None,
    reauth_reason: str | None = None,
    reason: str | None = None,
    external_request_id: str | None = None,
    metadata: dict | None = None,
) -> dict
```

Comportement :

- flag off -> `{"published": false, "reason": "disabled"}`;
- URL ou token absent -> `not_configured`;
- validation UUID-like de `account_id`;
- au moins un champ de statut ou `reauth_required`;
- `reason` bornee a 500 caracteres;
- `external_request_id` safe et borne a 120 caracteres;
- `metadata` doit etre un objet;
- metadata sensible rejetee avant tout appel HTTP;
- ajout de `metadata.source="python_status_publisher"` si le caller ne fournit
  pas de source;
- HTTP POST via `urllib.request` vers `instagram-account-status`;
- retour safe `{"published": true, "status_code": 200, "response": ...}` en
  succes.

Metadata interdite :

```text
password, secret, secret_ref, raw_secret, token, cookie, webhook, webhook_url,
vault, service_role, authorization, bearer, adb_serial, device_udid, xml,
screenshot, session_cookie
```

Erreurs fail-open :

- `forbidden_metadata`;
- `reason_too_long`;
- `external_request_id_invalid`;
- `metadata_must_be_object`;
- `http_error`;
- `timeout`;
- `network_error`.

Si `INSTAGRAM_ACCOUNT_STATUS_FAIL_OPEN=false`, les erreurs de publication
remontent sous forme d'exception controlee
`InstagramAccountStatusPublishError`. Cette politique est reservee a des
contextes futurs de provisioner dedie; elle ne doit pas etre activee sur les
flows sender/follow/outreach existants sans validation specifique.

Mappings runtime futurs :

- `login_connected` -> `login_status='connected'`,
  `provisioning_status='ready'`, `onboarding_status='ready'`,
  `reauth_required=false`;
- `two_factor_required` -> `login_status='needs_2fa'`,
  `provisioning_status='login_verification_pending'`,
  `onboarding_status='verification_pending'`;
- `checkpoint_required` -> `login_status='checkpoint'`,
  `provisioning_status='login_verification_pending'`,
  `onboarding_status='verification_pending'`;
- `login_failed` -> `login_status='failed'`,
  `provisioning_status='failed'`, `onboarding_status='support_required'`,
  `reauth_required=true`;
- `account_identity_mismatch` -> `login_status='mismatch'`,
  `provisioning_status='blocked'`, `onboarding_status='support_required'`;
- `session_expired` -> `login_status='logged_out'`,
  `provisioning_status='login_pending'`.

Relation ORF/incidents :

Le helper ne remplace pas `runtime_incidents.py`. Les incidents ORF restent la
verite ops durable; les statuts `client_instagram_accounts` restent la verite
dashboard/status. Un futur branchement mismatch pourra publier les deux signaux
derriere feature flag, mais Entry 2E-4A ne le fait pas.

Prochaine etape :

- Entry 2E-4C : brancher un point cible derriere flag, probablement
  `account_identity_guard` pour `login_status='mismatch'`, ou un futur vrai
  provisioner login si disponible.

## Entry 2E-4C-1 Runtime ciblé account mismatch status

Entry 2E-4C-1 branche le premier point runtime cible sur le helper Python de
publication de statut : `account_identity_guard.py` publie maintenant un statut
dashboard quand un mismatch actif Instagram est confirme.

Point exact :

- `verify_active_instagram_account_matches_expected`;
- uniquement lorsque `result.failure_reason == active_instagram_account_mismatch`;
- juste apres la publication observationnelle ORF
  `runtime_incidents.publish_account_incident`;
- sans changement dans `runner.py`, sender, orchestrators, account sessions,
  Edge Functions, migrations ou dashboard UI.

Payload status publie :

```text
login_status=mismatch
provisioning_status=blocked
onboarding_status=support_required
reason=account_identity_mismatch
external_request_id=identity_guard:<run_id|unknown>:<account_id|unknown>:mismatch
```

Metadata minimale :

```json
{
  "source": "account_identity_guard",
  "run_id": "<run id>",
  "run_type": "<run type>",
  "stage": "<guard stage>",
  "expected_account_username": "<expected handle>",
  "actual_username": "<actual handle>",
  "guard_reason": "active_instagram_account_mismatch",
  "verification_method": "<method>",
  "identity_evidence": "username_mismatch_stable_id_unavailable"
}
```

La publication de statut est complementaire aux incidents ORF. Les incidents
`account_incidents` restent la verite ops durable; le status
`client_instagram_accounts` sert le dashboard et la synchronisation d'actions
admin.

Comportement fail-open :

- flag `INSTAGRAM_ACCOUNT_STATUS_PUBLISH_ENABLED=false` -> aucun HTTP, retour
  helper `disabled`, guard inchange;
- URL/token absents -> retour helper `not_configured`, guard inchange;
- erreur HTTP/timeout/reseau -> retour helper `published=false` si fail-open,
  guard inchange;
- meme si `INSTAGRAM_ACCOUNT_STATUS_FAIL_OPEN=false`, le hook du guard capture
  l'exception et journalise un resume safe.

Le guard ne publie pas de status mismatch pour :

- `actual_logged_in_username_not_detected`;
- `own_profile_open_failed`;
- `expected_account_username_missing`;
- match OK;
- toute autre `failure_reason`.

Securite metadata :

- aucun password, `secret_ref`, payload/ref Vault, token, cookie, Authorization
  ou body HTTP complet;
- aucun XML brut, chemin screenshot, `adb_serial`, `device_udid` ou session
  cookie;
- aucun run device, webhook reel ou appel HTTP reel dans les tests.

## Entry 2E-5A Login status classifier + provisioner skeleton

Entry 2E-5A ajoute une couche Python isolee pour preparer le futur
provisioner/login sans brancher le runtime principal. Elle ne fait aucun vrai
login Instagram et ne pilote aucun device.

Nouveaux modules :

- `instagram_login_status_classifier.py` : classifier pur, sans HTTP, Vault,
  device ou dependance runtime. Il transforme un outcome abstrait de probe login
  en status pret pour `publish_instagram_account_status(...)`;
- `instagram_login_provisioner.py` : skeleton optionnel qui orchestre
  classifier -> publisher injectable, uniquement derriere le flag
  `INSTAGRAM_LOGIN_PROVISIONER_ENABLED`.

Scope volontaire :

- aucun read Vault et aucun password reel manipule;
- aucun `connect_device`, `app_start`, tap UI, dump XML ou screenshot;
- aucun hook dans `runner.py`, sender/orchestrators, `account_session*`,
  `instagram_navigation.py` ou `follow_action_engine.py`;
- aucun changement Edge Functions, migrations, dashboard UI, `config.py` ou
  `supabase_client.py`;
- aucun HTTP reel dans les tests : le publisher est mocke/injecte.

Flag :

```text
INSTAGRAM_LOGIN_PROVISIONER_ENABLED=false
```

Le flag est lu directement depuis l'environnement dans
`instagram_login_provisioner.py`, afin de ne pas elargir `config.py` pour ce
skeleton. Flag off -> aucun publish et retour `published=false,
reason=disabled`.

Mapping classifier :

```text
connected -> login_status=connected, provisioning_status=ready,
  onboarding_status=ready, reauth_required=false, reason=login_connected

needs_2fa -> login_status=needs_2fa,
  provisioning_status=login_verification_pending,
  onboarding_status=verification_pending, reason=two_factor_required

checkpoint -> login_status=checkpoint,
  provisioning_status=login_verification_pending,
  onboarding_status=verification_pending, reason=checkpoint_required

login_failed -> login_status=failed, provisioning_status=failed,
  onboarding_status=support_required, reauth_required=true,
  reauth_reason=credentials_invalid, reason=login_failed

logged_out -> login_status=logged_out, provisioning_status=login_pending,
  onboarding_status=credentials_submitted, reason=session_expired

skipped_not_implemented -> should_publish=false, reason=probe_not_implemented
unknown -> should_publish=false, reason=unknown_login_probe_outcome
```

Metadata safe par defaut :

```json
{
  "source": "provisioner",
  "stage": "login_probe",
  "probe_version": "v1"
}
```

Le classifier nettoie les cles interdites, y compris dans les objets imbriques :

```text
password, secret, secret_ref, raw_secret, token, cookie, vault, webhook,
webhook_url, service_role, authorization, bearer, xml, screenshot, adb_serial,
device_udid, session_cookie
```

Comportement du skeleton :

- `probe_device_login(...)` retourne `skipped_not_implemented` en 2E-5A;
- `publish_classified_login_status(...)` ne publie que si le flag est actif et
  si la classification est publishable;
- le publisher est injectable pour tests;
- exception publisher -> fail-open par defaut avec `reason=publisher_exception`;
- mismatch n'est pas traite ici : il reste couvert par `account_identity_guard`.

Suite prevue :

- Entry 2E-5B : probe UI minimale et/ou CLI isole, toujours derriere flag;
- Device Runtime Control Layer / ATX plus tard, apres validation du contrat de
  classification et sans brancher sender/follow/outreach.

## Entry 2E-5B Login UI probe minimale

Entry 2E-5B ajoute une premiere probe UI d'observation pour le futur
provisioner/login Instagram complet. Elle reste volontairement isolee et
read-only cote device : elle lit uniquement une hierarchie UI deja disponible
via un objet `uiautomator2`-like injecte ou mocke.

Nouveau module :

- `instagram_login_ui_probe.py` : detection texte/XML minimale qui retourne un
  `LoginProbeOutcome` compatible avec le classifier 2E-5A.

Integration minimale :

- `instagram_login_provisioner.run_login_ui_probe_check(...)` verifie d'abord
  `INSTAGRAM_LOGIN_PROVISIONER_ENABLED`;
- flag off -> aucun `dump_hierarchy`, aucun publish, retour `disabled`;
- flag on -> `probe_instagram_login_ui(...)` -> classifier 2E-5A -> publisher
  injectable;
- exception probe/publisher -> fail-open avec reason stable
  `probe_exception` ou `publisher_exception`.

Ce que 2E-5B ne fait pas :

- aucune saisie username/password;
- aucun read Vault ou acces credentials reels;
- aucun `app_start`, `app_stop`, tap/click ou navigation;
- aucun hook `runner.py`, sender/follow/outreach ou `account_session*`;
- aucun changement Edge Functions, migrations, dashboard UI, `config.py` ou
  `supabase_client.py`;
- aucun XML brut, chemin screenshot, `adb_serial`, `device_udid`, password,
  `secret_ref`, token ou cookie dans les metadata publiees.

Detections V1 :

```text
login screen / logged_out:
  Log in to Instagram, Username, Password, Forgot password, Continue as

needs_2fa:
  two-factor, 2FA, authentication code, security code, Enter code,
  confirmation code

checkpoint:
  checkpoint, challenge, Help us confirm, Confirm it's you,
  Suspicious login attempt, Verify your account

login_failed:
  incorrect password, wrong password, couldn't log in, invalid username,
  Sorry, your password was incorrect

connected:
  signaux connectes multiples comme Home, Search, Reels, Profile, Activity,
  Feed, Direct, New post
```

Principe de prudence :

- les signaux bloquants (`login_failed`, `needs_2fa`, `checkpoint`) gagnent sur
  les signaux generiques;
- `logged_out` requiert plusieurs indices login;
- `connected` requiert plusieurs indices connectes et aucune evidence login;
- en cas d'ambiguite, la probe retourne `unknown` et ne publie pas de status.

Limites connues :

- detection initiale texte/XML uniquement;
- pas encore de vision layer, navigation state machine ou recovery engine;
- pas encore de controle runtime device centralise;
- pas encore de design d'acces credentials securise cote worker.

Suite prevue :

- Entry 2E-5C : CLI isole optionnel avec device reel de test, toujours derriere
  flag et sans runner hook;
- ou audit/design dedie pour l'acces credentials securise;
- Device Runtime Control Layer / ATX plus tard. Le flow complet
  login/provisioning reste obligatoire roadmap, mais doit continuer a avancer
  par couches testables et observables.

## Entry 2E-5C CLI probe UI reelle isolee

Entry 2E-5C ajoute `instagram_login_probe_cli.py`, un CLI isole pour lancer la
probe UI login sur un device reel ou mocke et mesurer les timings. Il reste
probe-only : il ne fait aucun login et ne modifie pas l'etat Instagram.

Commande probe-only :

```bash
python3 instagram_login_probe_cli.py --device-serial emulator-5554 --json --no-publish
```

Commande avec contexte compte, toujours sans publish par defaut :

```bash
python3 instagram_login_probe_cli.py \
  --device-serial emulator-5554 \
  --account-id <uuid> \
  --expected-username <username> \
  --json
```

Publication optionnelle :

```bash
INSTAGRAM_LOGIN_PROVISIONER_ENABLED=true \
python3 instagram_login_probe_cli.py \
  --device-serial emulator-5554 \
  --account-id <uuid> \
  --publish \
  --json
```

Comportement :

- connexion `uiautomator2.connect(...)` uniquement;
- un seul `dump_hierarchy`;
- classification via `instagram_login_ui_probe.probe_login_ui_from_hierarchy`;
- mapping status via le classifier 2E-5A;
- aucun publish par defaut, meme si `should_publish=true`;
- `--publish` exige `--account-id` et respecte
  `INSTAGRAM_LOGIN_PROVISIONER_ENABLED`;
- flag off -> `published=false`, `publish_reason=disabled`, sans erreur fatale;
- sortie JSON/humaine safe, sans XML brut, screenshot, password, token,
  `secret_ref`, Vault, service role ou metadata device publiee.

Timings exposes :

```text
connect_ms
dump_hierarchy_ms
classify_ms
total_ms
```

Warnings V1 :

- `dump_hierarchy_ms > 2000` -> `slow_dump_hierarchy`;
- `total_ms > 3000` -> `slow_total_probe`.

Regle de vitesse :

- pas de sleep arbitraire;
- pas de retry par defaut;
- pas de dump redondant;
- pas de `app_start`, `app_stop`, tap/click ou saisie;
- les futurs tests reels devront conserver les fast paths, mesurer la cadence
  et eviter toute action inutile sur compte/device.

Contraintes device actuelles :

- une probe simple peut se faire sur le phone/emulateur unique si Instagram est
  deja dans un etat observable;
- l'absence de clone limite la representativite production;
- un vrai test login/provisioning prod necessitera un clone et un compte test
  dedie avant toute saisie credentials ou recovery.

Suite prevue :

- Entry 2E-5D : design d'acces credentials securise cote worker;
- ou CLI login controle compte test, seulement apres decision explicite;
- integration Device Runtime Control Layer / ATX plus tard, avec etat, recovery
  et observabilite avant tout branchement runtime principal.

## Entry 2E-5D Probe visible avec app_start controle

Entry 2E-5D etend `instagram_login_probe_cli.py` avec une option explicite
`--app-start`. Elle sert a positionner visiblement l'emulateur sur Instagram
avant la probe, sans effectuer de login ni d'action interactive. Cette etape
repond au smoke 2E-5C qui validait la chaine device mais retournait `unknown`,
probablement parce que l'ecran courant n'etait pas positionne sur Instagram.

Commande smoke controlee :

```bash
python3 instagram_login_probe_cli.py \
  --device-serial emulator-5554 \
  --app-start \
  --package-name com.instagram.android \
  --post-start-wait-ms 500 \
  --json \
  --no-publish
```

Options :

- `--app-start` : appelle `d.app_start(package_name)` apres la connexion device;
- `--package-name` : package a ouvrir, par defaut `com.instagram.android`, et
  configurable pour de futurs clones;
- `--post-start-wait-ms` : attente courte apres start, clamp 0..1500 ms;
- `--no-app-stop` : documente le comportement V1; aucun `app_stop` automatique
  n'est execute.

Garanties 2E-5D :

- aucun tap/click;
- aucune saisie username/password;
- aucun read Vault ou acces credentials;
- aucun publish par defaut;
- aucun hook `runner.py`, sender/follow/outreach ou `account_session*`;
- un seul `dump_hierarchy` apres `app_start`;
- pas de retry par defaut;
- pas de sleep hors `post_start_wait_ms` clampé;
- pas d'XML brut, screenshot, password, token, `secret_ref`, Vault ou
  `service_role` dans la sortie.

Timings exposes :

```text
app_start_ms
post_start_wait_ms
connect_ms
dump_hierarchy_ms
classify_ms
total_ms
```

Warnings V1 :

- `app_start_ms > 2000` -> `slow_app_start`;
- `dump_hierarchy_ms > 2000` -> `slow_dump_hierarchy`;
- `total_ms > 4000` avec `--app-start` -> `slow_total_probe`.

Limites :

- le test reste single phone / no clone;
- il valide une probe visible controlee, pas une situation prod multi-clone;
- un vrai login/provisioning necessitera clone + compte test dedie, puis design
  d'acces credentials securise cote worker avant toute saisie.

Suite prevue :

- Entry 2E-5E/2E-5D suivant selon decision : design d'acces credentials securise;
- puis CLI login controle compte test;
- puis integration Device Runtime Control Layer / ATX avec state machine,
  recovery et observabilite avant branchement runtime principal.

## Entry 2E-5E Login Screen Router

Entry 2E-5E ajoute une couche de decision pure avant tout vrai login. Le
router observe les signaux d'ecran login et decide la prochaine intention, mais
n'execute aucun tap/click et ne saisit aucun mot de passe.

Nouveaux composants :

- `instagram_login_screen_router.py` : decision layer pure pour les ecrans
  `Continue as`, `Use another profile`, login form vide et unknown;
- `instagram_login_ui_probe.extract_login_screen_signals_from_hierarchy(...)` :
  extraction pure de signaux safe depuis le XML deja disponible.

Screen types V1 :

```text
continue_as_candidate
login_form_empty
unknown
```

Decisions V1 :

- `continue_as_candidate` + suggested == expected ->
  `continue_expected_account`, `should_tap_continue=true`,
  `next_action=continue_then_secure_password_step_later`;
- `continue_as_candidate` + suggested != expected + lifecycle
  `canceled|archived|stopped` + `clone_reuse_allowed=true` ->
  `use_another_profile_previous_account_stopped`,
  `should_tap_use_another_profile=true`,
  `audit_reason=previous_account_stopped_override`,
  sans escalation;
- `continue_as_candidate` + suggested != expected + lifecycle actif, paused,
  onboarding, unknown, lookup error ou clone non reusable ->
  `block_wrong_suggested_account`, `login_status=mismatch`,
  `provisioning_status=blocked`, `onboarding_status=support_required`,
  `dashboard_action_type=review_account_mismatch`;
- `login_form_empty` -> `start_login_form_flow`,
  `next_action=secure_credentials_required_later`;
- `unknown` -> `unknown_no_action`.

Lifecycle V1 reconnu :

```text
active
paused
canceled
onboarding
archived  # alias stopped/canceled
stopped   # alias canceled
unknown
```

Cas test actuel :

- `suggested_username=i_m_your_traker`;
- lookup lifecycle -> `canceled`;
- `clone_reuse_allowed=true`;
- decision attendue :
  `use_another_profile_previous_account_stopped`;
- audit attendu : `previous_account_stopped_override`;
- pas d'escalade warning inutile.

Pourquoi cette couche existe :

- `Continue as <username>` ne doit jamais etre clique aveuglement;
- un ancien compte `canceled/stopped/archived` peut etre ignore proprement si le
  clone est reusable;
- un compte actif/paused/onboarding/inconnu reste un risque mismatch et doit
  produire une revue admin;
- `account_identity_guard.py` garde son role de safe-stop pour compte deja
  actif; le router prepare seulement la decision avant login.

Securite :

- aucun tap/click en 2E-5E;
- aucun password;
- aucun Vault;
- aucun publish HTTP reel;
- aucun Supabase reel dans le module;
- `account_lifecycle_lookup(username)` est injectable/mocke;
- metadata safe uniquement : source, screen type, decision, reason,
  usernames normalises, lifecycle, clone reuse, audit reason;
- aucun XML brut, screenshot, token, `secret_ref`, `adb_serial`, `device_udid`,
  cookie ou `service_role`.

Suite prevue :

- design d'acces credentials securise;
- action executor controle pour `Continue` / `Use another profile`;
- login controle sur clone + compte test dedie;
- puis seulement integration state machine / recovery avant runtime principal.

## Dashboard Foundation 1A — Admin Account Overview / Radar Base

Dashboard Foundation 1A ajoute une fondation backend **read-only** pour les
futures surfaces admin Manage, Radar / Server Check, projection client-safe et
compatibilite BotApp/Mac app. Cette entree ne modifie pas l'UI, ne cree aucune
mutation dashboard et ne branche aucun runtime device.

RPC ajoutees :

- `public.get_admin_account_overview(p_limit, p_offset, p_search, p_status)` ;
- `public.get_admin_radar_overview(p_limit, p_offset, p_search, p_status,
  p_health)`.

Contrat V1 :

- `SECURITY DEFINER` ;
- `grant execute` uniquement a `service_role` ;
- aucun grant `anon` ;
- aucun grant `authenticated` ;
- pas d'Edge Function dashboard admin en 1A ;
- pas de JWT admin/client en 1A.

Projection Manage admin :

- identifiants compte/client, nom client, username, email masque ;
- statuts dashboard derives (`admin_status`, `customer_status`,
  `subscription_status`) ;
- package / entitlements ;
- credentials status safe (`credentials_configured`, `credentials_status`,
  `reauth_required`, `password_display`) ;
- statuts login/provisioning/onboarding ;
- compteur actions dashboard pending, blocage campagne, derniere severite
  incident ;
- champs futurs stables (`tags`, `invoice_status`, `last_7d_growth`) meme
  lorsqu'ils retournent `[]`, `null` ou `unknown` en V1.

Projection Radar / Server Check admin :

- `health_status` dans `ok`, `monitor`, `problem`, `unknown` ;
- `health_reason` explicite ;
- actions 2j / 7j / 30j depuis logs safe lorsque disponibles ;
- FBR agrégé depuis `ig_interacted_users` quand un denominateur fiable existe ;
- champs futurs stables pour growth, posts, live-feed, special care et quick
  rules, sans inventer de metriques absentes.

No-leak contract :

- aucune sortie password, hash/longueur password, `secret_ref`, Vault id,
  token, Authorization header, `service_role`, webhook Slack/Discord, XML brut,
  screenshot path, raw logs, metadata incident brute sensible, `adb_serial`,
  `device_udid`, USB/hub port ;
- l'email est masque (`x***@domain`) ;
- `password_display` est limite a `configured`, `missing` ou `unknown`.

Compatibilite future :

- Propulse sert de reference UX Manage/Radar, sans copie UI dans ce patch ;
- BotApp sert de reference conceptuelle pour profiles/devices/settings/targets/
  stats/interactions/audit/scopes, sans creation d'API `/v1` ;
- la projection client-safe sera derivee plus tard (1B/1C) depuis ces contrats
  safe, avec ownership/JWT dedies.

Hors scope 1A :

- mutation status dropdown ;
- phone controls, restart/stop-all reels ;
- special care write, notes, phone sorting, activity log table ;
- Source Quality Control / CT auto-disable ;
- IA log analysis, customer import/webhooks ;
- publish backend connected, runner hook, smoke Instagram, Slack/Discord.

## Dashboard Settings Registry — DF-1B-prep

`docs/dashboard-settings-registry.md` documente le mapping officiel entre l'UI
admin Codex `/instagram-dashboard` et le backend Phone Farm cible. Ce registre
est documentaire uniquement : aucun patch runtime, aucune migration, aucune Edge
Function, aucun run device, aucun publish backend et aucun patch UI.

Le registre acte que l'UI existante est un prototype admin legacy
GramBot/Appium a conserver puis migrer progressivement. Elle lit/ecrit surtout
`ig_account_settings`, `ig_account_filters`, `ig_accounts`, `ig_runs`,
`ig_action_logs`, `ig_account_templates`, `ig_devices` et `ig_targets`, alors
que Phone Farm runtime s'appuie deja ou cible plutot `client_instagram_accounts`,
`account_credentials`, `ig_account_dm_settings`, `ig_dm_templates`,
`ig_account_unfollow_settings`, `ig_account_follow_settings`, `ig_targets`,
`ig_interacted_users`, `account_dashboard_actions`, `account_incidents`,
`runtime_events`, `phone_devices`, `phone_clones` et `account_assignments`.

Regles principales :

- ne pas brancher le dashboard client sur cette UI ni sur les tables legacy ;
- remplacer les reads admin par une future API/Edge admin-dashboard au-dessus
  des RPC 1A ;
- faire passer les writes par des APIs de domaine avec validation role,
  entitlement, support runtime et audit log ;
- deprecier `ig_account_settings.password` et utiliser
  `account_credentials` + `instagram-credentials` + Vault ;
- garder `device_udid`, device internals, raw logs, raw metadata, XML,
  screenshot paths, `secret_ref`, Vault id, tokens, webhooks et `service_role`
  hors de toute surface client ;
- separer `account_status` en `admin_status`, `login_status`,
  `provisioning_status`, `subscription_status` et `automation_health`.

Prochaine etape recommandee apres ce document : DF-1B admin-dashboard Edge/API
over RPC 1A, read-only, sans mutation settings, sans device controls, sans
Source Quality Control, sans client dashboard et sans branchement complet du
drawer Settings.

Checkpoint P1b Sources rotation settings 2026-06-02 :

- worker `93717ecc3b7828a01a00ca1ee8c81d077a821109` pousse sur
  `stable-follow-working-state`;
- frontend `b9f5fb4c23b745b084bedc831e69e892598e4f60` pousse sur `main`;
- `account_session` Follow charge plusieurs targets eligible, conserve
  l'attribution `target_id` avant/apres switch et change de target uniquement
  sur exhaustion ou budget par target atteint;
- pas de switch silencieux sur login, checkpoint, credentials, device issue,
  rate limit ou crash;
- settings Sources admin via `account_follow_source_settings` et
  `GET/PATCH /api/instagram-dashboard/settings/follow-sources`;
- defaults conservateurs `2 / 3`, bounds `1..50` et `1..10`, budget par
  target par run/session et jamais multiplicateur du cap global Follow;
- migrations remote appliquees et alignees localement :
  `20260601224833_follow_source_rotation_settings.sql` et
  `20260601224935_follow_source_rotation_settings_revoke_public_grants.sql`;
- smoke `cinema_catchup` : GET final `2 / 3`, PATCH `3 / 3` puis restore
  `2 / 3`, invalides `0`, `51`, `11` refuses sans clamp, audit
  `follow_source_rotation_settings_saved` safe;
- UI visuel Sources pending a cause redirect `restaurant-login`; aucun run reel,
  aucun follow reel, aucun 30/4 applique, aucun credential/password touche,
  aucun tag.

## Dashboard Foundation 1B — Admin Dashboard Edge/API over RPC 1A

Dashboard Foundation 1B ajoute l'Edge Function read-only
`supabase/functions/admin-dashboard/index.ts`. Elle expose une premiere API
interne/admin au-dessus des RPC 1A, sans lire directement les tables dashboard
et sans mutation.

Actions V1 :

- `health` : retourne `{ ok: true, service: "admin-dashboard", version:
  "df-1b" }` ;
- `manage_overview` : appelle `public.get_admin_account_overview(...)` ;
- `radar_overview` : appelle `public.get_admin_radar_overview(...)`.

Auth V1 :

- POST uniquement pour les actions ;
- header `Authorization: Bearer <ADMIN_DASHBOARD_INTERNAL_API_TOKEN>` ;
- secret Edge attendu : `ADMIN_DASHBOARD_INTERNAL_API_TOKEN` ;
- aucun accès `anon` ;
- aucun accès `authenticated` direct ;
- aucun JWT client/admin dans ce patch.

Contrat request :

- `action` obligatoire ;
- `limit` optionnel, clampe entre `1` et `200` ;
- `offset` optionnel, normalise a `0` minimum ;
- `search`, `status` optionnels ;
- `health` optionnel uniquement pour `radar_overview`.

Contrat response :

- success : `{ ok: true, action, count, items }` pour Manage/Radar ;
- erreur : `{ ok: false, error: { code, message } }` avec code safe dans
  `unauthorized`, `validation_error`, `unsupported_action`, `rpc_failed`,
  `internal_error`.

Note UI future : DF-1B ne modifie pas le repo Next.js et ne suppose pas que
`/instagram-dashboard` restera une seule grande page. L'API doit pouvoir
alimenter progressivement des vues admin separees : Admin Manage, Admin Radar /
Server Check, Account Detail, Settings / Growth Settings, Devices / Phones,
Activity Log, Target Accounts / CT, DM Templates, Credentials / Dashboard
Actions.

No-leak :

- l'API ne retourne pas password reel, hash/longueur password, `secret_ref`,
  Vault id, token, Authorization header, service-role, webhook Slack/Discord,
  XML brut, screenshot path, raw logs, raw metadata, `adb_serial`, `usb_port`,
  `hub_port` ou `device_udid` ;
- `password_display` reste autorise seulement comme valeur safe
  `configured`, `missing` ou `unknown` issue des RPC ;
- les logs Edge restent limites a `request_id`, `action`, `limit`, `offset`,
  `count`, status code et error code safe.

Hors scope 1B :

- deploy remote ;
- configuration du secret remote ;
- smoke HTTP reel ;
- UI Codex / repo `boost-ai-frontend` ;
- settings mutations ;
- status dropdown write ;
- device controls ;
- Source Quality Control / CT auto-disable ;
- publish backend connected, device run, smoke Instagram, Slack/Discord.

Remote deploy/smoke 2026-05-28 :

- `admin-dashboard` deployed on project `zgafnshkjywfltxgbtzg`;
- `ADMIN_DASHBOARD_INTERNAL_API_TOKEN` configured remotely without storing or
  displaying the value;
- smoke HTTP passed for missing auth, bad token, `health`, `manage_overview`,
  `radar_overview`, unsupported action and clamp validation;
- no-leak confirmed on remote responses; no UI, migration, settings mutation,
  device control or backend publish was performed.

## Entry 2E-5F Controlled Action Executor

Entry 2E-5F ajoute `instagram_login_action_executor.py`, un executor controle
qui applique uniquement une decision deja produite par le Login Screen Router
2E-5E. Il ne decide pas le metier, ne relit pas le lifecycle et ne fait aucun
override : il transforme seulement une decision sure en un tap UI minimal.

Actions autorisees V1 :

- `continue_expected_account` -> tap exact sur `Continue`;
- `use_another_profile_previous_account_stopped` -> tap exact sur
  `Use another profile`.

Toutes les autres decisions restent no-action :

- `block_wrong_suggested_account`;
- `start_login_form_flow`;
- `unknown_no_action`;
- toute decision inconnue, avec `failure_reason=unsupported_decision`.

Securite :

- aucun password;
- aucune saisie username/password;
- aucun read Vault ou acces credentials;
- aucun vrai login complet;
- aucun Supabase reel;
- aucun publish HTTP;
- aucun hook `runner.py`, sender/follow/outreach ou `account_session*`;
- aucun `app_stop` automatique;
- aucun XML brut, screenshot path, token, `secret_ref`, Vault, `adb_serial` ou
  `device_udid` dans le resultat.

Tap safety :

- recherche accessibility exacte sur le bouton cible;
- `Continue` ne matche pas `Create new account`;
- bouton absent -> `failure_reason=target_button_not_found`;
- plusieurs candidats -> `failure_reason=ambiguous_target_button`;
- resolution hierarchy XML prioritaire pour dedupliquer texte/parent/enfant et
  taper au centre des bounds valides; fallback selector `text` seulement;
- exception tap -> `failure_reason=tap_failed`;
- un seul tap maximum;
- pas de retry par defaut;
- `post_action_wait_ms` est borne entre `0` et `1500`.

Observation post-action :

- apres un tap reussi, l'executor attend court puis fait au plus un
  `dump_hierarchy`;
- il appelle `extract_login_screen_signals_from_hierarchy(...)`;
- apres `Use another profile`, l'ecran ideal est `login_form_empty`;
- apres `Continue`, l'ecran suivant peut etre un formulaire password, une
  validation, un etat connecte ou `unknown`; l'executor observe seulement et ne
  saisit rien.

Pourquoi :

- 2E-5E validait la decision sans action;
- 2E-5F ajoute le passage decision -> action UI minimale, avec reasons stables,
  anti-boucle, fast path et observation post-action;
- cette etape reste volontairement avant tout acces credentials.

Limites :

- pas encore de credentials cote worker;
- pas encore de login complet;
- pas encore de stable Instagram ID pour distinguer rename et mismatch;
- pas encore de clone prod/test complet;
- pas encore de state machine login dediee ni de recovery riche.

Prochaines etapes :

- secure credential access design;
- puis password form executor controle, uniquement sur clone + compte test
  dedie, avec etat/recovery avant tout branchement runtime principal.

## Entry 2E-5G Secure Credential Runtime Access Design

Entry 2E-5G ajoute `instagram_credentials_runtime_access.py`, une abstraction
testable pour le futur acces runtime aux credentials Instagram. Cette etape ne
lit pas encore Supabase Vault, ne tape aucun mot de passe et ne branche aucun
runner : elle definit seulement le contrat Python, la redaction et les erreurs
controlees.

Contrat V1 :

- `credentials_lookup(account_id, provider)` retourne uniquement la metadata
  active depuis `account_credentials` ou un mock equivalent;
- `secret_reader(secret_ref)` est injectable et mocke en tests;
- `get_instagram_credentials_for_login(...)` valide le payload avant toute
  lecture de secret;
- `provider='instagram'` est le seul provider accepte;
- `status='active'`, username present et `secret_ref` present sont obligatoires;
- `secret_reader` doit retourner une string non vide.

`SecretValue` :

- garde le secret en memoire uniquement;
- `str(secret)` et `repr(secret)` retournent toujours `[REDACTED]`;
- le secret brut n'est accessible que via
  `reveal_for_login_executor()`, appel explicite reserve au futur executor;
- aucun helper safe ne serialize le password;
- `credential_result_safe_dict(...)` exclut password, `secret_ref`, Vault UUID,
  token, service-role, cookie, XML/screenshot et identifiants device.

Erreurs stables V1 :

- `invalid_account_id`;
- `unsupported_provider`;
- `credentials_lookup_missing`;
- `credentials_not_found`;
- `credentials_payload_forbidden`;
- `credentials_provider_mismatch`;
- `credentials_username_missing`;
- `credentials_not_active`;
- `credentials_secret_ref_missing`;
- `secret_reader_missing`;
- `secret_reader_failed`;
- `secret_value_empty`.

NO-GO securite :

- le provisioner ne doit jamais recevoir un password via ChatGPT, prompt Cursor,
  fichier local durable, logs, JSON visible, `.env`, screenshot, XML, dashboard,
  incident ou reponse API;
- aucun password dans `account_incidents`, `runtime_events`, Slack/Discord,
  dashboard actions ou logs worker;
- aucun `secret_ref` complet, Vault UUID, token, service-role key, cookie,
  `adb_serial` ou `device_udid` dans les outputs safe;
- aucun vrai read Vault, aucun run device et aucun publish HTTP en 2E-5G.

## Entry 2E-5H Supabase Vault Reader Helper

Entry 2E-5H ajoute `instagram_supabase_vault_reader.py`, le helper de lecture
Vault cote worker pour le futur provisioner login. Cette etape ne fait toujours
aucun login Instagram, aucun tap password, aucun run device, aucun `app_start`,
aucun `app_stop`, aucun hook runner et aucune modification dashboard.

Audit prealable :

- `supabase_client.py` utilise deja `SUPABASE_URL` et
  `SUPABASE_SERVICE_ROLE_KEY` pour PostgREST/RPC via service-role;
- le repo contient un wrapper d'ecriture Vault
  `public.create_instagram_credentials_vault_secret(...)`;
- aucun wrapper SQL/RPC de lecture/decrypt Vault n'est encore versionne;
- le format V1 confirme reste `supabase_vault://{vault_secret_id}`.

Transport choisi en 2E-5H :

- adapter RPC injectable `SupabaseVaultClient`;
- `parse_supabase_vault_secret_ref(...)` accepte uniquement
  `supabase_vault://{uuid}`;
- `read_supabase_vault_secret(...)` retourne un `SecretValue`;
- `build_supabase_vault_secret_reader(...)` produit un `secret_reader`
  compatible avec `get_instagram_credentials_for_login(...)`;
- par defaut, sans `rpc_caller` explicite, le transport est fail-closed avec
  `vault_transport_not_configured`.

Limitation technique actuelle :

- le helper est pret pour une lecture service-role via RPC, mais le schema actuel
  ne contient pas encore de RPC publique/service-role de lecture Vault;
- le smoke reel devra donc attendre 2E-5H-2 avec un RPC de lecture/decrypt valide
  ou un transport worker equivalent confirme;
- aucun secret client reel n'est lu dans 2E-5H.

Erreurs safe V1 :

- `invalid_secret_ref`;
- `unsupported_secret_ref_provider`;
- `vault_secret_id_invalid`;
- `vault_transport_not_configured`;
- `vault_read_failed`;
- `vault_secret_empty`;
- `vault_secret_not_string`;
- `vault_timeout`.

Regles no-leak :

- `safe_ref_label` vaut toujours `supabase_vault://[REDACTED]`;
- aucun Vault UUID complet dans les erreurs safe;
- aucun `secret_ref` complet dans les erreurs safe;
- aucun password dans `str`, `repr`, dict safe, exception ou log;
- aucun header Authorization, service-role key, token, cookie, XML/screenshot ou
  identifiant device dans les sorties safe;
- le secret brut n'est accessible que via
  `SecretValue.reveal_for_login_executor()`, pour le futur executor controle.

## Entry 2E-5H-2 Real Supabase Vault Read RPC

Entry 2E-5H-2 ajoute le transport reel de lecture Supabase Vault cote
service-role pour le futur provisioner login. Cette etape reste strictement
limitee au reader : aucun login Instagram, aucun tap password, aucun run device,
aucun `app_start`, aucun `app_stop`, aucun runner hook et aucune modification
dashboard UI.

Audit Supabase/Vault :

- `vault.create_secret(...)` est disponible cote remote;
- `vault.update_secret(...)` est disponible cote remote;
- `vault.secrets` et `vault.decrypted_secrets` sont disponibles;
- `vault.decrypted_secrets.decrypted_secret` fournit la lecture decrypt cote SQL;
- aucun RPC versionne de lecture `read_instagram_credentials_vault_secret(...)`
  n'existait avant 2E-5H-2;
- `create_instagram_credentials_vault_secret(...)` reste service-role only.

RPC ajoutee :

- nom : `public.read_instagram_credentials_vault_secret(p_secret_ref text)`;
- retour : `jsonb`;
- scope accepte : uniquement `supabase_vault://{uuid}`;
- provider non supporte : `unsupported_secret_ref_provider`;
- UUID invalide : `vault_secret_id_invalid`;
- secret absent : `vault_secret_not_found`;
- secret vide : `vault_secret_empty`;
- erreur interne : `vault_read_failed`;
- succes : enveloppe minimale avec `ok=true`, `secret_value`,
  `secret_provider='supabase_vault'` et
  `safe_ref_label='supabase_vault://[REDACTED]'`.

Securite SQL :

- `SECURITY DEFINER`;
- `search_path = public, vault`;
- lecture limitee a `vault.decrypted_secrets` par `id`;
- `revoke execute` pour `public`, `anon`, `authenticated`;
- `grant execute` uniquement a `service_role`;
- aucune policy client, aucune exposition dashboard, aucun grant anon/auth.

Transport Python :

- `SupabaseVaultClient.read_secret(...)` accepte un UUID Vault ou un
  `supabase_vault://{uuid}` valide;
- le call RPC envoie uniquement `p_secret_ref`;
- le reader parse uniquement `ok=true` et `secret_value`;
- `ok=false`, shape inattendue, exception ou timeout deviennent des erreurs
  safe (`vault_read_failed`, `vault_timeout`, etc.);
- les reponses RPC brutes ne sont jamais loggees ni reprises dans les erreurs
  safe;
- le resultat public reste un `SecretValue` redige par `str(...)`, `repr(...)`
  et les dicts safe.

Smoke remote fake secret valide :

- secret fake uniquement, jamais `cinema_catchup`;
- aucun vrai credential client;
- migration remote appliquee;
- verification par egalite interne, longueur et prefix SHA-256 court;
- `secret_matches_expected=true`;
- `safe_ref_label=supabase_vault://[REDACTED]`;
- refus confirme pour provider non supporte et UUID invalide;
- grants confirmes : `service_role` peut executer, `anon` et `authenticated`
  ne peuvent pas executer;
- cleanup par neutralisation via `vault.update_secret(...)`;
- aucun password, `secret_ref`, Vault UUID, service-role key, Authorization
  header ou reponse RPC brute dans les outputs.

Prochaine etape apres validation 2E-5H-2 :

- Entry 2E-5I : password form executor controle;
- smoke `cinema_catchup` seulement plus tard via le flow securise
  `instagram-credentials` -> Supabase Vault;
- aucun password `cinema_catchup` dans chat, prompt, shell history visible,
  git, logs, screenshots ou XML.

Registre dashboard/backend/BotApp futur :

- le dashboard, le backend et BotApp devront afficher uniquement des statuts
  safe : credentials configured/missing, Vault reader status,
  login/provisioning status et action retry provisioning;
- aucun password, `secret_ref`, Vault UUID, token, cookie, service-role key,
  XML/screenshot ou identifiant device ne doit etre expose;
- un futur bouton/admin action pourra relancer une verification credentials,
  mais pas lire ni afficher le secret;
- 2E-5H documente ce contrat seulement : aucun dashboard n'est construit ici.

## Entry 2E-5I Controlled Password Form Executor

Entry 2E-5I ajoute `instagram_login_password_form_executor.py`, un executor
controle capable de remplir username/password et de tapper `Log in` uniquement
sur un formulaire deja valide comme `login_form_empty`. Cette etape reste
mocks-only : aucun smoke reel, aucun login Instagram, aucun runner hook, aucun
flow business, aucun run device, aucun HTTP publish et aucune ecriture Supabase.

Conditions obligatoires avant saisie :

- `prevalidated_signals.screen_type == login_form_empty`;
- `has_username_field == true`;
- `has_password_field == true`;
- `has_login_button == true`;
- aucun signal `ambiguous_login_form`;
- `expected_username` non vide;
- `password` est une instance `SecretValue`;
- champs username/password et bouton `Log in` resolus comme cibles uniques.

Reasons de refus 2E-5I :

- `login_form_not_validated`;
- `username_field_not_found`;
- `password_field_not_found`;
- `login_button_not_found`;
- `ambiguous_login_form`;
- `expected_username_missing`;
- `password_secret_missing`;
- `password_secret_invalid`;
- `input_failed`;
- `submit_failed`;
- `post_submit_dump_failed`.

Securite password/no-leak :

- le password n'est jamais converti via `str(...)` ou `repr(...)`;
- le password est obtenu uniquement via
  `SecretValue.reveal_for_login_executor()`;
- le password n'est pas stocke dans le resultat, `safe_metadata`, exception,
  log, XML, screenshot ou doc;
- aucun `secret_ref`, Vault UUID, token, cookie, service-role key, Authorization
  header, XML brut, screenshot ou device id n'est expose par le resultat;
- la sortie safe contient seulement action, reason/failure_reason,
  `expected_username`, timings et outcome post-submit.

Interaction UI :

- validation des signaux prealables;
- focus/clear/set_text username;
- focus/clear/set_text password;
- tap `Log in`;
- attente post-submit clampee `0..3000 ms`;
- dump post-submit optionnel;
- classification observationnelle uniquement.

Observation post-submit :

- outcomes observes : `connected`, `needs_2fa`, `checkpoint`,
  `login_failed`, `unknown`;
- aucune publication status dans 2E-5I;
- aucune gestion 2FA/checkpoint ici;
- aucun retry par defaut;
- aucune recovery complexe dans cet executor.

Retry / escalation policy :

- `instagram_login_password_form_executor.py` est un executor bas niveau;
- il ne fait aucun retry par defaut;
- il n'escalade pas directement;
- il ne publie pas de status;
- il n'ecrit pas Supabase;
- il retourne seulement `ok`, `executed`, `failure_reason`,
  `post_submit_outcome` et metadata safe;
- le futur provisioner orchestrator decidera retry, escalation, status publish
  et dashboard actions.

Aucun retry futur pour :

- `login_form_not_validated`;
- `expected_username_missing`;
- `password_secret_missing`;
- `password_secret_invalid`;
- `login_failed` confirme;
- `needs_2fa` confirme;
- `checkpoint` confirme;
- mismatch / wrong account.

Mini retry possible plus tard cote orchestrateur, jamais cote executor, pour
erreurs UI transitoires :

- `username_field_not_found`;
- `password_field_not_found`;
- `login_button_not_found`;
- `ambiguous_login_form`;
- `post_submit_dump_failed`;
- `input_failed`;
- `submit_failed`.

Regles retry futures :

- max 1 retry;
- retry seulement apres revalidation de l'ecran `login_form_empty`;
- aucune boucle infinie;
- si encore failure apres retry : stop safe, status provisioning/login selon
  contexte, dashboard action si necessaire.

Post-submit policy future :

- `connected` -> succes;
- `needs_2fa` -> action dashboard `complete_two_factor`, pas retry;
- `checkpoint` -> action dashboard `resolve_checkpoint`, pas retry;
- `login_failed` -> `update_password` ou `review_login_failure`, pas retry avec
  le meme password;
- `unknown` -> re-observe possible 1 fois, puis `retry_later` ou
  `support_required` selon contexte.

Prochaine etape apres 2E-5I :

- smoke controle `cinema_catchup` seulement apres procedure securisee explicite;
- credentials fournis uniquement via `instagram-credentials` -> Supabase Vault;
- jamais de password dans ChatGPT, prompt Cursor, shell history visible, git,
  logs, screenshots ou XML;
- apres smoke, changer le password.

## Entry 2E-5J Provisioner Orchestrator Skeleton

Entry 2E-5J ajoute `instagram_login_provisioner_orchestrator.py`, un
orchestrateur isole qui assemble les briques login/provisioning validees sans
brancher le runtime principal. Cette etape reste mocks-only : aucun vrai login,
aucun smoke device, aucun compte client reel, aucun runner hook, aucun
sender/follow/outreach hook, aucun `app_start`, aucun `app_stop`.

Role de l'orchestrateur :

- observer l'ecran courant via signaux injectes ou dump UI mockable;
- router avec `route_login_screen(...)`;
- executer `Continue` ou `Use another profile` via
  `execute_login_screen_decision(...)` si la decision l'autorise;
- re-observer apres action et revalider `login_form_empty`;
- charger credentials via `credentials_getter(account_id)` injecte;
- appeler `execute_login_form_credentials(...)` uniquement avec
  `SecretValue`;
- classifier `post_submit_outcome` via le classifier login existant;
- retourner un resultat safe pret a etre publie plus tard;
- publier uniquement si `publish_enabled=true` et `publisher` injecte.

Decisions V1 supportees :

- `continue_expected_account` -> tap Continue, re-observe, login form flow;
- `use_another_profile_previous_account_stopped` -> tap Use another profile,
  re-observe, login form flow;
- `block_wrong_suggested_account` -> stop safe, status `mismatch` /
  `blocked` / `support_required`, action dashboard `review_account_mismatch`;
- `unknown_no_action` -> stop safe, pas d'escalade directe V1;
- `start_login_form_flow` -> credentials + password form executor.

Retry / escalation centralises :

- aucun retry dans les executors bas niveau;
- retry max 1 dans l'orchestrateur;
- retry seulement pour erreurs UI transitoires :
  `username_field_not_found`, `password_field_not_found`,
  `login_button_not_found`, `ambiguous_login_form`, `post_submit_dump_failed`,
  `input_failed`, `submit_failed`, ou `unknown` post-submit re-observable;
- retry uniquement apres nouveau dump et revalidation claire
  `login_form_empty`;
- aucun retry pour credentials missing/invalid, `login_failed`, `needs_2fa`,
  `checkpoint`, mismatch/wrong account ou `block_wrong_suggested_account`;
- pas de boucle infinie;
- apres retry echoue : stop safe, reason stable, dashboard action future selon
  contexte.

Mapping status/dashboard futur :

- `connected` -> `connected` / `ready` / `ready`;
- `needs_2fa` -> `needs_2fa` / `login_verification_pending` /
  `verification_pending`, action dashboard `complete_two_factor`;
- `checkpoint` -> `checkpoint` / `login_verification_pending` /
  `verification_pending`, action dashboard `resolve_checkpoint`;
- `login_failed` -> `failed` / `failed` / `support_required`, action dashboard
  `update_instagram_password`;
- `unknown` -> stop safe, re-observe possible une fois, puis `retry_later` ou
  `support_required` selon orchestrateur futur.

Publish V1 :

- `publish_enabled=false` par defaut;
- `publisher` injectable et mocke en tests;
- aucun HTTP reel en 2E-5J;
- payload publish safe uniquement : account/status/reason/metadata safe;
- jamais password, `secret_ref`, Vault UUID, token, service-role key,
  Authorization header, XML brut, screenshot path, `adb_serial`,
  `device_udid`, cookies/sessionid ou raw RPC response.

Prochaine etape apres 2E-5J :

- 2E-5J-2 smoke orchestrator controle ou 2E-5I-2 smoke `cinema_catchup`, selon
  validation explicite;
- smoke reel seulement sur device idle, avec lock device, compte test dedie et
  credentials fournis via `instagram-credentials` -> Supabase Vault;
- aucun password dans chat, Cursor, logs, git, XML ou screenshots.

## Entry 2E-5J-2A Smoke Prep No-Password

Entry 2E-5J-2A prepare le futur smoke reel `cinema_catchup` sans utiliser de
password. Le but est de verifier le terrain device/runtime et la decision
probe/router/orchestrator, pas de soumettre un login.

Scope autorise :

- `adb devices -l`;
- verifier qu'aucun run business n'est actif sur le meme phone;
- connecter `uiautomator2`;
- `app_start` Instagram sans `app_stop`;
- un dump UI;
- extraction des signaux login;
- routing;
- orchestrator `dry_run=True`;
- aucun credential, aucun Vault read, aucun publish HTTP.

Dry-run orchestrator :

- `run_login_provisioning_flow(..., dry_run=True)` observe et route seulement;
- ne charge jamais `credentials_getter`;
- ne lance jamais `execute_login_form_credentials(...)`;
- ne tappe jamais `Log in`;
- ne lit jamais Vault;
- ne publie jamais;
- retourne uniquement decision safe :
  `screen_type`, `router_decision`, `would_tap_continue`,
  `would_tap_use_another_profile`, `would_request_credentials`,
  `would_submit_password=false`, `would_publish=false`,
  `smoke_ready_for_real_login`, `ready_for_password_smoke`, reason et timings.

Interpretation :

- `login_form_empty` -> `would_request_credentials=true`,
  `would_submit_password=false`, `ready_for_password_smoke=true`;
- `continue_as_candidate` attendu -> `would_tap_continue=true`, mais aucun tap
  automatique en 2E-5J-2A;
- `Continue as i_m_your_traker` ou autre mauvais compte -> dry-run seulement,
  `lifecycle_lookup` necessaire pour decider si `Use another profile` serait
  autorise;
- `unknown` -> pas pret pour password smoke; re-observation ou preparation UI
  requise.

No-leak :

- aucun password;
- aucun `secret_ref`;
- aucun Vault UUID;
- aucun token/service-role/Authorization;
- aucun XML brut;
- aucun screenshot path;
- aucun device id complet dans les outputs safe;
- aucun cookie/session.

Condition avant vrai smoke password :

- device idle confirme;
- lock UI disponible;
- aucun run business actif;
- ecran `login_form_empty` ou flow Continue/Use another profile explicitement
  valide;
- credentials uniquement via flow securise `instagram-credentials` -> Vault;
- validation humaine explicite avant toute saisie.

Resultat smoke prep 2E-5J-2A :

- device detecte : `emulator-5554`;
- device count : 1;
- business run detecte : false;
- smoke allowed : true;
- `app_start` Instagram : OK;
- dump UI unique : OK;
- `screen_type=continue_as_candidate`;
- `suggested_username=i_m_your_traker`;
- `has_continue_button=true`;
- `has_use_another_profile_button=true`;
- `has_create_new_account_button=true`;
- router sans lifecycle/clone reuse : `block_wrong_suggested_account`;
- router avec lifecycle mock `canceled` + `clone_reuse_allowed=true` :
  `use_another_profile_previous_account_stopped`;
- `would_submit_password=false`;
- `would_publish=false`;
- `ready_for_password_smoke=false`;
- `smoke_ready_for_real_login=true` seulement pour le chemin dry-run
  `Use another profile` controle;
- aucune action UI automatique n'a ete executee apres observation.

Prochaine condition requise :

- confirmer explicitement le lifecycle de `i_m_your_traker` avant tout tap
  `Use another profile`;
- ou afficher directement `login_form_empty`;
- puis validation humaine explicite avant toute saisie password.

No-leak smoke prep :

- aucun password affiche;
- aucun `secret_ref` affiche;
- aucun Vault UUID affiche;
- aucun token/service-role/Authorization affiche;
- aucun XML brut affiche;
- aucun screenshot path affiche;
- aucun cookie/session affiche.

## Entry 2E-5J-2B Use Another Profile Gate

Entry 2E-5J-2B vise un smoke controle ou l'unique action UI autorisee serait
`Use another profile`, afin de quitter un ancien compte suggere et d'arriver a
`login_form_empty`. Le smoke reste no-password : aucun credential, aucun Vault
read, aucun tap `Log in`, aucun publish HTTP et aucun runner hook.

Gate obligatoire avant tap :

- device unique `emulator-5554`;
- aucun run business actif;
- ecran observe `continue_as_candidate`;
- `suggested_username` extrait dynamiquement;
- `router_decision=use_another_profile_previous_account_stopped`;
- `clone_reuse_allowed=true`;
- lifecycle du `suggested_username` confirme `canceled`, `stopped` ou
  `archived`.

Resultat 2E-5J-2B initial :

- pre-check device OK;
- ecran observe `continue_as_candidate`;
- `suggested_username=i_m_your_traker`;
- recherche metadata non secrete : aucune entree lifecycle exploitable trouvee
  pour confirmer `canceled/stopped/archived`;
- action stoppee avant tap;
- `reason=lifecycle_not_confirmed`;
- `would_submit_password=false`;
- `would_publish=false`.

Resultat 2E-5J-2B apres checkpoint lifecycle lookup :

- pre-check device OK : device unique `emulator-5554`, aucun runner/business
  Python actif detecte;
- app_start Instagram OK, dump UI OK;
- ecran observe `continue_as_candidate`;
- `suggested_username=i_m_your_traker` extrait dynamiquement;
- override operateur explicite pour ce smoke uniquement :
  `lifecycle_status=canceled`, `clone_reuse_allowed=true`,
  `source=operator_smoke_override`;
- dry-run provisioner OK :
  `router_decision=use_another_profile_previous_account_stopped`,
  `would_tap_use_another_profile=true`, `would_submit_password=false`,
  `would_publish=false`;
- premier smoke reel stoppe avant tap par l'action executor :
  `failure_reason=ambiguous_target_button`;
- cause observee : `d(text="Use another profile")` retournait `count=1`, mais
  `d(description="Use another profile")` retournait `count=2`, alors que le dump
  XML ne contenait qu'un seul libelle texte exact non cliquable;
- le nœud texte exact etait `clickable=false`, avec bounds valides entre
  `Continue` et `Create new account`.

Correctif 2E-5J-2B target resolution :

- resolution primaire via `dump_hierarchy` et parsing XML safe;
- match exact normalise sur `text` / `content-desc`;
- filtre `enabled`, `visible`, bounds valides, hors status bar;
- zone verticale : sous `Continue`, au-dessus de `Create new account`;
- deduplication des candidats proches par bounds;
- tap unique au centre des bounds du candidat retenu;
- fallback selector `text` uniquement si le parsing hierarchy echoue;
- ne jamais utiliser de coordonnees fixes d'ecran hors bounds valides.

Resultat smoke reel 2E-5J-2B apres correctif :

- pre-check device OK : `emulator-5554`, `device_count=1`,
  `business_run_detected=false`;
- `screen_type=continue_as_candidate`;
- `suggested_username=i_m_your_traker` extrait dynamiquement;
- override operateur smoke :
  `lifecycle_status=canceled`, `clone_reuse_allowed=true`,
  `source=operator_smoke_override`;
- `router_decision=use_another_profile_previous_account_stopped`;
- `action_executed=true`, `action=tap_use_another_profile`;
- resolution utilisee : `target_resolution_hierarchy_bounds_center`;
- `post_action_screen_type=login_form_empty`;
- `ready_for_password_smoke=true`;
- `would_submit_password=false`;
- `would_publish=false`;
- aucun password, aucun credential, aucun Vault read, aucun publish HTTP, aucun
  runner hook.

Conclusion : le gate lifecycle + la resolution accessibility permettent
maintenant d'atteindre `login_form_empty` sans hardcode username ni coordonnees
fixes. La prochaine etape reste un smoke password `cinema_catchup` via flow
securise Vault, jamais via chat/prompt/shell history visible.

## Entry 2E-5J-2C Continue Expected Account Smoke

Entry 2E-5J-2C valide le Cas B no-password : Instagram affiche
`Continue as {expected_username}` et le compte suggere correspond exactement au
compte attendu.

Pre-check :

- device unique `emulator-5554`;
- aucun runner/business Python actif detecte;
- aucun credential, aucun Vault read, aucun publish HTTP;
- aucun tap `Log in`.

Observation avant action :

- `expected_username=cinema_catchup`;
- `screen_type=continue_as_candidate`;
- `suggested_username=cinema_catchup`, extrait dynamiquement;
- `router_decision=continue_expected_account`;
- `would_tap_continue=true`;
- `would_tap_use_another_profile=false`;
- `would_submit_password=false`;
- `would_publish=false`.

Action controlee :

- un seul tap `Continue`;
- resolution utilisee : `selector_text`;
- aucun retry en boucle;
- aucun tap `Use another profile`;
- aucun tap `Log in`;
- aucune saisie username/password.

Resultat post-action :

- `action_executed=true`;
- `post_action_screen_type=unknown` cote pre-login screen probe;
- classification login UI post-action : `connected`;
- libelles safe observes : feed/home Instagram, sans afficher XML brut;
- `ready_for_password_smoke=false`, car aucun formulaire password n'est demande;
- `status_candidate=connected`;
- `would_submit_password=false`;
- `would_publish=false`.

Mini patch 2E-5J-2C :

- l'orchestrateur ajoute maintenant `login_probe_outcome` lors des re-observes;
- si apres `Continue` le pre-login screen probe reste `unknown` mais le
  classifier login detecte `connected`, `needs_2fa`, `checkpoint` ou
  `login_failed`, ce classifier prime;
- pour le cas smoke reel connected :
  `final_outcome=connected`, `status_candidate=connected`,
  `password_required=false`, `ready_for_password_smoke=false`;
- aucun credential n'est demande, aucun password executor n'est appele, aucun
  retry password n'est lance, aucun publish n'est active par defaut;
- la logique reste generique et ne depend pas de `cinema_catchup` en dur.

Conclusion : le chemin `continue_expected_account` peut amener directement au
home/feed connecte sans password. Pour la suite, ne pas publier de status depuis
ce smoke; le futur publish connected devra passer par le provisioner runtime
approuve, avec observabilite et recovery.

## Entry 2E-5K Continue Password-Only Flow

Entry 2E-5K ajoute le sous-cas reel observe apres `Continue as
{expected_username}` : Instagram peut afficher un formulaire password-only avec
le username deja selectionne, un champ `Password` et un bouton `Log in`.

Detection UI :

- nouveau `screen_type=continue_password_only`;
- `suggested_username` reste extrait dynamiquement depuis le username visible;
- criteres : username visible, champ `Password`, bouton `Log in`, pas de champ
  username editable vide;
- `overlay_present` signale les overlays
  parasites sans remplacer la classification metier;
- overlays couverts : `Suggest strong password`, saved passwords/autofill,
  variantes Google/password manager et equivalents FR simples.

Execution password-only :

- l'executor password accepte `login_form_empty` et `continue_password_only`;
- en `continue_password_only`, il ne saisit pas le username : il focus seulement
  le champ password, revele le secret via `SecretValue.reveal_for_login_executor()`,
  saisit le password puis tap `Log in`;
- aucun password n'est loggue ni stocke dans result/metadata;
- si un overlay bloque une fois le tap `Log in`, l'executor tente une seule
  recovery minimale : `back` puis refocus password, puis retap `Log in`;
- pas de boucle infinie, pas de coordonnees fixes, pas d'escalade uniquement a
  cause de la presence d'un overlay.

Branchement orchestrateur :

- flow explicite :
  `continue_as_candidate -> Continue -> continue_password_only -> password -> Log in -> classifier`;
- le chemin reste distinct de `login_form_empty`, `connected` direct et
  `use_another_profile`;
- apres `Continue`, si le premier dump post-action retourne `unknown` avec une
  transition `Loading...`, l'orchestrateur fait une seule re-observation courte
  a 1500 ms, sans deuxieme tap `Continue`, sans password et sans `Log in`;
- metadata safe attendue pour ce settling :
  `post_continue_initial_screen=transition_loading`,
  `post_continue_reobserve=true`, `post_continue_reobserve_count=1`,
  `post_continue_final_screen_type=<screen_type>`;
- outcomes post-submit normalises : `connected`, `needs_2fa`, `checkpoint`,
  `login_failed`;
- retries limites a la policy existante de l'orchestrateur, max 1 pour erreurs
  UI transitoires.

Securite :

- aucun credential en clair dans logs/docs/tests;
- aucun Vault read dans les tests unitaires;
- aucun runner hook, aucun flow business, aucun publish HTTP;
- les tests utilisent des fixtures generiques (`random_expected`), pas de
  hardcode metier sur `cinema_catchup`.

Validation reelle no-password 2026-05-26 :

- pre-check device OK sur `emulator-5554` : device unique, aucun `runner.py`,
  aucun sender/follow/unfollow/outreach projet actif;
- `app_start` Instagram OK avec package `com.instagram.android`;
- pre-action observe : `screen_type=continue_as_candidate`,
  `suggested_username=cinema_catchup`, router
  `continue_expected_account`, `would_tap_continue=true`;
- action autorisee executee : un seul tap `Continue`, via l'action executor;
- post-Continue observe : `screen_type=unknown`, classifier `unknown`,
  libelles safe `cinema_catchup`, `Use another profile`,
  `Create new account`, `Loading...`; aucun champ `Password`, aucun bouton
  `Log in`, `overlay_present=false`;
- correction appliquee : `Loading...` est desormais traite comme transition
  post-Continue avec une seule re-observation courte;
- observation reelle suivante no-password : `screen_type=continue_password_only`,
  `suggested_username=cinema_catchup`, champ `Password` present, bouton
  `Log in` present, pas de champ username editable, overlay Google
  `Suggest strong password` detecte comme
  `overlay_type=password_manager_or_autofill`, `overlay_blocking_business=false`,
  `password_required=true`, `ready_for_password_submit=true`;
- aucun password saisi, aucun tap `Log in`, aucun Vault read, aucun credential,
  aucun publish et aucun runner hook;
- le vrai submit reste hors scope de cette validation et devra passer plus tard
  uniquement par Vault/`SecretValue`.

## Entry 2E-5L Direct Login Form Empty

Entry 2E-5L couvre le Cas D : `app_start` Instagram peut ouvrir directement
l'ecran de login complet vide, sans etape `Continue` ni `Use another profile`.

Detection UI :

- `screen_type=login_form_empty`;
- signaux principaux : champ `Username, email or mobile number`, champ
  `Password`, bouton `Log in`;
- signaux secondaires : `Forgot password?`, `Create new account`, `Meta`;
- signaux exposes : `username_editable_present=true`,
  `password_field_editable_present=true`, `password_required=true`,
  `ready_for_credentials_flow=true`;
- le cas reste distinct de `continue_password_only`, qui affiche deja un
  username et n'a pas de champ username editable;
- le patch couvre l'ecran observe en anglais. Des aliases EN/FR supplementaires
  pourront etre ajoutes plus tard sans changer le contrat de routage.

Routage / orchestrateur :

- `login_form_empty -> start_login_form_flow`;
- en dry-run/no-password :
  `would_request_credentials=true`, `would_submit_password=false`,
  `would_publish=false`;
- le vrai submit futur devra passer uniquement par
  `instagram-credentials`/Vault/`SecretValue`, jamais par un password en clair.

Validation reelle no-password 2026-05-26 :

- pre-check device OK sur `emulator-5554` : device unique, aucun `runner.py`,
  aucun sender/follow/unfollow/outreach projet actif;
- `app_start` Instagram OK avec package `com.instagram.android`;
- ecran reel detecte : `screen_type=login_form_empty`, classifier `logged_out`;
- champs/signaux : username field present, password field present, bouton
  `Log in` present, `Forgot password?`, `Create new account`, `Meta`,
  `overlay_present=false`, `ready_for_credentials_flow=true`;
- router/dry-run : `router_decision=start_login_form_flow`,
  `would_request_credentials=true`, `would_submit_password=false`,
  `would_publish=false`;
- aucun username saisi, aucun password saisi, aucun tap `Log in`, aucun Vault
  read, aucun credential reel, aucun publish et aucun runner hook.

## Entry 2E-5M Account Picker / Profile Chooser

Entry 2E-5M couvre le Cas E : `app_start` Instagram peut ouvrir un ecran de
selection de comptes, avec plusieurs lignes de profils et les actions
`Use another profile`, `Create new account` et `Meta`.

Detection UI :

- nouveau `screen_type=account_picker`;
- `available_usernames` liste les handles visibles de facon generique;
- `expected_username_present` et `expected_username_match_count` sont calcules
  quand un `expected_username` est fourni;
- signaux secondaires : `has_use_another_profile_button=true`,
  `has_create_new_account_button=true`, `meta_present=true`;
- aucune logique applicative ne hardcode `cinema_catchup` ou
  `i_m_your_traker`; ces usernames restent des fixtures de smoke/docs.

Routage :

- si `expected_username` est present une seule fois :
  `select_expected_account_from_picker`,
  `would_tap_expected_account=true`;
- si le compte attendu est absent : `expected_account_not_listed`, no tap,
  action dashboard future possible `review_account_picker_missing_expected`;
- si plusieurs lignes correspondent : `ambiguous_expected_account_row`, no tap;
- si `expected_username` manque : `expected_username_missing`, no tap.

Action executor :

- `select_expected_account_from_picker` resout uniquement la ligne du
  `expected_username`;
- resolution par hierarchy XML/bounds : texte username exact normalise, puis
  parent row cliquable contenant la cible si disponible, sinon centre bounds du
  username;
- un seul tap, aucune coordonnee fixe, aucun tap sur un autre compte, aucun
  retry en boucle;
- erreurs safe : `target_account_row_not_found` ou
  `ambiguous_target_account_row`.

Post-action :

- outcomes acceptes sans password : `connected`, `continue_password_only`,
  `login_form_empty`, `needs_2fa`, `checkpoint`, `login_failed`;
- apres tap de compte attendu, un `unknown` transitoire peut preceder le vrai
  ecran suivant. L'orchestrateur fait une seule re-observation courte a 1500 ms
  avec metadata `post_account_picker_*`, sans deuxieme tap;
- si `continue_password_only` ou `login_form_empty` est atteint dans cette
  validation no-password, le flow s'arrete avant submit si aucun credential
  injectable n'est fourni : `would_submit_password=false`.

Validation reelle no-password 2026-05-26 :

- pre-check device OK sur `emulator-5554` : device unique, aucun `runner.py`,
  aucun sender/follow/unfollow/outreach projet actif;
- `app_start` Instagram OK avec package `com.instagram.android`;
- ecran reel detecte : `screen_type=account_picker`,
  `available_usernames=[cinema_catchup, i_m_your_traker]`,
  `expected_username_present=true`, router
  `select_expected_account_from_picker`, `would_tap_expected_account=true`;
- action autorisee executee : un seul tap sur la ligne `cinema_catchup`;
- aucun tap `i_m_your_traker`, aucun tap `Use another profile`, aucun tap
  `Log in`, aucun username/password saisi, aucun Vault read;
- premier post-action observe `unknown` transitoire, puis stabilisation passive
  vers `continue_password_only` avec `ready_for_password_submit=true`;
- aucun submit dans cette validation. Le vrai submit futur reste limite au flow
  Vault/`SecretValue`.

## Entry 2E-5N Old Logged-In Account Recovery

Entry 2E-5N couvre le Cas F : Instagram peut s'ouvrir sur le home/feed ou le
profil d'un ancien compte encore connecte dans l'app, alors que le compte attendu
est different.

Detection UI :

- `screen_type=active_account_home` quand les marqueurs home/feed Instagram sont
  visibles et que le compte actif n'est pas encore confirme;
- `screen_type=active_account_profile` quand le profil actif affiche le username,
  `Edit profile`, `Share profile` et les stats posts/followers/following;
- `screen_type=account_switcher_sheet` pour le sheet contenant le compte actif,
  `Add Instagram account` / `Add profile` et `Go to Accounts Center`;
- `screen_type=add_account_sheet` pour le sheet `Add account` contenant
  `Log into existing account` et `Create new account`;
- `actual_logged_in_username` est extrait dynamiquement depuis le profil actif.

Gate lifecycle obligatoire :

- si `actual_logged_in_username == expected_username`, le flow termine en
  `connected` sans recovery;
- si le compte actif est different, le recovery n'est autorise que si
  `previous_account_lifecycle_lookup(actual_logged_in_username, context)` confirme
  `lifecycle_status in canceled/stopped/archived` et
  `clone_reuse_allowed=true`;
- pour le smoke reel courant, la source autorisee est
  `operator_smoke_override`. Elle est temporaire, explicite, non hardcodee par
  username, et ne vaut que pour cette validation;
- sinon le flow stoppe safe avec `router_decision=block_wrong_active_account` et
  `dashboard_action_type=review_logged_in_account_mismatch`, sans clic.

Recovery V1 no-password :

- depuis home/feed connecte, un seul tap sur le profil bottom nav pour identifier
  le compte actif;
- depuis le profil actif reusable/canceled, tap sur le username/fleche pour
  ouvrir l'account switcher;
- tap `Add Instagram account` / `Add profile`;
- tap `Log into existing account`;
- re-observation apres chaque etape, puis retour attendu vers un cas deja couvert :
  `continue_as_candidate`, `account_picker`, `login_form_empty`,
  `continue_password_only` ou `connected`;
- si l'ecran final est `unknown`, une seule re-observation courte est autorisee,
  puis stop safe.

Garde-fous :

- aucun logout automatique V1;
- aucun tap sur un compte non attendu;
- aucun password, aucun tap `Log in`, aucun Vault read, aucun credential reel;
- aucune coordonnee fixe : les cibles sont resolues par hierarchy XML/bounds,
  libelle exact normalise et dedup de bounds;
- aliases EN couverts dans le patch : `Add Instagram account`, `Add profile`,
  `Log into existing account`;
- aliases FR documentes pour extension : `Ajouter un compte Instagram`,
  `Ajouter un profil`, `Se connecter a un compte existant`,
  `Ajouter un compte existant`.

Validation reelle no-password 2026-05-26 :

- pre-check device OK sur `emulator-5554` : device unique, aucun `runner.py`,
  aucun sender/follow/unfollow/outreach projet actif;
- observation initiale apres `app_start` : `screen_type=active_account_home`,
  classifier `connected`;
- smoke depuis profil actif : `actual_logged_in_username=i_m_your_traker`,
  `expected_username=cinema_catchup`;
- lifecycle gate operator confirme :
  `lifecycle_status=canceled`, `clone_reuse_allowed=true`,
  `source=operator_smoke_override`;
- actions executees : tap username/fleche pour account switcher, tap
  `Add Instagram account`, tap `Log into existing account`;
- le premier ecran obtenu apres `Log into existing account` etait transitoire,
  puis re-observation vers `continue_as_candidate`;
- le flow existant a ensuite tap `Continue` et s'est arrete sur
  `continue_password_only` avec `credentials_missing`;
- signaux finaux : `ready_for_password_submit=true`,
  `ready_for_credentials_flow=false`, `would_submit_password=false`,
  `would_publish=false`;
- aucun logout automatique, aucun password saisi, aucun tap `Log in`, aucun
  Vault read, aucun credential reel, aucun publish.

## Entry 2E-5O Logout Fallback Old Canceled Account

Entry 2E-5O couvre le Cas G : fallback de logout controle pour un ancien compte
encore connecte, uniquement si le chemin Cas F (`Add Instagram account` ->
`Log into existing account`) echoue ou n'est pas disponible.

Ce fallback est separe du flow login principal : il est expose comme chemin
explicite, opt-in, et ne doit jamais etre declenche automatiquement par le
runner ou un flow business.

Gate de securite :

- `actual_logged_in_username` doit etre detecte dynamiquement sur le profil actif;
- `actual_logged_in_username != expected_username`;
- le username doit rester confirme avant les actions critiques;
- `previous_account_lifecycle_lookup(actual_logged_in_username, context)` doit
  confirmer `lifecycle_status in canceled/stopped/archived` et
  `clone_reuse_allowed=true`;
- source autorisee pour le smoke : `operator_smoke_override`, temporaire et non
  hardcodee par username;
- sinon stop safe : aucun logout, dashboard futur
  `review_logged_in_account_mismatch`.

Stabilisation profil / hamburger :

- le probe expose `profile_menu_ready` et
  `profile_menu_missing_transient` sur `active_account_profile`;
- si le hamburger/menu n'est pas disponible, l'orchestrateur attend courtement
  puis re-observe une fois;
- si toujours absent, il tente un seul aller-retour safe `Home` -> `Profile`;
- si le username change, devient incertain, ou si le menu reste absent :
  stop safe avec reason stable (`username_changed` ou `profile_menu_not_found`);
- metadata exposee :
  `profile_menu_initially_missing`, `profile_menu_wait_reobserve`,
  `profile_menu_home_profile_refresh_attempted`, `profile_menu_final_found`,
  `profile_menu_failure_reason`.

Screens / prompts ajoutes :

- `profile_menu_sheet` : entree `Settings and activity`;
- `settings_and_activity` : titre `Settings and activity`, section `Login`,
  lignes `Add account` et `Log out`;
- `save_login_info_prompt` : `Save your login info?`, boutons `Save` et
  `Not now`;
- `logout_confirmation_prompt` : `Log out of your account?`, boutons
  `Log out` et `Cancel`;
- `post_logout_known_screen` est vrai si l'ecran final retombe sur
  `login_form_empty`, `continue_as_candidate`, `account_picker`,
  `continue_password_only` ou `connected`.

Actions autorisees :

- ouvrir hamburger/menu profil par XML/bounds et zone logique action-bar;
- tap `Settings and activity` si necessaire;
- si `Log out` n'est pas visible sur la page settings, scroll controle borne vers
  la fin de la page settings, puis re-observation;
- tap `Log out` seulement apres gate lifecycle valide;
- sur `Save your login info?`, tap uniquement `Not now`, jamais `Save`;
- sur `Log out of your account?`, tap `Log out` seulement si le contexte reste
  confirme;
- une seule re-observation post-logout si l'ecran final est `unknown`, puis stop
  safe.

Aliases :

- EN couverts : `Settings and activity`, `Log out`, `Not now`;
- FR documentes : `Parametres et activite`, `Se deconnecter`, `Deconnexion`,
  `Pas maintenant`, `Plus tard`, `Annuler`, `Enregistrer`.

No-password / no-leak :

- aucune saisie, aucun password, aucun tap `Log in`, aucun Vault read, aucun
  credential reel, aucun publish HTTP, aucune ecriture Supabase;
- aucun logout du `expected_username`;
- aucune coordonnee fixe : les taps utilisent hierarchy XML/bounds, libelles ou
  resource-id, et dedup de bounds.

Validation reelle no-password 2026-05-26 :

- pre-check device OK sur `emulator-5554` : device unique, aucun `runner.py`,
  aucun sender/follow/unfollow/outreach projet actif;
- depart profile : `actual_logged_in_username=i_m_your_traker`,
  `expected_username=cinema_catchup`;
- lifecycle gate operator confirme :
  `lifecycle_status=canceled`, `clone_reuse_allowed=true`,
  `source=operator_smoke_override`;
- hamburger initialement disponible apres stabilisation profile;
- action executee : tap hamburger/profile menu;
- page `Settings and activity` confirmee, `Log out` non visible initialement;
- scroll controle vers la fin settings : `Log out` visible;
- action executee : tap `Log out`;
- prompt `Save your login info?` gere avec `Not now`, jamais `Save`;
- prompt `Log out of your account?` gere avec `Log out`;
- l'orchestrateur strict a stoppe safe sur `post_logout_unknown_screen` apres
  une re-observation unique;
- inspection passive juste apres le stop safe : stabilisation vers
  `screen_type=account_picker` avec `cinema_catchup` et `i_m_your_traker`;
- aucun password, aucune saisie, aucun tap `Log in`, aucun Vault read, aucun
  credential reel, aucun publish HTTP, aucune ecriture Supabase.

## Entry 2E-5P Secure Password Login Smoke

Entry 2E-5P prepare le premier smoke password reel controle pour
`expected_username=cinema_catchup`, exclusivement via le chemin securise :
`instagram-credentials` -> Supabase Vault -> runtime Vault reader ->
`SecretValue` -> login executor.

Regle de preparation ecran login/provisioning :

- tout flow reel, smoke reel ou onboarding/provisioning par defaut commence par
  `app_start(package_name)` avant le premier probe;
- `package_name` vaut `com.instagram.android` par defaut et reste configurable
  pour les futurs clones;
- le wait post-start est court et borne (`0..3000 ms`, defaut `1500 ms`);
- le mode sans `app_start` doit etre explicite :
  `observe_current_screen_only=True` / `--observe-current-screen-only`, reserve
  aux tests unitaires et diagnostics manuels;
- raison : eviter de classifier un launcher Android, home Android, autre app,
  clone non ouvert, ecran stale ou transition comme `unknown` exploitable;
- tout routing Cas A-G se fait apres `app_start`, dump et classification.

Regles no-leak :

- ne jamais demander ni coller le password dans Cursor;
- ne jamais afficher password, `SecretValue` utile, `secret_ref` complet, UUID
  Vault, service-role key, bearer token, XML brut ou screenshot path;
- le password ne peut etre revele que via
  `SecretValue.reveal_for_login_executor()` dans
  `instagram_login_password_form_executor.py`, apres validation de l'ecran;
- aucun publish HTTP ni ecriture Supabase par defaut.

Preconditions avant submit :

- device unique et idle (`emulator-5554`, aucun runner/sender/follow/outreach);
- ecran initial strictement `continue_password_only` ou `login_form_empty`;
- si username visible, il doit matcher `cinema_catchup`;
- credentials actifs avec username attendu, `secret_provider=supabase_vault`,
  `secret_ref` Vault valide, Vault read OK et `SecretValue` cree;
- sinon stop safe avant tout tap password.

Validation 2026-05-26 :

- tests pre-smoke OK sur runtime credentials, Vault reader, password executor,
  orchestrateur, probe, routeur et action executor;
- pre-check device OK : `emulator-5554` unique, aucun process business actif;
- verification credentials safe via Supabase : metadata presente, mais aucune
  row active ne satisfait simultanement `username=cinema_catchup`,
  `secret_provider=supabase_vault` et `secret_ref` Vault valide;
- Vault read runtime local non execute car les preconditions metadata ont echoue;
- observation passive device : ecran initial non accepte (`unknown`), pas
  `continue_password_only` ni `login_form_empty`;
- resultat : stop safe avant saisie password, aucun tap `Log in`, aucun Vault
  password expose, aucun publish.

Retry 2026-05-26 apres synchronisation du token interne :

- pre-check device OK : `emulator-5554` unique, aucun runner/sender/follow/
  unfollow/outreach detecte dans les processus projet;
- credentials safe check OK : metadata active pour `cinema_catchup`,
  `credentials_version=1000`, `reauth_required=true`,
  `secret_provider=supabase_vault`, forme de reference valide, username attendu
  confirme et lecture Vault reussie en memoire uniquement;
- observation passive device : ecran initial `unknown`, donc non accepte pour le
  smoke password reel;
- action executee : aucune. Le flow stoppe avant
  `SecretValue.reveal_for_login_executor()`, avant saisie password et avant tap
  `Log in`;
- classification : `final_outcome=unknown`,
  `status_candidate=unknown_stop_safe_initial_screen_not_accepted`,
  `would_publish=false`;
- aucune publication HTTP, aucune ecriture Supabase status, aucun runner hook,
  aucun flow business.

Correction architecture 2026-05-27 :

- `run_login_provisioning_flow(...)` tente maintenant `app_start` par defaut
  avant observation avec metadata safe :
  `app_start_attempted`, `app_start_ok`, `package_name`,
  `post_start_wait_ms`, `screen_after_app_start`;
- `instagram_login_probe_cli.py` demarre aussi le package par defaut; le mode
  courant sans start est uniquement `--observe-current-screen-only`;
- si `app_start` echoue : stop safe `reason=app_start_failed`, sans credentials,
  sans Vault reveal, sans password submit;
- si `app_start` reussit mais prepare un ecran `unknown` : stop safe
  `reason=screen_preparation_failed`, sans credentials, sans Vault reveal, sans
  password submit;
- si l'app arrive directement connectee : stop success
  `reason=connected_no_password_needed`, `would_publish=false`;
- les ecrans `account_picker` et `continue_as_candidate` passent par les routes
  validees avant tout submit; le password n'est revele que si le routing aboutit
  a `continue_password_only` ou `login_form_empty`.

Relance 2E-5P 2026-05-27 avec `app_start` obligatoire :

- credentials `cinema_catchup` inchanges et prets; aucune modification
  credentials;
- pre-check device OK : device unique et aucun process projet business actif;
- `app_start_attempted=true`, `app_start_ok=true`,
  `package_name=com.instagram.android`, `post_start_wait_ms=1500`;
- `screen_after_app_start=unknown`;
- resultat : stop safe `reason=screen_preparation_failed`,
  `final_outcome=unknown`;
- `preparation_flow_used=none`, `screen_before_submit=unknown`,
  `submit_executed=false`, `password_submit_result=not_executed`,
  `would_publish=false`;
- aucun password, aucun `secret_ref` complet, aucun UUID Vault, aucun bearer
  token/header, aucun XML brut, aucun screenshot path, aucun publish et aucune
  ecriture status Supabase.

Inspection 2E-5Q-1 2026-05-27 apres `screen_preparation_failed` :

- pre-check OK : device unique, aucun process projet business actif;
- inspection passive uniquement : `app_start`, wait `1500 ms`, dump/probe en
  memoire, aucun tap, aucune saisie, aucun credential, aucun Vault read;
- `app_start_attempted=true`, `app_start_ok=true`,
  `package_name=com.instagram.android`, top activity Instagram modal;
- l'ecran observe n'est pas `unknown` pendant cette inspection :
  `screen_after_app_start=continue_as_candidate`;
- labels safe visibles : compte attendu `cinema_catchup`, `Continue`,
  `Use another profile`, `Create new account`, signaux Meta/Instagram;
- grands signaux absents : password field, `Log in`, home/feed, profile,
  checkpoint/2FA/error, loading/progress;
- `classifier_outcome=unknown` reste attendu pour un ecran pre-login non final :
  le routing screen type est reconnu, mais le classifier de statut login ne doit
  pas le publier comme connected/2FA/checkpoint/failed;
- cause probable du `unknown` precedent : etat transitoire/stale ou timing de
  preparation lors de la relance 2E-5P, pas evidence d'un ecran connu mal classe;
- patch code non applique. 2E-5P reste sans submit tant qu'un flow controle ne
  confirme pas `continue_password_only` ou `login_form_empty`.

Relance 2E-5P 2026-05-27 avec preparation `continue_as_candidate` :

- pre-check OK : device unique, aucun runner/sender/follow/unfollow/outreach
  projet actif;
- credentials `cinema_catchup` inchanges et prets : metadata active,
  provider Vault attendu, username confirme, secret chargeable;
- `app_start_attempted=true`, `app_start_ok=true`,
  `screen_after_app_start=continue_as_candidate`,
  `preparation_flow_used=continue_as_candidate`;
- action autorisee executee : un seul tap `Continue`;
- reobserve passif post-Continue : `continue_password_only` avec username attendu
  confirme, champ password et bouton `Log in`;
- patch minimal executor : si une cible primaire unique (`text`) existe, une
  strategie secondaire (`content-desc`) ambigue du meme libelle ne bloque plus
  le submit. Les vrais doublons d'une meme strategie (`count > 1`) restent
  refuses;
- submit unique execute depuis `continue_password_only` : password saisi via
  `SecretValue.reveal_for_login_executor()` dans l'executor uniquement, tap
  `Log in` unique, aucun retry password;
- resultat post-submit : `final_outcome=unknown`, `status_candidate=unknown`.
  Le premier dump etait `logged_out`/session expired; un reobserve passif court
  a ensuite donne un ecran `unknown` avec seulement un libelle safe `OK`, sans
  signal connected, 2FA, checkpoint ou login_failed exploitable;
- `submit_executed=true`, `password_submit_result=submitted_not_connected`,
  `would_publish=false`, `published=false`;
- aucun password, aucun `secret_ref` complet, aucun UUID Vault, aucun bearer
  token/header, aucun XML brut, aucun screenshot path et aucune ecriture status
  Supabase.

## Entry 2E-5P-2 Password Required Dialog

Entry 2E-5P-2 traite explicitement la popup Instagram post-submit :
`Password required` / `Enter your password to continue.` / `OK`.

Detection :

- `extract_login_screen_signals_from_hierarchy(...)` expose
  `screen_type=password_required_dialog`,
  `password_required_dialog_present=true` et `has_ok_button=true`;
- `probe_login_ui_from_hierarchy(...)` retourne
  `reason=password_required_dialog` avec outcome `unknown`, afin de ne pas
  classer ce cas en connected/2FA/checkpoint/login_failed;
- aliases documentes : `Mot de passe requis`,
  `Saisissez votre mot de passe pour continuer`, `OK`.

Pre-submit guard :

- l'executor password continue a reveler le secret uniquement via
  `SecretValue.reveal_for_login_executor()`;
- si l'accessibilite expose clairement un champ password vide apres injection,
  l'executor stoppe avant `Log in` avec
  `password_input_not_confirmed`;
- si la valeur masquee n'est pas lisible, le signal safe reste
  `input_action_reported_success=true` tant qu'aucune erreur input n'est levee.

Recovery bornee :

- si la popup `password_required_dialog` apparait apres submit :
  tap `OK` une seule fois;
- refocus password, clear/refill via `SecretValue` uniquement dans l'executor;
- second tap `Log in` unique;
- `max_password_required_retry=1`;
- si la popup reapparait : `final_outcome=password_input_failed`, aucun retry
  supplementaire.

Metadata safe :

- `password_required_dialog_detected`;
- `password_required_retry_attempted`;
- `password_required_retry_count`;
- `password_refill_attempted`;
- `second_submit_executed`;
- jamais password, `secret_ref` complet, UUID Vault, token/header, XML brut ou
  screenshot path.

Smoke reel 2026-05-27 :

- la popup visuelle observee precedemment a ete identifiee comme
  `password_required_dialog`;
- apres patch, le premier smoke est reparti par `app_start`, mais l'observation
  immediate a stoppe safe en `screen_preparation_failed`; l'ecran s'est ensuite
  stabilise passivement en `continue_as_candidate`;
- une relance depuis `continue_as_candidate` stabilise a execute un seul tap
  `Continue`, puis a stoppe safe en `unknown_login_screen` pendant la transition;
- aucune popup password-required n'etait visible au moment du smoke patch, donc
  la recovery OK/refill/retry n'a pas ete exercee en reel;
- aucun password en clair, aucun publish et aucune ecriture status Supabase.

Prochaine etape :

- l'operateur doit soumettre/mettre a jour les credentials via le flow securise
  `instagram-credentials`;
- ne jamais fournir le password dans Cursor;
- relancer 2E-5P seulement quand une row active `supabase_vault` valide existe
  et que l'ecran initial est `continue_password_only` ou `login_form_empty`.

## Entry 2E-5P-3 Password Input Injection

Entry 2E-5P-3 corrige la cause racine observee apres 2E-5P-2 : le routing
atteignait bien `continue_password_only`, mais le password n'etait pas injecte
dans la vraie cible avant `Log in`.

Cause trouvee :

- l'ancien executor ciblait `text="Password"` / `content-desc="Password"`;
- sur l'ecran reel password-only, cette cible correspondait a un label
  `android.view.View`, pas a l'`android.widget.EditText`;
- l'appel input pouvait donc rapporter success alors que le readback du label
  restait vide/placeholder;
- le guard a correctement bloque `Log in` avec
  `password_input_not_confirmed` pendant le diagnostic.

Correction :

- en mode `continue_password_only`, l'executor prefere maintenant l'`EditText`
  unique avant les labels `Password`;
- focus robuste : clic accessibilite, fallback bounds/tap centre si disponible,
  puis stabilisation courte;
- injection password via ADB Keyboard `ADB_INPUT_B64` en priorite, envoyee via
  stdin adb shell pour eviter d'exposer la charge utile dans les argv host;
- `set_text` reste un fallback controle si ADB Keyboard est indisponible;
- verification post-input : `password_field_non_empty_confirmed=true/false/unknown`;
- si la lecture prouve `false`, aucun tap `Log in`.

Smoke reel 2026-05-27 :

- pre-check device OK : device unique, ADB Keyboard enabled/current, aucun
  runner/sender/follow/unfollow/outreach actif;
- credentials `cinema_catchup` actifs, version 1000, provider Vault, secret lu
  uniquement via `SecretValue`;
- premier diagnostic : `adb_keyboard_b64` rapportait success, mais l'ancienne
  cible label restait `password_field_non_empty_confirmed=false`, donc
  `submit_tapped=false`;
- apres correction cible `EditText` :
  `input_method_used=adb_keyboard_b64`,
  `password_field_focused_before_input=true`,
  `password_field_non_empty_confirmed=true`,
  `submit_tapped=true`;
- aucune popup `Password required` detectee, `retry_count=0`;
- post-submit observe `logged_out/session_expired`, sans connected/2FA/checkpoint
  ni publish/status write. L'injection password n'est plus le blocage.

No-leak :

- aucun password en clair, aucune longueur/hash, aucun `secret_ref` complet,
  aucun UUID Vault complet, aucun token/header, aucun XML brut, aucun screenshot
  path et aucune ecriture Supabase status.

## Entry 2E-5P-4 Vault Password Extraction

Entry 2E-5P-4 corrige un bug critique post-2E-5P-3 : l'injection ADB Keyboard
fonctionnait, mais le champ Instagram recevait parfois un payload JSON/metadata
Vault complet au lieu du mot de passe seul.

Cause :

- l'Edge Function `instagram-credentials` stocke dans Vault un JSON
  `{password, account_id, credentials_version, created_at, ...}`;
- `read_supabase_vault_secret(...)` enveloppait ce JSON brut dans `SecretValue`
  sans extraction;
- l'executor revelait donc une chaine de type
  `password":"[REDACTED]","account_id":"...","credentials_version":1000,...`.

Correction :

- `parse_vault_secret_for_login(...)` dans
  `instagram_credentials_runtime_access.py` :
  - si JSON : extraire uniquement `payload["password"]` (string non vide);
  - si legacy plain string : accepter seulement si la valeur ne ressemble pas a
    un payload metadata/JSON;
- `instagram_supabase_vault_reader.py` normalise avant `SecretValue`;
- `get_instagram_credentials_for_login(...)` re-normalise defensivenement;
- guard final dans `instagram_login_password_form_executor.py` :
  `blocked_secret_payload_shape` -> pas de tap `Log in`, pas d'injection.

Erreurs safe :

- `vault_secret_payload_missing_password`;
- `vault_secret_password_invalid`;
- `blocked_secret_payload_shape` (executor);
- `final_outcome=secret_payload_not_password` (orchestrateur).

Dry-run no-device 2026-05-27 (cinema_catchup, sans afficher secret/password) :

- `vault_secret_is_json=true`;
- `vault_secret_has_password_key=true`;
- `vault_secret_contains_metadata_keys=true` (attendu dans le secret Vault brut);
- `extracted_password_valid=true`;
- `secret_value_safe_for_injection=true`;
- `credentials_ok=true`, `secret_loaded=true`, provider Vault;
- guard anti-payload : `guard_would_block_revealed_value=false`.

Prochaine etape :

- smoke device 2E-5P-4 separe uniquement apres validation operateur du dry-run;
- aucun submit Instagram dans ce patch.

## Entry 2E-5P-5 Password-Only Secure Login Smoke (device)

Entry 2E-5P-5 valide sur device reel (`emulator-5554`, compte `cinema_catchup`)
que l'extraction Vault password-only (2E-5P-4) et l'injection robuste (2E-5P-3)
fonctionnent ensemble : plus de payload JSON dans le champ password, plus de
popup `Password required` apres submit.

Pre-check :

- `device_count=1`, `device_serial=emulator-5554`;
- `business_run_detected=false`, `runner.py` inactif;
- flows sender/follow/unfollow/outreach projet inactifs.

Credentials safe (metadata only, no-leak) :

- `username=cinema_catchup`, `credentials_status=active`,
  `credentials_version=1000`, `secret_provider=supabase_vault`;
- `secret_ref` shape valide (redacted), `username_matches_expected=true`;
- `secret_loaded=true`, `injectable_password_only=true`,
  `secret_value_safe_for_injection=true`,
  `guard_would_block_revealed_value=false`.

Preparation ecran :

- `app_start_ok=true` sur `com.instagram.android`;
- juste apres `app_start`, le probe peut encore classer `unknown` transitoire
  (orchestrateur : `screen_preparation_failed` si stop immediat);
- apres stabilisation (~1–2 s) : `continue_as_candidate` avec
  `suggested_username=cinema_catchup` -> tap `Continue` une fois ->
  `continue_password_only`, username affiche conforme.

Submit controle (`screen_before_submit=continue_password_only`) :

- `submit_executed=true`;
- `input_method_used=adb_keyboard_b64`;
- `password_field_focused_before_input=true`;
- `password_field_non_empty_confirmed=true`;
- `password_required_dialog_detected=false`, `retry_count=0`;
- injection uniquement via `SecretValue` dans l'executor, pas de publish HTTP,
  pas de status write Supabase, `would_publish=false`.

Post-submit :

- `final_outcome=logged_out`, `reason=session_expired` (classifieur safe);
- pas `connected`, pas `needs_2fa`, pas `checkpoint`, pas
  `password_input_failed`;
- aucun retry password automatique.

Note 2E-5P-7 : ce resultat etait base sur un dump immediat
`post_submit_wait_ms=0` et n'est pas une conclusion finale fiable. Un flow
login ne doit plus conclure `session_expired` juste apres `Log in` sans settling
post-submit borne.

Conclusion smoke :

- objectif 2E-5P-4/5 atteint : valeur injectable password-only, guard anti-payload
  inactif sur secret valide, champ password confirme non vide avant `Log in`;
- blocage restant = etat session Instagram post-login (`session_expired`), pas
  l'injection ni l'extraction Vault.

No-leak :

- aucun password, longueur/hash, `secret_ref` complet, UUID Vault complet,
  raw Vault payload, token/header, XML brut, screenshot path.

Checkpoint :

- SHA base `0af5258` (2E-5P-4) + docs smoke 2E-5P-5;
- tag `checkpoint-entry2e5p5-password-only-secure-login-smoke-20260526`.

CLI reproductible 2E-5P-5 :

```bash
python3 instagram_login_provisioner_cli.py \
  --device-serial emulator-5554 \
  --expected-username cinema_catchup \
  --account-id "$INSTAGRAM_ACCOUNT_ID" \
  --package-name com.instagram.android \
  --start-app-before-probe \
  --no-publish \
  --json
```

Commande logs apres run :

```bash
RUN_ID="<run_id_from_cli_json>" python3 - <<'PY'
import json
import os

run_id = os.environ["RUN_ID"]
path = "logs/instagram_login_provisioner.jsonl"
allowed = {
    "run_id",
    "expected_username",
    "device_serial",
    "flow_name",
    "app_start_ok",
    "screen_after_app_start",
    "preparation_flow_used",
    "screen_before_submit",
    "input_method_used",
    "password_field_non_empty_confirmed",
    "submit_executed",
    "post_submit_observation_count",
    "post_submit_wait_total_ms",
    "post_submit_screens",
    "final_terminal_screen",
    "final_outcome",
    "reason",
    "would_publish",
    "timings",
    "warnings",
    "no_leak_summary",
}

with open(path, "r", encoding="utf-8") as handle:
    for raw in handle:
        if f'"run_id":"{run_id}"' not in raw:
            continue
        payload = json.loads(raw)
        print(json.dumps({k: payload.get(k) for k in sorted(allowed)}, sort_keys=True))
PY
```

Fallback diagnostic par username (dernieres lignes safe du JSONL dedie) :

```bash
python3 - <<'PY'
import json

path = "logs/instagram_login_provisioner.jsonl"
rows = []
with open(path, "r", encoding="utf-8") as handle:
    for raw in handle:
        payload = json.loads(raw)
        if payload.get("expected_username") == "cinema_catchup":
            rows.append(payload)
for payload in rows[-20:]:
    print(json.dumps({
        "run_id": payload.get("run_id"),
        "final_outcome": payload.get("final_outcome"),
        "reason": payload.get("reason"),
        "screen_before_submit": payload.get("screen_before_submit"),
        "submit_executed": payload.get("submit_executed"),
        "post_submit_observation_count": payload.get("post_submit_observation_count"),
        "post_submit_wait_total_ms": payload.get("post_submit_wait_total_ms"),
        "post_submit_screens": payload.get("post_submit_screens"),
        "final_terminal_screen": payload.get("final_terminal_screen"),
        "would_publish": payload.get("would_publish"),
        "timings": payload.get("timings"),
    }, sort_keys=True))
PY
```

Notes CLI :

- `--account-id` est requis pour charger les credentials actifs via le runtime
  access + Supabase Vault; la valeur peut venir d'une variable shell locale et
  ne doit pas etre imprimee dans les logs applicatifs;
- aucun password ni `secret_ref` n'est accepte en argument;
- `--start-app-before-probe` est le comportement par defaut; utiliser
  `--observe-current-screen-only` uniquement en diagnostic;
- `--dry-run` ou `--no-submit` route/preparer sans charger Vault et sans submit;
- la sortie JSON est safe : `app_start_attempted`, `app_start_ok`,
  `screen_after_app_start`, `preparation_flow_used`, `screen_before_submit`,
  `input_method_used`, `password_field_non_empty_confirmed`,
  `submit_executed`, `post_submit_observation_count`,
  `post_submit_wait_total_ms`, `post_submit_screens`,
  `final_terminal_screen`, `final_outcome`, `reason`,
  `would_publish=false`, timings, warnings et resume no-leak;
- le CLI genere un `run_id` safe et append une ligne JSONL safe dans
  `logs/instagram_login_provisioner.jsonl`;
- l'orchestrateur doit traiter `unknown` juste apres `app_start` comme un etat
  potentiellement transitoire : le flow reel fait une preparation/reobserve
  bornee avant de conclure `screen_preparation_failed`.

## Entry 2E-5P-7 Post-submit Settling

Entry 2E-5P-7 corrige la classification trop precoce apres le tap `Log in`.
Avant ce patch, l'orchestrateur appelait l'executor avec
`post_submit_wait_ms=0`; un dump a ~100 ms pouvait encore voir l'ancien ecran
login et produire `logged_out/session_expired`, alors que l'app pouvait basculer
vers le home feed juste apres.

Comportement obligatoire apres `submit_tapped=true` :

- boucle bornee de settling post-submit dans l'executor password;
- observations rapides et limitees (`max_post_submit_observations=4`,
  intervalle par defaut 1000 ms, soit ~4 s max);
- arret immediat sur etat terminal clair :
  `connected`, `needs_2fa`, `checkpoint`, `login_failed`,
  `password_required_dialog`;
- `password_required_dialog` conserve la recovery existante bornee :
  OK -> refocus/refill -> un seul second submit maximum;
- `logged_out/session_expired` n'est retenu qu'apres settling complet :
  `reason=session_expired_after_settling`;
- `unknown` apres timeout devient
  `reason=post_submit_unknown_after_settling`;
- pas de retry password automatique hors recovery `Password required`, pas de
  publish HTTP, pas de status write Supabase.

Connected detection post-login :

- le classifieur continue d'utiliser les signaux `connected_ui_signal`;
- l'executor considere aussi les signaux safe `active_account_home` et
  `active_account_profile` comme `connected` apres submit;
- aucun XML brut, screenshot path, password, token, `secret_ref` complet ou UUID
  Vault n'est expose dans les metadata/logs.

Nouveaux champs JSON/JSONL safe :

- `post_submit_observation_count`;
- `post_submit_wait_total_ms`;
- `post_submit_screens` (labels safe, ex. `loading`, `connected_home`);
- `final_terminal_screen`;
- `final_outcome`;
- `reason`.

## Entry 2E-5P-8 Google Password Manager Save Prompt

Entry 2E-5P-8 traite l'overlay systeme post-login Google Password Manager qui
peut apparaitre apres le tap `Log in`, au-dessus d'Instagram. Il ne doit pas
etre classe comme `login_failed` ni comme `unknown` final tant qu'un dismiss
borne n'a pas ete tente.

Detection ajoutee :

- `screen_type=google_password_manager_save_prompt`;
- signaux EN observes : `Google Password Manager`,
  `Save password for Instagram?`, bouton `Continue`;
- alias documentes pour variantes futures : `Gestionnaire de mots de passe
  Google`, `Enregistrer le mot de passe pour Instagram ?`, `Continuer`;
- la popup peut etre absente ou varier selon device/ROM.

Regle d'action :

- ne jamais cliquer `Continue`;
- ne jamais sauvegarder le mot de passe;
- ne jamais extraire ni logger le contenu de la popup;
- dismiss unique par `back` (`dismiss_method=back`), puis reobserve pendant le
  settling post-submit;
- si la popup reste visible apres le dismiss unique :
  `final_outcome=save_password_prompt_blocking`, no retry infini, no publish.

Champs JSON/JSONL safe ajoutes :

- `save_password_prompt_detected`;
- `save_password_prompt_dismissed`;
- `dismiss_method`;
- `post_dismiss_screen_type`.

## Entry 2E-5P-9 Startup Screen Settling

Entry 2E-5P-9 corrige le stop trop precoce juste apres `app_start`. Certains
runs voyaient `screen_after_app_start=unknown` a ~1.5 s, alors que l'ecran
reel devenait ensuite `continue_as_candidate` (`cinema_catchup`, `Continue`,
`Use another profile`, `Create new account`).

Comportement obligatoire apres `app_start_ok=true` :

- premiere observation apres le wait post-start existant;
- fast path si l'ecran est deja exploitable, sans reobserve inutile;
- si la premiere observation est `unknown` ou transitoire, reobserve borne
  (`max_startup_observations=4`, intervalle par defaut 1000 ms);
- arret immediat des qu'un ecran routable est detecte :
  `continue_as_candidate`, `account_picker`, `login_form_empty`,
  `continue_password_only`, `active_account_home`, `active_account_profile`,
  `connected`, `password_required_dialog`, `google_password_manager_save_prompt`
  ou etat terminal 2FA/checkpoint/login_failed;
- si l'ecran reste `unknown` apres settling complet :
  `reason=screen_preparation_failed_after_startup_settling`;
- aucun credential/Vault n'est charge et aucun submit n'est autorise tant que
  l'ecran n'est pas routable ou accepte.

Champs JSON/JSONL safe ajoutes :

- `startup_observation_count`;
- `startup_wait_total_ms`;
- `startup_screens` (labels safe, ex. `unknown`, `continue_as_candidate`);
- `startup_final_screen_type`;
- `startup_settling_used`;
- `screen_after_app_start_initial`;
- `screen_after_app_start_final`.

## Entry 2E-5P-10 Long Post-submit Loading

Entry 2E-5P-10 distingue un vrai `unknown` post-submit d'un submit encore en
cours. Si toutes les observations post-submit restent sur un loading clair,
l'executor retourne :

- `final_outcome=login_submit_still_loading`;
- `reason=post_submit_loading_timeout`;
- `post_submit_loading_timeout=true`;
- `would_publish=false`.

Le CLI expose `--post-submit-timeout-ms` pour les smokes operateur. Le defaut
CLI est 10000 ms, avec intervalle court, et le settling garde le fast path :
arret immediat sur `connected`, 2FA, checkpoint, login_failed,
`password_required_dialog` ou `google_password_manager_save_prompt`.

Google Password Manager :

- la popup `google_password_manager_save_prompt` reste detectee via
  `Google Password Manager` / `Save password for Instagram?` / `Continue` et
  alias FR (`Gestionnaire de mots de passe Google`, `Enregistrer le mot de
  passe pour Instagram ?`, `Continuer`);
- le flow ne clique jamais `Continue` et ne sauvegarde jamais le mot de passe;
- dismiss safe par `back`, maximum 2 tentatives;
- si la popup reste visible apres 2 tentatives :
  `final_outcome=save_password_prompt_blocking`,
  `reason=save_password_prompt_not_dismissed_after_2_attempts`;
- logs safe ajoutes : `save_password_prompt_dismiss_attempt_count`,
  `dismiss_method`, `post_dismiss_screen_type`.

Depuis Entry 2E-5P-10, Cursor ne lance plus de smoke device reel par defaut :
Cursor fournit patch, tests, docs et commandes; l'operateur lance le run terminal
et rapporte l'observation visuelle.

## Entry 2E-5P-11 Cas A Router Wiring + Post Use Another Profile Settling

Patch 2E-5P-11 corrige d'abord le wiring Cas A avant le formulaire prefilled.

Bug observe :

- `previous_account_lifecycle_source=operator_smoke_override` et
  `clone_reuse_allowed=true` etaient visibles dans le JSON safe;
- mais `router_decision=unknown_no_action` et `preparation_flow_used=none`
  alors que `screen_after_app_start_final=continue_as_candidate`,
  `suggested_username != expected_username` et lifecycle `canceled` reusable.

Cause racine :

- le routeur recevait parfois un `screen_type` different de
  `continue_as_candidate` (ex. `active_account_home` ou `unknown`) alors que le
  settling startup avait deja stabilise `continue_as_candidate`;
- l'override operateur etait bien resolu pour la metadata lifecycle, mais la
  decision finale utilisait encore le mauvais `screen_type` de routage.

Correctifs :

- priorite probe : `continue_as_candidate` avant `active_account_home` quand
  Continue + Use another profile + suggested username sont presents;
- `_routing_screen_type(...)` reutilise `startup_final_screen_type` /
  `screen_after_app_start_final` et les marqueurs Continue-as avant de router;
- filet de securite `_route_provisioning_screen(...)` : si lifecycle
  `canceled/stopped/archived` + `clone_reuse_allowed=true` et suggested !=
  expected, ne jamais rester sur `unknown_no_action`;
- apres `tap_use_another_profile`, settling borne (jusqu'a 4 observations,
  intervalle ~1000 ms) avant stop sur `unknown`;
- ecrans acceptes apres le tap :
  `login_form_empty`, `login_form_prefilled_username`, `account_picker`,
  `continue_as_candidate`, `continue_password_only`.

Logs JSONL safe ajoutes :

- `post_use_another_profile_observation_count`;
- `post_use_another_profile_screens`;
- `screen_after_use_another_profile_final`;
- `preparation_flow_used=use_another_profile_previous_account_stopped` quand
  l'action `tap_use_another_profile` a ete executee.

Route attendue Cas A :

- `router_decision=use_another_profile_previous_account_stopped`;
- `action=tap_use_another_profile`;
- puis, si besoin, branche 2E-5P-11 prefilled username ci-dessous.

## Entry 2E-5P-12 Username Replace On Prefilled Login Form

Entry 2E-5P-12 corrige l'executor quand Cas A aboutit a
`login_form_prefilled_username` avec un ancien username editable.

Bug observe (`run_id` operateur) :

- routing OK : `use_another_profile_previous_account_stopped` puis
  `start_login_form_flow_replace_username`;
- ecran reel : username `i_m_your_traker` focus, password vide, bouton Log in;
- echec : `username_input_failed`, `username_input_ms=0`, `username_replaced=false`
  alors que le curseur etait dans le champ.

Cause racine :

- cible username parfois non-EditText ou clear juge obligatoire trop tot;
- `_clear_target_text_checked` retournait `False` sans fallback `set_text`;
- si le texte restait apres clear, l'executor stoppait avant toute saisie reelle;
- `username_input_ms` restait a 0 car l'echec arrivait avant la fin du bloc username.

Correctifs :

- preferer `android.widget.EditText` (premier champ) avant le match texte du prefilled;
- focus username (click + bounds tap) avant clear/input;
- clear en cascade : `clear_text` -> `set_text("")` -> `set_text(expected)` ->
  fallback `adb_keyboard_b64` si disponible;
- confirmation par readback selector + re-dump hierarchy (`prefilled_username`);
- reasons explicites :
  `username_clear_failed`, `username_still_prefilled_after_input`,
  `username_field_not_found`, `username_field_not_focusable`;
- pas de reveal password tant que username non confirme/assume;
- logs safe :
  `username_field_focused_before_input`, `username_clear_method`,
  `username_input_method`, `username_input_ms`.

## Entry 2E-5P-13 Masked Password Confirmation After Username Replace

Entry 2E-5P-13 traite la suite directe du Cas A :

- `login_form_prefilled_username` atteint apres `Use another profile`;
- ancien username remplace et confirme (`username_replaced=true`,
  `username_input_confirmed=true`);
- password injecte via methode robuste, mais submit bloque par
  `password_input_not_confirmed` alors que le champ affichait des bullets.

Cause racine :

- la confirmation password relisait surtout la cible accessibilite initiale
  `Password`;
- apres `adb_keyboard_b64`, cette cible peut rester au placeholder accessible
  meme si le vrai champ EditText affiche un contenu masque;
- l'executor interpretait alors `password_field_non_empty_confirmed=false` et
  stoppait avant `tap Log in`.

Correctifs :

- cible password : preferer le deuxieme `android.widget.EditText` lorsque le
  formulaire contient username + password;
- confirmation safe apres input :
  - `target_accessibility_non_empty`;
  - `hierarchy_masked_password` si le champ EditText contient uniquement des
    bullets / caracteres masques;
  - `unknown_but_input_success` si `adb_keyboard_b64` reussit, que la cible
    password est plausible et qu'aucun signal safe ne prouve un champ vide;
- si le hierarchy ou l'accessibilite prouve un champ vide :
  `password_input_not_confirmed`, no submit;
- la recovery `Password required` reste bornee : OK/refocus/refill, un retry
  maximum.

Logs JSONL safe ajoutes :

- `password_field_target_kind`;
- `password_input_method`;
- `password_input_result`;
- `password_confirm_method`;
- `password_field_non_empty_confirmed` peut valoir `true`, `false` ou
  `unknown_but_input_success`.

Le smoke reste `--no-publish`; aucun password, longueur/hash, `secret_ref`,
Vault UUID, token/header, XML brut ou screenshot path ne doit etre logge.

Finalisation metadata 2E-5P-13 :

- le run operateur Cas A complet a valide `final_outcome=connected`, username
  remplace, password confirme via `hierarchy_masked_password`, dismiss Google
  Password Manager par `back`, et `would_publish=false`;
- bug restant corrige : apres `Use another profile`, la re-resolution lifecycle
  sur `login_form_prefilled_username` ne doit plus ecraser l'override initial
  avec `{lifecycle_status=unknown, clone_reuse_allowed=false, source=""}`;
- les metadata finales conservent donc
  `source=operator_smoke_override`, `lifecycle_status=canceled`,
  `clone_reuse_allowed=true`, et `suggested_username` dynamique du compte
  precedent.

## Entry 2E-5P-15 Direct Empty Login Form Full Provisioning Flow

Entry 2E-5P-15 valide le Cas C : Instagram ouvre directement sur
`login_form_empty` avec username vide, password vide, `Log in`,
`Forgot password?`, `Create new account` et Meta.

Le CLI generique supporte ce chemin sans flag special :

- `screen_type=login_form_empty` ;
- `router_decision=start_login_form_flow` ;
- `preparation_flow_used=start_login_form_flow` ;
- saisie de `expected_username` dans le vrai champ username ;
- revelation du password uniquement via `SecretValue` / Vault apres validation
  du formulaire et de l'etape username ;
- injection password par la strategie robuste existante ;
- tap `Log in` ;
- post-submit settling ;
- dismiss Google Password Manager si present sans cliquer `Continue` ;
- `would_publish=false` pendant le smoke `--no-publish`.

Cas C ne doit pas etre confondu avec
`login_form_prefilled_username` :

- `login_form_empty` saisit username + password ;
- `login_form_prefilled_username` couvre le remplacement username du Cas A ;
- `username_replaced=false` sur Cas C ;
- `username_input_result=username_input_confirmed` quand la confirmation
  accessibilite est possible.

Etats post-submit acceptes :

- `connected_home` / feed ;
- `google_password_manager_save_prompt` puis connected apres dismiss ;
- `needs_2fa` ;
- `checkpoint` ;
- `login_failed` ;
- `password_required_dialog` avec recovery bornee existante ;
- `login_submit_still_loading` ;
- `unknown` seulement apres settling complet.

Metadata JSONL safe attendue :

- `screen_type`, `router_decision`, `preparation_flow_used` ;
- `screen_before_submit` ;
- `username_field_focused_before_input`, `username_clear_method`,
  `username_input_method`, `username_replaced`, `username_input_confirmed`,
  `username_input_result`, `username_input_ms` ;
- `password_field_target_kind`, `password_input_method`,
  `password_input_result`, `password_confirm_method`,
  `password_field_non_empty_confirmed` ;
- `submit_executed`, `post_submit_screens`, `final_terminal_screen` ;
- `save_password_prompt_detected`,
  `save_password_prompt_dismiss_attempt_count`,
  `save_password_prompt_dismissed`, `dismiss_method`,
  `post_dismiss_screen_type` ;
- `no_leak_summary`.

Le smoke Cas C reste local terminal, sans publish, sans dashboard action et sans
runner/social flow.

## Entry 2E-5P-16 Continue-As Expected Login Flow

Entry 2E-5P-16 valide le Cas D reel : Instagram ouvre sur
`continue_as_candidate` avec `suggested_username=expected_username`, puis le
flow tape `Continue`, observe `continue_password_only`, saisit uniquement le
password et atteint `connected_home`.

Le CLI generique supporte ce chemin sans flag special :

- `screen_type=continue_as_candidate` au demarrage stabilise ;
- `router_decision=continue_expected_account` ;
- `preparation_flow_used=continue_as_candidate` ;
- tap `Continue`, puis settling vers `continue_password_only` ;
- verification du username affiche avant tout acces credentials sur l'ecran
  password-only ;
- aucune saisie username (`username_input_result=not_required`) ;
- revelation du password uniquement via `SecretValue` / Vault apres validation
  de l'identite affichee ;
- injection password par la strategie robuste existante ;
- tap `Log in`, puis post-submit settling ;
- dismiss Google Password Manager si present sans cliquer `Continue` ;
- `would_publish=false` pendant le smoke `--no-publish`.

Securite mismatch :

- si le username affiche ne correspond pas a `expected_username` sur le
  password-only, stop safe `continue_password_only_username_mismatch` ;
- pas de reveal `SecretValue`, pas de saisie password, pas de submit ;
- si le username affiche est absent ou illisible, meme garde : pas de password
  tant que l'identite n'est pas confirmee.

Metadata JSONL safe Cas D :

- `displayed_username` ;
- `password_only_username` ;
- `username_match` ;
- `username_input_result=not_required` ;
- `username_replaced=false` ;
- champs password/post-submit habituels (`password_field_target_kind`,
  `password_input_method`, `password_input_result`, `password_confirm_method`,
  `post_submit_screens`, `final_terminal_screen`, prompt Google Password
  Manager).

Le cas ou l'app demarre **directement** sur `continue_password_only` reste une
couverture/futur smoke separe : il reutilise la meme garde `username_match`, mais
ce n'est pas le scope du checkpoint 2E-5P-16.

Un `logged_out` transitoire pendant le settling post-submit n'est pas terminal si
les observations suivantes atteignent un etat clair comme
`google_password_manager_save_prompt` puis `connected_home`.

## Entry 2E-5P-17 Active Account Home Add Existing Account Path

Entry 2E-5P-17 couvre le Cas E : Instagram ouvre sur le feed/home d'un ancien
compte encore connecte, different de `expected_username`. Ce n'est pas un succes
et ce n'est pas un compte pret.

Regle critique identite :

- `active_account_home` / `connected_home` ne declenche **jamais**
  `final_outcome=connected` via `connected_no_password_needed` tant que
  `actual_logged_in_username` (ou `active_account_username` / `profile_username`)
  n'est pas confirme et compare a `expected_username` ;
- si l'identite reste inconnue apres tentative profil : stop safe
  `identity_unknown_on_connected_home` (pas de password, pas de publish) ;
- smoke Cas E : `--operator-smoke-active-account-username` alimente
  `active_account_username` avec `active_account_lifecycle_source=operator_smoke_override`
  quand le feed ne permet pas l'extraction XML.

Garde obligatoire :

- `actual_logged_in_username != expected_username` ;
- lifecycle de l'ancien compte `canceled`, `stopped` ou `archived` ;
- `clone_reuse_allowed=true` ;
- source lifecycle explicite (`operator_smoke_override` pendant smoke, ou lookup
  safe equivalent).

Si la garde n'est pas confirmee : stop safe review, pas de tap Add account, pas
de logout, pas de reveal `SecretValue`, pas de submit.

Chemin principal Cas E :

1. `active_account_home` -> tap profile bottom nav ;
2. verifier `active_account_profile` et `actual_logged_in_username` ;
3. ouvrir l'account switcher depuis le username / chevron ;
4. tap `Add Instagram account` ;
5. settling borne post add-existing ;
6. reprendre un cas deja valide :
   `continue_as_candidate`, `account_picker`, `login_form_empty`,
   `login_form_prefilled_username` ou `continue_password_only`.

Deux chemins valides apres `Add Instagram account` (Entry 2E-5P-17B) :

- **E1 sheet intermediaire** : `add_account_sheet` -> tap `Log into existing account`
  -> settling -> cas deja couvert ;
- **E2 formulaire direct** : `login_form_empty` (ou autre cas deja couvert) sans sheet
  intermediaire. Dans ce cas `add_account_sheet_opened=false` et
  `log_into_existing_account_tapped=false` sont attendus et valides.

La sheet `Log into existing account` n'est **pas** obligatoire. Ne pas stopper avec
`add_account_sheet_not_validated` si l'ecran post-add est deja routable. Si l'ecran
reste `unknown` apres settling complet : `reason=post_add_existing_unknown`.

Distinction stricte : Cas E **n'est pas** le logout fallback. Le chemin principal
ne clique jamais `Log out`, ne passe pas par Settings logout, et ne clique pas
`Create new account` ni `Go to Accounts Center`.

Metadata safe Cas E :

- `actual_logged_in_username`, `active_account_username`,
  `account_mismatch_detected` ;
- `active_account_lifecycle_source`, `active_account_lifecycle_status`,
  `clone_reuse_allowed` ;
- `recovery_path=add_existing_account` ;
- `profile_opened`, `profile_username`, `profile_menu_initially_missing`,
  `profile_refresh_attempted` ;
- `account_switcher_opened`, `add_instagram_account_tapped`,
  `add_account_sheet_opened`, `log_into_existing_account_tapped` ;
- `post_add_existing_observation_count`, `post_add_existing_screens`,
  `screen_after_add_existing_final`.

Smoke Cas E : `--no-publish`, aucun status backend, aucun dashboard write,
aucun runner/social flow.

## Entry 2E-5P-19 Central Provisioning/Login Orchestrator

Entry 2E-5P-19 confirme que `run_login_provisioning_flow(...)` est
l'orchestrateur central provisioning/login appele par
`instagram_login_provisioner_cli.py`. Il ne cree pas de nouveau flow business :
il orchestre les sous-flows deja valides.

Responsabilites centrales :

1. `app_start` par defaut, sauf `--observe-current-screen-only` diagnostic ou
   reprise interne maitrisee post-logout ;
2. settling startup borne ;
3. classification safe de l'ecran courant ;
4. routage vers les chemins valides ;
5. reprise post-action quand Instagram affiche une transition ;
6. submit credentials uniquement quand l'identite est validee ;
7. JSONL safe, `--no-publish`, aucun runner/social/outreach hook.

Routes supportees :

- `continue_as_expected` : `continue_as_candidate` attendu -> `Continue` ;
- `use_another_profile` : ancien compte propose `canceled`/`stopped`/`archived`
  et clone reusable -> `Use another profile` ;
- `account_picker` : selection de la ligne expected sans index fixe ;
- `login_form_empty` : username + password ;
- `login_form_prefilled_expected` : password seulement, username deja correct ;
- `replace_prefilled_username` : prefilled ancien compte reusable -> remplacer
  username avant password ;
- `continue_password_only` : password-only si username == expected ;
- `already_connected_expected` : compte actif identifie comme expected ;
- `add_existing_account` : Cas E prioritaire par defaut pour ancien compte actif
  reusable ;
- `logout_fallback` : Cas F uniquement avec
  `--operator-smoke-allow-logout-fallback true`.

Stops safe centraux :

- `suggested_account_mismatch_requires_review` ;
- `username_prefilled_mismatch_requires_review` ;
- `continue_password_only_username_mismatch` ;
- `identity_unknown_on_connected_home` ;
- `expected_account_not_listed` ;
- `requires_review` pour lifecycle/source non confirme.

Metadata centrale :

- `central_orchestrator_used=true` ;
- `central_orchestrator_version=entry2e5p19-central-v1` ;
- `selected_route`, `selected_route_reason` ;
- conservation des metadata startup, route, sous-flow, password, logout et
  account-picker existantes.

Cas G/H (`login_failed`, bad password, password-required dialog, 2FA,
checkpoint) restent seulement detectes en terminal safe existant et sont
reportes apres dashboard client/admin.

## Entry 2E-5P-20 Controlled Backend Status Publish

Entry 2E-5P-20 branche le provisioner central au publisher status backend
existant, sans BotApp frontend, sans dashboard UI, sans runner hook et sans flow
business. Le helper reutilise est `instagram_account_status_publisher.py`, qui
appelle l'API interne status/RPC existante et reste injectable en tests.

Slack/Discord inchanges :

- 2E-5P-20 ne cree aucun nouveau type de notification Slack/Discord ;
- le provisioner n'envoie jamais directement vers Slack/Discord ;
- aucun webhook, token, contenu, frequence, canal ou regle d'envoi Slack/Discord
  existant n'est modifie ;
- un succes normal `connected` publie seulement un status backend si le publish
  est autorise, sans Slack/Discord.

Activation :

- `LOGIN_PROVISIONER_PUBLISH_ENABLED=false` par defaut ;
- `--publish` est obligatoire pour autoriser un publish depuis le CLI ;
- `--no-publish` a priorite absolue et force `published=false` ;
- le publisher backend reste fail-open et ne doit jamais casser le resultat local
  du login.

Mapping V1 :

- publier uniquement un succes sur : `ok=true`, `completed=true`,
  `final_outcome=connected`, `status_candidate=connected`, `account_id` present,
  route centrale sure ;
- payload status : `login_status=connected`,
  `provisioning_status=ready`, `onboarding_status=ready`,
  `reauth_required=false` selon le modele status existant ;
- metadata safe : `source=login_provisioner`, `flow_name`, `run_id`,
  `central_orchestrator_version`, `selected_route`, `final_outcome`, ecrans
  terminaux publics utiles ;
- ne pas publier maintenant : bad password, login failed, password-required
  avance, 2FA, checkpoint, mismatch/requires-review, unknown/logged-out ambigu,
  identity unknown, post-submit unknown.

Contrat miroir backend/dashboard futur :

- tout evenement operationnel deja notifie aujourd'hui vers Slack/Discord doit
  aussi avoir une trace backend safe et etre visible cote dashboard admin quand
  necessaire ;
- si l'evenement demande une action client, il doit aussi etre projetable en
  dashboard client via `account_dashboard_actions` ou sync status/action existant ;
- si l'evenement est interne ou admin-only, il reste dashboard admin only ;
- `account_incident_notifications` reste l'audit de livraison Slack/Discord, pas
  le payload UI dashboard ;
- aucun payload Slack/Discord brut, webhook, token, XML, screenshot path ou secret
  ne doit etre reutilise comme payload backend/dashboard.

Ce contrat ne deplace pas les canaux Slack/Discord dans le provisioner : le chemin
attendu reste evenement operationnel existant -> backend incident/action/status
safe -> dashboard admin -> dashboard client seulement si une action client est
necessaire.

JSONL expose :

- `would_publish`, `published`, `publish_enabled`, `publish_attempted` ;
- `publish_reason`, `publish_result`, `publish_error_code` ;
- `publish_reason=published_connected` en succes connected ;
- `publish_reason=deferred_until_dashboard` pour les cas G/H reportes ;
- `publish_reason=missing_account_id` si l'identifiant compte manque.
- avec `--no-publish`, aucun publish backend/status/action n'est tente depuis ce
  flow : `would_publish=false`, `published=false`, `publish_attempted=false`.

No-leak :

- aucun password, password length/hash, `secret_ref`, UUID Vault complet, token,
  header, cle Supabase, XML brut, screenshot path ou payload Vault brut ;
- en erreur publish, seul un code safe est conserve (`rpc_failed`,
  `publisher_exception`, `publisher_missing`, etc.) ;
- si le publish echoue apres login connecte, `final_outcome` reste `connected`,
  `ok` reste true, `published=false`, `publish_result=failed` et warning
  `publish_failed_safe`.

## Entry 2E-5P-18 Controlled Logout Fallback For Old Active Account

Entry 2E-5P-18 introduit le Cas F : un **logout fallback controle** pour un
ancien compte actif deja connecte. Ce chemin n'est jamais le chemin principal :
Cas E (`Add Instagram account` -> cas deja couvert) reste prioritaire par
defaut.

Le fallback logout n'est autorise que si toutes les conditions suivantes sont
vraies :

- `actual_logged_in_username != expected_username` ;
- lifecycle ancien compte `canceled`, `stopped` ou `archived` ;
- `clone_reuse_allowed=true` ;
- source lifecycle sure : `operator_smoke_override` ou `lifecycle_lookup_safe` ;
- autorisation explicite `--operator-smoke-allow-logout-fallback true`.

Sans flag explicite, le flow tente/privilegie toujours Cas E. Si l'identite est
inconnue, si le compte actif est deja le compte attendu, si le lifecycle/source
n'est pas fiable, ou si `clone_reuse_allowed=false`, aucun logout n'est tente.

Flow Cas F :

1. confirmer le profil de l'ancien compte actif ;
2. ouvrir le menu profil puis `Settings and activity` si necessaire ;
3. chercher `Log out` / `Deconnexion` dans `Settings and activity` ;
4. si le bouton n'est pas dans le viewport, scroller vers le bas de facon
   bornee, re-observer, puis ne taper que quand la cible texte/accessibilite est
   visible et non ambigue ;
5. si `Save your login info?` apparait : taper `Not now` (jamais `Save`) ;
6. si confirmation logout apparait : taper `Log out` (jamais `Cancel`) ;
7. settling borne post-logout (ne pas stopper sur `unknown` transitoire) ;
8. accepter `account_picker`, `continue_as_candidate`, `login_form_empty`,
   `login_form_prefilled_username` ou `continue_password_only` ;
9. reprendre le sous-flow login deja valide jusqu'a `connected` si possible.

Entry 2E-5P-18C : `Settings and activity` peut s'ouvrir en haut de page
(`Help`, `Privacy Center`, `Account Status`, `Threads`, `Facebook`, etc.) alors
que `Log out` est plus bas. Le flow doit scroller de facon bornee, sans
coordonnees fixes ni tap exploratoire, et s'arreter avec
`logout_not_visible_after_scrolls` si la cible reste absente.

Entry 2E-5P-18B : apres logout, Instagram peut re-afficher un ecran
`continue_as_candidate` pour l'**ancien** compte (avatar + `Continue` +
`Use another profile`). Ce n'est pas un echec : si `suggested_username` est
l'ancien compte actif, lifecycle `canceled`/`stopped`/`archived` et
`clone_reuse_allowed=true`, ne pas taper `Continue` — utiliser
`Use another profile`, puis reprendre les cas couverts (login form, picker,
password-only). Si `suggested_username == expected_username`, taper `Continue`
puis le flow password-only deja valide.

Metadata safe Cas F :

- `recovery_path=logout_fallback` ;
- `logout_fallback_allowed`, `logout_fallback_reason` ;
- `add_existing_attempted`, `add_existing_failed_reason` ;
- `profile_menu_opened`, `settings_opened` ;
- `logout_settings_scroll_attempted`, `logout_settings_scroll_count`,
  `logout_button_visible_before_scroll`, `logout_button_visible_after_scroll`,
  `logout_button_tapped`, `logout_button_target_text`,
  `logout_button_target_method`, `logout_not_visible_reason` ;
- `save_login_info_prompt_detected`, `save_login_info_not_now_tapped` ;
- `logout_confirmation_detected`, `logout_confirmation_tapped` ;
- `post_logout_observation_count`, `post_logout_screens`,
  `post_logout_wait_total_ms`, `screen_after_logout_final`,
  `post_logout_final_suggested_username` ;
- apres `Use another profile` : `post_use_another_profile_observation_count`,
  `post_use_another_profile_screens`, `screen_after_use_another_profile_final` ;
- `preparation_flow_used=logout_fallback_to_use_another_profile` quand le
  resume passe par cet ecran.
- `post_logout_resume_observe_only=true` : la reprise login apres logout ne
  relance pas `app_start` (observe-only interne). Les champs `app_start_*` du
  JSONL final conservent le demarrage initial du run CLI
  (`--start-app-before-probe` par defaut).

Smoke Cas F : `--no-publish`, aucun status backend, aucun dashboard write,
aucun runner/social flow. Les logs restent no-leak : pas de password,
`secret_ref`, Vault UUID, token/header, XML brut ni screenshot path.

## Entry 2E-5P-15B Login Form Empty Username Confirmation

Entry 2E-5P-15B corrige la confirmation username sur `login_form_empty` direct.

Cause racine :

- le resolver pouvait cibler un label/hint `Username, email or mobile number`
  au lieu du vrai `EditText` ;
- apres `set_text`, l'accessibilite peut conserver le placeholder ;
- l'executor comparait alors `before == after == placeholder` et retournait a tort
  `username_still_prefilled_after_input` (logique Cas A).

Correctifs :

- preferer le premier `android.widget.EditText` username sur formulaire vide ;
- ignorer les placeholders username (`Username, email or mobile number`, etc.) ;
- ne jamais retourner `username_still_prefilled_after_input` si seul un placeholder
  etait present ;
- confirmer via hierarchy EditText ou `username_input_assumed` apres `set_text`
  reussi si le placeholder reste visible ;
- conserver la logique Cas A `login_form_prefilled_username` pour un vrai ancien
  username encore present apres input.

Metadata safe ajoutee :

- `username_placeholder_ignored`.

## Entry 2E-5P-14 Account Picker Full Provisioning Flow

Entry 2E-5P-14 valide le Cas B : Instagram affiche un `account_picker` avec
plusieurs comptes visibles.

Comportement attendu :

- extraire `available_usernames`;
- verifier que `expected_username` est present;
- selectionner uniquement `expected_username`;
- ne jamais selectionner l'ancien compte visible;
- ne pas cliquer `Use another profile` dans ce chemin;
- reobserver apres tap avec settling borne.

Deux sorties normales :

1. `account_picker` -> tap expected -> `connected_home` :
   `final_outcome=connected`, `submit_executed=false`,
   `would_publish=false`.
2. `account_picker` -> tap expected -> `continue_password_only` ou formulaire
   login :
   credentials via `SecretValue` / Vault, submit password, post-submit settling,
   dismiss Google Password Manager par chemin safe si present, puis
   `final_outcome=connected`.

Metadata safe :

- `available_usernames`;
- `expected_username_present`;
- `selected_account_username`;
- `account_picker_selection_executed`;
- `post_account_picker_observation_count`;
- `post_account_picker_screens`;
- `screen_after_account_picker_final`;
- `preparation_flow_used=select_expected_account_from_picker`.

Si `expected_username` est absent du picker :

- `router_decision=expected_account_not_listed`;
- no tap, no credential, no submit;
- stop safe avec action dashboard future
  `review_account_picker_missing_expected`.

Le smoke Cas B utilise le CLI generique, sans override operateur, et reste
`--no-publish`.

## Entry 2E-5P-14B Account Picker Target Resolution (multi-compte)

Entry 2E-5P-14B corrige la resolution de cible sur `account_picker` lorsque
plusieurs nœuds accessibility (texte username, content-desc, chevron, row
cliquable) representent **une seule** row visuelle pour le meme compte.

Regles :

- le compte attendu peut etre en **premiere**, **milieu** ou **derniere** ligne
  visible ; il peut y avoir **2, 4, 6 comptes ou plus** dans le picker ;
- **ne jamais** selectionner par index fixe ni supposer que le compte attendu
  est le premier ;
- selection par **username exact** + **bounds** de la row (cluster vertical) ;
- plusieurs nœuds accessibility sur la meme row **ne sont pas** ambigus ;
- deux rows distinctes avec le meme username → stop safe
  `account_picker_ambiguous_duplicate_username_rows` ;
- `expected_username` absent du dump courant → stop safe
  `expected_account_not_listed` (router), pas de tap ni credentials ;
- ne pas cliquer `Use another profile` ni `Create new account` si le compte
  attendu est present.

Metadata safe supplementaires :

- `account_picker_target_resolution_method` ;
- `account_picker_target_row_count` ;
- `account_picker_target_node_count` ;
- `account_picker_visible_usernames_count` ;
- `account_picker_selected_row_index_if_known` (diagnostic uniquement, **pas**
  utilise pour choisir la cible) ;
- `account_picker_action_result`.

Sorties normales apres tap (inchangées Cas B) :

1. `connected_home` direct → `final_outcome=connected`, `submit_executed=false` ;
2. `continue_password_only` ou formulaire login → password Vault puis submit.

Scroll account picker : **non** implemente en V1 si le compte attendu est hors
viewport ; a documenter comme complement futur si la liste depasse l'ecran.

## Entry 2E-5P-11 Login Form Username Prefilled

Entry 2E-5P-11 traite le cas observe pendant le Cas A complet : apres
`Use another profile`, Instagram peut afficher un formulaire login avec le
username de l'ancien compte deja renseigne, au lieu d'un `login_form_empty`
strict.

Detection :

- nouveau `screen_type=login_form_prefilled_username`;
- signaux safe : `username_field_present`, `username_field_editable_present`,
  `username_prefilled_present`, `prefilled_username`,
  `password_field_present`, `login_button_present`;
- aucun XML brut, screenshot path, password, `secret_ref`, UUID Vault complet ou
  token/header dans les logs.

Routing :

- si `prefilled_username != expected_username` et le champ username est editable :
  `router_decision=start_login_form_flow_replace_username`;
- si `prefilled_username == expected_username` :
  `router_decision=start_login_form_flow_prefilled_expected`;
- si le champ username n'est pas editable :
  `final_outcome=username_prefilled_not_editable`, no submit;
- un username pre-rempli different sur un ecran login editable n'est pas un
  mismatch compte : le flow remplace le champ par `expected_username`.

Executor :

- focus username, clear, saisie `expected_username`, confirmation si le readback
  accessibilite le permet;
- le password `SecretValue` n'est revele qu'apres succes/confirmation suffisante
  de l'etape username;
- si clear/input username echoue :
  `final_outcome=username_input_failed`, aucun submit password si possible;
- post-submit settling et Google Password Manager restent ceux de 2E-5P-10.

Logs JSONL safe ajoutes :

- `screen_type`;
- `prefilled_username`;
- `username_replaced`;
- `username_input_confirmed`;
- `username_input_result`;
- `router_decision`;
- `preparation_flow_used`;
- `screen_before_submit`.

Les smokes restent `--no-publish` : aucun publish backend, aucune ecriture status
Supabase, aucun runner hook et aucun flow social/business.

Standard CLI provisioning/login :

- tout flow provisioning/login doit avoir une commande terminal reproductible
  ou un CLI dedie;
- options minimales : `--device-serial`, `--expected-username`,
  `--account-id` quand des credentials compte sont necessaires,
  `--package-name`, `--start-app-before-probe` par defaut,
  `--observe-current-screen-only` reserve diagnostic, `--dry-run` /
  `--no-submit` quand applicable, `--no-publish` par defaut et `--json`;
- sortie obligatoire : JSON safe, timings, warnings, no-leak summary,
  `would_publish=false` par defaut;
- forbidden scope : pas de hook runner, sender, follow/unfollow/outreach
  business, dashboard, migration ou status write Supabase sauf demande
  explicite.

## Entry 2E-5J-2B-1 Previous Account Lifecycle Gate Source

Entry 2E-5J-2B-1 audite la source fiable a utiliser avant d'autoriser
`Use another profile` lorsque l'ecran Instagram propose un compte different du
compte attendu.

Audit read-only :

- `client_instagram_accounts` porte les statuts dashboard-safe
  `login_status`, `provisioning_status`, `onboarding_status`, mais pas un champ
  lifecycle produit `active/paused/canceled/onboarding`;
- `client_subscription_accounts.status` existe avec
  `active/paused/removed`;
- `client_subscriptions.status` existe avec
  `active/paused/cancelled/expired`;
- `account_assignments.status` existe avec
  `pending/reserved/active/paused/failed/released`;
- `phone_clones.status` existe avec
  `available/reserved/active/maintenance/disabled`;
- `ig_accounts.status` et `ig_account_settings.account_status` existent dans le
  schema legacy/runtime, mais aucun row exploitable n'a ete trouve pour le
  `suggested_username` observe `i_m_your_traker`;
- `phone_devices.adb_serial='emulator-5554'` est `available`, avec un clone
  `reserved` pour l'account cible courant, mais cela ne prouve pas que le
  compte Instagram suggere est canceled/stopped/archived.

Conclusion audit :

- il existe des statuts utiles, mais aucune source unique actuelle ne couvre
  proprement le lifecycle du compte Instagram suggere par username;
- `i_m_your_traker` ne peut pas etre marque canceled proprement maintenant sans
  creer/mettre a jour une source de donnees explicite;
- `clone_reuse_allowed` n'est pas un fait deduit automatiquement du simple ecran
  Instagram : il doit venir d'une policy/assignment explicite;
- le gate 2E-5J-2B reste correct : sans lifecycle confirme,
  `block_wrong_suggested_account` / no tap.

Source recommandee pour la prochaine etape :

- Option B temporaire : helper lookup read-only injectable pour le smoke,
  retournant `{lifecycle_status, clone_reuse_allowed}` depuis une source
  explicite approuvee par l'operateur;
- utiliser ce helper seulement pour autoriser le tap quand
  `lifecycle_status in ('canceled','stopped','archived')` et
  `clone_reuse_allowed=true`;
- ne pas migrer tant que le mapping produit lifecycle/BotApp n'est pas valide;
- plus tard, si aucune source existante n'est retenue, prevoir une migration
  dediee au lifecycle compte / clone reuse apres validation explicite.

Patch 2E-5J-2B-1 :

- `run_login_provisioning_flow(...)` accepte maintenant
  `previous_account_lifecycle_lookup(username, context)`;
- le `username` vient toujours du `suggested_username` extrait dynamiquement;
- le `context` est metadata-only et safe : `account_id`, `expected_username`,
  `screen_type`;
- la sortie acceptee est limitee a `lifecycle_status`,
  `clone_reuse_allowed`, `source`, `reason`;
- le lookup est injectable et mockable; aucune lecture DB directe, aucune
  ecriture DB, aucune migration, aucun hook runtime;
- le patch ne fait aucun password read, aucun login et aucun tap device.

Decision :

- si `lifecycle_status in ('canceled','stopped','archived')` et
  `clone_reuse_allowed=true`, le router recoit
  `use_another_profile_previous_account_stopped` avec
  `audit_reason=previous_account_stopped_override`;
- sinon, ou si le lookup manque/echoue, le provisioner conserve
  `block_wrong_suggested_account` et l'action dashboard future
  `review_account_mismatch`;
- `source='operator_smoke_override'` peut etre exposee dans
  `safe_metadata.previous_account_lifecycle.source` pour prouver que la
  decision vient d'une validation operateur explicite;
- les champs contenant password, secret, Vault, token, XML, screenshot ou device
  brut sont vides dans la metadata publique.

Regle generique maintenue :

- `suggested_username` est extrait dynamiquement;
- aucune comparaison a `i_m_your_traker` dans la logique;
- `previous_account_lifecycle_lookup(suggested_username, context)` doit
  fonctionner pour tout handle valide;
- en absence de preuve : stop safe, `reason=lifecycle_not_confirmed`.

Procedure future `cinema_catchup` :

- l'utilisateur changera le mot de passe du compte test;
- le nouveau mot de passe sera soumis uniquement via le flow securise
  `instagram-credentials` -> Supabase Vault;
- jamais dans ChatGPT;
- jamais dans un prompt Cursor;
- jamais dans l'historique shell visible;
- jamais dans git, logs, screenshots ou XML;
- apres smoke, le password sera change a nouveau.

## Entry 2F-1 RPC incidents -> dashboard actions

Entry 2F-1 ajoute la RPC service-role
`public.sync_account_incident_dashboard_action(...)`. Elle projette un incident
ORF durable vers une action dashboard actionnable, sans brancher le runtime
Python.

Separation des sources :

- `account_incidents` reste la verite ops durable avec dedupe, lifecycle et
  `occurrence_count`;
- `account_incident_notifications` reste l'audit d'alerting Slack/Discord;
- `account_dashboard_actions` reste la projection UI actionnable;
- `client_instagram_accounts` reste la verite status dashboard safe.

Scope volontaire :

- nouvelle migration SQL uniquement;
- aucun changement `runtime_incidents.py`, `account_identity_guard.py`,
  `incident_notifications.py`, runner, sender/orchestrators, Edge Functions ou
  dashboard UI;
- aucun webhook, aucun run device, aucun deploy Edge.

Signature :

```sql
public.sync_account_incident_dashboard_action(
  p_incident_id uuid,
  p_actor_type text default 'system',
  p_reason text default null,
  p_metadata jsonb default '{}'::jsonb
) returns jsonb
```

La fonction est `SECURITY DEFINER`, fixe `search_path=public`, revoke
`public`, `anon` et `authenticated`, puis grant execute uniquement a
`service_role`.

Mapping V1 :

- `incident_type='active_instagram_account_mismatch'`;
- `action_type='review_account_mismatch'`;
- `audience='admin'`;
- `severity='critical'`;
- `requires_client_action=false`;
- `blocking_campaign=true`;
- `title='Compte Instagram incohérent'`;
- `action_label='Examiner le compte'`;
- `action_deep_link='/admin/accounts/{account_id}/identity'`.

Deduplication :

La projection incident utilise le meme `dedupe_key` que le status pipeline :

```text
account:{account_id}:dashboard_action:review_account_mismatch
```

Ainsi, si `sync_account_dashboard_actions_from_status(...)` a deja cree une
action `review_account_mismatch` via `login_status='mismatch'`, la projection
incident upsert la meme action active au lieu d'en creer une deuxieme. Elle
passe `p_incident_id` a `upsert_account_dashboard_action(...)`; si une action
active existe deja avec un autre `incident_id`, le comportement actuel de
`upsert_account_dashboard_action(...)` conserve le premier lien
`incident_id` via `coalesce(p_incident_id, ada.incident_id)` et fusionne la
metadata safe.

Lifecycle :

- incident `open` ou `acknowledged` -> upsert action active;
- incident `resolved` -> transition `resolved` uniquement si l'action est
  clairement liee par `incident_id` ou `metadata.incident_id`;
- incident `ignored` -> transition `ignored` avec le meme critere de lien;
- pour `active_instagram_account_mismatch`, si
  `client_instagram_accounts.login_status='mismatch'`, la resolution est
  ignoree avec `reason='status_still_mismatch'`.

Metadata copiee :

La RPC ne copie jamais `incident.metadata` en bloc. Elle whiteliste seulement :

- `run_id`, `stage`, `run_type`;
- `expected_account_username`;
- `actual_username` ou `actual_logged_in_username` normalise en
  `actual_username`;
- `verification_method`;
- `identity_evidence`;
- `guard_reason`;
- contexte incident safe : `incident_id`, `incident_type`, `incident_status`,
  `incident_severity`, `occurrence_count`.

Clés interdites top-level dans `p_metadata` :

```text
password, secret, secret_ref, raw_secret, token, cookie, webhook, webhook_url,
vault, service_role, authorization, bearer, adb_serial, device_udid, xml,
screenshot, session_cookie
```

Les incidents credentials/checkpoint/2FA/device/jobs restent non supportes en
V1 et retournent `unsupported_incident_type` sans creer d'action.

## Entry 2F-2 CLI reconciliation incidents -> actions

Entry 2F-2 ajoute `incident_dashboard_action_sync.py`, un CLI/job local de
reconciliation. Il selectionne les incidents `account_incidents` en
`open`/`acknowledged` puis appelle la RPC 2F-1
`sync_account_incident_dashboard_action(...)`.

Flags :

```text
INCIDENT_DASHBOARD_SYNC_ENABLED=false
INCIDENT_DASHBOARD_SYNC_LIMIT=50
INCIDENT_DASHBOARD_SYNC_FAIL_OPEN=true
```

Commandes :

```bash
python3 incident_dashboard_action_sync.py --limit 50
python3 incident_dashboard_action_sync.py --dry-run --limit 50
python3 incident_dashboard_action_sync.py --force --dry-run
```

Garanties :

- flag off -> aucun HTTP, summary `disabled`;
- `--dry-run` ne modifie pas la DB et n'appelle pas la RPC;
- `--force` sert aux executions manuelles controlees;
- aucun runtime runner/sender/orchestrator n'est appele;
- aucun webhook Slack/Discord n'est appele;
- la limite est clampée entre `1` et `200`;
- les erreurs par incident restent fail-open par defaut.

Le CLI ne copie pas de metadata incident brute. Il transmet seulement a la RPC
une metadata minimale :

```json
{
  "source": "incident_dashboard_action_sync",
  "run_id": "incident-dashboard-sync:<uuid>"
}
```

La RPC SQL garde la responsabilite du mapping, du dedupe account-level et de la
whitelist metadata. Les secrets, passwords, `secret_ref`, payload Vault, tokens,
cookies, webhooks, XML brut, screenshots et identifiants device ne doivent
jamais apparaitre dans les logs ou summaries.

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
