-- Run Control — account_run_requests queue + guarded RPCs.
--
-- RunControl-1: schema/RPC only. Play remains disabled until dispatcher + stop
-- integration are validated end-to-end (RunControl-5+).

create extension if not exists pgcrypto;

-- =============================================================================
-- 1. Safe metadata helper
-- =============================================================================
create or replace function public.run_control_metadata_is_safe(p_metadata jsonb)
returns boolean
language sql
immutable
set search_path = public
as $$
  select
    jsonb_typeof(coalesce(p_metadata, '{}'::jsonb)) = 'object'
    and not exists (
      select 1
      from jsonb_object_keys(coalesce(p_metadata, '{}'::jsonb)) as k(key)
      where lower(k.key) in (
        'password',
        'secret',
        'secret_ref',
        'raw_secret',
        'token',
        'cookie',
        'webhook',
        'webhook_url',
        'vault',
        'service_role',
        'authorization',
        'bearer',
        'raw_xml',
        'xml',
        'screenshot',
        'adb_serial',
        'device_udid'
      )
    );
$$;

-- =============================================================================
-- 2. Queue table
-- =============================================================================
create table if not exists public.account_run_requests (
  id uuid primary key default gen_random_uuid(),
  account_id uuid not null references public.ig_accounts (id) on delete cascade,
  requested_by uuid null,
  actor_type text not null default 'admin',
  source_surface text not null default 'instagram_dashboard',
  requested_run_type text not null,
  status text not null default 'queued',
  priority integer not null default 0,
  idempotency_key text not null,
  run_id uuid null,
  claimed_by text null,
  lease_expires_at timestamptz null,
  cancel_requested_at timestamptz null,
  cancel_reason text null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  claimed_at timestamptz null,
  started_at timestamptz null,
  completed_at timestamptz null,
  canceled_at timestamptz null,
  metadata_safe jsonb not null default '{}'::jsonb,
  error_code text null,
  error_message_safe text null,
  constraint account_run_requests_actor_type_check
    check (actor_type in ('admin', 'assistant', 'ops', 'system', 'internal')),
  constraint account_run_requests_status_check
    check (
      status in (
        'queued',
        'claimed',
        'starting',
        'running',
        'completed',
        'failed',
        'canceled',
        'blocked'
      )
    ),
  constraint account_run_requests_requested_run_type_nonempty
    check (char_length(trim(requested_run_type)) > 0),
  constraint account_run_requests_idempotency_key_nonempty
    check (char_length(trim(idempotency_key)) > 0),
  constraint account_run_requests_source_surface_safe
    check (
      char_length(trim(source_surface)) > 0
      and source_surface !~* '(token|secret|authorization|cookie|service_role|vault|password)'
    ),
  constraint account_run_requests_claimed_by_safe
    check (
      claimed_by is null
      or (
        char_length(trim(claimed_by)) > 0
        and char_length(trim(claimed_by)) <= 160
        and claimed_by !~* '(token|secret|authorization|cookie|service_role|vault|password)'
      )
    ),
  constraint account_run_requests_error_code_safe
    check (
      error_code is null
      or (
        char_length(trim(error_code)) > 0
        and char_length(trim(error_code)) <= 120
        and error_code !~* '(token|secret|authorization|cookie|service_role|vault|password)'
      )
    ),
  constraint account_run_requests_error_message_safe_check
    check (
      error_message_safe is null
      or (
        char_length(trim(error_message_safe)) > 0
        and char_length(trim(error_message_safe)) <= 500
        and error_message_safe !~* '(token|secret|authorization|cookie|service_role|vault|password)'
      )
    ),
  constraint account_run_requests_cancel_reason_safe
    check (
      cancel_reason is null
      or (
        char_length(trim(cancel_reason)) > 0
        and char_length(trim(cancel_reason)) <= 500
        and cancel_reason !~* '(token|secret|authorization|cookie|service_role|vault|password)'
      )
    ),
  constraint account_run_requests_metadata_safe_object_check
    check (jsonb_typeof(metadata_safe) = 'object'),
  constraint account_run_requests_metadata_safe_guard_check
    check (public.run_control_metadata_is_safe(metadata_safe))
);

create unique index if not exists account_run_requests_idempotency_key_key
  on public.account_run_requests (idempotency_key);

