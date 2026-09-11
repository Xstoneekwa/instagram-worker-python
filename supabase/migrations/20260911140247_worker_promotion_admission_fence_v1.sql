-- Durable Worker promotion admission fence.
--
-- Reuses the existing phonefarm_runtime_barriers authority and advisory lock.
-- Unlike storage maintenance, promotion has no TTL: only the exact transaction
-- may release it after terminal evidence is reverified.

create table if not exists public.phonefarm_runtime_barriers (
  barrier_name text primary key,
  active boolean not null default false,
  owner_token text null,
  acquired_at timestamptz null,
  expires_at timestamptz null,
  released_at timestamptz null,
  release_reason text null,
  metadata_safe jsonb not null default '{}'::jsonb,
  generation bigint not null default 0,
  updated_at timestamptz not null default now(),
  constraint phonefarm_runtime_barriers_owner_safe check (
    owner_token is null or owner_token ~ '^[A-Za-z0-9._:-]{8,160}$'
  ),
  constraint phonefarm_runtime_barriers_release_reason_safe check (
    release_reason is null
    or (
      char_length(release_reason) between 1 and 160
      and release_reason !~* '(token|secret|authorization|cookie|service_role|vault|password)'
    )
  ),
  constraint phonefarm_runtime_barriers_metadata_object check (
    jsonb_typeof(metadata_safe) = 'object'
  )
);

alter table public.phonefarm_runtime_barriers enable row level security;
revoke all on table public.phonefarm_runtime_barriers from public, anon, authenticated;
grant all on table public.phonefarm_runtime_barriers to service_role;

create or replace function public.phonefarm_storage_admission_guard_v2()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if tg_op = 'UPDATE' then
    -- Renewing an existing, unexpired lease is maintenance of admitted work,
    -- not a new device acquisition. Every ownership/identity field must match.
    if tg_table_name = 'auto_restart_device_locks' then
      if old.lease_expires_at > clock_timestamp()
       and new.lease_expires_at >= old.lease_expires_at
       and (to_jsonb(new) - 'lease_expires_at' - 'updated_at')
           = (to_jsonb(old) - 'lease_expires_at' - 'updated_at') then
        return new;
      end if;
    end if;
    if tg_table_name = 'account_run_requests'
       and not ((to_jsonb(new)->>'status') in ('queued', 'claimed', 'starting', 'running')
                and (to_jsonb(new)->>'status') is distinct from (to_jsonb(old)->>'status')) then
      return new;
    end if;
    if tg_table_name = 'auto_restart_tick_locks'
       and not ((to_jsonb(new)->>'status') = 'started' and (to_jsonb(new)->>'status') is distinct from (to_jsonb(old)->>'status')) then
      return new;
    end if;
  end if;

  perform pg_advisory_xact_lock(785215022);

  if exists (
    select 1
    from public.phonefarm_runtime_barriers b
    where b.active
      and (
        (b.barrier_name = 'storage_apply_active' and b.expires_at > clock_timestamp())
        or b.barrier_name = 'worker_promotion_active'
      )
  ) then
    raise exception 'phonefarm_runtime_admission_fenced'
      using errcode = '55000',
            detail = 'Phone Farm work creation and claim are closed by a durable runtime transaction.';
  end if;

  return new;
end;
$$;

revoke all on function public.phonefarm_storage_admission_guard_v2()
  from public, anon, authenticated;

drop trigger if exists phonefarm_promotion_guard_account_run_request_v1
  on public.account_run_requests;
create trigger phonefarm_promotion_guard_account_run_request_v1
  before insert or update of status on public.account_run_requests
  for each row
  execute function public.phonefarm_storage_admission_guard_v2();

drop trigger if exists phonefarm_promotion_guard_ig_run_v1
  on public.ig_runs;
create trigger phonefarm_promotion_guard_ig_run_v1
  before insert on public.ig_runs
  for each row execute function public.phonefarm_storage_admission_guard_v2();

