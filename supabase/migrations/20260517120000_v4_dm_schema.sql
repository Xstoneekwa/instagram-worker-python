-- V4.0 — DM architecture (Welcome + Outreach)
-- Idempotent where practical. Requires public.ig_accounts (uuid id).
-- Optional: public.ig_runs for source_scan_run_id FK.
--
-- Execution order: run this file top-to-bottom in Supabase SQL editor or via migration runner.

-- =============================================================================
-- 0. Prerequisites (no-op if already present)
-- =============================================================================
create extension if not exists pgcrypto;

-- =============================================================================
-- 1. ENUM types
-- =============================================================================
do $$
begin
  if not exists (select 1 from pg_type where typname = 'welcome_dm_status') then
    create type public.welcome_dm_status as enum (
      'not_eligible_baseline',
      'pending',
      'reserved',
      'sent',
      'skipped',
      'failed'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'dm_type') then
    create type public.dm_type as enum ('welcome', 'outreach');
  end if;

  if not exists (select 1 from pg_type where typname = 'dm_job_source') then
    create type public.dm_job_source as enum (
      'welcome_scan',
      'n8n',
      'dashboard',
      'campaign',
      'manual'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'dm_job_status') then
    create type public.dm_job_status as enum (
      'pending',
      'reserved',
      'running',
      'sent',
      'skipped',
      'failed',
      'cancelled'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'dm_template_type') then
    create type public.dm_template_type as enum ('welcome', 'outreach');
  end if;
end
$$;

-- =============================================================================
-- 2. Shared trigger: updated_at
-- =============================================================================
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- =============================================================================
-- 3. ig_dm_templates (per-account message bodies; replaces pm_welcome.txt files)
-- =============================================================================
create table if not exists public.ig_dm_templates (
  id uuid primary key default gen_random_uuid(),
  account_id uuid not null,
  template_type public.dm_template_type not null,
  name text not null default 'default',
  body text not null,
  is_default boolean not null default false,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint ig_dm_templates_name_nonempty check (char_length(trim(name)) > 0),
  constraint ig_dm_templates_body_nonempty check (char_length(trim(body)) > 0)
);

create unique index if not exists ig_dm_templates_account_type_name_key
  on public.ig_dm_templates (account_id, template_type, lower(trim(name)));

-- At most one default template per (account_id, template_type) when active.
create unique index if not exists ig_dm_templates_one_default_per_type_key
  on public.ig_dm_templates (account_id, template_type)
  where (is_default = true and active = true);

create index if not exists ig_dm_templates_account_id_idx
  on public.ig_dm_templates (account_id);

create index if not exists ig_dm_templates_account_type_active_idx
  on public.ig_dm_templates (account_id, template_type, active);

drop trigger if exists ig_dm_templates_set_updated_at on public.ig_dm_templates;
create trigger ig_dm_templates_set_updated_at
  before update on public.ig_dm_templates
  for each row execute function public.set_updated_at();

-- FK to ig_accounts (add only if table exists)
do $$
begin
  if exists (
    select 1 from information_schema.tables
    where table_schema = 'public' and table_name = 'ig_accounts'
  ) then
    if not exists (
      select 1 from information_schema.table_constraints
      where constraint_schema = 'public'
        and constraint_name = 'ig_dm_templates_account_id_fkey'
    ) then
      alter table public.ig_dm_templates
        add constraint ig_dm_templates_account_id_fkey
        foreign key (account_id) references public.ig_accounts (id)
        on delete cascade;
    end if;
  end if;
end
$$;

