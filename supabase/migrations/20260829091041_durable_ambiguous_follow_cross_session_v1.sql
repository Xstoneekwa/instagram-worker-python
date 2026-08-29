-- Durable post-tap Follow ambiguity is physical-risk memory, not success.
-- Forward-only replacement: preserve the exact action binding through P0C.

create or replace function public.reconcile_follow_candidate_recovery_backend_only_v1(
  p_recovery_id uuid,
  p_worker_id text
)
returns public.follow_candidate_recovery_queue
language plpgsql
security definer
set search_path = public
as $$
declare
  v_row public.follow_candidate_recovery_queue;
  v_follow_truth boolean := false;
  v_action_id text;
  v_existing_marker jsonb;
begin
  select * into v_row from public.follow_candidate_recovery_queue q
  where q.id = p_recovery_id and q.status = 'claimed' and q.claimed_by = p_worker_id
  for update;
  if v_row.id is null then raise exception 'follow_candidate_recovery_lineage_mismatch'; end if;
  if coalesce((v_row.evidence->>'follow_tap_sent')::boolean, false) is not true
     or coalesce((v_row.evidence->>'follow_verified')::boolean, false) is true
     or coalesce((v_row.evidence->>'follow_receipt_exists')::boolean, false) is true then
    raise exception 'backend_reconciliation_requires_physical_follow_ambiguity';
  end if;
  v_action_id := nullif(v_row.evidence->>'action_id', '');
  if v_action_id is null then
    raise exception 'backend_reconciliation_requires_action_id';
  end if;

  select exists(
    select 1 from public.ig_interacted_users iu
    where iu.account_id = v_row.account_id
      and lower(ltrim(iu.username,'@')) = lower(ltrim(v_row.candidate_username,'@'))
      and (
        lower(coalesce(iu.follow_status,'')) in ('following','requested','already_following')
        or lower(coalesce(iu.interaction_lifecycle_state,'')) = 'active_following'
        or iu.followed_at is not null
      )
      and iu.unfollowed_at is null
  ) into v_follow_truth;

  if not v_follow_truth then
    insert into public.ig_interacted_users(
      account_id, username, source_profile, interaction_type, was_successful,
      last_interaction_at, payload, updated_at
    ) values (
      v_row.account_id, lower(ltrim(v_row.candidate_username,'@')),
      v_row.source_ct_username, 'follow_ambiguous', false, now(),
      jsonb_build_object(
        'follow_mutation_ambiguous',
        jsonb_strip_nulls(jsonb_build_object(
          'durable', true,
          'resolved', false,
          'mutation_state', 'ambiguous',
          'action_id', v_action_id,
          'tap_sent', true,
          'receipt_exists', false,
          'follow_state_after', coalesce(nullif(v_row.evidence->>'follow_state_after',''),'unknown'),
          'observed_at', coalesce(nullif(v_row.evidence->>'observed_at',''), now()::text),
          'source_run_id', v_row.original_run_id,
          'source_business_session_id', v_row.business_session_id,
          'source_target_id', v_row.source_target_id,
          'failure_reason', nullif(v_row.evidence->>'failure_reason',''),
          'evidence_source', 'p0c_backend_reconciliation'
        ))
      ), now()
    )
    on conflict (account_id, username) do update set
      payload = jsonb_set(
        coalesce(public.ig_interacted_users.payload,'{}'::jsonb),
        '{follow_mutation_ambiguous}',
        excluded.payload->'follow_mutation_ambiguous',
        true
      ),
      updated_at = now();
  else
    select iu.payload->'follow_mutation_ambiguous'
      into v_existing_marker
      from public.ig_interacted_users iu
     where iu.account_id = v_row.account_id
       and lower(ltrim(iu.username,'@')) = lower(ltrim(v_row.candidate_username,'@'))
     limit 1;
    if coalesce(v_existing_marker->>'action_id','') = v_action_id then
      update public.ig_interacted_users iu
         set payload = jsonb_set(
               coalesce(iu.payload,'{}'::jsonb),
               '{follow_mutation_ambiguous}',
               v_existing_marker || jsonb_build_object(
                 'resolved', true,
                 'mutation_state', 'resolved',
                 'resolved_at', now(),
                 'resolution', 'canonical_follow_receipt'
               ),
               true
             ),
             updated_at = now()
       where iu.account_id = v_row.account_id
         and lower(ltrim(iu.username,'@')) = lower(ltrim(v_row.candidate_username,'@'));
    end if;
  end if;

  update public.follow_candidate_recovery_queue q
  set status = case when v_follow_truth then 'terminal' else 'quarantined' end,
      outcome = case when v_follow_truth then 'reconciled_existing_canonical_follow_truth'
                     else 'backend_follow_reconciliation_unresolved' end,
      completed_at = now(), updated_at = now(), claimed_by = null, lease_expires_at = null
  where q.id = v_row.id returning q.* into v_row;
  return v_row;
end;
$$;

revoke all on function public.reconcile_follow_candidate_recovery_backend_only_v1(uuid,text)
  from public, anon, authenticated;
grant execute on function public.reconcile_follow_candidate_recovery_backend_only_v1(uuid,text)
  to service_role;
