-- Sync ig_targets.followbacks_count from ig_interacted_users attribution.
-- Positive-only certification by default: followbacks_metrics_reliable_at is set only when
-- attributable followbacks_count > 0. Zero followback certification requires explicit scan
-- coverage (p_certify_zero_coverage = true) and is not used by backfill or worker hooks yet.
-- Does not enable auto-archive; display/policy gates remain in application code.

create or replace function public.sync_ig_target_followbacks_count(
  p_target_id uuid,
  p_certify_zero_coverage boolean default false
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_target record;
  v_count integer := 0;
  v_now timestamptz := now();
  v_certify boolean := false;
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

  v_certify := v_count > 0 or coalesce(p_certify_zero_coverage, false);

  if not v_certify then
    return jsonb_build_object(
      'ok', true,
      'target_id', p_target_id,
      'account_id', v_target.account_id,
      'normalized_username', v_target.normalized_username,
      'follows_sent_count', v_target.follows_sent_count,
      'followbacks_count', v_count,
      'certified', false,
      'reason', 'no_positive_attribution_without_scan_coverage'
    );
  end if;

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
    'followbacks_metrics_reliable_at', v_now,
    'certified', true,
    'certify_zero_coverage', coalesce(p_certify_zero_coverage, false)
  );
end;
$$;

comment on function public.sync_ig_target_followbacks_count(uuid, boolean) is
  'Recompute CT followbacks_count. Certifies followbacks_metrics_reliable_at only when count > 0 or explicit zero-coverage proof.';

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
  v_certified integer := 0;
begin
  if p_account_id is null then
    return jsonb_build_object('ok', false, 'reason', 'missing_account_id');
  end if;

  for v_target in
    select distinct t.id
    from public.ig_targets t
    where t.account_id = p_account_id
      and coalesce(t.follows_sent_count, 0) > 0
      and t.followbacks_metrics_reliable_at is null
      and exists (
        select 1
        from public.ig_interacted_users iu
        where iu.account_id = t.account_id
          and iu.is_following_back = true
          and iu.followed_by_bot = true
          and coalesce(iu.follow_status, 'following') = 'following'
          and iu.unfollowed_at is null
          and (
            iu.source_target_id = t.id
            or (
              iu.source_target_id is null
              and iu.source_target_username is not null
              and lower(trim(both '@' from iu.source_target_username)) = lower(t.normalized_username)
            )
          )
      )
    order by t.id
  loop
    v_one := public.sync_ig_target_followbacks_count(v_target.id, false);
    v_results := v_results || jsonb_build_array(v_one);
    if coalesce(v_one->>'ok', 'false') = 'true' then
      v_synced := v_synced + 1;
      if coalesce(v_one->>'certified', 'false') = 'true' then
        v_certified := v_certified + 1;
      end if;
    end if;
  end loop;

  return jsonb_build_object(
    'ok', true,
    'account_id', p_account_id,
    'synced_targets', v_synced,
    'certified_targets', v_certified,
    'results', v_results
  );
end;
$$;

comment on function public.sync_ig_account_target_followbacks(uuid) is
  'Positive-only account sync: certifies only CT rows with attributable followbacks_count > 0.';

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
  v_certified integer := 0;
  v_limit integer := greatest(1, least(coalesce(p_limit, 500), 5000));
begin
  for v_target in
    select distinct t.id
    from public.ig_targets t
    where coalesce(t.follows_sent_count, 0) > 0
      and t.followbacks_metrics_reliable_at is null
      and exists (
        select 1
        from public.ig_interacted_users iu
        where iu.account_id = t.account_id
          and iu.is_following_back = true
          and iu.followed_by_bot = true
          and coalesce(iu.follow_status, 'following') = 'following'
          and iu.unfollowed_at is null
          and (
            iu.source_target_id = t.id
            or (
              iu.source_target_id is null
              and iu.source_target_username is not null
              and lower(trim(both '@' from iu.source_target_username)) = lower(t.normalized_username)
            )
          )
      )
    order by t.follows_sent_count desc, t.id
    limit v_limit
  loop
    v_one := public.sync_ig_target_followbacks_count(v_target.id, false);
    v_results := v_results || jsonb_build_array(v_one);
    if coalesce(v_one->>'ok', 'false') = 'true' then
      v_synced := v_synced + 1;
      if coalesce(v_one->>'certified', 'false') = 'true' then
        v_certified := v_certified + 1;
      end if;
    end if;
  end loop;

  return jsonb_build_object(
    'ok', true,
    'synced_targets', v_synced,
    'certified_targets', v_certified,
    'left_unmeasured', greatest(0, v_synced - v_certified),
    'limit', v_limit,
    'results', v_results
  );
end;
$$;

comment on function public.backfill_ig_target_followbacks(integer) is
  'Global positive-only backfill: certifies CT rows with attributable followbacks_count > 0 only.';
