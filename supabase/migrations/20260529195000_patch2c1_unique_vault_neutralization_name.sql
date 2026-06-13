-- Credential Secure Pipeline Patch 2C-1 - unique Vault neutralization names.
--
-- Supabase Vault secret names can collide. Use a deterministic per-secret
-- neutralized name instead of one shared revoked/instagram/credential name.

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
    'revoked/instagram/credential/' || v_secret_id::text,
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

comment on function public.revoke_instagram_credentials_vault_secret(text, text, text) is
  'Patch 2C-1 service-role-only helper that neutralizes a Supabase Vault Instagram credential secret with a unique revoked name. Valid reasons exclude restorable lifecycle states.';

revoke execute on function public.revoke_instagram_credentials_vault_secret(text, text, text)
  from public, anon, authenticated;
grant execute on function public.revoke_instagram_credentials_vault_secret(text, text, text)
  to service_role;
