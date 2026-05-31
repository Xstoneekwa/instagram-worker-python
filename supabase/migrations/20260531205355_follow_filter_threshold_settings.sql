-- Follow filter threshold settings.
-- Extends the existing account-level Follow settings table used by the worker.

alter table public.ig_account_follow_settings
  add column if not exists min_followers integer,
  add column if not exists max_followers integer,
  add column if not exists min_posts integer;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'ig_account_follow_settings_min_followers_nonnegative'
  ) then
    alter table public.ig_account_follow_settings
      add constraint ig_account_follow_settings_min_followers_nonnegative
      check (min_followers is null or min_followers >= 0);
  end if;

  if not exists (
    select 1
    from pg_constraint
    where conname = 'ig_account_follow_settings_max_followers_nonnegative'
  ) then
    alter table public.ig_account_follow_settings
      add constraint ig_account_follow_settings_max_followers_nonnegative
      check (max_followers is null or max_followers >= 0);
  end if;

  if not exists (
    select 1
    from pg_constraint
    where conname = 'ig_account_follow_settings_min_posts_nonnegative'
  ) then
    alter table public.ig_account_follow_settings
      add constraint ig_account_follow_settings_min_posts_nonnegative
      check (min_posts is null or min_posts >= 0);
  end if;

  if not exists (
    select 1
    from pg_constraint
    where conname = 'ig_account_follow_settings_followers_range_valid'
  ) then
    alter table public.ig_account_follow_settings
      add constraint ig_account_follow_settings_followers_range_valid
      check (
        min_followers is null
        or max_followers is null
        or min_followers <= max_followers
      );
  end if;
end $$;

create index if not exists ig_account_follow_settings_thresholds_idx
  on public.ig_account_follow_settings (min_followers, max_followers, min_posts)
  where min_followers is not null
     or max_followers is not null
     or min_posts is not null;
