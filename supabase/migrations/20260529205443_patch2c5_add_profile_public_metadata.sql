-- Credential Secure Pipeline Patch 2C-5 - Add Profile public profile metadata.
--
-- Safe storage only. This does not scrape Instagram, does not read credentials,
-- does not touch worker/runtime/login/provisioner flows, and does not implement
-- target-account quality filtering.

alter table public.ig_accounts
  add column if not exists username_verification_status text not null default 'pending',
  add column if not exists username_verified_at timestamptz,
  add column if not exists username_verification_reason text,
  add column if not exists instagram_user_id text,
  add column if not exists external_profile_id text,
  add column if not exists is_private boolean,
  add column if not exists is_verified boolean,
  add column if not exists followers_count integer,
  add column if not exists avatar_url text,
  add column if not exists avatar_checked_at timestamptz,
  add column if not exists public_profile_metadata jsonb not null default '{}'::jsonb;

alter table public.ig_accounts
  drop constraint if exists ig_accounts_username_verification_status_check;

alter table public.ig_accounts
  add constraint ig_accounts_username_verification_status_check
  check (
    username_verification_status in (
      'pending',
      'verified',
      'not_found',
      'username_changed',
      'private_or_limited',
      'inaccessible',
      'verification_unavailable',
      'provider_error',
      'invalid_format',
      'unknown'
    )
  );

alter table public.ig_accounts
  drop constraint if exists ig_accounts_public_profile_metadata_safe_check;

alter table public.ig_accounts
  add constraint ig_accounts_public_profile_metadata_safe_check
  check (
    jsonb_typeof(public_profile_metadata) = 'object'
    and not public.jsonb_has_forbidden_safe_metadata_key(public_profile_metadata)
  );

alter table public.ig_accounts
  drop constraint if exists ig_accounts_avatar_url_safe_check;

alter table public.ig_accounts
  add constraint ig_accounts_avatar_url_safe_check
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
  );

alter table public.ig_accounts
  drop constraint if exists ig_accounts_followers_count_nonnegative;

alter table public.ig_accounts
  add constraint ig_accounts_followers_count_nonnegative
  check (followers_count is null or followers_count >= 0);

create index if not exists ig_accounts_username_verification_status_idx
  on public.ig_accounts (username_verification_status);

create index if not exists ig_accounts_avatar_checked_at_idx
  on public.ig_accounts (avatar_checked_at desc)
  where avatar_checked_at is not null;

comment on column public.ig_accounts.username_verification_status is
  'Patch 2C-5 safe public username verification status. V1 is syntax/preflight plus provider-unavailable state; no credential or device login is used.';

comment on column public.ig_accounts.username_verified_at is
  'Timestamp when a safe public/profile verification source confirmed the username. Null for pending or unavailable verification.';

comment on column public.ig_accounts.username_verification_reason is
  'Safe stable reason for public username verification state. Never store raw provider responses or request bodies.';

comment on column public.ig_accounts.instagram_user_id is
  'Optional public/stable Instagram user id when a safe provider returns one. Never inferred from credentials.';

comment on column public.ig_accounts.external_profile_id is
  'Optional provider-specific public profile id for future verified profile metadata. Never a secret or Vault reference.';

comment on column public.ig_accounts.is_private is
  'Optional public profile privacy signal when safely available.';

comment on column public.ig_accounts.is_verified is
  'Optional public verified/blue-badge signal when safely available.';

comment on column public.ig_accounts.followers_count is
  'Optional public follower count when safely available. Target-account filtering is out of scope for Patch 2C-5.';

comment on column public.ig_accounts.avatar_url is
  'Optional public avatar/profile picture URL. Must not include tokens, signatures, secret refs, Vault ids, cookies, or service-role data.';

comment on column public.ig_accounts.avatar_checked_at is
  'Timestamp when avatar_url/public profile metadata was last checked by a safe source.';

comment on column public.ig_accounts.public_profile_metadata is
  'Safe public profile metadata only. Never store passwords, tokens, Authorization headers, service-role keys, cookies/sessions, secret refs, Vault payloads, raw request bodies, raw logs, XML, screenshots, or device internals.';
