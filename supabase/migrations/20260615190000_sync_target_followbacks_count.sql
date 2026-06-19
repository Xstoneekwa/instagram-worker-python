-- Sync ig_targets.followbacks_count from ig_interacted_users attribution.
-- Sets followbacks_metrics_reliable_at when the sync runs (certifies measurement).
-- Does not enable auto-archive; display/policy gates remain in application code.

create or replace function public.sync_ig_target_followbacks_count(p_target_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_target record;
  v_count integer := 0;
  v_now timestamptz := now();
begin
  select id, account_id, normalized_username, follows_sent_count
  into v_target
  from public.ig_targets
  where id = p_target_id;

  if not found then
    return jsonb_build_object('ok', false, 'reason', 'target_not_found', 'target_id', p_target_id);
  end if;

  select count(*)::integer
  into v_count
  from public.ig_interacted_users iu
  where iu.account_id = v_target.account_id
    and iu.is_following_back = true
    and iu.followed_by_bot = true
    and coalesce(iu.follow_status, 'following') = 'following'
    and iu.unfollowed_at is null
    and (
      iu.source_target_id = v_target.id
      or (
        iu.source_target_id is null
        and iu.source_target_username is not null
        and lower(trim(both '@' from iu.source_target_username)) = lower(v_target.normalized_username)
      )
    );

  update public.ig_targets
  set
    followbacks_count = v_count,
    followbacks_metrics_reliable_at = v_now,
    metrics_updated_at = v_now
  where id = p_target_id;

  return jsonb_build_object(
    'ok', true,
    'target_id', p_target_id,
    'account_id', v_target.account_id,
    'normalized_username', v_target.normalized_username,
    'follows_sent_count', v_target.follows_sent_count,
    'followbacks_count', v_count,
    'followbacks_metrics_reliable_at', v_now
  );
end;
$$;

comment on function public.sync_ig_target_followbacks_count(uuid) is
  'Recompute CT-level followbacks_count from ig_interacted_users and certify followbacks_metrics_reliable_at.';

create or replace function public.sync_ig_account_target_followbacks(p_account_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_target record;
  v_results jsonb := '[]'::jsonb;
  v_one jsonb;
  v_synced integer := 0;
begin
  if p_account_id is null then
    return jsonb_build_object('ok', false, 'reason', 'missing_account_id');
  end if;

  for v_target in
    select id
    from public.ig_targets
    where account_id = p_account_id
      and coalesce(follows_sent_count, 0) > 0
    order by follows_sent_count desc
  loop
    v_one := public.sync_ig_target_followbacks_count(v_target.id);
    v_results := v_results || jsonb_build_array(v_one);
    if coalesce(v_one->>'ok', 'false') = 'true' then
      v_synced := v_synced + 1;
    end if;
  end loop;

  return jsonb_build_object(
    'ok', true,
    'account_id', p_account_id,
    'synced_targets', v_synced,
    'results', v_results
  );
end;
$$;

comment on function public.sync_ig_account_target_followbacks(uuid) is
  'Batch sync followbacks_count for all CT rows on an account with follows_sent_count > 0.';

create or replace function public.backfill_ig_target_followbacks(p_limit integer default 500)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_target record;
  v_results jsonb := '[]'::jsonb;
  v_one jsonb;
  v_synced integer := 0;
  v_limit integer := greatest(1, least(coalesce(p_limit, 500), 5000));
begin
  for v_target in
    select id
    from public.ig_targets
    where coalesce(follows_sent_count, 0) > 0
      and followbacks_metrics_reliable_at is null
    order by follows_sent_count desc
    limit v_limit
  loop
    v_one := public.sync_ig_target_followbacks_count(v_target.id);
    v_results := v_results || jsonb_build_array(v_one);
    if coalesce(v_one->>'ok', 'false') = 'true' then
      v_synced := v_synced + 1;
    end if;
  end loop;

  return jsonb_build_object(
    'ok', true,
    'synced_targets', v_synced,
    'limit', v_limit,
    'results', v_results
  );
end;
$$;

comment on function public.backfill_ig_target_followbacks(integer) is
  'Safe backfill: sync followbacks_count for targets with follows but no followbacks_metrics_reliable_at yet.';
