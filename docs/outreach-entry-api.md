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

`action` may be `submit` or `update_password`.

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
