-- Entry 2B — account-scoped client subscriptions and product modules.
--
-- Product/billing layer only. This migration intentionally does not create
-- device/clone/timeslot/provisioning/campaign tables and does not change worker
-- flows. Runtime settings remain in ig_account_*_settings and dm counters.

create extension if not exists pgcrypto;

-- =============================================================================
-- 1. Product subscriptions
-- =============================================================================
create table if not exists public.client_subscriptions (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references public.clients (id) on delete cascade,
  subscription_type text not null,
  status text not null default 'active',
  starts_at timestamptz not null default now(),
  ends_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint client_subscriptions_type_check
    check (subscription_type in ('full_cycle', 'outreach_only')),
  constraint client_subscriptions_status_check
    check (status in ('active', 'paused', 'cancelled', 'expired')),
  constraint client_subscriptions_valid_window_check
    check (ends_at is null or ends_at > starts_at)
);

create index if not exists client_subscriptions_client_status_idx
  on public.client_subscriptions (client_id, status, starts_at desc);

create index if not exists client_subscriptions_type_idx
  on public.client_subscriptions (subscription_type);

comment on table public.client_subscriptions is
  'Entry 2B product/billing subscriptions. No runtime quotas, devices, credentials, or provisioning data.';

comment on column public.client_subscriptions.subscription_type is
  'Product type: full_cycle or outreach_only. Device pools/session kinds are assigned in a later Entry 2C.';

-- =============================================================================
-- 2. Account scope for subscriptions
-- =============================================================================
create table if not exists public.client_subscription_accounts (
  id uuid primary key default gen_random_uuid(),
  subscription_id uuid not null references public.client_subscriptions (id) on delete cascade,
  client_instagram_account_id uuid not null references public.client_instagram_accounts (id) on delete cascade,
  account_id uuid not null references public.ig_accounts (id) on delete restrict,
  status text not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint client_subscription_accounts_subscription_account_key unique (subscription_id, account_id),
  constraint client_subscription_accounts_status_check
    check (status in ('active', 'paused', 'removed'))
);

create index if not exists client_subscription_accounts_subscription_idx
  on public.client_subscription_accounts (subscription_id, status);

create index if not exists client_subscription_accounts_account_idx
  on public.client_subscription_accounts (account_id, status);

comment on table public.client_subscription_accounts is
  'Entry 2B account scope for subscriptions. Allows one client to have different package types per Instagram account.';

-- =============================================================================
-- 3. Product modules
-- =============================================================================
create table if not exists public.client_subscription_modules (
  id uuid primary key default gen_random_uuid(),
  subscription_id uuid not null references public.client_subscriptions (id) on delete cascade,
  feature_code text not null,
  enabled boolean not null default true,
  entitlement_type text not null default 'included',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint client_subscription_modules_subscription_feature_key unique (subscription_id, feature_code),
  constraint client_subscription_modules_feature_code_check
    check (feature_code in ('welcome', 'follow', 'unfollow', 'outreach')),
  constraint client_subscription_modules_entitlement_type_check
    check (entitlement_type in ('included', 'addon'))
);

create index if not exists client_subscription_modules_subscription_idx
  on public.client_subscription_modules (subscription_id, enabled);

comment on table public.client_subscription_modules is
  'Entry 2B product modules included in or added onto a subscription. Not a runtime quota table.';

-- =============================================================================
-- 4. Account-scoped runtime entitlement compatibility
-- =============================================================================
alter table public.client_entitlements
  add column if not exists account_id uuid,
  add column if not exists source_subscription_id uuid,
  add column if not exists source_subscription_account_id uuid,
  add column if not exists source_subscription_module_id uuid;

do $$
begin
  if not exists (
    select 1 from information_schema.table_constraints
    where constraint_schema = 'public'
      and table_name = 'client_entitlements'
      and constraint_name = 'client_entitlements_account_id_fkey'
  ) then
    alter table public.client_entitlements
      add constraint client_entitlements_account_id_fkey
      foreign key (account_id) references public.ig_accounts (id)
      on delete cascade;
  end if;

  if not exists (
    select 1 from information_schema.table_constraints
    where constraint_schema = 'public'
      and table_name = 'client_entitlements'
      and constraint_name = 'client_entitlements_source_subscription_id_fkey'
  ) then
    alter table public.client_entitlements
      add constraint client_entitlements_source_subscription_id_fkey
      foreign key (source_subscription_id) references public.client_subscriptions (id)
      on delete set null;
  end if;

  if not exists (
    select 1 from information_schema.table_constraints
    where constraint_schema = 'public'
      and table_name = 'client_entitlements'
      and constraint_name = 'client_entitlements_source_subscription_account_id_fkey'
  ) then
    alter table public.client_entitlements
      add constraint client_entitlements_source_subscription_account_id_fkey
      foreign key (source_subscription_account_id) references public.client_subscription_accounts (id)
      on delete set null;
  end if;

  if not exists (
    select 1 from information_schema.table_constraints
    where constraint_schema = 'public'
      and table_name = 'client_entitlements'
      and constraint_name = 'client_entitlements_source_subscription_module_id_fkey'
  ) then
    alter table public.client_entitlements
      add constraint client_entitlements_source_subscription_module_id_fkey
      foreign key (source_subscription_module_id) references public.client_subscription_modules (id)
      on delete set null;
  end if;
