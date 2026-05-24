-- Entry 2C-2 — service-role auto assignment helper.
--
-- This migration only adds an explicit SQL helper that reserves an available
-- clone for an account-scoped subscription. It does not create timeslot catalog
-- tables, worker dispatch, credentials, auto-login, provisioning jobs, phone
-- rest runtime, or session window enforcement.

create extension if not exists pgcrypto;

create or replace function public.auto_assign_account_from_subscription(
  p_subscription_account_id uuid,
  p_starts_at timestamptz,
  p_ends_at timestamptz,
  p_preferred_device_id uuid default null,
  p_preferred_clone_id uuid default null,
  p_metadata jsonb default '{}'::jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_subscription_id uuid;
  v_client_id uuid;
  v_account_id uuid;
  v_subscription_account_status text;
  v_subscription_status text;
  v_subscription_type text;
  v_subscription_starts_at timestamptz;
  v_subscription_ends_at timestamptz;
  v_assignment_type text;
  v_slot_kind text;
  v_device_id uuid;
  v_clone_id uuid;
  v_assignment_id uuid;
begin
  if p_subscription_account_id is null then
    raise exception 'subscription_account_not_found';
  end if;

  if p_starts_at is null or p_ends_at is null or p_ends_at <= p_starts_at then
    raise exception 'invalid_assignment_window';
  end if;

  if p_metadata is null or jsonb_typeof(p_metadata) <> 'object' then
    raise exception 'invalid_assignment_metadata';
  end if;

  select
    csa.subscription_id,
    csa.account_id,
    csa.status,
    cs.client_id,
    cs.status,
    cs.subscription_type,
    cs.starts_at,
    cs.ends_at
  into
    v_subscription_id,
    v_account_id,
    v_subscription_account_status,
    v_client_id,
    v_subscription_status,
    v_subscription_type,
    v_subscription_starts_at,
    v_subscription_ends_at
  from public.client_subscription_accounts csa
  join public.client_subscriptions cs
    on cs.id = csa.subscription_id
  where csa.id = p_subscription_account_id;

  if v_subscription_id is null then
    raise exception 'subscription_account_not_found';
  end if;

  if v_subscription_account_status <> 'active' then
    raise exception 'subscription_account_not_active';
  end if;

  if v_subscription_status <> 'active'
     or v_subscription_starts_at > p_starts_at
     or (v_subscription_ends_at is not null and v_subscription_ends_at <= p_starts_at) then
    raise exception 'subscription_not_active';
  end if;

  v_assignment_type := v_subscription_type;
  v_slot_kind := case
    when v_assignment_type = 'full_cycle' then 'full_cycle_6h'
    when v_assignment_type = 'outreach_only' then 'outreach_short'
    else null
  end;

  if v_slot_kind is null then
    raise exception 'subscription_not_active';
  end if;

  if exists (
    select 1
    from public.account_assignments aa
    where aa.account_id = v_account_id
      and aa.status in ('pending', 'reserved', 'active')
  ) then
    raise exception 'account_already_has_open_assignment';
  end if;

  if p_preferred_device_id is not null and not exists (
    select 1
    from public.phone_devices pd
    where pd.id = p_preferred_device_id
      and pd.status in ('available', 'active')
      and pd.pool_type in (v_assignment_type, 'shared')
  ) then
    raise exception 'preferred_device_incompatible';
  end if;

  if p_preferred_clone_id is not null and not exists (
    select 1
    from public.phone_clones pc
    join public.phone_devices pd
      on pd.id = pc.device_id
    where pc.id = p_preferred_clone_id
      and pc.status = 'available'
      and pc.clone_index <= pd.max_clones
      and pd.status in ('available', 'active')
      and pd.pool_type in (v_assignment_type, 'shared')
      and (p_preferred_device_id is null or pd.id = p_preferred_device_id)
      and not exists (
        select 1
        from public.account_assignments aa
        where aa.clone_id = pc.id
          and aa.status in ('pending', 'reserved', 'active')
          and tstzrange(aa.starts_at, aa.ends_at, '[)') && tstzrange(p_starts_at, p_ends_at, '[)')
      )
  ) then
    raise exception 'preferred_clone_incompatible';
  end if;

  select candidate.device_id, candidate.clone_id
  into v_device_id, v_clone_id
  from (
    select
      pd.id as device_id,
      pc.id as clone_id,
      (
        select count(*)
        from public.account_assignments aa
        where aa.device_id = pd.id
          and aa.status in ('pending', 'reserved', 'active')
      ) as open_assignments_on_device
    from public.phone_clones pc
    join public.phone_devices pd
      on pd.id = pc.device_id
    where pd.status in ('available', 'active')
      and pd.pool_type in (v_assignment_type, 'shared')
      and pc.status = 'available'
      and pc.clone_index <= pd.max_clones
      and (p_preferred_device_id is null or pd.id = p_preferred_device_id)
      and (p_preferred_clone_id is null or pc.id = p_preferred_clone_id)
      and not exists (
        select 1
        from public.account_assignments aa
        where aa.clone_id = pc.id
          and aa.status in ('pending', 'reserved', 'active')
          and tstzrange(aa.starts_at, aa.ends_at, '[)') && tstzrange(p_starts_at, p_ends_at, '[)')
      )
    order by
      case when p_preferred_device_id is not null and pd.id = p_preferred_device_id then 0 else 1 end,
      case when p_preferred_clone_id is not null and pc.id = p_preferred_clone_id then 0 else 1 end,
      case when pd.pool_type = v_assignment_type then 0 else 1 end,
      case when pd.status = 'available' then 0 else 1 end,
      open_assignments_on_device asc,
      pd.created_at asc,
      pc.clone_index asc,
      pd.id asc,
      pc.id asc
    for update of pc skip locked
    limit 1
  ) as candidate;

  if v_clone_id is null then
    raise exception 'no_capacity_available';
  end if;

  insert into public.account_assignments (
    client_id,
    subscription_id,
    subscription_account_id,
    account_id,
    device_id,
    clone_id,
    assignment_type,
    slot_kind,
    status,
    starts_at,
    ends_at,
    assigned_at,
    metadata
  )
  values (
    v_client_id,
    v_subscription_id,
    p_subscription_account_id,
    v_account_id,
    v_device_id,
    v_clone_id,
    v_assignment_type,
    v_slot_kind,
    'reserved',
    p_starts_at,
    p_ends_at,
    now(),
    p_metadata
  )
  returning id into v_assignment_id;

  update public.phone_clones
  set status = 'reserved',
      current_account_id = v_account_id,
      updated_at = now()
  where id = v_clone_id;

  return jsonb_build_object(
    'ok', true,
    'assignment_id', v_assignment_id,
    'device_id', v_device_id,
    'clone_id', v_clone_id,
    'account_id', v_account_id,
    'subscription_id', v_subscription_id,
    'subscription_account_id', p_subscription_account_id,
    'assignment_type', v_assignment_type,
    'slot_kind', v_slot_kind,
    'starts_at', p_starts_at,
    'ends_at', p_ends_at
  );
end;
$$;

comment on function public.auto_assign_account_from_subscription(
  uuid,
  timestamptz,
  timestamptz,
  uuid,
  uuid,
  jsonb
) is
  'Entry 2C-2 service-role helper: reserve an available clone for an account-scoped subscription. Does not dispatch workers or provision logins.';

revoke all on function public.auto_assign_account_from_subscription(
  uuid,
  timestamptz,
  timestamptz,
  uuid,
  uuid,
  jsonb
) from public;

revoke all on function public.auto_assign_account_from_subscription(
  uuid,
  timestamptz,
  timestamptz,
  uuid,
  uuid,
  jsonb
) from authenticated;

grant execute on function public.auto_assign_account_from_subscription(
  uuid,
  timestamptz,
  timestamptz,
  uuid,
  uuid,
  jsonb
) to service_role;