create unique index if not exists account_run_requests_one_active_per_account_key
  on public.account_run_requests (account_id)
  where status in ('queued', 'claimed', 'starting', 'running');

create index if not exists account_run_requests_claim_idx
  on public.account_run_requests (status, priority desc, created_at asc)
  where status in ('queued', 'claimed');

create index if not exists account_run_requests_account_status_created_idx
  on public.account_run_requests (account_id, status, created_at desc);

create index if not exists account_run_requests_run_id_idx
  on public.account_run_requests (run_id)
  where run_id is not null;

drop trigger if exists account_run_requests_set_updated_at on public.account_run_requests;
create trigger account_run_requests_set_updated_at
  before update on public.account_run_requests
  for each row execute function public.set_updated_at();

comment on table public.account_run_requests is
  'Run Control operator intent ledger. Consumed by supervised Python dispatcher; ig_runs remains runtime truth after runner.py starts.';

-- =============================================================================
-- 3. Active run helpers
-- =============================================================================
create or replace function public.account_has_active_ig_run(p_account_id uuid)
returns boolean
language sql
stable
set search_path = public
as $$
  select exists (
    select 1
    from public.ig_runs r
    where r.account_id = p_account_id
      and lower(coalesce(r.status, '')) in (
        'running',
        'queued',
        'pending',
        'in_progress',
        'active',
        'starting'
      )
  );
$$;

-- =============================================================================
-- 4. Create request RPC (dashboard)
-- =============================================================================
create or replace function public.create_account_run_request(
  p_account_id uuid,
  p_requested_by uuid default null,
  p_actor_type text default 'admin',
  p_source_surface text default 'instagram_dashboard',
  p_requested_run_type text default 'account_session',
  p_idempotency_key text default null,
  p_priority integer default 0,
  p_metadata_safe jsonb default '{}'::jsonb
)
returns public.account_run_requests
language plpgsql
security definer
set search_path = public
as $$
declare
  v_actor_type text := lower(coalesce(nullif(trim(p_actor_type), ''), 'admin'));
  v_source_surface text := coalesce(nullif(trim(p_source_surface), ''), 'instagram_dashboard');
  v_requested_run_type text := lower(coalesce(nullif(trim(p_requested_run_type), ''), 'account_session'));
  v_idempotency_key text := coalesce(
    nullif(trim(p_idempotency_key), ''),
    'dashboard:' || p_account_id::text || ':' || gen_random_uuid()::text
  );
  v_metadata jsonb := coalesce(p_metadata_safe, '{}'::jsonb);
  v_existing public.account_run_requests;
  v_row public.account_run_requests;
begin
  if p_account_id is null then
    raise exception 'account_id_required' using errcode = '22023';
  end if;

  if v_actor_type not in ('admin', 'assistant', 'ops', 'system', 'internal') then
    raise exception 'invalid_actor_type' using errcode = '22023';
  end if;

  if not public.run_control_metadata_is_safe(v_metadata) then
    raise exception 'metadata_forbidden_key' using errcode = '22023';
  end if;

  select *
    into v_existing
  from public.account_run_requests arr
  where arr.idempotency_key = v_idempotency_key
  limit 1;

  if found then
    return v_existing;
  end if;

  if public.account_has_active_ig_run(p_account_id) then
    raise exception 'account_already_running' using errcode = '22023';
  end if;

  if exists (
    select 1
    from public.account_run_requests arr
    where arr.account_id = p_account_id
      and arr.status in ('queued', 'claimed', 'starting', 'running')
  ) then
    raise exception 'account_run_already_requested' using errcode = '22023';
  end if;

  insert into public.account_run_requests (
    account_id,
    requested_by,
    actor_type,
    source_surface,
    requested_run_type,
    status,
    priority,
    idempotency_key,
    metadata_safe
  )
  values (
    p_account_id,
    p_requested_by,
    v_actor_type,
    v_source_surface,
    v_requested_run_type,
    'queued',
    coalesce(p_priority, 0),
    v_idempotency_key,
    v_metadata
  )
  returning * into v_row;

  return v_row;
end;
$$;

