# Ops Realtime Foundation

ORF-1 creates the durable Supabase schema foundation for future realtime
operations dashboards. It is schema-only: no Python worker integration, no
event publishing, no Slack/Discord webhook, no Redis, no short-window rate
limiter, no credentials/provisioning, and no phone-rest/session guard runtime.

## ORF-1 Scope

Tables:

- `runtime_events`
  - append-only event stream for future dashboard realtime;
  - severity: `debug`, `info`, `warning`, `error`, `critical`;
  - visibility: `admin_only`, `assistant_safe`, `client_safe`;
  - service-role writes only in ORF-1;
  - no direct authenticated/client access until safe views/RPCs exist.
- `worker_heartbeats`
  - future worker presence and current runtime context;
  - statuses: `starting`, `idle`, `running`, `stopping`, `offline`, `error`,
    `unknown`;
  - service-role access only in ORF-1.
- `device_heartbeats`
  - future device presence linked to `phone_devices`;
  - statuses: `online`, `offline`, `unauthorized`, `busy`, `maintenance`,
    `unknown`, `error`;
  - `adb_serial` and `host_machine` are ops-only and must never be exposed to
    clients.

Supabase remains the durable source of truth. Supabase Realtime is the V1
delivery target for admin dashboards once worker publishing and safe views are
added.

## Not Implemented In ORF-1

- Worker event publishing.
- `runtime_events.py`, `runtime_heartbeat.py`, `runtime_incidents.py`,
  `runtime_locks.py`, or `webhook_dispatcher.py`.
- Slack/Discord delivery.
- Redis locks, Redis presence, Redis pub/sub, or Redis rate limiting.
- `account_incidents` / `account_reliability_events`.
- `account_rate_windows` or short-window limiter RPCs.
- Realtime publication configuration.
- Dashboard views/RPCs.
- Any change to sender, orchestrators, Edge Functions, credentials,
  provisioning, session 6h guard, or phone rest.

## Visibility Model

- `admin_only`: raw operations data such as device routing, worker internals,
  raw errors, and metadata that may contain sensitive implementation details.
- `assistant_safe`: operational states an assistant can act on, such as
  checkpoint, account mismatch, device offline, action block, or stuck jobs.
- `client_safe`: generic customer-facing states such as preparing, connected,
  paused, action required, completed, or quota status.

Never expose client-side:

- `adb_serial`
- `device_udid`
- `host_machine`
- `hub_label` / `hub_port`
- raw worker IDs if sensitive
- service-role details
- credentials or password material
- raw stack traces
- raw Supabase errors
- ops hard caps and internal run metadata

Client-safe reads should come later through explicit safe views/RPCs with
ownership checks, not direct table access.

## Event Taxonomy Draft

Assignment/device:

- `account_assignment_dispatch_resolved`
- `account_assignment_dispatch_missing`
- `account_assignment_dispatch_incompatible`
- `device_connected`
- `device_offline`
- `device_unauthorized`
- `clone_reserved`
- `clone_released`

Worker/run:

- `worker_started`
- `worker_heartbeat`
- `worker_stopping`
- `run_started`
- `run_completed`
- `run_failed`
- `run_aborted`

Outreach/DM:

- `outreach_session_started`
- `outreach_session_completed`
- `dm_job_claimed`
- `dm_job_sent`
- `dm_job_skipped`
- `dm_job_failed`
- `dm_no_pending_job`
- `stale_job_requeued`
- `job_stuck_detected`

Account safety:

- `account_identity_check_ok`
- `active_instagram_account_mismatch`
- `checkpoint_detected`
- `action_block_detected`
- `restriction_detected`
- `login_failed`
- `password_changed_suspected`
- `username_changed_suspected`

Reliability:

- `resume_plan_built`
- `resume_plan_blocked`
- `manual_resume_command_built`
- `restart_blocked`
- `max_restart_attempts_reached`
- `escalation_event_built`

## Runtime Events vs Incidents vs Webhooks

`runtime_events` is for live technical events and future realtime dashboards.
Examples: `run_started`, `device_connected`, assignment dispatch, job claimed,
job sent, no jobs, worker/device heartbeat.

