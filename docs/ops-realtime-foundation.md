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
- ORF-4: add Slack/Discord dispatcher with dedupe, rate limits, and delivery
  status. Do not send webhooks directly from flows.
- ORF-5: add admin, assistant-safe, and client-safe views/RPCs plus Realtime
  subscription guidance.
- ORF-6: add Redis backend for locks, high-frequency heartbeats, short rate
  limits, and pub/sub.

## Retention Note

`runtime_events` can grow quickly once worker publishing is enabled. Add
retention, partitioning, or archival policy before high-volume event publishing
is enabled broadly.
