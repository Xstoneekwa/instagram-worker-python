-- PREPARED ONLY — NOT APPLIED TO PRODUCTION.
-- CT Resume V5: persist a proven first-viewport evaluated prefix without
-- inventing a physical scroll depth. Additive; V4 remains authoritative for
-- every verified depth transition.

begin;

do $migration_guard$
begin
  if to_regclass('public.ig_target_followers_resume_checkpoints') is null
     or to_regclass('public.ig_target_followers_resume_checkpoint_events') is null
     or to_regclass('public.account_run_requests') is null
     or to_regclass('public.ig_runs') is null
     or to_regclass('public.ig_targets') is null then
    raise exception 'target_followers_resume_v5_required_schema_missing';
  end if;
  if to_regprocedure(
    'public.commit_target_followers_resume_checkpoint_v4(uuid,uuid,text,uuid,text,bigint,integer,jsonb,text,text,jsonb,text,text,boolean,text,integer)'
  ) is null then
    raise exception 'target_followers_resume_v4_baseline_missing';
  end if;
end
$migration_guard$;

create or replace function public.commit_target_followers_resume_first_pass_progress_v5(
  p_account_id uuid,
  p_target_id uuid,
  p_surface text,
  p_run_id uuid,
  p_mode text,
  p_expected_version bigint,
  p_commit_context jsonb,
  p_last_safe_anchor text,
  p_anchor_fingerprint text,
  p_evaluated_anchor_hashes jsonb,
  p_reason text default 'first_pass_evaluated_prefix',
  p_lease_seconds integer default 3600
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_checkpoint public.ig_target_followers_resume_checkpoints%rowtype;
  v_request public.account_run_requests%rowtype;
  v_run public.ig_runs%rowtype;
  v_metadata jsonb;
  v_attempt_text text;
  v_source_request_id uuid;
  v_source_attempt_id bigint;
  v_evaluated_count integer;
  v_existing_anchors jsonb;
  v_existing_count integer;
  v_index integer;
  v_new_version bigint;
  v_expiry timestamptz;
  v_event_id uuid;
  v_committed_at timestamptz;
  v_event_metadata jsonb;
begin
  if p_account_id is null
     or p_target_id is null
     or p_run_id is null
     or p_expected_version is null
     or p_surface is distinct from 'followers'
     or p_mode not in ('shadow', 'enforce')
     or p_reason !~ '^[a-z0-9_:-]{1,120}$'
     or p_lease_seconds not between 300 and 7200
     or p_last_safe_anchor !~ '^a3:[0-9a-f]{32}$'
     or p_anchor_fingerprint !~ '^v3:[0-9a-f]{32}$' then
    return jsonb_build_object('ok', false, 'reason', 'invalid_commit_input');
  end if;

  if jsonb_typeof(p_evaluated_anchor_hashes) is distinct from 'array'
     or jsonb_array_length(p_evaluated_anchor_hashes) not between 1 and 12
     or exists (
       select 1
       from jsonb_array_elements(p_evaluated_anchor_hashes) as anchor(value)
       where jsonb_typeof(anchor.value) is distinct from 'string'
          or (anchor.value #>> '{}') !~ '^a3:[0-9a-f]{32}$'
     )
     or p_last_safe_anchor is distinct from (
       p_evaluated_anchor_hashes ->> (jsonb_array_length(p_evaluated_anchor_hashes) - 1)
     ) then
    return jsonb_build_object('ok', false, 'reason', 'invalid_evaluated_prefix');
  end if;

  if jsonb_typeof(p_commit_context) is distinct from 'object'
     or pg_column_size(p_commit_context) > 1024
     or (select count(*) from jsonb_object_keys(p_commit_context)) <> 4
     or exists (
       select 1 from jsonb_object_keys(p_commit_context) as key(name)
       where not (key.name = any(array[
         'source_request_id', 'source_attempt_id', 'release_sha', 'evaluated_count'
       ]::text[]))
     )
     or jsonb_typeof(p_commit_context -> 'source_request_id') is distinct from 'string'
     or coalesce(p_commit_context ->> 'source_request_id', '')
       !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
     or jsonb_typeof(p_commit_context -> 'source_attempt_id') is distinct from 'number'
     or coalesce(p_commit_context ->> 'source_attempt_id', '') !~ '^[1-9][0-9]{0,9}$'
     or jsonb_typeof(p_commit_context -> 'release_sha') is distinct from 'string'
     or coalesce(p_commit_context ->> 'release_sha', '') !~ '^[0-9a-f]{40}$'
     or jsonb_typeof(p_commit_context -> 'evaluated_count') is distinct from 'number'
     or coalesce(p_commit_context ->> 'evaluated_count', '') !~ '^[1-9][0-9]{0,2}$' then
    return jsonb_build_object('ok', false, 'reason', 'invalid_commit_context');
  end if;

  v_source_request_id := (p_commit_context ->> 'source_request_id')::uuid;
  v_source_attempt_id := (p_commit_context ->> 'source_attempt_id')::bigint;
  v_evaluated_count := (p_commit_context ->> 'evaluated_count')::integer;
  if v_source_attempt_id > 2147483647
     or v_evaluated_count <> jsonb_array_length(p_evaluated_anchor_hashes) then
    return jsonb_build_object('ok', false, 'reason', 'invalid_commit_context');
  end if;

  select * into v_request
  from public.account_run_requests as request_row
  where request_row.id = v_source_request_id
  for share;
  if not found then
    return jsonb_build_object('ok', false, 'reason', 'source_request_missing');
  end if;
  if v_request.account_id is distinct from p_account_id
     or v_request.run_id is distinct from p_run_id
     or v_request.requested_run_type <> 'account_session'
     or v_request.status is distinct from 'running' then
    return jsonb_build_object('ok', false, 'reason', 'source_request_lineage_mismatch');
  end if;

  select * into v_run
  from public.ig_runs as run_row
  where run_row.id = p_run_id
  for share;
  if not found
     or v_run.account_id is distinct from p_account_id
     or v_run.status is distinct from 'running' then
    return jsonb_build_object('ok', false, 'reason', 'source_run_lineage_mismatch');
  end if;
  if not exists (
    select 1 from public.ig_targets as target_row
    where target_row.id = p_target_id
      and target_row.account_id = p_account_id
  ) then
    return jsonb_build_object('ok', false, 'reason', 'target_account_mismatch');
  end if;

  v_metadata := coalesce(v_request.metadata_safe, '{}'::jsonb);
  if jsonb_typeof(v_metadata) is distinct from 'object' then
    return jsonb_build_object('ok', false, 'reason', 'source_request_metadata_invalid');
  end if;
  if nullif(v_metadata ->> 'attempt_id', '') is not null
     and nullif(v_metadata ->> 'current_attempt_id', '') is not null
     and v_metadata ->> 'attempt_id' <> v_metadata ->> 'current_attempt_id' then
    return jsonb_build_object('ok', false, 'reason', 'source_attempt_divergence');
  end if;
  v_attempt_text := coalesce(
    nullif(v_metadata ->> 'attempt_id', ''),
    nullif(v_metadata ->> 'current_attempt_id', ''),
    nullif(v_metadata #>> '{resume_plan,attempt_id}', ''),
    nullif(v_metadata #>> '{resume_plan,current_attempt_id}', ''),
    '1'
  );
  if v_attempt_text !~ '^[1-9][0-9]{0,9}$'
     or v_attempt_text::bigint <> v_source_attempt_id then
    return jsonb_build_object('ok', false, 'reason', 'source_attempt_mismatch');
  end if;

  select * into v_checkpoint
  from public.ig_target_followers_resume_checkpoints as checkpoint_row
  where checkpoint_row.account_id = p_account_id
    and checkpoint_row.target_id = p_target_id
    and checkpoint_row.surface = p_surface
  for update;
  if not found then
    return jsonb_build_object('ok', false, 'reason', 'checkpoint_missing');
  end if;
  if v_checkpoint.checkpoint_version <> 3
     or v_checkpoint.status not in ('active', 'exhausted') then
    return jsonb_build_object('ok', false, 'reason', 'checkpoint_not_committable');
  end if;
  if v_checkpoint.optimistic_version <> p_expected_version then
    return jsonb_build_object(
      'ok', false, 'reason', 'optimistic_version_conflict',
      'optimistic_version', v_checkpoint.optimistic_version
    );
  end if;
  if v_checkpoint.lease_owner_run_id is distinct from p_run_id
     or v_checkpoint.lease_mode is distinct from p_mode
     or v_checkpoint.lease_expires_at is null
     or v_checkpoint.lease_expires_at <= now() then
    return jsonb_build_object('ok', false, 'reason', 'lease_invalid');
  end if;
  if (case when p_mode = 'enforce' then v_checkpoint.last_safe_depth
           else v_checkpoint.shadow_last_safe_depth end) <> 0 then
    return jsonb_build_object('ok', false, 'reason', 'first_pass_boundary_closed');
  end if;

  v_existing_anchors := case when p_mode = 'enforce'
    then coalesce(v_checkpoint.last_visible_anchor_hashes, '[]'::jsonb)
    else coalesce(v_checkpoint.shadow_visible_anchor_hashes, '[]'::jsonb)
  end;
  v_existing_count := jsonb_array_length(v_existing_anchors);
  if v_existing_count >= v_evaluated_count then
    return jsonb_build_object('ok', false, 'reason', 'no_safe_progress_rejected');
  end if;
  if v_existing_count > 0 then
    for v_index in 0..v_existing_count - 1 loop
      if v_existing_anchors ->> v_index
         is distinct from p_evaluated_anchor_hashes ->> v_index then
        return jsonb_build_object('ok', false, 'reason', 'evaluated_prefix_divergence');
      end if;
    end loop;
  end if;

  v_expiry := now() + make_interval(secs => p_lease_seconds);
  update public.ig_target_followers_resume_checkpoints as checkpoint_row
  set
    last_safe_anchor = case when p_mode = 'enforce' then p_last_safe_anchor else checkpoint_row.last_safe_anchor end,
    anchor_fingerprint = case when p_mode = 'enforce' then p_anchor_fingerprint else checkpoint_row.anchor_fingerprint end,
    last_visible_anchor_hashes = case when p_mode = 'enforce' then p_evaluated_anchor_hashes else checkpoint_row.last_visible_anchor_hashes end,
    shadow_last_safe_anchor = case when p_mode = 'shadow' then p_last_safe_anchor else checkpoint_row.shadow_last_safe_anchor end,
    shadow_anchor_fingerprint = case when p_mode = 'shadow' then p_anchor_fingerprint else checkpoint_row.shadow_anchor_fingerprint end,
    shadow_visible_anchor_hashes = case when p_mode = 'shadow' then p_evaluated_anchor_hashes else checkpoint_row.shadow_visible_anchor_hashes end,
    last_run_id = p_run_id,
    last_reached_at = now(),
    last_verified_at = now(),
    invalidation_reason = null,
    lease_expires_at = v_expiry,
    lease_heartbeat_at = now(),
    optimistic_version = checkpoint_row.optimistic_version + 1,
    updated_at = now()
  where checkpoint_row.id = v_checkpoint.id
  returning optimistic_version into v_new_version;

  v_event_metadata := p_commit_context || jsonb_build_object(
    'provenance_schema', 'target_followers_resume_first_pass_progress_v1',
    'committed_via', 'commit_target_followers_resume_first_pass_progress_v5',
    'checkpoint_version', v_checkpoint.checkpoint_version,
    'first_pass_boundary', true,
    'depth_unchanged', true,
    'anchor_count', v_evaluated_count,
    'last_safe_anchor', p_last_safe_anchor,
    'anchor_fingerprint', p_anchor_fingerprint,
    'evaluated_anchor_hashes', p_evaluated_anchor_hashes
  );
  if pg_column_size(v_event_metadata) > 4096 then
    raise exception 'target_followers_resume_v5_event_metadata_too_large';
  end if;

  insert into public.ig_target_followers_resume_checkpoint_events(
    checkpoint_id, account_id, target_id, run_id, event_type, mode,
    previous_optimistic_version, new_optimistic_version,
    previous_depth, new_depth, reason, metadata
  ) values (
    v_checkpoint.id, p_account_id, p_target_id, p_run_id, 'committed', p_mode,
    v_checkpoint.optimistic_version, v_new_version,
    0, 0, p_reason, v_event_metadata
  ) returning id, created_at into v_event_id, v_committed_at;

  return jsonb_build_object(
    'ok', true,
    'reason', 'first_pass_progress_committed',
    'optimistic_version', v_new_version,
    'depth', 0,
    'checkpoint_version', v_checkpoint.checkpoint_version,
    'lease_expires_at', v_expiry,
    'provenance_persisted', true,
    'commit_event_id', v_event_id,
    'committed_at', v_committed_at,
    'evaluated_count', v_evaluated_count
  );
end
$function$;

alter function public.commit_target_followers_resume_first_pass_progress_v5(
  uuid,uuid,text,uuid,text,bigint,jsonb,text,text,jsonb,text,integer
) owner to postgres;

revoke all on function public.commit_target_followers_resume_first_pass_progress_v5(
  uuid,uuid,text,uuid,text,bigint,jsonb,text,text,jsonb,text,integer
) from public, anon, authenticated, service_role;

grant execute on function public.commit_target_followers_resume_first_pass_progress_v5(
  uuid,uuid,text,uuid,text,bigint,jsonb,text,text,jsonb,text,integer
) to service_role;

comment on function public.commit_target_followers_resume_first_pass_progress_v5(
  uuid,uuid,text,uuid,text,bigint,jsonb,text,text,jsonb,text,integer
) is 'Atomically persists a contiguous evaluated prefix at physical depth zero. No raw usernames. service_role only.';

commit;