`account_incidents` is for durable, action-oriented account incidents with
lifecycle and dedupe. Examples: account mismatch, checkpoint, action block,
restriction, login failure, device offline, device unauthorized, stuck jobs,
provisioning failure, resume blocked, and max restart attempts reached.

Slack/Discord should only be sent by a future dispatcher that reads persisted
incidents/events, applies dedupe and rate limits, and records delivery status.
Flows should not call webhooks directly.

Target pipeline:

```text
worker/logs/reliability snapshot
  -> publish_runtime_event()
  -> runtime_events
  -> account_incidents when durable/actionable
  -> admin realtime dashboard
  -> webhook_dispatcher later
```

## ORF-3A Account Incidents

ORF-3A adds `account_incidents`, a durable account-level incident table for
actionable safety and reliability states. It is schema-only: no
`runtime_incidents.py`, no Python runtime hooks, no sender/orchestrator/Edge
changes, no Slack/Discord dispatcher, no Redis, no dashboard views/RPC clients,
and no direct client access.

`runtime_events` remains the low-level realtime event stream for worker/run
activity. `account_incidents` is the slower lifecycle record for states that
need dedupe, acknowledgement, resolution, and safe messaging. A future incident
may link back to `runtime_events.id` through `source_event_id`, but incidents
can also come from scanners or reliability snapshots without a source event.

Incident lifecycle:

- `open`: active incident that needs operator/assistant attention.
- `acknowledged`: active incident seen by an operator; still deduped as active.
- `resolved`: closed incident with optional `resolved_at` / `resolved_by`.
- `ignored`: closed/no-action state for noisy or non-actionable findings.

Deduplication uses a stable `dedupe_key`, for example
`active_instagram_account_mismatch:<account_id>` or
`device_heartbeat_stale:<device_id>`. ORF-3A creates a partial unique index on
`dedupe_key` for active incidents only (`open`, `acknowledged`) so resolved or
ignored history can remain in the table. PostgREST upsert with a partial unique
index is not reliable for this pattern. ORF-3B should add a SECURITY DEFINER
`upsert_account_incident(...)` RPC that atomically:

- increments `occurrence_count` and refreshes `last_seen_at` / context when an
  active incident already exists;
- inserts a new row when no active incident exists.

Future runtime integration should live in `runtime_incidents.py` behind an
OFF-by-default flag. ORF-3C should start with
`active_instagram_account_mismatch` from the identity guard, then expand to
selected dispatch/failure states. ORF-4 should dispatch Slack/Discord from
persisted `account_incidents`, not directly from flows, so dedupe/rate limits
and delivery records are centralized.

Visibility model for incidents:

- Admin views can include raw operational context, device routing, and detailed
  failure metadata.
- Assistant-safe views should include actionable but sanitized remediation
  context.
- Client-safe views should expose only generic state and `safe_client_message`,
  with ownership checks.

There is no direct authenticated/client access to `account_incidents` in
ORF-3A. Future safe views/RPCs should decide exactly which fields are visible.
Examples of `safe_client_message` values:

- `Instagram is asking for an account security check before work can continue.`
- `The assigned device is temporarily offline; operations will resume after it is restored.`
- `We detected a login issue and need updated account access before continuing.`

Incident taxonomy draft:

- `active_instagram_account_mismatch`
- `possible_username_rename_detected`
- `account_identity_unknown`
- `login_failed`
- `checkpoint_detected`
- `two_factor_required`
- `password_changed_suspected`
- `credentials_invalid`
- `action_block_detected`
- `restriction_detected`
- `challenge_detected`
- `rate_limited_by_instagram`
- `device_offline`
- `device_unauthorized`
- `device_heartbeat_stale`
- `worker_heartbeat_stale`
- `assignment_dispatch_missing`
- `assignment_dispatch_incompatible`
- `dm_job_stuck`
- `stale_job_requeued`
- `dm_job_failed`
- `queue_backlog_high`
- `restart_blocked`
- `resume_plan_blocked`
- `max_restart_attempts_reached`
- `quota_not_reached_restart_blocked`

