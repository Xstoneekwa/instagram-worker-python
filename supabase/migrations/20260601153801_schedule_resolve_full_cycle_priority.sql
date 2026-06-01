-- Schedule runtime profile resolution: full_cycle wins over outreach_only add-on subscriptions.
--
-- Product rule:
-- - Pro/full_cycle accounts with Outreach add-on keep full_cycle schedule slots.
-- - outreach_only schedule applies only to standalone outreach runtime profiles
--   or explicit outreach_standalone commercial packages.

create or replace function public.resolve_account_schedule_assignment_type(p_account_id uuid)
returns text
language plpgsql
stable
set search_path = public
as $$
declare
  v_package_code text;
  v_has_full_cycle boolean := false;
  v_has_outreach_only boolean := false;
begin
  if p_account_id is null then
    return null;
  end if;

  select acp.package_code
  into v_package_code
  from public.account_commercial_packages acp
  where acp.account_id = p_account_id
    and acp.status = 'active'
    and acp.starts_at <= now()
    and (acp.ends_at is null or acp.ends_at > now())
  order by acp.starts_at desc, acp.created_at desc
  limit 1;

  select exists (
    select 1
    from public.client_subscription_accounts csa
    join public.client_subscriptions cs on cs.id = csa.subscription_id
    where csa.account_id = p_account_id
      and csa.status = 'active'
      and cs.status = 'active'
      and cs.subscription_type = 'full_cycle'
  ) into v_has_full_cycle;

  select exists (
    select 1
    from public.client_subscription_accounts csa
    join public.client_subscriptions cs on cs.id = csa.subscription_id
    where csa.account_id = p_account_id
      and csa.status = 'active'
      and cs.status = 'active'
      and cs.subscription_type = 'outreach_only'
  ) into v_has_outreach_only;

  if v_package_code = 'outreach_standalone' and v_has_outreach_only then
    return 'outreach_only';
  end if;

  if v_has_full_cycle then
    return 'full_cycle';
  end if;

  if v_has_outreach_only then
    return 'outreach_only';
  end if;

  return null;
end;
$$;

comment on function public.resolve_account_schedule_assignment_type(uuid) is
  'Resolves the primary Schedule assignment_type for an account. full_cycle wins over outreach_only add-on subscriptions; Outreach entitlement/add-on does not override full_cycle.';

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
      from public.phone_clones pc
      where pc.device_id = v_device_id
        and pc.status = 'available'
        and not exists (
          select 1
          from public.account_assignments aa
          where aa.clone_id = pc.id
            and aa.status in ('pending', 'reserved', 'active')
            and tstzrange(aa.starts_at, aa.ends_at, '[)') && tstzrange(v_slot.starts_at, v_slot.ends_at, '[)')
        )
    ) then
      v_available := false;
      v_reason := 'no_clone_available';
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
    'current_assignment', case
      when v_current_assignment.id is null then null
      else jsonb_build_object(
        'assignment_id', v_current_assignment.id,
        'device_id', v_current_assignment.device_id,
        'clone_id', v_current_assignment.clone_id,
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
  v_clone_id uuid := p_clone_id;
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
  v_old_clone_id uuid;
  v_clone_device_id uuid;
  v_clone_status text;
  v_clone_current_account_id uuid;
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

  v_old_clone_id := v_existing.clone_id;

  if v_existing.id is not null
     and v_existing.device_id = v_device_id
     and v_existing.starts_at = v_starts_at
     and v_existing.ends_at = v_ends_at
     and (v_clone_id is null or v_clone_id = v_existing.clone_id) then
    return jsonb_build_object(
      'ok', true,
      'idempotent', true,
      'assignment_id', v_existing.id,
      'device_id', v_existing.device_id,
      'clone_id', v_existing.clone_id,
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

  if v_clone_id is null then
    select pc.id
    into v_clone_id
    from public.phone_clones pc
    where pc.device_id = v_device_id
      and pc.status = 'available'
      and not exists (
        select 1
        from public.account_assignments aa
        where aa.clone_id = pc.id
          and aa.status in ('pending', 'reserved', 'active')
          and tstzrange(aa.starts_at, aa.ends_at, '[)') && tstzrange(v_starts_at, v_ends_at, '[)')
      )
    order by pc.clone_index asc
    limit 1
    for update skip locked;
  else
    select pc.device_id, pc.status, pc.current_account_id
    into v_clone_device_id, v_clone_status, v_clone_current_account_id
    from public.phone_clones pc
    where pc.id = v_clone_id
    for update;

    if v_clone_device_id is null or v_clone_device_id <> v_device_id then
      raise exception 'preferred_clone_incompatible';
    end if;

    if v_clone_status not in ('available', 'reserved', 'active') then
      raise exception 'preferred_clone_incompatible';
    end if;

    if v_clone_current_account_id is not null and v_clone_current_account_id <> v_account_id then
      raise exception 'preferred_clone_incompatible';
    end if;

    if exists (
      select 1
      from public.account_assignments aa
      where aa.clone_id = v_clone_id
        and aa.account_id <> v_account_id
        and aa.status in ('pending', 'reserved', 'active')
        and tstzrange(aa.starts_at, aa.ends_at, '[)') && tstzrange(v_starts_at, v_ends_at, '[)')
    ) then
      raise exception 'preferred_clone_incompatible';
    end if;
  end if;

  if v_clone_id is null then
    raise exception 'no_capacity_available';
  end if;

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

  update public.phone_clones
  set status = 'reserved',
      current_account_id = v_account_id,
      updated_at = now()
  where id = v_clone_id;

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
    'assignment_type', v_assignment_type,
    'slot_kind', v_slot_kind,
    'starts_at', v_starts_at,
    'ends_at', v_ends_at,
    'assignment_source', v_assignment_source
  );
end;
$$;

revoke all on function public.resolve_account_schedule_assignment_type(uuid) from public;
revoke all on function public.resolve_account_schedule_assignment_type(uuid) from anon;
revoke all on function public.resolve_account_schedule_assignment_type(uuid) from authenticated;
grant execute on function public.resolve_account_schedule_assignment_type(uuid) to service_role;
