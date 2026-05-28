-- Dashboard Foundation 1A - read-only admin/backend projections.
--
-- Adds service-role-only RPCs for future Admin Manage and Radar/Server Check
-- surfaces. No Edge Function, JWT admin/client exposure, dashboard UI,
-- mutations, phone controls, runtime hook, Slack/Discord change, or device run
-- is introduced here.

-- =============================================================================
-- 1. Admin Manage account overview
-- =============================================================================
create or replace function public.get_admin_account_overview(
  p_limit int default 100,
  p_offset int default 0,
  p_search text default null,
  p_status text default null
)
returns table (
  account_id uuid,
  client_id uuid,
  client_name text,
  username text,
  email_display text,
  admin_status text,
  customer_status text,
  subscription_status text,
  package_label text,
  entitlement_summary text[],
  credentials_configured boolean,
  credentials_status text,
  reauth_required boolean,
  login_status text,
  provisioning_status text,
  onboarding_status text,
  password_display text,
  two_factor_display text,
  last_7d_growth numeric,
  created_at timestamptz,
  tags text[],
  invoice_status text,
  pending_actions_count integer,
  blocking_campaign boolean,
  latest_incident_severity text,
  last_safe_update timestamptz
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_limit int := least(200, greatest(1, coalesce(p_limit, 100)));
  v_offset int := greatest(0, coalesce(p_offset, 0));
  v_search text := nullif(trim(coalesce(p_search, '')), '');
  v_status text := lower(nullif(trim(coalesce(p_status, '')), ''));
begin
  return query
  with latest_credentials as (
    select distinct on (ac.account_id)
      ac.account_id,
      ac.status,
      coalesce(ac.reauth_required, false) as reauth_required,
      ac.updated_at
    from public.account_credentials as ac
    where ac.provider = 'instagram'
    order by ac.account_id, ac.credentials_version desc, ac.updated_at desc
  ),
  subscription_agg as (
    select
      csa.account_id,
      case
        when bool_or(cs.status = 'active') then 'active'
        when bool_or(cs.status = 'paused') then 'paused'
        when bool_or(cs.status = 'cancelled') then 'cancelled'
        when bool_or(cs.status = 'expired') then 'expired'
        else 'unknown'
      end as subscription_status,
      nullif(string_agg(distinct cs.subscription_type, ', ' order by cs.subscription_type), '') as package_label
    from public.client_subscription_accounts as csa
    join public.client_subscriptions as cs
      on cs.id = csa.subscription_id
    group by csa.account_id
  ),
  entitlement_agg as (
    select
      ce.account_id,
      array_remove(array_agg(distinct ce.feature_code order by ce.feature_code), null) as entitlement_summary
    from public.client_entitlements as ce
    where ce.active = true
    group by ce.account_id
  ),
  action_agg as (
    select
      ada.account_id,
      count(*) filter (where ada.status in ('pending', 'acknowledged', 'pending_verification'))::int
        as pending_actions_count,
      bool_or(ada.blocking_campaign and ada.status in ('pending', 'acknowledged', 'pending_verification'))
        as blocking_campaign,
      max(ada.updated_at) as latest_action_at
    from public.account_dashboard_actions as ada
    group by ada.account_id
  ),
  incident_ranked as (
    select
      ai.account_id,
      ai.severity,
      ai.updated_at,
      row_number() over (
        partition by ai.account_id
        order by
          case ai.severity
            when 'critical' then 4
            when 'error' then 3
            when 'warning' then 2
            when 'info' then 1
            else 0
          end desc,
          ai.last_seen_at desc,
          ai.updated_at desc
      ) as rn
    from public.account_incidents as ai
    where ai.status in ('open', 'acknowledged')
  ),
  incident_latest as (
    select
      ir.account_id,
      ir.severity as latest_incident_severity,
      ir.updated_at as latest_incident_at
    from incident_ranked as ir
    where ir.rn = 1
  ),
  action_metrics as (
    select
      ial.account_id,
      count(*) filter (
        where ial.created_at >= now() - interval '7 days'
          and coalesce(ial.status, '') in ('success', 'completed', 'ok')
      )::numeric as last_7d_growth,
      max(ial.created_at) as latest_log_at
    from public.ig_action_logs as ial
    group by ial.account_id
  ),
  rows as (
    select
      cia.account_id,
      cia.client_id,
      c.name as client_name,
      nullif(trim(coalesce(ia.username, cia.label)), '') as username,
      case
        when nullif(trim(ia.email), '') is null then null
        when position('@' in ia.email) <= 1 then '***'
        else left(split_part(ia.email, '@', 1), 1) || '***@' || split_part(ia.email, '@', 2)
      end as email_display,
      case
        when coalesce(ia.status, '') in ('cancelled', 'canceled') then 'cancelled'
        when coalesce(ia.status, '') = 'paused' or coalesce(cia.provisioning_status, '') = 'paused' then 'paused'
        when coalesce(ia.status, '') = 'unpaid' then 'unpaid'
        when coalesce(cia.login_status, '') = 'needs_2fa' then 'twofactor'
        when coalesce(cia.login_status, '') = 'failed' or coalesce(lc.reauth_required, false) then 'incorrect'
        when coalesce(cia.login_status, '') = 'checkpoint' then 'checking'
        when coalesce(cia.login_status, '') = 'connected'
          and coalesce(cia.provisioning_status, '') in ('ready', 'assigned', 'provisioning') then 'active'
        when coalesce(cia.onboarding_status, '') in ('pending', 'incomplete', 'credentials_required') then 'pending'
        when coalesce(cia.login_status, '') in ('unknown', '') then 'new'
        else 'unknown'
      end as admin_status,
      case
        when coalesce(c.status, '') in ('active', 'trial', 'paused', 'inactive') then c.status
        else 'unknown'
      end as customer_status,
      coalesce(sa.subscription_status, 'unknown') as subscription_status,
      sa.package_label,
      coalesce(ea.entitlement_summary, array[]::text[]) as entitlement_summary,
      (lc.account_id is not null and lc.status = 'active') as credentials_configured,
      coalesce(lc.status, case when lc.account_id is null then 'missing' else 'unknown' end) as credentials_status,
      coalesce(lc.reauth_required, false) as reauth_required,
      coalesce(cia.login_status, 'unknown') as login_status,
      cia.provisioning_status,
      cia.onboarding_status,
      case
        when lc.account_id is not null and lc.status = 'active' then 'configured'
        when lc.account_id is null then 'missing'
        else 'unknown'
      end as password_display,
      case
        when coalesce(cia.login_status, '') = 'needs_2fa' then 'required'
        else 'unknown'
      end as two_factor_display,
      am.last_7d_growth,
      coalesce(cia.created_at, ia.created_at) as created_at,
      array[]::text[] as tags,
      null::text as invoice_status,
      coalesce(aa.pending_actions_count, 0) as pending_actions_count,
      coalesce(aa.blocking_campaign, false) as blocking_campaign,
      il.latest_incident_severity,
      greatest(
        coalesce(cia.updated_at, '-infinity'::timestamptz),
        coalesce(ia.updated_at, '-infinity'::timestamptz),
        coalesce(lc.updated_at, '-infinity'::timestamptz),
        coalesce(aa.latest_action_at, '-infinity'::timestamptz),
        coalesce(il.latest_incident_at, '-infinity'::timestamptz),
        coalesce(am.latest_log_at, '-infinity'::timestamptz)
      ) as last_safe_update
    from public.client_instagram_accounts as cia
    left join public.ig_accounts as ia
      on ia.id = cia.account_id
    left join public.clients as c
      on c.id = cia.client_id
    left join latest_credentials as lc
      on lc.account_id = cia.account_id
    left join subscription_agg as sa
      on sa.account_id = cia.account_id
    left join entitlement_agg as ea
      on ea.account_id = cia.account_id
    left join action_agg as aa
      on aa.account_id = cia.account_id
    left join incident_latest as il
      on il.account_id = cia.account_id
    left join action_metrics as am
      on am.account_id = cia.account_id
  )
  select
    rows.account_id,
    rows.client_id,
    rows.client_name,
    rows.username,
    rows.email_display,
    rows.admin_status,
    rows.customer_status,
    rows.subscription_status,
    rows.package_label,
    rows.entitlement_summary,
    rows.credentials_configured,
    rows.credentials_status,
    rows.reauth_required,
    rows.login_status,
    rows.provisioning_status,
    rows.onboarding_status,
    rows.password_display,
    rows.two_factor_display,
    rows.last_7d_growth,
    rows.created_at,
    rows.tags,
    rows.invoice_status,
    rows.pending_actions_count,
    rows.blocking_campaign,
    rows.latest_incident_severity,
    nullif(rows.last_safe_update, '-infinity'::timestamptz) as last_safe_update
  from rows
  where (v_search is null or (
      rows.username ilike '%' || v_search || '%'
      or rows.email_display ilike '%' || v_search || '%'
      or rows.client_name ilike '%' || v_search || '%'
    ))
    and (v_status is null or rows.admin_status = v_status)
  order by rows.created_at desc nulls last, rows.account_id
  limit v_limit
  offset v_offset;
end;
$$;

-- =============================================================================
-- 2. Admin Radar / Server Check base overview
-- =============================================================================
create or replace function public.get_admin_radar_overview(
  p_limit int default 100,
  p_offset int default 0,
  p_search text default null,
  p_status text default null,
  p_health text default null
)
returns table (
  account_id uuid,
  username text,
  email_display text,
  health_status text,
  health_reason text,
  admin_status text,
  password_display text,
  two_factor_display text,
  actions_2d integer,
  actions_7d integer,
  actions_30d integer,
  followers_gained_30d integer,
  followers_gained_90d integer,
  fbr_percent numeric,
  posts_30d integer,
  live_feed_not_updated_2d boolean,
  last_block_at timestamptz,
  dashboard_activity_status text,
  quick_rule_flags text[],
  special_care_active boolean,
  tags text[]
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_limit int := least(200, greatest(1, coalesce(p_limit, 100)));
  v_offset int := greatest(0, coalesce(p_offset, 0));
  v_search text := nullif(trim(coalesce(p_search, '')), '');
  v_status text := lower(nullif(trim(coalesce(p_status, '')), ''));
  v_health text := lower(nullif(trim(coalesce(p_health, '')), ''));
begin
  return query
  with manage as (
    select *
    from public.get_admin_account_overview(
      p_limit := 200,
      p_offset := 0,
      p_search := null,
      p_status := null
    )
  ),
  action_metrics as (
    select
      ial.account_id,
      count(*) filter (where ial.created_at >= now() - interval '2 days')::int as actions_2d,
      count(*) filter (where ial.created_at >= now() - interval '7 days')::int as actions_7d,
      count(*) filter (where ial.created_at >= now() - interval '30 days')::int as actions_30d,
      max(ial.created_at) filter (
        where lower(coalesce(ial.message, '') || ' ' || coalesce(ial.status, '') || ' ' || coalesce(ial.action_type, ''))
          like '%block%'
      ) as last_block_at
    from public.ig_action_logs as ial
    group by ial.account_id
  ),
  followback_metrics as (
    select
      iiu.account_id,
      round(
        100.0 * count(*) filter (where iiu.is_following_back is true)
        / nullif(count(*) filter (where iiu.followed_by_bot is true or iiu.follow_requested_at is not null), 0),
        2
      ) as fbr_percent
    from public.ig_interacted_users as iiu
    where iiu.last_interaction_at >= now() - interval '90 days'
    group by iiu.account_id
  ),
  latest_event as (
    select
      re.account_id,
      max(re.created_at) as latest_runtime_event_at
    from public.runtime_events as re
    where re.account_id is not null
    group by re.account_id
  ),
  open_incidents as (
    select
      ai.account_id,
      bool_or(ai.severity in ('critical', 'error')) as has_problem_incident,
      bool_or(ai.severity = 'warning') as has_warning_incident,
      max(ai.reason) filter (where ai.severity in ('critical', 'error')) as problem_reason,
      max(ai.reason) filter (where ai.severity = 'warning') as warning_reason
    from public.account_incidents as ai
    where ai.status in ('open', 'acknowledged')
    group by ai.account_id
  ),
  rows as (
    select
      m.account_id,
      m.username,
      m.email_display,
      case
        when coalesce(oi.has_problem_incident, false)
          or m.blocking_campaign
          or m.admin_status in ('incorrect', 'temporary_block', 'need_targets')
          or m.login_status in ('failed', 'checkpoint') then 'problem'
        when coalesce(oi.has_warning_incident, false)
          or m.pending_actions_count > 0
          or m.reauth_required
          or m.admin_status in ('checking', 'pending', 'twofactor')
          or (m.admin_status = 'active' and le.latest_runtime_event_at is null) then 'monitor'
        when m.admin_status = 'active'
          and m.login_status = 'connected'
          and not m.blocking_campaign
          and m.pending_actions_count = 0 then 'ok'
        else 'unknown'
      end as health_status,
      case
        when coalesce(oi.has_problem_incident, false) then coalesce(oi.problem_reason, 'open_problem_incident')
        when m.blocking_campaign then 'blocking_dashboard_action'
        when m.admin_status in ('incorrect', 'temporary_block', 'need_targets') then 'admin_status_' || m.admin_status
        when m.login_status in ('failed', 'checkpoint') then 'login_status_' || m.login_status
        when coalesce(oi.has_warning_incident, false) then coalesce(oi.warning_reason, 'open_warning_incident')
        when m.pending_actions_count > 0 then 'pending_dashboard_action'
        when m.reauth_required then 'reauth_required'
        when m.admin_status = 'active' and le.latest_runtime_event_at is null then 'runtime_data_missing'
        when m.admin_status = 'active' and m.login_status = 'connected' then 'healthy_connected'
        else 'insufficient_data'
      end as health_reason,
      m.admin_status,
      m.password_display,
      m.two_factor_display,
      am.actions_2d,
      am.actions_7d,
      am.actions_30d,
      null::int as followers_gained_30d,
      null::int as followers_gained_90d,
      fm.fbr_percent,
      null::int as posts_30d,
      case
        when le.latest_runtime_event_at is null then null
        else le.latest_runtime_event_at < now() - interval '2 days'
      end as live_feed_not_updated_2d,
      am.last_block_at,
      case
        when m.blocking_campaign then 'blocking'
        when m.pending_actions_count > 0 then 'pending'
        when m.last_safe_update >= now() - interval '2 days' then 'recent'
        else 'none'
      end as dashboard_activity_status,
      array[]::text[] as quick_rule_flags,
      false as special_care_active,
      m.tags
    from manage as m
    left join action_metrics as am
      on am.account_id = m.account_id
    left join followback_metrics as fm
      on fm.account_id = m.account_id
    left join latest_event as le
      on le.account_id = m.account_id
    left join open_incidents as oi
      on oi.account_id = m.account_id
  )
  select
    rows.account_id,
    rows.username,
    rows.email_display,
    rows.health_status,
    rows.health_reason,
    rows.admin_status,
    rows.password_display,
    rows.two_factor_display,
    rows.actions_2d,
    rows.actions_7d,
    rows.actions_30d,
    rows.followers_gained_30d,
    rows.followers_gained_90d,
    rows.fbr_percent,
    rows.posts_30d,
    rows.live_feed_not_updated_2d,
    rows.last_block_at,
    rows.dashboard_activity_status,
    rows.quick_rule_flags,
    rows.special_care_active,
    rows.tags
  from rows
  where (v_search is null or (
      rows.username ilike '%' || v_search || '%'
      or rows.email_display ilike '%' || v_search || '%'
    ))
    and (v_status is null or rows.admin_status = v_status)
    and (v_health is null or rows.health_status = v_health)
  order by
    case rows.health_status
      when 'problem' then 1
      when 'monitor' then 2
      when 'unknown' then 3
      when 'ok' then 4
      else 5
    end,
    rows.username nulls last,
    rows.account_id
  limit v_limit
  offset v_offset;
end;
$$;

-- =============================================================================
-- 3. Grants and documentation
-- =============================================================================
revoke execute on function public.get_admin_account_overview(int, int, text, text) from public;
revoke execute on function public.get_admin_account_overview(int, int, text, text) from anon;
revoke execute on function public.get_admin_account_overview(int, int, text, text) from authenticated;
grant execute on function public.get_admin_account_overview(int, int, text, text) to service_role;

revoke execute on function public.get_admin_radar_overview(int, int, text, text, text) from public;
revoke execute on function public.get_admin_radar_overview(int, int, text, text, text) from anon;
revoke execute on function public.get_admin_radar_overview(int, int, text, text, text) from authenticated;
grant execute on function public.get_admin_radar_overview(int, int, text, text, text) to service_role;

comment on function public.get_admin_account_overview(int, int, text, text) is
  'Dashboard Foundation 1A service-role-only read projection for future Admin Manage. Returns safe account overview fields only; no credentials, secret refs, raw logs, device internals, webhooks, or screenshots.';

comment on function public.get_admin_radar_overview(int, int, text, text, text) is
  'Dashboard Foundation 1A service-role-only read projection for future Admin Radar and Server Check. Health is derived from safe statuses/actions/incidents/runtime signals; no mutations or device controls.';
