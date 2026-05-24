-- ORF-1 — Ops Realtime Foundation schema-only.
--
-- Durable Supabase foundation for future admin/assistant dashboard realtime:
-- runtime_events, worker_heartbeats, and device_heartbeats.
--
-- This migration intentionally does not integrate the Python worker, publish
-- runtime events, create Redis/Slack/Discord helpers, add short-window rate
-- limits, provision credentials, or change Edge Functions/runtime flows.

create extension if not exists pgcrypto;

-- =============================================================================
-- 1. Append-only runtime events
-- =============================================================================
create table if not exists public.runtime_events (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  event_type text not null,
  severity text not null default 'info',
  visibility text not null default 'admin_only',
  account_id uuid,
  run_id uuid,
  assignment_id uuid,
  device_id uuid,
  clone_id uuid,
  job_id uuid,
  dm_type text,
  source text,
  reason text,
  message text,
  metadata jsonb not null default '{}'::jsonb,
  constraint runtime_events_event_type_nonempty
    check (char_length(trim(event_type)) > 0),
  constraint runtime_events_severity_check
    check (severity in ('debug', 'info', 'warning', 'error', 'critical')),
  constraint runtime_events_visibility_check
    check (visibility in ('admin_only', 'assistant_safe', 'client_safe')),
  constraint runtime_events_metadata_object_check
    check (jsonb_typeof(metadata) = 'object')
);

create index if not exists runtime_events_created_at_idx
  on public.runtime_events (created_at desc);

create index if not exists runtime_events_event_type_created_at_idx
  on public.runtime_events (event_type, created_at desc);

create index if not exists runtime_events_severity_created_at_idx
  on public.runtime_events (severity, created_at desc);

create index if not exists runtime_events_visibility_created_at_idx
  on public.runtime_events (visibility, created_at desc);

create index if not exists runtime_events_account_created_at_idx
  on public.runtime_events (account_id, created_at desc)
  where account_id is not null;

create index if not exists runtime_events_run_created_at_idx
  on public.runtime_events (run_id, created_at desc)
  where run_id is not null;

create index if not exists runtime_events_assignment_created_at_idx
  on public.runtime_events (assignment_id, created_at desc)
  where assignment_id is not null;

create index if not exists runtime_events_device_created_at_idx
  on public.runtime_events (device_id, created_at desc)
  where device_id is not null;

create index if not exists runtime_events_job_created_at_idx
  on public.runtime_events (job_id, created_at desc)
  where job_id is not null;

comment on table public.runtime_events is
  'ORF-1 append-only worker/runtime event stream for future Supabase Realtime dashboards. Client-safe reads must go through future safe views/RPCs.';

comment on column public.runtime_events.visibility is
  'admin_only, assistant_safe, or client_safe. ORF-1 grants no direct authenticated/client access.';

comment on column public.runtime_events.metadata is
  'Structured event metadata. Do not store passwords, service keys, credentials, raw stack traces for clients, adb_serial/device_udid/hub/host in client-visible events.';

-- =============================================================================
-- 2. Worker heartbeats
-- =============================================================================
create table if not exists public.worker_heartbeats (
  worker_id text primary key,
  host_machine text,
  process_id text,
  version text,
  git_sha text,
  status text not null default 'unknown',
  current_account_id uuid,
  current_run_id uuid,
  current_assignment_id uuid,
  current_device_id uuid,
  current_clone_id uuid,
  last_seen_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint worker_heartbeats_worker_id_nonempty
    check (char_length(trim(worker_id)) > 0),
  constraint worker_heartbeats_status_check
    check (status in ('starting', 'idle', 'running', 'stopping', 'offline', 'error', 'unknown')),
  constraint worker_heartbeats_metadata_object_check
    check (jsonb_typeof(metadata) = 'object')
);

create index if not exists worker_heartbeats_status_seen_idx
  on public.worker_heartbeats (status, last_seen_at desc);

create index if not exists worker_heartbeats_seen_idx
  on public.worker_heartbeats (last_seen_at desc);

create index if not exists worker_heartbeats_current_account_idx
  on public.worker_heartbeats (current_account_id)
  where current_account_id is not null;

create index if not exists worker_heartbeats_current_device_idx
  on public.worker_heartbeats (current_device_id)
  where current_device_id is not null;

