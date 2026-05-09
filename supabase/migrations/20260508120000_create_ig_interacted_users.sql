-- Persistent interaction memory: one row per (account_id, username).
-- Apply in Supabase SQL editor or via supabase db push / migration runner.

create table if not exists public.ig_interacted_users (
  id uuid primary key default gen_random_uuid(),
  account_id uuid not null,
  run_id uuid,
  username text not null,
  source_profile text,
  interaction_type text not null,
  follow_status text,
  was_successful boolean default false,
  followed_at timestamptz,
  unfollowed_at timestamptz,
  last_interaction_at timestamptz default now(),
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  payload jsonb default '{}'::jsonb
);

create unique index if not exists ig_interacted_users_account_id_username_key
  on public.ig_interacted_users (account_id, username);

create index if not exists ig_interacted_users_account_id_idx
  on public.ig_interacted_users (account_id);

create index if not exists ig_interacted_users_username_idx
  on public.ig_interacted_users (username);

create index if not exists ig_interacted_users_source_profile_idx
  on public.ig_interacted_users (source_profile);

create index if not exists ig_interacted_users_interaction_type_idx
  on public.ig_interacted_users (interaction_type);

create index if not exists ig_interacted_users_last_interaction_at_idx
  on public.ig_interacted_users (last_interaction_at desc);