-- =============================================================================
-- 4. ig_account_dm_settings (one row per Instagram account)
-- =============================================================================
create table if not exists public.ig_account_dm_settings (
  account_id uuid primary key,
  welcome_enabled boolean not null default false,
  outreach_enabled boolean not null default false,
  welcome_template_id uuid,
  default_outreach_template_id uuid,
  welcome_per_session_limit integer not null default 10,
  welcome_per_day_limit integer not null default 50,
  outreach_per_session_limit integer not null default 25,
  outreach_per_day_limit integer not null default 80,
  total_dm_per_day_limit integer not null default 100,
  check_chat_before_welcome boolean not null default true,
  welcome_skip_if_existing_thread boolean not null default true,
  outreach_skip_if_existing_thread boolean not null default true,
  max_welcoming_skips integer not null default 6,
  random_delay_min_seconds integer not null default 3,
  random_delay_max_seconds integer not null default 12,
  welcome_baseline_completed_at timestamptz,
  welcome_baseline_scan_run_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint ig_account_dm_settings_welcome_session_limit_positive
    check (welcome_per_session_limit >= 0),
  constraint ig_account_dm_settings_welcome_day_limit_positive
    check (welcome_per_day_limit >= 0),
  constraint ig_account_dm_settings_outreach_session_limit_positive
    check (outreach_per_session_limit >= 0),
  constraint ig_account_dm_settings_outreach_day_limit_positive
    check (outreach_per_day_limit >= 0),
  constraint ig_account_dm_settings_total_dm_day_limit_positive
    check (total_dm_per_day_limit >= 0),
  constraint ig_account_dm_settings_max_welcoming_skips_positive
    check (max_welcoming_skips >= 0),
  constraint ig_account_dm_settings_random_delay_order
    check (random_delay_max_seconds >= random_delay_min_seconds)
);

create index if not exists ig_account_dm_settings_welcome_enabled_idx
  on public.ig_account_dm_settings (welcome_enabled)
  where welcome_enabled = true;

create index if not exists ig_account_dm_settings_outreach_enabled_idx
  on public.ig_account_dm_settings (outreach_enabled)
  where outreach_enabled = true;

drop trigger if exists ig_account_dm_settings_set_updated_at on public.ig_account_dm_settings;
create trigger ig_account_dm_settings_set_updated_at
  before update on public.ig_account_dm_settings
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
        and constraint_name = 'ig_account_dm_settings_account_id_fkey'
    ) then
      alter table public.ig_account_dm_settings
        add constraint ig_account_dm_settings_account_id_fkey
        foreign key (account_id) references public.ig_accounts (id)
        on delete cascade;
    end if;
  end if;

  if not exists (
    select 1 from information_schema.table_constraints
    where constraint_schema = 'public'
      and constraint_name = 'ig_account_dm_settings_welcome_template_id_fkey'
  ) then
    alter table public.ig_account_dm_settings
      add constraint ig_account_dm_settings_welcome_template_id_fkey
      foreign key (welcome_template_id) references public.ig_dm_templates (id)
      on delete set null;
  end if;

  if not exists (
    select 1 from information_schema.table_constraints
    where constraint_schema = 'public'
      and constraint_name = 'ig_account_dm_settings_default_outreach_template_id_fkey'
  ) then
    alter table public.ig_account_dm_settings
      add constraint ig_account_dm_settings_default_outreach_template_id_fkey
      foreign key (default_outreach_template_id) references public.ig_dm_templates (id)
      on delete set null;
  end if;

  if exists (
    select 1 from information_schema.tables
    where table_schema = 'public' and table_name = 'ig_runs'
  ) then
    if not exists (
      select 1 from information_schema.table_constraints
      where constraint_schema = 'public'
        and constraint_name = 'ig_account_dm_settings_baseline_scan_run_id_fkey'
    ) then
      alter table public.ig_account_dm_settings
        add constraint ig_account_dm_settings_baseline_scan_run_id_fkey
        foreign key (welcome_baseline_scan_run_id) references public.ig_runs (id)
        on delete set null;
    end if;
  end if;
end
$$;

-- =============================================================================
-- 5. ig_account_followers (long-term follower memory)
-- =============================================================================
create table if not exists public.ig_account_followers (
  id uuid primary key default gen_random_uuid(),
  account_id uuid not null,
  follower_username text not null,
  follower_username_normalized text generated always as (lower(trim(follower_username))) stored,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  baseline_existing boolean not null default false,
  welcome_dm_status public.welcome_dm_status not null default 'pending',
  welcomed_at timestamptz,
  dm_thread_checked_at timestamptz,
  skip_reason text,
  last_error text,
  source_scan_run_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint ig_account_followers_username_nonempty
    check (char_length(trim(follower_username)) > 0),
  constraint ig_account_followers_username_normalized_nonempty
    check (char_length(follower_username_normalized) > 0),
  constraint ig_account_followers_welcomed_at_when_sent
    check (
      welcome_dm_status <> 'sent'
      or welcomed_at is not null
    )
);

create unique index if not exists ig_account_followers_account_username_key
  on public.ig_account_followers (account_id, follower_username_normalized);

create index if not exists ig_account_followers_account_welcome_status_idx
  on public.ig_account_followers (account_id, welcome_dm_status);

create index if not exists ig_account_followers_account_last_seen_idx
  on public.ig_account_followers (account_id, last_seen_at desc);