-- =============================================================================
-- 5. Claim RPC (dispatcher)
-- =============================================================================
create or replace function public.claim_next_account_run_request(
  p_worker_id text,
  p_lease_seconds integer default 120,
  p_allowed_run_types text[] default null
)
returns public.account_run_requests
language plpgsql
security definer
set search_path = public
as $$
declare
  v_worker_id text := left(
    regexp_replace(coalesce(nullif(trim(p_worker_id), ''), 'run-dispatcher'), '[^a-zA-Z0-9_.:-]', '_', 'g'),
    160
  );
  v_lease_seconds integer := least(greatest(coalesce(p_lease_seconds, 120), 30), 3600);
  v_request_id uuid;
  v_row public.account_run_requests;
begin
  if v_worker_id is null or v_worker_id = '' then
    raise exception 'worker_id_required' using errcode = '22023';
  end if;

  perform public.reclaim_stale_account_run_requests(v_worker_id);

  select arr.id
    into v_request_id
  from public.account_run_requests arr
  where arr.status = 'queued'
    and arr.cancel_requested_at is null
    and (
      p_allowed_run_types is null
      or arr.requested_run_type = any (p_allowed_run_types)
    )
    and not public.account_has_active_ig_run(arr.account_id)
  order by arr.priority desc, arr.created_at asc
  for update skip locked
  limit 1;

  if v_request_id is null then
    return null;
  end if;

  update public.account_run_requests arr
  set
    status = 'claimed',
    claimed_by = v_worker_id,
    claimed_at = now(),
    lease_expires_at = now() + make_interval(secs => v_lease_seconds),
    updated_at = now()
  where arr.id = v_request_id
    and arr.status = 'queued'
    and arr.cancel_requested_at is null
  returning * into v_row;

  return v_row;
end;
$$;

-- =============================================================================
-- 6. Transition RPCs
-- =============================================================================
create or replace function public.mark_account_run_request_starting(
  p_request_id uuid,
  p_worker_id text
)
returns public.account_run_requests
language plpgsql
security definer
set search_path = public
as $$
declare
  v_worker_id text := left(
    regexp_replace(coalesce(nullif(trim(p_worker_id), ''), 'run-dispatcher'), '[^a-zA-Z0-9_.:-]', '_', 'g'),
    160
  );
  v_row public.account_run_requests;
begin
  update public.account_run_requests arr
  set
    status = 'starting',
    lease_expires_at = greatest(
      coalesce(arr.lease_expires_at, now()),
      now() + interval '2 minutes'
    ),
    updated_at = now()
  where arr.id = p_request_id
    and arr.status = 'claimed'
    and arr.claimed_by = v_worker_id
    and (arr.lease_expires_at is null or arr.lease_expires_at > now())
    and arr.cancel_requested_at is null
  returning * into v_row;

  return v_row;
end;
$$;

create or replace function public.link_account_run_request_run(
  p_request_id uuid,
  p_worker_id text,
  p_run_id uuid
)
returns public.account_run_requests
language plpgsql
security definer
set search_path = public
as $$
declare
  v_worker_id text := left(
    regexp_replace(coalesce(nullif(trim(p_worker_id), ''), 'run-dispatcher'), '[^a-zA-Z0-9_.:-]', '_', 'g'),
    160
  );
  v_row public.account_run_requests;
begin
  if p_run_id is null then
    raise exception 'run_id_required' using errcode = '22023';
  end if;

  update public.account_run_requests arr
  set
    status = 'running',
    run_id = p_run_id,
    started_at = coalesce(arr.started_at, now()),
    lease_expires_at = greatest(
      coalesce(arr.lease_expires_at, now()),
      now() + interval '5 minutes'
    ),
    updated_at = now()
  where arr.id = p_request_id
    and arr.status in ('claimed', 'starting')
    and arr.claimed_by = v_worker_id
  returning * into v_row;

  return v_row;
end;
$$;

create or replace function public.complete_account_run_request(
  p_request_id uuid,
  p_worker_id text,
  p_status text,
  p_error_code text default null,
  p_error_message_safe text default null
)
returns public.account_run_requests
language plpgsql
security definer
set search_path = public
as $$
declare
  v_worker_id text := left(
    regexp_replace(coalesce(nullif(trim(p_worker_id), ''), 'run-dispatcher'), '[^a-zA-Z0-9_.:-]', '_', 'g'),
    160
  );
  v_status text := lower(coalesce(nullif(trim(p_status), ''), ''));
  v_row public.account_run_requests;
