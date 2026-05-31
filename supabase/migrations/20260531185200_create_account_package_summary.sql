create table if not exists public.commercial_packages (
  code text primary key,
  label text not null,
  default_follow_day_cap integer,
  default_unfollow_day_cap integer,
  default_follow_session_cap integer,
  default_unfollow_session_cap integer,
  default_welcome_enabled boolean not null default false,
  default_outreach_enabled boolean not null default false,
  default_welcome_day_cap integer,
  default_outreach_day_cap integer,
  advanced_ct_enabled boolean not null default false,
  ai_comment_enabled boolean not null default false,
  ai_targeting_enabled boolean not null default false,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.account_commercial_packages (
  id uuid primary key default gen_random_uuid(),
  account_id uuid not null references public.ig_accounts(id) on delete cascade,
  package_code text not null references public.commercial_packages(code),
  status text not null default 'active',
  starts_at timestamptz not null default now(),
  ends_at timestamptz,
  source text not null default 'operator',
  metadata_safe jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists account_commercial_packages_account_status_idx
  on public.account_commercial_packages(account_id, status, starts_at desc);

create unique index if not exists account_commercial_packages_one_active_idx
  on public.account_commercial_packages(account_id)
  where status = 'active' and ends_at is null;

create table if not exists public.account_commercial_addons (
  id uuid primary key default gen_random_uuid(),
  account_id uuid not null references public.ig_accounts(id) on delete cascade,
  addon_code text not null,
  addon_variant text,
  source_type text,
  status text not null default 'active',
  starts_at timestamptz not null default now(),
  ends_at timestamptz,
  source text not null default 'operator',
  metadata_safe jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists account_commercial_addons_account_status_idx
  on public.account_commercial_addons(account_id, status, addon_code, starts_at desc);

create unique index if not exists account_commercial_addons_one_active_idx
  on public.account_commercial_addons(account_id, addon_code, coalesce(addon_variant, ''))
  where status = 'active' and ends_at is null;

alter table public.commercial_packages enable row level security;
alter table public.account_commercial_packages enable row level security;
alter table public.account_commercial_addons enable row level security;

insert into public.commercial_packages (
  code,
  label,
  default_follow_day_cap,
  default_unfollow_day_cap,
  default_follow_session_cap,
  default_unfollow_session_cap,
  default_welcome_enabled,
  default_outreach_enabled,
  default_welcome_day_cap,
  default_outreach_day_cap,
  advanced_ct_enabled,
  ai_comment_enabled,
  ai_targeting_enabled,
  active
)
values
  ('growth', 'Growth', 80, 80, 80, 80, false, false, null, null, false, false, false, true),
  ('pro', 'Pro', 120, 120, 120, 120, true, false, 10, null, true, false, false, true),
  ('premium', 'Premium', 120, 120, 120, 120, true, false, 10, null, true, true, true, true),
  ('outreach_standalone', 'Outreach standalone', null, null, null, null, false, true, null, 30, false, false, false, true)
on conflict (code) do update set
  label = excluded.label,
  default_follow_day_cap = excluded.default_follow_day_cap,
  default_unfollow_day_cap = excluded.default_unfollow_day_cap,
  default_follow_session_cap = excluded.default_follow_session_cap,
  default_unfollow_session_cap = excluded.default_unfollow_session_cap,
  default_welcome_enabled = excluded.default_welcome_enabled,
  default_outreach_enabled = excluded.default_outreach_enabled,
  default_welcome_day_cap = excluded.default_welcome_day_cap,
  default_outreach_day_cap = excluded.default_outreach_day_cap,
  advanced_ct_enabled = excluded.advanced_ct_enabled,
  ai_comment_enabled = excluded.ai_comment_enabled,
  ai_targeting_enabled = excluded.ai_targeting_enabled,
  active = excluded.active,
  updated_at = now();

create or replace view public.account_package_summary
with (security_invoker = true) as
with current_package as (
  select distinct on (acp.account_id)
    acp.account_id,
    acp.package_code,
    cp.label as package_label
  from public.account_commercial_packages acp
  join public.commercial_packages cp on cp.code = acp.package_code
  where acp.status = 'active'
    and acp.starts_at <= now()
    and (acp.ends_at is null or acp.ends_at > now())
  order by acp.account_id, acp.starts_at desc, acp.created_at desc
), active_addons as (
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
), account_clients as (
  select account_id, client_id
  from public.client_instagram_accounts
), entitlement_summary as (
  select
    coalesce(ce.account_id, ac.account_id) as account_id,
    array_agg(distinct ce.feature_code order by ce.feature_code) filter (where ce.active) as entitlements
  from public.client_entitlements ce
  left join account_clients ac on ac.client_id = ce.client_id
  where ce.active = true
  group by coalesce(ce.account_id, ac.account_id)
), runtime_profiles as (
  select
    csa.account_id,
    array_agg(distinct cs.subscription_type order by cs.subscription_type) as runtime_profiles
  from public.client_subscription_accounts csa
  join public.client_subscriptions cs on cs.id = csa.subscription_id
  where csa.status = 'active'
    and cs.status = 'active'
  group by csa.account_id
)
select
  a.id as account_id,
  cp.package_code as commercial_package_code,
  cp.package_label as commercial_package_label,
  coalesce(aa.commercial_addons, array[]::text[]) as commercial_addons,
  coalesce(aa.outreach_variant, 'pending_source_classification') as outreach_variant,
  coalesce(aa.outreach_job_source, 'pending_source_classification') as outreach_job_source,
  coalesce(es.entitlements, array[]::text[]) as entitlements,
  coalesce(rp.runtime_profiles, array[]::text[]) as runtime_profiles
from public.ig_accounts a
left join current_package cp on cp.account_id = a.id
left join active_addons aa on aa.account_id = a.id
left join entitlement_summary es on es.account_id = a.id
left join runtime_profiles rp on rp.account_id = a.id;