create index if not exists ig_account_followers_account_baseline_idx
  on public.ig_account_followers (account_id, baseline_existing);

create index if not exists ig_account_followers_pending_welcome_idx
  on public.ig_account_followers (account_id, last_seen_at desc)
  where welcome_dm_status = 'pending' and baseline_existing = false;

create index if not exists ig_account_followers_source_scan_run_id_idx
  on public.ig_account_followers (source_scan_run_id)
  where source_scan_run_id is not null;

drop trigger if exists ig_account_followers_set_updated_at on public.ig_account_followers;
create trigger ig_account_followers_set_updated_at
  before update on public.ig_account_followers
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
        and constraint_name = 'ig_account_followers_account_id_fkey'
    ) then
      alter table public.ig_account_followers
        add constraint ig_account_followers_account_id_fkey
        foreign key (account_id) references public.ig_accounts (id)
        on delete cascade;
    end if;
  end if;

  if exists (
    select 1 from information_schema.tables
    where table_schema = 'public' and table_name = 'ig_runs'
  ) then
    if not exists (
      select 1 from information_schema.table_constraints
      where constraint_schema = 'public'
        and constraint_name = 'ig_account_followers_source_scan_run_id_fkey'
    ) then
      alter table public.ig_account_followers
        add constraint ig_account_followers_source_scan_run_id_fkey
        foreign key (source_scan_run_id) references public.ig_runs (id)
        on delete set null;
    end if;
  end if;
end
$$;

-- =============================================================================
-- 6. ig_account_dm_counters (daily quotas)
-- =============================================================================
create table if not exists public.ig_account_dm_counters (
  id uuid primary key default gen_random_uuid(),
  account_id uuid not null,
  counter_date date not null default (timezone('utc', now()))::date,
  welcome_sent_count integer not null default 0,
  outreach_sent_count integer not null default 0,
  total_dm_sent_count integer not null default 0,
  welcome_skipped_count integer not null default 0,
  outreach_skipped_count integer not null default 0,
  failed_count integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint ig_account_dm_counters_counts_nonnegative check (
    welcome_sent_count >= 0
    and outreach_sent_count >= 0
    and total_dm_sent_count >= 0
    and welcome_skipped_count >= 0
    and outreach_skipped_count >= 0
    and failed_count >= 0
  )
);

create unique index if not exists ig_account_dm_counters_account_date_key
  on public.ig_account_dm_counters (account_id, counter_date);

create index if not exists ig_account_dm_counters_account_id_idx
  on public.ig_account_dm_counters (account_id);

create index if not exists ig_account_dm_counters_counter_date_idx
  on public.ig_account_dm_counters (counter_date desc);

drop trigger if exists ig_account_dm_counters_set_updated_at on public.ig_account_dm_counters;
create trigger ig_account_dm_counters_set_updated_at
  before update on public.ig_account_dm_counters
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
        and constraint_name = 'ig_account_dm_counters_account_id_fkey'
    ) then
      alter table public.ig_account_dm_counters
        add constraint ig_account_dm_counters_account_id_fkey
        foreign key (account_id) references public.ig_accounts (id)
        on delete cascade;
    end if;
  end if;
end
$$;

-- =============================================================================
-- 7. ig_dm_jobs (unified Welcome + Outreach queue)
-- =============================================================================
create table if not exists public.ig_dm_jobs (
  id uuid primary key default gen_random_uuid(),
  account_id uuid not null,
  dm_type public.dm_type not null,
  recipient_username text not null,
  recipient_username_normalized text generated always as (lower(trim(recipient_username))) stored,
  message_body text,
  template_id uuid,
  source public.dm_job_source not null default 'manual',
  campaign_id uuid,
  priority integer not null default 0,
  status public.dm_job_status not null default 'pending',
  skip_reason text,
  attempts integer not null default 0,
  max_attempts integer not null default 3,
  next_retry_at timestamptz,
  reserved_at timestamptz,
  reserved_by text,
  started_at timestamptz,
  finished_at timestamptz,
  sent_at timestamptz,
  last_error text,
  idempotency_key text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint ig_dm_jobs_recipient_nonempty
    check (char_length(trim(recipient_username)) > 0),
  constraint ig_dm_jobs_idempotency_key_nonempty
    check (char_length(trim(idempotency_key)) > 0),
  constraint ig_dm_jobs_attempts_nonnegative check (attempts >= 0),
  constraint ig_dm_jobs_max_attempts_positive check (max_attempts >= 1),
  constraint ig_dm_jobs_message_or_template check (
    (message_body is not null and char_length(trim(message_body)) > 0)
    or template_id is not null
  ),
  constraint ig_dm_jobs_sent_at_when_sent
    check (status <> 'sent' or sent_at is not null)
);

