-- CT-2 - Durable target verification jobs.
--
-- Additive queue foundation only. This does not activate SearchApi in
-- production, does not touch worker Python/follow runtime, and does not
-- compute FBR/performance metrics.

create extension if not exists pgcrypto;

create table if not exists public.ct_target_verification_jobs (
  id uuid primary key default gen_random_uuid(),
  target_id uuid not null,
  account_id uuid not null,
  batch_id uuid,
  normalized_username text not null,
  status text not null default 'pending',
  attempt_count integer not null default 0,
  max_attempts integer not null default 3,
  next_attempt_at timestamptz,
  locked_at timestamptz,
  locked_by text,
  last_error_code text,
  last_error_message text,
  provider_status text,
  metadata_safe jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint ct_target_verification_jobs_target_fk
    foreign key (target_id) references public.ig_targets(id) on delete cascade,
  constraint ct_target_verification_jobs_target_unique
    unique (target_id),
  constraint ct_target_verification_jobs_status_check
    check (status in ('pending', 'processing', 'succeeded', 'failed', 'skipped', 'retry_scheduled')),
  constraint ct_target_verification_jobs_attempt_count_check
    check (attempt_count >= 0),
  constraint ct_target_verification_jobs_max_attempts_check
    check (max_attempts between 1 and 10),
  constraint ct_target_verification_jobs_normalized_username_check
    check (
      normalized_username ~ '^[a-z0-9._]{1,30}$'
      and normalized_username not like '%.'
      and normalized_username not like '.%'
      and normalized_username not like '%..%'
    ),
  constraint ct_target_verification_jobs_locked_by_check
    check (locked_by is null or (char_length(locked_by) <= 120 and locked_by !~* '(token|secret|authorization|cookie|service_role|vault)')),
  constraint ct_target_verification_jobs_error_code_check
    check (last_error_code is null or (char_length(last_error_code) <= 120 and last_error_code ~ '^[a-z0-9_:-]+$')),
  constraint ct_target_verification_jobs_error_message_check
    check (last_error_message is null or (
      char_length(last_error_message) <= 240
      and last_error_message !~* '(token|secret|authorization|cookie|service_role|vault|password|raw|html)'
    )),
  constraint ct_target_verification_jobs_provider_status_check
    check (
      provider_status is null
      or provider_status in ('pending', 'found', 'not_found', 'unavailable', 'rate_limited', 'provider_error', 'provider_not_configured', 'username_invalid')
    ),
  constraint ct_target_verification_jobs_metadata_safe_check
    check (
      jsonb_typeof(metadata_safe) = 'object'
      and not public.jsonb_has_forbidden_safe_metadata_key(metadata_safe)
    )
);

create index if not exists ct_target_verification_jobs_ready_idx
  on public.ct_target_verification_jobs (status, next_attempt_at, created_at)
  where status in ('pending', 'retry_scheduled');

create index if not exists ct_target_verification_jobs_account_status_idx
  on public.ct_target_verification_jobs (account_id, status, created_at desc);

create index if not exists ct_target_verification_jobs_batch_status_idx
  on public.ct_target_verification_jobs (batch_id, status, created_at desc)
  where batch_id is not null;

create or replace function public.set_ct_target_verification_jobs_updated_at()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists set_ct_target_verification_jobs_updated_at on public.ct_target_verification_jobs;
create trigger set_ct_target_verification_jobs_updated_at
before update on public.ct_target_verification_jobs
for each row
execute function public.set_ct_target_verification_jobs_updated_at();

alter table public.ct_target_verification_jobs enable row level security;

drop policy if exists ct_target_verification_jobs_service_role_all on public.ct_target_verification_jobs;
create policy ct_target_verification_jobs_service_role_all
  on public.ct_target_verification_jobs
  for all
  using ((select auth.role()) = 'service_role')
  with check ((select auth.role()) = 'service_role');

revoke all on public.ct_target_verification_jobs from public;
revoke all on public.ct_target_verification_jobs from anon;
revoke all on public.ct_target_verification_jobs from authenticated;
grant all on public.ct_target_verification_jobs to service_role;

create or replace function public.claim_ct_target_verification_jobs(
  batch_limit integer default 5,
  worker_id text default 'dashboard_verify_batch'
)
returns setof public.ct_target_verification_jobs
language plpgsql
security invoker
set search_path = public
as $$
declare
  safe_limit integer := least(greatest(coalesce(batch_limit, 5), 1), 10);
  safe_worker_id text := left(regexp_replace(coalesce(worker_id, 'dashboard_verify_batch'), '[^a-zA-Z0-9_.:-]', '_', 'g'), 120);
begin
  return query
  with next_jobs as (
    select j.id
    from public.ct_target_verification_jobs j
    join public.ig_targets t on t.id = j.target_id
    where j.status in ('pending', 'retry_scheduled')
      and coalesce(j.next_attempt_at, now()) <= now()
      and (j.locked_at is null or j.locked_at < now() - interval '15 minutes')
      and t.status not in ('archived', 'deleted')
      and t.archived_at is null
      and t.deleted_at is null
    order by coalesce(j.next_attempt_at, j.created_at), j.created_at
    limit safe_limit
    for update of j skip locked
  ),
  claimed as (
    update public.ct_target_verification_jobs j
    set
      status = 'processing',
      attempt_count = j.attempt_count + 1,
      locked_at = now(),
      locked_by = safe_worker_id,
      updated_at = now()
    from next_jobs
    where j.id = next_jobs.id
    returning j.*
  )
  select * from claimed;
end;
$$;

revoke all on function public.claim_ct_target_verification_jobs(integer, text) from public;
revoke all on function public.claim_ct_target_verification_jobs(integer, text) from anon;
revoke all on function public.claim_ct_target_verification_jobs(integer, text) from authenticated;
grant execute on function public.claim_ct_target_verification_jobs(integer, text) to service_role;

comment on table public.ct_target_verification_jobs is
  'Durable CT target verification jobs for bulk-imported target accounts. Stores safe status, retry and provider summary only; never raw provider responses, full URLs, headers, cookies, sessions, tokens, secret refs, Vault ids, raw HTML, XML, screenshots or logs.';

comment on function public.claim_ct_target_verification_jobs(integer, text) is
  'Atomically claims a small CT target verification batch with FOR UPDATE SKIP LOCKED. Intended for service-role dashboard/API use only.';
