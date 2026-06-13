-- Credential Secure Pipeline Patch 2C-1 - lifecycle-safe cleanup reason guards.
--
-- Credentials cleanup remains explicitly decoupled from account lifecycle
-- archive/trash/restore because those lifecycle states are restorable.

create or replace function public.instagram_credential_cleanup_reason_allowed(
  p_reason text
)
returns boolean
language sql
immutable
set search_path = public
as $$
  select lower(coalesce(nullif(btrim(p_reason), ''), '')) in (
    'smoke_cleanup',
    'failed_ingestion_cleanup',
    'explicit_credential_revoke',
    'security_revoke',
    'permanent_account_delete'
  );
$$;

comment on function public.instagram_credential_cleanup_reason_allowed(text) is
  'Patch 2C-1 reason guard for credential cleanup. Restorable lifecycle states such as archive/trash/paused are intentionally not valid cleanup reasons.';

revoke execute on function public.instagram_credential_cleanup_reason_allowed(text)
  from public, anon, authenticated;
grant execute on function public.instagram_credential_cleanup_reason_allowed(text)
  to service_role;

create or replace function public.revoke_instagram_credentials_vault_secret(
  p_secret_ref text,
  p_reason text default 'explicit_credential_revoke',
  p_request_id text default null
)
returns jsonb
language plpgsql
security definer
set search_path = public, vault
as $$
declare
  v_secret_ref text := btrim(coalesce(p_secret_ref, ''));
  v_reason text := lower(coalesce(nullif(btrim(p_reason), ''), 'explicit_credential_revoke'));
  v_request_id text := nullif(btrim(coalesce(p_request_id, '')), '');
  v_secret_id_text text;
  v_secret_id uuid;
  v_secret_exists boolean := false;
  v_neutral_payload text;
begin
  if auth.role() <> 'service_role' then
    raise exception 'service_role_required'
      using errcode = '42501';
  end if;

  if not public.instagram_credential_cleanup_reason_allowed(v_reason) then
    raise exception 'cleanup_reason_not_allowed'
      using errcode = '22023';
  end if;

  if v_request_id is not null and v_request_id !~ '^[a-zA-Z0-9._:-]{1,120}$' then
    raise exception 'request_id_invalid'
      using errcode = '22023';
  end if;

  if left(v_secret_ref, length('supabase_vault://')) <> 'supabase_vault://' then
    raise exception 'vault_secret_ref_invalid'
      using errcode = '22023';
  end if;

  v_secret_id_text := btrim(substr(v_secret_ref, length('supabase_vault://') + 1));
  if v_secret_id_text !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
    raise exception 'vault_secret_ref_invalid'
      using errcode = '22023';
  end if;
  v_secret_id := v_secret_id_text::uuid;

  select exists (
    select 1
    from vault.secrets s
    where s.id = v_secret_id
  )
    into v_secret_exists;

  if not v_secret_exists then
    return jsonb_build_object(
      'ok', true,
      'vault_cleanup_attempted', true,
      'vault_cleanup_status', 'not_found',
      'safe_ref_label', 'supabase_vault://[REDACTED]'
    );
  end if;

  v_neutral_payload := jsonb_strip_nulls(jsonb_build_object(
    'revoked', true,
    'revoked_at', now(),
    'reason', v_reason,
    'request_id', v_request_id,
    'secret_value_removed', true
  ))::text;

  perform vault.update_secret(
    v_secret_id,
    v_neutral_payload,
    'revoked/instagram/credential',
    'Revoked Instagram credential secret; value neutralized by Patch 2C-1.',
    null
  );

  return jsonb_build_object(
    'ok', true,
    'vault_cleanup_attempted', true,
    'vault_cleanup_status', 'neutralized',
    'safe_ref_label', 'supabase_vault://[REDACTED]'
  );
exception
  when others then
    if sqlstate in ('22023', '42501') then
      raise;
    end if;

    return jsonb_build_object(
      'ok', false,
      'vault_cleanup_attempted', true,
      'vault_cleanup_status', 'failed',
      'safe_ref_label', 'supabase_vault://[REDACTED]'
    );
end;
$$;