create unique index if not exists ig_dm_jobs_idempotency_key_key
  on public.ig_dm_jobs (idempotency_key);

-- Worker claim path: pending jobs ready for retry, per account.
create index if not exists ig_dm_jobs_claim_idx
  on public.ig_dm_jobs (
    account_id,
    status,
    next_retry_at nulls first,
    priority desc,
    created_at asc
  )
  where status in ('pending', 'reserved');

create index if not exists ig_dm_jobs_account_status_created_idx
  on public.ig_dm_jobs (account_id, status, created_at desc);

create index if not exists ig_dm_jobs_dm_type_status_idx
  on public.ig_dm_jobs (dm_type, status);

create index if not exists ig_dm_jobs_campaign_id_idx
  on public.ig_dm_jobs (campaign_id)
  where campaign_id is not null;

create index if not exists ig_dm_jobs_account_recipient_type_idx
  on public.ig_dm_jobs (account_id, recipient_username_normalized, dm_type);

drop trigger if exists ig_dm_jobs_set_updated_at on public.ig_dm_jobs;
create trigger ig_dm_jobs_set_updated_at
  before update on public.ig_dm_jobs
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
        and constraint_name = 'ig_dm_jobs_account_id_fkey'
    ) then
      alter table public.ig_dm_jobs
        add constraint ig_dm_jobs_account_id_fkey
        foreign key (account_id) references public.ig_accounts (id)
        on delete cascade;
    end if;
  end if;

  if not exists (
    select 1 from information_schema.table_constraints
    where constraint_schema = 'public'
      and constraint_name = 'ig_dm_jobs_template_id_fkey'
  ) then
    alter table public.ig_dm_jobs
      add constraint ig_dm_jobs_template_id_fkey
      foreign key (template_id) references public.ig_dm_templates (id)
      on delete set null;
  end if;
end
$$;

-- =============================================================================
-- 8. Helper: normalize Instagram handle
-- =============================================================================
create or replace function public.normalize_ig_username(p_username text)
returns text
language sql
immutable
as $$
  select lower(trim(p_username));
$$;

-- =============================================================================
-- 9. Helper: ensure daily counter row exists
-- =============================================================================
create or replace function public.ensure_dm_counter_row(
  p_account_id uuid,
  p_counter_date date default (timezone('utc', now()))::date
)
returns public.ig_account_dm_counters
language plpgsql
security definer
set search_path = public
as $$
declare
  v_row public.ig_account_dm_counters;
begin
  insert into public.ig_account_dm_counters (account_id, counter_date)
  values (p_account_id, p_counter_date)
  on conflict (account_id, counter_date) do nothing;

  select *
  into v_row
  from public.ig_account_dm_counters
  where account_id = p_account_id
    and counter_date = p_counter_date;

  return v_row;
end;
$$;

-- =============================================================================
-- 10. RPC: upsert_account_follower_seen
--     V4.1 — Welcome follower scan / baseline
-- =============================================================================
create or replace function public.upsert_account_follower_seen(
  p_account_id uuid,
  p_follower_username text,
  p_source_scan_run_id uuid default null,
  p_is_baseline_scan boolean default false
)
returns public.ig_account_followers
language plpgsql
security definer
set search_path = public
as $$
declare
  v_username text := trim(p_follower_username);
  v_row public.ig_account_followers;
  v_now timestamptz := now();
