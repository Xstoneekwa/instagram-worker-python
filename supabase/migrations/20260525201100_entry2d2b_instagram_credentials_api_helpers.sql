-- Entry 2D-2B — secure Instagram credentials API helpers.
--
-- Adds a generic account ownership RPC for credentials management and a
-- service-role-only metadata rotation RPC. Password material remains outside
-- public app tables and is written to Supabase Vault by the Edge Function.

-- =============================================================================
-- 1. Generic Instagram account ownership helper
-- =============================================================================
create or replace function public.client_can_manage_instagram_account(
  p_auth_user_id uuid,
  p_account_id uuid
)
returns boolean
language sql
stable
security invoker
set search_path = public
as $$
  select exists (
    select 1
    from public.client_users cu
    join public.clients c
      on c.id = cu.client_id
     and c.status = 'active'
    join public.client_instagram_accounts cia
      on cia.client_id = c.id
     and cia.account_id = p_account_id
    where cu.auth_user_id = p_auth_user_id
      and cu.status = 'active'
      and cu.role in ('owner', 'admin', 'assistant')
      and p_auth_user_id is not null
      and p_account_id is not null
  );
$$;

comment on function public.client_can_manage_instagram_account(uuid, uuid) is
  'Entry 2D-2B generic client/account ownership helper for credentials APIs. Does not require outreach entitlement.';

-- =============================================================================
-- 2. Client lookup helper for internal credentials operations
-- =============================================================================
create or replace function public.client_id_for_instagram_account(
  p_account_id uuid
)
returns uuid
language sql
stable
security invoker
set search_path = public
as $$
  select cia.client_id
  from public.client_instagram_accounts cia
  join public.clients c
    on c.id = cia.client_id
   and c.status = 'active'
  where cia.account_id = p_account_id
    and p_account_id is not null
  limit 1;
$$;

comment on function public.client_id_for_instagram_account(uuid) is
  'Entry 2D-2B service-role helper returning the active owning client for an Instagram account.';

-- =============================================================================
-- 3. Service-role-only Vault write wrapper
-- =============================================================================
create or replace function public.create_instagram_credentials_vault_secret(
  p_secret_payload text,
  p_secret_name text,
  p_secret_description text
)
returns uuid
language sql
volatile
security invoker
set search_path = public, vault
as $$
  select vault.create_secret(
    p_secret_payload,
    p_secret_name,
    coalesce(p_secret_description, '')
  );
$$;

comment on function public.create_instagram_credentials_vault_secret(text, text, text) is
  'Entry 2D-2B service-role-only wrapper for creating a Supabase Vault secret from the credentials Edge Function.';

-- =============================================================================
-- 4. Service-role-only account_credentials rotation
-- =============================================================================
create or replace function public.rotate_instagram_account_credentials(
  p_account_id uuid,
  p_client_id uuid,
  p_username_at_submission text,
  p_secret_ref text,
  p_secret_provider text,
  p_credentials_version integer,
  p_action text,
  p_external_request_id text,
  p_request_id text,
  p_submitted_by uuid,
  p_submitted_via text
)
returns public.account_credentials
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_next_version integer;
  v_row public.account_credentials;
  v_metadata jsonb;
begin
  if auth.role() <> 'service_role' then
    raise exception 'service_role_required';
  end if;

  if p_account_id is null then
    raise exception 'account_id_required';
  end if;

  if p_secret_ref is null or char_length(trim(p_secret_ref)) = 0 then
    raise exception 'secret_ref_required';
  end if;

  if p_secret_provider <> 'supabase_vault' then
    raise exception 'secret_provider_not_supported';
  end if;

  if p_credentials_version is null or p_credentials_version < 1 then
    raise exception 'credentials_version_required';
  end if;

  if p_action not in ('submit', 'update_password') then
    raise exception 'action_not_supported';
  end if;

  select coalesce(max(credentials_version), 0) + 1
    into v_next_version
  from public.account_credentials
  where account_id = p_account_id
    and provider = 'instagram';

  if v_next_version <> p_credentials_version then
    raise exception 'credentials_version_conflict';
  end if;

  update public.account_credentials
     set status = 'superseded',
         updated_at = now()
   where account_id = p_account_id
     and provider = 'instagram'
     and status = 'active';

  v_metadata := jsonb_build_object(
    'action', p_action,
    'request_id', nullif(trim(coalesce(p_request_id, '')), ''),
    'vault_secret_created', true
  );

  if nullif(trim(coalesce(p_external_request_id, '')), '') is not null then
    v_metadata := v_metadata || jsonb_build_object(
      'external_request_id',
      left(trim(p_external_request_id), 120)
    );
  end if;

  insert into public.account_credentials (
    account_id,
    client_id,
    provider,
    username_at_submission,
    secret_ref,
    secret_provider,
    credentials_version,
    status,
    last_submitted_at,
    last_rotated_at,
    reauth_required,
    reauth_reason,
    submitted_by,
    submitted_via,
    metadata
  )
  values (
    p_account_id,
    p_client_id,
    'instagram',
    nullif(trim(coalesce(p_username_at_submission, '')), ''),
    trim(p_secret_ref),
    p_secret_provider,
    v_next_version,
    'active',
    now(),
    case when p_action = 'update_password' then now() else null end,
    true,
    'awaiting_login_verification',
    p_submitted_by,
    p_submitted_via,
    v_metadata
  )
  returning * into v_row;

  return v_row;
end;
$$;

comment on function public.rotate_instagram_account_credentials(
  uuid, uuid, text, text, text, integer, text, text, text, uuid, text
) is
  'Entry 2D-2B service-role-only metadata rotation after the Edge Function stores the password in Vault. Never accepts raw password material.';

-- =============================================================================
-- 5. Grants
-- =============================================================================
revoke execute on function public.client_can_manage_instagram_account(uuid, uuid)
  from public, anon, authenticated;
revoke execute on function public.client_id_for_instagram_account(uuid)
  from public, anon, authenticated;
revoke execute on function public.create_instagram_credentials_vault_secret(text, text, text)
  from public, anon, authenticated;
revoke execute on function public.rotate_instagram_account_credentials(
  uuid, uuid, text, text, text, integer, text, text, text, uuid, text
) from public, anon, authenticated;

grant execute on function public.client_can_manage_instagram_account(uuid, uuid)
  to service_role;
grant execute on function public.client_id_for_instagram_account(uuid)
  to service_role;
grant execute on function public.create_instagram_credentials_vault_secret(text, text, text)
  to service_role;
grant execute on function public.rotate_instagram_account_credentials(
  uuid, uuid, text, text, text, integer, text, text, text, uuid, text
) to service_role;
