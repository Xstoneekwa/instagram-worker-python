-- P1c follow target metrics foundation.
--
-- Local additive migration only: no remote apply, no runtime run, no auto-archive,
-- and no automatic performance status changes.

alter table public.ig_targets
  add column if not exists follows_sent_count integer not null default 0,
  add column if not exists followbacks_count integer not null default 0,
  add column if not exists followback_ratio numeric,
  add column if not exists last_selected_at timestamptz,
  add column if not exists last_used_at timestamptz,
  add column if not exists last_successful_candidate_at timestamptz,
  add column if not exists last_exhausted_at timestamptz,
  add column if not exists exhaustion_reason text,
  add column if not exists cooldown_until timestamptz,
  add column if not exists metrics_updated_at timestamptz;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'ig_targets_follows_sent_count_p1c_check'
      and conrelid = 'public.ig_targets'::regclass
  ) then
    alter table public.ig_targets
      add constraint ig_targets_follows_sent_count_p1c_check
        check (follows_sent_count >= 0) not valid;
  end if;

  if not exists (
    select 1
    from pg_constraint
    where conname = 'ig_targets_followbacks_count_p1c_check'
      and conrelid = 'public.ig_targets'::regclass
  ) then
    alter table public.ig_targets
      add constraint ig_targets_followbacks_count_p1c_check
        check (followbacks_count >= 0) not valid;
  end if;

  if not exists (
    select 1
    from pg_constraint
    where conname = 'ig_targets_followback_ratio_p1c_check'
      and conrelid = 'public.ig_targets'::regclass
  ) then
    alter table public.ig_targets
      add constraint ig_targets_followback_ratio_p1c_check
        check (followback_ratio is null or followback_ratio >= 0) not valid;
  end if;

  if not exists (
    select 1
    from pg_constraint
    where conname = 'ig_targets_exhaustion_reason_p1c_check'
      and conrelid = 'public.ig_targets'::regclass
  ) then
    alter table public.ig_targets
      add constraint ig_targets_exhaustion_reason_p1c_check
        check (exhaustion_reason is null or char_length(exhaustion_reason) <= 500) not valid;
  end if;
end
$$;

create or replace function public.set_ig_target_followback_ratio_p1c()
returns trigger
language plpgsql
as $$
begin
  if new.follows_sent_count > 0 then
    new.followback_ratio := round(
      (new.followbacks_count::numeric / new.follows_sent_count::numeric) * 100,
      4
    );
  else
    new.followback_ratio := null;
  end if;
  return new;
end;
$$;

do $$
begin
  if not exists (
    select 1
    from pg_trigger
    where tgname = 'set_ig_target_followback_ratio_p1c'
      and tgrelid = 'public.ig_targets'::regclass
  ) then
    create trigger set_ig_target_followback_ratio_p1c
      before insert or update of follows_sent_count, followbacks_count
      on public.ig_targets
      for each row
      execute function public.set_ig_target_followback_ratio_p1c();
  end if;
end
$$;

create or replace function public.increment_ig_target_follows_sent_p1c(
  p_target_id uuid,
  p_account_id uuid default null,
  p_last_successful_candidate_at timestamptz default now()
)
returns table (
  follows_sent_count integer,
  followback_ratio numeric
)
language plpgsql
as $$
begin
  return query
  update public.ig_targets as target
  set
    follows_sent_count = target.follows_sent_count + 1,
    last_successful_candidate_at = p_last_successful_candidate_at,
    last_used_at = p_last_successful_candidate_at,
    metrics_updated_at = p_last_successful_candidate_at,
    updated_at = p_last_successful_candidate_at
  where target.id = p_target_id
    and (p_account_id is null or target.account_id = p_account_id)
  returning target.follows_sent_count, target.followback_ratio;
end;
$$;

create index if not exists ig_targets_account_last_used_p1c_idx
  on public.ig_targets (account_id, last_used_at desc);

create index if not exists ig_targets_account_cooldown_until_p1c_idx
  on public.ig_targets (account_id, cooldown_until)
  where cooldown_until is not null;

create index if not exists ig_targets_account_last_exhausted_p1c_idx
  on public.ig_targets (account_id, last_exhausted_at desc)
  where last_exhausted_at is not null;

alter table public.ig_interaction_events
  add column if not exists target_id uuid references public.ig_targets(id) on delete set null;

create index if not exists ig_interaction_events_target_id_p1c_idx
  on public.ig_interaction_events (target_id);

create index if not exists ig_interaction_events_account_target_event_at_p1c_idx
  on public.ig_interaction_events (account_id, target_id, event_at desc)
  where target_id is not null;

alter table public.ig_targets enable row level security;
alter table public.ig_interaction_events enable row level security;

do $$
begin
  if not exists (
    select 1
    from pg_policies
    where schemaname = 'public'
      and tablename = 'ig_interaction_events'
      and policyname = 'ig_interaction_events_service_role_all'
  ) then
    create policy ig_interaction_events_service_role_all
      on public.ig_interaction_events
      for all
      using ((select auth.role()) = 'service_role')
      with check ((select auth.role()) = 'service_role');
  end if;
end
$$;

revoke all on public.ig_interaction_events from public;
revoke all on public.ig_interaction_events from anon;
revoke all on public.ig_interaction_events from authenticated;
grant all on public.ig_interaction_events to service_role;

revoke all on function public.set_ig_target_followback_ratio_p1c() from public;
revoke all on function public.set_ig_target_followback_ratio_p1c() from anon;
revoke all on function public.set_ig_target_followback_ratio_p1c() from authenticated;
grant execute on function public.set_ig_target_followback_ratio_p1c() to service_role;

revoke all on function public.increment_ig_target_follows_sent_p1c(uuid, uuid, timestamptz) from public;
revoke all on function public.increment_ig_target_follows_sent_p1c(uuid, uuid, timestamptz) from anon;
revoke all on function public.increment_ig_target_follows_sent_p1c(uuid, uuid, timestamptz) from authenticated;
grant execute on function public.increment_ig_target_follows_sent_p1c(uuid, uuid, timestamptz) to service_role;
