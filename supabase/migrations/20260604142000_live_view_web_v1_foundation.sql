-- Live Phone View Web V1 foundation: session model + audit events.
-- Admin/operator only via service_role APIs. No direct client table access.

create extension if not exists pgcrypto;

-- =============================================================================
-- 1. Sessions
-- =============================================================================
create table if not exists public.live_view_sessions (
  id uuid primary key default gen_random_uuid(),
  account_id uuid not null references public.ig_accounts (id) on delete cascade,
  device_id uuid not null references public.phone_devices (id) on delete restrict,
  app_instance_id uuid not null references public.phone_app_instances (id) on delete restrict,
  host_id text not null,
  requested_by text not null,
  source text not null default 'manager_row_eye',
  mode text not null default 'view_only',
  status text not null default 'pending',
  stream_transport text not null default 'webrtc',
  livekit_room_name text,
  run_active_at_start boolean not null default false,
  interaction_enabled boolean not null default false,
  failure_reason text,
  metadata_safe jsonb not null default '{}'::jsonb,
  started_at timestamptz,
  stopped_at timestamptz,
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint live_view_sessions_mode_check
    check (mode in ('view_only', 'interactive')),
  constraint live_view_sessions_status_check
    check (status in ('pending', 'starting', 'active', 'stopped', 'failed', 'expired')),
  constraint live_view_sessions_stream_transport_check
    check (stream_transport in ('webrtc', 'mjpeg', 'screenshot_polling')),
  constraint live_view_sessions_source_nonempty
    check (char_length(trim(source)) > 0),
  constraint live_view_sessions_host_id_nonempty
    check (char_length(trim(host_id)) > 0),
  constraint live_view_sessions_requested_by_nonempty
    check (char_length(trim(requested_by)) > 0),
  constraint live_view_sessions_metadata_safe_object_check
    check (jsonb_typeof(metadata_safe) = 'object'),
  constraint live_view_sessions_metadata_safe_guard_check
    check (public.run_control_metadata_is_safe(metadata_safe)),
  constraint live_view_sessions_expires_after_created_check
    check (expires_at > created_at)
);

create unique index if not exists live_view_sessions_one_active_per_app_instance_key
  on public.live_view_sessions (app_instance_id)
  where status in ('pending', 'starting', 'active');

create index if not exists live_view_sessions_account_status_created_idx
  on public.live_view_sessions (account_id, status, created_at desc);

create index if not exists live_view_sessions_host_status_created_idx
  on public.live_view_sessions (host_id, status, created_at desc);

create index if not exists live_view_sessions_expires_at_idx
  on public.live_view_sessions (expires_at)
  where status in ('pending', 'starting', 'active');

comment on table public.live_view_sessions is
  'Admin-only remote live phone view sessions. Device routing stays server-side; never expose adb_serial to web clients.';

comment on column public.live_view_sessions.host_id is
  'Ops host identifier, typically phone_devices.host_machine. Used by outbound farm agents.';

comment on column public.live_view_sessions.metadata_safe is
  'Sanitized session metadata only. Must pass run_control_metadata_is_safe.';

drop trigger if exists live_view_sessions_set_updated_at on public.live_view_sessions;
create trigger live_view_sessions_set_updated_at
  before update on public.live_view_sessions
  for each row execute function public.set_updated_at();

-- =============================================================================
-- 2. Audit events
-- =============================================================================
create table if not exists public.live_view_audit_events (
  id uuid primary key default gen_random_uuid(),
  session_id uuid references public.live_view_sessions (id) on delete set null,
  event_type text not null,
  actor_id text,
  account_id uuid references public.ig_accounts (id) on delete set null,
  device_id uuid references public.phone_devices (id) on delete set null,
  app_instance_id uuid references public.phone_app_instances (id) on delete set null,
  action text not null,
  metadata_safe jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint live_view_audit_events_action_check
    check (action in (
      'start',
      'stop',
      'status',
      'token',
      'agent_starting',
      'agent_active',
      'agent_failed',
      'agent_stopped',
      'tap',
      'scroll',
      'drag',
      'back',
      'home',
      'text_input',
      'override'
    )),
  constraint live_view_audit_events_event_type_nonempty
    check (char_length(trim(event_type)) > 0),
  constraint live_view_audit_events_metadata_safe_object_check
    check (jsonb_typeof(metadata_safe) = 'object'),
  constraint live_view_audit_events_metadata_safe_guard_check
    check (public.run_control_metadata_is_safe(metadata_safe))
);

create index if not exists live_view_audit_events_session_created_idx
  on public.live_view_audit_events (session_id, created_at desc);

create index if not exists live_view_audit_events_account_created_idx
  on public.live_view_audit_events (account_id, created_at desc);

comment on table public.live_view_audit_events is
  'Audit trail for admin live view lifecycle and future control events.';

-- =============================================================================
-- 3. RLS and grants
-- =============================================================================
alter table public.live_view_sessions enable row level security;
alter table public.live_view_audit_events enable row level security;

drop policy if exists live_view_sessions_service_role_all on public.live_view_sessions;
create policy live_view_sessions_service_role_all
  on public.live_view_sessions
  for all
  using ((select auth.role()) = 'service_role')
  with check ((select auth.role()) = 'service_role');

drop policy if exists live_view_audit_events_service_role_all on public.live_view_audit_events;
create policy live_view_audit_events_service_role_all
  on public.live_view_audit_events
  for all
  using ((select auth.role()) = 'service_role')
  with check ((select auth.role()) = 'service_role');

revoke all on public.live_view_sessions from public;
revoke all on public.live_view_sessions from anon;
revoke all on public.live_view_sessions from authenticated;
grant all on public.live_view_sessions to service_role;

revoke all on public.live_view_audit_events from public;
revoke all on public.live_view_audit_events from anon;
revoke all on public.live_view_audit_events from authenticated;
grant all on public.live_view_audit_events to service_role;
