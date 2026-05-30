-- CT-2B - Reclaim expired processing CT verification jobs.
--
-- Processor readiness patch only. Keeps service-role-only execution and does
-- not activate SearchApi production, cron, worker Python, phone runtime or FBR.

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
    where (
        j.status in ('pending', 'retry_scheduled')
        or (j.status = 'processing' and j.locked_at < now() - interval '15 minutes')
      )
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

comment on function public.claim_ct_target_verification_jobs(integer, text) is
  'Atomically claims a small CT target verification batch with FOR UPDATE SKIP LOCKED, including expired processing jobs. Intended for service-role dashboard/API use only.';
