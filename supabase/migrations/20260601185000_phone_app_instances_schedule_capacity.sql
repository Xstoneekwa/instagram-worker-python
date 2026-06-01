-- Phone app instances model for Schedule / auto-login capacity.
--
-- Product rule:
-- - A phone has assignable Instagram app instances, not only clones.
-- - The primary Instagram app is assignable when free.
-- - An instance is unavailable only when occupied, disabled, unknown, absent,
--   not launchable, or blocked by ops.
-- - Paused, needs_assistance, and runtime stopped keep assignments, slots, and
--   app instances. They are not release triggers.
-- - Keep reasons are account_paused_keep_assignment,
--   account_needs_assistance_keep_assignment, and runtime_stopped_keep_assignment.
-- - phone_clones remains a legacy compatibility table during migration.

create extension if not exists pgcrypto;

-- =============================================================================
-- 1. App instance inventory
-- =============================================================================
create table if not exists public.phone_app_instances (
  id uuid primary key default gen_random_uuid(),
  device_id uuid not null references public.phone_devices (id) on delete cascade,
  instance_type text not null,
  instance_index integer not null,
  visible_label text not null,
  package_name text,
  launch_activity text,
  is_launchable boolean not null default true,
  status text not null default 'unknown',
  current_account_id uuid references public.ig_accounts (id) on delete set null,
  usable_for_auto_login boolean not null default true,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint phone_app_instances_type_check
    check (instance_type in ('primary_app', 'clone')),
  constraint phone_app_instances_index_check
    check (
      (instance_type = 'primary_app' and instance_index = 0)
      or
      (instance_type = 'clone' and instance_index >= 1)
    ),
  constraint phone_app_instances_status_check
    check (status in ('available', 'occupied', 'disabled', 'unknown')),
  constraint phone_app_instances_visible_label_nonempty
    check (char_length(trim(visible_label)) > 0),
  constraint phone_app_instances_metadata_object_check
    check (jsonb_typeof(metadata) = 'object')
);

create unique index if not exists phone_app_instances_device_index_key
  on public.phone_app_instances (device_id, instance_index);

create unique index if not exists phone_app_instances_device_package_key
  on public.phone_app_instances (device_id, package_name)
  where package_name is not null and trim(package_name) <> '';

create index if not exists phone_app_instances_device_status_idx
  on public.phone_app_instances (device_id, status, usable_for_auto_login, is_launchable);

create index if not exists phone_app_instances_current_account_idx
  on public.phone_app_instances (current_account_id)
  where current_account_id is not null;

comment on table public.phone_app_instances is
  'Assignable Instagram app instances on a phone: primary app plus clone apps. The primary app is assignable when free.';

comment on column public.phone_app_instances.instance_index is
  '0 = primary Instagram app; 1+ = Instagram clone/app profile.';

comment on column public.phone_app_instances.status is
  'available, occupied, disabled, or unknown. Only available + launchable + usable_for_auto_login + no current account is assignable.';

drop trigger if exists phone_app_instances_set_updated_at on public.phone_app_instances;
create trigger phone_app_instances_set_updated_at
  before update on public.phone_app_instances
  for each row execute function public.set_updated_at();

alter table public.phone_app_instances enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public'
      and tablename = 'phone_app_instances'
      and policyname = 'phone_app_instances_service_role_all'
  ) then
    create policy phone_app_instances_service_role_all on public.phone_app_instances
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;
end
$$;

grant select, insert, update, delete on public.phone_app_instances to service_role;
revoke all on public.phone_app_instances from anon;
revoke all on public.phone_app_instances from authenticated;

-- Backfill legacy clone rows as app instances. This does not invent primary apps
-- or missing clones; real inventory rows must come from an operator/device audit.
insert into public.phone_app_instances (
  id,
  device_id,
  instance_type,
  instance_index,
  visible_label,
  package_name,
  launch_activity,
  is_launchable,
  status,
  current_account_id,
  usable_for_auto_login,
  metadata,
  created_at,
  updated_at
)
select
  pc.id,
  pc.device_id,
  'clone',
  pc.clone_index,
  coalesce(nullif(trim(pc.clone_label), ''), 'Instagram ' || pc.clone_index::text),
  nullif(trim(pc.metadata ->> 'package_name'), ''),
  nullif(trim(pc.metadata ->> 'launch_activity'), ''),
  case
    when lower(coalesce(pc.metadata ->> 'is_launchable', '')) in ('true', 't', '1', 'yes') then true
    when lower(coalesce(pc.metadata ->> 'is_launchable', '')) in ('false', 'f', '0', 'no') then false
    else true
  end,
  case
    when pc.status in ('reserved', 'active') or pc.current_account_id is not null then 'occupied'
    when pc.status in ('maintenance', 'disabled') then 'disabled'
    else 'available'
  end,
  pc.current_account_id,
  case
    when pc.status in ('maintenance', 'disabled') then false
    when lower(coalesce(pc.metadata ->> 'usable_for_auto_login', '')) in ('true', 't', '1', 'yes') then true
    when lower(coalesce(pc.metadata ->> 'usable_for_auto_login', '')) in ('false', 'f', '0', 'no') then false
    else true
  end,
  coalesce(pc.metadata, '{}'::jsonb) || jsonb_build_object(
    'legacy_phone_clones_id', pc.id,
    'legacy_clone_index', pc.clone_index,
    'inventory_source', 'phone_clones_backfill'
  ),
  pc.created_at,
  pc.updated_at
from public.phone_clones pc
on conflict (device_id, instance_index) do update
set visible_label = excluded.visible_label,
    package_name = coalesce(public.phone_app_instances.package_name, excluded.package_name),
    launch_activity = coalesce(public.phone_app_instances.launch_activity, excluded.launch_activity),
    is_launchable = excluded.is_launchable,
    status = case
      when public.phone_app_instances.status = 'disabled' then 'disabled'
      else excluded.status
    end,
    current_account_id = excluded.current_account_id,
    usable_for_auto_login = excluded.usable_for_auto_login,
    metadata = public.phone_app_instances.metadata || excluded.metadata,
    updated_at = now();

-- =============================================================================
-- 2. Assignment compatibility
-- =============================================================================
alter table public.account_assignments
  add column if not exists app_instance_id uuid;

alter table public.account_assignments
  drop constraint if exists account_assignments_app_instance_id_fkey;

alter table public.account_assignments
  add constraint account_assignments_app_instance_id_fkey
  foreign key (app_instance_id) references public.phone_app_instances (id) on delete restrict;

alter table public.account_assignments
  alter column clone_id drop not null;

alter table public.account_assignments
  drop constraint if exists account_assignments_status_check;