begin
  if v_username is null or char_length(v_username) = 0 then
    raise exception 'follower_username must be non-empty';
  end if;

  insert into public.ig_account_followers (
    account_id,
    follower_username,
    first_seen_at,
    last_seen_at,
    baseline_existing,
    welcome_dm_status,
    source_scan_run_id
  )
  values (
    p_account_id,
    v_username,
    v_now,
    v_now,
    coalesce(p_is_baseline_scan, false),
    case
      when coalesce(p_is_baseline_scan, false) then 'not_eligible_baseline'::public.welcome_dm_status
      else 'pending'::public.welcome_dm_status
    end,
    p_source_scan_run_id
  )
  on conflict (account_id, follower_username_normalized)
  do update set
    last_seen_at = v_now,
    source_scan_run_id = coalesce(excluded.source_scan_run_id, ig_account_followers.source_scan_run_id),
    baseline_existing = ig_account_followers.baseline_existing
      or excluded.baseline_existing,
    welcome_dm_status = case
      when ig_account_followers.welcome_dm_status in ('sent', 'reserved')
        then ig_account_followers.welcome_dm_status
      when ig_account_followers.baseline_existing
        or excluded.baseline_existing
        then 'not_eligible_baseline'::public.welcome_dm_status
      when ig_account_followers.welcome_dm_status = 'not_eligible_baseline'
        and not excluded.baseline_existing
        then 'pending'::public.welcome_dm_status
      else ig_account_followers.welcome_dm_status
    end,
    updated_at = v_now
  returning * into v_row;

  return v_row;
end;
$$;

-- =============================================================================
-- 11. RPC: build idempotency keys (internal + for producers)
-- =============================================================================
create or replace function public.dm_job_idempotency_key_welcome(
  p_account_id uuid,
  p_recipient_username text
)
returns text
language sql
immutable
as $$
  select format('welcome:%s:%s', p_account_id::text, public.normalize_ig_username(p_recipient_username));
$$;

create or replace function public.dm_job_idempotency_key_outreach(
  p_account_id uuid,
  p_recipient_username text,
  p_campaign_id uuid default null
)
returns text
language sql
immutable
as $$
  select case
    when p_campaign_id is null then format(
      'outreach:%s:%s',
      p_account_id::text,
      public.normalize_ig_username(p_recipient_username)
    )
    else format(
      'outreach:%s:%s:%s',
      p_account_id::text,
      p_campaign_id::text,
      public.normalize_ig_username(p_recipient_username)
    )
  end;
$$;

-- =============================================================================
-- 12. RPC: enqueue_welcome_dm_job_if_eligible
--     V4.2 — Welcome producer (after scan diff)
-- =============================================================================
create or replace function public.enqueue_welcome_dm_job_if_eligible(
  p_account_id uuid,
  p_follower_username text,
  p_source_scan_run_id uuid default null,
  p_message_body text default null,
  p_template_id uuid default null,
  p_priority integer default 10
)
returns public.ig_dm_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  v_username text := trim(p_follower_username);
  v_follower public.ig_account_followers;
  v_settings public.ig_account_dm_settings;
  v_job public.ig_dm_jobs;
  v_key text;
  v_body text;
begin
  if v_username is null or char_length(v_username) = 0 then
    raise exception 'follower_username must be non-empty';
  end if;

  select * into v_settings
  from public.ig_account_dm_settings
  where account_id = p_account_id;

  if not found or v_settings.welcome_enabled is distinct from true then
    return null;
  end if;

  select * into v_follower
  from public.ig_account_followers
  where account_id = p_account_id
    and follower_username_normalized = public.normalize_ig_username(v_username);

  if not found then
    v_follower := public.upsert_account_follower_seen(
      p_account_id,
      v_username,
      p_source_scan_run_id,
      false
    );
  end if;

  if v_follower.baseline_existing
     or v_follower.welcome_dm_status in (
       'not_eligible_baseline', 'sent', 'reserved', 'skipped', 'failed'
     ) then
    return null;
  end if;

  v_key := public.dm_job_idempotency_key_welcome(p_account_id, v_username);

  -- Idempotence: never re-enqueue a terminal welcome job for this follower.
  select * into v_job
  from public.ig_dm_jobs
  where idempotency_key = v_key
  limit 1;

  if found then
    if v_job.status in ('sent', 'skipped', 'failed', 'cancelled') then
      return null;
    end if;
    return v_job;
  end if;

  v_body := nullif(trim(coalesce(p_message_body, '')), '');
  if v_body is null then
    if p_template_id is not null then
      select t.body into v_body
      from public.ig_dm_templates t
      where t.id = p_template_id
        and t.account_id = p_account_id
        and t.active = true;
    elsif v_settings.welcome_template_id is not null then
      select t.body into v_body
      from public.ig_dm_templates t
      where t.id = v_settings.welcome_template_id
        and t.active = true;
    else
      select t.body into v_body
      from public.ig_dm_templates t
      where t.account_id = p_account_id
        and t.template_type = 'welcome'
        and t.is_default = true
        and t.active = true
      order by t.created_at asc
      limit 1;
    end if;
  end if;

  if v_body is null or char_length(v_body) = 0 then
    raise exception 'no welcome message_body or template available for account %', p_account_id;
  end if;

  insert into public.ig_dm_jobs (
    account_id,
    dm_type,
    recipient_username,
    message_body,
    template_id,
    source,
    priority,
    status,
    idempotency_key,
    metadata
  )
  values (
    p_account_id,
    'welcome',
    v_username,
    v_body,
    coalesce(p_template_id, v_settings.welcome_template_id),
    'welcome_scan',
    coalesce(p_priority, 10),
    'pending',
    v_key,
    jsonb_build_object(
      'source_scan_run_id', p_source_scan_run_id,
      'follower_id', v_follower.id
    )
  )
  on conflict (idempotency_key) do nothing
  returning * into v_job;

  if v_job.id is null then
    return null;
  end if;

  update public.ig_account_followers
  set welcome_dm_status = 'pending',
      updated_at = now()
  where id = v_follower.id
    and welcome_dm_status not in ('sent', 'reserved');

  return v_job;
