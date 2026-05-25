-- Entry 2D-1 — account credentials metadata / secret references.
--
-- Schema-only foundation for Instagram credential onboarding and password
-- rotation. This migration does not store raw passwords, encrypted passwords,
-- vault payloads, provisioning jobs, dashboard APIs, Edge Functions, or worker
-- login flows.

create extension if not exists pgcrypto;

-- =============================================================================
-- 1. Account credentials metadata
-- =============================================================================
create table if not exists public.account_credentials (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  account_id uuid not null references public.ig_accounts (id) on delete cascade,
  client_id uuid references public.clients (id) on delete set null,

  provider text not null default 'instagram',
  username_at_submission text,

  secret_ref text not null,
  secret_provider text not null,
  credentials_version integer not null default 1,
  status text not null default 'active',

  last_submitted_at timestamptz not null default now(),
  last_rotated_at timestamptz,
  reauth_required boolean not null default false,
  reauth_reason text,

  submitted_by uuid,
  submitted_via text,
  revoked_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,

  constraint account_credentials_provider_check
    check (provider in ('instagram')),
  constraint account_credentials_status_check
    check (status in ('active', 'superseded', 'revoked')),
  constraint account_credentials_secret_ref_nonempty
    check (char_length(trim(secret_ref)) > 0),
  constraint account_credentials_secret_provider_nonempty
    check (char_length(trim(secret_provider)) > 0),
  constraint account_credentials_credentials_version_positive
    check (credentials_version >= 1),
  constraint account_credentials_metadata_object_check
    check (jsonb_typeof(metadata) = 'object'),
  constraint account_credentials_submitted_via_check
    check (
      submitted_via is null
      or submitted_via in ('client_dashboard', 'admin_dashboard', 'api', 'system', 'migration')
    ),
  constraint account_credentials_reauth_reason_nonempty
    check (reauth_reason is null or char_length(trim(reauth_reason)) > 0),
  constraint account_credentials_username_nonempty
    check (
      username_at_submission is null
      or char_length(trim(username_at_submission)) > 0
    )
);

create index if not exists account_credentials_account_id_idx
  on public.account_credentials (account_id);

create index if not exists account_credentials_client_id_idx
  on public.account_credentials (client_id)
  where client_id is not null;

create index if not exists account_credentials_provider_status_idx
  on public.account_credentials (provider, status);

create index if not exists account_credentials_status_created_at_idx
  on public.account_credentials (status, created_at desc);

create index if not exists account_credentials_reauth_required_idx
  on public.account_credentials (reauth_required)
  where reauth_required = true;

create unique index if not exists account_credentials_one_active_account_provider_key
  on public.account_credentials (account_id, provider)
  where status = 'active';

-- =============================================================================
-- 2. updated_at trigger
-- =============================================================================
do $$
begin
  if exists (
    select 1
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname = 'set_updated_at'
  ) then
    drop trigger if exists account_credentials_set_updated_at
      on public.account_credentials;
    create trigger account_credentials_set_updated_at
      before update on public.account_credentials
      for each row execute function public.set_updated_at();
  end if;
end
$$;

-- =============================================================================
-- 3. RLS and grants
-- =============================================================================
alter table public.account_credentials enable row level security;

do $$
begin
  if not exists (
    select 1
    from pg_policies
    where schemaname = 'public'
      and tablename = 'account_credentials'
      and policyname = 'account_credentials_service_role_all'
  ) then
    create policy account_credentials_service_role_all
      on public.account_credentials
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;
end
$$;

revoke all on public.account_credentials from public;
revoke all on public.account_credentials from anon;
revoke all on public.account_credentials from authenticated;

grant all on public.account_credentials to service_role;

-- =============================================================================
-- 4. Documentation comments
-- =============================================================================
comment on table public.account_credentials is
  'Entry 2D-1 metadata-only credential registry. Stores secret_ref/secret_provider only; never store raw passwords, encrypted passwords, tokens, cookies, or vault payloads.';

comment on column public.account_credentials.secret_ref is
  'Reference to a future vault/KMS/secret manager entry. This is not the secret value and must not be client-readable.';

comment on column public.account_credentials.secret_provider is
  'Identifier for the future secret backend, for example supabase_vault, aws_secrets_manager, 1password, or local_ops_vault.';

comment on column public.account_credentials.credentials_version is
  'Monotonic safe credential metadata version. Increment on future submit/update/rotation APIs.';

comment on column public.account_credentials.metadata is
  'Safe metadata only. Never store passwords, raw secrets, tokens, cookies, recovery codes, screenshots, raw XML, or client-visible device internals.';