## ORF-3B-1 Account Incident RPC

ORF-3B-1 adds the schema-only `public.upsert_account_incident(...)` RPC. It is
`SECURITY DEFINER`, pins `search_path` to `public`, revokes `public`, `anon`,
and `authenticated`, and grants execute only to `service_role`. It does not add
`runtime_incidents.py`, Python helpers, runner integration, Edge Functions,
Slack/Discord, Redis, dashboard views, or client access.

The RPC intentionally avoids PostgREST upsert because active incident dedupe is
backed by a partial unique index:

```sql
unique (dedupe_key) where status in ('open', 'acknowledged')
```

PostgREST upsert cannot carry the full active-only conflict policy and runtime
incident lifecycle rules. The RPC uses an explicit active-row lookup with
`select ... for update`, then inserts when no active row exists. If a concurrent
writer wins the insert race, the function retries and updates the newly locked
active row.

Recurrence behavior:

- `open` incidents stay `open`.
- `acknowledged` incidents stay `acknowledged`; recurrence does not reopen or
  spam operators.
- `resolved` and `ignored` incidents are inactive, so the same `dedupe_key`
  can create a new active row.
- `occurrence_count` increments on active recurrence.
- `last_seen_at` and latest non-null context fields update on active recurrence.
- `first_seen_at`, `created_at`, `acknowledged_at`, and `resolved_at` are not
  modified during active recurrence.
- Severity uses max severity and never downgrades:
  `info < warning < error < critical`.

Metadata behavior in ORF-3B-1 is a shallow JSONB merge:

```sql
metadata = existing.metadata || incoming_metadata
```

Incoming keys replace existing keys at the top level. Runtime callers must
redact secrets before calling the RPC.

## ORF-3B-2 Runtime Incidents Helper

ORF-3B-2 adds Python helpers only. It does not change `runner.py`,
`account_identity_guard.py`, sender/orchestrators, Edge Functions, migrations,
Slack/Discord, Redis, dashboard views, or device runs.

Modules:

- `runtime_incidents.py` — `publish_account_incident(...)` and pure builders.
- `supabase_client.upsert_account_incident(payload)` — calls the ORF-3B-1 RPC;
  no PostgREST table upsert.

Flags (OFF by default):

- `RUNTIME_INCIDENTS_ENABLED=false`
- `RUNTIME_INCIDENTS_FAIL_OPEN=true`
- `RUNTIME_INCIDENTS_LOG_LOCAL_FALLBACK=true`
- `RUNTIME_INCIDENTS_INCLUDE_DEBUG_METADATA=false`

Behavior:

- When disabled, `publish_account_incident` returns
  `{"published": false, "reason": "disabled"}` without calling Supabase.
- When enabled, metadata is recursively redacted (reuses `runtime_events`
  redaction rules) before RPC.
- RPC/DB failures are fail-open: structured
  `{"published": false, "reason": "upsert_failed"}` plus a local warning log;
  no exception escapes to worker flows.
- Success returns `incident_id`, `dedupe_key`, `status`, `severity`,
  `occurrence_count`.
- Does not write `runtime_events` or send Slack/Discord.

Pure builders (no DB):

- `build_identity_mismatch_incident(...)` for
  `active_instagram_account_mismatch`.
- `build_assignment_dispatch_incident(...)` for dispatch missing/incompatible.

ORF-3C is next: integrate only identity mismatch from
`account_identity_guard.py` behind `RUNTIME_INCIDENTS_ENABLED`.

## ORF-3C Identity Mismatch Incidents

ORF-3C wires only `active_instagram_account_mismatch` from
`account_identity_guard.py` into `runtime_incidents.py`. The integration is
observation-only: it persists an incident when the logged-in Instagram username
does not match the expected account username, but it does not change the guard
result, `failure_reason`, existing logs, or the runner-owned safe-stop path.
Exit code `75` remains owned by `runner.py`.

Incident publishing stays behind `RUNTIME_INCIDENTS_ENABLED` and remains
fail-open. If the RPC/helper path fails, the identity guard still returns the
same mismatch result and the existing safe-stop behavior proceeds unchanged.

