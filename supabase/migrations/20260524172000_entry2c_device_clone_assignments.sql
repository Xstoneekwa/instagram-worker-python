-- Entry 2C — device / clone / account assignment model.
--
-- Capacity and assignment layer only. This migration intentionally does not
-- create credentials, auto-login/provisioning jobs, campaign/import tables,
-- scheduler dispatch, phone rest runtime, or worker flow changes.

create extension if not exists pgcrypto;

-- =============================================================================
-- 1. Device inventory
-- =============================================================================
create table if not exists public.phone_devices (
  id uuid primary key default gen_random_uuid(),
  device_kind text not null,
  name text not null,
  device_name text,
  adb_serial text,
  device_udid text,
  host_machine text,
  hub_label text,
  hub_port text,
  pool_type text not null,
  max_clones integer not null default 4,
  status text not null default 'available',
  status_reason text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint phone_devices_kind_check
    check (device_kind in ('emulator', 'physical_phone')),
  constraint phone_devices_name_nonempty
    check (char_length(trim(name)) > 0),
  constraint phone_devices_pool_type_check
    check (pool_type in ('full_cycle', 'outreach_only', 'shared')),
  constraint phone_devices_status_check
    check (status in ('available', 'reserved', 'active', 'maintenance', 'offline', 'unauthorized', 'disabled')),
  constraint phone_devices_max_clones_positive
    check (max_clones >= 1),
  constraint phone_devices_adb_serial_nonempty
    check (adb_serial is null or char_length(trim(adb_serial)) > 0),
  constraint phone_devices_device_udid_nonempty
    check (device_udid is null or char_length(trim(device_udid)) > 0)
);

create unique index if not exists phone_devices_adb_serial_key
  on public.phone_devices (adb_serial)
  where adb_serial is not null;

create unique index if not exists phone_devices_device_udid_key
  on public.phone_devices (device_udid)
  where device_udid is not null;

create index if not exists phone_devices_pool_status_idx
  on public.phone_devices (pool_type, status);

create index if not exists phone_devices_kind_status_idx
  on public.phone_devices (device_kind, status);

create index if not exists phone_devices_host_hub_port_idx
  on public.phone_devices (host_machine, hub_label, hub_port);

comment on table public.phone_devices is
  'Entry 2C admin-only inventory for emulator and physical-phone capacity. Never expose adb_serial/device_udid/hub details to client dashboards.';

comment on column public.phone_devices.device_kind is
  'Device class: emulator for dev/test (e.g. emulator-5554) or physical_phone for production USB phone farm.';

comment on column public.phone_devices.pool_type is
  'Assignment pool: full_cycle, outreach_only, or shared. Worker dispatch is added later.';

comment on column public.phone_devices.adb_serial is
  'ADB serial for operator/worker routing. Not a durable identity by itself and not client-visible.';

comment on column public.phone_devices.device_udid is
  'Optional ops identifier, useful for legacy ig_accounts.device_udid compatibility. Not client-visible.';

comment on column public.phone_devices.hub_port is
  'Physical USB hub port label for production phone farm operations. Not client-visible.';

-- =============================================================================
-- 2. Clone inventory
-- =============================================================================
create table if not exists public.phone_clones (
  id uuid primary key default gen_random_uuid(),
  device_id uuid not null references public.phone_devices (id) on delete cascade,
  clone_index integer not null,
  clone_label text,
  status text not null default 'available',
  current_account_id uuid references public.ig_accounts (id) on delete set null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint phone_clones_device_clone_key unique (device_id, clone_index),
  constraint phone_clones_clone_index_positive
    check (clone_index >= 1),
  constraint phone_clones_status_check
    check (status in ('available', 'reserved', 'active', 'maintenance', 'disabled'))
);

create index if not exists phone_clones_device_status_idx
  on public.phone_clones (device_id, status);

create index if not exists phone_clones_current_account_idx
  on public.phone_clones (current_account_id)
  where current_account_id is not null;

comment on table public.phone_clones is
  'Entry 2C admin-only clone/app-profile inventory. Compatible with emulator clones and physical-phone app instances.';

comment on column public.phone_clones.current_account_id is
  'Optional admin denormalization. Source of truth for assignment remains account_assignments.';