create index if not exists worker_heartbeats_current_assignment_idx
  on public.worker_heartbeats (current_assignment_id)
  where current_assignment_id is not null;

drop trigger if exists worker_heartbeats_set_updated_at on public.worker_heartbeats;
create trigger worker_heartbeats_set_updated_at
  before update on public.worker_heartbeats
  for each row execute function public.set_updated_at();

comment on table public.worker_heartbeats is
  'ORF-1 worker presence table for future admin dashboards. Worker integration is added later.';

comment on column public.worker_heartbeats.worker_id is
  'Stable worker process identifier chosen by the future runtime helper, e.g. host:pid or configured WORKER_ID.';

-- =============================================================================
-- 3. Device heartbeats
-- =============================================================================
create table if not exists public.device_heartbeats (
  device_id uuid primary key references public.phone_devices (id) on delete cascade,
  adb_serial text,
  host_machine text,
  status text not null default 'unknown',
  current_account_id uuid,
  current_assignment_id uuid,
  current_clone_id uuid,
  battery_pct integer,
  last_seen_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint device_heartbeats_status_check
    check (status in ('online', 'offline', 'unauthorized', 'busy', 'maintenance', 'unknown', 'error')),
  constraint device_heartbeats_battery_pct_check
    check (battery_pct is null or (battery_pct >= 0 and battery_pct <= 100)),
  constraint device_heartbeats_metadata_object_check
    check (jsonb_typeof(metadata) = 'object'),
  constraint device_heartbeats_adb_serial_nonempty
    check (adb_serial is null or char_length(trim(adb_serial)) > 0)
);

create index if not exists device_heartbeats_status_seen_idx
  on public.device_heartbeats (status, last_seen_at desc);

create index if not exists device_heartbeats_seen_idx
  on public.device_heartbeats (last_seen_at desc);

create index if not exists device_heartbeats_current_account_idx
  on public.device_heartbeats (current_account_id)
  where current_account_id is not null;

create index if not exists device_heartbeats_current_assignment_idx
  on public.device_heartbeats (current_assignment_id)
  where current_assignment_id is not null;

create index if not exists device_heartbeats_current_clone_idx
  on public.device_heartbeats (current_clone_id)
  where current_clone_id is not null;

create index if not exists device_heartbeats_adb_serial_idx
  on public.device_heartbeats (adb_serial)
  where adb_serial is not null;

drop trigger if exists device_heartbeats_set_updated_at on public.device_heartbeats;
create trigger device_heartbeats_set_updated_at
  before update on public.device_heartbeats
  for each row execute function public.set_updated_at();

comment on table public.device_heartbeats is
  'ORF-1 device presence table for future admin dashboards. adb_serial and host_machine are ops-only and must not be exposed to clients.';

comment on column public.device_heartbeats.adb_serial is
  'Ops-only ADB routing hint. Future client-safe views must redact this field.';

-- =============================================================================
-- 4. RLS and grants
-- =============================================================================
alter table public.runtime_events enable row level security;
alter table public.worker_heartbeats enable row level security;
alter table public.device_heartbeats enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'runtime_events' and policyname = 'runtime_events_service_role_all'
  ) then
    create policy runtime_events_service_role_all on public.runtime_events
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'worker_heartbeats' and policyname = 'worker_heartbeats_service_role_all'
  ) then
    create policy worker_heartbeats_service_role_all on public.worker_heartbeats
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'device_heartbeats' and policyname = 'device_heartbeats_service_role_all'
  ) then
    create policy device_heartbeats_service_role_all on public.device_heartbeats
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;
end
$$;

grant select, insert, update, delete on public.runtime_events to service_role;
grant select, insert, update, delete on public.worker_heartbeats to service_role;
grant select, insert, update, delete on public.device_heartbeats to service_role;

revoke all on public.runtime_events from public;
revoke all on public.worker_heartbeats from public;
revoke all on public.device_heartbeats from public;

revoke all on public.runtime_events from anon;
revoke all on public.worker_heartbeats from anon;
revoke all on public.device_heartbeats from anon;

revoke all on public.runtime_events from authenticated;
revoke all on public.worker_heartbeats from authenticated;
revoke all on public.device_heartbeats from authenticated;