ORF-3C does not add Slack/Discord, Redis, dashboard views, Edge Functions,
migrations, device runs, or runner changes. Other account identity failures,
such as `own_profile_open_failed` or
`actual_logged_in_username_not_detected`, may become separate incident types in
a later patch; ORF-3C intentionally leaves them as logs/results only.

## ORF-4A Account Incident Notification Delivery Audit

ORF-4A adds the schema-only delivery audit table
`account_incident_notifications` for future Slack/Discord incident
notifications. The source of truth remains `account_incidents`; notification
dispatchers must consume durable incident rows, not raw logs and not runtime
flows. Runtime flows must never call Slack/Discord directly.

`account_incident_notifications` records delivery attempts and outcomes for
future dispatchers. It is service-role only in ORF-4A, with no direct
authenticated/client access and no dashboard views/RPCs. Client dashboards
should not expose notification payloads or delivery internals; future client
views should continue to use sanitized incident fields such as
`safe_client_message`.

Delivery dedupe V1:

- `delivery_key` pattern: `{channel}:{incident_id}:opened`.
- Send at most one notification per incident when it first opens.
- Ignore `ignored` and `resolved` incidents in the future dispatcher.
- Keep `acknowledged` incidents visible but do not repeat notifications in V1.
- Do not renotify on `occurrence_count` increases until cooldown support exists.

Secrets and payload safety:

- Webhook URLs are env-only in future dispatcher phases.
- Never store webhook URLs in Supabase, dashboards, logs, payloads, or metadata.
- Notification payloads must be redacted: no service-role keys, tokens,
  cookies, raw XML, raw stack traces, `device_udid`, credentials, or full
  secrets.

ORF-4A does not add Python dispatchers, `supabase_client.py` helpers,
`config.py` flags, runtime incident changes, runner/sender/orchestrator hooks,
Edge Functions, Redis, dashboard views, real webhooks, Slack/Discord sends, or
device runs.

Planned follow-ups:

- ORF-4B: add a Python dispatcher helper in dry-run mode with payload builders,
  load/query helpers, delivery recording, and tests; no real webhook by default.
- ORF-4C: enable real Slack/Discord sends only behind explicit env flags and
  `DRY_RUN=false`, with delivery tracking and fail-open behavior.
- ORF-4D: add scheduled execution through cron, Edge Function scheduling, or a
  separate supervisor; dashboard/admin controls can come later.

## ORF-4B Incident Notification Dispatcher Dry-Run

ORF-4B adds a standalone `incident_notifications.py` dispatcher in dry-run mode.
It reads actionable rows from `account_incidents`, builds safe Slack/Discord
payloads, and records what would have been sent in
`account_incident_notifications`. It does not send real webhooks, does not make
HTTP calls to Slack/Discord, and is not called by runtime flows.

Flags:

- `INCIDENT_NOTIFICATIONS_ENABLED=false`
- `INCIDENT_NOTIFICATIONS_FAIL_OPEN=true`
- `INCIDENT_NOTIFICATIONS_DRY_RUN=true`
- `INCIDENT_NOTIFICATIONS_CHANNELS=slack`
- `INCIDENT_NOTIFICATIONS_MIN_SEVERITY=warning`
- `INCIDENT_NOTIFICATIONS_MAX_PER_RUN=20`
- `INCIDENT_NOTIFICATIONS_COOLDOWN_MINUTES=60`

Selection V1:

- source table: `account_incidents`;
- statuses: `open`, `acknowledged`;
- excluded statuses: `ignored`, `resolved`;
- severity order: `info < warning < error < critical`;
- default minimum severity: `warning`;
- sort order: highest severity first, then `last_seen_at desc`;
- delivery key: `{channel}:{incident_id}:opened`;
- duplicate delivery keys are skipped.

Dry-run delivery records use:

- `status='skipped'`;
- `target='dry-run'`;
- `attempt_count=0`;
- `metadata.dry_run=true`;
- `metadata.reason='dry_run_no_webhook_sent'`;
- `metadata.dispatcher='incident_notifications'`;
- `metadata.dispatcher_version='orf-4b'`.

