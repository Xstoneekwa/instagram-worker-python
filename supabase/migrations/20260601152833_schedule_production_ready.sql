-- Schedule production-ready: phone timezone, fixed blackout windows, assignment source,
-- phone-wide slot anti-collision, slot catalog RPCs, and schedule gates.
--
-- Product note:
-- - Full-cycle capacity stays four assignable 6h slots per phone/day by default.
-- - Natural phone rest is dynamic post-session buffer when a run finishes before
--   its assigned slot ends.
-- - phone_rest_windows represents explicit fixed blackout/maintenance windows
--   only. It must not be populated as a default "sacrifice one full slot" rest
--   strategy.

create extension if not exists pgcrypto;
create extension if not exists btree_gist;

-- =============================================================================
-- 1. Phone timezone + assignment metadata
-- =============================================================================
alter table public.phone_devices
  add column if not exists timezone text;

update public.phone_devices
set timezone = coalesce(nullif(trim(timezone), ''), 'UTC')
where timezone is null or trim(timezone) = '';

alter table public.phone_devices
  alter column timezone set default 'UTC';

alter table public.phone_devices
  alter column timezone set not null;

alter table public.phone_devices
  drop constraint if exists phone_devices_timezone_check;

alter table public.phone_devices
  add constraint phone_devices_timezone_check
  check (char_length(trim(timezone)) > 0);

comment on column public.phone_devices.timezone is
  'IANA timezone used to interpret assignment slot windows for this phone/device.';

create or replace function public.is_valid_schedule_timezone(p_timezone text)
returns boolean
language sql
stable
set search_path = public, pg_catalog
as $$
  select exists (
    select 1
    from pg_catalog.pg_timezone_names
    where name = nullif(trim(p_timezone), '')
  );
$$;

create or replace function public.validate_phone_device_timezone()
returns trigger
language plpgsql
security invoker
set search_path = public, pg_catalog
as $$
begin
  if not public.is_valid_schedule_timezone(new.timezone) then
    raise exception 'invalid_timezone';
  end if;

  return new;
end;
$$;

drop trigger if exists phone_devices_validate_timezone on public.phone_devices;
create trigger phone_devices_validate_timezone
  before insert or update of timezone on public.phone_devices
  for each row execute function public.validate_phone_device_timezone();

alter table public.account_assignments
  add column if not exists assignment_source text;

update public.account_assignments
set assignment_source = coalesce(nullif(trim(assignment_source), ''), 'legacy_unknown')
where assignment_source is null or trim(assignment_source) = '';

alter table public.account_assignments
  alter column assignment_source set default 'manual_dashboard';

alter table public.account_assignments
  alter column assignment_source set not null;

alter table public.account_assignments
  drop constraint if exists account_assignments_assignment_source_check;

alter table public.account_assignments
  add constraint account_assignments_assignment_source_check
  check (assignment_source in (
    'manual_dashboard',
    'onboarding_auto',
    'ops',
    'legacy_unknown',
    'auto_assign_rpc'
  ));

alter table public.account_assignments
  drop constraint if exists account_assignments_slot_kind_check;

alter table public.account_assignments
  add constraint account_assignments_slot_kind_check
  check (slot_kind in ('full_cycle_6h', 'outreach_short', 'outreach_40m'));

alter table public.account_assignments
  drop constraint if exists account_assignments_device_open_no_overlap;

alter table public.account_assignments
  add constraint account_assignments_device_open_no_overlap
  exclude using gist (
    device_id with =,
    tstzrange(starts_at, ends_at, '[)') with &&
  )
  where (status in ('pending', 'reserved', 'active'));

