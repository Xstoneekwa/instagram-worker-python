-- Credential Secure Pipeline V1 foundation.
--
-- Backend/schema only. This migration does not read or migrate legacy
-- ig_account_settings.password values, does not activate frontend flows, does
-- not generate raw update tokens, and does not run device/login/provisioning
-- workflows.

create extension if not exists pgcrypto;

-- =============================================================================
-- 1. Shared safe metadata guard
-- =============================================================================
create or replace function public.jsonb_has_forbidden_safe_metadata_key(
  p_metadata jsonb
)
returns boolean
language sql
immutable
set search_path = public
as $$
  with recursive walk(value) as (
    select coalesce(p_metadata, '{}'::jsonb)
    union all
    select child.value
    from walk w
    cross join lateral (
      select e.value
      from jsonb_each(
        case when jsonb_typeof(w.value) = 'object' then w.value else '{}'::jsonb end
      ) as e
      union all
      select a.value
      from jsonb_array_elements(
        case when jsonb_typeof(w.value) = 'array' then w.value else '[]'::jsonb end
      ) as a
    ) as child
  ),
  keys as (
    select lower(k.key) as key
    from walk w
    cross join lateral jsonb_object_keys(
      case when jsonb_typeof(w.value) = 'object' then w.value else '{}'::jsonb end
    ) as k(key)
  )
  select exists (
    select 1
    from keys
    where key in (
      'password',
      'password_hash',
      'password_length',
      'secret',
      'secret_ref',
      'secret_provider',
      'raw_secret',
      'token',
      'raw_token',
      'authorization',
      'bearer',
      'cookie',
      'session_cookie',
      'vault',
      'vault_id',
      'vault_payload',
      'service_role',
      'service_role_key',
      'internal_api_token',
      'webhook',
      'webhook_url',
      'slack_url',
      'discord_url',
      'raw_xml',
      'xml',
      'screenshot',
      'screenshot_path',
      'raw_logs',
      'raw_metadata',
      'request_body',
      'raw_request_body',
      'adb_serial',
      'usb_port',
      'hub_port',
      'device_udid'
    )
  );
$$;

comment on function public.jsonb_has_forbidden_safe_metadata_key(jsonb) is
  'Credential pipeline safe-metadata guard. Returns true when JSON contains forbidden secret, token, raw log/XML, screenshot, or device-internal keys.';

revoke execute on function public.jsonb_has_forbidden_safe_metadata_key(jsonb)
  from public, anon, authenticated;
grant execute on function public.jsonb_has_forbidden_safe_metadata_key(jsonb)
  to service_role;

-- =============================================================================
-- 2. account_credentials compatibility foundation
-- =============================================================================
alter table public.account_credentials
  alter column secret_ref drop not null,
  alter column secret_provider drop not null;

alter table public.account_credentials
  add column if not exists last_updated_at timestamptz,
  add column if not exists updated_by_actor_type text,
  add column if not exists updated_by_actor_id text,
  add column if not exists source text,
  add column if not exists metadata_safe jsonb not null default '{}'::jsonb;

update public.account_credentials
set last_updated_at = coalesce(last_updated_at, updated_at, last_submitted_at, created_at, now())
where last_updated_at is null;

alter table public.account_credentials
  alter column last_updated_at set default now(),
  alter column last_updated_at set not null;

alter table public.account_credentials
  drop constraint if exists account_credentials_status_check;

alter table public.account_credentials
  add constraint account_credentials_status_check
  check (
    status in (
      -- Existing versioned-history statuses kept for backward compatibility.
      'active',
      'superseded',
      'revoked',
      -- Credential Secure Pipeline V1 current-state statuses.
      'missing',
      'configured',
      'reauth_required',
      'update_needed',
      'unknown'
    )
  );

alter table public.account_credentials
  drop constraint if exists account_credentials_submitted_via_check;