end
$$;

-- Entry 2A had one active entitlement per client+feature. Entry 2B keeps that
-- client-wide form for account_id IS NULL, and adds account-scoped active rows.
drop index if exists public.client_entitlements_one_active_feature_per_client_key;

create unique index if not exists client_entitlements_one_active_client_feature_key
  on public.client_entitlements (client_id, feature_code)
  where active = true and account_id is null;

create unique index if not exists client_entitlements_one_active_account_feature_key
  on public.client_entitlements (client_id, account_id, feature_code)
  where active = true and account_id is not null;

create index if not exists client_entitlements_account_feature_idx
  on public.client_entitlements (account_id, feature_code, active)
  where account_id is not null;

create index if not exists client_entitlements_source_subscription_idx
  on public.client_entitlements (source_subscription_id, source_subscription_module_id);

comment on column public.client_entitlements.account_id is
  'Nullable Entry 2B account scope. NULL keeps Entry 2A client-wide entitlement semantics for backward compatibility.';

comment on column public.client_entitlements.source_subscription_id is
  'Audit link to the product subscription that produced this runtime entitlement, when synced.';

-- =============================================================================
-- 5. updated_at triggers
-- =============================================================================
drop trigger if exists client_subscriptions_set_updated_at on public.client_subscriptions;
create trigger client_subscriptions_set_updated_at
  before update on public.client_subscriptions
  for each row execute function public.set_updated_at();

drop trigger if exists client_subscription_accounts_set_updated_at on public.client_subscription_accounts;
create trigger client_subscription_accounts_set_updated_at
  before update on public.client_subscription_accounts
  for each row execute function public.set_updated_at();

drop trigger if exists client_subscription_modules_set_updated_at on public.client_subscription_modules;
create trigger client_subscription_modules_set_updated_at
  before update on public.client_subscription_modules
  for each row execute function public.set_updated_at();

-- =============================================================================
-- 6. Validation triggers
-- =============================================================================
create or replace function public.validate_client_subscription_account()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_subscription_client_id uuid;
  v_account_client_id uuid;
  v_account_id uuid;
begin
  select cs.client_id
  into v_subscription_client_id
  from public.client_subscriptions cs
  where cs.id = new.subscription_id;

  select cia.client_id, cia.account_id
  into v_account_client_id, v_account_id
  from public.client_instagram_accounts cia
  where cia.id = new.client_instagram_account_id;

  if v_subscription_client_id is null then
    raise exception 'client_subscription_accounts.subscription_id does not reference an existing subscription';
  end if;

  if v_account_client_id is null then
    raise exception 'client_subscription_accounts.client_instagram_account_id does not reference an existing client account';
  end if;

  if v_subscription_client_id <> v_account_client_id then
    raise exception 'subscription and client_instagram_account must belong to the same client';
  end if;

  if new.account_id <> v_account_id then
    raise exception 'client_subscription_accounts.account_id must match client_instagram_accounts.account_id';
  end if;

  return new;
end;
$$;

drop trigger if exists client_subscription_accounts_validate on public.client_subscription_accounts;
create trigger client_subscription_accounts_validate
  before insert or update on public.client_subscription_accounts
  for each row execute function public.validate_client_subscription_account();

create or replace function public.validate_client_subscription_module()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_subscription_type text;
begin
  select cs.subscription_type
  into v_subscription_type
  from public.client_subscriptions cs
  where cs.id = new.subscription_id;

  if v_subscription_type is null then
    raise exception 'client_subscription_modules.subscription_id does not reference an existing subscription';
  end if;

  if v_subscription_type = 'outreach_only'
     and new.enabled = true
     and new.feature_code <> 'outreach' then
    raise exception 'outreach_only subscriptions can only enable the outreach module';
  end if;

  return new;
end;
$$;