-- =============================================================================
-- 2. Fixed blackout windows
-- =============================================================================
create table if not exists public.phone_rest_windows (
  id uuid primary key default gen_random_uuid(),
  device_id uuid not null references public.phone_devices (id) on delete cascade,
  rest_type text not null default 'weekly',
  weekday smallint,
  rest_date date,
  starts_at_local time not null,
  ends_at_local time not null,
  timezone text not null default 'UTC',
  status text not null default 'active',
  reason text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint phone_rest_windows_type_check
    check (rest_type in ('weekly', 'one_off')),
  constraint phone_rest_windows_weekday_check
    check (weekday is null or (weekday >= 0 and weekday <= 6)),
  constraint phone_rest_windows_scope_check
    check (
      (rest_type = 'weekly' and weekday is not null and rest_date is null)
      or
      (rest_type = 'one_off' and rest_date is not null and weekday is null)
    ),
  constraint phone_rest_windows_status_check
    check (status in ('active', 'disabled')),
  constraint phone_rest_windows_timezone_nonempty_check
    check (char_length(trim(timezone)) > 0)
);

create index if not exists phone_rest_windows_device_status_idx
  on public.phone_rest_windows (device_id, status);

comment on table public.phone_rest_windows is
  'Device-local weekly or one-off fixed blackout/maintenance windows. Dynamic post-session rest is derived after runs finish and does not consume a scheduled slot by default.';

create or replace function public.validate_phone_rest_window()
returns trigger
language plpgsql
security invoker
set search_path = public, pg_catalog
as $$
begin
  if not public.is_valid_schedule_timezone(new.timezone) then
    raise exception 'invalid_timezone';
  end if;

  return new;
end;
$$;

drop trigger if exists phone_rest_windows_set_updated_at on public.phone_rest_windows;
create trigger phone_rest_windows_set_updated_at
  before update on public.phone_rest_windows
  for each row execute function public.set_updated_at();

drop trigger if exists phone_rest_windows_validate on public.phone_rest_windows;
create trigger phone_rest_windows_validate
  before insert or update of rest_type, weekday, rest_date, timezone on public.phone_rest_windows
  for each row execute function public.validate_phone_rest_window();

alter table public.phone_rest_windows enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'phone_rest_windows' and policyname = 'phone_rest_windows_service_role_all'
  ) then
    create policy phone_rest_windows_service_role_all on public.phone_rest_windows
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;
end
$$;

grant select, insert, update, delete on public.phone_rest_windows to service_role;
revoke all on public.phone_rest_windows from anon;
revoke all on public.phone_rest_windows from authenticated;

-- Optional outreach-only rest policy. Disabled/no rows by default, so it does
-- not remove any outreach slots until an operator explicitly configures it.
create table if not exists public.device_outreach_rest_policies (
  id uuid primary key default gen_random_uuid(),
  device_id uuid not null references public.phone_devices (id) on delete cascade,
  status text not null default 'disabled',
  daily_rest_slot_count integer not null default 0,
  buffer_minutes integer not null default 0,
  reserved_slot_indexes integer[] not null default '{}'::integer[],
  reason text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint device_outreach_rest_policies_status_check
    check (status in ('active', 'disabled')),
  constraint device_outreach_rest_policies_counts_check
    check (daily_rest_slot_count >= 0 and buffer_minutes >= 0),
  constraint device_outreach_rest_policies_reserved_indexes_check
    check (
      array_length(reserved_slot_indexes, 1) is null
      or
      (
        0 < all (reserved_slot_indexes)
        and 36 >= all (reserved_slot_indexes)
      )
    )
);

create unique index if not exists device_outreach_rest_policies_one_per_device_key
  on public.device_outreach_rest_policies (device_id);

comment on table public.device_outreach_rest_policies is
  'Optional outreach-only rest policy. Disabled by default. Future operators may reserve specific 40-minute slots or configure buffer metadata without affecting full-cycle capacity.';

drop trigger if exists device_outreach_rest_policies_set_updated_at on public.device_outreach_rest_policies;
create trigger device_outreach_rest_policies_set_updated_at
  before update on public.device_outreach_rest_policies
  for each row execute function public.set_updated_at();

