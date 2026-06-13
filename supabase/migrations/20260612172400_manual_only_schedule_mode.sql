begin;

alter table public.account_assignments
  add column if not exists schedule_mode text not null default 'scheduled';

alter table public.account_assignments
  alter column starts_at drop not null,
  alter column ends_at drop not null;

alter table public.account_assignments
  drop constraint if exists account_assignments_valid_window_check,
  drop constraint if exists account_assignments_slot_kind_check,
  drop constraint if exists account_assignments_schedule_mode_check,
  drop constraint if exists account_assignments_schedule_shape_check;

alter table public.account_assignments
  add constraint account_assignments_schedule_mode_check
    check (schedule_mode in ('scheduled', 'manual_only')),
  add constraint account_assignments_slot_kind_check
    check (slot_kind in ('full_cycle_6h', 'outreach_short', 'outreach_40m', 'manual_only')),
  add constraint account_assignments_schedule_shape_check
    check (
      (
        schedule_mode = 'scheduled'
        and starts_at is not null
        and ends_at is not null
        and ends_at > starts_at
        and slot_kind <> 'manual_only'
      )
      or
      (
        schedule_mode = 'manual_only'
        and starts_at is null
        and ends_at is null
        and app_instance_id is not null
        and slot_kind = 'manual_only'
      )
    );

alter table public.account_assignments
  drop constraint if exists account_assignments_device_open_no_overlap;

alter table public.account_assignments
  add constraint account_assignments_device_open_no_overlap
  exclude using gist (
    device_id with =,
    tstzrange(starts_at, ends_at, '[)') with &&
  )
  where (
    schedule_mode = 'scheduled'
    and status in ('pending', 'reserved', 'active')
  );

create or replace function public.assign_account_manual_only(
  p_account_id uuid,
  p_device_id uuid,
  p_app_instance_id uuid,
  p_assignment_source text default 'manual_dashboard',
  p_actor_id uuid default null
)
returns jsonb
language plpgsql
security definer
set search_path to 'public'
as $function$
declare
  v_account_id uuid := p_account_id;
  v_device_id uuid := p_device_id;
  v_app_instance_id uuid := p_app_instance_id;
  v_assignment_source text := coalesce(nullif(trim(p_assignment_source), ''), 'manual_dashboard');
  v_subscription_id uuid;
  v_client_id uuid;
  v_subscription_account_id uuid;
  v_assignment_type text;
  v_device_status text;
  v_device_pool_type text;
  v_instance_device_id uuid;
  v_instance_status text;
  v_instance_current_account_id uuid;
  v_instance_usable boolean;
  v_instance_launchable boolean;
  v_existing record;
  v_old_app_instance_id uuid;
  v_old_clone_id uuid;
  v_clone_id uuid;
  v_assignment_id uuid;
  v_metadata jsonb;
begin
  if v_account_id is null or v_device_id is null then
    raise exception 'invalid_assignment_payload';
  end if;

  if v_app_instance_id is null then
    raise exception 'manual_only_requires_app_instance';
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

  select pd.status, pd.pool_type
  into v_device_status, v_device_pool_type
  from public.phone_devices pd
  where pd.id = v_device_id
  for update;

  if v_device_status is null or v_device_status not in ('available', 'active') then
    raise exception 'device_unavailable';
  end if;

  if v_device_pool_type <> 'shared' and v_device_pool_type <> v_assignment_type then
    raise exception 'assignment_profile_mismatch';
  end if;

  select pai.device_id, pai.status, pai.current_account_id, pai.usable_for_auto_login, pai.is_launchable
  into v_instance_device_id, v_instance_status, v_instance_current_account_id, v_instance_usable, v_instance_launchable
  from public.phone_app_instances pai
  where pai.id = v_app_instance_id
  for update;

  if v_instance_device_id is null or v_instance_device_id <> v_device_id then
    raise exception 'preferred_app_instance_incompatible';
  end if;

  if not coalesce(v_instance_usable, false) or not coalesce(v_instance_launchable, false) then
    raise exception 'preferred_app_instance_incompatible';
  end if;

  if v_instance_current_account_id is not null and v_instance_current_account_id <> v_account_id then
    raise exception 'app_instance_already_reserved';
  end if;

  if v_instance_status <> 'available'
     and not (v_instance_status = 'occupied' and v_instance_current_account_id = v_account_id) then
    raise exception 'app_instance_already_reserved';
  end if;

  if exists (
    select 1
    from public.account_assignments aa
    where aa.app_instance_id = v_app_instance_id
      and aa.account_id <> v_account_id
      and aa.status in ('pending', 'reserved', 'active')
  ) then
    raise exception 'app_instance_already_reserved';
  end if;

  select
    aa.id,
    aa.device_id,
    aa.clone_id,
    aa.app_instance_id,
    aa.starts_at,
    aa.ends_at,
    aa.status,
    aa.schedule_mode
  into v_existing
  from public.account_assignments aa
  where aa.account_id = v_account_id
    and aa.status in ('pending', 'reserved', 'active')
  order by aa.created_at desc
  limit 1
  for update;

  v_old_app_instance_id := v_existing.app_instance_id;
  v_old_clone_id := v_existing.clone_id;

  select pc.id
  into v_clone_id
  from public.phone_clones pc
  where pc.id = v_app_instance_id;

  v_metadata := jsonb_build_object(
    'assigned_by', coalesce(p_actor_id::text, 'system'),
    'assignment_source', v_assignment_source,
    'schedule_mode', 'manual_only',
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
        'status', v_existing.status,
        'schedule_mode', v_existing.schedule_mode
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
      slot_kind = 'manual_only',
      schedule_mode = 'manual_only',
      status = 'reserved',
      starts_at = null,
      ends_at = null,
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
      schedule_mode,
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
      'manual_only',
      'manual_only',
      'reserved',
      null,
      null,
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
      'assign_account_manual_only'
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
    'idempotent', v_existing.id is not null
      and v_existing.schedule_mode = 'manual_only'
      and v_existing.device_id = v_device_id
      and v_existing.app_instance_id = v_app_instance_id,
    'assignment_id', v_assignment_id,
    'device_id', v_device_id,
    'clone_id', v_clone_id,
    'app_instance_id', v_app_instance_id,
    'assignment_type', v_assignment_type,
    'slot_kind', 'manual_only',
    'schedule_mode', 'manual_only',
    'starts_at', null,
    'ends_at', null,
    'assignment_source', v_assignment_source
  );