drop trigger if exists phonefarm_promotion_guard_device_lock_v1
  on public.auto_restart_device_locks;
create trigger phonefarm_promotion_guard_device_lock_v1
  before insert or update of lease_expires_at on public.auto_restart_device_locks
  for each row execute function public.phonefarm_storage_admission_guard_v2();

drop trigger if exists phonefarm_promotion_guard_tick_lock_v1
  on public.auto_restart_tick_locks;
create trigger phonefarm_promotion_guard_tick_lock_v1
  before insert or update of status on public.auto_restart_tick_locks
  for each row
  execute function public.phonefarm_storage_admission_guard_v2();

create or replace function public.acquire_phonefarm_worker_promotion_fence_v1(
  p_transaction_id text,
  p_candidate_sha text
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_now timestamptz := clock_timestamp();
  v_transaction text := nullif(trim(p_transaction_id), '');
  v_candidate text := lower(nullif(trim(p_candidate_sha), ''));
  v_existing public.phonefarm_runtime_barriers%rowtype;
  v_requests bigint;
  v_runs bigint;
  v_devices bigint;
  v_ticks bigint;
  v_generation bigint;
begin
  if v_transaction is null or v_transaction !~ '^[A-Za-z0-9._:-]{8,160}$' then
    raise exception 'invalid_worker_promotion_transaction_id' using errcode = '22023';
  end if;
  if v_candidate is null or v_candidate !~ '^[0-9a-f]{40}$' then
    raise exception 'invalid_worker_promotion_candidate_sha' using errcode = '22023';
  end if;

  perform pg_advisory_xact_lock(785215022);

  if exists (
    select 1 from public.phonefarm_runtime_barriers
    where barrier_name = 'storage_apply_active'
      and active and expires_at > v_now
  ) then
    return jsonb_build_object('ok', false, 'active', false, 'reason', 'storage_apply_barrier_held');
  end if;

  select * into v_existing
  from public.phonefarm_runtime_barriers
  where barrier_name = 'worker_promotion_active'
  for update;

  if found and v_existing.active then
    if v_existing.owner_token = v_transaction
       and v_existing.metadata_safe ->> 'candidate_sha' = v_candidate then
      return jsonb_build_object(
        'ok', true, 'active', true, 'acquired', false, 'idempotent', true,
        'persistent', true, 'transaction_id', v_transaction,
        'candidate_sha', v_candidate, 'generation', v_existing.generation
      );
    end if;
    return jsonb_build_object(
      'ok', false, 'active', true, 'reason', 'worker_promotion_fence_held',
      'generation', v_existing.generation
    );
  end if;

  select count(*) into v_requests from public.account_run_requests
    where status in ('queued', 'claimed', 'starting', 'running');
  select count(*) into v_runs from public.ig_runs
    where lower(coalesce(status, '')) in ('pending', 'running', 'queued', 'starting', 'active', 'in_progress');
  select count(*) into v_devices from public.auto_restart_device_locks
    where lease_expires_at > v_now;
  select count(*) into v_ticks from public.auto_restart_tick_locks
    where status = 'started';

  v_generation := coalesce(v_existing.generation, 0) + 1;
  insert into public.phonefarm_runtime_barriers (
    barrier_name, active, owner_token, acquired_at, expires_at,
    released_at, release_reason, metadata_safe, generation, updated_at
  ) values (
    'worker_promotion_active', true, v_transaction, v_now, 'infinity'::timestamptz,
    null, null, jsonb_build_object('candidate_sha', v_candidate, 'persistent', true),
    v_generation, v_now
  )
  on conflict (barrier_name) do update set
    active = true,
    owner_token = excluded.owner_token,
    acquired_at = excluded.acquired_at,
    expires_at = excluded.expires_at,
    released_at = null,
    release_reason = null,
    metadata_safe = excluded.metadata_safe,
    generation = excluded.generation,
    updated_at = excluded.updated_at;

  return jsonb_build_object(
    'ok', true, 'active', true, 'acquired', true, 'persistent', true,
    'transaction_id', v_transaction, 'candidate_sha', v_candidate,
    'generation', v_generation, 'active_requests', v_requests, 'active_runs', v_runs,
    'device_locks', v_devices, 'tick_locks', v_ticks
  );
end;
$$;

create or replace function public.inspect_phonefarm_worker_promotion_fence_v1(
  p_transaction_id text
)
returns jsonb
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select coalesce(
    (
      select jsonb_build_object(
        'ok', owner_token = nullif(trim(p_transaction_id), ''),
        'active', active,
        'persistent', expires_at = 'infinity'::timestamptz,
        'transaction_id', owner_token,
        'candidate_sha', metadata_safe ->> 'candidate_sha',
        'generation', generation,
        'acquired_at', acquired_at,
        'released_at', released_at,
        'terminal_state', upper(release_reason)
      )
      from public.phonefarm_runtime_barriers
      where barrier_name = 'worker_promotion_active'
    ),
    jsonb_build_object(
      'ok', false, 'active', false, 'persistent', false,
      'transaction_id', null, 'generation', 0, 'reason', 'worker_promotion_fence_missing'
    )
  );
$$;

create or replace function public.release_phonefarm_worker_promotion_fence_v1(
  p_transaction_id text,
  p_terminal_state text
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_now timestamptz := clock_timestamp();
  v_terminal text := upper(coalesce(nullif(trim(p_terminal_state), ''), ''));
  v_generation bigint;
  v_released boolean := false;
  v_idempotent boolean := false;
  v_other_active boolean := false;
begin
  if v_terminal not in ('PROMOTED', 'ROLLED_BACK') then
    raise exception 'invalid_worker_promotion_terminal_state' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(785215022);
  update public.phonefarm_runtime_barriers
  set active = false,
      released_at = v_now,
      release_reason = lower(v_terminal),
      updated_at = v_now
  where barrier_name = 'worker_promotion_active'
    and active
    and expires_at = 'infinity'::timestamptz
    and owner_token = nullif(trim(p_transaction_id), '')
  returning generation into v_generation;
  v_released := found;
  if not v_released then
    select generation into v_generation
    from public.phonefarm_runtime_barriers
    where barrier_name = 'worker_promotion_active'
      and not active
      and owner_token = nullif(trim(p_transaction_id), '')
      and release_reason = lower(v_terminal);
    v_idempotent := found;
  end if;
  if not v_released and not v_idempotent then
    select active into v_other_active
    from public.phonefarm_runtime_barriers
    where barrier_name = 'worker_promotion_active';
    v_other_active := coalesce(v_other_active, false);
  end if;
  return jsonb_build_object(
    'ok', v_released or v_idempotent,
    'active', case when v_released or v_idempotent then false else v_other_active end,
    'released', v_released,
    'idempotent', v_idempotent,
    'reason', case when v_released or v_idempotent then null else 'worker_promotion_fence_not_owned' end,
    'transaction_id', nullif(trim(p_transaction_id), ''),
    'terminal_state', v_terminal,
    'generation', v_generation
  );
end;
$$;

revoke all on function public.acquire_phonefarm_worker_promotion_fence_v1(text, text)
  from public, anon, authenticated;
revoke all on function public.inspect_phonefarm_worker_promotion_fence_v1(text)
  from public, anon, authenticated;
revoke all on function public.release_phonefarm_worker_promotion_fence_v1(text, text)
  from public, anon, authenticated;

grant execute on function public.acquire_phonefarm_worker_promotion_fence_v1(text, text)
  to service_role;
grant execute on function public.inspect_phonefarm_worker_promotion_fence_v1(text)
  to service_role;
grant execute on function public.release_phonefarm_worker_promotion_fence_v1(text, text)
  to service_role;

comment on function public.acquire_phonefarm_worker_promotion_fence_v1(text, text) is
  'Acquire a durable no-TTL Worker promotion fence before natural drain and the exact zero gate.';