end;
$$;

-- =============================================================================
-- 13. RPC: claim_next_dm_job
--     V4.3+ — Worker consumer
-- =============================================================================
create or replace function public.claim_next_dm_job(
  p_account_id uuid,
  p_reserved_by text,
  p_dm_type public.dm_type default null
)
returns public.ig_dm_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  v_job_id uuid;
  v_job public.ig_dm_jobs;
begin
  if p_reserved_by is null or char_length(trim(p_reserved_by)) = 0 then
    raise exception 'reserved_by must be non-empty (worker id / device serial)';
  end if;

  select j.id
  into v_job_id
  from public.ig_dm_jobs j
  where j.account_id = p_account_id
    and j.status = 'pending'
    and (j.next_retry_at is null or j.next_retry_at <= now())
    and (p_dm_type is null or j.dm_type = p_dm_type)
  order by j.priority desc, j.created_at asc
  for update skip locked
  limit 1;

  if v_job_id is null then
    return null;
  end if;

  update public.ig_dm_jobs j
  set
    status = 'reserved',
    reserved_at = now(),
    reserved_by = trim(p_reserved_by),
    updated_at = now()
  where j.id = v_job_id
  returning * into v_job;

  return v_job;
end;
$$;

-- Mark job running (optional second step before UI work).
create or replace function public.mark_dm_job_running(p_job_id uuid)
returns public.ig_dm_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  v_job public.ig_dm_jobs;
begin
  update public.ig_dm_jobs
  set
    status = 'running',
    started_at = coalesce(started_at, now()),
    updated_at = now()
  where id = p_job_id
    and status in ('reserved', 'pending')
  returning * into v_job;

  return v_job;
end;
$$;

-- =============================================================================
-- 14. RPC: complete_dm_job
--     V4.3+ — Worker finalize + daily counters
-- =============================================================================
create or replace function public.complete_dm_job(
  p_job_id uuid,
  p_final_status public.dm_job_status,
  p_skip_reason text default null,
  p_last_error text default null,
  p_increment_attempt boolean default false,
  p_retry_delay_seconds integer default null,
  p_metadata_patch jsonb default '{}'::jsonb
)
returns public.ig_dm_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  v_job public.ig_dm_jobs;
  v_counter public.ig_account_dm_counters;
  v_today date := (timezone('utc', now()))::date;