alter table public.account_credentials
  add constraint account_credentials_submitted_via_check
  check (
    submitted_via is null
    or submitted_via in (
      'client_dashboard',
      'admin_dashboard',
      'api',
      'system',
      'migration',
      'add_profile',
      'secure_update_link',
      'admin_backend',
      'unknown'
    )
  );

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'account_credentials_actor_type_check'
      and conrelid = 'public.account_credentials'::regclass
  ) then
    alter table public.account_credentials
      add constraint account_credentials_actor_type_check
      check (
        updated_by_actor_type is null
        or updated_by_actor_type in ('admin', 'client', 'system', 'migration', 'backend')
      );
  end if;

  if not exists (
    select 1
    from pg_constraint
    where conname = 'account_credentials_source_check'
      and conrelid = 'public.account_credentials'::regclass
  ) then
    alter table public.account_credentials
      add constraint account_credentials_source_check
      check (
        source is null
        or source in (
          'add_profile',
          'secure_update_link',
          'admin_backend',
          'migration',
          'unknown'
        )
      );
  end if;

  if not exists (
    select 1
    from pg_constraint
    where conname = 'account_credentials_metadata_safe_check'
      and conrelid = 'public.account_credentials'::regclass
  ) then
    alter table public.account_credentials
      add constraint account_credentials_metadata_safe_check
      check (
        jsonb_typeof(metadata_safe) = 'object'
        and not public.jsonb_has_forbidden_safe_metadata_key(metadata_safe)
      );
  end if;
end
$$;

create unique index if not exists account_credentials_current_state_account_provider_key
  on public.account_credentials (account_id, provider)
  where status in ('missing', 'configured', 'reauth_required', 'update_needed', 'unknown');

comment on column public.account_credentials.last_updated_at is
  'Credential Secure Pipeline V1 safe metadata update timestamp. Does not imply a password read.';
comment on column public.account_credentials.updated_by_actor_type is
  'Safe actor type for metadata updates: admin, client, system, migration, or backend.';
comment on column public.account_credentials.updated_by_actor_id is
  'Safe actor identifier supplied by backend/API. Never store password, token, or secret material here.';
comment on column public.account_credentials.source is
  'Safe ingestion source: add_profile, secure_update_link, admin_backend, migration, or unknown.';
comment on column public.account_credentials.metadata_safe is
  'Safe metadata only. Forbidden keys include password, token, secret_ref, raw request body, raw logs/XML, screenshots, and device internals.';

-- =============================================================================
-- 3. account_dashboard_actions compatibility foundation
-- =============================================================================
alter table public.account_dashboard_actions
  add column if not exists created_by_actor_type text,
  add column if not exists created_by_actor_id text,
  add column if not exists metadata_safe jsonb not null default '{}'::jsonb;

alter table public.account_dashboard_actions
  drop constraint if exists account_dashboard_actions_status_check;

alter table public.account_dashboard_actions
  add constraint account_dashboard_actions_status_check
  check (
    status in (
      'pending',
      'acknowledged',
      'pending_verification',
      'submitted',
      'resolved',
      'dismissed',
      'expired',
      'revoked',
      'ignored'
    )
  );

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'account_dashboard_actions_created_actor_type_check'
      and conrelid = 'public.account_dashboard_actions'::regclass
  ) then
    alter table public.account_dashboard_actions
      add constraint account_dashboard_actions_created_actor_type_check
      check (
        created_by_actor_type is null
        or created_by_actor_type in ('admin', 'client', 'system', 'migration', 'backend')
      );
  end if;

  if not exists (
    select 1
    from pg_constraint
    where conname = 'account_dashboard_actions_metadata_safe_check'
      and conrelid = 'public.account_dashboard_actions'::regclass
  ) then
    alter table public.account_dashboard_actions
      add constraint account_dashboard_actions_metadata_safe_check
      check (
        jsonb_typeof(metadata_safe) = 'object'
        and not public.jsonb_has_forbidden_safe_metadata_key(metadata_safe)
      );
  end if;
end
$$;

comment on column public.account_dashboard_actions.metadata_safe is
  'Credential pipeline safe metadata. Never store password, token, secret_ref, raw request body, raw logs/XML, screenshots, or device internals.';

