alter table public.ig_account_unfollow_settings
  add column if not exists runtime_cap_mode text not null default 'prod_normal',
  add column if not exists runtime_safety_cap integer;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'ig_account_unfollow_settings_runtime_cap_mode_check'
  ) then
    alter table public.ig_account_unfollow_settings
      add constraint ig_account_unfollow_settings_runtime_cap_mode_check
      check (runtime_cap_mode in ('mini_run', 'prod_normal', 'incident_safety'));
  end if;

  if not exists (
    select 1
    from pg_constraint
    where conname = 'ig_account_unfollow_settings_runtime_safety_cap_check'
  ) then
    alter table public.ig_account_unfollow_settings
      add constraint ig_account_unfollow_settings_runtime_safety_cap_check
      check (runtime_safety_cap is null or runtime_safety_cap >= 0);
  end if;
end $$;
