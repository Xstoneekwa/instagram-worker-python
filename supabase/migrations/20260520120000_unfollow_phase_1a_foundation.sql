-- Phase 1A — Unfollow foundation: account settings + ig_interacted_users eligibility columns.
-- Idempotent; safe to re-apply.

-- =============================================================================
-- 1. ig_account_unfollow_settings (one row per Instagram account)
-- =============================================================================
create table if not exists public.ig_account_unfollow_settings (
  account_id uuid primary key,
  unfollow_enabled boolean not null default false,
  unfollow_only boolean not null default false,
  do_unfollow_first boolean not null default false,
  unfollow_after_days integer not null default 3,
  unfollow_mode text not null default 'unfollow',
  unfollow_sort_mode text not null default 'default',
  unfollow_per_session_limit integer not null default 50,
  unfollow_per_day_limit integer not null default 200,
  package_default_snapshot jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint ig_account_unfollow_settings_after_days_nonnegative
    check (unfollow_after_days >= 0),
  constraint ig_account_unfollow_settings_session_limit_positive
    check (unfollow_per_session_limit >= 0),
  constraint ig_account_unfollow_settings_day_limit_positive
    check (unfollow_per_day_limit >= 0),
  constraint ig_account_unfollow_settings_mode_valid
    check (
      unfollow_mode in (
        'unfollow',
        'unfollow-non-followers',
        'unfollow-any',
        'unfollow-any-non-followers',
        'unfollow-any-followers'
      )
    ),
  constraint ig_account_unfollow_settings_sort_mode_valid
    check (
      unfollow_sort_mode in (
        'default',
        'newest-to-oldest',
        'oldest-to-newest'
      )
    )
);

create index if not exists ig_account_unfollow_settings_enabled_idx
  on public.ig_account_unfollow_settings (unfollow_enabled)
  where unfollow_enabled = true;

drop trigger if exists ig_account_unfollow_settings_set_updated_at
  on public.ig_account_unfollow_settings;
create trigger ig_account_unfollow_settings_set_updated_at
  before update on public.ig_account_unfollow_settings
  for each row execute function public.set_updated_at();

do $$
begin
  if exists (
    select 1 from information_schema.tables
    where table_schema = 'public' and table_name = 'ig_accounts'
  ) then
    if not exists (
      select 1 from information_schema.table_constraints
      where constraint_schema = 'public'
        and table_name = 'ig_account_unfollow_settings'
        and constraint_name = 'ig_account_unfollow_settings_account_id_fkey'
    ) then
      alter table public.ig_account_unfollow_settings
        add constraint ig_account_unfollow_settings_account_id_fkey
        foreign key (account_id) references public.ig_accounts (id)
        on delete cascade;
    end if;
  end if;
end $$;

-- =============================================================================
-- 2. ig_interacted_users — unfollow eligibility columns
-- =============================================================================
alter table public.ig_interacted_users
  add column if not exists followed_by_bot boolean;

alter table public.ig_interacted_users
  add column if not exists eligible_unfollow_at timestamptz;

alter table public.ig_interacted_users
  add column if not exists unfollow_mode_applied text;

alter table public.ig_interacted_users
  add column if not exists unfollow_result text;

alter table public.ig_interacted_users
  add column if not exists unfollow_attempts integer not null default 0;

alter table public.ig_interacted_users
  add column if not exists last_unfollow_attempt_at timestamptz;

alter table public.ig_interacted_users
  add column if not exists unfollow_skip_reason text;

alter table public.ig_interacted_users
  add column if not exists whitelist_protected boolean not null default false;

-- Lifecycle column used by worker patches (idempotent if already present elsewhere).
alter table public.ig_interacted_users
  add column if not exists interaction_lifecycle_state text;

-- Retroactive: rows with a bot-recorded follow timestamp are treated as bot-followed.
update public.ig_interacted_users
set followed_by_bot = true
where followed_at is not null
  and followed_by_bot is distinct from true
  and lower(trim(coalesce(interaction_type, ''))) = 'follow'
  and coalesce(was_successful, false) = true;

create index if not exists ig_interacted_users_unfollow_strict_eligible_idx
  on public.ig_interacted_users (
    account_id,
    interaction_lifecycle_state,
    eligible_unfollow_at
  )
  where unfollowed_at is null
    and followed_by_bot = true
    and whitelist_protected = false
    and followed_at is not null;