end;
$function$;

create or replace function public.evaluate_account_schedule_gate(p_account_id uuid, p_requested_run_type text default null::text)
returns jsonb
language plpgsql
security definer
set search_path to 'public'
as $function$
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
    aa.schedule_mode,
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
      'schedule_mode', v_assignment.schedule_mode,
      'window_active', false,
      'phone_rest_active', false,
      'next_eligible_starts_at', null
    );
  end if;

  if v_assignment.schedule_mode = 'manual_only' then
    return jsonb_build_object(
      'ok', false,
      'reason', 'manual_only_runtime_disabled',
      'assignment_id', v_assignment.id,
      'clone_id', v_assignment.clone_id,
      'app_instance_id', v_assignment.app_instance_id,
      'assignment_type', v_assignment.assignment_type,
      'slot_kind', v_assignment.slot_kind,
      'schedule_mode', v_assignment.schedule_mode,
      'starts_at', null,
      'ends_at', null,
      'assignment_source', v_assignment.assignment_source,
      'device_id', v_assignment.device_id,
      'device_label', v_device.name,
      'device_timezone', v_device.timezone,
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
      'schedule_mode', v_assignment.schedule_mode,
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
      and aa.schedule_mode = 'scheduled'
      and tstzrange(aa.starts_at, aa.ends_at, '[)') && tstzrange(v_assignment.starts_at, v_assignment.ends_at, '[)')
  ) then
    return jsonb_build_object(
      'ok', false,
      'reason', 'assignment_slot_conflict',
      'assignment_id', v_assignment.id,
      'app_instance_id', v_assignment.app_instance_id,
      'schedule_mode', v_assignment.schedule_mode,
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
          and aa.schedule_mode = 'scheduled'
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
    'schedule_mode', v_assignment.schedule_mode,
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
$function$;

create or replace function public.list_available_assignment_slots(
  p_account_id uuid,
  p_device_id uuid default null::uuid,
  p_assignment_type text default null::text,
  p_slot_date date default null::date
)
returns jsonb
language plpgsql
security definer
set search_path to 'public'
as $function$
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
    aa.schedule_mode,
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
      and aa.schedule_mode = 'scheduled'
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
            and aa.schedule_mode = 'scheduled'
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
      and v_current_assignment.schedule_mode = 'scheduled'
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
        'schedule_mode', v_current_assignment.schedule_mode,
        'status', v_current_assignment.status,
        'starts_at', v_current_assignment.starts_at,
        'ends_at', v_current_assignment.ends_at,
        'assignment_source', v_current_assignment.assignment_source
      )
    end,
    'slots', v_slots
  );
end;
$function$;

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
set search_path to 'public'
as $function$
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
    aa.status,
    aa.schedule_mode
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
     and v_existing.schedule_mode = 'scheduled'
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
      'schedule_mode', 'scheduled',
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
      and aa.schedule_mode = 'scheduled'
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
          and aa.account_id <> v_account_id
          and aa.status in ('pending', 'reserved', 'active')
      )
      and not exists (
        select 1
        from public.account_assignments aa
        where aa.app_instance_id = pai.id
          and aa.status in ('pending', 'reserved', 'active')
          and aa.schedule_mode = 'scheduled'
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
    'assigned_by', coalesce(p_actor_id::text, 'system'),
    'assignment_source', v_assignment_source,
    'schedule_mode', 'scheduled',
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
        'status', v_existing.status,
        'schedule_mode', v_existing.schedule_mode
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
      schedule_mode = 'scheduled',
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
      schedule_mode,
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
      'scheduled',
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
    'schedule_mode', 'scheduled',
    'starts_at', v_starts_at,
    'ends_at', v_ends_at,
    'assignment_source', v_assignment_source
  );
end;
$function$;

comment on column public.account_assignments.schedule_mode is
  'scheduled reserves a recurring assignment window; manual_only reserves device/app_instance placement without an automatic timeslot.';

comment on function public.assign_account_manual_only(uuid, uuid, uuid, text, uuid) is
  'Reserve a device/app_instance placement for a manual-only account without creating a scheduled timeslot or run request.';

commit;