`status='pending'` is intentionally not used for dry-run rows, because there is
no real delivery waiting to be retried. `pending`, `sent`, and `failed` are
reserved for ORF-4C real delivery behavior.

Payload safety:

- payloads may include severity, incident type, account username, shortened
  account ID, status, occurrence count, `last_seen_at`, action/admin/assistant
  messages, `run_id`, and a dashboard URL placeholder;
- payloads must not include service-role keys, tokens, cookies, webhook URLs,
  raw XML, raw stack traces, `device_udid`, full ADB serial, credentials, or
  raw secrets.

If `INCIDENT_NOTIFICATIONS_DRY_RUN=false` in ORF-4B, the dispatcher must not send
anything. It returns `real_send_not_implemented`; real Slack/Discord delivery is
reserved for ORF-4C.

ORF-4 final completion requirement:

- a dedicated Slack or Discord incident channel exists;
- webhook URL is configured only as an environment secret;
- `INCIDENT_NOTIFICATIONS_ENABLED=true` and
  `INCIDENT_NOTIFICATIONS_DRY_RUN=false` are used for one controlled test;
- one real notification is sent;
- delivery is recorded as `sent` or `failed`;
- webhook URL is not stored or logged;
- runtime flows still never call webhooks directly.

Future admin controls should allow Slack/Discord ON/OFF toggles from the admin
web dashboard and from the local Mac backend/admin app. These toggles should
stop or resume sends per channel only; they must not delete incidents, must not
delete `account_incident_notifications` history, and must never expose webhook
URLs in Supabase, dashboards, logs, payloads, or metadata.

## ORF-4C Real Slack/Discord Incident Delivery

ORF-4C extends `incident_notifications.py` with real Slack/Discord webhook
delivery behind explicit environment configuration. Runtime flows still must not
call Slack/Discord directly; `incident_notifications.py` is the only module that
may perform webhook HTTP POSTs.

Additional environment variables:

- `SLACK_WEBHOOK_URL`
- `DISCORD_WEBHOOK_URL`
- `INCIDENT_NOTIFICATIONS_HTTP_TIMEOUT_SECONDS=10`

Webhook URLs are env-only. They must never be stored in Supabase, dashboards,
logs, notification payloads, metadata, `last_error`, or response previews. Docs
must mention only variable names, never example webhook values.

Real delivery requires:

- `INCIDENT_NOTIFICATIONS_ENABLED=true`;
- `INCIDENT_NOTIFICATIONS_DRY_RUN=false`;
- `INCIDENT_NOTIFICATIONS_CHANNELS=slack` or `discord`;
- matching webhook URL configured in the process environment.

Delivery state V1:

- duplicate `delivery_key={channel}:{incident_id}:opened` rows are skipped;
- missing webhook configuration creates a `failed` notification row without
  making an HTTP request;
- configured real sends create a `pending` row before POST, then update it to
  `sent` for any Slack/Discord 2xx response, including Discord 204;
- non-2xx responses and request exceptions update the row to `failed`;
- `attempt_count=1`, `last_attempt_at` is set before send, and successful sends
  set `delivered_at`;
- response previews and errors are redacted and truncated;
- webhook POSTs send `Content-Type: application/json` and a fixed safe
  `User-Agent: PhoneFarmIncidentNotifier/1.0 (+https://localhost)` (no secrets
  or account data in headers).

ORF-4C does not add retry loops. In V1, any existing delivery row for a key,
including `failed`, blocks another send. Retry/cooldown, manual resend, and
renotify-on-occurrence-count are ORF-4D/V2 concerns.

Controlled real smoke plan:

1. Create a dedicated incident channel, for example Slack or Discord
   `phone-farm-incidents`.
2. Configure the webhook URL only in local environment variables.
3. Set `INCIDENT_NOTIFICATIONS_ENABLED=true`,
   `INCIDENT_NOTIFICATIONS_DRY_RUN=false`, and a single channel.