alter table public.device_outreach_rest_policies enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'device_outreach_rest_policies' and policyname = 'device_outreach_rest_policies_service_role_all'
  ) then
    create policy device_outreach_rest_policies_service_role_all on public.device_outreach_rest_policies
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;
end
$$;

grant select, insert, update, delete on public.device_outreach_rest_policies to service_role;
revoke all on public.device_outreach_rest_policies from anon;
revoke all on public.device_outreach_rest_policies from authenticated;

-- =============================================================================
-- 3. Validation trigger: clone overlap + phone-wide slot overlap
-- =============================================================================
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
  v_slot_kind text;
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

  if not public.validate_assignment_slot_window(
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
    where aa.clone_id = new.clone_id
      and aa.status in ('pending', 'reserved', 'active')
      and aa.id <> coalesce(new.id, '00000000-0000-0000-0000-000000000000'::uuid)
      and tstzrange(aa.starts_at, aa.ends_at, '[)') && tstzrange(new.starts_at, new.ends_at, '[)')
  ) then
    raise exception 'phone clone already has an overlapping open assignment';
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
-- 4. Slot helpers
-- =============================================================================
create or replace function public.normalize_assignment_slot_kind(p_slot_kind text)
returns text
language sql
immutable
as $$
  select case
    when p_slot_kind = 'outreach_40m' then 'outreach_short'
    else p_slot_kind
  end;
$$;

create or replace function public.resolve_assignment_slot_kind(p_assignment_type text)
returns text
language sql
immutable
as $$
  select case
    when p_assignment_type = 'full_cycle' then 'full_cycle_6h'
    when p_assignment_type = 'outreach_only' then 'outreach_40m'
    else null
  end;
$$;

create or replace function public.validate_assignment_slot_window(
  p_assignment_type text,
  p_starts_at timestamptz,
  p_ends_at timestamptz,
  p_timezone text
)
returns boolean
language plpgsql
stable
set search_path = public, pg_catalog
as $$
declare
  v_tz text := coalesce(nullif(trim(p_timezone), ''), 'UTC');
  v_duration_seconds numeric;
  v_local_start timestamp;
  v_local_hour integer;
  v_local_minute integer;
  v_local_total_minutes integer;
begin
  if p_starts_at is null or p_ends_at is null or p_ends_at <= p_starts_at then
    return false;
  end if;

  if not public.is_valid_schedule_timezone(v_tz) then
    raise exception 'invalid_timezone';
  end if;

  v_duration_seconds := extract(epoch from (p_ends_at - p_starts_at));
  v_local_start := p_starts_at at time zone v_tz;
  v_local_hour := extract(hour from v_local_start)::integer;
  v_local_minute := extract(minute from v_local_start)::integer;
  v_local_total_minutes := v_local_hour * 60 + v_local_minute;

  if p_assignment_type = 'full_cycle' then
    return v_duration_seconds = 21600
      and v_local_minute = 0
      and v_local_hour in (0, 6, 12, 18);
  end if;

  if p_assignment_type = 'outreach_only' then
    return v_duration_seconds = 2400
      and (v_local_total_minutes % 40) = 0;
  end if;

  return false;
end;
$$;

create or replace function public.slot_overlaps_phone_rest(
  p_device_id uuid,
  p_starts_at timestamptz,
  p_ends_at timestamptz,
  p_timezone text
)
returns boolean
language plpgsql
stable
set search_path = public
as $$
declare
  v_window record;
  v_tz text := coalesce(nullif(trim(p_timezone), ''), 'UTC');
  v_slot_start_local timestamp;
  v_slot_end_local timestamp;
  v_rest_start_local timestamp;
  v_rest_end_local timestamp;
  v_day date;