-- =============================================================================
-- 4. One-time credential update request metadata
-- =============================================================================
create table if not exists public.credential_update_requests (
  id uuid primary key default gen_random_uuid(),
  action_id uuid references public.account_dashboard_actions (id) on delete set null,
  account_id uuid not null references public.ig_accounts (id) on delete cascade,
  token_hash text not null,
  expires_at timestamptz not null,
  used_at timestamptz,
  revoked_at timestamptz,
  status text not null default 'pending',
  created_by_actor_type text,
  created_by_actor_id text,
  delivery_status text,
  metadata_safe jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint credential_update_requests_status_check
    check (status in ('pending', 'submitted', 'expired', 'revoked')),
  constraint credential_update_requests_token_hash_nonempty
    check (char_length(trim(token_hash)) >= 32),
  constraint credential_update_requests_expires_at_valid
    check (expires_at > created_at),
  constraint credential_update_requests_actor_type_check
    check (
      created_by_actor_type is null
      or created_by_actor_type in ('admin', 'client', 'system', 'migration', 'backend')
    ),
  constraint credential_update_requests_metadata_safe_check
    check (
      jsonb_typeof(metadata_safe) = 'object'
      and not public.jsonb_has_forbidden_safe_metadata_key(metadata_safe)
    )
);

create unique index if not exists credential_update_requests_token_hash_key
  on public.credential_update_requests (token_hash);

create index if not exists credential_update_requests_account_status_idx
  on public.credential_update_requests (account_id, status);

create index if not exists credential_update_requests_action_id_idx
  on public.credential_update_requests (action_id)
  where action_id is not null;

create index if not exists credential_update_requests_expires_at_idx
  on public.credential_update_requests (expires_at)
  where status = 'pending';

drop trigger if exists credential_update_requests_set_updated_at
  on public.credential_update_requests;
create trigger credential_update_requests_set_updated_at
  before update on public.credential_update_requests
  for each row execute function public.set_updated_at();

alter table public.credential_update_requests enable row level security;

do $$
begin
  if not exists (
    select 1
    from pg_policies
    where schemaname = 'public'
      and tablename = 'credential_update_requests'
      and policyname = 'credential_update_requests_service_role_all'
  ) then
    create policy credential_update_requests_service_role_all
      on public.credential_update_requests
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;
end
$$;

revoke all on public.credential_update_requests from public;
revoke all on public.credential_update_requests from anon;
revoke all on public.credential_update_requests from authenticated;
grant all on public.credential_update_requests to service_role;

comment on table public.credential_update_requests is
  'Credential Secure Pipeline V1 one-time update request metadata. Stores token_hash only; never raw tokens, passwords, secret_ref, Vault payloads, or raw request bodies.';
comment on column public.credential_update_requests.token_hash is
  'Hash of the one-time update token. The raw token is generated by a future Edge/API and is never stored.';
comment on column public.credential_update_requests.metadata_safe is
  'Safe metadata only. Forbidden keys include password, token, secret_ref, raw request body, raw logs/XML, screenshots, and device internals.';

-- =============================================================================
-- 5. Credential ingestion metadata RPC
-- =============================================================================
create or replace function public.record_instagram_credential_ingestion(
  p_account_id uuid,
  p_provider text default 'instagram',
  p_secret_ref text default null,
  p_status text default 'configured',
  p_source text default 'unknown',
  p_actor_type text default 'backend',
  p_actor_id text default null,
  p_metadata_safe jsonb default '{}'::jsonb
)
returns table (
  account_id uuid,
  provider text,
  credentials_version integer,
  status text,
  last_updated_at timestamptz
)
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_provider text := lower(coalesce(nullif(trim(p_provider), ''), 'instagram'));
  v_status text := lower(coalesce(nullif(trim(p_status), ''), 'configured'));
  v_source text := lower(coalesce(nullif(trim(p_source), ''), 'unknown'));
  v_actor_type text := lower(coalesce(nullif(trim(p_actor_type), ''), 'backend'));
  v_secret_ref text := nullif(trim(coalesce(p_secret_ref, '')), '');
  v_metadata_safe jsonb := coalesce(p_metadata_safe, '{}'::jsonb);
  v_existing public.account_credentials%rowtype;
  v_next_version integer := 1;
  v_now timestamptz := now();
