-- Entry 2E-5H-2: service-role-only Supabase Vault read RPC for Instagram credentials.
-- This RPC is intentionally narrow: it accepts only supabase_vault://{uuid}
-- references and returns a minimal response for the worker-side Vault reader.

create or replace function public.read_instagram_credentials_vault_secret(
  p_secret_ref text
)
returns jsonb
language plpgsql
stable
security definer
set search_path = public, vault
as $$
declare
  v_secret_ref text := btrim(coalesce(p_secret_ref, ''));
  v_secret_id_text text;
  v_secret_id uuid;
  v_secret_value text;
begin
  if v_secret_ref = '' then
    return jsonb_build_object(
      'ok', false,
      'reason', 'invalid_secret_ref',
      'secret_provider', 'supabase_vault',
      'safe_ref_label', 'supabase_vault://[REDACTED]'
    );
  end if;

  if left(v_secret_ref, length('supabase_vault://')) <> 'supabase_vault://' then
    return jsonb_build_object(
      'ok', false,
      'reason', 'unsupported_secret_ref_provider',
      'secret_provider', 'supabase_vault',
      'safe_ref_label', 'supabase_vault://[REDACTED]'
    );
  end if;

  v_secret_id_text := btrim(substr(v_secret_ref, length('supabase_vault://') + 1));
  if v_secret_id_text !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
    return jsonb_build_object(
      'ok', false,
      'reason', 'vault_secret_id_invalid',
      'secret_provider', 'supabase_vault',
      'safe_ref_label', 'supabase_vault://[REDACTED]'
    );
  end if;

  v_secret_id := v_secret_id_text::uuid;

  select ds.decrypted_secret
    into v_secret_value
  from vault.decrypted_secrets ds
  where ds.id = v_secret_id
  limit 1;

  if not found then
    return jsonb_build_object(
      'ok', false,
      'reason', 'vault_secret_not_found',
      'secret_provider', 'supabase_vault',
      'safe_ref_label', 'supabase_vault://[REDACTED]'
    );
  end if;

  if coalesce(v_secret_value, '') = '' then
    return jsonb_build_object(
      'ok', false,
      'reason', 'vault_secret_empty',
      'secret_provider', 'supabase_vault',
      'safe_ref_label', 'supabase_vault://[REDACTED]'
    );
  end if;

  return jsonb_build_object(
    'ok', true,
    'secret_value', v_secret_value,
    'secret_provider', 'supabase_vault',
    'safe_ref_label', 'supabase_vault://[REDACTED]'
  );
exception
  when others then
    return jsonb_build_object(
      'ok', false,
      'reason', 'vault_read_failed',
      'secret_provider', 'supabase_vault',
      'safe_ref_label', 'supabase_vault://[REDACTED]'
    );
end;
$$;

comment on function public.read_instagram_credentials_vault_secret(text) is
  'Entry 2E-5H-2 service-role-only Supabase Vault read helper for worker-side Instagram credentials. Returns a minimal safe envelope; callers must never log raw responses.';

revoke execute on function public.read_instagram_credentials_vault_secret(text)
  from public, anon, authenticated;

grant execute on function public.read_instagram_credentials_vault_secret(text)
  to service_role;
