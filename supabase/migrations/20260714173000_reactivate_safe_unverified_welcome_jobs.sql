-- Reactivate only Welcome jobs that were cancelled as unused scan overflow.
-- Sent, attempted, reserved, failed, and otherwise skipped jobs remain terminal.

create or replace function public.enqueue_welcome_dm_job_if_eligible(
  p_account_id uuid,
  p_follower_username text,
  p_source_scan_run_id uuid default null,
  p_message_body text default null,
  p_template_id uuid default null,
  p_priority integer default 10
)
returns public.ig_dm_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  v_username text := trim(p_follower_username);
  v_follower public.ig_account_followers;
  v_settings public.ig_account_dm_settings;
  v_job public.ig_dm_jobs;
  v_key text;
  v_body text;
begin
  if v_username is null or char_length(v_username) = 0 then
    raise exception 'follower_username must be non-empty';
  end if;

  select * into v_settings
  from public.ig_account_dm_settings
  where account_id = p_account_id;

  if not found or v_settings.welcome_enabled is distinct from true then
    return null;
  end if;

  if v_settings.welcome_baseline_completed_at is null then
    return null;
  end if;

  select * into v_follower
  from public.ig_account_followers
  where account_id = p_account_id
    and follower_username_normalized = public.normalize_ig_username(v_username);

  if not found then
    v_follower := public.upsert_account_follower_seen(
      p_account_id,
      v_username,
      p_source_scan_run_id,
      false
    );
  end if;

  if v_follower.baseline_existing
     or v_follower.welcome_dm_status in (
       'not_eligible_baseline', 'sent', 'reserved', 'skipped', 'failed'
     ) then
    return null;
  end if;

  v_key := public.dm_job_idempotency_key_welcome(p_account_id, v_username);

  select * into v_job
  from public.ig_dm_jobs
  where idempotency_key = v_key
  for update;

  if found then
    if v_job.status = 'cancelled'
       and v_job.skip_reason = 'cleanup_full_cycle_minimum_extra_welcome_jobs'
       and v_job.sent_at is null
       and coalesce(v_job.attempts, 0) = 0
       and v_follower.welcome_dm_status = 'pending' then
      update public.ig_dm_jobs
      set status = 'pending',
          skip_reason = null,
          next_retry_at = null,
          reserved_at = null,
          reserved_by = null,
          started_at = null,
          finished_at = null,
          last_error = null,
          metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object(
            'reactivated_at', now(),
            'reactivation_reason', 'safe_unverified_scan_overflow_job',
            'previous_status', v_job.status,
            'previous_skip_reason', v_job.skip_reason,
            'source_scan_run_id', p_source_scan_run_id
          ),
          updated_at = now()
      where id = v_job.id
      returning * into v_job;
      return v_job;
    end if;

    if v_job.status in ('sent', 'skipped', 'failed', 'cancelled') then
      return null;
    end if;
    return v_job;
  end if;

  v_body := nullif(trim(coalesce(p_message_body, '')), '');
  if v_body is null then
    if p_template_id is not null then
      select t.body into v_body
      from public.ig_dm_templates t
      where t.id = p_template_id
        and t.account_id = p_account_id
        and t.active = true;
    elsif v_settings.welcome_template_id is not null then
      select t.body into v_body
      from public.ig_dm_templates t
      where t.id = v_settings.welcome_template_id
        and t.active = true;
    else
      select t.body into v_body
      from public.ig_dm_templates t
      where t.account_id = p_account_id
        and t.template_type = 'welcome'
        and t.is_default = true
        and t.active = true
      order by t.created_at asc
      limit 1;
    end if;
  end if;

  if v_body is null or char_length(v_body) = 0 then
    raise exception 'no welcome message_body or template available for account %', p_account_id;
  end if;

  insert into public.ig_dm_jobs (
    account_id,
    dm_type,
    recipient_username,
    message_body,
    template_id,
    source,
    priority,
    status,
    idempotency_key,
    metadata
  )
  values (
    p_account_id,
    'welcome',
    v_username,
    v_body,
    coalesce(p_template_id, v_settings.welcome_template_id),
    'welcome_scan',
    coalesce(p_priority, 10),
    'pending',
    v_key,
    jsonb_build_object(
      'source_scan_run_id', p_source_scan_run_id,
      'follower_id', v_follower.id
    )
  )
  on conflict (idempotency_key) do nothing
  returning * into v_job;

  if v_job.id is null then
    return null;
  end if;

  update public.ig_account_followers
  set welcome_dm_status = 'pending',
      updated_at = now()
  where id = v_follower.id
    and welcome_dm_status not in ('sent', 'reserved');

  return v_job;
end;
$$;

comment on function public.enqueue_welcome_dm_job_if_eligible(uuid, text, uuid, text, uuid, integer) is
  'Welcome producer enqueue. Safely reactivates only unattempted scan-overflow cancellations with no send evidence.';
