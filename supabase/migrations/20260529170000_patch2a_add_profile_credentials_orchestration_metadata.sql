-- Credential Secure Pipeline Patch 2A - Add Profile credential orchestration metadata.
--
-- This migration keeps the existing Vault + rotate pipeline. It does not accept
-- raw passwords, does not create secure links, does not migrate legacy
-- ig_account_settings.password, and does not touch device/provisioning flows.

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
  v_metadata_safe jsonb;
  v_submitted_via text := lower(coalesce(nullif(trim(p_submitted_via), ''), 'unknown'));
  v_source text := 'unknown';
  v_actor_type text := 'backend';
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

  if v_submitted_via not in (
    'client_dashboard',
    'admin_dashboard',
    'api',
    'system',
    'migration',
    'add_profile',
    'secure_update_link',
    'admin_backend',
    'unknown'
  ) then
    raise exception 'submitted_via_not_supported';
  end if;

  v_source := case v_submitted_via
    when 'add_profile' then 'add_profile'
    when 'secure_update_link' then 'secure_update_link'
    when 'admin_backend' then 'admin_backend'
    when 'migration' then 'migration'
    else 'unknown'
  end;

  v_actor_type := case v_submitted_via
    when 'client_dashboard' then 'client'
    when 'migration' then 'migration'
    when 'system' then 'system'
    else 'backend'
  end;

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

  v_metadata_safe := jsonb_build_object(
    'source', v_source,
    'action', p_action,
    'request_id', nullif(trim(coalesce(p_request_id, '')), ''),
    'submitted_via', v_submitted_via,
    'vault_secret_created', true
  );

  if nullif(trim(coalesce(p_external_request_id, '')), '') is not null then
    v_metadata := v_metadata || jsonb_build_object(
      'external_request_id',
      left(trim(p_external_request_id), 120)
    );
    v_metadata_safe := v_metadata_safe || jsonb_build_object(
      'external_request_id',
      left(trim(p_external_request_id), 120)
    );
  end if;

  if public.jsonb_has_forbidden_safe_metadata_key(v_metadata_safe) then
    raise exception 'metadata_safe_forbidden';
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
    metadata,
    last_updated_at,
    updated_by_actor_type,
    updated_by_actor_id,
    source,
    metadata_safe
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
    v_submitted_via,
    v_metadata,
    now(),
    v_actor_type,
    case when p_submitted_by is not null then p_submitted_by::text else null end,
    v_source,
    v_metadata_safe
  )
  returning * into v_row;

  return v_row;
end;
$$;

comment on function public.rotate_instagram_account_credentials(
  uuid, uuid, text, text, text, integer, text, text, text, uuid, text
) is
  'Patch 2A service-role-only metadata rotation after the Edge Function stores the password in Vault. Writes active credential metadata and safe source/actor fields; never accepts raw password material.';
