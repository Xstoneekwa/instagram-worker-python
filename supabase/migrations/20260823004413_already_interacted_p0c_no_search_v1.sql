-- Already-interacted Follow exclusion + P0C backend-only recovery.
-- Forward-only.  No Instagram observation or mutation is performed here.

alter table public.follow_candidate_recovery_queue
  drop constraint if exists follow_candidate_recovery_queue_status_check;
alter table public.follow_candidate_recovery_queue
  add constraint follow_candidate_recovery_queue_status_check
  check (status in ('pending','claimed','deferred','completed','terminal','quarantined'));

create or replace function public.enqueue_follow_candidate_recovery_v1(
  p_recovery_key text,
  p_account_id uuid,
  p_candidate_username text,
  p_source_target_id uuid default null,
  p_source_ct_username text default null,
  p_original_run_id uuid default null,
  p_original_request_id uuid default null,
  p_business_session_id uuid default null,
  p_evidence jsonb default '{}'::jsonb
)
returns public.follow_candidate_recovery_queue
language plpgsql
security definer
set search_path = public
as $$
declare v_row public.follow_candidate_recovery_queue;
begin
  if nullif(btrim(coalesce(p_recovery_key, '')), '') is null
     or nullif(btrim(coalesce(p_candidate_username, '')), '') is null then
    raise exception 'follow_candidate_recovery_identity_missing';
  end if;
  if coalesce((p_evidence->>'follow_tap_sent')::boolean, false) is not true
     or coalesce((p_evidence->>'follow_verified')::boolean, false) is true
     or coalesce((p_evidence->>'follow_receipt_exists')::boolean, false) is true then
    raise exception 'follow_candidate_recovery_requires_physical_follow_ambiguity';
  end if;
  insert into public.follow_candidate_recovery_queue(
    recovery_key, account_id, candidate_username, source_target_id,
    source_ct_username, original_run_id, original_request_id,
    business_session_id, evidence
  ) values (
    p_recovery_key, p_account_id, lower(ltrim(btrim(p_candidate_username), '@')),
    p_source_target_id, nullif(lower(ltrim(btrim(coalesce(p_source_ct_username,'')), '@')), ''),
    p_original_run_id, p_original_request_id, p_business_session_id,
    coalesce(p_evidence, '{}'::jsonb)
  )
  on conflict (recovery_key) do update
  set evidence = public.follow_candidate_recovery_queue.evidence || excluded.evidence,
      updated_at = now()
  returning * into v_row;
  return v_row;
end;
$$;

create or replace function public.claim_follow_candidate_recovery_v1(
  p_account_id uuid,
  p_worker_id text,
  p_limit integer default 20
)
returns setof public.follow_candidate_recovery_queue
language plpgsql
security definer
set search_path = public
as $$
begin
  if nullif(btrim(coalesce(p_worker_id, '')), '') is null then
    raise exception 'follow_candidate_recovery_worker_missing';
  end if;
  return query
  with claimable as (
    select q.id
    from public.follow_candidate_recovery_queue q
    where q.account_id = p_account_id
      and q.next_attempt_at <= now()
      and coalesce((q.evidence->>'follow_tap_sent')::boolean, false) is true
      and coalesce((q.evidence->>'follow_verified')::boolean, false) is false
      and coalesce((q.evidence->>'follow_receipt_exists')::boolean, false) is false
      and (
        q.status in ('pending','deferred')
        or (q.status = 'claimed' and q.lease_expires_at < now())
      )
    order by q.created_at, q.id
    for update skip locked
    limit greatest(1, least(coalesce(p_limit, 20), 8))
  )
  update public.follow_candidate_recovery_queue q
  set status = 'claimed', claimed_by = p_worker_id,
      lease_expires_at = now() + interval '5 minutes',
      attempt_count = q.attempt_count + 1, updated_at = now()
  from claimable c
  where q.id = c.id
  returning q.*;
end;
$$;

