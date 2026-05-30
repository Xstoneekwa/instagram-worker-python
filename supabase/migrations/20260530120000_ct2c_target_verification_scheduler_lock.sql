-- CT-2C - Expiring scheduler lock for CT target verification cron.
--
-- Internal concurrency guard only. Does not activate SearchApi production,
-- worker Python, phone runtime, cron schedules, or FBR.

create table if not exists public.ct_target_verification_scheduler_locks (
  lock_key text primary key,
  worker_id text not null,
  locked_at timestamptz not null default now(),
  expires_at timestamptz not null,
  constraint ct_target_verification_scheduler_locks_worker_id_check
    check (
      char_length(worker_id) <= 120
      and worker_id !~* '(token|secret|authorization|cookie|service_role|vault)'
    )
);

alter table public.ct_target_verification_scheduler_locks enable row level security;

drop policy if exists ct_target_verification_scheduler_locks_service_role_all
  on public.ct_target_verification_scheduler_locks;
create policy ct_target_verification_scheduler_locks_service_role_all
  on public.ct_target_verification_scheduler_locks
  for all
  using ((select auth.role()) = 'service_role')
  with check ((select auth.role()) = 'service_role');

revoke all on public.ct_target_verification_scheduler_locks from public;
revoke all on public.ct_target_verification_scheduler_locks from anon;
revoke all on public.ct_target_verification_scheduler_locks from authenticated;
grant all on public.ct_target_verification_scheduler_locks to service_role;

create or replace function public.claim_ct_target_verification_scheduler_lock(
  worker_id text default 'ct_verify_cron',
  ttl_seconds integer default 120
)
returns boolean
language plpgsql
security invoker
set search_path = public
as $$
declare
  safe_worker_id text := left(
    regexp_replace(coalesce(worker_id, 'ct_verify_cron'), '[^a-zA-Z0-9_.:-]', '_', 'g'),
    120
  );
  safe_ttl integer := least(greatest(coalesce(ttl_seconds, 120), 30), 600);
  lock_name constant text := 'ct_target_verification';
begin
  if safe_worker_id is null or safe_worker_id = '' then
    safe_worker_id := 'ct_verify_cron';
  end if;

  insert into public.ct_target_verification_scheduler_locks (
    lock_key,
    worker_id,
    locked_at,
    expires_at
  )
  values (
    lock_name,
    safe_worker_id,
    now(),
    now() - interval '1 second'
  )
  on conflict (lock_key) do nothing;

  update public.ct_target_verification_scheduler_locks
  set
    worker_id = safe_worker_id,
    locked_at = now(),
    expires_at = now() + make_interval(secs => safe_ttl)
  where lock_key = lock_name
    and (
      expires_at <= now()
      or ct_target_verification_scheduler_locks.worker_id = safe_worker_id
    );

  return found;
end;
$$;

create or replace function public.release_ct_target_verification_scheduler_lock(
  worker_id text default 'ct_verify_cron'
)
returns boolean
language plpgsql
security invoker
set search_path = public
as $$
declare
  safe_worker_id text := left(
    regexp_replace(coalesce(worker_id, 'ct_verify_cron'), '[^a-zA-Z0-9_.:-]', '_', 'g'),
    120
  );
  lock_name constant text := 'ct_target_verification';
begin
  if safe_worker_id is null or safe_worker_id = '' then
    safe_worker_id := 'ct_verify_cron';
  end if;

  update public.ct_target_verification_scheduler_locks
  set expires_at = now()
  where lock_key = lock_name
    and ct_target_verification_scheduler_locks.worker_id = safe_worker_id;

  return found;
end;
$$;

revoke all on function public.claim_ct_target_verification_scheduler_lock(text, integer) from public;
revoke all on function public.claim_ct_target_verification_scheduler_lock(text, integer) from anon;
revoke all on function public.claim_ct_target_verification_scheduler_lock(text, integer) from authenticated;
grant execute on function public.claim_ct_target_verification_scheduler_lock(text, integer) to service_role;

revoke all on function public.release_ct_target_verification_scheduler_lock(text) from public;
revoke all on function public.release_ct_target_verification_scheduler_lock(text) from anon;
revoke all on function public.release_ct_target_verification_scheduler_lock(text) from authenticated;
grant execute on function public.release_ct_target_verification_scheduler_lock(text) to service_role;

comment on table public.ct_target_verification_scheduler_locks is
  'Single-row expiring lock for CT target verification scheduler runs. Prevents overlapping cron invocations; TTL auto-expires stale locks.';

comment on function public.claim_ct_target_verification_scheduler_lock(text, integer) is
  'Claims or renews the CT verification scheduler lock when free or expired. Returns false when another active worker holds the lock.';

comment on function public.release_ct_target_verification_scheduler_lock(text) is
  'Releases the CT verification scheduler lock for the matching worker_id by expiring it immediately.';