begin
  if p_final_status not in ('sent', 'skipped', 'failed', 'cancelled') then
    raise exception 'final_status must be sent, skipped, failed, or cancelled';
  end if;

  update public.ig_dm_jobs
  set
    skip_reason = case when p_final_status = 'skipped' then p_skip_reason else skip_reason end,
    last_error = case when p_final_status in ('failed', 'skipped') then p_last_error else last_error end,
    attempts = case when p_increment_attempt then attempts + 1 else attempts end,
    next_retry_at = case
      when p_final_status = 'failed'
        and p_retry_delay_seconds is not null
        and attempts + case when p_increment_attempt then 1 else 0 end < max_attempts
        then now() + make_interval(secs => greatest(p_retry_delay_seconds, 0))
      else null
    end,
    status = case
      when p_final_status = 'failed'
        and p_retry_delay_seconds is not null
        and attempts + case when p_increment_attempt then 1 else 0 end < max_attempts
        then 'pending'::public.dm_job_status
      else p_final_status
    end,
    reserved_at = case
      when p_final_status = 'failed'
        and p_retry_delay_seconds is not null
        and attempts + case when p_increment_attempt then 1 else 0 end < max_attempts
        then null
      else reserved_at
    end,
    reserved_by = case
      when p_final_status = 'failed'
        and p_retry_delay_seconds is not null
        and attempts + case when p_increment_attempt then 1 else 0 end < max_attempts
        then null
      else reserved_by
    end,
    sent_at = case when p_final_status = 'sent' then coalesce(sent_at, now()) else sent_at end,
    finished_at = case
      when p_final_status in ('sent', 'skipped', 'cancelled')
        then coalesce(finished_at, now())
      when p_final_status = 'failed'
        and (
          p_retry_delay_seconds is null
          or attempts + case when p_increment_attempt then 1 else 0 end >= max_attempts
        )
        then coalesce(finished_at, now())
      else finished_at
    end,
    metadata = coalesce(metadata, '{}'::jsonb) || coalesce(p_metadata_patch, '{}'::jsonb),
    updated_at = now()
  where id = p_job_id
  returning * into v_job;

  if not found then
    raise exception 'job not found: %', p_job_id;
  end if;

  -- Sync follower memory for welcome jobs.
  if v_job.dm_type = 'welcome' then
    update public.ig_account_followers f
    set
      welcome_dm_status = case v_job.status
        when 'sent' then 'sent'::public.welcome_dm_status
        when 'skipped' then 'skipped'::public.welcome_dm_status
        when 'failed' then 'failed'::public.welcome_dm_status
        when 'pending' then f.welcome_dm_status
        else f.welcome_dm_status
      end,
      welcomed_at = case when v_job.status = 'sent' then coalesce(f.welcomed_at, now()) else f.welcomed_at end,
      skip_reason = case when v_job.status = 'skipped' then v_job.skip_reason else f.skip_reason end,
      last_error = case when v_job.status = 'failed' then v_job.last_error else f.last_error end,
      dm_thread_checked_at = coalesce(f.dm_thread_checked_at, now()),
      updated_at = now()
    where f.account_id = v_job.account_id
      and f.follower_username_normalized = v_job.recipient_username_normalized;
  end if;

  -- Daily counters: terminal outcomes only (retry → pending leaves finished_at null).
  if v_job.status in ('sent', 'skipped', 'failed')
     and v_job.finished_at is not null then
    v_counter := public.ensure_dm_counter_row(v_job.account_id, v_today);

    update public.ig_account_dm_counters
    set
      welcome_sent_count = welcome_sent_count
        + case when v_job.dm_type = 'welcome' and v_job.status = 'sent' then 1 else 0 end,
      outreach_sent_count = outreach_sent_count
        + case when v_job.dm_type = 'outreach' and v_job.status = 'sent' then 1 else 0 end,
      total_dm_sent_count = total_dm_sent_count
        + case when v_job.status = 'sent' then 1 else 0 end,
      welcome_skipped_count = welcome_skipped_count
        + case when v_job.dm_type = 'welcome' and v_job.status = 'skipped' then 1 else 0 end,
      outreach_skipped_count = outreach_skipped_count
        + case when v_job.dm_type = 'outreach' and v_job.status = 'skipped' then 1 else 0 end,
      failed_count = failed_count
        + case when v_job.status = 'failed' then 1 else 0 end,
      updated_at = now()
    where id = v_counter.id;
  end if;

  return v_job;
end;
$$;

-- =============================================================================
-- 15. RPC: enqueue_outreach_dm_job (n8n / dashboard)
--     V4.5+
-- =============================================================================
create or replace function public.enqueue_outreach_dm_job(
  p_account_id uuid,
  p_recipient_username text,
  p_message_body text default null,
  p_template_id uuid default null,
  p_source public.dm_job_source default 'manual',
  p_campaign_id uuid default null,
  p_priority integer default 0,
  p_metadata jsonb default '{}'::jsonb
)
returns public.ig_dm_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  v_username text := trim(p_recipient_username);
  v_settings public.ig_account_dm_settings;
  v_job public.ig_dm_jobs;
  v_key text;
  v_body text;