4. Create a controlled open test incident.
5. Run the dispatcher manually.
6. Verify one message in the dedicated channel.
7. Verify `account_incident_notifications.status='sent'` or `failed` with safe
   delivery metadata.
8. Cleanup the test notification row and mark the smoke incident `ignored`.

Future admin controls remain out of ORF-4C minimal scope. ORF-4D/dashboard work
should add Slack and Discord ON/OFF toggles in the admin web dashboard and in
the local Mac backend/admin app. Toggles control sending only: incidents keep
being created, delivery history remains, and webhook URLs are never exposed.

## ORF-4D-1 Dispatcher CLI and Per-Channel Toggles

ORF-4D-1 adds a one-shot local dispatcher CLI and env-only Slack/Discord send
toggles. Runtime flows still must not call webhooks directly.

Additional environment variables:

- `INCIDENT_NOTIFICATIONS_SLACK_ENABLED=true`
- `INCIDENT_NOTIFICATIONS_DISCORD_ENABLED=true`

Channel model:

- `INCIDENT_NOTIFICATIONS_CHANNELS` is the global allow-list and ordering, for
  example `slack,discord`;
- per-channel enabled flags filter the allow-list independently;
- Slack ON + Discord ON sends to both listed channels;
- Slack ON + Discord OFF sends Slack only;
- Slack OFF + Discord ON sends Discord only;
- both OFF performs no webhook sends (`reason=channels_disabled`).

Disabled channels do not create `account_incident_notifications` rows in V1.
This avoids scheduler spam and prevents a `skipped` delivery row from blocking a
later send when the channel is turned back on. The dispatcher summary exposes
`skipped_channel_disabled_count` for operator visibility.

One-shot CLI:

```bash
python3 scripts/dispatch_incident_notifications.py
```

CLI behavior:

- loads repo `.env` without printing secrets;
- calls `dispatch_account_incident_notifications()` once;
- prints one JSON summary with existing redaction rules;
- exits `0` for disabled/no-op, dry-run, sent, handled failed, and fail-open
  dispatch errors;
- exits non-zero only for fatal init errors or non-fail-open dispatch failures.

Scheduling V1 (ORF-4D-2, not auto-installed):

- prefer Mac `launchd` on the worker host to run the CLI every 1-5 minutes;
- cron is an acceptable fallback;
- do not use Supabase Edge scheduled functions for webhook delivery in V1.

## ORF-4D-2 Local Scheduler Templates

ORF-4D-2 documents local scheduling only. It does not install or start a
scheduler. The scheduler triggers only the incident notification dispatcher; it
does not create incidents, does not launch `runner.py`, does not run device
automation, and does not touch Instagram flows.

Scheduled execution periodically:

- reads open/acknowledged `account_incidents`;
- sends Slack/Discord notifications according to
  `INCIDENT_NOTIFICATIONS_CHANNELS`,
  `INCIDENT_NOTIFICATIONS_SLACK_ENABLED`, and
  `INCIDENT_NOTIFICATIONS_DISCORD_ENABLED`;
- writes `account_incident_notifications` delivery history;
- skips duplicate delivery keys created by earlier runs.

Manual test before scheduling:

```bash
cd /Users/admin/instagram-worker-python
python3 scripts/dispatch_incident_notifications.py
```

The CLI loads the repo `.env` itself. Keep webhook URLs in local `.env` only.
Never put webhook URLs, service-role keys, tokens, cookies, or device secrets in
`launchd` plists, cron entries, docs, or logs.

Recommended Mac `launchd` template:

- example: `scripts/templates/com.openai.phonefarm.incident-notifications.plist.example`;
- default interval: 120 seconds;
- acceptable production range: 60-300 seconds, chosen according to incident
  volume and anti-spam needs;
- stdout: `logs/incident_notifications_dispatcher.out.log`;
- stderr: `logs/incident_notifications_dispatcher.err.log`.

Manual install example for a user LaunchAgent:

```bash
cp scripts/templates/com.openai.phonefarm.incident-notifications.plist.example \
  ~/Library/LaunchAgents/com.openai.phonefarm.incident-notifications.plist
launchctl bootstrap "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.openai.phonefarm.incident-notifications.plist
launchctl kickstart -k "gui/$(id -u)/com.openai.phonefarm.incident-notifications"
```

