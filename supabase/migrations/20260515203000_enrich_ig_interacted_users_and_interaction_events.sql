-- Enrich ig_interacted_users (state) + create ig_interaction_events (append-only audit).
-- Idempotent: safe to re-apply.

-- --- ig_interacted_users: new columns ---
alter table public.ig_interacted_users add column if not exists first_source_profile text;
alter table public.ig_interacted_users add column if not exists last_source_profile text;

alter table public.ig_interacted_users add column if not exists first_run_id uuid;
alter table public.ig_interacted_users add column if not exists last_run_id uuid;

alter table public.ig_interacted_users add column if not exists last_session_id text;

alter table public.ig_interacted_users add column if not exists follow_requested_at timestamptz;

alter table public.ig_interacted_users add column if not exists muted_posts boolean default false;
alter table public.ig_interacted_users add column if not exists muted_stories boolean default false;
alter table public.ig_interacted_users add column if not exists last_muted_at timestamptz;

alter table public.ig_interacted_users add column if not exists posts_liked_count integer default 0;
alter table public.ig_interacted_users add column if not exists stories_liked_count integer default 0;
alter table public.ig_interacted_users add column if not exists stories_watched_count integer default 0;

alter table public.ig_interacted_users add column if not exists dm_sent boolean default false;
alter table public.ig_interacted_users add column if not exists welcome_dm_sent boolean default false;

alter table public.ig_interacted_users add column if not exists is_following_back boolean;
alter table public.ig_interacted_users add column if not exists followback_detected_at timestamptz;

create index if not exists ig_interacted_users_first_source_profile_idx
  on public.ig_interacted_users (first_source_profile);

create index if not exists ig_interacted_users_last_source_profile_idx
  on public.ig_interacted_users (last_source_profile);

create index if not exists ig_interacted_users_followback_idx
  on public.ig_interacted_users (is_following_back);

-- --- ig_interaction_events ---
create table if not exists public.ig_interaction_events (
  id uuid primary key default gen_random_uuid(),
  account_id uuid,
  run_id uuid,
  session_id text,
  username text not null,
  source_profile text,
  event_type text not null,
  event_status text not null default 'success',
  event_reason text,
  event_at timestamptz default now(),
  created_at timestamptz default now(),
  payload jsonb default '{}'::jsonb
);

create index if not exists ig_interaction_events_account_username_event_at_idx
  on public.ig_interaction_events (account_id, username, event_at desc);

create index if not exists ig_interaction_events_account_source_event_at_idx
  on public.ig_interaction_events (account_id, source_profile, event_at desc);

create index if not exists ig_interaction_events_event_type_event_at_idx
  on public.ig_interaction_events (event_type, event_at desc);

create index if not exists ig_interaction_events_run_id_idx
  on public.ig_interaction_events (run_id);