create or replace function public.project_follow_recovery_already_interacted_v1(
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
  v_like boolean;
  v_mute boolean;
begin
  select * into v_row from public.follow_candidate_recovery_queue q
  where q.id = p_recovery_id and q.status = 'claimed' and q.claimed_by = p_worker_id
  for update;
  if v_row.id is null then raise exception 'follow_candidate_recovery_lineage_mismatch'; end if;
  if coalesce((v_row.evidence->>'follow_tap_sent')::boolean, false) then
    raise exception 'already_interacted_projection_rejects_follow_tap';
  end if;
  v_like := lower(coalesce(v_row.evidence->>'like_state','')) = 'liked';
  v_mute := coalesce((v_row.evidence->>'mute_verify_success')::boolean, false)
    and (coalesce((v_row.evidence->>'muted_posts')::boolean, false)
      or coalesce((v_row.evidence->>'muted_stories')::boolean, false));
  if not (v_like or v_mute) then raise exception 'already_interacted_durable_proof_missing'; end if;

  insert into public.ig_interacted_users(
    account_id, username, source_profile, interaction_type, was_successful,
    last_interaction_at, posts_liked_count, muted_posts, muted_stories,
    last_muted_at, payload, updated_at
  ) values (
    v_row.account_id, lower(ltrim(v_row.candidate_username, '@')),
    v_row.source_ct_username, case when v_like then 'like' else 'mute' end, true,
    now(), case when v_like then 1 else 0 end,
    v_mute and coalesce((v_row.evidence->>'muted_posts')::boolean, false),
    v_mute and coalesce((v_row.evidence->>'muted_stories')::boolean, false),
    case when v_mute then now() else null end,
    jsonb_strip_nulls(jsonb_build_object(
      'already_interacted_like', case when v_like then jsonb_build_object('durable', true, 'evidence_source', 'p0c_recovery_projection') end,
      'already_interacted_mute', case when v_mute then jsonb_build_object('durable', true, 'evidence_source', 'p0c_recovery_projection') end
    )), now()
  )
  on conflict (account_id, username) do update set
    posts_liked_count = greatest(coalesce(public.ig_interacted_users.posts_liked_count,0), excluded.posts_liked_count),
    muted_posts = coalesce(public.ig_interacted_users.muted_posts,false) or excluded.muted_posts,
    muted_stories = coalesce(public.ig_interacted_users.muted_stories,false) or excluded.muted_stories,
    last_muted_at = coalesce(public.ig_interacted_users.last_muted_at, excluded.last_muted_at),
    payload = coalesce(public.ig_interacted_users.payload,'{}'::jsonb) || excluded.payload,
    updated_at = now();

  update public.follow_candidate_recovery_queue q
  set status = 'terminal', outcome = 'non_actionable_already_interacted_no_follow_recovery',
      completed_at = now(), updated_at = now(), claimed_by = null, lease_expires_at = null
  where q.id = v_row.id returning q.* into v_row;
  return v_row;
end;
$$;

create or replace function public.quarantine_follow_candidate_recovery_v1(
  p_recovery_id uuid,
  p_worker_id text,
  p_reason text
)
returns public.follow_candidate_recovery_queue
language plpgsql
security definer
set search_path = public
as $$
declare v_row public.follow_candidate_recovery_queue;
begin
  update public.follow_candidate_recovery_queue q
  set status = 'quarantined', outcome = coalesce(nullif(p_reason,''),'recovery_evidence_unknown_quarantined'),
      completed_at = now(), updated_at = now(), claimed_by = null, lease_expires_at = null
  where q.id = p_recovery_id and q.status = 'claimed' and q.claimed_by = p_worker_id
  returning q.* into v_row;
  if v_row.id is null then raise exception 'follow_candidate_recovery_lineage_mismatch'; end if;
  return v_row;
end;
$$;

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
        jsonb_build_object('durable', true, 'resolved', false, 'evidence_source', 'p0c_backend_reconciliation')
      ), now()
    )
    on conflict (account_id, username) do update set
      payload = coalesce(public.ig_interacted_users.payload,'{}'::jsonb) || excluded.payload,
      updated_at = now();
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