begin
  if auth.role() <> 'service_role' then
    raise exception 'service_role_required'
      using errcode = '42501';
  end if;

  if p_account_id is null then
    raise exception 'account_id_required'
      using errcode = '22023';
  end if;

  if v_provider <> 'instagram' then
    raise exception 'provider_not_supported'
      using errcode = '22023';
  end if;

  if v_status not in ('missing', 'configured', 'reauth_required', 'update_needed', 'revoked', 'unknown') then
    raise exception 'invalid_credential_status'
      using errcode = '22023';
  end if;

  if v_source not in ('add_profile', 'secure_update_link', 'admin_backend', 'migration', 'unknown') then
    raise exception 'invalid_credential_source'
      using errcode = '22023';
  end if;

  if v_actor_type not in ('admin', 'client', 'system', 'migration', 'backend') then
    raise exception 'invalid_actor_type'
      using errcode = '22023';
  end if;

  if jsonb_typeof(v_metadata_safe) is distinct from 'object'
    or public.jsonb_has_forbidden_safe_metadata_key(v_metadata_safe)
  then
    raise exception 'metadata_safe_forbidden'
      using errcode = '22023';
  end if;

  select *
    into v_existing
  from public.account_credentials ac
  where ac.account_id = p_account_id
    and ac.provider = v_provider
  order by ac.credentials_version desc, ac.updated_at desc
  limit 1
  for update;

  if found then
    v_next_version := v_existing.credentials_version
      + case
          when v_existing.status is distinct from v_status then 1
          when v_secret_ref is not null and v_existing.secret_ref is distinct from v_secret_ref then 1
          else 0
        end;

    update public.account_credentials ac
    set
      status = v_status,
      secret_ref = coalesce(v_secret_ref, ac.secret_ref),
      secret_provider = case
        when v_secret_ref is not null and ac.secret_provider is null then 'supabase_vault'
        else ac.secret_provider
      end,
      credentials_version = v_next_version,
      last_updated_at = v_now,
      updated_by_actor_type = v_actor_type,
      updated_by_actor_id = nullif(trim(coalesce(p_actor_id, '')), ''),
      source = v_source,
      metadata_safe = v_metadata_safe,
      updated_at = v_now,
      reauth_required = v_status in ('reauth_required', 'update_needed'),
      reauth_reason = case
        when v_status = 'reauth_required' then coalesce(ac.reauth_reason, 'credential_reauth_required')
        when v_status = 'update_needed' then coalesce(ac.reauth_reason, 'credential_update_needed')
        else null
      end
    where ac.id = v_existing.id;
  else
    insert into public.account_credentials (
      account_id,
      provider,
      secret_ref,
      secret_provider,
      credentials_version,
      status,
      last_updated_at,
      updated_by_actor_type,
      updated_by_actor_id,
      source,
      metadata_safe,
      reauth_required,
      reauth_reason
    )
    values (
      p_account_id,
      v_provider,
      v_secret_ref,
      case when v_secret_ref is not null then 'supabase_vault' else null end,
      1,
      v_status,
      v_now,
      v_actor_type,
      nullif(trim(coalesce(p_actor_id, '')), ''),
      v_source,
      v_metadata_safe,
      v_status in ('reauth_required', 'update_needed'),
      case
        when v_status = 'reauth_required' then 'credential_reauth_required'
        when v_status = 'update_needed' then 'credential_update_needed'
        else null
      end
    );
  end if;

  return query
    select
      ac.account_id,
      ac.provider,
      ac.credentials_version,
      ac.status,
      ac.last_updated_at
    from public.account_credentials ac
    where ac.account_id = p_account_id
      and ac.provider = v_provider
    order by ac.credentials_version desc, ac.updated_at desc
    limit 1;
end;
$$;

comment on function public.record_instagram_credential_ingestion(
  uuid, text, text, text, text, text, text, jsonb
) is
  'Credential Secure Pipeline V1 service-role RPC for safe credential metadata ingestion. Does not accept password input and never returns secret_ref.';

revoke execute on function public.record_instagram_credential_ingestion(
  uuid, text, text, text, text, text, text, jsonb
) from public, anon, authenticated;
grant execute on function public.record_instagram_credential_ingestion(
  uuid, text, text, text, text, text, text, jsonb
) to service_role;

