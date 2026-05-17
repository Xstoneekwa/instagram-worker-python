-- V4.3-B — Release DM job after dry-run (pending + metadata, clear reservation).
-- Keeps job reusable for V4.4 send without touching follower memory or sent counters.

create or replace function public.release_dm_job_after_dry_run(
  p_job_id uuid,
  p_thread_state text,
  p_sendable boolean,
  p_skip_reason_candidate text default null,
  p_metadata_patch jsonb default '{}'::jsonb
)
returns public.ig_dm_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  v_job public.ig_dm_jobs;
  v_now timestamptz := now();
  v_meta jsonb;
begin
  if p_job_id is null then
    raise exception 'job_id is required';
  end if;

  select * into v_job
  from public.ig_dm_jobs
  where id = p_job_id;

  if not found then
    raise exception 'job not found: %', p_job_id;
  end if;

  if v_job.status not in ('running', 'reserved') then
    raise exception 'job % must be running or reserved to release after dry-run (status=%)',
      p_job_id, v_job.status;
  end if;

  v_meta := coalesce(v_job.metadata, '{}'::jsonb)
    || jsonb_build_object(
      'dry_run_passed_at', v_now,
      'dry_run_started_at', v_job.started_at,
      'thread_state', coalesce(nullif(trim(p_thread_state), ''), 'unknown'),
      'sendable', coalesce(p_sendable, false),
      'skip_reason_candidate', nullif(trim(coalesce(p_skip_reason_candidate, '')), '')
    )
    || coalesce(p_metadata_patch, '{}'::jsonb);

  update public.ig_dm_jobs
  set
    status = 'pending'::public.dm_job_status,
    reserved_at = null,
    reserved_by = null,
    started_at = null,
    metadata = v_meta,
    updated_at = v_now
  where id = p_job_id
  returning * into v_job;

  return v_job;
end;
$$;

comment on function public.release_dm_job_after_dry_run(uuid, text, boolean, text, jsonb) is
  'V4.3-B dry-run finalize: pending + dry_run metadata; clears reservation and started_at (audit in metadata.dry_run_started_at).';

grant execute on function public.release_dm_job_after_dry_run(uuid, text, boolean, text, jsonb) to service_role;
