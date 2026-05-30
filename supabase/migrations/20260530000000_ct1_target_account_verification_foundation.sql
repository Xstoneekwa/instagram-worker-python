-- CT-1 - Target account add/bulk verification foundation.
--
-- Additive schema foundation only. This does not activate SearchApi in
-- production, does not call Instagram, does not touch worker/follow runtime,
-- and does not implement CT bulk queue execution.

create extension if not exists pgcrypto;

create table if not exists public.ig_targets (
  id uuid primary key default gen_random_uuid(),
  account_id uuid not null,
  target_username text not null,
  status text not null default 'pending_verification',
  source text not null default 'manual_single',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.ig_targets
  add column if not exists input_username text,
  add column if not exists normalized_username text,
  add column if not exists canonical_username text,
  add column if not exists verification_status text not null default 'pending',
  add column if not exists verification_reason text,
  add column if not exists quality_status text not null default 'unknown',
  add column if not exists avatar_url text,
  add column if not exists followers_count integer,
  add column if not exists is_verified boolean,
  add column if not exists is_private boolean,
  add column if not exists provider_checked_at timestamptz,
  add column if not exists batch_id uuid,
  add column if not exists actor_type text,
  add column if not exists archive_reason text,
  add column if not exists rejected_reason text,
  add column if not exists archived_at timestamptz,
  add column if not exists deleted_at timestamptz,
  add column if not exists metadata_safe jsonb not null default '{}'::jsonb;

update public.ig_targets
set
  input_username = coalesce(input_username, target_username),
  normalized_username = coalesce(
    normalized_username,
    lower(regexp_replace(trim(coalesce(target_username, '')), '^@+', ''))
  ),
  verification_status = coalesce(verification_status, 'pending'),
  quality_status = coalesce(quality_status, 'unknown')
where input_username is null
  or normalized_username is null
  or verification_status is null
  or quality_status is null;

alter table public.ig_targets
  drop constraint if exists ig_targets_status_ct1_check,
  drop constraint if exists ig_targets_verification_status_ct1_check,
  drop constraint if exists ig_targets_quality_status_ct1_check,
  drop constraint if exists ig_targets_actor_type_ct1_check,
  drop constraint if exists ig_targets_normalized_username_ct1_check,
  drop constraint if exists ig_targets_followers_count_ct1_check,
  drop constraint if exists ig_targets_avatar_url_ct1_safe_check,
  drop constraint if exists ig_targets_metadata_safe_ct1_check;

alter table public.ig_targets
  add constraint ig_targets_status_ct1_check
    check (
      status in (
        'pending_verification',
        'valid',
        'rejected',
        'review',
        'duplicate',
        'archived',
        -- Legacy/dashboard statuses kept to avoid breaking runtime readers.
        'pending',
        'completed',
        'failed',
        'skipped',
        'active',
        'paused',
        'filtered',
        'deleted',
        'poor_performance'
      )
    ) not valid,
  add constraint ig_targets_verification_status_ct1_check
    check (
      verification_status in (
        'pending',
        'found',
        'not_found',
        'unavailable',
        'rate_limited',
        'provider_error'
      )
    ) not valid,
  add constraint ig_targets_quality_status_ct1_check
    check (
      quality_status in (
        'unknown',
        'eligible',
        'rejected_low_followers',
        'rejected_verified',
        'rejected_private',
        'rejected_not_found',
        'review_provider_unavailable',
        'review_username_changed'
      )
    ) not valid,
  add constraint ig_targets_actor_type_ct1_check
    check (
      actor_type is null
      or actor_type in ('admin', 'client', 'system')
    ) not valid,
  add constraint ig_targets_normalized_username_ct1_check
    check (
      normalized_username is null
      or (
        normalized_username ~ '^[a-z0-9._]{1,30}$'
        and normalized_username not like '%.'
        and normalized_username not like '.%'
        and normalized_username not like '%..%'
      )
    ) not valid,
  add constraint ig_targets_followers_count_ct1_check
    check (followers_count is null or followers_count >= 0) not valid,
  add constraint ig_targets_avatar_url_ct1_safe_check
    check (
      avatar_url is null
      or (
        avatar_url ~* '^https?://'
        and char_length(avatar_url) <= 2048
        and lower(avatar_url) not like '%token%'
        and lower(avatar_url) not like '%secret%'
        and lower(avatar_url) not like '%signature%'
        and lower(avatar_url) not like '%authorization%'
        and lower(avatar_url) not like '%service_role%'
        and lower(avatar_url) not like '%supabase_vault://%'
      )
    ) not valid,
  add constraint ig_targets_metadata_safe_ct1_check
    check (
      jsonb_typeof(metadata_safe) = 'object'
      and not public.jsonb_has_forbidden_safe_metadata_key(metadata_safe)
    ) not valid;

create index if not exists ig_targets_account_normalized_idx
  on public.ig_targets (account_id, normalized_username)
  where archived_at is null and deleted_at is null;

create index if not exists ig_targets_account_id_status_idx
  on public.ig_targets (account_id, status);

create index if not exists ig_targets_verification_status_idx
  on public.ig_targets (verification_status, provider_checked_at desc);

create index if not exists ig_targets_quality_status_idx
  on public.ig_targets (quality_status);

create index if not exists ig_targets_batch_id_idx
  on public.ig_targets (batch_id)
  where batch_id is not null;

alter table public.ig_targets enable row level security;

do $$
begin
  if not exists (
    select 1
    from pg_policies
    where schemaname = 'public'
      and tablename = 'ig_targets'
      and policyname = 'ig_targets_service_role_all'
  ) then
    create policy ig_targets_service_role_all
      on public.ig_targets
      for all
      using ((select auth.role()) = 'service_role')
      with check ((select auth.role()) = 'service_role');
  end if;
end
$$;

revoke all on public.ig_targets from public;
revoke all on public.ig_targets from anon;
revoke all on public.ig_targets from authenticated;
grant all on public.ig_targets to service_role;

create table if not exists public.ct_target_audit_events (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  account_id uuid not null,
  target_id uuid,
  operation text not null,
  result text not null,
  reason text,
  actor_type text not null,
  actor_id uuid,
  batch_id uuid,
  counts jsonb,
  metadata_safe jsonb not null default '{}'::jsonb,
  constraint ct_target_audit_operation_check
    check (operation in ('target_add_single', 'target_add_bulk', 'target_verify')),
  constraint ct_target_audit_result_check
    check (result in ('accepted', 'duplicate', 'rejected', 'review', 'failed')),
  constraint ct_target_audit_actor_type_check
    check (actor_type in ('admin', 'client', 'system')),
  constraint ct_target_audit_counts_object_check
    check (counts is null or jsonb_typeof(counts) = 'object'),
  constraint ct_target_audit_metadata_safe_check
    check (
      jsonb_typeof(metadata_safe) = 'object'
      and not public.jsonb_has_forbidden_safe_metadata_key(metadata_safe)
    )
);

create index if not exists ct_target_audit_account_created_idx
  on public.ct_target_audit_events (account_id, created_at desc);

create index if not exists ct_target_audit_target_created_idx
  on public.ct_target_audit_events (target_id, created_at desc)
  where target_id is not null;

create index if not exists ct_target_audit_batch_created_idx
  on public.ct_target_audit_events (batch_id, created_at desc)
  where batch_id is not null;

alter table public.ct_target_audit_events enable row level security;

do $$
begin
  if not exists (
    select 1
    from pg_policies
    where schemaname = 'public'
      and tablename = 'ct_target_audit_events'
      and policyname = 'ct_target_audit_events_service_role_all'
  ) then
    create policy ct_target_audit_events_service_role_all
      on public.ct_target_audit_events
      for all
      using ((select auth.role()) = 'service_role')
      with check ((select auth.role()) = 'service_role');
  end if;
end
$$;

revoke all on public.ct_target_audit_events from public;
revoke all on public.ct_target_audit_events from anon;
revoke all on public.ct_target_audit_events from authenticated;
grant all on public.ct_target_audit_events to service_role;

comment on table public.ig_targets is
  'Target accounts / CT source of truth. CT-1 adds safe verification, quality, source, batch and soft-archive fields without activating worker/follow runtime.';

comment on column public.ig_targets.metadata_safe is
  'Safe target verification metadata only. Never raw provider payloads, URLs with keys, headers, cookies, sessions, tokens, secret refs, Vault ids, raw HTML, XML, screenshots or logs.';

comment on table public.ct_target_audit_events is
  'Safe CT target add/bulk/verify audit events. Stores counts and reasons only, never raw provider responses, secrets, tokens, cookies, sessions, raw HTML, XML, screenshots or logs.';