begin
  if not public.is_valid_schedule_timezone(v_tz) then
    raise exception 'invalid_timezone';
  end if;

  for v_window in
    select
      prw.rest_type,
      prw.weekday,
      prw.rest_date,
      prw.starts_at_local,
      prw.ends_at_local,
      prw.timezone
    from public.phone_rest_windows prw
    where prw.device_id = p_device_id
      and prw.status = 'active'
  loop
    if not public.is_valid_schedule_timezone(v_window.timezone) then
      raise exception 'invalid_timezone';
    end if;

    v_slot_start_local := p_starts_at at time zone v_window.timezone;
    v_slot_end_local := p_ends_at at time zone v_window.timezone;

    if v_slot_end_local <= v_slot_start_local then
      v_slot_end_local := v_slot_end_local + interval '1 day';
    end if;

    for v_day in
      select generate_series(
        (v_slot_start_local::date - 1),
        v_slot_end_local::date,
        interval '1 day'
      )::date
    loop
      if v_window.rest_type = 'weekly'
         and v_window.weekday <> extract(dow from v_day)::integer then
        continue;
      end if;

      if v_window.rest_type = 'one_off'
         and v_window.rest_date <> v_day then
        continue;
      end if;

      v_rest_start_local := v_day + v_window.starts_at_local;
      v_rest_end_local := v_day + v_window.ends_at_local;
      if v_rest_end_local <= v_rest_start_local then
        v_rest_end_local := v_rest_end_local + interval '1 day';
      end if;

      if tsrange(v_slot_start_local, v_slot_end_local, '[)')
         && tsrange(v_rest_start_local, v_rest_end_local, '[)') then
        return true;
      end if;
    end loop;
  end loop;

  return false;
end;
$$;

create or replace function public.generate_assignment_slot_catalog(
  p_assignment_type text,
  p_slot_date date,
  p_timezone text
)
returns table (
  slot_index integer,
  slot_kind text,
  local_label text,
  starts_at timestamptz,
  ends_at timestamptz
)
language plpgsql
stable
set search_path = public
as $$
declare
  v_tz text := coalesce(nullif(trim(p_timezone), ''), 'UTC');
  v_day_start timestamptz;
  v_offset_minutes integer;
  v_slot_kind text;
begin
  if not public.is_valid_schedule_timezone(v_tz) then
    raise exception 'invalid_timezone';
  end if;

  v_slot_kind := public.resolve_assignment_slot_kind(p_assignment_type);
  if v_slot_kind is null then
    return;
  end if;

  v_day_start := (p_slot_date::timestamp at time zone v_tz);

  if p_assignment_type = 'full_cycle' then
    return query
    select *
    from (
      values
        (1, 'full_cycle_6h'::text, '00:00 - 06:00'::text, v_day_start + interval '0 hour', v_day_start + interval '6 hours'),
        (2, 'full_cycle_6h'::text, '06:00 - 12:00'::text, v_day_start + interval '6 hours', v_day_start + interval '12 hours'),
        (3, 'full_cycle_6h'::text, '12:00 - 18:00'::text, v_day_start + interval '12 hours', v_day_start + interval '18 hours'),
        (4, 'full_cycle_6h'::text, '18:00 - 00:00'::text, v_day_start + interval '18 hours', v_day_start + interval '24 hours')
    ) as slots(slot_index, slot_kind, local_label, starts_at, ends_at);
    return;
  end if;

  v_offset_minutes := 0;
  while v_offset_minutes < 24 * 60 loop
    slot_index := (v_offset_minutes / 40) + 1;
    slot_kind := 'outreach_40m';
    starts_at := v_day_start + make_interval(mins => v_offset_minutes);
    ends_at := v_day_start + make_interval(mins => v_offset_minutes + 40);
    local_label := to_char(starts_at at time zone v_tz, 'HH24:MI') || ' - ' || to_char(ends_at at time zone v_tz, 'HH24:MI');
    return next;
    v_offset_minutes := v_offset_minutes + 40;
  end loop;
end;
$$;

