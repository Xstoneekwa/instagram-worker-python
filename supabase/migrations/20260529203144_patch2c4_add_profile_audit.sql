-- Credential Secure Pipeline Patch 2C-4 - Add Profile audit events.
--
-- Durable, safe audit for Add Profile outcomes. This table is not a secret log,
-- not an incident queue, and not a lifecycle archive/trash/delete mechanism.

create extension if not exists pgcrypto;

create table if not exists public.add_profile_audit_events (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),

  account_id uuid references public.ig_accounts (id) on delete set null,
  username text not null,
  request_id text not null,
  credential_request_id text,

  source_surface text not null default 'admin_dashboard',
  operation text not null default 'add_profile',
  result_status text not null,
  failure_reason text,

  actor_type text,
  actor_id uuid,
  metadata_safe jsonb not null default '{}'::jsonb,

  constraint add_profile_audit_username_nonempty
    check (char_length(trim(username)) > 0),
  constraint add_profile_audit_request_id_nonempty
    check (char_length(trim(request_id)) > 0),
  constraint add_profile_audit_request_id_safe_length
    check (char_length(request_id) <= 120),
  constraint add_profile_audit_credential_request_id_safe_length
    check (credential_request_id is null or char_length(credential_request_id) <= 120),
  constraint add_profile_audit_source_surface_check
    check (source_surface = 'admin_dashboard'),
  constraint add_profile_audit_operation_check
    check (operation = 'add_profile'),
  constraint add_profile_audit_result_status_check
    check (result_status in ('success', 'failed', 'compensated', 'duplicate')),
  constraint add_profile_audit_actor_type_check
    check (
      actor_type is null
      or actor_type in ('admin', 'client', 'system', 'backend')
    ),
  constraint add_profile_audit_metadata_safe_check
    check (
      jsonb_typeof(metadata_safe) = 'object'
      and not public.jsonb_has_forbidden_safe_metadata_key(metadata_safe)
    )
);

create index if not exists add_profile_audit_account_created_idx
  on public.add_profile_audit_events (account_id, created_at desc)
  where account_id is not null;

create index if not exists add_profile_audit_username_created_idx
  on public.add_profile_audit_events (lower(username), created_at desc);

create index if not exists add_profile_audit_request_id_idx
  on public.add_profile_audit_events (request_id);

create index if not exists add_profile_audit_result_status_idx
  on public.add_profile_audit_events (result_status, created_at desc);

alter table public.add_profile_audit_events enable row level security;

do $$
begin
  if not exists (
    select 1
    from pg_policies
    where schemaname = 'public'
      and tablename = 'add_profile_audit_events'
      and policyname = 'add_profile_audit_events_service_role_all'
  ) then
    create policy add_profile_audit_events_service_role_all
      on public.add_profile_audit_events
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;
end
$$;

revoke all on public.add_profile_audit_events from public;
revoke all on public.add_profile_audit_events from anon;
revoke all on public.add_profile_audit_events from authenticated;

grant all on public.add_profile_audit_events to service_role;

comment on table public.add_profile_audit_events is
  'Patch 2C-4 safe Add Profile audit trail. Stores only safe outcome metadata; never passwords, tokens, Authorization headers, full secret refs, Vault payloads, cookies, sessions, service-role data, raw XML/screenshots, or raw request bodies.';

comment on column public.add_profile_audit_events.account_id is
  'Optional Add Profile account id. Uses ON DELETE SET NULL so compensated/deleted account audits remain available without preserving lifecycle state.';

comment on column public.add_profile_audit_events.request_id is
  'Safe Add Profile idempotency/correlation id truncated to 120 characters.';

comment on column public.add_profile_audit_events.credential_request_id is
  'Optional safe credential ingestion request id truncated to 120 characters.';

comment on column public.add_profile_audit_events.metadata_safe is
  'Safe metadata only. Forbidden keys include password, token, secret_ref, raw request body, raw logs/XML, screenshots, Vault payloads, cookies, sessions, and service-role data.';
