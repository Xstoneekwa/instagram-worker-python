-- Never Follow Twice V1
-- Canonical authority remains ig_interaction_events.  The two columns below
-- are a monotone read projection only; mutable relationship state is untouched.

alter table public.ig_interacted_users
  add column if not exists ever_followed_canonical_at timestamptz,
  add column if not exists ever_followed_action_id uuid;

comment on column public.ig_interacted_users.ever_followed_canonical_at is
  'Monotone projection of the first canonical successful Follow event. Never cleared by Unfollow.';
comment on column public.ig_interacted_users.ever_followed_action_id is
  'Action id of the first canonical successful Follow event used by Never Follow Twice V1.';

create or replace function public.guard_never_follow_twice_v1()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_username text := lower(ltrim(trim(coalesce(new.username, '')), '@'));
begin
  if new.event_type <> 'follow_verified_persisted_v1' then
    return new;
  end if;
  if new.account_id is null or v_username = '' then
    raise exception 'never_follow_twice_identity_required' using errcode = '22023';
  end if;

  -- Serialize distinct action ids for the immutable account + username key.
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(new.account_id::text || ':' || v_username, 0)
  );

  if exists (
    select 1
    from public.ig_interaction_events as prior
    where prior.account_id = new.account_id
      and lower(ltrim(trim(coalesce(prior.username, '')), '@')) = v_username
      and prior.event_type = 'follow_verified_persisted_v1'
      and prior.event_status = 'success'
      and prior.id is distinct from new.id
  ) then
    raise exception 'follow_persistence_canonical_once_already_exists'
      using errcode = '23505';
  end if;
  return new;
end;
$$;

drop trigger if exists trg_never_follow_twice_v1
  on public.ig_interaction_events;
create trigger trg_never_follow_twice_v1
before insert or update of account_id, username, event_type, event_status
on public.ig_interaction_events
for each row
when (new.event_type = 'follow_verified_persisted_v1')
execute function public.guard_never_follow_twice_v1();

create or replace function public.project_ever_followed_canonical_v1()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_username text := lower(ltrim(trim(coalesce(new.username, '')), '@'));
begin
  if new.event_type <> 'follow_verified_persisted_v1'
     or new.event_status <> 'success'
     or new.account_id is null
     or v_username = '' then
    return new;
  end if;

  update public.ig_interacted_users as iu
  set ever_followed_canonical_at = coalesce(iu.ever_followed_canonical_at, new.event_at),
      ever_followed_action_id = coalesce(iu.ever_followed_action_id, new.id)
  where iu.account_id = new.account_id
    and lower(ltrim(trim(coalesce(iu.username, '')), '@')) = v_username;
  if not found then
    -- The canonical RPC owns creation of the interaction row. Never commit a
    -- successful event without its monotone future-admission projection.
    raise exception 'ever_followed_projection_interaction_row_missing'
      using errcode = '23503';
  end if;
  return new;
end;
$$;

drop trigger if exists trg_project_ever_followed_canonical_v1
  on public.ig_interaction_events;
create trigger trg_project_ever_followed_canonical_v1
after insert or update of event_status on public.ig_interaction_events
for each row
when (
  new.event_type = 'follow_verified_persisted_v1'
  and new.event_status = 'success'
)
execute function public.project_ever_followed_canonical_v1();

create or replace function public.guard_ever_followed_projection_v1()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_username text := lower(ltrim(trim(coalesce(new.username, '')), '@'));
begin
  if tg_op = 'UPDATE'
     and old.ever_followed_canonical_at is not null
     and (
       new.ever_followed_canonical_at is distinct from old.ever_followed_canonical_at
       or new.ever_followed_action_id is distinct from old.ever_followed_action_id
     ) then
    raise exception 'ever_followed_projection_is_monotone' using errcode = '23514';
  end if;
  if new.ever_followed_canonical_at is null
     and new.ever_followed_action_id is null then
    return new;
  end if;
  if new.ever_followed_canonical_at is null
     or new.ever_followed_action_id is null then
    raise exception 'ever_followed_projection_incomplete' using errcode = '23514';
  end if;
  if not exists (
    select 1
    from public.ig_interaction_events as event
    where event.id = new.ever_followed_action_id
      and event.account_id = new.account_id
      and lower(ltrim(trim(coalesce(event.username, '')), '@')) = v_username
      and event.event_type = 'follow_verified_persisted_v1'
      and event.event_status = 'success'
      and event.event_at is not distinct from new.ever_followed_canonical_at
  ) then
    raise exception 'ever_followed_projection_canonical_proof_missing'
      using errcode = '23514';
  end if;
  return new;
end;
$$;

drop trigger if exists trg_guard_ever_followed_projection_v1
  on public.ig_interacted_users;
create trigger trg_guard_ever_followed_projection_v1
before insert or update of account_id, username, ever_followed_canonical_at, ever_followed_action_id
on public.ig_interacted_users
for each row
execute function public.guard_ever_followed_projection_v1();

create or replace function public.guard_ever_followed_row_delete_v1()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if old.ever_followed_canonical_at is not null
     or old.ever_followed_action_id is not null then
    raise exception 'ever_followed_projection_row_delete_forbidden'
      using errcode = '23514';
  end if;
  return old;
end;
$$;

drop trigger if exists trg_guard_ever_followed_row_delete_v1
  on public.ig_interacted_users;
create trigger trg_guard_ever_followed_row_delete_v1
before delete on public.ig_interacted_users
for each row
execute function public.guard_ever_followed_row_delete_v1();

-- Idempotent projection-only backfill.  No receipt/event/counter or mutable
-- relationship/lifecycle field is written.
with canonical_first as (
  select distinct on (
    event.account_id,
    lower(ltrim(trim(coalesce(event.username, '')), '@'))
  )
    event.account_id,
    lower(ltrim(trim(coalesce(event.username, '')), '@')) as normalized_username,
    event.event_at,
    event.id as action_id
  from public.ig_interaction_events as event
  where event.event_type = 'follow_verified_persisted_v1'
    and event.event_status = 'success'
    and event.account_id is not null
    and lower(ltrim(trim(coalesce(event.username, '')), '@')) <> ''
  order by event.account_id, normalized_username, event.event_at, event.id
)
update public.ig_interacted_users as iu
set ever_followed_canonical_at = coalesce(iu.ever_followed_canonical_at, first.event_at),
    ever_followed_action_id = coalesce(iu.ever_followed_action_id, first.action_id)
from canonical_first as first
where iu.account_id = first.account_id
  and lower(ltrim(trim(coalesce(iu.username, '')), '@')) = first.normalized_username
  and (
    iu.ever_followed_canonical_at is null
    or iu.ever_followed_action_id is null
  );

revoke all on function public.guard_never_follow_twice_v1() from public;
revoke all on function public.project_ever_followed_canonical_v1() from public;
revoke all on function public.guard_ever_followed_projection_v1() from public;
revoke all on function public.guard_ever_followed_row_delete_v1() from public;
