-- Entry 2A — client ownership and entitlements for Outreach enqueue.
--
-- This migration intentionally does not touch worker runtime tables/RPCs except
-- for revoking direct authenticated access to enqueue_outreach_dm_job. Producers
-- must go through the Edge Function so ownership and entitlement checks run
-- before the service-role RPC call.

create extension if not exists pgcrypto;

-- =============================================================================
-- 1. Clients and users
-- =============================================================================
create table if not exists public.clients (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  status text not null default 'active',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint clients_name_nonempty check (char_length(trim(name)) > 0),
  constraint clients_status_check check (status in ('active', 'suspended', 'archived'))
);

create table if not exists public.client_users (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references public.clients (id) on delete cascade,
  auth_user_id uuid not null references auth.users (id) on delete cascade,
  role text not null default 'owner',
  status text not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint client_users_client_auth_user_key unique (client_id, auth_user_id),
  constraint client_users_role_check check (role in ('owner', 'admin', 'assistant', 'viewer')),
  constraint client_users_status_check check (status in ('active', 'disabled'))
);

create index if not exists client_users_auth_user_id_idx
  on public.client_users (auth_user_id);

create index if not exists client_users_client_id_idx
  on public.client_users (client_id);

-- =============================================================================
-- 2. Instagram account ownership and entitlements
-- =============================================================================
create table if not exists public.client_instagram_accounts (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references public.clients (id) on delete cascade,
  account_id uuid not null references public.ig_accounts (id) on delete restrict,
  label text,
  onboarding_status text not null default 'pending',
  provisioning_status text not null default 'not_started',
  login_status text not null default 'unknown',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint client_instagram_accounts_account_id_key unique (account_id),
  constraint client_instagram_accounts_client_account_key unique (client_id, account_id),
  constraint client_instagram_accounts_onboarding_status_check
    check (onboarding_status in ('pending', 'configured', 'ready', 'blocked')),
  constraint client_instagram_accounts_provisioning_status_check
    check (provisioning_status in ('not_started', 'pending', 'provisioning', 'ready', 'failed')),
  constraint client_instagram_accounts_login_status_check
    check (login_status in ('unknown', 'pending', 'connected', 'needs_2fa', 'checkpoint', 'failed', 'mismatch'))
);

create index if not exists client_instagram_accounts_client_id_idx
  on public.client_instagram_accounts (client_id);

create table if not exists public.client_entitlements (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references public.clients (id) on delete cascade,
  feature_code text not null,
  entitlement_type text not null default 'standalone',
  active boolean not null default true,
  valid_from timestamptz default now(),
  valid_until timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint client_entitlements_feature_code_check
    check (feature_code in ('outreach', 'welcome', 'follow', 'unfollow')),
  constraint client_entitlements_type_check
    check (entitlement_type in ('standalone', 'addon', 'bundle')),
  constraint client_entitlements_valid_window_check
    check (valid_until is null or valid_from is null or valid_until > valid_from)
);

create unique index if not exists client_entitlements_one_active_feature_per_client_key
  on public.client_entitlements (client_id, feature_code)
  where active = true;

create index if not exists client_entitlements_client_feature_idx
  on public.client_entitlements (client_id, feature_code, active);

-- =============================================================================
-- 3. updated_at triggers
-- =============================================================================
drop trigger if exists clients_set_updated_at on public.clients;
create trigger clients_set_updated_at
  before update on public.clients
  for each row execute function public.set_updated_at();

drop trigger if exists client_users_set_updated_at on public.client_users;
create trigger client_users_set_updated_at
  before update on public.client_users
  for each row execute function public.set_updated_at();

drop trigger if exists client_instagram_accounts_set_updated_at on public.client_instagram_accounts;
create trigger client_instagram_accounts_set_updated_at
  before update on public.client_instagram_accounts
  for each row execute function public.set_updated_at();

drop trigger if exists client_entitlements_set_updated_at on public.client_entitlements;
create trigger client_entitlements_set_updated_at
  before update on public.client_entitlements
  for each row execute function public.set_updated_at();

-- =============================================================================
-- 4. Ownership / entitlement helpers
-- =============================================================================
create or replace function public.client_can_enqueue_outreach(
  p_auth_user_id uuid,
  p_account_id uuid
)
returns boolean
language sql
stable
security invoker
set search_path = public
as $$
  select exists (
    select 1
    from public.client_users cu
    join public.clients c
      on c.id = cu.client_id
     and c.status = 'active'
    join public.client_instagram_accounts cia
      on cia.client_id = c.id
     and cia.account_id = p_account_id
    join public.client_entitlements ce
      on ce.client_id = c.id
     and ce.feature_code = 'outreach'
     and ce.active = true
     and (ce.valid_from is null or ce.valid_from <= now())
     and (ce.valid_until is null or ce.valid_until > now())
    join public.ig_account_dm_settings s
      on s.account_id = cia.account_id
     and s.outreach_enabled = true
    where cu.auth_user_id = p_auth_user_id
      and cu.status = 'active'
      and p_auth_user_id is not null
      and p_account_id is not null
  );