-- =============================================================================
-- 3. Account assignments
-- =============================================================================
create table if not exists public.account_assignments (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references public.clients (id) on delete cascade,
  subscription_id uuid not null references public.client_subscriptions (id) on delete cascade,
  subscription_account_id uuid not null references public.client_subscription_accounts (id) on delete cascade,
  account_id uuid not null references public.ig_accounts (id) on delete restrict,
  device_id uuid not null references public.phone_devices (id) on delete restrict,
  clone_id uuid not null references public.phone_clones (id) on delete restrict,
  assignment_type text not null,
  slot_kind text not null,
  status text not null default 'pending',
  starts_at timestamptz not null,
  ends_at timestamptz not null,
  assigned_at timestamptz,
  released_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint account_assignments_type_check
    check (assignment_type in ('full_cycle', 'outreach_only')),
  constraint account_assignments_slot_kind_check
    check (slot_kind in ('full_cycle_6h', 'outreach_short')),
  constraint account_assignments_status_check
    check (status in ('pending', 'reserved', 'active', 'paused', 'failed', 'released')),
  constraint account_assignments_valid_window_check
    check (ends_at > starts_at),
  constraint account_assignments_release_check
    check (released_at is null or released_at >= assigned_at or assigned_at is null)
);

create unique index if not exists account_assignments_one_open_assignment_per_account_key
  on public.account_assignments (account_id)
  where status in ('pending', 'reserved', 'active');

create index if not exists account_assignments_client_status_idx
  on public.account_assignments (client_id, status, starts_at desc);

create index if not exists account_assignments_subscription_account_idx
  on public.account_assignments (subscription_account_id, status);

create index if not exists account_assignments_clone_window_idx
  on public.account_assignments (clone_id, starts_at, ends_at)
  where status in ('pending', 'reserved', 'active');

create index if not exists account_assignments_device_status_idx
  on public.account_assignments (device_id, status);

comment on table public.account_assignments is
  'Entry 2C assignment of an account-scoped subscription to a device clone and reserved time window. Does not dispatch workers or provision logins.';

comment on column public.account_assignments.slot_kind is
  'Scheduling hint only in Entry 2C: full_cycle_6h or outreach_short. Runtime enforcement is added later.';

-- =============================================================================
-- 4. updated_at triggers
-- =============================================================================
drop trigger if exists phone_devices_set_updated_at on public.phone_devices;
create trigger phone_devices_set_updated_at
  before update on public.phone_devices
  for each row execute function public.set_updated_at();

drop trigger if exists phone_clones_set_updated_at on public.phone_clones;
create trigger phone_clones_set_updated_at
  before update on public.phone_clones
  for each row execute function public.set_updated_at();

drop trigger if exists account_assignments_set_updated_at on public.account_assignments;
create trigger account_assignments_set_updated_at
  before update on public.account_assignments
  for each row execute function public.set_updated_at();

-- =============================================================================
-- 5. Validation triggers
-- =============================================================================
create or replace function public.validate_phone_clone_capacity()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_max_clones integer;
begin
  select pd.max_clones
  into v_max_clones
  from public.phone_devices pd
  where pd.id = new.device_id;

  if v_max_clones is null then
    raise exception 'phone_clones.device_id does not reference an existing phone device';
  end if;

  if new.clone_index > v_max_clones then
    raise exception 'phone_clones.clone_index exceeds phone_devices.max_clones';
  end if;

  return new;
end;
$$;

drop trigger if exists phone_clones_validate_capacity on public.phone_clones;
create trigger phone_clones_validate_capacity
  before insert or update of device_id, clone_index on public.phone_clones
  for each row execute function public.validate_phone_clone_capacity();

create or replace function public.validate_account_assignment()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_subscription_client_id uuid;
  v_subscription_type text;
  v_subscription_account_id uuid;
  v_subscription_account_account_id uuid;
  v_clone_device_id uuid;
  v_device_pool_type text;
