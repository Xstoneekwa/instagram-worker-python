create table if not exists public.account_follow_source_settings (
  account_id uuid primary key references public.ig_accounts(id) on delete cascade,
  max_follows_per_target_per_run integer not null default 2,
  max_targets_per_run integer not null default 3,
  updated_at timestamptz not null default now(),
  updated_by text,
  metadata jsonb not null default '{}'::jsonb,
  constraint account_follow_source_settings_max_follows_per_target_per_run_check
    check (max_follows_per_target_per_run between 1 and 50),
  constraint account_follow_source_settings_max_targets_per_run_check
    check (max_targets_per_run between 1 and 10),
  constraint account_follow_source_settings_metadata_object_check
    check (jsonb_typeof(metadata) = 'object')
);

alter table public.account_follow_source_settings enable row level security;

grant all on table public.account_follow_source_settings to service_role;

create index if not exists account_follow_source_settings_updated_at_idx
  on public.account_follow_source_settings (updated_at desc);

comment on table public.account_follow_source_settings is
  'Per-account Follow source rotation settings for runtime workers and admin dashboard.';
comment on column public.account_follow_source_settings.max_follows_per_target_per_run is
  'Maximum follows from one target during one run before trying the next target. Does not raise global follow caps.';
comment on column public.account_follow_source_settings.max_targets_per_run is
  'Maximum number of target/source accounts the worker may try during one run.';
