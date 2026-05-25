-- ORF-3A — account-level incidents schema-only.
--
-- Durable incident lifecycle table for future account safety/reliability
-- workflows. This migration intentionally does not add Python runtime
-- integration, runtime_incidents.py, runner/sender/orchestrator hooks, Edge
-- Functions, Slack/Discord delivery, Redis, dashboard views/RPC clients,
-- account_rate_windows, credentials/provisioning, or session/phone-rest logic.

create extension if not exists pgcrypto;

-- =============================================================================
-- 1. Account incidents
-- =============================================================================
create table if not exists public.account_incidents (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  resolved_at timestamptz,

  status text not null default 'open',
  severity text not null default 'warning',
  incident_type text not null,
  dedupe_key text not null,
  occurrence_count integer not null default 1,

  client_id uuid references public.clients (id) on delete set null,
  account_id uuid references public.ig_accounts (id) on delete set null,
  account_username text,
  run_id uuid,
  assignment_id uuid references public.account_assignments (id) on delete set null,
  device_id uuid references public.phone_devices (id) on delete set null,
  clone_id uuid references public.phone_clones (id) on delete set null,
  source_event_id uuid references public.runtime_events (id) on delete set null,

  source text,
  reason text,
  failure_reason text,
  action_required text,
  safe_client_message text,
  assistant_message text,
  admin_message text,
  metadata jsonb not null default '{}'::jsonb,

  acknowledged_at timestamptz,
  acknowledged_by uuid,
  resolved_by uuid,

  constraint account_incidents_status_check
    check (status in ('open', 'acknowledged', 'resolved', 'ignored')),
  constraint account_incidents_severity_check
    check (severity in ('info', 'warning', 'error', 'critical')),
  constraint account_incidents_incident_type_nonempty
    check (char_length(trim(incident_type)) > 0),
  constraint account_incidents_dedupe_key_nonempty
    check (char_length(trim(dedupe_key)) > 0),
  constraint account_incidents_occurrence_count_positive
    check (occurrence_count >= 1),
  constraint account_incidents_metadata_object_check
    check (jsonb_typeof(metadata) = 'object')
);

create index if not exists account_incidents_account_status_severity_idx
  on public.account_incidents (account_id, status, severity)
  where account_id is not null;

create index if not exists account_incidents_client_status_idx
  on public.account_incidents (client_id, status)
  where client_id is not null;

create index if not exists account_incidents_incident_type_status_idx
  on public.account_incidents (incident_type, status);

create index if not exists account_incidents_severity_created_at_idx
  on public.account_incidents (severity, created_at desc);

create index if not exists account_incidents_last_seen_at_idx
  on public.account_incidents (last_seen_at desc);

create index if not exists account_incidents_dedupe_key_idx
  on public.account_incidents (dedupe_key);

create index if not exists account_incidents_source_event_id_idx
  on public.account_incidents (source_event_id)
  where source_event_id is not null;

create index if not exists account_incidents_run_id_idx
  on public.account_incidents (run_id)
  where run_id is not null;

create index if not exists account_incidents_assignment_id_idx
  on public.account_incidents (assignment_id)
  where assignment_id is not null;

create index if not exists account_incidents_device_id_idx
  on public.account_incidents (device_id)
  where device_id is not null;

create unique index if not exists account_incidents_active_dedupe_key
  on public.account_incidents (dedupe_key)
  where status in ('open', 'acknowledged');

drop trigger if exists account_incidents_set_updated_at on public.account_incidents;
create trigger account_incidents_set_updated_at
  before update on public.account_incidents
  for each row execute function public.set_updated_at();

comment on table public.account_incidents is
  'ORF-3A durable account-level incident lifecycle table. Service-role only in ORF-3A; safe admin/assistant/client views and RPCs are added later.';

comment on column public.account_incidents.dedupe_key is
  'Stable incident identity for active dedupe. PostgREST upsert with a partial unique index is not reliable; ORF-3B should use a SECURITY DEFINER upsert_account_incident RPC for atomic dedupe.';

comment on column public.account_incidents.run_id is
  'Nullable run correlation only. No FK in ORF-3A because ig_runs compatibility is not guaranteed across deployments.';

comment on column public.account_incidents.safe_client_message is
  'Optional sanitized client-facing summary for future safe views. Never store raw credentials, device routing details, stack traces, or service-role errors here.';

comment on column public.account_incidents.metadata is
  'Structured incident context for operators. Do not store passwords, service-role keys, credentials, tokens, cookies, raw session data, or client-visible device internals.';

-- ORF-3B should add a SECURITY DEFINER upsert_account_incident(...) RPC for
-- atomic dedupe: if an open/acknowledged incident exists, increment
-- occurrence_count and refresh last_seen_at/context; otherwise insert a new row.
-- No RPC is created in ORF-3A.

-- =============================================================================
-- 2. RLS and grants
-- =============================================================================
alter table public.account_incidents enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'account_incidents' and policyname = 'account_incidents_service_role_all'
  ) then
    create policy account_incidents_service_role_all on public.account_incidents
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;
end
$$;

revoke all on public.account_incidents from public;
revoke all on public.account_incidents from anon;
revoke all on public.account_incidents from authenticated;

grant all on public.account_incidents to service_role;