begin
  select cs.client_id, cs.subscription_type
  into v_subscription_client_id, v_subscription_type
  from public.client_subscriptions cs
  where cs.id = new.subscription_id;

  if v_subscription_client_id is null then
    raise exception 'account_assignments.subscription_id does not reference an existing subscription';
  end if;

  select csa.id, csa.account_id
  into v_subscription_account_id, v_subscription_account_account_id
  from public.client_subscription_accounts csa
  where csa.id = new.subscription_account_id
    and csa.subscription_id = new.subscription_id;

  if v_subscription_account_id is null then
    raise exception 'account_assignments.subscription_account_id must belong to subscription_id';
  end if;

  if new.client_id <> v_subscription_client_id then
    raise exception 'account_assignments.client_id must match client_subscriptions.client_id';
  end if;

  if new.account_id <> v_subscription_account_account_id then
    raise exception 'account_assignments.account_id must match client_subscription_accounts.account_id';
  end if;

  if new.assignment_type <> v_subscription_type then
    raise exception 'account_assignments.assignment_type must match client_subscriptions.subscription_type';
  end if;

  if new.assignment_type = 'full_cycle' and new.slot_kind <> 'full_cycle_6h' then
    raise exception 'full_cycle assignments require slot_kind=full_cycle_6h';
  end if;

  if new.assignment_type = 'outreach_only' and new.slot_kind <> 'outreach_short' then
    raise exception 'outreach_only assignments require slot_kind=outreach_short';
  end if;

  select pc.device_id
  into v_clone_device_id
  from public.phone_clones pc
  where pc.id = new.clone_id;

  if v_clone_device_id is null then
    raise exception 'account_assignments.clone_id does not reference an existing clone';
  end if;

  if new.device_id <> v_clone_device_id then
    raise exception 'account_assignments.device_id must match phone_clones.device_id';
  end if;

  select pd.pool_type
  into v_device_pool_type
  from public.phone_devices pd
  where pd.id = new.device_id;

  if v_device_pool_type is null then
    raise exception 'account_assignments.device_id does not reference an existing phone device';
  end if;

  if v_device_pool_type <> 'shared' and v_device_pool_type <> new.assignment_type then
    raise exception 'phone_devices.pool_type is incompatible with account_assignments.assignment_type';
  end if;

  if new.status in ('pending', 'reserved', 'active') and exists (
    select 1
    from public.account_assignments aa
    where aa.clone_id = new.clone_id
      and aa.status in ('pending', 'reserved', 'active')
      and aa.id <> coalesce(new.id, '00000000-0000-0000-0000-000000000000'::uuid)
      and tstzrange(aa.starts_at, aa.ends_at, '[)') && tstzrange(new.starts_at, new.ends_at, '[)')
  ) then
    raise exception 'phone clone already has an overlapping open assignment';
  end if;

  return new;
end;
$$;

drop trigger if exists account_assignments_validate on public.account_assignments;
create trigger account_assignments_validate
  before insert or update on public.account_assignments
  for each row execute function public.validate_account_assignment();

-- =============================================================================
-- 6. RLS and grants
-- =============================================================================
alter table public.phone_devices enable row level security;
alter table public.phone_clones enable row level security;
alter table public.account_assignments enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'phone_devices' and policyname = 'phone_devices_service_role_all'
  ) then
    create policy phone_devices_service_role_all on public.phone_devices
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'phone_clones' and policyname = 'phone_clones_service_role_all'
  ) then
    create policy phone_clones_service_role_all on public.phone_clones
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'account_assignments' and policyname = 'account_assignments_service_role_all'
  ) then
    create policy account_assignments_service_role_all on public.account_assignments
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'account_assignments' and policyname = 'account_assignments_select_own'
  ) then
    create policy account_assignments_select_own on public.account_assignments
      for select
      to authenticated
      using (
        exists (
          select 1
          from public.client_users cu
          where cu.client_id = account_assignments.client_id
            and cu.auth_user_id = auth.uid()
            and cu.status = 'active'
        )
      );
  end if;
end
$$;

grant select, insert, update, delete on public.phone_devices to service_role;
grant select, insert, update, delete on public.phone_clones to service_role;
grant select, insert, update, delete on public.account_assignments to service_role;

grant select on public.account_assignments to authenticated;

revoke all on public.phone_devices from authenticated;
revoke all on public.phone_clones from authenticated;

revoke all on function public.validate_phone_clone_capacity() from public;
revoke all on function public.validate_account_assignment() from public;
revoke all on function public.validate_phone_clone_capacity() from authenticated;
revoke all on function public.validate_account_assignment() from authenticated;

grant execute on function public.validate_phone_clone_capacity() to service_role;
grant execute on function public.validate_account_assignment() to service_role;