drop trigger if exists client_subscription_modules_validate on public.client_subscription_modules;
create trigger client_subscription_modules_validate
  before insert or update on public.client_subscription_modules
  for each row execute function public.validate_client_subscription_module();

create or replace function public.validate_client_subscription_type()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
begin
  if new.subscription_type = 'outreach_only'
     and exists (
       select 1
       from public.client_subscription_modules csm
       where csm.subscription_id = new.id
         and csm.enabled = true
         and csm.feature_code <> 'outreach'
     ) then
    raise exception 'cannot set subscription_type=outreach_only while non-outreach modules are enabled';
  end if;

  return new;
end;
$$;

drop trigger if exists client_subscriptions_validate_type on public.client_subscriptions;
create trigger client_subscriptions_validate_type
  before update of subscription_type on public.client_subscriptions
  for each row execute function public.validate_client_subscription_type();

-- =============================================================================
-- 7. Explicit sync from product subscription to runtime entitlements
-- =============================================================================
create or replace function public.sync_client_subscription_entitlements(
  p_subscription_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_subscription public.client_subscriptions%rowtype;
  v_subscription_active boolean;
  v_row record;
  v_entitlement_type text;
  v_upserted integer := 0;
  v_disabled integer := 0;
begin
  select *
  into v_subscription
  from public.client_subscriptions
  where id = p_subscription_id;

  if not found then
    raise exception 'subscription not found: %', p_subscription_id;
  end if;

  v_subscription_active :=
    v_subscription.status = 'active'
    and v_subscription.starts_at <= now()
    and (v_subscription.ends_at is null or v_subscription.ends_at > now());

  for v_row in
    select
      csa.id as subscription_account_id,
      csa.account_id,
      csa.status as account_status,
      csm.id as subscription_module_id,
      csm.feature_code,
      csm.enabled,
      csm.entitlement_type
    from public.client_subscription_accounts csa
    join public.client_subscription_modules csm
      on csm.subscription_id = csa.subscription_id
    where csa.subscription_id = p_subscription_id
  loop
    v_entitlement_type := case
      when v_row.entitlement_type = 'addon' then 'addon'
      else 'bundle'
    end;

    if v_subscription_active
       and v_row.account_status = 'active'
       and v_row.enabled = true then
      -- Prefer an existing row from this subscription/module/account.
      update public.client_entitlements
      set active = true,
          entitlement_type = v_entitlement_type,
          valid_from = v_subscription.starts_at,
          valid_until = v_subscription.ends_at,
          metadata = jsonb_set(
            jsonb_set(
              coalesce(metadata, '{}'::jsonb),
              '{source}',
              '"client_subscription"'::jsonb,
              true
            ),
            '{subscription_type}',
            to_jsonb(v_subscription.subscription_type),
            true
          ),
          updated_at = now()
      where client_id = v_subscription.client_id
        and account_id = v_row.account_id
        and feature_code = v_row.feature_code
        and source_subscription_id = p_subscription_id
        and source_subscription_account_id = v_row.subscription_account_id
        and source_subscription_module_id = v_row.subscription_module_id;

      if not found then
        -- If a manual active account-scoped entitlement already exists, attach
        -- it to this subscription instead of creating a second active row.
        update public.client_entitlements
        set entitlement_type = v_entitlement_type,
            valid_from = v_subscription.starts_at,
            valid_until = v_subscription.ends_at,
            metadata = jsonb_set(
              jsonb_set(
                coalesce(metadata, '{}'::jsonb),
                '{source}',
                '"client_subscription"'::jsonb,
                true
              ),
              '{subscription_type}',
              to_jsonb(v_subscription.subscription_type),
              true
            ),
            source_subscription_id = p_subscription_id,
            source_subscription_account_id = v_row.subscription_account_id,
            source_subscription_module_id = v_row.subscription_module_id,
            updated_at = now()
        where client_id = v_subscription.client_id
          and account_id = v_row.account_id
          and feature_code = v_row.feature_code
          and active = true;

        if not found then
          insert into public.client_entitlements (
            client_id,
            account_id,
            feature_code,
            entitlement_type,
            active,
            valid_from,
            valid_until,
            metadata,
            source_subscription_id,
            source_subscription_account_id,
            source_subscription_module_id
          )
          values (
            v_subscription.client_id,
            v_row.account_id,
            v_row.feature_code,
            v_entitlement_type,
            true,
            v_subscription.starts_at,
            v_subscription.ends_at,
            jsonb_build_object(
              'source', 'client_subscription',
              'subscription_type', v_subscription.subscription_type
            ),
            p_subscription_id,
            v_row.subscription_account_id,
            v_row.subscription_module_id
          );
        end if;
      end if;

      v_upserted := v_upserted + 1;
    end if;
  end loop;

  update public.client_entitlements ce
  set active = false,
      updated_at = now()
  where ce.source_subscription_id = p_subscription_id
    and ce.active = true
    and not exists (
      select 1
      from public.client_subscription_accounts csa
      join public.client_subscription_modules csm
        on csm.subscription_id = csa.subscription_id
      where csa.subscription_id = p_subscription_id
        and csa.id = ce.source_subscription_account_id
        and csm.id = ce.source_subscription_module_id
        and csa.account_id = ce.account_id
        and csm.feature_code = ce.feature_code
        and v_subscription_active = true
        and csa.status = 'active'
        and csm.enabled = true
    );

  get diagnostics v_disabled = row_count;

  return jsonb_build_object(
    'ok', true,
    'subscription_id', p_subscription_id,
    'subscription_active', v_subscription_active,
    'entitlements_upserted', v_upserted,
    'entitlements_disabled', v_disabled
  );
end;
$$;

comment on function public.sync_client_subscription_entitlements(uuid) is
  'Entry 2B explicit admin/service-role sync from product subscription modules to runtime client_entitlements. Not a trigger.';

revoke all on function public.sync_client_subscription_entitlements(uuid) from public;
revoke all on function public.sync_client_subscription_entitlements(uuid) from authenticated;

-- =============================================================================
-- 8. Outreach entitlement helpers remain backward compatible
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
     and (ce.account_id is null or ce.account_id = p_account_id)
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
     and (ce.account_id is null or ce.account_id = p_account_id)
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
-- 9. RLS and grants
-- =============================================================================
alter table public.client_subscriptions enable row level security;
alter table public.client_subscription_accounts enable row level security;
alter table public.client_subscription_modules enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'client_subscriptions' and policyname = 'client_subscriptions_service_role_all'
  ) then
    create policy client_subscriptions_service_role_all on public.client_subscriptions
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'client_subscription_accounts' and policyname = 'client_subscription_accounts_service_role_all'
  ) then
    create policy client_subscription_accounts_service_role_all on public.client_subscription_accounts
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'client_subscription_modules' and policyname = 'client_subscription_modules_service_role_all'
  ) then
    create policy client_subscription_modules_service_role_all on public.client_subscription_modules
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'client_subscriptions' and policyname = 'client_subscriptions_select_own'
  ) then
    create policy client_subscriptions_select_own on public.client_subscriptions
      for select
      to authenticated
      using (
        exists (
          select 1
          from public.client_users cu
          where cu.client_id = client_subscriptions.client_id
            and cu.auth_user_id = auth.uid()
            and cu.status = 'active'
        )
      );
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'client_subscription_accounts' and policyname = 'client_subscription_accounts_select_own'
  ) then
    create policy client_subscription_accounts_select_own on public.client_subscription_accounts
      for select
      to authenticated
      using (
        exists (
          select 1
          from public.client_subscriptions cs
          join public.client_users cu
            on cu.client_id = cs.client_id
           and cu.auth_user_id = auth.uid()
           and cu.status = 'active'
          where cs.id = client_subscription_accounts.subscription_id
        )
      );
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'client_subscription_modules' and policyname = 'client_subscription_modules_select_own'
  ) then
    create policy client_subscription_modules_select_own on public.client_subscription_modules
      for select
      to authenticated
      using (
        exists (
          select 1
          from public.client_subscriptions cs
          join public.client_users cu
            on cu.client_id = cs.client_id
           and cu.auth_user_id = auth.uid()
           and cu.status = 'active'
          where cs.id = client_subscription_modules.subscription_id
        )
      );
  end if;
end
$$;

grant select on public.client_subscriptions to authenticated;
grant select on public.client_subscription_accounts to authenticated;
grant select on public.client_subscription_modules to authenticated;

grant select, insert, update, delete on public.client_subscriptions to service_role;
grant select, insert, update, delete on public.client_subscription_accounts to service_role;
grant select, insert, update, delete on public.client_subscription_modules to service_role;

grant execute on function public.validate_client_subscription_account() to service_role;
grant execute on function public.validate_client_subscription_module() to service_role;
grant execute on function public.validate_client_subscription_type() to service_role;
grant execute on function public.sync_client_subscription_entitlements(uuid) to service_role;

comment on table public.client_subscription_modules is
  'Entry 2B product modules. outreach_only subscriptions may only enable outreach; full_cycle may enable welcome/follow/unfollow/outreach.';