alter table public.account_assignments
  add constraint account_assignments_status_check
  check (status in (
    'pending',
    'reserved',
    'active',
    'paused',
    'failed',
    'released',
    'canceled',
    'expired',
    'archived'
  ));

create index if not exists account_assignments_app_instance_open_idx
  on public.account_assignments (app_instance_id, starts_at, ends_at)
  where status in ('pending', 'reserved', 'active');

alter table public.account_assignments disable trigger account_assignments_validate;

update public.account_assignments aa
set app_instance_id = aa.clone_id,
    metadata = coalesce(aa.metadata, '{}'::jsonb) || jsonb_build_object(
      'app_instance_backfill_source', 'legacy_clone_id'
    ),
    updated_at = now()
where aa.app_instance_id is null
  and aa.clone_id is not null
  and exists (
    select 1
    from public.phone_app_instances pai
    where pai.id = aa.clone_id
  );

alter table public.account_assignments enable trigger account_assignments_validate;

-- =============================================================================
-- 3. Capacity helpers and release rules
-- =============================================================================
create or replace function public.is_open_assignment_status(p_status text)
returns boolean
language sql
immutable
as $$
  select coalesce(p_status, '') in ('pending', 'reserved', 'active');
$$;

create or replace function public.is_terminal_assignment_status(p_status text)
returns boolean
language sql
immutable
as $$
  select coalesce(p_status, '') in ('released', 'canceled', 'expired', 'archived');
$$;

create or replace function public.account_has_active_runtime_session(p_account_id uuid)
returns boolean
language plpgsql
stable
set search_path = public
as $$
declare
  v_has_active boolean := false;
begin
  if p_account_id is null then
    return false;
  end if;

  if to_regclass('public.ig_runs') is not null then
    begin
      execute $query$
        select exists (
          select 1
          from public.ig_runs
          where account_id = $1
            and status in ('queued', 'pending', 'starting', 'running', 'in_progress', 'active')
        )
      $query$ into v_has_active using p_account_id;
      if v_has_active then
        return true;
      end if;
    exception
      when undefined_table or undefined_column then
        null;
    end;
  end if;

  if to_regclass('public.account_run_requests') is not null then
    begin
      execute $query$
        select exists (
          select 1
          from public.account_run_requests
          where account_id = $1
            and status in ('queued', 'claimed', 'starting', 'running', 'in_progress')
        )
      $query$ into v_has_active using p_account_id;
      if v_has_active then
        return true;
      end if;
    exception
      when undefined_table or undefined_column then
        null;
    end;
  end if;

  return false;
end;
$$;