$$;

create or replace function public.client_account_has_outreach_entitlement(
  p_account_id uuid
)
returns boolean
language sql
stable
security invoker
set search_path = public
as $$
  select exists (
    select 1
    from public.clients c
    join public.client_instagram_accounts cia
      on cia.client_id = c.id
     and cia.account_id = p_account_id
    join public.client_entitlements ce
      on ce.client_id = c.id
     and ce.feature_code = 'outreach'
     and ce.active = true
     and (ce.valid_from is null or ce.valid_from <= now())
     and (ce.valid_until is null or ce.valid_until > now())
    join public.ig_account_dm_settings s
      on s.account_id = cia.account_id
     and s.outreach_enabled = true
    where c.status = 'active'
      and p_account_id is not null
  );
$$;

-- =============================================================================
-- 5. RLS
-- =============================================================================
alter table public.clients enable row level security;
alter table public.client_users enable row level security;
alter table public.client_instagram_accounts enable row level security;
alter table public.client_entitlements enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'clients' and policyname = 'clients_service_role_all'
  ) then
    create policy clients_service_role_all on public.clients
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'client_users' and policyname = 'client_users_service_role_all'
  ) then
    create policy client_users_service_role_all on public.client_users
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'client_instagram_accounts' and policyname = 'client_instagram_accounts_service_role_all'
  ) then
    create policy client_instagram_accounts_service_role_all on public.client_instagram_accounts
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'client_entitlements' and policyname = 'client_entitlements_service_role_all'
  ) then
    create policy client_entitlements_service_role_all on public.client_entitlements
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'client_users' and policyname = 'client_users_select_own_membership'
  ) then
    create policy client_users_select_own_membership on public.client_users
      for select
      to authenticated
      using (auth_user_id = auth.uid() and status = 'active');
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'clients' and policyname = 'clients_select_own_clients'
  ) then
    create policy clients_select_own_clients on public.clients
      for select
      to authenticated
      using (
        exists (
          select 1
          from public.client_users cu
          where cu.client_id = clients.id
            and cu.auth_user_id = auth.uid()
            and cu.status = 'active'
        )
      );
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'client_instagram_accounts' and policyname = 'client_instagram_accounts_select_own_accounts'
  ) then
    create policy client_instagram_accounts_select_own_accounts on public.client_instagram_accounts
      for select
      to authenticated
      using (
        exists (
          select 1
          from public.client_users cu
          where cu.client_id = client_instagram_accounts.client_id
            and cu.auth_user_id = auth.uid()
            and cu.status = 'active'
        )
      );
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'client_entitlements' and policyname = 'client_entitlements_select_own_entitlements'
  ) then
    create policy client_entitlements_select_own_entitlements on public.client_entitlements
      for select
      to authenticated
      using (
        exists (
          select 1
          from public.client_users cu
          where cu.client_id = client_entitlements.client_id
            and cu.auth_user_id = auth.uid()
            and cu.status = 'active'
        )
      );
  end if;
end
$$;

-- =============================================================================
-- 6. Grants
-- =============================================================================
grant select on public.clients to authenticated;
grant select on public.client_users to authenticated;
grant select on public.client_instagram_accounts to authenticated;
grant select on public.client_entitlements to authenticated;

grant select, insert, update, delete on public.clients to service_role;
grant select, insert, update, delete on public.client_users to service_role;
grant select, insert, update, delete on public.client_instagram_accounts to service_role;
grant select, insert, update, delete on public.client_entitlements to service_role;

grant execute on function public.client_can_enqueue_outreach(uuid, uuid) to authenticated, service_role;
grant execute on function public.client_account_has_outreach_entitlement(uuid) to service_role;

revoke execute on function public.enqueue_outreach_dm_job(
  uuid,
  text,
  text,
  uuid,
  public.dm_job_source,
  uuid,
  integer,
  jsonb
) from authenticated;

comment on table public.client_instagram_accounts is
  'Entry 2A ownership mapping from SaaS clients to Instagram accounts. Does not store Instagram credentials.';

comment on table public.client_entitlements is
  'Entry 2A product entitlements. Outreach enqueue requires an active outreach entitlement plus account outreach_enabled=true.';
