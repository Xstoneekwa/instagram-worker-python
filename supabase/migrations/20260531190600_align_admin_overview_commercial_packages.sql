create or replace view public.account_package_summary
with (security_invoker = true) as
with current_package as (
  select distinct on (acp.account_id)
    acp.account_id,
    acp.package_code,
    cp.label as package_label,
    coalesce(
      nullif(acp.metadata_safe ->> 'package_started_at', '')::timestamptz,
      nullif(acp.metadata_safe ->> 'service_started_at', '')::timestamptz,
      nullif(acp.metadata_safe ->> 'warmup_started_at', '')::timestamptz
    ) as package_started_at,
    cp.default_follow_day_cap,
    cp.default_follow_session_cap,
    cp.default_unfollow_day_cap,
    cp.default_unfollow_session_cap,
    cp.default_welcome_enabled,
    cp.default_welcome_day_cap,
    cp.default_outreach_enabled,
    cp.default_outreach_day_cap,
    cp.advanced_ct_enabled,
    cp.ai_comment_enabled,
    cp.ai_targeting_enabled
  from public.account_commercial_packages acp
  join public.commercial_packages cp on cp.code = acp.package_code
  where acp.status = 'active'
    and acp.starts_at <= now()
    and (acp.ends_at is null or acp.ends_at > now())
  order by acp.account_id, acp.starts_at desc, acp.created_at desc
),
active_addons as (
  select
    account_id,
    array_agg(distinct addon_code order by addon_code) as commercial_addons,
    max(addon_variant) filter (where addon_code like 'outreach%') as outreach_variant,
    max(source_type) filter (where addon_code like 'outreach%') as outreach_job_source
  from public.account_commercial_addons
  where status = 'active'
    and starts_at <= now()
    and (ends_at is null or ends_at > now())
  group by account_id
),
account_clients as (
  select account_id, client_id
  from public.client_instagram_accounts
),
entitlement_summary as (
  select
    coalesce(ce.account_id, ac.account_id) as account_id,
    array_agg(distinct ce.feature_code order by ce.feature_code) filter (where ce.active) as entitlements
  from public.client_entitlements ce
  left join account_clients ac on ac.client_id = ce.client_id
  where ce.active = true
  group by coalesce(ce.account_id, ac.account_id)
),
runtime_profiles as (
  select
    csa.account_id,
    array_agg(distinct cs.subscription_type order by cs.subscription_type) as runtime_profiles
  from public.client_subscription_accounts csa
  join public.client_subscriptions cs on cs.id = csa.subscription_id
  where csa.status = 'active'
    and cs.status = 'active'
  group by csa.account_id
),
follow_caps as (
  select
    account_id,
    max_follow_per_run as follow_session_cap,
    max_actions_per_day as follow_day_cap,
    warmup_mode
  from public.ig_account_settings
),
unfollow_caps as (
  select
    account_id,
    unfollow_per_session_limit,
    unfollow_per_day_limit
  from public.ig_account_unfollow_settings
),
dm_caps as (
  select
    account_id,
    welcome_per_session_limit,
    welcome_per_day_limit,
    outreach_per_session_limit,
    outreach_per_day_limit
  from public.ig_account_dm_settings
)
select
  a.id as account_id,
  cp.package_code as commercial_package_code,
  cp.package_label as commercial_package_label,
  coalesce(aa.commercial_addons, array[]::text[]) as commercial_addons,
  coalesce(aa.outreach_variant, 'pending_source_classification') as outreach_variant,
  coalesce(aa.outreach_job_source, 'pending_source_classification') as outreach_job_source,
  coalesce(es.entitlements, array[]::text[]) as entitlements,
  coalesce(rp.runtime_profiles, array[]::text[]) as runtime_profiles,
  null::text as assignment_profile,
  case
    when cp.package_started_at is null then 'pending_package_start'
    when coalesce(fc.warmup_mode, false) then 'warmup_config_present_legacy_unverified'
    else 'not_configured'
  end as warmup_status,
  case
    when cp.package_started_at is null then null::integer
    else greatest(1, (current_date - cp.package_started_at::date + 1))::integer
  end as warmup_day,
  cp.package_started_at,
  jsonb_build_object(
    'follow_day', cp.default_follow_day_cap,
    'follow_session', cp.default_follow_session_cap,
    'unfollow_day', cp.default_unfollow_day_cap,
    'unfollow_session', cp.default_unfollow_session_cap,
    'welcome_enabled', cp.default_welcome_enabled,
    'welcome_day', cp.default_welcome_day_cap,
    'outreach_enabled', cp.default_outreach_enabled,
    'outreach_day', cp.default_outreach_day_cap,
    'advanced_ct_enabled', cp.advanced_ct_enabled,
    'ai_comment_enabled', cp.ai_comment_enabled,
    'ai_targeting_enabled', cp.ai_targeting_enabled
  ) as package_caps,
  jsonb_build_object(
    'follow_day', least(coalesce(fc.follow_day_cap, cp.default_follow_day_cap), cp.default_follow_day_cap),
    'follow_session', least(coalesce(fc.follow_session_cap, cp.default_follow_session_cap), cp.default_follow_session_cap),
    'unfollow_day', least(coalesce(uc.unfollow_per_day_limit, cp.default_unfollow_day_cap), cp.default_unfollow_day_cap),
    'unfollow_session', least(coalesce(uc.unfollow_per_session_limit, cp.default_unfollow_session_cap), cp.default_unfollow_session_cap),
    'welcome_day', least(coalesce(dc.welcome_per_day_limit, cp.default_welcome_day_cap), cp.default_welcome_day_cap),
    'welcome_session', least(coalesce(dc.welcome_per_session_limit, cp.default_welcome_day_cap), cp.default_welcome_day_cap),
    'outreach_day', least(coalesce(dc.outreach_per_day_limit, cp.default_outreach_day_cap), cp.default_outreach_day_cap),
    'outreach_session', dc.outreach_per_session_limit,
    'warmup_applied', false,
    'warmup_note', 'warmup audit only; not applied by this projection yet'
  ) as effective_caps_preview
from public.ig_accounts a
left join current_package cp on cp.account_id = a.id
left join active_addons aa on aa.account_id = a.id
left join entitlement_summary es on es.account_id = a.id
left join runtime_profiles rp on rp.account_id = a.id
left join follow_caps fc on fc.account_id = a.id
left join unfollow_caps uc on uc.account_id = a.id
left join dm_caps dc on dc.account_id = a.id;

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
      end as subscription_status
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
      count(*) filter (where ada.status in ('pending', 'acknowledged', 'pending_verification'))::int as pending_actions_count,
      bool_or(ada.blocking_campaign and ada.status in ('pending', 'acknowledged', 'pending_verification')) as blocking_campaign,
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
      aps.commercial_package_label as package_label,
      coalesce(ea.entitlement_summary, aps.entitlements, array[]::text[]) as entitlement_summary,
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
    left join public.ig_accounts as ia on ia.id = cia.account_id
    left join public.clients as c on c.id = cia.client_id
    left join latest_credentials as lc on lc.account_id = cia.account_id
    left join subscription_agg as sa on sa.account_id = cia.account_id
    left join public.account_package_summary as aps on aps.account_id = cia.account_id
    left join entitlement_agg as ea on ea.account_id = cia.account_id
    left join action_agg as aa on aa.account_id = cia.account_id
    left join incident_latest as il on il.account_id = cia.account_id
    left join action_metrics as am on am.account_id = cia.account_id
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