begin
  if v_status not in ('completed', 'failed', 'blocked', 'canceled') then
    raise exception 'invalid_terminal_status' using errcode = '22023';
  end if;

  update public.account_run_requests arr
  set
    status = v_status,
    completed_at = case when v_status in ('completed', 'failed', 'blocked') then now() else arr.completed_at end,
    canceled_at = case when v_status = 'canceled' then now() else arr.canceled_at end,
    error_code = nullif(trim(coalesce(p_error_code, '')), ''),
    error_message_safe = nullif(trim(coalesce(p_error_message_safe, '')), ''),
    lease_expires_at = null,
    updated_at = now()
  where arr.id = p_request_id
    and arr.claimed_by = v_worker_id
    and arr.status in ('claimed', 'starting', 'running')
  returning * into v_row;

  return v_row;
end;
$$;

create or replace function public.cancel_account_run_request(
  p_request_id uuid default null,
  p_account_id uuid default null,
  p_actor_id uuid default null,
  p_reason text default 'manual_stop'
)
returns public.account_run_requests
language plpgsql
security definer
set search_path = public
as $$
declare
  v_reason text := left(
    coalesce(nullif(trim(p_reason), ''), 'manual_stop'),
    500
  );
  v_target_id uuid;
  v_row public.account_run_requests;
begin
  if p_request_id is null and p_account_id is null then
    raise exception 'request_or_account_required' using errcode = '22023';
  end if;

  if p_request_id is not null then
    v_target_id := p_request_id;
  else
    select arr.id
      into v_target_id
    from public.account_run_requests arr
    where arr.account_id = p_account_id
      and arr.status in ('queued', 'claimed', 'starting', 'running')
    order by arr.created_at desc
    limit 1;
  end if;

  if v_target_id is null then
    return null;
  end if;

  update public.account_run_requests arr
  set
    cancel_requested_at = coalesce(arr.cancel_requested_at, now()),
    cancel_reason = coalesce(arr.cancel_reason, v_reason),
    status = case
      when arr.status in ('queued', 'claimed', 'starting') then 'canceled'
      else arr.status
    end,
    canceled_at = case
      when arr.status in ('queued', 'claimed', 'starting') then coalesce(arr.canceled_at, now())
      else arr.canceled_at
    end,
    lease_expires_at = case
      when arr.status in ('queued', 'claimed', 'starting') then null
      else arr.lease_expires_at
    end,
    updated_at = now()
  where arr.id = v_target_id
    and arr.status in ('queued', 'claimed', 'starting', 'running')
  returning * into v_row;

  return v_row;
end;
$$;

create or replace function public.reclaim_stale_account_run_requests(
  p_worker_id text default null
)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_count integer := 0;
  v_reclaimed integer := 0;
begin
  update public.account_run_requests arr
  set
    status = 'queued',
    claimed_by = null,
    claimed_at = null,
    lease_expires_at = null,
    updated_at = now()
  where arr.status in ('claimed', 'starting')
    and arr.run_id is null
    and arr.lease_expires_at is not null
    and arr.lease_expires_at <= now()
    and arr.cancel_requested_at is null;

  get diagnostics v_reclaimed = row_count;
  v_count := v_count + v_reclaimed;

  update public.account_run_requests arr
  set
    status = 'failed',
    completed_at = now(),
    error_code = 'stale_claim_timeout',
    error_message_safe = 'Dispatcher claim lease expired before run linked.',
    lease_expires_at = null,
    updated_at = now()
  where arr.status = 'running'
    and arr.run_id is null
    and arr.lease_expires_at is not null
    and arr.lease_expires_at <= now()
    and arr.cancel_requested_at is null;

  get diagnostics v_reclaimed = row_count;
  v_count := v_count + v_reclaimed;

  return v_count;
end;
$$;

create or replace function public.is_account_run_request_cancel_requested(
  p_request_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.account_run_requests arr
    where arr.id = p_request_id
      and (
        arr.cancel_requested_at is not null
        or arr.status = 'canceled'
      )
  );
$$;