-- =============================================================================
-- 6. Credential dashboard action skeleton RPC
-- =============================================================================
create or replace function public.create_credential_dashboard_action(
  p_account_id uuid,
  p_action_type text default 'update_instagram_password',
  p_safe_client_message text default null,
  p_admin_message text default null,
  p_action_deep_link text default null,
  p_severity text default 'warning',
  p_requires_client_action boolean default true,
  p_blocking_campaign boolean default false,
  p_actor_type text default 'backend',
  p_actor_id text default null,
  p_metadata_safe jsonb default '{}'::jsonb
)
returns table (
  action_id uuid,
  account_id uuid,
  action_type text,
  status text,
  severity text,
  requires_client_action boolean,
  blocking_campaign boolean,
  safe_client_message text,
  action_deep_link text,
  created_at timestamptz,
  updated_at timestamptz
)
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_action_type text := lower(coalesce(nullif(trim(p_action_type), ''), 'update_instagram_password'));
  v_actor_type text := lower(coalesce(nullif(trim(p_actor_type), ''), 'backend'));
  v_metadata_safe jsonb := coalesce(p_metadata_safe, '{}'::jsonb);
  v_title text;
  v_label text;
  v_dedupe_key text;
  v_action public.account_dashboard_actions;
begin
  if auth.role() <> 'service_role' then
    raise exception 'service_role_required'
      using errcode = '42501';
  end if;

  if p_account_id is null then
    raise exception 'account_id_required'
      using errcode = '22023';
  end if;

  if v_action_type not in (
    'submit_instagram_credentials',
    'update_instagram_password',
    'complete_two_factor',
    'resolve_checkpoint',
    'review_login_failure',
    'review_account_mismatch',
    'review_credentials'
  ) then
    raise exception 'invalid_credential_action_type'
      using errcode = '22023';
  end if;

  if v_actor_type not in ('admin', 'client', 'system', 'migration', 'backend') then
    raise exception 'invalid_actor_type'
      using errcode = '22023';
  end if;

  if jsonb_typeof(v_metadata_safe) is distinct from 'object'
    or public.jsonb_has_forbidden_safe_metadata_key(v_metadata_safe)
  then
    raise exception 'metadata_safe_forbidden'
      using errcode = '22023';
  end if;

  v_title := case v_action_type
    when 'submit_instagram_credentials' then 'Submit Instagram credentials'
    when 'update_instagram_password' then 'Update Instagram password'
    when 'complete_two_factor' then 'Complete Instagram two-factor challenge'
    when 'resolve_checkpoint' then 'Resolve Instagram checkpoint'
    when 'review_login_failure' then 'Review Instagram login failure'
    when 'review_account_mismatch' then 'Review Instagram account mismatch'
    else 'Review Instagram credentials'
  end;

  v_label := case v_action_type
    when 'submit_instagram_credentials' then 'Submit credentials'
    when 'update_instagram_password' then 'Update password'
    when 'complete_two_factor' then 'Complete 2FA'
    when 'resolve_checkpoint' then 'Resolve checkpoint'
    else 'Review'
  end;

  v_dedupe_key := 'account:' || p_account_id::text || ':credential_action:' || v_action_type;

  v_action := public.upsert_account_dashboard_action(
    p_account_id := p_account_id,
    p_action_type := v_action_type,
    p_title := v_title,
    p_dedupe_key := v_dedupe_key,
    p_status := 'pending',
    p_severity := p_severity,
    p_audience := 'client',
    p_requires_client_action := p_requires_client_action,
    p_blocking_campaign := p_blocking_campaign,
    p_safe_client_message := p_safe_client_message,
    p_admin_message := p_admin_message,
    p_action_label := v_label,
    p_action_deep_link := p_action_deep_link,
    p_metadata := v_metadata_safe
  );

  update public.account_dashboard_actions ada
  set
    created_by_actor_type = v_actor_type,
    created_by_actor_id = nullif(trim(coalesce(p_actor_id, '')), ''),
    metadata_safe = v_metadata_safe
  where ada.id = v_action.id
  returning ada.* into v_action;

  return query
    select
      v_action.id,
      v_action.account_id,
      v_action.action_type,
      v_action.status,
      v_action.severity,
      v_action.requires_client_action,
      v_action.blocking_campaign,
      v_action.safe_client_message,
      v_action.action_deep_link,
      v_action.created_at,
      v_action.updated_at;