Manual stop/uninstall example:

```bash
launchctl bootout "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.openai.phonefarm.incident-notifications.plist
rm ~/Library/LaunchAgents/com.openai.phonefarm.incident-notifications.plist
```

Older `launchctl load` / `launchctl unload` commands may be used on older macOS
versions, but `bootstrap` / `bootout` is preferred on current macOS.

Cron fallback:

- example: `scripts/templates/incident_notifications.cron.example`;
- includes every-2-minute and every-5-minute examples;
- `launchd` remains preferred on Mac because it survives reboots and has clearer
  per-user service control.

Temporary disable options:

```bash
INCIDENT_NOTIFICATIONS_ENABLED=false
INCIDENT_NOTIFICATIONS_SLACK_ENABLED=false
INCIDENT_NOTIFICATIONS_DISCORD_ENABLED=false
```

The dispatcher logs a redacted JSON summary. Webhook URLs remain env-only and
must never appear in Supabase, dashboard settings, templates, cron, plist files,
payloads, metadata, `last_error`, response previews, or logs.

Future dashboard/admin toggles may use a non-secret `ops_settings` table and
override env flags later. Webhook URLs remain env-only.

## Entry 2F-1 Incident Dashboard Action Sync

Entry 2F-1 ajoute une projection SQL explicite entre les incidents ORF et les
actions dashboard :

```sql
public.sync_account_incident_dashboard_action(
  p_incident_id uuid,
  p_actor_type text default 'system',
  p_reason text default null,
  p_metadata jsonb default '{}'::jsonb
) returns jsonb
```

Cette etape est schema-only :

- aucun appel depuis `runtime_incidents.py`;
- aucun changement dans `account_identity_guard.py`;
- aucun changement dans `incident_notifications.py`;
- aucun runner, sender/orchestrator, Edge Function, dashboard UI, webhook ou
  run device.

Separation des responsabilites :

- `account_incidents` est la verite ops durable;
- `account_incident_notifications` est l'audit Slack/Discord;
- `account_dashboard_actions` est la projection UI actionnable;
- `client_instagram_accounts` est le status dashboard safe.

Mapping V1 :

- seul `active_instagram_account_mismatch` est supporte;
- il cree ou synchronise `review_account_mismatch`;
- l'action est `audience='admin'`, `severity='critical'`,
  `requires_client_action=false`, `blocking_campaign=true`;
- les autres incident types retournent `unsupported_incident_type`.

Deduplication avec le status pipeline :

Le mismatch peut deja creer une action via
`client_instagram_accounts.login_status='mismatch'`. La projection incident
utilise donc le meme `dedupe_key` action-level :

```text
account:{account_id}:dashboard_action:review_account_mismatch
```

Il ne faut pas utiliser un `dedupe_key` base sur `incident_id` pour ce type
d'action account-level, sinon une action status et une action incident peuvent
rester actives en double.

Lifecycle V1 :

- incident `open` / `acknowledged` -> upsert action active;
- incident `resolved` -> transition action `resolved` seulement si l'action est
  clairement liee par `incident_id` ou `metadata.incident_id`;
- incident `ignored` -> transition action `ignored` avec le meme critere;
- si le status reste `login_status='mismatch'`, la transition mismatch est
  skippee avec `status_still_mismatch`.

Metadata :

La RPC ne copie jamais `incident.metadata` en bloc. Elle whiteliste seulement le
contexte safe : `run_id`, `stage`, `run_type`, usernames attendus/detectes,
`verification_method`, `identity_evidence`, `guard_reason`, `incident_id`,
`incident_type`, `incident_status`, `incident_severity` et
`occurrence_count`.

Les cles sensibles top-level dans `p_metadata` sont rejetees : password,
`secret_ref`, payload/ref Vault, token, cookie, webhook, `service_role`,
Authorization, XML brut, screenshot, `adb_serial`, `device_udid` et session
cookies.