-- One-time generic reconciliation.  Like/Mute-only rows become Social Memory;
-- UNKNOWN rows are quarantined.  Neither class can manufacture Follow truth.
insert into public.ig_interacted_users(
  account_id, username, source_profile, interaction_type, was_successful,
  last_interaction_at, posts_liked_count, muted_posts, muted_stories,
  last_muted_at, payload, updated_at
)
select q.account_id, lower(ltrim(q.candidate_username,'@')), q.source_ct_username,
  case when lower(coalesce(q.evidence->>'like_state','')) = 'liked' then 'like' else 'mute' end,
  true, coalesce(q.updated_at,q.created_at),
  case when lower(coalesce(q.evidence->>'like_state','')) = 'liked' then 1 else 0 end,
  coalesce((q.evidence->>'muted_posts')::boolean,false),
  coalesce((q.evidence->>'muted_stories')::boolean,false),
  case when coalesce((q.evidence->>'mute_verify_success')::boolean,false) then coalesce(q.updated_at,q.created_at) end,
  jsonb_strip_nulls(jsonb_build_object(
    'already_interacted_like', case when lower(coalesce(q.evidence->>'like_state','')) = 'liked' then jsonb_build_object('durable', true, 'evidence_source', 'p0c_historical_projection') end,
    'already_interacted_mute', case when coalesce((q.evidence->>'mute_verify_success')::boolean,false) then jsonb_build_object('durable', true, 'evidence_source', 'p0c_historical_projection') end
  )), now()
from public.follow_candidate_recovery_queue q
where coalesce((q.evidence->>'follow_tap_sent')::boolean,false) is false
  and (
    lower(coalesce(q.evidence->>'like_state','')) = 'liked'
    or (coalesce((q.evidence->>'mute_verify_success')::boolean,false)
      and (coalesce((q.evidence->>'muted_posts')::boolean,false)
        or coalesce((q.evidence->>'muted_stories')::boolean,false)))
  )
on conflict (account_id, username) do update set
  posts_liked_count = greatest(coalesce(public.ig_interacted_users.posts_liked_count,0), excluded.posts_liked_count),
  muted_posts = coalesce(public.ig_interacted_users.muted_posts,false) or excluded.muted_posts,
  muted_stories = coalesce(public.ig_interacted_users.muted_stories,false) or excluded.muted_stories,
  last_muted_at = coalesce(public.ig_interacted_users.last_muted_at, excluded.last_muted_at),
  payload = coalesce(public.ig_interacted_users.payload,'{}'::jsonb) || excluded.payload,
  updated_at = now();

update public.follow_candidate_recovery_queue q
set status = 'terminal', outcome = 'non_actionable_already_interacted_no_follow_recovery',
    completed_at = now(), updated_at = now(), claimed_by = null, lease_expires_at = null
where coalesce((q.evidence->>'follow_tap_sent')::boolean,false) is false
  and (
    lower(coalesce(q.evidence->>'like_state','')) = 'liked'
    or (coalesce((q.evidence->>'mute_verify_success')::boolean,false)
      and (coalesce((q.evidence->>'muted_posts')::boolean,false)
        or coalesce((q.evidence->>'muted_stories')::boolean,false)))
  );

update public.follow_candidate_recovery_queue q
set status = 'quarantined', outcome = 'recovery_evidence_unknown_quarantined',
    completed_at = now(), updated_at = now(), claimed_by = null, lease_expires_at = null
where coalesce((q.evidence->>'follow_tap_sent')::boolean,false) is false
  and lower(coalesce(q.evidence->>'like_state','')) <> 'liked'
  and not (
    coalesce((q.evidence->>'mute_verify_success')::boolean,false)
    and (coalesce((q.evidence->>'muted_posts')::boolean,false)
      or coalesce((q.evidence->>'muted_stories')::boolean,false))
  )
  and q.status not in ('completed','terminal','quarantined');

revoke all on function public.enqueue_follow_candidate_recovery_v1(text,uuid,text,uuid,text,uuid,uuid,uuid,jsonb) from public, anon, authenticated;
revoke all on function public.claim_follow_candidate_recovery_v1(uuid,text,integer) from public, anon, authenticated;
revoke all on function public.project_follow_recovery_already_interacted_v1(uuid,text) from public, anon, authenticated;
revoke all on function public.quarantine_follow_candidate_recovery_v1(uuid,text,text) from public, anon, authenticated;
revoke all on function public.reconcile_follow_candidate_recovery_backend_only_v1(uuid,text) from public, anon, authenticated;
grant execute on function public.enqueue_follow_candidate_recovery_v1(text,uuid,text,uuid,text,uuid,uuid,uuid,jsonb) to service_role;
grant execute on function public.claim_follow_candidate_recovery_v1(uuid,text,integer) to service_role;
grant execute on function public.project_follow_recovery_already_interacted_v1(uuid,text) to service_role;
grant execute on function public.quarantine_follow_candidate_recovery_v1(uuid,text,text) to service_role;
grant execute on function public.reconcile_follow_candidate_recovery_backend_only_v1(uuid,text) to service_role;
