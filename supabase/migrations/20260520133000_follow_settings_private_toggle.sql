-- Phase Follow settings — account-level private-profile follow toggle.
-- Idempotent; safe to re-apply.

create table if not exists public.ig_account_follow_settings (
  account_id uuid primary key,
  dont_follow_private_accounts boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

drop trigger if exists ig_account_follow_settings_set_updated_at
  on public.ig_account_follow_settings;
create trigger ig_account_follow_settings_set_updated_at
  before update on public.ig_account_follow_settings
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
        and table_name = 'ig_account_follow_settings'
        and constraint_name = 'ig_account_follow_settings_account_id_fkey'
    ) then
      alter table public.ig_account_follow_settings
        add constraint ig_account_follow_settings_account_id_fkey
        foreign key (account_id) references public.ig_accounts (id)
        on delete cascade;
    end if;
  end if;
end $$;

create index if not exists ig_account_follow_settings_private_skip_idx
  on public.ig_account_follow_settings (dont_follow_private_accounts)
  where dont_follow_private_accounts = true;
