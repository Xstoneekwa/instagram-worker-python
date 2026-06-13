begin;

-- validate_account_assignment() predated schedule_mode=manual_only and rejected
-- slot_kind=manual_only for full_cycle subscriptions. Allow manual_only shape and
-- skip scheduled window / overlap checks when schedule_mode is manual_only.

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

  if new.schedule_mode = 'manual_only' then
    if new.slot_kind <> 'manual_only' then
      raise exception 'manual_only assignments require slot_kind=manual_only';
    end if;
    if new.starts_at is not null or new.ends_at is not null then
      raise exception 'manual_only assignments require null starts_at and ends_at';
    end if;
    if new.app_instance_id is null then
      raise exception 'manual_only_requires_app_instance';
    end if;
  else
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

  if new.schedule_mode <> 'manual_only'
     and new.status in ('pending', 'reserved', 'active')
     and not public.validate_assignment_slot_window(
    new.assignment_type,
    new.starts_at,
    new.ends_at,
    coalesce((select timezone from public.phone_devices where id = new.device_id), 'UTC')
  ) then
    raise exception 'assignment_slot_kind_window_mismatch';
  end if;

  if new.schedule_mode <> 'manual_only'
     and new.status in ('pending', 'reserved', 'active')
     and exists (
    select 1
    from public.account_assignments aa
    where aa.app_instance_id = new.app_instance_id
      and aa.status in ('pending', 'reserved', 'active')
      and aa.schedule_mode = 'scheduled'
      and aa.id <> coalesce(new.id, '00000000-0000-0000-0000-000000000000'::uuid)
      and tstzrange(aa.starts_at, aa.ends_at, '[)') && tstzrange(new.starts_at, new.ends_at, '[)')
  ) then
    raise exception 'phone app instance already has an overlapping open assignment';
  end if;

  if new.schedule_mode <> 'manual_only'
     and new.status in ('pending', 'reserved', 'active')
     and exists (
    select 1
    from public.account_assignments aa
    where aa.device_id = new.device_id
      and aa.account_id <> new.account_id
      and aa.status in ('pending', 'reserved', 'active')
      and aa.schedule_mode = 'scheduled'
      and aa.id <> coalesce(new.id, '00000000-0000-0000-0000-000000000000'::uuid)
      and tstzrange(aa.starts_at, aa.ends_at, '[)') && tstzrange(new.starts_at, new.ends_at, '[)')
  ) then
    raise exception 'phone device already has an overlapping open assignment slot';
  end if;

  return new;
end;
$$;

commit;