create or replace function public.revoke_instagram_account_credentials(
  p_account_id uuid,
  p_provider text default 'instagram',
  p_reason text default 'explicit_credential_revoke',
  p_request_id text default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_provider text := lower(coalesce(nullif(btrim(p_provider), ''), 'instagram'));
  v_reason text := lower(coalesce(nullif(btrim(p_reason), ''), 'explicit_credential_revoke'));
  v_request_id text := nullif(btrim(coalesce(p_request_id, '')), '');
  v_now timestamptz := now();
  v_found_count integer := 0;
  v_revoked_count integer := 0;
  v_already_revoked_count integer := 0;
  v_vault_attempted_count integer := 0;
  v_vault_neutralized_count integer := 0;
  v_vault_not_found_count integer := 0;
  v_vault_failed_count integer := 0;
  v_vault_status text := 'not_applicable';
  v_cleanup jsonb;
  v_secret_ref text;
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

  if not public.instagram_credential_cleanup_reason_allowed(v_reason) then
    raise exception 'cleanup_reason_not_allowed'
      using errcode = '22023';
  end if;

  if v_request_id is not null and v_request_id !~ '^[a-zA-Z0-9._:-]{1,120}$' then
    raise exception 'request_id_invalid'
      using errcode = '22023';
  end if;

  select count(*),
         count(*) filter (where status = 'revoked'),
         count(*) filter (where status <> 'revoked')
    into v_found_count, v_already_revoked_count, v_revoked_count
  from public.account_credentials ac
  where ac.account_id = p_account_id
    and ac.provider = v_provider;

  for v_secret_ref in
    select distinct ac.secret_ref
    from public.account_credentials ac
    where ac.account_id = p_account_id
      and ac.provider = v_provider
      and ac.secret_ref is not null
      and ac.secret_ref like 'supabase_vault://%'
  loop
    v_cleanup := public.revoke_instagram_credentials_vault_secret(
      v_secret_ref,
      v_reason,
      v_request_id
    );
    v_vault_attempted_count := v_vault_attempted_count + 1;

    if v_cleanup->>'vault_cleanup_status' = 'neutralized' then
      v_vault_neutralized_count := v_vault_neutralized_count + 1;
    elsif v_cleanup->>'vault_cleanup_status' = 'not_found' then
      v_vault_not_found_count := v_vault_not_found_count + 1;
    else
      v_vault_failed_count := v_vault_failed_count + 1;
    end if;
  end loop;

  update public.account_credentials ac
     set status = 'revoked',
         revoked_at = coalesce(ac.revoked_at, v_now),
         reauth_required = true,
         reauth_reason = v_reason,
         last_updated_at = v_now,
         updated_at = v_now,
         updated_by_actor_type = 'backend',
         source = coalesce(nullif(ac.source, ''), 'unknown'),
         metadata_safe = coalesce(ac.metadata_safe, '{}'::jsonb)
           || jsonb_strip_nulls(jsonb_build_object(
             'action', 'revoke',
             'source', coalesce(nullif(ac.source, ''), 'unknown'),
             'revoke_reason', v_reason,
             'request_id', v_request_id,
             'credential_cleanup', true
           ))
   where ac.account_id = p_account_id
     and ac.provider = v_provider
     and ac.status <> 'revoked';

  if v_vault_attempted_count = 0 then
    v_vault_status := 'not_applicable';
  elsif v_vault_failed_count > 0 then
    v_vault_status := 'partial_failed';
  elsif v_vault_neutralized_count > 0 and v_vault_not_found_count > 0 then
    v_vault_status := 'partial_neutralized';
  elsif v_vault_neutralized_count > 0 then
    v_vault_status := 'neutralized';
  else
    v_vault_status := 'not_found';
  end if;

  return jsonb_build_object(
    'ok', true,
    'account_id', p_account_id,
    'provider', v_provider,
    'credentials_found', v_found_count > 0,
    'credentials_revoked', v_revoked_count,
    'credentials_already_revoked', v_already_revoked_count,
    'vault_cleanup_attempted', v_vault_attempted_count > 0,
    'vault_cleanup_status', v_vault_status,
    'vault_cleanup_attempted_count', v_vault_attempted_count,
    'vault_cleanup_neutralized_count', v_vault_neutralized_count,
    'vault_cleanup_not_found_count', v_vault_not_found_count,
    'vault_cleanup_failed_count', v_vault_failed_count
  );
end;
$$;

comment on function public.revoke_instagram_credentials_vault_secret(text, text, text) is
  'Patch 2C-1 service-role-only helper that neutralizes a Supabase Vault Instagram credential secret by replacing its value. Valid reasons exclude restorable lifecycle states.';

comment on function public.revoke_instagram_account_credentials(uuid, text, text, text) is
  'Patch 2C-1 service-role-only helper that revokes Instagram credential metadata and neutralizes linked Vault secrets when possible. Valid reasons exclude archive/trash/restore lifecycle states.';

revoke execute on function public.revoke_instagram_credentials_vault_secret(text, text, text)
  from public, anon, authenticated;
revoke execute on function public.revoke_instagram_account_credentials(uuid, text, text, text)
  from public, anon, authenticated;

grant execute on function public.revoke_instagram_credentials_vault_secret(text, text, text)
  to service_role;
grant execute on function public.revoke_instagram_account_credentials(uuid, text, text, text)
  to service_role;