create or replace function public.release_app_instance_if_unused(
  p_app_instance_id uuid,
  p_account_id uuid,
  p_reason text,
  p_source text default 'system'
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_updated_count integer := 0;
begin
  if p_app_instance_id is null then
    return false;
  end if;

  update public.phone_app_instances pai
  set status = 'available',
      current_account_id = null,
      metadata = coalesce(pai.metadata, '{}'::jsonb) || jsonb_build_object(
        'last_release_reason', p_reason,
        'last_release_source', p_source,
        'last_released_at', now()
      ),
      updated_at = now()
  where pai.id = p_app_instance_id
    and pai.status = 'occupied'
    and (pai.current_account_id = p_account_id or pai.current_account_id is null)
    and not exists (
      select 1
      from public.account_assignments aa
      where aa.app_instance_id = pai.id
        and aa.status in ('pending', 'reserved', 'active')
    );

  get diagnostics v_updated_count = row_count;

  if v_updated_count > 0 then
    update public.phone_clones pc
    set status = 'available',
        current_account_id = null,
        updated_at = now()
    where (pc.id = p_app_instance_id or exists (
        select 1
        from public.phone_app_instances pai
        where pai.id = p_app_instance_id
          and pai.metadata ->> 'legacy_phone_clones_id' = pc.id::text
      ))
      and (pc.current_account_id = p_account_id or pc.current_account_id is null);
  end if;

  return v_updated_count > 0;
end;
$$;

create or replace function public.audit_schedule_capacity_event(
  p_event_type text,
  p_account_id uuid,
  p_assignment_id uuid,
  p_app_instance_id uuid,
  p_reason text,
  p_metadata jsonb default '{}'::jsonb
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.runtime_events (
    event_type,
    severity,
    visibility,
    account_id,
    assignment_id,
    reason,
    message,
    metadata
  )
  values (
    p_event_type,
    'info',
    'admin_only',
    p_account_id,
    p_assignment_id,
    p_reason,
    'Schedule app instance capacity updated.',
    coalesce(p_metadata, '{}'::jsonb) || jsonb_build_object(
      'app_instance_id', p_app_instance_id,
      'safe_audit', true
    )
  );
end;
$$;

create or replace function public.release_account_schedule_capacity(
  p_account_id uuid,
  p_reason text,
  p_source text default 'system',
  p_actor_id uuid default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_reason text := coalesce(nullif(trim(p_reason), ''), 'manual_assignment_release');
  v_release_status text := 'released';
  v_assignment record;
  v_released_count integer := 0;
  v_instance_released_count integer := 0;
  v_instance_released boolean;
begin
  if p_account_id is null then
    raise exception 'missing_account_id';
  end if;

  if public.account_has_active_runtime_session(p_account_id) then
    perform public.audit_schedule_capacity_event(
      'schedule_capacity_release_skipped',
      p_account_id,
      null,
      null,
      'active_session_guard',
      jsonb_build_object(
        'requested_reason', v_reason,
        'source', p_source,
        'released_by', coalesce(p_actor_id::text, 'system')
      )
    );
    return jsonb_build_object(
      'ok', false,
      'reason', 'active_session_guard',
      'released_count', 0,
      'app_instances_released_count', 0
    );
  end if;

  v_release_status := case
    when v_reason = 'account_canceled_release' then 'canceled'
    when v_reason = 'account_cancelled_release' then 'canceled'
    when v_reason = 'account_archived_release' then 'archived'
    when v_reason = 'assignment_expired_release' then 'expired'
    when v_reason = 'clone_reset_release' then 'released'
    else 'released'
  end;

  for v_assignment in
    select *
    from public.account_assignments aa
    where aa.account_id = p_account_id
      and aa.status in ('pending', 'reserved', 'active')
    order by aa.created_at desc
    for update
  loop
    update public.account_assignments
    set status = v_release_status,
        released_at = coalesce(released_at, now()),
        metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object(
          'release_reason', v_reason,
          'release_source', p_source,
          'released_by', coalesce(p_actor_id::text, 'system'),
          'old_status', v_assignment.status,
          'new_status', v_release_status
        ),
        updated_at = now()
    where id = v_assignment.id;

    v_released_count := v_released_count + 1;

    select exists (
      select 1
      from public.phone_app_instances pai
      where pai.id = v_assignment.app_instance_id
        and pai.status = 'available'
        and pai.current_account_id is null
    )
    into v_instance_released;

    if v_instance_released then
      v_instance_released_count := v_instance_released_count + 1;
    end if;

    perform public.audit_schedule_capacity_event(
      'schedule_assignment_capacity_released',
      p_account_id,
      v_assignment.id,
      v_assignment.app_instance_id,
      v_reason,
      jsonb_build_object(
        'source', p_source,
        'released_by', coalesce(p_actor_id::text, 'system'),
        'old_status', v_assignment.status,
        'new_status', v_release_status,
        'app_instance_released', v_instance_released
      )
    );
  end loop;

  return jsonb_build_object(
    'ok', true,
    'reason', case when v_released_count = 0 then 'no_open_assignment_after_release' else v_reason end,
    'released_count', v_released_count,
    'app_instances_released_count', v_instance_released_count
  );
end;
$$;

create or replace function public.sync_app_instance_after_assignment_change()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_old_open boolean := false;
  v_new_open boolean := false;
  v_reason text := 'manual_assignment_release';
  v_instance_released boolean := false;
begin
  if tg_op = 'UPDATE' then
    v_old_open := public.is_open_assignment_status(old.status);
  end if;
  v_new_open := public.is_open_assignment_status(new.status);

  if v_new_open and new.app_instance_id is not null then
    update public.phone_app_instances pai
    set status = 'occupied',
        current_account_id = new.account_id,
        updated_at = now()
    where pai.id = new.app_instance_id
      and pai.status <> 'disabled';
  end if;

  if tg_op = 'UPDATE'
     and old.app_instance_id is not null
     and (
       (v_old_open and not v_new_open)
       or old.app_instance_id is distinct from new.app_instance_id
     ) then
    if public.account_has_active_runtime_session(old.account_id) then
      perform public.audit_schedule_capacity_event(
        'schedule_capacity_release_skipped',
        old.account_id,
        old.id,
        old.app_instance_id,
        'active_session_guard',
        jsonb_build_object(
          'source', 'account_assignments_trigger',
          'old_status', old.status,
          'new_status', new.status
        )
      );
      return new;
    end if;

    v_reason := case
      when old.app_instance_id is distinct from new.app_instance_id then 'reassignment_release_old_instance'
      when new.status = 'canceled' then 'assignment_canceled_release'
      when new.status = 'archived' then 'account_archived_release'
      when new.status = 'expired' then 'assignment_expired_release'
      else 'manual_assignment_release'
    end;

    v_instance_released := public.release_app_instance_if_unused(
      old.app_instance_id,
      old.account_id,
      v_reason,
      'account_assignments_trigger'
    );

    perform public.audit_schedule_capacity_event(
      'schedule_app_instance_released',
      old.account_id,
      old.id,
      old.app_instance_id,
      case when v_instance_released then v_reason else 'no_open_assignment_after_release' end,
      jsonb_build_object(
        'source', 'account_assignments_trigger',
        'old_status', old.status,
        'new_status', new.status,
        'old_app_instance_id', old.app_instance_id,
        'new_app_instance_id', new.app_instance_id,
        'app_instance_released', v_instance_released
      )
    );
  end if;

  return new;
end;
$$;

drop trigger if exists account_assignments_sync_app_instance on public.account_assignments;
create trigger account_assignments_sync_app_instance
  after insert or update of status, app_instance_id, account_id on public.account_assignments
  for each row execute function public.sync_app_instance_after_assignment_change();

create or replace function public.stamp_assignment_release_metadata()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
begin
  if public.is_open_assignment_status(old.status)
     and public.is_terminal_assignment_status(new.status)
     and public.account_has_active_runtime_session(old.account_id) then
    raise exception 'assignment_terminal_update_blocked_active_run';
  end if;

  if public.is_open_assignment_status(old.status)
     and public.is_terminal_assignment_status(new.status)
     and new.released_at is null then
    new.released_at := now();
  end if;

  return new;
end;
$$;

drop trigger if exists account_assignments_stamp_release_metadata on public.account_assignments;
create trigger account_assignments_stamp_release_metadata
  before update of status on public.account_assignments
  for each row execute function public.stamp_assignment_release_metadata();

-- =============================================================================
-- 4. Assignment validation using app instances
-- =============================================================================
create or replace function public.validate_account_assignment()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_subscription_account_account_id uuid;
  v_subscription_id uuid;
  v_subscription_type text;
  v_subscription_status text;
  v_device_pool_type text;
  v_slot_kind text;
  v_app_instance_device_id uuid;
  v_app_instance_status text;
  v_app_instance_account_id uuid;
  v_app_instance_usable boolean;
  v_app_instance_launchable boolean;
  v_clone_device_id uuid;
begin
  select csa.account_id, csa.subscription_id, cs.subscription_type, cs.status
  into v_subscription_account_account_id, v_subscription_id, v_subscription_type, v_subscription_status
  from public.client_subscription_accounts csa
  join public.client_subscriptions cs on cs.id = csa.subscription_id
  where csa.id = new.subscription_account_id;

  if v_subscription_account_account_id is null then
    raise exception 'account_assignments.subscription_account_id does not reference an active account subscription';
  end if;

  if new.subscription_id <> v_subscription_id then
    raise exception 'account_assignments.subscription_id must match client_subscription_accounts.subscription_id';
  end if;

  if v_subscription_status <> 'active' then
    raise exception 'account_assignments.subscription_id must reference an active subscription';
  end if;

  if new.account_id <> v_subscription_account_account_id then
    raise exception 'account_assignments.account_id must match client_subscription_accounts.account_id';
  end if;

  if new.assignment_type <> v_subscription_type then
    raise exception 'account_assignments.assignment_type must match client_subscriptions.subscription_type';
  end if;

  v_slot_kind := case
    when new.slot_kind = 'outreach_40m' then 'outreach_short'
    else new.slot_kind
  end;

  if new.assignment_type = 'full_cycle' and v_slot_kind <> 'full_cycle_6h' then
    raise exception 'full_cycle assignments require slot_kind=full_cycle_6h';
  end if;

  if new.assignment_type = 'outreach_only' and v_slot_kind <> 'outreach_short' then
    raise exception 'outreach_only assignments require slot_kind=outreach_short or outreach_40m';
  end if;

  if new.app_instance_id is not null then
    select pai.device_id, pai.status, pai.current_account_id, pai.usable_for_auto_login, pai.is_launchable
    into v_app_instance_device_id, v_app_instance_status, v_app_instance_account_id, v_app_instance_usable, v_app_instance_launchable
    from public.phone_app_instances pai
    where pai.id = new.app_instance_id;

    if v_app_instance_device_id is null then
      raise exception 'account_assignments.app_instance_id does not reference an existing app instance';
    end if;

    if new.device_id <> v_app_instance_device_id then
      raise exception 'account_assignments.device_id must match phone_app_instances.device_id';
    end if;

    if new.status in ('pending', 'reserved', 'active') then
      if v_app_instance_status <> 'available'
         and not (v_app_instance_status = 'occupied' and v_app_instance_account_id = new.account_id) then
        raise exception 'app_instance_unavailable';
      end if;

      if not coalesce(v_app_instance_usable, false) or not coalesce(v_app_instance_launchable, false) then
        raise exception 'app_instance_unavailable';
      end if;

      if v_app_instance_account_id is not null and v_app_instance_account_id <> new.account_id then
        raise exception 'app_instance_unavailable';
      end if;
    end if;
  elsif new.clone_id is not null then
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
  elsif new.status in ('pending', 'reserved', 'active') then
    raise exception 'account_assignments.app_instance_id is required for open assignments';
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

  if new.status in ('pending', 'reserved', 'active')
     and not public.validate_assignment_slot_window(
    new.assignment_type,
    new.starts_at,
    new.ends_at,
    coalesce((select timezone from public.phone_devices where id = new.device_id), 'UTC')
  ) then
    raise exception 'assignment_slot_kind_window_mismatch';
  end if;

  if new.status in ('pending', 'reserved', 'active') and exists (
    select 1
    from public.account_assignments aa
    where aa.app_instance_id = new.app_instance_id
      and aa.status in ('pending', 'reserved', 'active')
      and aa.id <> coalesce(new.id, '00000000-0000-0000-0000-000000000000'::uuid)
      and tstzrange(aa.starts_at, aa.ends_at, '[)') && tstzrange(new.starts_at, new.ends_at, '[)')
  ) then
    raise exception 'phone app instance already has an overlapping open assignment';
  end if;

  if new.status in ('pending', 'reserved', 'active') and exists (
    select 1
    from public.account_assignments aa
    where aa.device_id = new.device_id
      and aa.account_id <> new.account_id
      and aa.status in ('pending', 'reserved', 'active')
      and aa.id <> coalesce(new.id, '00000000-0000-0000-0000-000000000000'::uuid)
      and tstzrange(aa.starts_at, aa.ends_at, '[)') && tstzrange(new.starts_at, new.ends_at, '[)')
  ) then
    raise exception 'phone device already has an overlapping open assignment slot';
  end if;

  return new;
end;
$$;

-- =============================================================================
-- 5. Schedule RPCs using app instances
-- =============================================================================
create or replace function public.list_available_assignment_slots(
  p_account_id uuid,
  p_device_id uuid default null,
  p_assignment_type text default null,
  p_slot_date date default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_account_id uuid := p_account_id;
  v_device_id uuid := p_device_id;
  v_assignment_type text := coalesce(
    nullif(trim(p_assignment_type), ''),
    public.resolve_account_schedule_assignment_type(p_account_id)
  );
  v_subscription_account_id uuid;
  v_device_timezone text := 'UTC';
  v_device_status text;
  v_device_pool_type text;
  v_slot_date date;
  v_slot_kind text;
  v_current_assignment record;
  v_slot record;
  v_slots jsonb := '[]'::jsonb;
  v_available boolean;
  v_reason text;
  v_occupied_by text;
  v_occupant record;
  v_app_instance_summary jsonb := '{}'::jsonb;
begin
  if v_account_id is null then
    raise exception 'missing_account_id';
  end if;

  if v_assignment_type is null then
    return jsonb_build_object(
      'ok', false,
      'reason', 'subscription_not_active',
      'slots', '[]'::jsonb
    );
  end if;

  v_slot_kind := public.resolve_assignment_slot_kind(v_assignment_type);

  select csa.id
  into v_subscription_account_id
  from public.client_subscription_accounts csa
  join public.client_subscriptions cs on cs.id = csa.subscription_id
  where csa.account_id = v_account_id
    and csa.status = 'active'
    and cs.status = 'active'
    and cs.subscription_type = v_assignment_type
  order by cs.starts_at desc, cs.created_at desc
  limit 1;

  select
    aa.id,
    aa.device_id,
    aa.clone_id,
    aa.app_instance_id,
    aa.assignment_type,
    aa.slot_kind,
    aa.status,
    aa.starts_at,
    aa.ends_at,
    aa.assignment_source
  into v_current_assignment
  from public.account_assignments aa
  where aa.account_id = v_account_id
    and aa.status in ('pending', 'reserved', 'active')
  order by aa.created_at desc
  limit 1;

  if v_device_id is null then
    v_device_id := v_current_assignment.device_id;
  end if;

  if v_device_id is null then
    select pd.id
    into v_device_id
    from public.phone_devices pd
    where pd.status in ('available', 'active')
      and pd.pool_type in (v_assignment_type, 'shared')
    order by pd.created_at asc
    limit 1;
  end if;

  if v_device_id is null then
    return jsonb_build_object(
      'ok', false,
      'reason', 'device_unavailable',
      'assignment_type', v_assignment_type,
      'slot_kind', v_slot_kind,
      'slots', '[]'::jsonb
    );
  end if;

  select pd.timezone, pd.status, pd.pool_type
  into v_device_timezone, v_device_status, v_device_pool_type
  from public.phone_devices pd
  where pd.id = v_device_id;

  if v_device_timezone is null then
    return jsonb_build_object(
      'ok', false,
      'reason', 'device_unavailable',
      'assignment_type', v_assignment_type,
      'slot_kind', v_slot_kind,
      'slots', '[]'::jsonb
    );
  end if;

  if v_device_status not in ('available', 'active') then
    return jsonb_build_object(
      'ok', false,
      'reason', 'device_unavailable',
      'assignment_type', v_assignment_type,
      'slot_kind', v_slot_kind,
      'device_id', v_device_id,
      'device_timezone', v_device_timezone,
      'slots', '[]'::jsonb
    );
  end if;

  if v_device_pool_type <> 'shared' and v_device_pool_type <> v_assignment_type then
    return jsonb_build_object(
      'ok', false,
      'reason', 'outside_policy',
      'assignment_type', v_assignment_type,
      'slot_kind', v_slot_kind,
      'device_id', v_device_id,
      'device_timezone', v_device_timezone,
      'slots', '[]'::jsonb
    );
  end if;

  select jsonb_build_object(
    'total', count(*),
    'available', count(*) filter (
      where pai.status = 'available'
        and pai.usable_for_auto_login
        and pai.is_launchable
        and pai.current_account_id is null
    ),
    'occupied', count(*) filter (where pai.status = 'occupied' or pai.current_account_id is not null),
    'disabled', count(*) filter (where pai.status = 'disabled'),
    'unknown', count(*) filter (where pai.status = 'unknown'),
    'primary_app', count(*) filter (where pai.instance_type = 'primary_app'),
    'clones', count(*) filter (where pai.instance_type = 'clone')
  )
  into v_app_instance_summary
  from public.phone_app_instances pai
  where pai.device_id = v_device_id;

  v_slot_date := coalesce(
    p_slot_date,
    (now() at time zone v_device_timezone)::date
  );

  for v_slot in
    select *
    from public.generate_assignment_slot_catalog(v_assignment_type, v_slot_date, v_device_timezone)
    order by slot_index
  loop
    v_available := true;
    v_reason := null;
    v_occupied_by := null;

    select
      aa.account_id,
      ia.username
    into v_occupant
    from public.account_assignments aa
    left join public.ig_accounts ia on ia.id = aa.account_id
    where aa.device_id = v_device_id
      and aa.status in ('pending', 'reserved', 'active')
      and aa.account_id <> v_account_id
      and tstzrange(aa.starts_at, aa.ends_at, '[)') && tstzrange(v_slot.starts_at, v_slot.ends_at, '[)')
    order by aa.created_at desc
    limit 1;

    if found then
      v_available := false;
      v_reason := 'occupied';
      v_occupied_by := coalesce(nullif(trim(v_occupant.username), ''), 'assigned account');
    elsif not exists (
      select 1
      from public.phone_app_instances pai
      where pai.device_id = v_device_id
        and pai.status = 'available'
        and pai.usable_for_auto_login
        and pai.is_launchable
        and pai.current_account_id is null
        and not exists (
          select 1
          from public.account_assignments aa
          where aa.app_instance_id = pai.id
            and aa.status in ('pending', 'reserved', 'active')
            and tstzrange(aa.starts_at, aa.ends_at, '[)') && tstzrange(v_slot.starts_at, v_slot.ends_at, '[)')
        )
    ) then
      v_available := false;
      v_reason := 'no_app_instance_available';
    elsif public.slot_overlaps_phone_rest(v_device_id, v_slot.starts_at, v_slot.ends_at, v_device_timezone) then
      v_available := false;
      v_reason := 'phone_rest';
    elsif v_assignment_type = 'outreach_only'
      and public.slot_reserved_for_outreach_rest(v_device_id, v_slot.slot_index) then
      v_available := false;
      v_reason := 'outreach_rest_reserved';
    elsif v_current_assignment.id is not null
      and v_current_assignment.starts_at = v_slot.starts_at
      and v_current_assignment.ends_at = v_slot.ends_at then
      v_available := true;
      v_reason := 'current';
    end if;

    v_slots := v_slots || jsonb_build_array(
      jsonb_build_object(
        'slot_index', v_slot.slot_index,
        'slot_kind', v_slot.slot_kind,
        'slot_kind_label', case when v_slot.slot_kind in ('outreach_short', 'outreach_40m') then 'outreach_40m' else v_slot.slot_kind end,
        'local_label', v_slot.local_label,
        'starts_at', v_slot.starts_at,
        'ends_at', v_slot.ends_at,
        'available', v_available,
        'reason', v_reason,
        'occupied_by', v_occupied_by
      )
    );
  end loop;

  return jsonb_build_object(
    'ok', true,
    'account_id', v_account_id,
    'device_id', v_device_id,
    'assignment_type', v_assignment_type,
    'slot_kind', v_slot_kind,
    'slot_date', v_slot_date,
    'device_timezone', v_device_timezone,
    'app_instance_availability', coalesce(v_app_instance_summary, '{}'::jsonb),
    'current_assignment', case
      when v_current_assignment.id is null then null
      else jsonb_build_object(
        'assignment_id', v_current_assignment.id,
        'device_id', v_current_assignment.device_id,
        'clone_id', v_current_assignment.clone_id,
        'app_instance_id', v_current_assignment.app_instance_id,
        'assignment_type', v_current_assignment.assignment_type,
        'slot_kind', v_current_assignment.slot_kind,
        'status', v_current_assignment.status,
        'starts_at', v_current_assignment.starts_at,
        'ends_at', v_current_assignment.ends_at,
        'assignment_source', v_current_assignment.assignment_source
      )
    end,
    'slots', v_slots
  );
end;
$$;

create or replace function public.assign_account_slot(
  p_account_id uuid,
  p_device_id uuid,
  p_starts_at timestamptz,
  p_ends_at timestamptz,
  p_clone_id uuid default null,
  p_assignment_source text default 'manual_dashboard',
  p_actor_id uuid default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_account_id uuid := p_account_id;
  v_device_id uuid := p_device_id;
  v_starts_at timestamptz := p_starts_at;
  v_ends_at timestamptz := p_ends_at;
  v_preferred_app_instance_id uuid := p_clone_id;
  v_app_instance_id uuid;
  v_clone_id uuid;
  v_assignment_source text := coalesce(nullif(trim(p_assignment_source), ''), 'manual_dashboard');
  v_subscription_id uuid;
  v_client_id uuid;
  v_subscription_account_id uuid;
  v_assignment_type text;
  v_slot_kind text;
  v_device_timezone text;
  v_device_status text;
  v_device_pool_type text;
  v_existing record;
  v_old_app_instance_id uuid;
  v_old_clone_id uuid;
  v_account_app_instance_id uuid;
  v_instance_device_id uuid;
  v_instance_status text;
  v_instance_current_account_id uuid;
  v_instance_usable boolean;
  v_instance_launchable boolean;
  v_assignment_id uuid;
  v_metadata jsonb;
begin
  if v_account_id is null or v_device_id is null or v_starts_at is null or v_ends_at is null then
    raise exception 'invalid_assignment_payload';
  end if;

  if v_ends_at <= v_starts_at then
    raise exception 'invalid_assignment_window';
  end if;

  if v_assignment_source not in ('manual_dashboard', 'onboarding_auto', 'ops', 'auto_assign_rpc') then
    raise exception 'invalid_assignment_source';
  end if;

  v_assignment_type := public.resolve_account_schedule_assignment_type(v_account_id);

  if v_assignment_type is null then
    raise exception 'subscription_not_active';
  end if;

  select
    csa.id,
    csa.subscription_id,
    cs.client_id
  into v_subscription_account_id, v_subscription_id, v_client_id
  from public.client_subscription_accounts csa
  join public.client_subscriptions cs on cs.id = csa.subscription_id
  where csa.account_id = v_account_id
    and csa.status = 'active'
    and cs.status = 'active'
    and cs.subscription_type = v_assignment_type
  order by cs.starts_at desc, cs.created_at desc
  limit 1;

  if v_subscription_account_id is null then
    raise exception 'subscription_not_active';
  end if;

  v_slot_kind := public.resolve_assignment_slot_kind(v_assignment_type);

  select pd.timezone, pd.status, pd.pool_type
  into v_device_timezone, v_device_status, v_device_pool_type
  from public.phone_devices pd
  where pd.id = v_device_id
  for update;

  if v_device_timezone is null then
    raise exception 'device_unavailable';
  end if;

  if v_device_status not in ('available', 'active') then
    raise exception 'device_unavailable';
  end if;

  if v_device_pool_type <> 'shared' and v_device_pool_type <> v_assignment_type then
    raise exception 'assignment_profile_mismatch';
  end if;

  if not public.validate_assignment_slot_window(
    v_assignment_type,
    v_starts_at,
    v_ends_at,
    v_device_timezone
  ) then
    raise exception 'assignment_slot_kind_window_mismatch';
  end if;

  select
    aa.id,
    aa.device_id,
    aa.clone_id,
    aa.app_instance_id,
    aa.starts_at,
    aa.ends_at,
    aa.status
  into v_existing
  from public.account_assignments aa
  where aa.account_id = v_account_id
    and aa.status in ('pending', 'reserved', 'active')
  order by aa.created_at desc
  limit 1
  for update;

  v_old_app_instance_id := v_existing.app_instance_id;
  v_old_clone_id := v_existing.clone_id;

  select pai.id
  into v_account_app_instance_id
  from public.phone_app_instances pai
  where pai.device_id = v_device_id
    and pai.status = 'occupied'
    and pai.current_account_id = v_account_id
    and pai.usable_for_auto_login
    and pai.is_launchable
  order by case when pai.instance_type = 'primary_app' then 0 else 1 end, pai.instance_index asc
  limit 1
  for update skip locked;

  if v_existing.id is not null
     and v_existing.device_id = v_device_id
     and v_existing.starts_at = v_starts_at
     and v_existing.ends_at = v_ends_at
     and (v_preferred_app_instance_id is null or v_preferred_app_instance_id = v_existing.app_instance_id or v_preferred_app_instance_id = v_existing.clone_id)
     and (v_account_app_instance_id is null or v_account_app_instance_id = v_existing.app_instance_id) then
    return jsonb_build_object(
      'ok', true,
      'idempotent', true,
      'assignment_id', v_existing.id,
      'device_id', v_existing.device_id,
      'clone_id', v_existing.clone_id,
      'app_instance_id', v_existing.app_instance_id,
      'assignment_type', v_assignment_type,
      'slot_kind', v_slot_kind,
      'starts_at', v_existing.starts_at,
      'ends_at', v_existing.ends_at,
      'assignment_source', v_assignment_source
    );
  end if;

  if public.slot_overlaps_phone_rest(v_device_id, v_starts_at, v_ends_at, v_device_timezone) then
    raise exception 'phone_rest_active';
  end if;

  if v_assignment_type = 'outreach_only'
    and public.slot_reserved_for_outreach_rest(
      v_device_id,
      ((extract(hour from (v_starts_at at time zone v_device_timezone))::integer * 60
        + extract(minute from (v_starts_at at time zone v_device_timezone))::integer) / 40) + 1
    ) then
    raise exception 'outreach_rest_reserved';
  end if;

  if exists (
    select 1
    from public.account_assignments aa
    where aa.device_id = v_device_id
      and aa.account_id <> v_account_id
      and aa.status in ('pending', 'reserved', 'active')
      and tstzrange(aa.starts_at, aa.ends_at, '[)') && tstzrange(v_starts_at, v_ends_at, '[)')
  ) then
    raise exception 'assignment_slot_conflict';
  end if;

  v_app_instance_id := v_account_app_instance_id;

  if v_app_instance_id is null and v_preferred_app_instance_id is not null then
    select pai.id
    into v_app_instance_id
    from public.phone_app_instances pai
    where pai.id = v_preferred_app_instance_id;

    if v_app_instance_id is null then
      select pai.id
      into v_app_instance_id
      from public.phone_app_instances pai
      where pai.metadata ->> 'legacy_phone_clones_id' = v_preferred_app_instance_id::text
      limit 1;
    end if;
  end if;

  if v_app_instance_id is null then
    select pai.id
    into v_app_instance_id
    from public.phone_app_instances pai
    where pai.device_id = v_device_id
      and pai.status = 'available'
      and pai.usable_for_auto_login
      and pai.is_launchable
      and pai.current_account_id is null
      and not exists (
        select 1
        from public.account_assignments aa
        where aa.app_instance_id = pai.id
          and aa.status in ('pending', 'reserved', 'active')
          and tstzrange(aa.starts_at, aa.ends_at, '[)') && tstzrange(v_starts_at, v_ends_at, '[)')
      )
    order by pai.instance_index asc
    limit 1
    for update skip locked;
  else
    select pai.device_id, pai.status, pai.current_account_id, pai.usable_for_auto_login, pai.is_launchable
    into v_instance_device_id, v_instance_status, v_instance_current_account_id, v_instance_usable, v_instance_launchable
    from public.phone_app_instances pai
    where pai.id = v_app_instance_id
    for update;

    if v_instance_device_id is null or v_instance_device_id <> v_device_id then
      raise exception 'preferred_app_instance_incompatible';
    end if;

    if v_instance_status <> 'available'
       and not (v_instance_status = 'occupied' and v_instance_current_account_id = v_account_id) then
      raise exception 'preferred_app_instance_incompatible';
    end if;

    if not coalesce(v_instance_usable, false) or not coalesce(v_instance_launchable, false) then
      raise exception 'preferred_app_instance_incompatible';
    end if;

    if v_instance_current_account_id is not null and v_instance_current_account_id <> v_account_id then
      raise exception 'preferred_app_instance_incompatible';
    end if;

    if exists (
      select 1
      from public.account_assignments aa
      where aa.app_instance_id = v_app_instance_id
        and aa.account_id <> v_account_id
        and aa.status in ('pending', 'reserved', 'active')
        and tstzrange(aa.starts_at, aa.ends_at, '[)') && tstzrange(v_starts_at, v_ends_at, '[)')
    ) then
      raise exception 'preferred_app_instance_incompatible';
    end if;
  end if;

  if v_app_instance_id is null then
    raise exception 'no_app_instance_available';
  end if;

  select pc.id
  into v_clone_id
  from public.phone_clones pc
  where pc.id = v_app_instance_id;

  v_metadata := jsonb_build_object(
    'assigned_by', coalesce(p_actor_id::text, 'service_role'),
    'assignment_source', v_assignment_source,
    'assigned_at', now(),
    'previous_assignment', case
      when v_existing.id is null then null
      else jsonb_build_object(
        'assignment_id', v_existing.id,
        'device_id', v_existing.device_id,
        'clone_id', v_existing.clone_id,
        'app_instance_id', v_existing.app_instance_id,
        'starts_at', v_existing.starts_at,
        'ends_at', v_existing.ends_at,
        'status', v_existing.status
      )
    end
  );

  if v_existing.id is not null then
    update public.account_assignments
    set
      client_id = v_client_id,
      subscription_id = v_subscription_id,
      subscription_account_id = v_subscription_account_id,
      device_id = v_device_id,
      clone_id = v_clone_id,
      app_instance_id = v_app_instance_id,
      assignment_type = v_assignment_type,
      slot_kind = v_slot_kind,
      status = 'reserved',
      starts_at = v_starts_at,
      ends_at = v_ends_at,
      assignment_source = v_assignment_source,
      assigned_at = now(),
      metadata = coalesce(metadata, '{}'::jsonb) || v_metadata,
      updated_at = now()
    where id = v_existing.id
    returning id into v_assignment_id;
  else
    insert into public.account_assignments (
      client_id,
      subscription_id,
      subscription_account_id,
      account_id,
      device_id,
      clone_id,
      app_instance_id,
      assignment_type,
      slot_kind,
      status,
      starts_at,
      ends_at,
      assigned_at,
      assignment_source,
      metadata
    )
    values (
      v_client_id,
      v_subscription_id,
      v_subscription_account_id,
      v_account_id,
      v_device_id,
      v_clone_id,
      v_app_instance_id,
      v_assignment_type,
      v_slot_kind,
      'reserved',
      v_starts_at,
      v_ends_at,
      now(),
      v_assignment_source,
      v_metadata
    )
    returning id into v_assignment_id;
  end if;

  update public.phone_app_instances
  set status = 'occupied',
      current_account_id = v_account_id,
      updated_at = now()
  where id = v_app_instance_id
    and status <> 'disabled';

  if v_clone_id is not null then
    update public.phone_clones
    set status = 'reserved',
        current_account_id = v_account_id,
        updated_at = now()
    where id = v_clone_id;
  end if;

  if v_old_app_instance_id is not null and v_old_app_instance_id <> v_app_instance_id then
    perform public.release_app_instance_if_unused(
      v_old_app_instance_id,
      v_account_id,
      'reassignment_release_old_instance',
      'assign_account_slot'
    );
  end if;

  if v_old_clone_id is not null and v_old_clone_id <> v_clone_id and not exists (
    select 1
    from public.account_assignments aa
    where aa.clone_id = v_old_clone_id
      and aa.status in ('pending', 'reserved', 'active')
  ) then
    update public.phone_clones
    set status = 'available',
        current_account_id = null,
        updated_at = now()
    where id = v_old_clone_id
      and current_account_id = v_account_id;
  end if;

  return jsonb_build_object(
    'ok', true,
    'idempotent', false,
    'assignment_id', v_assignment_id,
    'device_id', v_device_id,
    'clone_id', v_clone_id,
    'app_instance_id', v_app_instance_id,
    'assignment_type', v_assignment_type,
    'slot_kind', v_slot_kind,
    'starts_at', v_starts_at,
    'ends_at', v_ends_at,
    'assignment_source', v_assignment_source
  );
end;
$$;

create or replace function public.evaluate_account_schedule_gate(
  p_account_id uuid,
  p_requested_run_type text default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_account_id uuid := p_account_id;
  v_requested_run_type text := nullif(lower(trim(p_requested_run_type)), '');
  v_assignment record;
  v_device record;
  v_now timestamptz := now();
  v_window_active boolean;
  v_phone_rest_active boolean;
  v_outreach_rest_reserved boolean;
  v_next_slot timestamptz;
  v_reason text := null;
  v_ok boolean := true;
begin
  if v_account_id is null then
    return jsonb_build_object('ok', false, 'reason', 'assignment_missing');
  end if;

  select
    aa.id,
    aa.device_id,
    aa.clone_id,
    aa.app_instance_id,
    aa.assignment_type,
    aa.slot_kind,
    aa.status,
    aa.starts_at,
    aa.ends_at,
    aa.assignment_source
  into v_assignment
  from public.account_assignments aa
  where aa.account_id = v_account_id
    and aa.status in ('pending', 'reserved', 'active')
  order by aa.created_at desc
  limit 1;

  if v_assignment.id is null then
    return jsonb_build_object(
      'ok', false,
      'reason', 'assignment_missing',
      'window_active', false,
      'phone_rest_active', false,
      'next_eligible_starts_at', null
    );
  end if;

  select pd.id, pd.name, pd.status, pd.timezone, pd.pool_type
  into v_device
  from public.phone_devices pd
  where pd.id = v_assignment.device_id;

  if v_device.id is null or v_device.status not in ('available', 'active') then
    return jsonb_build_object(
      'ok', false,
      'reason', 'device_unavailable',
      'assignment_id', v_assignment.id,
      'app_instance_id', v_assignment.app_instance_id,
      'window_active', false,
      'phone_rest_active', false,
      'next_eligible_starts_at', null
    );
  end if;

  if v_requested_run_type = 'account_session' and v_assignment.assignment_type = 'outreach_only' then
    return jsonb_build_object(
      'ok', false,
      'reason', 'assignment_profile_mismatch',
      'assignment_id', v_assignment.id,
      'app_instance_id', v_assignment.app_instance_id,
      'window_active', false,
      'phone_rest_active', false,
      'next_eligible_starts_at', null
    );
  end if;

  if exists (
    select 1
    from public.account_assignments aa
    where aa.device_id = v_assignment.device_id
      and aa.account_id <> v_account_id
      and aa.id <> v_assignment.id
      and aa.status in ('pending', 'reserved', 'active')
      and tstzrange(aa.starts_at, aa.ends_at, '[)') && tstzrange(v_assignment.starts_at, v_assignment.ends_at, '[)')
  ) then
    return jsonb_build_object(
      'ok', false,
      'reason', 'assignment_slot_conflict',
      'assignment_id', v_assignment.id,
      'app_instance_id', v_assignment.app_instance_id,
      'window_active', false,
      'phone_rest_active', false,
      'next_eligible_starts_at', null
    );
  end if;

  v_window_active := v_assignment.starts_at <= v_now and v_now < v_assignment.ends_at;
  v_phone_rest_active := public.slot_overlaps_phone_rest(
    v_assignment.device_id,
    v_now,
    v_now + interval '1 minute',
    coalesce(v_device.timezone, 'UTC')
  );
  v_outreach_rest_reserved := v_assignment.assignment_type = 'outreach_only'
    and public.slot_reserved_for_outreach_rest(
      v_assignment.device_id,
      ((extract(hour from (v_assignment.starts_at at time zone coalesce(v_device.timezone, 'UTC')))::integer * 60
        + extract(minute from (v_assignment.starts_at at time zone coalesce(v_device.timezone, 'UTC')))::integer) / 40) + 1
    );

  if v_phone_rest_active then
    v_ok := false;
    v_reason := 'phone_rest_active';
  elsif v_outreach_rest_reserved then
    v_ok := false;
    v_reason := 'outreach_rest_reserved';
  elsif not v_window_active then
    v_ok := false;
    v_reason := 'assignment_window_closed';
  end if;

  if not v_ok then
    select min(slot.starts_at)
    into v_next_slot
    from public.generate_assignment_slot_catalog(
      v_assignment.assignment_type,
      (v_now at time zone coalesce(v_device.timezone, 'UTC'))::date,
      coalesce(v_device.timezone, 'UTC')
    ) as slot
    where slot.starts_at > v_now
      and not exists (
        select 1
        from public.account_assignments aa
        where aa.device_id = v_assignment.device_id
          and aa.account_id <> v_account_id
          and aa.status in ('pending', 'reserved', 'active')
          and tstzrange(aa.starts_at, aa.ends_at, '[)') && tstzrange(slot.starts_at, slot.ends_at, '[)')
      )
      and not public.slot_overlaps_phone_rest(
        v_assignment.device_id,
        slot.starts_at,
        slot.ends_at,
        coalesce(v_device.timezone, 'UTC')
      )
      and not (
        v_assignment.assignment_type = 'outreach_only'
        and public.slot_reserved_for_outreach_rest(v_assignment.device_id, slot.slot_index)
      );
  end if;

  return jsonb_build_object(
    'ok', v_ok,
    'reason', coalesce(v_reason, 'assignment_window_open'),
    'assignment_id', v_assignment.id,
    'clone_id', v_assignment.clone_id,
    'app_instance_id', v_assignment.app_instance_id,
    'assignment_type', v_assignment.assignment_type,
    'slot_kind', v_assignment.slot_kind,
    'starts_at', v_assignment.starts_at,
    'ends_at', v_assignment.ends_at,
    'assignment_source', v_assignment.assignment_source,
    'device_id', v_assignment.device_id,
    'device_label', v_device.name,
    'device_timezone', v_device.timezone,
    'window_active', v_window_active,
    'phone_rest_active', v_phone_rest_active,
    'next_eligible_starts_at', v_next_slot
  );
end;
$$;

-- =============================================================================
-- 6. Grants
-- =============================================================================
revoke all on function public.is_open_assignment_status(text) from public;
revoke all on function public.is_terminal_assignment_status(text) from public;
revoke all on function public.account_has_active_runtime_session(uuid) from public;
revoke all on function public.release_app_instance_if_unused(uuid, uuid, text, text) from public;
revoke all on function public.audit_schedule_capacity_event(text, uuid, uuid, uuid, text, jsonb) from public;
revoke all on function public.release_account_schedule_capacity(uuid, text, text, uuid) from public;
revoke all on function public.sync_app_instance_after_assignment_change() from public;
revoke all on function public.stamp_assignment_release_metadata() from public;
revoke all on function public.validate_account_assignment() from public;
revoke all on function public.list_available_assignment_slots(uuid, uuid, text, date) from public;
revoke all on function public.assign_account_slot(uuid, uuid, timestamptz, timestamptz, uuid, text, uuid) from public;
revoke all on function public.evaluate_account_schedule_gate(uuid, text) from public;

revoke all on function public.is_open_assignment_status(text) from anon;
revoke all on function public.is_terminal_assignment_status(text) from anon;
revoke all on function public.account_has_active_runtime_session(uuid) from anon;
revoke all on function public.release_app_instance_if_unused(uuid, uuid, text, text) from anon;
revoke all on function public.audit_schedule_capacity_event(text, uuid, uuid, uuid, text, jsonb) from anon;
revoke all on function public.release_account_schedule_capacity(uuid, text, text, uuid) from anon;
revoke all on function public.sync_app_instance_after_assignment_change() from anon;
revoke all on function public.stamp_assignment_release_metadata() from anon;
revoke all on function public.validate_account_assignment() from anon;
revoke all on function public.list_available_assignment_slots(uuid, uuid, text, date) from anon;
revoke all on function public.assign_account_slot(uuid, uuid, timestamptz, timestamptz, uuid, text, uuid) from anon;
revoke all on function public.evaluate_account_schedule_gate(uuid, text) from anon;

revoke all on function public.is_open_assignment_status(text) from authenticated;
revoke all on function public.is_terminal_assignment_status(text) from authenticated;
revoke all on function public.account_has_active_runtime_session(uuid) from authenticated;
revoke all on function public.release_app_instance_if_unused(uuid, uuid, text, text) from authenticated;
revoke all on function public.audit_schedule_capacity_event(text, uuid, uuid, uuid, text, jsonb) from authenticated;
revoke all on function public.release_account_schedule_capacity(uuid, text, text, uuid) from authenticated;
revoke all on function public.sync_app_instance_after_assignment_change() from authenticated;
revoke all on function public.stamp_assignment_release_metadata() from authenticated;
revoke all on function public.validate_account_assignment() from authenticated;
revoke all on function public.list_available_assignment_slots(uuid, uuid, text, date) from authenticated;
revoke all on function public.assign_account_slot(uuid, uuid, timestamptz, timestamptz, uuid, text, uuid) from authenticated;
revoke all on function public.evaluate_account_schedule_gate(uuid, text) from authenticated;

grant execute on function public.is_open_assignment_status(text) to service_role;
grant execute on function public.is_terminal_assignment_status(text) to service_role;
grant execute on function public.account_has_active_runtime_session(uuid) to service_role;
grant execute on function public.release_app_instance_if_unused(uuid, uuid, text, text) to service_role;
grant execute on function public.audit_schedule_capacity_event(text, uuid, uuid, uuid, text, jsonb) to service_role;
grant execute on function public.release_account_schedule_capacity(uuid, text, text, uuid) to service_role;
grant execute on function public.sync_app_instance_after_assignment_change() to service_role;
grant execute on function public.stamp_assignment_release_metadata() to service_role;
grant execute on function public.validate_account_assignment() to service_role;
grant execute on function public.list_available_assignment_slots(uuid, uuid, text, date) to service_role;
grant execute on function public.assign_account_slot(uuid, uuid, timestamptz, timestamptz, uuid, text, uuid) to service_role;
grant execute on function public.evaluate_account_schedule_gate(uuid, text) to service_role;