Suites prevues :

- 2F-2 : dispatcher/job de reconciliation des incidents ouverts;
- 2F-3 : wiring optionnel derriere flag apres validation dedupe;
- credentials/checkpoint/2FA/device/jobs restent des mappings futurs.

## ORF-2 Runtime Integration

ORF-2 adds opt-in, best-effort Python runtime helpers for low-volume worker
telemetry. It does not change worker behavior when flags are off.

Flags:

- `RUNTIME_EVENTS_ENABLED=false`
- `RUNTIME_HEARTBEATS_ENABLED=false`
- `RUNTIME_EVENTS_FAIL_OPEN=true`
- `RUNTIME_HEARTBEAT_INTERVAL_SECONDS=30`
- `RUNTIME_EVENTS_LOG_LOCAL_FALLBACK=true`
- `RUNTIME_EVENTS_INCLUDE_DEBUG=false`

Runtime events are fail-open: Supabase insert/upsert failures log a local
warning and return a structured failure result, but never crash the worker.
ORF-2 publishes `admin_only` events from `runner.py` only:

- `run_started`
- `run_completed`
- `run_failed`
- `account_assignment_dispatch_resolved`
- `account_assignment_dispatch_missing`
- `account_assignment_dispatch_incompatible`
- `device_connected`

Heartbeats are also opt-in and low-volume:

- `heartbeat_worker(status="running")` near run start
- `heartbeat_worker(status="idle" | "error")` after run status updates
- `heartbeat_device(status="busy")` after device connection only when a real
  `phone_devices.id` is known from assignment dispatch

ORF-2 intentionally does not add a dedicated heartbeat loop. Repeated heartbeat
calls are throttled by `RUNTIME_HEARTBEAT_INTERVAL_SECONDS`, with explicit
force support for start/stop transitions.

Heartbeat upserts must send `last_seen_at` explicitly in the payload. PostgREST
`resolution=merge-duplicates` updates do not reapply table defaults, so a
conflict update would otherwise keep the previous smoke-test timestamp.

Metadata is recursively redacted before persistence. Passwords, service-role
keys, tokens, cookies, credentials, sessions, and `device_udid` are removed.
`adb_serial` is converted to suffix/hash in runtime event metadata and remains
ops-only in `device_heartbeats`. Client-safe reads still require future safe
views/RPCs.

Not included in ORF-2:

- sender/orchestrator integration;
- `dm_job_*` events;
- account incidents;
- Slack/Discord or webhook dispatch;
- Redis;
- migrations or schema changes;
- quotas/settings runtime changes.

Local tests without a device:

```bash
python3 -m py_compile runtime_events.py runtime_heartbeat.py supabase_client.py config.py runner.py
python3 -m unittest tests/test_runtime_events.py tests/test_runtime_heartbeat.py
```

## Redis Readiness

Redis is optional V2 infrastructure, not ORF-1. It can later back:

- worker/device presence TTL keys;
- short-window counters with TTL;
- device/clone locks with `SET NX EX`;
- fast pub/sub fan-out.

Supabase remains the durable archive and source of truth. Future Python helpers
should use a small backend abstraction so Redis can be added without rewriting
runtime flows.

## Future Patch Plan

- ORF-2: add `runtime_events.py` and `runtime_heartbeat.py`; integrate minimal
  `runner.py` events behind flags/no-op fallbacks.
- ORF-3B/3C: add incident helpers/RPCs, then persist selected reliability
  snapshot / escalation output behind runtime flags.
- ORF-4A/4B/4C/4D: add notification delivery audit schema first, then a dry-run
  dispatcher, then real Slack/Discord behind explicit flags, then scheduled
  execution. Do not send webhooks directly from flows.
- ORF-5: add admin, assistant-safe, and client-safe views/RPCs plus Realtime
  subscription guidance.
- ORF-6: add Redis backend for locks, high-frequency heartbeats, short rate
  limits, and pub/sub.

## Retention Note

`runtime_events` can grow quickly once worker publishing is enabled. Add
retention, partitioning, or archival policy before high-volume event publishing
is enabled broadly.