-- =============================================================================
-- 7. RLS and grants
-- =============================================================================
alter table public.account_run_requests enable row level security;

drop policy if exists account_run_requests_service_role_all on public.account_run_requests;
create policy account_run_requests_service_role_all
  on public.account_run_requests
  for all
  using ((select auth.role()) = 'service_role')
  with check ((select auth.role()) = 'service_role');

revoke all on public.account_run_requests from public;
revoke all on public.account_run_requests from anon;
revoke all on public.account_run_requests from authenticated;
grant all on public.account_run_requests to service_role;

revoke all on function public.run_control_metadata_is_safe(jsonb) from public;
grant execute on function public.run_control_metadata_is_safe(jsonb) to service_role;

revoke all on function public.account_has_active_ig_run(uuid) from public;
grant execute on function public.account_has_active_ig_run(uuid) to service_role;

revoke all on function public.create_account_run_request(uuid, uuid, text, text, text, text, integer, jsonb) from public;
revoke all on function public.create_account_run_request(uuid, uuid, text, text, text, text, integer, jsonb) from anon;
revoke all on function public.create_account_run_request(uuid, uuid, text, text, text, text, integer, jsonb) from authenticated;
grant execute on function public.create_account_run_request(uuid, uuid, text, text, text, text, integer, jsonb) to service_role;

revoke all on function public.claim_next_account_run_request(text, integer, text[]) from public;
revoke all on function public.claim_next_account_run_request(text, integer, text[]) from anon;
revoke all on function public.claim_next_account_run_request(text, integer, text[]) from authenticated;
grant execute on function public.claim_next_account_run_request(text, integer, text[]) to service_role;

revoke all on function public.mark_account_run_request_starting(uuid, text) from public;
revoke all on function public.mark_account_run_request_starting(uuid, text) from anon;
revoke all on function public.mark_account_run_request_starting(uuid, text) from authenticated;
grant execute on function public.mark_account_run_request_starting(uuid, text) to service_role;

revoke all on function public.link_account_run_request_run(uuid, text, uuid) from public;
revoke all on function public.link_account_run_request_run(uuid, text, uuid) from anon;
revoke all on function public.link_account_run_request_run(uuid, text, uuid) from authenticated;
grant execute on function public.link_account_run_request_run(uuid, text, uuid) to service_role;

revoke all on function public.complete_account_run_request(uuid, text, text, text, text) from public;
revoke all on function public.complete_account_run_request(uuid, text, text, text, text) from anon;
revoke all on function public.complete_account_run_request(uuid, text, text, text, text) from authenticated;
grant execute on function public.complete_account_run_request(uuid, text, text, text, text) to service_role;

revoke all on function public.cancel_account_run_request(uuid, uuid, uuid, text) from public;
revoke all on function public.cancel_account_run_request(uuid, uuid, uuid, text) from anon;
revoke all on function public.cancel_account_run_request(uuid, uuid, uuid, text) from authenticated;
grant execute on function public.cancel_account_run_request(uuid, uuid, uuid, text) to service_role;

revoke all on function public.reclaim_stale_account_run_requests(text) from public;
revoke all on function public.reclaim_stale_account_run_requests(text) from anon;
revoke all on function public.reclaim_stale_account_run_requests(text) from authenticated;
grant execute on function public.reclaim_stale_account_run_requests(text) to service_role;

revoke all on function public.is_account_run_request_cancel_requested(uuid) from public;
revoke all on function public.is_account_run_request_cancel_requested(uuid) from anon;
revoke all on function public.is_account_run_request_cancel_requested(uuid) from authenticated;
grant execute on function public.is_account_run_request_cancel_requested(uuid) to service_role;

comment on function public.create_account_run_request(uuid, uuid, text, text, text, text, integer, jsonb) is
  'Create a guarded manual run request. Idempotent on idempotency_key. Fails if account already running or active request exists.';

comment on function public.claim_next_account_run_request(text, integer, text[]) is
  'Atomically claim the next queued run request using FOR UPDATE SKIP LOCKED. Reclaims stale leases first.';

comment on function public.cancel_account_run_request(uuid, uuid, uuid, text) is
  'Cancel queued/claimed/starting requests immediately; running requests get cancel_requested_at for cooperative stop.';