create or replace function public.slot_reserved_for_outreach_rest(
  p_device_id uuid,
  p_slot_index integer
)
returns boolean
language sql
stable
set search_path = public
as $$
  select exists (
    select 1
    from public.device_outreach_rest_policies policy
    where policy.device_id = p_device_id
      and policy.status = 'active'
      and p_slot_index = any(policy.reserved_slot_indexes)
  );
$$;

-- =============================================================================
-- 5. list_available_assignment_slots
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
  v_assignment_type text := nullif(trim(p_assignment_type), '');
  v_subscription_account_id uuid;
  v_device_timezone text := 'UTC';
  v_device_status text;
  v_device_pool_type text;
  v_slot_date date;
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

  select
    csa.id,
    cs.subscription_type
  into v_subscription_account_id, v_assignment_type
  from public.client_subscription_accounts csa
  join public.client_subscriptions cs on cs.id = csa.subscription_id
  where csa.account_id = v_account_id
    and csa.status = 'active'
    and cs.status = 'active'
  order by cs.starts_at desc
  limit 1;

  if v_assignment_type is null then
    return jsonb_build_object(
      'ok', false,
      'reason', 'subscription_not_active',
      'slots', '[]'::jsonb
    );
  end if;

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
      'slots', '[]'::jsonb
    );
  end if;

  if v_device_status not in ('available', 'active') then
    return jsonb_build_object(
      'ok', false,
      'reason', 'device_unavailable',
      'assignment_type', v_assignment_type,
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

-- =============================================================================
-- 6. assign_account_slot
-- =============================================================================
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

  select
    csa.id,
    csa.subscription_id,
    cs.client_id,
    cs.subscription_type
  into v_subscription_account_id, v_subscription_id, v_client_id, v_assignment_type
  from public.client_subscription_accounts csa
  join public.client_subscriptions cs on cs.id = csa.subscription_id
  where csa.account_id = v_account_id
    and csa.status = 'active'
    and cs.status = 'active'
  order by cs.starts_at desc
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

-- =============================================================================
-- 7. evaluate_account_schedule_gate
-- =============================================================================
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
      'window_active', false,
      'phone_rest_active', false,
      'next_eligible_starts_at', null
    );
  end if;

  if v_requested_run_type = 'outreach_session' and v_assignment.assignment_type = 'full_cycle' then
    -- full_cycle phones may still run outreach sessions per resolver parity
    null;
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

    if v_next_slot is null then
      select min(slot.starts_at)
      into v_next_slot
      from public.generate_assignment_slot_catalog(
        v_assignment.assignment_type,
        ((v_now at time zone coalesce(v_device.timezone, 'UTC'))::date + 1),
        coalesce(v_device.timezone, 'UTC')
      ) as slot
      where not exists (
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
  end if;

  return jsonb_build_object(
    'ok', v_ok,
    'reason', coalesce(v_reason, 'assignment_window_open'),
    'assignment_id', v_assignment.id,
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
-- 8. Grants
-- =============================================================================
revoke all on function public.normalize_assignment_slot_kind(text) from public;
revoke all on function public.resolve_assignment_slot_kind(text) from public;
revoke all on function public.is_valid_schedule_timezone(text) from public;
revoke all on function public.validate_phone_device_timezone() from public;
revoke all on function public.validate_phone_rest_window() from public;
revoke all on function public.slot_overlaps_phone_rest(uuid, timestamptz, timestamptz, text) from public;
revoke all on function public.generate_assignment_slot_catalog(text, date, text) from public;
revoke all on function public.validate_assignment_slot_window(text, timestamptz, timestamptz, text) from public;
revoke all on function public.slot_reserved_for_outreach_rest(uuid, integer) from public;
revoke all on function public.list_available_assignment_slots(uuid, uuid, text, date) from public;
revoke all on function public.assign_account_slot(uuid, uuid, timestamptz, timestamptz, uuid, text, uuid) from public;
revoke all on function public.evaluate_account_schedule_gate(uuid, text) from public;

revoke all on function public.normalize_assignment_slot_kind(text) from anon;
revoke all on function public.resolve_assignment_slot_kind(text) from anon;
revoke all on function public.is_valid_schedule_timezone(text) from anon;
revoke all on function public.validate_phone_device_timezone() from anon;
revoke all on function public.validate_phone_rest_window() from anon;
revoke all on function public.slot_overlaps_phone_rest(uuid, timestamptz, timestamptz, text) from anon;
revoke all on function public.generate_assignment_slot_catalog(text, date, text) from anon;
revoke all on function public.validate_assignment_slot_window(text, timestamptz, timestamptz, text) from anon;
revoke all on function public.slot_reserved_for_outreach_rest(uuid, integer) from anon;
revoke all on function public.list_available_assignment_slots(uuid, uuid, text, date) from anon;
revoke all on function public.assign_account_slot(uuid, uuid, timestamptz, timestamptz, uuid, text, uuid) from anon;
revoke all on function public.evaluate_account_schedule_gate(uuid, text) from anon;

revoke all on function public.normalize_assignment_slot_kind(text) from authenticated;
revoke all on function public.resolve_assignment_slot_kind(text) from authenticated;
revoke all on function public.is_valid_schedule_timezone(text) from authenticated;
revoke all on function public.validate_phone_device_timezone() from authenticated;
revoke all on function public.validate_phone_rest_window() from authenticated;
revoke all on function public.slot_overlaps_phone_rest(uuid, timestamptz, timestamptz, text) from authenticated;
revoke all on function public.generate_assignment_slot_catalog(text, date, text) from authenticated;
revoke all on function public.validate_assignment_slot_window(text, timestamptz, timestamptz, text) from authenticated;
revoke all on function public.slot_reserved_for_outreach_rest(uuid, integer) from authenticated;
revoke all on function public.list_available_assignment_slots(uuid, uuid, text, date) from authenticated;
revoke all on function public.assign_account_slot(uuid, uuid, timestamptz, timestamptz, uuid, text, uuid) from authenticated;
revoke all on function public.evaluate_account_schedule_gate(uuid, text) from authenticated;

grant execute on function public.normalize_assignment_slot_kind(text) to service_role;
grant execute on function public.resolve_assignment_slot_kind(text) to service_role;
grant execute on function public.is_valid_schedule_timezone(text) to service_role;
grant execute on function public.validate_phone_device_timezone() to service_role;
grant execute on function public.validate_phone_rest_window() to service_role;
grant execute on function public.slot_overlaps_phone_rest(uuid, timestamptz, timestamptz, text) to service_role;
grant execute on function public.generate_assignment_slot_catalog(text, date, text) to service_role;
grant execute on function public.validate_assignment_slot_window(text, timestamptz, timestamptz, text) to service_role;
grant execute on function public.slot_reserved_for_outreach_rest(uuid, integer) to service_role;
grant execute on function public.list_available_assignment_slots(uuid, uuid, text, date) to service_role;
grant execute on function public.assign_account_slot(uuid, uuid, timestamptz, timestamptz, uuid, text, uuid) to service_role;
grant execute on function public.evaluate_account_schedule_gate(uuid, text) to service_role;

comment on function public.list_available_assignment_slots(uuid, uuid, text, date) is
  'Returns phone-local slot catalog with availability, occupied reason, and explicit fixed-blackout blocking. Does not create default rest slots.';

comment on function public.assign_account_slot(uuid, uuid, timestamptz, timestamptz, uuid, text, uuid) is
  'Transactionally assigns an account to a phone slot with phone-wide anti-collision and explicit fixed-blackout validation.';

comment on function public.evaluate_account_schedule_gate(uuid, text) is
  'Evaluates whether an account may start now based on assignment window and explicit fixed-blackout windows.';