end;
$$;

comment on function public.create_credential_dashboard_action(
  uuid, text, text, text, text, text, boolean, boolean, text, text, jsonb
) is
  'Credential Secure Pipeline V1 service-role skeleton for safe credential-related dashboard actions. Does not create raw update links or tokens.';

revoke execute on function public.create_credential_dashboard_action(
  uuid, text, text, text, text, text, boolean, boolean, text, text, jsonb
) from public, anon, authenticated;
grant execute on function public.create_credential_dashboard_action(
  uuid, text, text, text, text, text, boolean, boolean, text, text, jsonb
) to service_role;

-- =============================================================================
-- 7. Credential update request metadata RPC
-- =============================================================================
create or replace function public.create_credential_update_request_metadata(
  p_account_id uuid,
  p_token_hash text,
  p_expires_at timestamptz,
  p_action_id uuid default null,
  p_created_by_actor_type text default 'backend',
  p_created_by_actor_id text default null,
  p_delivery_status text default null,
  p_metadata_safe jsonb default '{}'::jsonb
)
returns table (
  request_id uuid,
  action_id uuid,
  account_id uuid,
  status text,
  expires_at timestamptz,
  created_at timestamptz
)
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_actor_type text := lower(coalesce(nullif(trim(p_created_by_actor_type), ''), 'backend'));
  v_token_hash text := nullif(trim(coalesce(p_token_hash, '')), '');
  v_metadata_safe jsonb := coalesce(p_metadata_safe, '{}'::jsonb);
  v_row public.credential_update_requests;
begin
  if auth.role() <> 'service_role' then
    raise exception 'service_role_required'
      using errcode = '42501';
  end if;

  if p_account_id is null then
    raise exception 'account_id_required'
      using errcode = '22023';
  end if;

  if v_token_hash is null or char_length(v_token_hash) < 32 then
    raise exception 'token_hash_required'
      using errcode = '22023';
  end if;

  if lower(v_token_hash) like 'bearer %' or lower(v_token_hash) like 'http%' then
    raise exception 'token_hash_must_not_be_raw_token'
      using errcode = '22023';
  end if;

  if p_expires_at is null or p_expires_at <= now() then
    raise exception 'expires_at_must_be_future'
      using errcode = '22023';
  end if;

  if v_actor_type not in ('admin', 'client', 'system', 'migration', 'backend') then
    raise exception 'invalid_actor_type'
      using errcode = '22023';
  end if;

  if jsonb_typeof(v_metadata_safe) is distinct from 'object'
    or public.jsonb_has_forbidden_safe_metadata_key(v_metadata_safe)
  then
    raise exception 'metadata_safe_forbidden'
      using errcode = '22023';
  end if;

  insert into public.credential_update_requests (
    action_id,
    account_id,
    token_hash,
    expires_at,
    status,
    created_by_actor_type,
    created_by_actor_id,
    delivery_status,
    metadata_safe
  )
  values (
    p_action_id,
    p_account_id,
    v_token_hash,
    p_expires_at,
    'pending',
    v_actor_type,
    nullif(trim(coalesce(p_created_by_actor_id, '')), ''),
    nullif(trim(coalesce(p_delivery_status, '')), ''),
    v_metadata_safe
  )
  returning * into v_row;

  return query
    select
      v_row.id,
      v_row.action_id,
      v_row.account_id,
      v_row.status,
      v_row.expires_at,
      v_row.created_at;
end;
$$;

comment on function public.create_credential_update_request_metadata(
  uuid, text, timestamptz, uuid, text, text, text, jsonb
) is
  'Credential Secure Pipeline V1 service-role RPC for storing one-time credential update request metadata. Accepts token_hash only and never returns token_hash.';

revoke execute on function public.create_credential_update_request_metadata(
  uuid, text, timestamptz, uuid, text, text, text, jsonb
) from public, anon, authenticated;
grant execute on function public.create_credential_update_request_metadata(
  uuid, text, timestamptz, uuid, text, text, text, jsonb
) to service_role;