begin
  if v_username is null or char_length(v_username) = 0 then
    raise exception 'recipient_username must be non-empty';
  end if;

  select * into v_settings
  from public.ig_account_dm_settings
  where account_id = p_account_id;

  if not found or v_settings.outreach_enabled is distinct from true then
    return null;
  end if;

  v_body := nullif(trim(coalesce(p_message_body, '')), '');
  if v_body is null and p_template_id is not null then
    select t.body into v_body
    from public.ig_dm_templates t
    where t.id = p_template_id and t.account_id = p_account_id and t.active = true;
  end if;

  if v_body is null and v_settings.default_outreach_template_id is not null then
    select t.body into v_body
    from public.ig_dm_templates t
    where t.id = v_settings.default_outreach_template_id and t.active = true;
  end if;

  if v_body is null or char_length(v_body) = 0 then
    raise exception 'no outreach message_body or template for account %', p_account_id;
  end if;

  v_key := public.dm_job_idempotency_key_outreach(p_account_id, v_username, p_campaign_id);

  insert into public.ig_dm_jobs (
    account_id,
    dm_type,
    recipient_username,
    message_body,
    template_id,
    source,
    campaign_id,
    priority,
    status,
    idempotency_key,
    metadata
  )
  values (
    p_account_id,
    'outreach',
    v_username,
    v_body,
    p_template_id,
    coalesce(p_source, 'manual'::public.dm_job_source),
    p_campaign_id,
    coalesce(p_priority, 0),
    'pending',
    v_key,
    coalesce(p_metadata, '{}'::jsonb)
  )
  on conflict (idempotency_key) do nothing
  returning * into v_job;

  if v_job.id is null then
    select * into v_job from public.ig_dm_jobs where idempotency_key = v_key;
  end if;

  return v_job;
end;
$$;

-- =============================================================================
-- 16. RLS stubs (enable later; service role bypasses)
-- Tenant isolation via ig_accounts.tenant_id — add policies when dashboard reads.
-- =============================================================================
alter table public.ig_dm_templates enable row level security;
alter table public.ig_account_dm_settings enable row level security;
alter table public.ig_account_followers enable row level security;
alter table public.ig_account_dm_counters enable row level security;
alter table public.ig_dm_jobs enable row level security;

-- Service role full access (Supabase default for service_role often bypasses RLS;
-- explicit policies help authenticated dashboard users later.)

do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'ig_dm_jobs' and policyname = 'ig_dm_jobs_service_role_all'
  ) then
    create policy ig_dm_jobs_service_role_all on public.ig_dm_jobs
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;
exception
  when undefined_object then null;
end
$$;

-- =============================================================================
-- 17. Grants (RPCs callable by service role / authenticated producers)
-- =============================================================================
grant usage on schema public to postgres, anon, authenticated, service_role;

grant select, insert, update, delete on public.ig_dm_templates to service_role;
grant select, insert, update, delete on public.ig_account_dm_settings to service_role;
grant select, insert, update, delete on public.ig_account_followers to service_role;
grant select, insert, update, delete on public.ig_account_dm_counters to service_role;
grant select, insert, update, delete on public.ig_dm_jobs to service_role;

grant execute on function public.normalize_ig_username(text) to service_role, authenticated;
grant execute on function public.ensure_dm_counter_row(uuid, date) to service_role;
grant execute on function public.upsert_account_follower_seen(uuid, text, uuid, boolean) to service_role;
grant execute on function public.dm_job_idempotency_key_welcome(uuid, text) to service_role, authenticated;
grant execute on function public.dm_job_idempotency_key_outreach(uuid, text, uuid) to service_role, authenticated;
grant execute on function public.enqueue_welcome_dm_job_if_eligible(uuid, text, uuid, text, uuid, integer) to service_role;
grant execute on function public.enqueue_outreach_dm_job(uuid, text, text, uuid, public.dm_job_source, uuid, integer, jsonb) to service_role, authenticated;
grant execute on function public.claim_next_dm_job(uuid, text, public.dm_type) to service_role;
grant execute on function public.mark_dm_job_running(uuid) to service_role;
grant execute on function public.complete_dm_job(uuid, public.dm_job_status, text, text, boolean, integer, jsonb) to service_role;

-- Optional: seed dm_settings row when ig_accounts row is created (uncomment + adapt).
-- create or replace function public.seed_ig_account_dm_settings() ...

comment on table public.ig_account_followers is
  'V4 Welcome: long-term follower memory per ig_accounts row; baseline vs incremental scans.';

comment on table public.ig_dm_jobs is
  'V4 unified DM queue (welcome + outreach); claimed by Python worker via claim_next_dm_job.';

comment on table public.ig_dm_templates is
  'Per-account DM templates; replaces legacy pm_welcome.txt files on disk.';
